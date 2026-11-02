"""The guardrail signals Logfire alerts read (library_guardrails_hardening.md Phase 5).

No Redis, no DB, no network: logfire is replaced with a recorder and the limiters run
through throwaway FastAPI apps.

The test that matters most is ``test_each_limiter_reports_itself``. All three limiters
answer the same 429 on purpose, so these labels are the only way to tell them apart,
and getting that wrong once already sent a fix at the wrong limiter.
"""
from __future__ import annotations

import asyncio
import re
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.app.api import library_items
from backend.app.errors import LunaHTTPException
from backend.app.middleware import guard_signals as gs
from backend.app.middleware.rate_limit import RateLimitMiddleware
from backend.app.middleware.route_limits import RouteRateLimiter
from backend.app.services import library_budget_service as lb
from backend.app.services import library_service as ls
from backend.tests.test_rate_limit_library import FakeRedis, _route_limited_app
from shared.observability import _KEEP_PATHS, _PII_EXTRA_PATTERNS, _scrub_callback


class Recorder:
    def __init__(self) -> None:
        self.events: List[Tuple[str, Dict[str, Any]]] = []
        self.fail = False

    def info(self, event: str, **attributes: Any) -> None:
        if self.fail:
            raise RuntimeError("logfire is down")
        self.events.append((event, attributes))

    def named(self, event: str) -> List[Dict[str, Any]]:
        return [attrs for name, attrs in self.events if name == event]


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()
    gs.reset_for_tests()
    monkeypatch.setattr(gs, "_logfire", rec)
    yield rec
    gs.reset_for_tests()


def _request(path: str, query: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query.encode(),
            "headers": [],
            "server": ("test", 80),
            "scheme": "http",
        }
    )


def _flush_after_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    real = time.monotonic
    monkeypatch.setattr(gs.time, "monotonic", lambda: real() + gs._FLUSH_SECONDS + 1)
    gs.flush_due()
    monkeypatch.setattr(gs.time, "monotonic", real)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "searching", "expected"),
    [
        ("/api/v1/public/library/judgments", True, "hub_search"),
        ("/api/v1/public/library/judgments", False, "hub_browse"),
        ("/api/v1/public/library/judgments/", False, "hub_browse"),
        ("/api/v1/public/library/judgments/some-slug", False, "library_item"),
        ("/api/v1/public/library/judgments/some-slug", True, "library_item"),
        ("/api/v1/public/library/regulations/a/articles/b", False, "library_item"),
        ("/api/v1/workspace/abc/references/3/source", False, "reference_source"),
        ("/api/v1/library/full/regulation/x", False, "library"),
        ("/api/v1/auth/login", False, "account"),
        ("/api/v1/conversations/abc/messages", False, "app"),
        ("/wp-login.php", False, "other"),
    ],
)
def test_routes_classify_into_the_closed_vocabulary(
    path: str, searching: bool, expected: str
) -> None:
    label = gs.classify_route(path, searching=searching)
    assert label == expected
    assert label in gs.ROUTES


def test_a_blank_search_is_browsing(recorder: Recorder) -> None:
    gs.report_refused(
        gs.LIMITER_GLOBAL,
        _request("/api/v1/public/library/judgments", "q=%20%20"),
        key_kind=gs.KEY_ANON,
    )
    assert recorder.named(gs.REFUSED_EVENT)[0]["route"] == "hub_browse"


def test_nothing_raw_reaches_an_attribute(recorder: Recorder) -> None:
    gs.report_refused(
        gs.LIMITER_GLOBAL,
        _request("/api/v1/public/library/judgments", "q=attacker-text"),
        key_kind=gs.KEY_ANON,
    )
    gs.report_refused(
        gs.LIMITER_GLOBAL, _request("/attacker-path"), key_kind=gs.KEY_ANON
    )
    assert "attacker" not in str(recorder.events)


def test_vocabulary_survives_the_scrubber() -> None:
    """A scrubbed label would silently break every alert filtering on it. Logfire's
    default patterns include ``auth`` and ``session``, so this is not hypothetical."""
    scrubbing = pytest.importorskip("logfire._internal.scrubbing")
    patterns = [*scrubbing.DEFAULT_PATTERNS, *_PII_EXTRA_PATTERNS]
    scrubbed = re.compile("|".join(patterns), re.IGNORECASE)
    words = [
        gs.REFUSED_EVENT,
        gs.UNLOCK_EVENT,
        gs.CARRIER_EVENT,
        "limiter",
        "route",
        "key_kind",
        "tier",
        "count",
        "library_user_id",
        "content_type",
        "cost",
        "page_type",
        *gs.LIMITERS,
        *gs.ROUTES,
        *gs.KEY_KINDS,
        *gs.TIERS,
        *library_items.library_item_service.SUPPORTED_PAGE_TYPES,
        "regulation",
        "circular",
        "form",
    ]
    assert [word for word in words if scrubbed.search(word)] == []


def test_a_digit_heavy_uuid_survives_only_under_the_dedicated_key() -> None:
    """Why the attribute is not ``user_id``: this UUID comes back as a fragment under
    that name, and a per-user alert could not name the account."""
    scrubbing = pytest.importorskip("logfire._internal.scrubbing")
    scrubber = scrubbing.Scrubber(_PII_EXTRA_PATTERNS, _scrub_callback)
    value = "ab12cd34-1234-4abc-8def-123456789abc"

    kept = scrubbing.SpanScrubber(scrubber).scrub(("attributes", "library_user_id"), value)
    assert kept == value
    assert ("attributes", "library_user_id") in _KEEP_PATHS

    redacted = scrubbing.SpanScrubber(scrubber).scrub(("attributes", "user_id"), value)
    assert redacted != value


# ---------------------------------------------------------------------------
# Each limiter reports itself
# ---------------------------------------------------------------------------


def _global_limiter_app(redis: FakeRedis) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)
    app.state.redis = redis

    @app.get("/{full_path:path}")
    async def catch_all(full_path: str) -> Dict[str, str]:
        return {"ok": full_path}

    return app


def test_each_limiter_reports_itself(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The global limiter, past its per-path 60/min.
    client = TestClient(_global_limiter_app(FakeRedis()))
    for _ in range(61):
        response = client.get("/api/v1/public/library/judgments?q=abc")
    assert response.status_code == 429

    # The route limiter, past its budget.
    limiter = RouteRateLimiter(scope="test-signal", limit=1)
    client = TestClient(_route_limited_app(FakeRedis(), limiter=limiter))
    client.get("/api/v1/library/full/regulation/x")
    assert client.get("/api/v1/library/full/regulation/x").status_code == 429

    # The item budget, with its window already full.
    monkeypatch.setattr(lb, "item_budget_limit", lambda tier=None: 1)

    async def _full(*_: Any) -> int:
        return 1

    monkeypatch.setattr(lb, "_used_redis", _full)
    monkeypatch.setattr(lb, "_redis", lambda request: object())
    with pytest.raises(LunaHTTPException) as refused:
        asyncio.run(
            lb.enforce_item_budget(
                _request("/api/v1/public/library/judgments", "q=abc"), "u-1", "free"
            )
        )
    assert refused.value.status_code == 429

    lines = recorder.named(gs.REFUSED_EVENT)
    assert [(l["limiter"], l["route"], l["key_kind"], l["tier"]) for l in lines] == [
        ("global", "hub_search", "anon", "none"),
        ("route", "library", "anon", "none"),
        ("item_budget", "hub_search", "user", "free"),
    ]


@pytest.mark.parametrize(
    ("tier", "label"), [("free", "free"), ("paid", "paid"), ("pro", "paid"), (None, "none")]
)
def test_tier_labels(recorder: Recorder, tier: Any, label: str) -> None:
    gs.report_refused(gs.LIMITER_ITEM_BUDGET, None, key_kind=gs.KEY_USER, tier=tier)
    line = recorder.named(gs.REFUSED_EVENT)[0]
    assert line["tier"] == label
    assert line["route"] == "other"


def test_a_bearer_labels_the_global_key_as_user(recorder: Recorder) -> None:
    import jwt as pyjwt

    token = pyjwt.encode({"sub": "abc"}, "k" * 32, algorithm="HS256")
    client = TestClient(_global_limiter_app(FakeRedis()))
    for _ in range(61):
        response = client.get(
            "/api/v1/public/library/judgments",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 429
    assert recorder.named(gs.REFUSED_EVENT)[0]["key_kind"] == "user"


# ---------------------------------------------------------------------------
# Folding
# ---------------------------------------------------------------------------


def test_a_burst_folds_and_the_counts_add_up(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request("/api/v1/public/library/judgments", "q=abc")
    for _ in range(1000):
        gs.report_refused(gs.LIMITER_GLOBAL, request, key_kind=gs.KEY_USER)

    assert len(recorder.events) == 1
    _flush_after_interval(monkeypatch)

    lines = recorder.named(gs.REFUSED_EVENT)
    assert len(lines) == 2
    assert sum(line["count"] for line in lines) == 1000


def test_a_quiet_key_emits_nothing_on_flush(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    gs.report_refused(gs.LIMITER_GLOBAL, _request("/x"), key_kind=gs.KEY_ANON)
    _flush_after_interval(monkeypatch)
    assert len(recorder.events) == 1


def test_an_ordinary_request_carries_a_stopped_burst_out(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A burst that stops has no later 429 to flush it, so without this the alert
    summing ``count`` would read 1."""
    request = _request("/api/v1/public/library/judgments", "q=abc")
    for _ in range(23):
        gs.report_refused(gs.LIMITER_ITEM_BUDGET, request, key_kind=gs.KEY_USER)

    real = time.monotonic
    monkeypatch.setattr(gs.time, "monotonic", lambda: real() + gs._FLUSH_SECONDS + 1)
    client = TestClient(_global_limiter_app(FakeRedis()))
    assert client.get("/anything").status_code == 200

    assert sum(line["count"] for line in recorder.named(gs.REFUSED_EVENT)) == 23


def test_the_fold_table_is_bounded_whatever_is_sent(recorder: Recorder) -> None:
    for n in range(5000):
        gs.report_refused(
            gs.LIMITER_GLOBAL,
            _request(f"/api/v1/public/library/judgments/{n}", f"q={n}"),
            key_kind=gs.KEY_ANON,
        )
    assert len(gs._fold._pending) == 1


# ---------------------------------------------------------------------------
# A telemetry failure never reaches the request
# ---------------------------------------------------------------------------


def test_a_logfire_failure_still_answers_429(recorder: Recorder) -> None:
    recorder.fail = True
    client = TestClient(_global_limiter_app(FakeRedis()))
    for _ in range(61):
        response = client.get("/api/v1/public/library/judgments")
    assert response.status_code == 429


def test_a_bad_request_object_does_not_raise(recorder: Recorder) -> None:
    gs.report_refused(gs.LIMITER_GLOBAL, SimpleNamespace(), key_kind=gs.KEY_ANON)
    assert recorder.events == []


def test_the_library_events_swallow_a_logfire_failure(recorder: Recorder) -> None:
    recorder.fail = True
    gs.report_unlock_written("u-1", "regulation", 2)
    gs.report_carrier_call("u-1", "judgment")


# ---------------------------------------------------------------------------
# Unlock written: the charged path only
# ---------------------------------------------------------------------------


def _resolve_access(monkeypatch: pytest.MonkeyPatch, *, outcome: str) -> Any:
    state = SimpleNamespace(
        locked=False,
        used=0,
        limit=10,
        resets_at=None,
        period_key="2026-11",
        has_room=lambda cost: True,
    )

    async def _library_state(*_: Any) -> Any:
        return state

    monkeypatch.setattr(ls._quota, "library_state", _library_state)
    monkeypatch.setattr(ls, "resolve_gate", lambda *a, **k: "gated")
    monkeypatch.setattr(ls, "_find_unlock_row", lambda *a, **k: None)
    monkeypatch.setattr(ls, "unlock_cost", lambda *a, **k: 3)
    monkeypatch.setattr(ls, "_insert_unlock", lambda *a, **k: outcome)
    return asyncio.run(ls.resolve_access(object(), "u-1", "regulation", "r-1"))


def test_a_charged_unlock_is_reported(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    decision = _resolve_access(monkeypatch, outcome="inserted")
    assert decision.charged is True
    assert recorder.named(gs.UNLOCK_EVENT) == [
        {"library_user_id": "u-1", "content_type": "regulation", "cost": 3}
    ]


@pytest.mark.parametrize("outcome", ["conflict", "failed"])
def test_an_unwritten_unlock_is_not_reported(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    decision = _resolve_access(monkeypatch, outcome=outcome)
    assert decision.charged is False
    assert recorder.named(gs.UNLOCK_EVENT) == []


def test_a_logfire_failure_never_costs_a_reader_their_unlock(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder.fail = True
    assert _resolve_access(monkeypatch, outcome="inserted").may_unlock is True


# ---------------------------------------------------------------------------
# The carrier route
# ---------------------------------------------------------------------------


def _call_carrier(monkeypatch: pytest.MonkeyPatch, page_type: str, *, service: Any) -> Any:
    monkeypatch.setattr(library_items, "get_user_id", lambda *_: "u-1")
    monkeypatch.setattr(library_items.library_item_service, "create_library_item", service)
    monkeypatch.setattr(library_items, "_wi_to_response", lambda item: item)
    monkeypatch.setattr(
        library_items, "LibraryItemResponse", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    return asyncio.run(
        library_items.create_library_item(
            "00000000-0000-4000-8000-000000000001",
            library_items.CreateLibraryItemRequest(page_type=page_type, page_id="slug"),
            current_user=SimpleNamespace(auth_id="a-1"),
            supabase=object(),
        )
    )


def test_a_carry_is_reported(recorder: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    _call_carrier(monkeypatch, " Judgment ", service=lambda *a, **k: ({"item_id": "i"}, False))
    assert recorder.named(gs.CARRIER_EVENT) == [
        {"library_user_id": "u-1", "page_type": "judgment"}
    ]


def test_a_refused_probe_is_still_reported_without_its_raw_type(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(*_: Any, **__: Any) -> Any:
        raise LunaHTTPException(status_code=400, code="VALIDATION_ERROR", detail="x")

    with pytest.raises(LunaHTTPException):
        _call_carrier(monkeypatch, "attacker-type", service=_refuse)

    assert recorder.named(gs.CARRIER_EVENT) == [
        {"library_user_id": "u-1", "page_type": "other"}
    ]
