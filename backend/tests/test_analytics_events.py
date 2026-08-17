"""The product-analytics beacon — `POST /api/v1/public/events` (Phase 0).

Covers `.claude/plans/product_analytics.md` §5.5 and the traps it names. Five of
these tests exist because the endpoint's value depends on things NOT happening:

* **T1** — Googlebot renders JavaScript, so it executes the tracker. A batch
  carrying the verified-bot signal must vanish, or every metric is polluted by
  a crawler that visits every page and converts on nothing.
* **§2** — the raw User-Agent and the IP are never written. Both are available
  to the handler and both must die there: the UA becomes three buckets, the IP
  is a Redis key and nothing else.
* **§5.5** — 204, always. There is no input (garbage JSON, an oversized batch,
  a dead table, a rate-limit refusal) that produces a non-204 response.
* **Taxonomy** — an unknown `event_name` is dropped WITHOUT costing the rest of
  its batch, so one stale client cannot take a flush down with it.
* **user_type** — set from the token at event time, which is the whole reason
  it is a column rather than `user_id is null` (migration 138).

No Redis, no DB, no live app: a fake Supabase records exactly what would have
been written, and the route limiter falls back to its in-process window because
`app.state.redis` is absent.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import analytics
from backend.app.deps import get_supabase
from backend.app.services import analytics_service as svc
from shared.auth.jwt import AuthUser


SESSION = "sess-0123456789abcdef"
AUTH_ID = "auth-0000-1111"
USER_ID = "aaaaaaaa-0000-0000-0000-000000000001"

# A real Safari-on-iPhone string. Distinctive enough that any leak into a row is
# caught by substring, which is how the "never store the raw UA" test works.
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


# ---------------------------------------------------------------------------
# Fake Supabase — records inserts/deletes, answers the users lookup
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data: Any = None, count: Optional[int] = None) -> None:
        self.data = data
        self.count = count


class _Table:
    def __init__(self, fake: "FakeSupabase", name: str) -> None:
        self._fake = fake
        self._name = name
        self._op: Optional[str] = None
        self._kwargs: dict = {}
        self._filters: list[tuple] = []
        self._rows: Any = None

    def insert(self, rows: Any, **kwargs: Any) -> "_Table":
        self._op, self._rows, self._kwargs = "insert", rows, kwargs
        return self

    def delete(self, **kwargs: Any) -> "_Table":
        self._op, self._kwargs = "delete", kwargs
        return self

    def select(self, *_a: Any, **_kw: Any) -> "_Table":
        self._op = "select"
        return self

    def eq(self, column: str, value: Any) -> "_Table":
        self._filters.append(("eq", column, value))
        return self

    def lt(self, column: str, value: Any) -> "_Table":
        self._filters.append(("lt", column, value))
        return self

    def limit(self, _n: int) -> "_Table":
        return self

    def execute(self) -> _Result:
        if self._fake.explode:
            raise RuntimeError("relation \"analytics_events\" does not exist")
        if self._op == "insert":
            rows = self._rows if isinstance(self._rows, list) else [self._rows]
            self._fake.inserts.setdefault(self._name, []).extend(rows)
            self._fake.insert_kwargs.append(self._kwargs)
            return _Result(data=[])
        if self._op == "delete":
            self._fake.deletes.append(
                {"table": self._name, "kwargs": self._kwargs, "filters": self._filters}
            )
            return _Result(data=[], count=self._fake.delete_count)
        if self._name == "users":
            wanted = {c: v for _op, c, v in self._filters}.get("auth_id")
            user_id = self._fake.users.get(wanted)
            return _Result(data=[{"user_id": user_id}] if user_id else [])
        return _Result(data=[])


class FakeSupabase:
    def __init__(self) -> None:
        self.inserts: dict[str, list[dict]] = {}
        self.insert_kwargs: list[dict] = []
        self.deletes: list[dict] = []
        self.users: dict[str, str] = {AUTH_ID: USER_ID}
        self.delete_count = 0
        self.explode = False

    def table(self, name: str) -> _Table:
        return _Table(self, name)

    # -- convenience -------------------------------------------------------

    @property
    def events(self) -> list[dict]:
        return self.inserts.get("analytics_events", [])


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_limiter():
    """The limiter is a module singleton; TestClient always reports the same
    client host, so without this every test shares one bucket and the ordering
    of the file would decide who gets throttled."""
    analytics.analytics_rate_limit._fallback.reset()
    yield
    analytics.analytics_rate_limit._fallback.reset()


def _client(fake: FakeSupabase) -> TestClient:
    app = FastAPI()
    app.include_router(analytics.router)
    app.dependency_overrides[get_supabase] = lambda: fake
    return TestClient(app)


def _post(
    client: TestClient,
    events: list[dict],
    *,
    headers: Optional[dict] = None,
    token: Optional[str] = None,
    raw: Optional[str] = None,
):
    hdrs = dict(headers or {})
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if raw is not None:
        hdrs.setdefault("content-type", "application/json")
        return client.post("/api/v1/public/events", content=raw, headers=hdrs)
    return client.post("/api/v1/public/events", json={"events": events}, headers=hdrs)


def _event(name: str = "page_view", **over: Any) -> dict:
    row = {"event_name": name, "session_key": SESSION, "path": "/library/labor"}
    row.update(over)
    return row


# ===========================================================================
# 1. The happy path
# ===========================================================================


def test_a_batch_is_accepted_and_written_in_order() -> None:
    fake = FakeSupabase()
    res = _post(
        _client(fake),
        [
            _event("session_start", path="/blog"),
            _event("page_view", path="/blog/post-1"),
            _event("gate_view", props={"gate_kind": "blog_cta"}),
        ],
    )

    assert res.status_code == 204
    assert res.content == b""

    rows = fake.events
    assert [r["event_name"] for r in rows] == [
        "session_start",
        "page_view",
        "gate_view",
    ]
    # Order is the contract: event_id is a bigserial and the §6b chat-depth
    # queries walk one run's events by it, so a reordered insert silently
    # scrambles every "did they wait" metric.
    assert rows[0]["session_key"] == SESSION
    assert rows[2]["props"]["gate_kind"] == "blog_cta"
    # Nobody reads inserted analytics rows back.
    assert fake.insert_kwargs[0].get("returning") == "minimal"


def test_the_whole_taxonomy_is_accepted() -> None:
    """All 22 names in §3 + §3b must be storable — a name the plan lists but the
    endpoint rejects is a funnel with a silent hole in it."""
    fake = FakeSupabase()
    names = sorted(svc.EVENT_NAMES)
    assert len(names) == 22
    for chunk_start in range(0, len(names), svc.MAX_BATCH_EVENTS):
        chunk = names[chunk_start : chunk_start + svc.MAX_BATCH_EVENTS]
        assert _post(_client(fake), [_event(n) for n in chunk]).status_code == 204

    assert {r["event_name"] for r in fake.events} == set(names)


def test_the_beacon_content_type_is_accepted() -> None:
    """`navigator.sendBeacon` sends `text/plain` unless the client wraps the
    payload in a typed Blob, and the anonymous flush path uses sendBeacon. A
    body parsed through a declared Pydantic model would 415/422 exactly the
    anonymous half of the funnel — the half this whole plan is about."""
    fake = FakeSupabase()
    res = _client(fake).post(
        "/api/v1/public/events",
        content=json.dumps({"events": [_event()]}),
        headers={"content-type": "text/plain;charset=UTF-8"},
    )
    assert res.status_code == 204
    assert len(fake.events) == 1


def test_an_empty_batch_writes_nothing_and_still_204s() -> None:
    fake = FakeSupabase()
    assert _post(_client(fake), []).status_code == 204
    assert fake.events == []


# ===========================================================================
# 2. Unknown event names are dropped, not fatal
# ===========================================================================


def test_unknown_event_names_are_dropped_without_losing_the_batch() -> None:
    fake = FakeSupabase()
    res = _post(
        _client(fake),
        [
            _event("page_view"),
            _event("totally_made_up"),
            _event("PAGE_VIEW"),          # case matters — not the same name
            _event("page_exit"),
        ],
    )

    assert res.status_code == 204
    assert [r["event_name"] for r in fake.events] == ["page_view", "page_exit"]


def test_events_without_a_session_key_are_dropped() -> None:
    """`session_key` is NOT NULL and is the join key for bounce, exit page and
    gate abandonment. An unkeyed event is noise that would also 500 the insert
    and take its whole batch with it."""
    fake = FakeSupabase()
    _post(
        _client(fake),
        [
            {"event_name": "page_view"},
            {"event_name": "page_view", "session_key": "   "},
            _event("page_view"),
        ],
    )
    assert len(fake.events) == 1


def test_malformed_bodies_are_a_silent_204() -> None:
    """Never a 422. `sendBeacon` drops the response unread, so an error status
    buys nothing and costs a red console line per visitor (§7 T9)."""
    fake = FakeSupabase()
    client = _client(fake)
    for body in ("not json at all", "[]", '{"events": "nope"}', '{"nope": 1}', ""):
        assert _post(client, [], raw=body).status_code == 204
    assert fake.events == []


def test_a_dead_table_is_still_a_204() -> None:
    fake = FakeSupabase()
    fake.explode = True
    assert _post(_client(fake), [_event()]).status_code == 204


# ===========================================================================
# 3. T1 — verified bots never enter the dataset
# ===========================================================================


def test_verified_bot_header_drops_the_entire_batch() -> None:
    fake = FakeSupabase()
    res = _post(
        _client(fake),
        [_event("session_start"), _event("page_view")],
        headers={"x-verified-bot": "1"},
    )

    assert res.status_code == 204
    assert fake.events == []


def test_a_bot_signal_on_any_copy_of_the_header_still_drops() -> None:
    fake = FakeSupabase()
    res = _client(fake).post(
        "/api/v1/public/events",
        json={"events": [_event()]},
        headers={"x-verified-bot": "true"},
    )
    assert res.status_code == 204
    assert fake.events == []


def test_crawler_user_agents_are_dropped_without_the_edge_header() -> None:
    """The edge header is absent on plenty of real crawler traffic today, and a
    crawler that renders JS pollutes exactly the wings built for crawlers."""
    fake = FakeSupabase()
    client = _client(fake)
    for ua in (
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
        "Mozilla/5.0 HeadlessChrome/120.0.0.0",
    ):
        assert _post(client, [_event()], headers={"user-agent": ua}).status_code == 204
    assert fake.events == []


def test_a_human_user_agent_is_kept() -> None:
    """Anchor for the three drop tests above — without it they could all pass
    because the endpoint stopped writing anything at all."""
    fake = FakeSupabase()
    _post(_client(fake), [_event()], headers={"user-agent": IPHONE_UA})
    assert len(fake.events) == 1


# ===========================================================================
# 4. §2 — the raw UA and the IP never reach a row
# ===========================================================================


def test_the_raw_user_agent_is_bucketed_and_discarded() -> None:
    fake = FakeSupabase()
    _post(_client(fake), [_event()], headers={"user-agent": IPHONE_UA})

    row = fake.events[0]
    assert (row["device_type"], row["browser"], row["os"]) == ("mobile", "safari", "ios")

    blob = json.dumps(fake.events, ensure_ascii=False)
    for fingerprint in ("Mozilla", "AppleWebKit", "15E148", "605.1.15", IPHONE_UA):
        assert fingerprint not in blob


def test_the_client_ip_never_reaches_a_row() -> None:
    """The IP is a rate-limit key and nothing else (§2). It arrives on the
    request and must not appear in what is written."""
    fake = FakeSupabase()
    _post(
        _client(fake),
        [_event()],
        headers={"x-forwarded-for": "203.0.113.77", "cf-connecting-ip": "198.51.100.9"},
    )
    blob = json.dumps(fake.events)
    assert "203.0.113.77" not in blob
    assert "198.51.100.9" not in blob


def test_client_hints_decide_the_device_when_the_ua_is_generic() -> None:
    fake = FakeSupabase()
    _post(
        _client(fake),
        [_event()],
        headers={
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64)",
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "sec-ch-ua": '"Chromium";v="120", "Google Chrome";v="120"',
        },
    )
    row = fake.events[0]
    assert (row["device_type"], row["browser"], row["os"]) == (
        "mobile",
        "chrome",
        "android",
    )


# ===========================================================================
# 5. user_type — authed vs anon
# ===========================================================================


def test_no_token_is_anonymous() -> None:
    fake = FakeSupabase()
    _post(_client(fake), [_event()])
    row = fake.events[0]
    assert row["user_type"] == "anon"
    assert row["user_id"] is None


def test_a_verified_token_is_authed_and_carries_the_user_id(monkeypatch) -> None:
    monkeypatch.setattr(
        analytics,
        "extract_user",
        lambda _t: AuthUser(auth_id=AUTH_ID, email="a@b.com", role="authenticated"),
    )
    fake = FakeSupabase()
    _post(_client(fake), [_event("chat_send")], token="good-token")

    row = fake.events[0]
    assert row["user_type"] == "authed"
    assert row["user_id"] == USER_ID


def test_an_unverifiable_token_degrades_to_anon(monkeypatch) -> None:
    """Forged / expired / JWKS-down are all "anonymous", never an error: the
    beacon has no way to prompt a re-login and must not 401 a reader."""
    def _boom(_t):
        raise RuntimeError("signature verification failed")

    monkeypatch.setattr(analytics, "extract_user", _boom)
    fake = FakeSupabase()
    res = _post(_client(fake), [_event()], token="forged")

    assert res.status_code == 204
    assert fake.events[0]["user_type"] == "anon"
    assert fake.events[0]["user_id"] is None


def test_authed_survives_an_unresolvable_users_row(monkeypatch) -> None:
    """user_type comes from the TOKEN, not from the lookup. This is the case
    migration 138's column comment is about: a NULL user_id must not silently
    re-classify a signed-in actor as anonymous."""
    monkeypatch.setattr(
        analytics,
        "extract_user",
        lambda _t: AuthUser(auth_id="unknown-auth", email="a@b.com", role="authenticated"),
    )
    fake = FakeSupabase()
    _post(_client(fake), [_event()], token="good-token")

    row = fake.events[0]
    assert row["user_type"] == "authed"
    assert row["user_id"] is None


# ===========================================================================
# 6. Batch cap + rate limit
# ===========================================================================


def test_an_oversized_batch_is_refused_whole() -> None:
    """21 events is a client bug. Keeping 20 of them would hide it while still
    losing whatever the 21st belonged to."""
    fake = FakeSupabase()
    res = _post(_client(fake), [_event() for _ in range(svc.MAX_BATCH_EVENTS + 1)])

    assert res.status_code == 204
    assert fake.events == []


def test_a_full_batch_at_the_cap_is_accepted() -> None:
    fake = FakeSupabase()
    _post(_client(fake), [_event() for _ in range(svc.MAX_BATCH_EVENTS)])
    assert len(fake.events) == svc.MAX_BATCH_EVENTS


def test_an_oversized_body_is_refused_before_parsing() -> None:
    fake = FakeSupabase()
    huge = json.dumps({"events": [_event(props={"x": "y" * 400})]})
    padded = huge[:-1] + ',"pad":"' + "z" * analytics.MAX_BODY_BYTES + '"}'
    assert _post(_client(fake), [], raw=padded).status_code == 204
    assert fake.events == []


def test_the_rate_limit_is_enforced_per_ip(monkeypatch) -> None:
    """Refusal is a silent drop, not a 429 — but it IS a refusal: nothing is
    written once the budget is spent."""
    monkeypatch.setattr(analytics.analytics_rate_limit, "limit", 3)
    fake = FakeSupabase()
    client = _client(fake)

    for _ in range(5):
        assert _post(client, [_event()]).status_code == 204

    assert len(fake.events) == 3


def test_two_flushes_on_tab_hide_are_never_throttled() -> None:
    """The client double-flushes on hide by design. The real budget must have
    room for that many times over, or the exit event — the one page_exit and
    the abandonment metrics depend on — is the one that gets dropped."""
    assert analytics.ANALYTICS_RATE_LIMIT >= 20


# ===========================================================================
# 7. Field sanitation (§2, §7 T4)
# ===========================================================================


def test_query_strings_are_stripped_from_the_path() -> None:
    """T4: `?q=` on the search surfaces is user-typed legal text — potentially a
    case description. Enforced here, not only in the client."""
    fake = FakeSupabase()
    _post(
        _client(fake),
        [
            _event(path="/search?q=%D8%B9%D9%82%D8%AF+%D8%A5%D9%8A%D8%AC%D8%A7%D8%B1"),
            _event(path="https://rayhanai.com/library/labor?utm_source=x#frag"),
        ],
    )
    assert [r["path"] for r in fake.events] == ["/search", "/library/labor"]


def test_session_start_lifts_referrer_and_utm_into_columns() -> None:
    """Acceptance query 5 reads the COLUMNS. If they stay NULL because the
    values were left in props, the referrer/utm breakdown cannot run."""
    fake = FakeSupabase()
    _post(
        _client(fake),
        [
            _event(
                "session_start",
                path="/blog/post-1",
                props={
                    "entry_path": "/blog/post-1",
                    "referrer_host": "https://www.linkedin.com/feed/?trk=secret",
                    "utm_source": "linkedin",
                    "utm_medium": "social",
                    "utm_campaign": "launch",
                },
            )
        ],
    )
    row = fake.events[0]
    assert row["referrer_host"] == "www.linkedin.com"   # host only, never the URL
    assert row["utm_source"] == "linkedin"
    assert row["utm_medium"] == "social"
    assert row["utm_campaign"] == "launch"
    assert row["props"]["entry_path"] == "/blog/post-1"
    # The full URL must not survive anywhere on the row.
    assert "trk=secret" not in json.dumps(row)


def test_attribution_columns_are_session_start_only() -> None:
    """"Populated on session_start only" (§4). A page_view carrying utm_* would
    double-count the session's source."""
    fake = FakeSupabase()
    _post(
        _client(fake),
        [_event("page_view", props={"utm_source": "linkedin", "referrer_host": "x.com"})],
    )
    row = fake.events[0]
    assert "utm_source" not in row or row.get("utm_source") is None
    assert "referrer_host" not in row or row.get("referrer_host") is None


def test_every_row_in_a_batch_carries_the_same_keys() -> None:
    """PostgREST's bulk insert refuses (older versions) or has to guess (newer)
    when the objects in one array have different key sets — and a batch is
    exactly the mixed shape that trips it, since only `session_start` fills the
    attribution columns. The insert error would be SWALLOWED, so the symptom
    would be an empty table rather than an alarm."""
    fake = FakeSupabase()
    _post(
        _client(fake),
        [
            _event("session_start", props={"utm_source": "linkedin"}),
            _event("page_view"),
            _event("chat_send", props={"message_id": "m-1"}),
        ],
    )
    key_sets = {frozenset(r) for r in fake.events}
    assert len(fake.events) == 3
    assert len(key_sets) == 1


def test_props_are_scalars_only_and_bounded() -> None:
    fake = FakeSupabase()
    _post(
        _client(fake),
        [
            _event(
                "wi_dwell",
                props={
                    "wi_id": "wi-1",
                    "dwell_ms": 4200,
                    "ok": True,
                    "nested": {"a": 1},
                    "list": [1, 2, 3],
                    "long": "x" * 5000,
                },
            )
        ],
    )
    props = fake.events[0]["props"]
    assert props["wi_id"] == "wi-1"
    assert props["dwell_ms"] == 4200
    assert props["ok"] is True
    assert "nested" not in props and "list" not in props
    assert len(props["long"]) == svc.MAX_PROP_VALUE_CHARS


def test_non_finite_numbers_are_dropped() -> None:
    """`json.dumps` happily emits bare `NaN`, PostgREST rejects it, and the
    whole batch would be lost to one bad number."""
    fake = FakeSupabase()
    _post(_client(fake), [], raw='{"events":[{"event_name":"run_done",'
                                 '"session_key":"' + SESSION + '",'
                                 '"props":{"ms_since_send":NaN,"ok":1}}]}')
    props = fake.events[0]["props"]
    assert "ms_since_send" not in props
    assert props["ok"] == 1


# ===========================================================================
# 8. The UA classifier, directly
# ===========================================================================


@pytest.mark.parametrize(
    "ua,expected",
    [
        (IPHONE_UA, ("mobile", "safari", "ios")),
        (
            "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.0 Safari/604.1",
            ("tablet", "safari", "ios"),
        ),
        (
            "Mozilla/5.0 (Linux; Android 13; SM-S911B) AppleWebKit/537.36 (KHTML, "
            "like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            ("mobile", "chrome", "android"),
        ),
        (
            "Mozilla/5.0 (Linux; Android 13; SM-X710) AppleWebKit/537.36 (KHTML, "
            "like Gecko) Chrome/120.0.0.0 Safari/537.36",
            ("tablet", "chrome", "android"),   # no "Mobile" token => tablet
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, "
            "like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
            ("desktop", "edge", "windows"),
        ),
        (
            "Mozilla/5.0 (Linux; Android 13; SM-S911B) AppleWebKit/537.36 (KHTML, "
            "like Gecko) SamsungBrowser/23.0 Chrome/115.0.0.0 Mobile Safari/537.36",
            ("mobile", "samsung", "android"),
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 "
            "Firefox/121.0",
            ("desktop", "firefox", "macos"),
        ),
        (
            "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:121.0) Gecko/20100101 "
            "Firefox/121.0",
            ("desktop", "firefox", "linux"),
        ),
    ],
)
def test_classify_client_buckets(ua: str, expected: tuple) -> None:
    buckets = svc.classify_client(user_agent=ua)
    assert (buckets.device_type, buckets.browser, buckets.os) == expected


def test_an_unclassifiable_caller_gets_nulls_not_desktop() -> None:
    """Inventing `desktop` for a headerless caller would inflate the exact
    number question 1 exists to answer."""
    buckets = svc.classify_client()
    assert (buckets.device_type, buckets.browser, buckets.os) == (None, None, None)


def test_a_tablet_reporting_ch_ua_mobile_false_stays_a_tablet() -> None:
    buckets = svc.classify_client(
        user_agent="Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) Safari/604.1",
        ch_ua_mobile="?0",
    )
    assert buckets.device_type == "tablet"


# ===========================================================================
# 9. Retention
# ===========================================================================


def test_purge_deletes_only_events_past_the_window() -> None:
    fake = FakeSupabase()
    fake.delete_count = 7
    stats = svc.purge_old_analytics_events(fake)

    assert stats["ok"] is True and stats["deleted"] == 7
    assert len(fake.deletes) == 1
    call = fake.deletes[0]
    assert call["table"] == "analytics_events"
    assert call["kwargs"].get("returning") == "minimal"   # never echo the rows back

    op, column, cutoff = call["filters"][0]
    assert (op, column) == ("lt", "occurred_at")
    expected = datetime.now(timezone.utc) - timedelta(days=svc.RETENTION_DAYS)
    assert abs((datetime.fromisoformat(cutoff) - expected).total_seconds()) < 120


def test_retention_is_180_days() -> None:
    """The plan text says 90 and its own §9 Q2 asks for longer for the chat
    cohorts; 180 is the decision. Pinned so a doc-driven edit cannot quietly
    halve the window that the §6b metrics need."""
    assert svc.RETENTION_DAYS == 180


def test_a_failing_purge_never_raises() -> None:
    fake = FakeSupabase()
    fake.explode = True
    assert svc.purge_old_analytics_events(fake)["ok"] is False
