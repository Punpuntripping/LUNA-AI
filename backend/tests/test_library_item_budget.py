"""The per-user library item budget + the yield-to-open detector.

Plan: ``.claude/plans/cloudflare_navigation_hardening.md`` §2.2 · §2.3

§2.1 (``test_library_filter_hardening.py``) closed the "every filter value is a
fresh page 1" hole and the depth cap bounds anon/free. Neither bounds a PAID
caller: their depth is unbounded by design, which makes them the last tier that
can still traverse the corpus — and the only tier with an identity to charge. So
the hubs now meter the DISTINCT content ids they have yielded per user (rolling
hour) and refuse past it with the project's EXISTING 429.

THE LADDER (owner, 2026-08-02): free 36 · paid 96 · anon never metered. Anon is
bounded by DEPTH instead (``ANON_HUB_MAX_PAGE = 1``), which is why metering it
here is unnecessary as well as unsafe.

The invariants worth naming, because breaking any of them is a production
incident rather than a failing test:

  * ``test_anonymous_browsing_is_never_metered`` — anon traffic arrives through
    the Next ISR renderer as ONE caller. Metering it would meter the renderer and
    take the public library down for everyone. This is the test that must never
    be "fixed" by making it pass with an anon key.
  * ``test_the_429_is_the_projects_existing_envelope`` — one 429 contract across
    the middleware, ``route_limits`` and this budget; a third shape would break
    every client branch that already handles the other two.
  * ``test_an_ordinary_session_never_trips_the_budget`` — what 96 buys a paying
    reader. Its sibling ``test_the_part_4_heavy_session_now_trips_at_96`` records
    that the original PART 4 criterion is deliberately no longer met.
  * ``test_repeating_a_page_costs_nothing`` — DISTINCT ids, not requests. A
    request counter punishes an ordinary reader and is beaten by pagination.

No Redis, no DB, no live app: a fake async Redis records the exact ZSET the
budget builds, and the hub listers are stubbed — the wiring is what is under
test here, not the queries.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import public_library as pl
from backend.app.deps import get_current_user_optional, get_supabase
from backend.app.errors import LunaHTTPException, luna_exception_handler
from backend.app.middleware.rate_limit import RATE_LIMIT_MESSAGE
from backend.app.middleware.route_limits import library_rate_limit
from backend.app.services import case_service
from backend.app.services import library_budget_service as lb
from backend.app.services import library_service as ls

from backend.tests.test_library_gating import (  # noqa: F401
    USER,
    FakeSupabase,
    quota_row,
)

AUTH_ID = "auth-0000-1111"
OTHER_AUTH_ID = "auth-0000-2222"
OTHER_USER = "bbbbbbbb-0000-0000-0000-000000000002"

HUBS = {
    "regulations": ("/api/v1/public/library/regulations", "list_regulations_hub",
                    "regulations_hub_total_pages"),
    "compliance": ("/api/v1/public/library/compliance", "list_compliance_hub",
                   "compliance_hub_total_pages"),
    "circulars": ("/api/v1/public/library/circulars", "list_circulars_hub",
                  "circulars_hub_total_pages"),
    "judgments": ("/api/v1/public/library/judgments", "list_judgments_hub",
                  "judgments_hub_total_pages"),
    "forms": ("/api/v1/public/library/forms", "list_forms_hub",
              "forms_hub_total_pages"),
}

REG_HUB = HUBS["regulations"][0]
JUD_HUB = HUBS["judgments"][0]

# What a real hub page yields.
PAGE_SIZE = 9


# ---------------------------------------------------------------------------
# Fake async Redis — the ZSET + string surface this module uses
# ---------------------------------------------------------------------------


class _FakePipeline:
    def __init__(self, store: "FakeRedis") -> None:
        self._store = store
        self._ops: List[Tuple[str, tuple]] = []

    def zremrangebyscore(self, key: str, lo: float, hi: float) -> "_FakePipeline":
        self._ops.append(("zremrangebyscore", (key, lo, hi)))
        return self

    def zadd(self, key: str, mapping: Dict[str, float], nx: bool = False,
             **_kw: Any) -> "_FakePipeline":
        self._ops.append(("zadd", (key, dict(mapping), nx)))
        return self

    def zcard(self, key: str) -> "_FakePipeline":
        self._ops.append(("zcard", (key,)))
        return self

    def expire(self, key: str, seconds: int) -> "_FakePipeline":
        self._ops.append(("expire", (key, seconds)))
        return self

    async def execute(self) -> List[Any]:
        if self._store.fail:
            raise ConnectionError("redis is down")
        results: List[Any] = []
        for op, args in self._ops:
            if op == "zremrangebyscore":
                key, lo, hi = args
                bucket = self._store.zsets.setdefault(key, {})
                doomed = [m for m, score in bucket.items() if lo <= score <= hi]
                for m in doomed:
                    bucket.pop(m, None)
                results.append(len(doomed))
            elif op == "zadd":
                key, mapping, nx = args
                bucket = self._store.zsets.setdefault(key, {})
                added = 0
                for member, score in mapping.items():
                    if member in bucket:
                        if nx:
                            continue  # NX: the ORIGINAL score stands
                        bucket[member] = score
                        continue
                    bucket[member] = score
                    added += 1
                results.append(added)
            elif op == "zcard":
                results.append(len(self._store.zsets.get(args[0], {})))
            elif op == "expire":
                results.append(True)
        return results


class FakeRedis:
    """In-memory stand-in. ``fail=True`` makes every operation raise."""

    def __init__(self, fail: bool = False) -> None:
        self.zsets: Dict[str, Dict[str, float]] = {}
        self.strings: Dict[str, str] = {}
        self.fail = fail

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    async def set(self, key: str, value: str, ex: Optional[int] = None,
                  nx: bool = False, **_kw: Any) -> Optional[bool]:
        if self.fail:
            raise ConnectionError("redis is down")
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True

    async def zremrangebyscore(self, key: str, lo: float, hi: float) -> int:
        if self.fail:
            raise ConnectionError("redis is down")
        bucket = self.zsets.setdefault(key, {})
        doomed = [m for m, score in bucket.items() if lo <= score <= hi]
        for m in doomed:
            bucket.pop(m, None)
        return len(doomed)

    async def zcard(self, key: str) -> int:
        if self.fail:
            raise ConnectionError("redis is down")
        return len(self.zsets.get(key, {}))

    async def scan_iter(self, match: Optional[str] = None, count: int = 100):
        prefix = (match or "*").rstrip("*")
        for key in list(self.zsets):
            if key.startswith(prefix):
                yield key

    # -- test helpers ------------------------------------------------------

    def age(self, seconds: float) -> None:
        """Push every stored score back in time — simulates the window rolling
        forward without touching the process clock."""
        for bucket in self.zsets.values():
            for member in list(bucket):
                bucket[member] -= seconds


# ---------------------------------------------------------------------------
# App wiring (same throwaway-app style as test_library_filter_hardening)
# ---------------------------------------------------------------------------


class _User:
    """Stands in for AuthUser — the routes only ever read ``auth_id``."""

    def __init__(self, auth_id: str = AUTH_ID) -> None:
        self.auth_id = auth_id
        self.email = "lawyer@example.com"
        self.role = "authenticated"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Module-level TTL caches, the route limiter's fallback and THIS module's
    process-local window; one test's state must never leak into the next."""
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()
    library_rate_limit._fallback.reset()
    lb.reset_process_state()
    monkeypatch.delenv(lb.ITEM_BUDGET_ENV, raising=False)
    monkeypatch.delenv(lb.FREE_ITEM_BUDGET_ENV, raising=False)
    monkeypatch.delenv(lb.PAID_ITEM_BUDGET_ENV, raising=False)
    monkeypatch.delenv(lb.ITEM_BUDGET_WINDOW_ENV, raising=False)
    monkeypatch.delenv(lb.YIELD_ALERT_THRESHOLD_ENV, raising=False)
    yield
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()
    library_rate_limit._fallback.reset()
    lb.reset_process_state()


@pytest.fixture(autouse=True)
def _map_auth_id_to_user_id(monkeypatch):
    mapping = {AUTH_ID: USER, OTHER_AUTH_ID: OTHER_USER}
    monkeypatch.setattr(
        case_service, "get_user_id", lambda supabase, auth_id: mapping.get(auth_id)
    )


@pytest.fixture
def stub_hubs(monkeypatch):
    """Every lister yields a full 9-card page of ids unique to (section, page);
    every counter reports 40 pages. ``calls['list_regulations_hub']`` counts the
    queries actually issued — that is how "the 429 never reaches the DB" is
    asserted."""
    calls: Dict[str, int] = {}

    def _item(section: str, page: int, n: int) -> Dict[str, Any]:
        return {
            "slug": f"{section}-p{page}-{n}",
            "title": "عنوان",
            "status": "active",
            "court": "المحكمة",
            "body_snippet": "",
            "use_case_snippet": "",
            "intro_snippet": "",
            "snippet": "",
        }

    for section, (_path, lister, counter) in HUBS.items():

        def _lister(_supabase: Any, _section: str = section, _name: str = lister,
                    **kw: Any) -> Dict[str, Any]:
            calls[_name] = calls.get(_name, 0) + 1
            page = int(kw.get("page") or 1)
            q = kw.get("q") or ""
            # A distinct ``q`` is a distinct slice of the corpus — that is the
            # whole reason reach has to be metered rather than requests.
            tag = f"{_section}{('-' + str(abs(hash(q)) % 997)) if q else ''}"
            return {
                "items": [_item(tag, page, n) for n in range(PAGE_SIZE)],
                "page": page,
                "total_pages": 40,
            }

        monkeypatch.setattr(ls, lister, _lister)

        def _counter(*_a: Any, _name: str = counter, **_k: Any) -> int:
            calls[_name] = calls.get(_name, 0) + 1
            return 40

        monkeypatch.setattr(ls, counter, _counter)

    return calls


def _app(supabase: Any, user: Optional[_User], redis: Optional[FakeRedis]) -> FastAPI:
    app = FastAPI()
    app.state.redis = redis
    app.add_exception_handler(LunaHTTPException, luna_exception_handler)
    app.include_router(pl.router)
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[get_current_user_optional] = lambda: user
    app.dependency_overrides[library_rate_limit] = lambda: None
    return app


def _client(
    *,
    user: Optional[_User] = None,
    redis: Optional[FakeRedis] = None,
    plan: str = "pro",
    supabase: Any = None,
) -> TestClient:
    """A paid caller by default — the tier with unbounded depth, i.e. the one the
    budget exists for. ``plan='free'`` gets the 3-page cap as well."""
    fake = supabase
    if fake is None:
        fake = FakeSupabase()
        fake.quota_row = quota_row(plan=plan)
    return TestClient(_app(fake, user, redis), client=("8.8.8.8", 51000))


def _bucket(redis: FakeRedis, user_id: str = USER) -> Dict[str, float]:
    return redis.zsets.get(lb.budget_key(user_id, lb.DEFAULT_WINDOW_SECONDS), {})


# ===========================================================================
# 1. Anonymous is never metered — the invariant that must not be "fixed"
# ===========================================================================


def test_anonymous_browsing_is_never_metered(stub_hubs) -> None:
    """⚠ THE ONE THAT MATTERS. Anonymous library traffic reaches this backend
    through the Next ISR renderer — one server-side fetcher, no auth header — so
    every anonymous visitor on the planet arrives as ONE caller. A budget keyed
    on anything available here would meter the RENDERER and take the whole public
    library down the moment it tripped. Bounding that layer is the edge's job."""
    redis = FakeRedis()
    client = _client(redis=redis)  # no user

    for _ in range(50):
        assert client.get(REG_HUB).status_code == 200

    assert redis.zsets == {}, "anonymous traffic must not create a budget bucket"


def test_anonymous_never_sees_a_429_no_matter_how_deep(stub_hubs) -> None:
    """Belt and braces on the same property, from the caller's side."""
    redis = FakeRedis()
    client = _client(redis=redis)
    codes = {client.get(REG_HUB, params={"page": p}).status_code for p in range(1, 30)}
    assert codes == {200}


# ===========================================================================
# 2. Distinct ids, not requests
# ===========================================================================


def test_a_hub_page_charges_exactly_the_ids_it_yielded(stub_hubs) -> None:
    redis = FakeRedis()
    res = _client(user=_User(), redis=redis).get(REG_HUB)

    assert res.status_code == 200
    slugs = {it["slug"] for it in res.json()["items"]}
    assert len(slugs) == PAGE_SIZE
    assert set(_bucket(redis)) == {f"regulations:{s}" for s in slugs}


def test_repeating_a_page_costs_nothing(stub_hubs) -> None:
    """DISTINCT ids is the whole design: re-reading a page (a browser back, a
    re-render, a refetch on focus) must never move the meter, or an ordinary
    reader is metered for the client's behaviour rather than their reach."""
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis)

    for _ in range(40):
        assert client.get(REG_HUB).status_code == 200

    assert len(_bucket(redis)) == PAGE_SIZE


def test_two_wings_sharing_a_slug_are_two_items(stub_hubs) -> None:
    """The member is ``section:slug``. Without the prefix a collision between two
    wings would silently discount reach."""
    keys = lb.item_keys("regulations", [{"slug": "x"}])
    keys += lb.item_keys("judgments", [{"slug": "x"}])
    assert keys == ["regulations:x", "judgments:x"]


def test_distinct_filters_do_move_the_meter(stub_hubs) -> None:
    """The §2.1 residue this control exists for: every distinct ``q`` is a fresh
    page 1 of NEW items, which a request counter would happily allow."""
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis)

    for term in ("نظام", "لائحة", "تنظيم", "قرار"):
        assert client.get(REG_HUB, params={"q": term}).status_code == 200

    assert len(_bucket(redis)) == 4 * PAGE_SIZE


# ===========================================================================
# 3. The refusal
# ===========================================================================


def _exhaust(client: TestClient, limit: int) -> None:
    """Walk enough distinct pages to fill the window."""
    page = 1
    while page <= (limit // PAGE_SIZE) + 2:
        res = client.get(REG_HUB, params={"page": page})
        if res.status_code == 429:
            return
        page += 1
    raise AssertionError("the budget never tripped")


def test_the_budget_trips_once_the_window_is_full(stub_hubs, monkeypatch) -> None:
    monkeypatch.setenv(lb.ITEM_BUDGET_ENV, "27")  # 3 pages
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis)

    assert client.get(REG_HUB, params={"page": 1}).status_code == 200
    assert client.get(REG_HUB, params={"page": 2}).status_code == 200
    assert client.get(REG_HUB, params={"page": 3}).status_code == 200
    assert client.get(REG_HUB, params={"page": 4}).status_code == 429


def test_the_429_is_the_projects_existing_envelope(stub_hubs, monkeypatch) -> None:
    """ONE 429 contract for the whole backend (``rate_limit.py``: "Both produce
    the same 429 body/headers … one contract"). A third shape here would break
    every client branch that already understands the other two."""
    monkeypatch.setenv(lb.ITEM_BUDGET_ENV, "9")
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis)

    assert client.get(REG_HUB, params={"page": 1}).status_code == 200
    res = client.get(REG_HUB, params={"page": 2})

    assert res.status_code == 429
    body = res.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    assert body["error"]["status"] == 429
    assert body["error"]["message"] == RATE_LIMIT_MESSAGE
    assert body["detail"] == body["error"]["message"]
    # Arabic, per Rule #5 — and the SAME string both limiters already use.
    assert not any("a" <= ch.lower() <= "z" for ch in body["detail"])
    assert any("؀" <= ch <= "ۿ" for ch in body["detail"])
    assert res.headers["x-ratelimit-remaining"] == "0"
    assert int(res.headers["x-ratelimit-reset"]) > 0
    assert res.headers["retry-after"] == str(lb.DEFAULT_WINDOW_SECONDS)


def test_the_429_is_never_shared_cached(stub_hubs, monkeypatch) -> None:
    """A refusal belongs to ONE user, and it lands on a URL the edge caches for
    an hour. Parked there it would serve one caller's exhausted budget to every
    visitor asking for that page."""
    monkeypatch.setenv(lb.ITEM_BUDGET_ENV, "9")
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis)
    client.get(REG_HUB, params={"page": 1})

    res = client.get(REG_HUB, params={"page": 2})
    assert res.status_code == 429
    assert res.headers["cache-control"] == "private, no-store"


def test_a_refused_request_never_reaches_the_database(stub_hubs, monkeypatch) -> None:
    """Gate BEFORE the query, or the 429 becomes its own load generator."""
    monkeypatch.setenv(lb.ITEM_BUDGET_ENV, "9")
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis)

    client.get(REG_HUB, params={"page": 1})
    before = stub_hubs["list_regulations_hub"]
    assert client.get(REG_HUB, params={"page": 2}).status_code == 429
    assert stub_hubs["list_regulations_hub"] == before


def test_the_budget_is_shared_across_every_wing(stub_hubs, monkeypatch) -> None:
    """One budget per user, not one per hub — otherwise the ceiling is 5×500 and
    a traverser simply rotates wings."""
    monkeypatch.setenv(lb.ITEM_BUDGET_ENV, str(2 * PAGE_SIZE))
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis)

    assert client.get(REG_HUB).status_code == 200
    assert client.get(HUBS["judgments"][0]).status_code == 200
    assert client.get(HUBS["circulars"][0]).status_code == 429


def test_one_users_budget_never_touches_anothers(stub_hubs, monkeypatch) -> None:
    monkeypatch.setenv(lb.ITEM_BUDGET_ENV, "9")
    redis = FakeRedis()

    first = _client(user=_User(AUTH_ID), redis=redis)
    assert first.get(REG_HUB, params={"page": 1}).status_code == 200
    assert first.get(REG_HUB, params={"page": 2}).status_code == 429

    second = _client(user=_User(OTHER_AUTH_ID), redis=redis)
    assert second.get(REG_HUB, params={"page": 2}).status_code == 200


# ===========================================================================
# 4. The window rolls
# ===========================================================================


def test_the_window_is_rolling_not_fixed(stub_hubs, monkeypatch) -> None:
    """Ids age out of the window; a caller who waits gets their reach back."""
    monkeypatch.setenv(lb.ITEM_BUDGET_ENV, "9")
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis)

    assert client.get(REG_HUB, params={"page": 1}).status_code == 200
    assert client.get(REG_HUB, params={"page": 2}).status_code == 429

    redis.age(lb.DEFAULT_WINDOW_SECONDS + 60)
    assert client.get(REG_HUB, params={"page": 2}).status_code == 200
    assert len(_bucket(redis)) == PAGE_SIZE  # the aged-out page is gone


def test_an_id_is_charged_once_per_window_not_once_per_view(stub_hubs) -> None:
    """``ZADD … NX``: a re-seen id keeps its ORIGINAL score, so it ages out one
    window after FIRST sight and re-rendering cannot extend anyone's residency
    (nor charge them twice)."""
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis)

    assert client.get(REG_HUB).status_code == 200
    first_scores = dict(_bucket(redis))
    redis.age(10)
    assert client.get(REG_HUB).status_code == 200

    aged = {m: s + 10 for m, s in _bucket(redis).items()}
    assert aged == first_scores


# ===========================================================================
# 5. Where the meter deliberately does NOT apply
# ===========================================================================


def test_a_walled_page_yields_nothing_and_charges_nothing(stub_hubs) -> None:
    """A free caller past the 3-page cap gets the CTA wall (items=[]). Charging
    for a body that carries no items would meter a user for being refused."""
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis, plan="free")

    res = client.get(REG_HUB, params={"page": 9})
    assert res.status_code == 200
    assert res.json()["cap_reached"] is True
    assert _bucket(redis) == {}


def test_document_pages_are_not_metered(stub_hubs, monkeypatch) -> None:
    """Only hub/list YIELD is reach. Opening a document is the behaviour the
    product wants and the thing §2.3 looks for the ABSENCE of — metering it would
    charge a reader for reading."""
    monkeypatch.setattr(
        ls, "get_regulation_doc",
        lambda _s, slug: {
            "slug": slug, "title": "ت", "status": "active", "metadata": [],
            "summary_md": None, "gate": "open", "toc": [], "article_index": [],
            "visible_sections": [], "hidden_section_count": 0,
            "official_sources": [], "draft_notice": False,
        },
    )
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis)

    for n in range(20):
        res = client.get(f"/api/v1/public/library/regulations/doc-{n}")
        assert res.status_code == 200

    assert redis.zsets == {}


def test_the_budget_can_be_disabled_entirely(stub_hubs, monkeypatch) -> None:
    """``<= 0`` is the kill switch — the plan ships every threshold loose and
    reserves the right to turn it off without a deploy."""
    monkeypatch.setenv(lb.ITEM_BUDGET_ENV, "0")
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis)

    for page in range(1, 20):
        assert client.get(REG_HUB, params={"page": page}).status_code == 200
    assert redis.zsets == {}


def test_the_limit_and_window_are_env_configurable(monkeypatch) -> None:
    assert lb.item_budget_limit() == 96
    assert lb.item_budget_window_seconds() == 3600
    monkeypatch.setenv(lb.ITEM_BUDGET_ENV, "120")
    monkeypatch.setenv(lb.ITEM_BUDGET_WINDOW_ENV, "900")
    assert lb.item_budget_limit() == 120
    assert lb.item_budget_window_seconds() == 900
    # Junk must never disable the control by accident.
    monkeypatch.setenv(lb.ITEM_BUDGET_ENV, "لا")
    monkeypatch.setenv(lb.ITEM_BUDGET_WINDOW_ENV, "-5")
    assert lb.item_budget_limit() == 96
    assert lb.item_budget_window_seconds() == 3600


# ===========================================================================
# 5b. THE LADDER — free 36 / paid 96 (owner, 2026-08-02)
# ===========================================================================


def test_the_ladder_is_free_36_paid_96() -> None:
    """The shipped rows. ``navigation_enumeration_defence.md`` §3 said 300/500
    and ``cloudflare_navigation_hardening.md`` §2.2 said a flat 500; neither is
    what runs. These two numbers are, and they are the owner's."""
    assert lb.item_budget_limit("free") == 36
    assert lb.item_budget_limit("paid") == 96
    assert (lb.DEFAULT_FREE_ITEM_BUDGET, lb.DEFAULT_PAID_ITEM_BUDGET) == (36, 96)


def test_an_unknown_tier_resolves_to_the_paid_row() -> None:
    """Forgiving in the same direction as the rest of the meter: a tier lookup
    that hiccups must not manufacture a 429 for a legitimate reader. Safe because
    the same failure hands the caller the FREE depth cap (3 pages = 27 ids), so
    they cannot walk far enough to exploit the wider budget."""
    assert lb.item_budget_limit(None) == 96
    assert lb.item_budget_limit("") == 96
    assert lb.item_budget_limit("anon") == 96  # never reached: anon has no user_id


def test_each_row_is_independently_tunable(monkeypatch) -> None:
    monkeypatch.setenv(lb.FREE_ITEM_BUDGET_ENV, "12")
    monkeypatch.setenv(lb.PAID_ITEM_BUDGET_ENV, "240")
    assert lb.item_budget_limit("free") == 12
    assert lb.item_budget_limit("paid") == 240


def test_the_single_knob_overrides_every_row(monkeypatch) -> None:
    """``LIBRARY_USER_ITEM_BUDGET`` stays the one-flip override AND kill switch,
    so an incident does not need two env vars set in the right order."""
    monkeypatch.setenv(lb.FREE_ITEM_BUDGET_ENV, "12")
    monkeypatch.setenv(lb.PAID_ITEM_BUDGET_ENV, "240")
    monkeypatch.setenv(lb.ITEM_BUDGET_ENV, "50")
    assert lb.item_budget_limit("free") == lb.item_budget_limit("paid") == 50
    monkeypatch.setenv(lb.ITEM_BUDGET_ENV, "0")
    assert lb.item_budget_limit("free") == lb.item_budget_limit("paid") == 0


def test_a_free_caller_is_metered_on_the_free_row(stub_hubs) -> None:
    """End-to-end: the tier the hub resolved for the DEPTH cap is the tier the
    budget charges against. A free account exhausts at 36, not 96."""
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis, plan="free")

    # Free depth caps at page 3, so breadth is the only way to spend: distinct
    # filter slices, each a fresh page 1 of 9.
    codes = [
        client.get(REG_HUB, params={"q": term}).status_code
        for term in ("عمل", "تجارة", "شركات", "ضريبة", "تأمين", "عقار")
    ]
    assert 429 in codes, codes
    assert len(_bucket(redis)) <= lb.DEFAULT_FREE_ITEM_BUDGET + PAGE_SIZE


# ===========================================================================
# 6. Headroom — the PART 4 acceptance criterion
# ===========================================================================


def test_an_ordinary_session_never_trips_the_budget(stub_hubs) -> None:
    """What 96 actually buys a paying reader: three wings browsed to their third
    page, a filtered slice, and every page re-visited once (browser back).

    Re-visits are the point — they are free by construction, so an ordinary
    reader who backtracks pays nothing for it. That is 10 distinct hub pages = 90
    ids against 96. Documents are opened via the doc routes, which are unmetered,
    so the ~20 documents in the PART 4 criterion cost this session nothing."""
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis)
    codes = set()

    for section in ("regulations", "compliance", "judgments"):
        path = HUBS[section][0]
        for page in (1, 2, 3):
            codes.add(client.get(path, params={"page": page}).status_code)
            codes.add(client.get(path, params={"page": page}).status_code)  # back
    codes.add(client.get(REG_HUB, params={"doc_type": "law_statute"}).status_code)

    assert codes == {200}
    assert len(_bucket(redis)) <= lb.DEFAULT_PAID_ITEM_BUDGET


def test_the_part_4_heavy_session_now_trips_at_96(stub_hubs) -> None:
    """⚠ THE COST OF THE 2026-08-02 TIGHTENING — recorded, not hidden.

    PART 4's acceptance criterion was "normal lawyer session (30 min, filters +
    20 documents) → never challenged, never 429", and the session below is how
    this suite modelled it at 500: five wings to page 3, four searches, two
    filtered slices ≈ 189 ids. At the owner's 96 that criterion is NO LONGER MET
    — this session 429s roughly halfway through.

    That is a deliberate trade (96 was confirmed as intent, knowing it refuses a
    12-page walk), so this test pins the consequence rather than asserting the
    old promise. If the ceiling is ever raised, expect this test to fail: it is
    the tripwire that says the trade was reconsidered, and the sibling above is
    the one that says an ordinary session still fits."""
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis)
    codes = set()

    for section, (path, _l, _c) in HUBS.items():
        for page in (1, 2, 3):
            codes.add(client.get(path, params={"page": page}).status_code)
            codes.add(client.get(path, params={"page": page}).status_code)  # back
    for term in ("نظام العمل", "لائحة تنفيذية", "تنظيم", "قرار وزاري"):
        codes.add(client.get(REG_HUB, params={"q": term}).status_code)
    codes.add(client.get(REG_HUB, params={"doc_type": "law_statute"}).status_code)
    codes.add(client.get(JUD_HUB, params={"court_level": "appeal"}).status_code)

    assert 429 in codes, codes
    # The overshoot is bounded at one page: the gate refuses BEFORE the query, so
    # nothing past limit + one page's worth is ever charged.
    assert len(_bucket(redis)) <= lb.DEFAULT_PAID_ITEM_BUDGET + PAGE_SIZE


# ===========================================================================
# 7. Storage failure
# ===========================================================================


def test_a_redis_outage_falls_back_to_the_process_window(stub_hubs, monkeypatch) -> None:
    """Fail-CLOSED like ``route_limits``, not fail-open like the global
    middleware: this is a boundary, not a damper. Per-process, so it is a floor
    rather than an exact cap — and because it can only ever count LESS than Redis
    would, an outage cannot manufacture a 429 for a legitimate reader."""
    monkeypatch.setenv(lb.ITEM_BUDGET_ENV, "9")
    client = _client(user=_User(), redis=FakeRedis(fail=True))

    assert client.get(REG_HUB, params={"page": 1}).status_code == 200
    assert client.get(REG_HUB, params={"page": 2}).status_code == 429


def test_no_redis_at_all_still_serves_pages(stub_hubs) -> None:
    """``app.state.redis`` is ``None`` for the whole of a Redis outage
    (``main.py``'s supervisor parks it there). The hubs must keep serving."""
    client = _client(user=_User(), redis=None)
    assert client.get(REG_HUB).status_code == 200


def test_a_charge_failure_never_breaks_the_response(stub_hubs) -> None:
    """The caller already paid for this page with a DB round-trip. Metering is an
    abuse bound, not a correctness property of the body."""

    class _BrokenCharge(FakeRedis):
        async def _boom(self, *_a: Any, **_k: Any) -> None:
            raise ConnectionError("write path is down")

        def pipeline(self):  # type: ignore[override]
            pipe = super().pipeline()
            if getattr(self, "_reads_done", 0) >= 1:
                raise ConnectionError("write path is down")
            self._reads_done = getattr(self, "_reads_done", 0) + 1
            return pipe

    res = _client(user=_User(), redis=_BrokenCharge()).get(REG_HUB)
    assert res.status_code == 200
    assert len(res.json()["items"]) == PAGE_SIZE


# ===========================================================================
# 8. §2.3 — the yield-to-open detector (detection, never enforcement)
# ===========================================================================


class _ShelfResult:
    def __init__(self, data: List[Dict[str, Any]], count: Optional[int]) -> None:
        self.data = data
        self.count = count


class _ShelfChain:
    """The one PostgREST shape §2.3 issues: ``select(count='exact')`` + ``eq`` +
    ``gte`` + ``limit(1)``. Written here rather than bolted onto the shared
    ``_Chain`` (which has no ``gte``) so this file cannot change how the other
    library test modules see the fake."""

    def __init__(self, rows: List[Dict[str, Any]], fail: bool) -> None:
        self._rows = rows
        self._fail = fail
        self._eq: List[Tuple[str, Any]] = []
        self._gte: List[Tuple[str, Any]] = []
        self._count: Optional[str] = None
        self._limit: Optional[int] = None

    def select(self, *_cols: Any, count: Optional[str] = None, **_k: Any) -> "_ShelfChain":
        self._count = count
        return self

    def eq(self, col: str, val: Any) -> "_ShelfChain":
        self._eq.append((col, val))
        return self

    def gte(self, col: str, val: Any) -> "_ShelfChain":
        self._gte.append((col, val))
        return self

    def limit(self, n: int) -> "_ShelfChain":
        self._limit = n
        return self

    def execute(self) -> _ShelfResult:
        if self._fail:
            raise RuntimeError("simulated PostgREST failure on library_items")
        rows = [
            r for r in self._rows
            if all(str(r.get(c)) == str(v) for c, v in self._eq)
            # NULL is never >= a bound — the real SQL semantics, and the reason a
            # «حفظ» pin (last_used_at NULL) does not read as an open.
            and all(
                r.get(c) is not None and str(r.get(c)) >= str(v)
                for c, v in self._gte
            )
        ]
        count = len(rows) if self._count == "exact" else None
        if self._limit is not None:
            rows = rows[: self._limit]
        return _ShelfResult(rows, count)


class _ShelfSupabase(FakeSupabase):
    """FakeSupabase + a ``library_items`` table shaped like migration 106."""

    def __init__(
        self,
        rows: List[Dict[str, Any]],
        *,
        plan: str = "pro",
        shelf_fails: bool = False,
    ) -> None:
        super().__init__()
        self.tables["library_items"] = list(rows)
        self.quota_row = quota_row(plan=plan)
        self._shelf_fails = shelf_fails

    def table(self, name: str) -> Any:
        if name == "library_items":
            return _ShelfChain(self.tables["library_items"], self._shelf_fails)
        return super().table(name)


def _shelf_supabase(
    rows: List[Dict[str, Any]], *, plan: str = "pro", shelf_fails: bool = False
) -> _ShelfSupabase:
    return _ShelfSupabase(rows, plan=plan, shelf_fails=shelf_fails)


def _shelf_row(user_id: str = USER, *, last_used_at: Optional[str]) -> Dict[str, Any]:
    """One ``library_items`` row in its real shape (migration 106)."""
    return {
        "item_row_id": "r1",
        "user_id": user_id,
        "content_type": "regulation",
        "content_id": "11111111-2222-3333-4444-555555555555",
        "source": "auto" if last_used_at else "manual",
        "use_count": 1 if last_used_at else 0,
        "first_used_at": last_used_at,
        "last_used_at": last_used_at,
        "saved_at": "2026-07-28T00:00:00+00:00",
    }


def test_open_count_reads_library_items_and_ignores_stale_rows() -> None:
    fake = _shelf_supabase(
        [
            _shelf_row(last_used_at="2999-01-01T00:00:00+00:00"),   # inside window
            _shelf_row(last_used_at="2000-01-01T00:00:00+00:00"),   # long past
            _shelf_row(last_used_at=None),                          # «حفظ» pin only
        ]
    )
    assert asyncio.run(lb.count_document_opens(fake, USER)) == 1


def test_a_saved_but_never_opened_row_is_not_an_open() -> None:
    """Migration 106 inserts a «حفظ» pin with ``use_count=0`` and NO
    ``last_used_at``. Counting it would hide the exact signal §2.3 looks for."""
    fake = _shelf_supabase([_shelf_row(last_used_at=None)])
    assert asyncio.run(lb.count_document_opens(fake, USER)) == 0


def test_the_sweep_flags_reach_without_a_single_open() -> None:
    redis = FakeRedis()
    key = lb.budget_key(USER, lb.DEFAULT_WINDOW_SECONDS)
    redis.zsets[key] = {f"regulations:r{i}": 1e12 for i in range(250)}
    fake = _shelf_supabase([])

    flagged = asyncio.run(lb.yield_to_open_report(redis, fake))
    assert flagged == [
        {"user_id": USER, "yielded": 250, "opens": 0,
         "window_seconds": lb.DEFAULT_WINDOW_SECONDS}
    ]


def test_the_sweep_does_not_flag_a_reader() -> None:
    """Reach WITH reading is a lawyer doing research. The signature is reach
    without a single open — anything else is a false positive on a paying user."""
    redis = FakeRedis()
    redis.zsets[lb.budget_key(USER, lb.DEFAULT_WINDOW_SECONDS)] = {
        f"regulations:r{i}": 1e12 for i in range(250)
    }
    fake = _shelf_supabase([_shelf_row(last_used_at="2999-01-01T00:00:00+00:00")])

    assert asyncio.run(lb.yield_to_open_report(redis, fake)) == []


def test_the_sweep_ignores_sessions_under_the_threshold() -> None:
    redis = FakeRedis()
    redis.zsets[lb.budget_key(USER, lb.DEFAULT_WINDOW_SECONDS)] = {
        f"regulations:r{i}": 1e12 for i in range(10)
    }
    assert asyncio.run(lb.yield_to_open_report(redis, _shelf_supabase([]))) == []


def test_the_inline_detector_warns_once_and_serves_the_page(
    stub_hubs, monkeypatch, caplog
) -> None:
    """Detection rides the charge path, is guarded by ``SET NX EX`` so it costs
    at most one count query per user per window, and CANNOT change the response.
    """
    monkeypatch.setenv(lb.YIELD_ALERT_THRESHOLD_ENV, str(PAGE_SIZE))
    redis = FakeRedis()
    client = _client(user=_User(), redis=redis, supabase=_shelf_supabase([]))

    with caplog.at_level(logging.WARNING, logger=lb.logger.name):
        assert client.get(REG_HUB, params={"page": 1}).status_code == 200  # 9 — no
        assert client.get(REG_HUB, params={"page": 2}).status_code == 200  # 18 — yes
        assert client.get(REG_HUB, params={"page": 3}).status_code == 200  # once only

    alerts = [r for r in caplog.records if "yield-to-open alert" in r.getMessage()]
    assert len(alerts) == 1, [r.getMessage() for r in caplog.records]
    assert str(USER) in alerts[0].getMessage()


def test_the_detector_never_blocks_when_the_shelf_query_explodes(
    stub_hubs, monkeypatch
) -> None:
    """§2.3 is DETECTION. A broken detector must cost a log line, never a page."""
    monkeypatch.setenv(lb.YIELD_ALERT_THRESHOLD_ENV, "1")
    fake = _shelf_supabase([], shelf_fails=True)
    client = _client(user=_User(), redis=FakeRedis(), supabase=fake)

    res = client.get(REG_HUB)
    assert res.status_code == 200
    assert len(res.json()["items"]) == PAGE_SIZE


def test_the_detector_is_off_when_the_threshold_is_disabled(
    stub_hubs, monkeypatch, caplog
) -> None:
    monkeypatch.setenv(lb.YIELD_ALERT_THRESHOLD_ENV, "0")
    client = _client(user=_User(), redis=FakeRedis(), supabase=_shelf_supabase([]))
    with caplog.at_level(logging.WARNING, logger=lb.logger.name):
        assert client.get(REG_HUB).status_code == 200
    assert not [r for r in caplog.records if "yield-to-open" in r.getMessage()]


# ===========================================================================
# 9. Module invariants
# ===========================================================================


def test_the_budget_key_round_trips_the_user_id() -> None:
    """``yield_to_open_report`` scans these keys and joins the id back to
    ``library_items``; a key format change that breaks this makes the sweep
    silently report nothing."""
    key = lb.budget_key(USER, 3600)
    assert key.startswith("library:itembudget:")
    assert lb.user_id_from_budget_key(key) == USER
    assert lb.user_id_from_budget_key("ratelimit:route:library:user:x:60") is None


def test_item_keys_skips_unslugged_rows_instead_of_merging_them() -> None:
    keys = lb.item_keys("forms", [{"slug": "a"}, {"slug": ""}, {}, {"slug": "a"}])
    assert keys == ["forms:a"]


def test_one_response_cannot_charge_an_unbounded_number_of_ids() -> None:
    keys = lb.item_keys("regulations", [{"slug": f"s{i}"} for i in range(1000)])
    assert len(keys) == lb.MAX_KEYS_PER_CALL


def test_the_process_fallback_is_bounded() -> None:
    """It must not turn a Redis outage into a memory incident."""
    window = lb._ProcessLocalDistinctWindow()
    for i in range(window.MAX_TRACKED_IDENTITIES + 50):
        window.add(f"user-{i}", [f"regulations:s{i}"], 1e12, 3600, 10)
    assert len(window._buckets) <= window.MAX_TRACKED_IDENTITIES

    for i in range(50):
        window.add("user-0", [f"regulations:x{i}"], 1e12, 3600, 10)
    assert len(window._buckets["user-0"]) <= 11  # limit + 1
