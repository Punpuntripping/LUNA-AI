"""Access-tiers Phase A — Layer B entitlement + the library meter.

Plan: ``.claude/plans/access_tiers_gating.md`` (§1.2, §1.2.1, PART 2, PART 4.1–4.3)
Decisions: ``.claude/plans/access_tiers_gating_DECISIONS.md`` (D4, D5, D6, D9, D14).

Covers the pieces Phase A shipped:

    backend.app.deps.get_current_user_optional
    shared.quota.library_state / LibraryQuotaState / current_usage_report
    library_service.unlock_cost / resolve_access / stored_library_count
    backend.app.errors.library_refusal_response  (the D14 402 payload)

No live DB. Supabase is a small in-memory PostgREST stand-in (``FakeSupabase``)
that holds real row dicts per table and actually APPLIES the filters, the exact
count, and — critically — the ``ON CONFLICT DO NOTHING`` semantics of
``upsert(ignore_duplicates=True)``. A scripted result queue cannot catch the two
bugs this layer is most exposed to: a double-charge on a concurrent double-click,
and a نظام-covered مادة being charged twice.

The load-bearing assertions here are:
  * ``test_unlock_is_charged_exactly_once`` — money must not be charged twice.
  * ``test_frozen_row_unfreezes_on_re_upgrade`` — the §1.2 predicate.
  * ``test_full_regulation_never_includes_sharh`` — the moat invariant (D6).
"""
from __future__ import annotations

import asyncio
import json
import math
from typing import Any, Optional

import pytest

from backend.app.services import library_service as ls
from shared import quota


# ---------------------------------------------------------------------------
# In-memory PostgREST stand-in
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data: Any, count: Optional[int] = None) -> None:
        self.data = data
        self.count = count


class _Chain:
    """Applies the subset of PostgREST semantics Layer B uses."""

    def __init__(self, fake: "FakeSupabase", table: str) -> None:
        self._fake = fake
        self._table = table
        self._filters: list[tuple[str, str, Any]] = []
        self._orders: list[tuple[str, bool]] = []
        self._range: Optional[tuple[int, int]] = None
        self._limit: Optional[int] = None
        self._count: Optional[str] = None
        self._negate = False

    # --- builders ---------------------------------------------------------
    def select(self, *_cols: Any, count: Optional[str] = None, **_k: Any) -> "_Chain":
        self._count = count
        return self

    def eq(self, col: str, val: Any) -> "_Chain":
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col: str, vals: list[Any]) -> "_Chain":
        self._filters.append(("in", col, list(vals)))
        return self

    def like(self, col: str, pattern: str) -> "_Chain":
        self._filters.append(("ilike", col, pattern))
        return self

    def ilike(self, col: str, pattern: str) -> "_Chain":
        self._filters.append(("ilike", col, pattern))
        return self

    def contains(self, col: str, vals: list[Any]) -> "_Chain":
        """PostgREST ``cs.`` — the array-CONTAINS operator behind every sector
        filter (``regulations_v2.sectors``, ``cases.legal_domains``, …). Postgres
        semantics: the row's array must contain ALL of ``vals``."""
        self._filters.append(("contains", col, list(vals)))
        return self

    @property
    def not_(self) -> "_Chain":
        self._negate = True
        return self

    def is_(self, col: str, val: Any) -> "_Chain":
        self._filters.append(("not_is" if self._negate else "is", col, val))
        self._negate = False
        return self

    def order(self, col: str, *, desc: bool = False, **_k: Any) -> "_Chain":
        self._orders.append((col, desc))
        return self

    def range(self, start: int, end: int) -> "_Chain":
        self._range = (start, end)
        return self

    def limit(self, n: int) -> "_Chain":
        self._limit = n
        return self

    # --- writes -----------------------------------------------------------
    def upsert(
        self,
        json_body: Any,
        *,
        on_conflict: str = "",
        ignore_duplicates: bool = False,
        **_k: Any,
    ) -> "_Chain":
        self._fake.upserts.append((self._table, dict(json_body), on_conflict,
                                   ignore_duplicates))
        self._pending_upsert = (dict(json_body), on_conflict, ignore_duplicates)
        return self

    def insert(self, json_body: Any, **_k: Any) -> "_Chain":
        return self.upsert(json_body)

    # --- execution --------------------------------------------------------
    def _matches(self, row: dict[str, Any]) -> bool:
        for op, col, val in self._filters:
            cell = row.get(col)
            if op == "eq":
                if cell is None or str(cell) != str(val):
                    return False
            elif op == "in":
                if cell is None or str(cell) not in {str(v) for v in val}:
                    return False
            elif op == "ilike":
                needle = str(val).strip("%").lower()
                if needle and needle not in str(cell or "").lower():
                    return False
            elif op == "contains":
                have = {str(v) for v in (cell or [])}
                if not {str(v) for v in val}.issubset(have):
                    return False
            elif op == "is":
                if val == "null" and cell is not None:
                    return False
            elif op == "not_is":
                if val == "null" and cell is None:
                    return False
        return True

    def execute(self) -> _Result:
        pending = getattr(self, "_pending_upsert", None)
        if pending is not None:
            return self._execute_upsert(*pending)

        if self._table in self._fake.fail_tables:
            raise RuntimeError(f"simulated PostgREST failure on {self._table}")

        rows = [dict(r) for r in self._fake.tables.get(self._table, []) if self._matches(r)]
        for col, desc in reversed(self._orders):
            rows.sort(key=lambda r: (r.get(col) is None, str(r.get(col))), reverse=desc)

        # PostgREST reports the FULL matched count in Content-Range even when the
        # response body is limited/ranged — that is what makes
        # `.select(count="exact").limit(1)` a cheap COUNT(*).
        count = len(rows) if self._count == "exact" else None
        if self._range is not None:
            start, end = self._range
            rows = rows[start : end + 1]
        if self._limit is not None:
            rows = rows[: self._limit]
        return _Result(rows, count)

    def _execute_upsert(
        self, body: dict[str, Any], on_conflict: str, ignore_duplicates: bool
    ) -> _Result:
        if self._table in self._fake.fail_tables:
            raise RuntimeError(f"simulated PostgREST failure on {self._table}")
        table = self._fake.tables.setdefault(self._table, [])
        keys = [k.strip() for k in (on_conflict or "").split(",") if k.strip()]
        if keys and ignore_duplicates:
            for existing in table:
                if all(str(existing.get(k)) == str(body.get(k)) for k in keys):
                    # ON CONFLICT DO NOTHING → zero rows returned.
                    return _Result([])
        row = dict(body)
        row.setdefault("unlock_id", f"u{len(table) + 1}")
        table.append(row)
        return _Result([row])


class FakeSupabase:
    """Row-backed fake: seed tables, then the service queries them for real."""

    def __init__(self, *, quota_row: Optional[dict[str, Any]] = None, **tables: Any) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {
            k: list(v) for k, v in tables.items()
        }
        self.tables.setdefault("library_unlocks", [])
        # Empty seo_gate_defaults => resolve_gate falls through to its ultimate
        # fail-closed default ('gated', except 'service'). That is the state we
        # want for every entitlement test.
        self.tables.setdefault("seo_gate_defaults", [])
        self.quota_row = quota_row
        self.upserts: list[tuple[str, dict, str, bool]] = []
        self.rpc_calls: list[tuple[str, dict]] = []
        self.fail_tables: set[str] = set()

    def table(self, name: str) -> _Chain:
        return _Chain(self, name)

    def rpc(self, name: str, params: dict) -> "_RpcChain":
        self.rpc_calls.append((name, dict(params)))
        return _RpcChain(self, name)


class _RpcChain:
    def __init__(self, fake: FakeSupabase, name: str) -> None:
        self._fake = fake
        self._name = name

    def execute(self) -> _Result:
        if self._name != "get_user_quota_state":
            raise AssertionError(f"unexpected RPC {self._name}")
        row = self._fake.quota_row
        return _Result([row] if row else [])


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """The gate-defaults + published-ids caches are module-level TTL caches;
    a test's seeded policy must never leak into the next test."""
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()
    yield
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()


def run(coro):
    """Run one coroutine to completion (no pytest-asyncio dependency)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Quota-state fixtures — the get_user_quota_state row shape (migration 105)
# ---------------------------------------------------------------------------

USER = "aaaaaaaa-0000-0000-0000-000000000001"
REG_ID = "11111111-2222-3333-4444-555555555555"

FREE_PERIOD = "free:202607"
PRO_PERIOD = "pro:20260101:0"
RESETS_AT = "2026-08-01T00:00:00+00:00"


def quota_row(
    *,
    plan: str = "free",
    limit: Optional[int] = 10,
    used: int = 0,
    period_key: Optional[str] = FREE_PERIOD,
    resets_at: Optional[str] = RESETS_AT,
    locked: bool = False,
) -> dict[str, Any]:
    return {
        "locked": locked,
        "plan_id": plan,
        "plan_name_ar": "مجاني",
        "expires_at": None,
        "is_expired": False,
        "effective_plan_id": None if locked else plan,
        "effective_name_ar": None if locked else "مجاني",
        "points_session": 100,
        "points_weekly": 700,
        "points_monthly": None,
        "ocr_pages_monthly": 20,
        "web_calls_monthly": 0,
        "session_cost": 0.0,
        "weekly_cost": 0.0,
        "ocr_pages": 0,
        "session_oldest": None,
        "weekly_oldest": None,
        "ocr_oldest": None,
        "library_unlocks_limit": 0 if locked else limit,
        "library_unlocks_used": used,
        "library_period_key": None if locked else period_key,
        "library_period_resets_at": None if locked else resets_at,
    }


def unlock_row(
    *,
    content_type: str = "regulation",
    content_id: str = REG_ID,
    period_key: str = FREE_PERIOD,
    cost: int = 1,
    user_id: str = USER,
) -> dict[str, Any]:
    return {
        "unlock_id": "seeded-1",
        "user_id": user_id,
        "content_type": content_type,
        "content_id": content_id,
        "period_key": period_key,
        "cost": cost,
        "surface": "library",
        "unlocked_at": "2026-07-01T00:00:00+00:00",
    }


# ===========================================================================
# 1. get_current_user_optional  (PART 4.1)
# ===========================================================================


def test_optional_auth_returns_none_without_credentials() -> None:
    from backend.app.deps import get_current_user_optional

    assert run(get_current_user_optional(None, None)) is None


def test_optional_auth_returns_none_for_an_invalid_token(monkeypatch) -> None:
    """An expired/forged token is 'anonymous', not an error — /library/full is
    reached from PUBLIC pages and a 401 would trip the global login redirect."""
    from backend.app import deps
    from fastapi.security import HTTPAuthorizationCredentials
    from shared.auth.jwt import TokenInvalidError

    def _boom(_token):
        raise TokenInvalidError("bad signature")

    monkeypatch.setattr(deps, "extract_user", _boom)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="junk")
    assert run(deps.get_current_user_optional(None, creds)) is None


def test_optional_auth_returns_none_for_an_expired_token(monkeypatch) -> None:
    from backend.app import deps
    from fastapi.security import HTTPAuthorizationCredentials
    from shared.auth.jwt import TokenExpiredError

    def _boom(_token):
        raise TokenExpiredError("expired")

    monkeypatch.setattr(deps, "extract_user", _boom)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="old")
    assert run(deps.get_current_user_optional(None, creds)) is None


def test_optional_auth_propagates_the_503_auth_outage(monkeypatch) -> None:
    """Auth being genuinely DOWN is not 'anonymous'. Swallowing the 503 would
    silently downgrade every subscriber to the anon tier for the outage."""
    from backend.app import deps
    from backend.app.errors import LunaHTTPException
    from fastapi.security import HTTPAuthorizationCredentials
    from shared.auth.jwt import AuthUnavailableError

    def _boom(_token):
        raise AuthUnavailableError("JWKS unreachable")

    monkeypatch.setattr(deps, "extract_user", _boom)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")
    with pytest.raises(LunaHTTPException) as exc:
        run(deps.get_current_user_optional(None, creds))
    assert exc.value.status_code == 503


def test_optional_auth_returns_the_user_for_a_valid_token(monkeypatch) -> None:
    from backend.app import deps
    from fastapi.security import HTTPAuthorizationCredentials
    from shared.auth.jwt import AuthUser

    user = AuthUser(auth_id="auth-1", email="a@b.com", role="authenticated")
    monkeypatch.setattr(deps, "extract_user", lambda _t: user)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="good")
    assert run(deps.get_current_user_optional(None, creds)) is user


# ===========================================================================
# 2. The library meter (shared/quota) — D9
# ===========================================================================


def test_library_meter_is_registered() -> None:
    from shared.quota import redis_store

    assert "library" in redis_store.METERS
    assert quota._AR_METER["library"] == "فتح المصادر"


def test_library_quota_exceeded_arabic_message() -> None:
    exc = quota.QuotaExceeded("library", "period", 10, 10,
                              quota.datetime.now(quota.timezone.utc))
    payload = exc.to_event_payload()
    assert payload["meter"] == "library"
    assert payload["message_ar"] == quota.LIBRARY_QUOTA_EXHAUSTED_AR
    assert payload["message_ar"] == "تم استهلاك رصيد فتح المصادر لهذه الفترة."


def test_library_meter_not_in_plan_message() -> None:
    """limit 0 keeps the shared 'باقتك لا تشمل …' phrasing (never a scolding)."""
    msg = quota._arabic_message("library", "period", 0)
    assert msg == "باقتك الحالية لا تشمل فتح المصادر."


def test_library_state_reads_the_rpc_row() -> None:
    fake = FakeSupabase(quota_row=quota_row(limit=10, used=3))
    st = run(quota.library_state(fake, USER))
    assert (st.limit, st.used, st.remaining) == (10, 3, 7)
    assert st.period_key == FREE_PERIOD
    assert st.resets_at is not None and st.resets_at.year == 2026
    assert st.is_paid is False
    assert st.locked is False
    assert st.has_room(7) and not st.has_room(8)


def test_library_state_unlimited_plan() -> None:
    fake = FakeSupabase(quota_row=quota_row(plan="dev", limit=None,
                                            period_key="dev:20260626:0"))
    st = run(quota.library_state(fake, USER))
    assert st.limit is None
    assert st.remaining is None
    assert st.has_room(8) is True
    assert st.is_paid is True


def test_library_state_locked_account() -> None:
    fake = FakeSupabase(quota_row=quota_row(locked=True))
    st = run(quota.library_state(fake, USER))
    assert st.locked is True
    assert st.period_key is None
    assert st.has_room(1) is False


def test_library_state_reads_the_rpc_exactly_once() -> None:
    """No second RPC call site, and no Python-side period_key derivation."""
    fake = FakeSupabase(quota_row=quota_row())
    run(quota.library_state(fake, USER))
    assert [c[0] for c in fake.rpc_calls] == ["get_user_quota_state"]


def test_usage_report_carries_the_library_bar() -> None:
    fake = FakeSupabase(quota_row=quota_row(limit=10, used=4))
    report = run(quota.current_usage_report(None, fake, USER))
    bar = report["library"]["period"]
    assert bar == {
        "used": 4,
        "limit": 10,
        "pct": 40,
        "resets_at": "2026-08-01T00:00:00+00:00",
        "approximate": False,
    }


def test_usage_report_library_bar_has_resets_at_at_zero_usage() -> None:
    """Unlike the rolling points/OCR bars, the library window is a FIXED
    calendar/subscription period — its reset date is meaningful at zero usage
    and is what «يتجدّد رصيدك …» renders before anything is spent."""
    fake = FakeSupabase(quota_row=quota_row(limit=10, used=0))
    report = run(quota.current_usage_report(None, fake, USER))
    assert report["library"]["period"]["used"] == 0
    assert report["library"]["period"]["resets_at"] == "2026-08-01T00:00:00+00:00"
    # …while the rolling bars still send null at zero usage (unchanged contract).
    assert report["points"]["session"]["resets_at"] is None
    assert report["ocr"]["monthly"]["resets_at"] is None


def test_usage_report_locked_user_gets_null_library_period() -> None:
    fake = FakeSupabase(quota_row=quota_row(locked=True))
    report = run(quota.current_usage_report(None, fake, USER))
    assert report["locked"] is True
    assert report["library"] == {"period": None}


# ===========================================================================
# 3. Weighted unlock cost — §1.2.1 / D4
# ===========================================================================


def _reg_with_articles(n: int) -> FakeSupabase:
    return FakeSupabase(
        seo_articles=[
            {"regulation_id": REG_ID, "article_no": i, "article_label": f"المادة {i}",
             "slug": f"m-{i}", "chunk_id": None, "article_text": "نص",
             "extraction_status": "extracted"}
            for i in range(1, n + 1)
        ]
    )


def test_cost_of_a_median_regulation_is_one() -> None:
    """The median نظام is 18 مواد — the common case must be unchanged by weighting."""
    assert ls.unlock_cost(_reg_with_articles(18), "regulation", REG_ID) == 1


def test_cost_of_a_200_article_regulation_is_eight() -> None:
    assert ls.unlock_cost(_reg_with_articles(200), "regulation", REG_ID) == 8


def test_cost_of_the_largest_regulation_is_clamped_at_eight() -> None:
    """716 مواد → ceil(716/25) = 29, clamped to the 8 ceiling."""
    assert ls.unlock_cost(_reg_with_articles(716), "regulation", REG_ID) == 8


def test_cost_boundaries_around_the_25_article_step() -> None:
    assert ls.unlock_cost(_reg_with_articles(25), "regulation", REG_ID) == 1
    assert ls.unlock_cost(_reg_with_articles(26), "regulation", REG_ID) == 2
    assert ls.unlock_cost(_reg_with_articles(50), "regulation", REG_ID) == 2


def test_cost_of_leaf_content_types_is_always_one() -> None:
    fake = FakeSupabase()
    for ct in ("article", "judgment", "circular", "form"):
        assert ls.unlock_cost(fake, ct, "x") == 1


def test_cost_of_a_chunk_only_regulation_is_weighted_by_chunk_count() -> None:
    """No seo_articles rows → weight by CHUNK COUNT at 1 chunk ≈ 3 مواد:
    clamp(ceil(chunks / (25/3)), 1, 8).

    RETARGETED 2026-08-07 (`regulation_article_coverage_fallback.md` §4.4). This
    used to assert character weighting — `clamp(ceil(chars/25000),1,8)` — which
    had to page every chunk BODY through the wire just to sum len(). That rule and
    its `CHARS_PER_UNLOCK` constant are DELETED; the assertion moved with them.
    """
    # No `content` on these rows AT ALL: the price no longer depends on body
    # length, and a fixture that still carried text would hide a regression back
    # to the character scan.
    fake = FakeSupabase(
        seo_articles=[],
        chunks_v2=[{"id": f"c{i}", "regulation_id": REG_ID} for i in range(20)],
    )
    assert ls.unlock_cost(fake, "regulation", REG_ID) == 3     # ceil(20 / 8.33…)


def test_cost_of_an_empty_regulation_falls_back_to_one() -> None:
    fake = FakeSupabase(seo_articles=[], chunks_v2=[])
    assert ls.unlock_cost(fake, "regulation", REG_ID) == 1


def test_cost_lookup_failure_fails_to_the_minimum_not_the_maximum() -> None:
    fake = _reg_with_articles(200)
    fake.fail_tables = {"seo_articles", "chunks_v2"}
    assert ls.unlock_cost(fake, "regulation", REG_ID) == 1


# ===========================================================================
# 4. resolve_access — the §1.2 predicate
# ===========================================================================


def test_service_is_never_gated_and_never_charged() -> None:
    """Compliance services are policy-open: no ledger row, ever (§1.3)."""
    fake = FakeSupabase(quota_row=quota_row(limit=0, used=0))
    d = run(ls.resolve_access(fake, USER, "service", "sha1-abc"))
    assert (d.may_unlock, d.charged, d.reason) == (True, False, "open")
    assert fake.tables["library_unlocks"] == []
    assert fake.rpc_calls == []          # not even a quota read


def test_service_is_open_for_anonymous_visitors_too() -> None:
    fake = FakeSupabase(quota_row=quota_row())
    d = run(ls.resolve_access(fake, None, "service", "sha1-abc"))
    assert (d.may_unlock, d.charged, d.reason) == (True, False, "open")


def test_anonymous_user_is_refused_without_touching_the_ledger() -> None:
    fake = FakeSupabase(quota_row=quota_row())
    d = run(ls.resolve_access(fake, None, "regulation", REG_ID))
    assert (d.may_unlock, d.charged, d.reason) == (False, False, "anonymous")
    assert fake.tables["library_unlocks"] == []


def test_open_item_costs_nothing_and_writes_no_row() -> None:
    fake = FakeSupabase(
        quota_row=quota_row(limit=10, used=9),
        seo_item_meta=[{"content_type": "regulation", "content_id": REG_ID,
                        "slug": "s", "seo_tier": "open", "gate_override": None}],
        seo_articles=[],
    )
    d = run(ls.resolve_access(fake, USER, "regulation", REG_ID))
    assert (d.may_unlock, d.charged, d.reason) == (True, False, "open")
    assert fake.tables["library_unlocks"] == []


def test_first_unlock_is_granted_and_charged() -> None:
    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(limit=10, used=0)
    d = run(ls.resolve_access(fake, USER, "regulation", REG_ID))
    assert (d.may_unlock, d.charged, d.reason) == (True, True, "granted")
    assert d.cost == 1
    assert d.used == 1 and d.limit == 10
    rows = fake.tables["library_unlocks"]
    assert len(rows) == 1
    assert rows[0]["period_key"] == FREE_PERIOD
    assert rows[0]["cost"] == 1
    assert rows[0]["surface"] == "library"


def test_unlock_is_charged_exactly_once() -> None:
    """Idempotency: two reveals of the same item produce ONE ledger row and ONE
    charge. This is the property `ON CONFLICT DO NOTHING` exists for."""
    fake = _reg_with_articles(200)
    fake.quota_row = quota_row(limit=100, used=0)

    first = run(ls.resolve_access(fake, USER, "regulation", REG_ID))
    assert (first.charged, first.reason, first.cost) == (True, "granted", 8)

    # The RPC row is a snapshot; reflect the charge as the DB would.
    fake.quota_row = quota_row(limit=100, used=8)
    second = run(ls.resolve_access(fake, USER, "regulation", REG_ID))
    assert (second.may_unlock, second.charged) == (True, False)
    assert second.reason == "already_unlocked"
    assert len(fake.tables["library_unlocks"]) == 1


def test_concurrent_double_click_never_double_charges() -> None:
    """Both calls see 'no row' (same stale snapshot) and both try to insert; the
    UNIQUE constraint makes the second a no-op, and Layer B reports it as
    already_unlocked rather than as a second charge."""
    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(limit=10, used=0)

    async def _both():
        return await asyncio.gather(
            ls.resolve_access(fake, USER, "regulation", REG_ID),
            ls.resolve_access(fake, USER, "regulation", REG_ID),
        )

    a, b = run(_both())
    assert len(fake.tables["library_unlocks"]) == 1
    assert {a.charged, b.charged} == {True, False}
    assert a.may_unlock and b.may_unlock


def test_surface_is_recorded_but_never_changes_the_charge() -> None:
    """`surface` is analytics only — if it altered the charge the reference panel
    would be a bypass again."""
    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(limit=10, used=0)
    d = run(ls.resolve_access(fake, USER, "judgment", "case-1", surface="reference"))
    assert d.charged is True and d.cost == 1
    assert fake.tables["library_unlocks"][0]["surface"] == "reference"


def test_quota_exhaustion_refuses_with_the_reset_date() -> None:
    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(limit=10, used=10)
    d = run(ls.resolve_access(fake, USER, "regulation", REG_ID))
    assert (d.may_unlock, d.charged, d.reason) == (False, False, "quota_exhausted")
    assert (d.used, d.limit) == (10, 10)
    assert d.resets_at is not None
    assert d.resets_at.isoformat() == RESETS_AT
    assert fake.tables["library_unlocks"] == []


def test_a_weighted_charge_that_does_not_fit_is_refused_whole() -> None:
    """A 200-مادة نظام costs 8; with 5 left it is refused rather than part-charged."""
    fake = _reg_with_articles(200)
    fake.quota_row = quota_row(limit=10, used=5)
    d = run(ls.resolve_access(fake, USER, "regulation", REG_ID))
    assert d.reason == "quota_exhausted"
    assert d.cost == 8
    assert fake.tables["library_unlocks"] == []


def test_unlimited_plan_is_never_refused() -> None:
    fake = _reg_with_articles(716)
    fake.quota_row = quota_row(plan="dev", limit=None, used=9_999,
                               period_key="dev:20260626:0")
    d = run(ls.resolve_access(fake, USER, "regulation", REG_ID))
    assert (d.may_unlock, d.charged, d.reason) == (True, True, "granted")
    assert d.limit is None


def test_locked_account_is_refused() -> None:
    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(locked=True)
    d = run(ls.resolve_access(fake, USER, "regulation", REG_ID))
    assert (d.may_unlock, d.charged, d.reason) == (False, False, "locked")
    assert fake.tables["library_unlocks"] == []


# ---- §1.2 freeze / unfreeze -----------------------------------------------


def test_paid_era_row_is_frozen_for_a_now_free_user() -> None:
    """Downgrade freezes the shelf: the row survives, access does not."""
    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(plan="free", limit=10, used=0, period_key=FREE_PERIOD)
    fake.tables["library_unlocks"] = [
        unlock_row(period_key=PRO_PERIOD, cost=1),
        unlock_row(content_type="judgment", content_id="c-1", period_key=PRO_PERIOD),
    ]
    d = run(ls.resolve_access(fake, USER, "regulation", REG_ID))
    assert (d.may_unlock, d.charged, d.reason) == (False, False, "frozen_library")
    assert d.stored_count == 2          # drives «لديك {n} مصدراً محفوظاً …»
    assert len(fake.tables["library_unlocks"]) == 2   # nothing deleted, nothing added


def test_frozen_row_unfreezes_on_re_upgrade() -> None:
    """The §1.2 predicate's first clause: a paid plan reaches EVERY row ever
    unlocked, in any period. Re-upgrading restores the whole shelf at once."""
    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(plan="pro", limit=200, used=0, period_key="pro:20260701:0")
    fake.tables["library_unlocks"] = [unlock_row(period_key=PRO_PERIOD, cost=1)]
    d = run(ls.resolve_access(fake, USER, "regulation", REG_ID))
    assert (d.may_unlock, d.charged, d.reason) == (True, False, "already_unlocked")
    assert len(fake.tables["library_unlocks"]) == 1


def test_free_user_still_reaches_this_periods_own_rows() -> None:
    """'Behaves as a free account' = the current period's unlocks still work."""
    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(plan="free", limit=10, used=1, period_key=FREE_PERIOD)
    fake.tables["library_unlocks"] = [unlock_row(period_key=FREE_PERIOD, cost=1)]
    d = run(ls.resolve_access(fake, USER, "regulation", REG_ID))
    assert (d.may_unlock, d.charged, d.reason) == (True, False, "already_unlocked")


# ---- D5: a نظام covers its مواد, but not the reverse -----------------------


def test_unlocking_a_regulation_covers_its_articles() -> None:
    """Re-charging for a مادة the user just read in the continuous view is
    exactly the 'trick' feeling §5.1 forbids (D5)."""
    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(limit=10, used=1)
    fake.tables["library_unlocks"] = [unlock_row(period_key=FREE_PERIOD, cost=1)]
    d = run(ls.resolve_access(fake, USER, "article", f"{REG_ID}#3"))
    assert (d.may_unlock, d.charged, d.reason) == (True, False, "already_unlocked")
    assert len(fake.tables["library_unlocks"]) == 1


def test_regulation_cover_honours_an_explicit_parent_id() -> None:
    """Callers that already resolved the regulation (the /library/full/article
    route starts from the reg slug) may pass it instead of the key shape."""
    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(limit=10, used=1)
    fake.tables["library_unlocks"] = [unlock_row(period_key=FREE_PERIOD, cost=1)]
    d = run(ls.resolve_access(fake, USER, "article", "opaque-article-key",
                              parent_regulation_id=REG_ID))
    assert d.reason == "already_unlocked" and d.charged is False


def test_a_frozen_regulation_does_not_cover_its_articles_for_free() -> None:
    """The cover only applies when the §1.2 predicate PASSES on the parent row."""
    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(plan="free", limit=10, used=0, period_key=FREE_PERIOD)
    fake.tables["library_unlocks"] = [unlock_row(period_key=PRO_PERIOD, cost=1)]
    d = run(ls.resolve_access(fake, USER, "article", f"{REG_ID}#3"))
    assert d.reason == "granted" and d.charged is True and d.cost == 1


def test_unlocking_an_article_does_not_unlock_the_regulation() -> None:
    """The reverse of D5 does NOT hold — otherwise one 1-point مادة would buy a
    716-مادة statute."""
    fake = _reg_with_articles(200)
    fake.quota_row = quota_row(limit=100, used=1)
    fake.tables["library_unlocks"] = [
        unlock_row(content_type="article", content_id=f"{REG_ID}#3", cost=1)
    ]
    d = run(ls.resolve_access(fake, USER, "regulation", REG_ID))
    assert (d.may_unlock, d.charged, d.reason) == (True, True, "granted")
    assert d.cost == 8
    assert len(fake.tables["library_unlocks"]) == 2


def test_parent_regulation_of_article_parsing() -> None:
    assert ls.parent_regulation_of_article(f"{REG_ID}#12") == REG_ID
    assert ls.parent_regulation_of_article("no-hash") is None
    assert ls.parent_regulation_of_article("x#1", "explicit") == "explicit"


# ---- stored_library_count (Phase B2 consumes this) ------------------------


def test_stored_library_count_counts_rows_not_cost() -> None:
    fake = FakeSupabase(quota_row=quota_row())
    fake.tables["library_unlocks"] = [
        unlock_row(cost=8),
        unlock_row(content_type="judgment", content_id="c-1", cost=1),
        unlock_row(user_id="someone-else", content_id="other", cost=1),
    ]
    assert run(ls.stored_library_count(fake, USER)) == 2


# ===========================================================================
# 5. D14 refusal payload (backend → frontend contract)
# ===========================================================================


def _body(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def test_refusal_is_402_never_401() -> None:
    """/library/full is reached from PUBLIC pages; a 401 would trip the
    frontend's global redirect-to-login and eject a browsing visitor."""
    from backend.app.errors import library_refusal_response

    resp = library_refusal_response(
        ls.AccessDecision(may_unlock=False, charged=False, reason="anonymous")
    )
    body = _body(resp)
    assert resp.status_code == 402
    assert body["error"]["code"] == "LIBRARY_ANONYMOUS"
    assert body["reason"] == "anonymous"
    assert body["error"]["message"] == "سجّل مجاناً لعرض النص كاملاً"


def test_quota_refusal_payload_carries_used_limit_and_reset() -> None:
    from backend.app.errors import library_refusal_response

    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(limit=10, used=10)
    decision = run(ls.resolve_access(fake, USER, "regulation", REG_ID))
    body = _body(library_refusal_response(decision))
    assert body["error"]["code"] == "LIBRARY_QUOTA_EXCEEDED"
    assert body["error"]["status"] == 402
    assert body["reason"] == "quota_exhausted"
    assert (body["used"], body["limit"]) == (10, 10)
    assert body["resets_at"] == RESETS_AT
    assert body["detail"] == quota.LIBRARY_QUOTA_EXHAUSTED_AR
    assert "stored_count" not in body        # present only for frozen_library


def test_frozen_refusal_payload_carries_the_shelf_count() -> None:
    from backend.app.errors import library_refusal_response

    decision = ls.AccessDecision(
        may_unlock=False, charged=False, reason="frozen_library", stored_count=42
    )
    body = _body(library_refusal_response(decision))
    assert body["error"]["code"] == "LIBRARY_FROZEN"
    assert body["stored_count"] == 42
    assert "42" in body["error"]["message"]   # «لديك 42 مصدراً محفوظاً …»


def test_refusal_response_is_never_shared_cached() -> None:
    from backend.app.errors import library_refusal_response

    resp = library_refusal_response(
        ls.AccessDecision(may_unlock=False, charged=False, reason="unresolvable")
    )
    assert resp.headers["cache-control"] == "private, no-store"
    assert _body(resp)["error"]["code"] == "LIBRARY_UNRESOLVABLE"


def test_every_refusal_message_is_arabic() -> None:
    from backend.app.errors import library_refusal_payload

    for reason in ("anonymous", "quota_exhausted", "frozen_library",
                   "unresolvable", "locked"):
        msg = library_refusal_payload(
            ls.AccessDecision(may_unlock=False, charged=False, reason=reason)
        )["error"]["message"]
        assert msg, reason
        assert any("؀" <= ch <= "ۿ" for ch in msg), reason
        assert not any("a" <= ch.lower() <= "z" for ch in msg), reason


def test_library_error_codes_exist() -> None:
    from backend.app.errors import ErrorCode

    for name in ("LIBRARY_QUOTA_EXCEEDED", "LIBRARY_FROZEN",
                 "LIBRARY_ANONYMOUS", "LIBRARY_UNRESOLVABLE"):
        assert getattr(ErrorCode, name).value == name


# ===========================================================================
# 6. THE MOAT INVARIANT — D6 / §1.2
# ===========================================================================

SHARH_TEXT = "شرح ريحان الحصري لهذه المادة — CANARY-SHARH"


def _regulation_with_sharh() -> FakeSupabase:
    return FakeSupabase(
        seo_item_meta=[{"content_type": "regulation", "content_id": REG_ID,
                        "slug": "nizam-test", "seo_tier": None,
                        "gate_override": None}],
        seo_articles=[
            {"regulation_id": REG_ID, "article_no": i, "article_label": f"المادة {i}",
             "slug": f"m-{i}", "chunk_id": f"ch-{i}",
             "article_text": f"نص المادة {i} الكامل",
             "extraction_status": "extracted"}
            for i in (1, 2, 3)
        ],
        seo_sharh=[
            {"regulation_id": REG_ID, "article_no": i, "sharh_md": SHARH_TEXT}
            for i in (1, 2, 3)
        ],
        chunks_v2=[{"id": f"ch-{i}", "regulation_id": REG_ID, "content": "chunk"}
                   for i in (1, 2, 3)],
    )


def _walk_keys(node: Any):
    if isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            yield from _walk_keys(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _walk_keys(v)


def test_full_regulation_never_includes_sharh() -> None:
    """MOAT INVARIANT (D6 / §1.2) — NOT a nicety.

    ``/library/full/regulation/{slug}`` returns every مادة of a نظام for ONE
    unlock. Raw statute is public domain; the شرح is Rayhan's. Bundling شرح into
    the continuous payload would turn 50,923 مادة-level unlocks (~9,600 SAR for
    the AI layer) into 3,373 regulation-level ones — a ~15× collapse of the moat.
    A "continuous reading WITH شرح" feature is therefore forbidden here; شرح stays
    reachable one مادة at a time via /library/full/article.
    """
    payload = ls.get_full_regulation(_regulation_with_sharh(), "nizam-test")
    assert payload is not None

    keys = list(_walk_keys(payload))
    offenders = [k for k in keys if k.lower().startswith("sharh")]
    assert offenders == [], f"شرح key leaked into the continuous payload: {offenders}"

    serialized = json.dumps(payload, ensure_ascii=False)
    assert SHARH_TEXT not in serialized
    assert "CANARY-SHARH" not in serialized
    # …and the statute text IS there (the test would pass vacuously otherwise).
    assert "نص المادة 1 الكامل" in serialized
    assert len(payload["sections"]) == 3


def test_full_article_still_carries_the_sharh() -> None:
    """The one-مادة-at-a-time path is where شرح legitimately unlocks — this is the
    counterpart that proves the invariant above is a placement rule, not a
    deletion. ``include_sharh`` is the entitled caller's opt-in."""
    payload = ls.get_full_article(
        _regulation_with_sharh(), "nizam-test", "m-2", include_sharh=True
    )
    assert payload is not None
    assert payload["sharh_md"] == SHARH_TEXT


def test_full_article_withholds_the_sharh_by_default() -> None:
    """``include_sharh`` DEFAULTS TO FALSE — the fail-closed direction (H-5).

    The شرح is §1.3 ALWAYS-GATED, so a caller that has not thought about
    entitlement must get the نص and nothing more. A default of True would make
    every future caller a leak by omission, which is how the corpus escaped the
    first time.
    """
    payload = ls.get_full_article(_regulation_with_sharh(), "nizam-test", "m-2")
    assert payload is not None
    assert payload["sharh_md"] is None
    # ...and the نص is still whole: this withholds the شرح, it does not truncate.
    assert payload["text"] == "نص المادة 2 الكامل"


def test_an_unentitled_reader_never_even_queries_the_sharh_table() -> None:
    """Not merely "not in the payload" — not READ. The gated bytes must not enter
    the process at all, which is also what makes the withholding un-leakable by a
    later serialization mistake."""
    fake = _regulation_with_sharh()
    fake.fail_tables = {"seo_sharh"}  # any read here blows up

    payload = ls.get_full_article(fake, "nizam-test", "m-2")

    assert payload is not None
    assert payload["sharh_md"] is None


# ---------------------------------------------------------------------------
# 6b. THE ALWAYS-GATED ENTITLEMENT — H-5 (security review 2026-08-07)
# ---------------------------------------------------------------------------
#
# The شرح is gated BY POLICY (§1.3 "always gated"), independent of the مادة's
# tier. ``resolve_access`` used to return the moment the Layer-A gate read
# 'open' — BEFORE the quota was ever read — and step (b) of ``resolve_gate``
# makes a مادة inherit its parent نظام's tier. Since all 229 of 229 مواد that
# carry a شرح sit under an OPEN نظام, that early return handed 100% of the شرح
# corpus to any registered account, free, unmetered, with no ledger row.
#
# The unit-level half of the fix is ``always_gated=``; the HTTP-level half (the
# route asking ``article_has_sharh`` and passing ``is_entitled`` to the reader)
# is pinned in test_library_enforcement.py §1b.


def _open_tier_article_corpus(*, used: int = 0, limit: Optional[int] = 10) -> FakeSupabase:
    """An OPEN-tier نظام, one published مادة under it, with a cached شرح."""
    fake = _regulation_with_sharh()
    fake.tables["seo_item_meta"] = [
        {"content_type": "regulation", "content_id": REG_ID, "slug": "nizam-test",
         "seo_tier": "open", "gate_override": None},
        {"content_type": "article", "content_id": f"{REG_ID}#2", "slug": "m-2",
         "seo_tier": None, "gate_override": None},
    ]
    fake.quota_row = quota_row(limit=limit, used=used)
    return fake


def test_an_open_tier_article_is_still_free_when_it_has_no_sharh() -> None:
    """The property the fix must NOT regress: an open-tier نص is free, uncharged,
    and writes no ledger row. ``always_gated`` is per-ITEM, so the ~50k مواد with
    no شرح keep exactly the access they have today."""
    fake = _open_tier_article_corpus()
    decision = run(
        ls.resolve_access(
            fake, USER, "article", f"{REG_ID}#2",
            parent_regulation_id=REG_ID, always_gated=False,
        )
    )
    assert decision.may_unlock is True
    assert decision.charged is False
    assert decision.reason == "open"
    assert decision.is_entitled is False, "an 'open' verdict buys nothing"
    assert fake.tables["library_unlocks"] == []


def test_an_open_tier_article_with_a_sharh_is_metered() -> None:
    """H-5: 'open' must NOT short-circuit the meter when always-gated bytes ride
    along. The نص is free by tier; the شرح on top of it is bought."""
    fake = _open_tier_article_corpus()
    decision = run(
        ls.resolve_access(
            fake, USER, "article", f"{REG_ID}#2",
            parent_regulation_id=REG_ID, always_gated=True,
        )
    )
    assert decision.reason == "granted"
    assert decision.charged is True
    assert decision.is_entitled is True
    # The ledger row is the point — it is the sole revenue control.
    assert len(fake.tables["library_unlocks"]) == 1
    row = fake.tables["library_unlocks"][0]
    assert row["content_type"] == "article"
    assert row["content_id"] == f"{REG_ID}#2"


def test_an_exhausted_account_is_refused_the_sharh_of_an_open_article() -> None:
    """quota_exhausted is one of the three verdicts the 'open' return skipped."""
    fake = _open_tier_article_corpus(used=10, limit=10)
    decision = run(
        ls.resolve_access(
            fake, USER, "article", f"{REG_ID}#2",
            parent_regulation_id=REG_ID, always_gated=True,
        )
    )
    assert decision.may_unlock is False
    assert decision.reason == "quota_exhausted"
    assert decision.is_entitled is False
    assert fake.tables["library_unlocks"] == []


def test_a_locked_account_is_refused_the_sharh_of_an_open_article() -> None:
    """...and so is `locked` — a plan-less account reaches nothing gated."""
    fake = _open_tier_article_corpus()
    fake.quota_row = quota_row(locked=True)
    decision = run(
        ls.resolve_access(
            fake, USER, "article", f"{REG_ID}#2",
            parent_regulation_id=REG_ID, always_gated=True,
        )
    )
    assert decision.may_unlock is False
    assert decision.reason == "locked"
    assert fake.tables["library_unlocks"] == []


def test_a_frozen_row_still_freezes_the_sharh_of_an_open_article() -> None:
    """...and so is `frozen_library`: a paid-era مادة read by a now-free account
    is frozen even though its نظام is open-tier."""
    fake = _open_tier_article_corpus()
    fake.tables["library_unlocks"] = [
        unlock_row(content_type="article", content_id=f"{REG_ID}#2",
                   period_key=PRO_PERIOD)
    ]
    decision = run(
        ls.resolve_access(
            fake, USER, "article", f"{REG_ID}#2",
            parent_regulation_id=REG_ID, always_gated=True,
        )
    )
    assert decision.may_unlock is False
    assert decision.reason == "frozen_library"


def test_an_already_unlocked_open_article_is_not_charged_twice_for_its_sharh() -> None:
    """always_gated does not re-charge: the ledger row IS the شرح purchase."""
    fake = _open_tier_article_corpus(used=1)
    fake.tables["library_unlocks"] = [
        unlock_row(content_type="article", content_id=f"{REG_ID}#2",
                   period_key=FREE_PERIOD)
    ]
    decision = run(
        ls.resolve_access(
            fake, USER, "article", f"{REG_ID}#2",
            parent_regulation_id=REG_ID, always_gated=True,
        )
    )
    assert decision.reason == "already_unlocked"
    assert decision.charged is False
    assert decision.is_entitled is True
    assert len(fake.tables["library_unlocks"]) == 1


def test_an_unlocked_open_tier_regulation_still_covers_its_articles_sharh() -> None:
    """D5 survives always_gated — the نظام covers its مواد. Re-charging for a مادة
    the user just read in the continuous view is the "trick" feeling §5.1
    forbids, and that does not stop being true because a شرح is attached."""
    fake = _open_tier_article_corpus(used=1)
    fake.tables["library_unlocks"] = [unlock_row(period_key=FREE_PERIOD)]  # the نظام
    decision = run(
        ls.resolve_access(
            fake, USER, "article", f"{REG_ID}#2",
            parent_regulation_id=REG_ID, always_gated=True,
        )
    )
    assert decision.reason == "already_unlocked"
    assert decision.charged is False
    assert decision.is_entitled is True


def test_anonymous_is_still_refused_before_the_always_gated_branch() -> None:
    """Step 2 precedes step 3, so ``always_gated`` cannot change the anon verdict
    (and must never turn it into a quota read for a user that does not exist)."""
    fake = _open_tier_article_corpus()
    decision = run(
        ls.resolve_access(fake, None, "article", f"{REG_ID}#2", always_gated=True)
    )
    assert decision.may_unlock is False
    assert decision.reason == "anonymous"


def test_a_never_charged_type_ignores_always_gated() -> None:
    """Step 1 precedes step 3 too: a compliance service is policy-open and must
    stay free even if a caller mistakenly flags it always-gated."""
    fake = _open_tier_article_corpus()
    decision = run(ls.resolve_access(fake, USER, "service", "svc-1", always_gated=True))
    assert decision.may_unlock is True
    assert decision.charged is False
    assert decision.reason == "open"


# --- article_has_sharh — the per-item probe that keeps the meter honest ----


def test_article_has_sharh_detects_a_cached_sharh() -> None:
    assert ls.article_has_sharh(_regulation_with_sharh(), f"{REG_ID}#2") is True


def test_article_has_sharh_is_false_without_a_row() -> None:
    """~50k of ~50k-odd مواد have no شرح. Flagging them always-gated would charge
    an unlock for bytes the reader already had in full — §5.1's "trick" feeling.
    """
    assert ls.article_has_sharh(_regulation_with_sharh(), f"{REG_ID}#9") is False


def test_article_has_sharh_treats_an_empty_body_as_absent() -> None:
    """Parity with ``_sharh_teaser``: a blank شرح renders no teaser, so it must
    not trigger a charge either."""
    fake = _regulation_with_sharh()
    fake.tables["seo_sharh"] = [
        {"regulation_id": REG_ID, "article_no": 2, "sharh_md": "   "}
    ]
    assert ls.article_has_sharh(fake, f"{REG_ID}#2") is False


@pytest.mark.parametrize("key", ["", "not-a-key", f"{REG_ID}#", f"{REG_ID}#abc", "#3"])
def test_article_has_sharh_rejects_a_malformed_key(key: str) -> None:
    assert ls.article_has_sharh(_regulation_with_sharh(), key) is False


def test_article_has_sharh_fails_soft_to_false() -> None:
    """A lookup blip must not 500 the reveal. False is safe in both directions
    ONLY because the sink is independent: the نص stays free (correct) and
    ``get_full_article`` still withholds the شرح without ``is_entitled``."""
    fake = _regulation_with_sharh()
    fake.fail_tables = {"seo_sharh"}
    assert ls.article_has_sharh(fake, f"{REG_ID}#2") is False


# ===========================================================================
# Ledger write FAILURE — the meter failing open must be visible
# ===========================================================================
#
# Granting access when the ledger write errors is deliberate policy: the user
# clicked once and the content is theirs, and the reverse would let a transient
# DB blip paywall a paying customer. But `library_unlocks` is the SOLE revenue
# control for this design, so a write failure must NOT be reported as an ordinary
# re-read. If it were, a permission drift / bad period_key / pool exhaustion would
# make EVERY reveal for EVERY user free indefinitely, while the response, the
# balance chip and every ledger dashboard all agreed nothing was wrong.
# (Security review 2026-07-27, MEDIUM-4.)


def test_a_ledger_write_failure_grants_access_but_is_reported_distinctly() -> None:
    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(limit=10, used=0)
    fake.fail_tables = {"library_unlocks"}

    decision = run(ls.resolve_access(fake, USER, "regulation", REG_ID))

    # Access is granted — a DB blip must not paywall a paying customer.
    assert decision.may_unlock is True
    assert decision.charged is False
    # ...but it must NOT masquerade as a normal already-unlocked re-read.
    assert decision.reason == "ledger_unavailable", (
        "a ledger write failure is being reported as ordinary re-read traffic — "
        "a silently unmetered library would be invisible in telemetry"
    )
    assert fake.tables["library_unlocks"] == []


def test_ledger_failure_is_logged_at_ERROR_so_it_can_be_alerted_on(caplog) -> None:
    """WARNING is not enough for total-bypass mode. The stable marker
    `event=library_ledger_write_failed` is what an alert keys on."""
    import logging

    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(limit=10, used=0)
    fake.fail_tables = {"library_unlocks"}

    with caplog.at_level(logging.ERROR):
        run(ls.resolve_access(fake, USER, "regulation", REG_ID))

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "ledger write failure did not log at ERROR"
    assert any("library_ledger_write_failed" in r.getMessage() for r in errors)


def test_an_exhausted_account_is_refused_without_costing_a_corpus_scan() -> None:
    """`unlock_cost` pages through every chunk body of a chunk-only نظام just to
    measure len(). Running it before the quota check let an ALREADY-exhausted
    account force one full scan per request, at the whole route budget, forever,
    for zero cost to itself. The cheap refusal must come first.
    (Security review 2026-07-27, MEDIUM-2.)"""
    fake = _reg_with_articles(18)
    fake.quota_row = quota_row(limit=10, used=10)  # nothing left
    # If the cost computation runs at all, these reads blow up.
    fake.fail_tables = {"seo_articles", "chunks_v2"}

    decision = run(ls.resolve_access(fake, USER, "regulation", REG_ID))

    assert decision.may_unlock is False
    assert decision.reason == "quota_exhausted"
    assert decision.resets_at is not None


# ===========================================================================
# 7. Article-coverage fallback — `regulation_article_coverage_fallback.md` §3/§6
# ===========================================================================
#
# A `seo_articles` index is keyed by `article_no`, so a document whose highest
# article_no far exceeds its row count has HOLES: مواد that exist in the نظام and
# have no row. Committing the reading surface to that index on the mere EXISTENCE
# of one row renders the holes as nothing at all — no gap marker, no count
# mismatch, no signal of any kind. Past both thresholds the chunks are the more
# honest surface even though they are coarser.
#
# ⚠ EVERY assertion below counts gaps from the NUMBERING. Not `extraction_status`,
# not `article_text`. On 17900_reg_128_p2 — the document this rule was written for
# — all 68 present rows are `extracted` with non-null text, so a content-based
# completeness test scores that page 100/100 and leaves 164 مواد silently dropped.
# A test written against row CONTENT here would be vacuous.


def _article_rows(numbers: list[int]) -> list[dict[str, Any]]:
    """`seo_articles` rows for the given article numbers (same shape as
    `_reg_with_articles`, but with the numbering under the caller's control)."""
    return [
        {"regulation_id": REG_ID, "article_no": n, "article_label": f"المادة {n}",
         "slug": f"m-{n}", "chunk_id": None, "article_text": f"نص المادة {n}",
         "extraction_status": "extracted"}
        for n in numbers
    ]


def _holed(max_no: int, rows: int) -> list[dict[str, Any]]:
    """`rows` مادة rows whose highest `article_no` is `max_no`.

    Gap count is `max_no - rows` by construction, which is exactly the quantity
    the rule measures. Numbering is 1..rows-1 plus `max_no`, so the hole is one
    contiguous run — the real documents have several, but the rule cannot tell
    the difference and must not start trying to.
    """
    assert 1 <= rows <= max_no
    return _article_rows(list(range(1, rows)) + [max_no])


def _chunks(n: int) -> list[dict[str, Any]]:
    return [
        {"id": f"cccccccc-0000-0000-0000-{i:012d}", "regulation_id": REG_ID,
         "title": f"الفصل {i}", "position": i, "content": f"نص المقطع {i}"}
        for i in range(1, n + 1)
    ]


class _CountingSupabase(FakeSupabase):
    """FakeSupabase that also records which tables were READ.

    The base fake logs writes and RPCs but not selects, and "this code path did
    not need a second round trip" is a claim only a select log can settle.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tables_queried: list[str] = []

    def table(self, name: str) -> _Chain:
        self.tables_queried.append(name)
        return super().table(name)


# ---- 7.1 the pure helper --------------------------------------------------
#
# No DB — `article_coverage_is_trustworthy` reads `article_no` off the rows it is
# handed and nothing else.


def test_the_labour_regulation_part_2_index_is_distrusted() -> None:
    """THE trigger case — 17900_reg_128_p2, اللائحة التنفيذية لنظام العمل ج2.

    68 rows for a 232-مادة لائحة: 164 missing (70.7%). The page advertised
    «68 مادة» and silently omitted the other 164. This is the document the whole
    rule exists for, and every present row of it is perfectly healthy.
    """
    assert ls.article_coverage_is_trustworthy(_holed(232, 68)) is False


def test_a_contiguous_index_is_trusted() -> None:
    """1..100 with no holes — the shape 315 of 330 published regulations have."""
    assert ls.article_coverage_is_trustworthy(_holed(100, 100)) is True


def test_a_small_gap_ratio_is_trusted() -> None:
    """5 missing out of 100 = 5% — over the absolute floor, under the ratio."""
    assert ls.article_coverage_is_trustworthy(_holed(100, 95)) is True


def test_the_absolute_floor_protects_short_documents() -> None:
    """20 max / 17 rows: 3 missing is 15% — over the RATIO but not over the
    floor, and both must hold. Without the floor a 10-مادة قواعد missing 2 would
    flip on nothing but arithmetic noise."""
    assert ls.article_coverage_is_trustworthy(_holed(20, 17)) is True


def test_a_large_gap_ratio_is_distrusted() -> None:
    """40 max / 35 rows: 5 missing = 12.5% — both thresholds cleared."""
    assert ls.article_coverage_is_trustworthy(_holed(40, 35)) is False


def test_exactly_ten_percent_missing_is_still_trusted() -> None:
    """The ratio test is STRICT `>`, so the threshold value itself passes.

    50 max / 45 rows = 5 missing = exactly 10.0%, and 5 > 3 clears the floor —
    so the ratio is the only thing standing between this document and a flip,
    and it holds. One row fewer (12%) and it goes.
    """
    assert ls.article_coverage_is_trustworthy(_holed(50, 45)) is True
    assert ls.article_coverage_is_trustworthy(_holed(50, 44)) is False


def test_an_empty_index_is_not_trustworthy() -> None:
    """Vacuously false. Callers branch on falsiness first, so this is a
    contract-stability assertion, not a live path."""
    assert ls.article_coverage_is_trustworthy([]) is False


def test_rows_without_an_article_no_are_ignored_not_counted_as_zero() -> None:
    """An unnumbered row is not a مادة — it must not pad the row count.

    Counting it as `article_no = 0` would make junk rows PAPER OVER real holes:
    the 40/35 document below is distrusted, and 10 unnumbered rows would push its
    apparent count to 45 (missing = −5) and quietly restore the broken index.
    """
    junk = [
        {"regulation_id": REG_ID, "article_no": None, "article_label": None},
        {"regulation_id": REG_ID, "article_no": 0, "article_label": ""},
        {"regulation_id": REG_ID},                       # column absent entirely
    ] * 4                                                # 12 junk rows
    assert ls.article_coverage_is_trustworthy(_holed(40, 35) + junk) is False
    # …and a list of nothing BUT junk is distrusted, not a ZeroDivisionError on
    # max(numbers) == 0.
    assert ls.article_coverage_is_trustworthy(junk) is False


# ---- 7.2 use_article_surface — the decision point -------------------------


def test_a_holed_index_falls_back_to_chunks_when_chunks_exist() -> None:
    fake = FakeSupabase(chunks_v2=_chunks(60))
    assert ls.use_article_surface(fake, REG_ID, _holed(232, 68)) is False


def test_a_holed_index_is_kept_when_the_regulation_has_no_chunks() -> None:
    """HARD GUARD: a partial document beats a blank one.

    Falling back to a chunk surface that does not exist would render the نظام as
    nothing — strictly worse than the 68 مواد it does have. No published
    regulation hits this today (all 15 flip candidates carry 4–60 chunks); the
    guard is for the corpus we don't have yet.
    """
    fake = FakeSupabase(chunks_v2=[])
    assert ls.use_article_surface(fake, REG_ID, _holed(232, 68)) is True


def test_a_healthy_index_needs_no_chunk_count_query() -> None:
    """The trusted path is the overwhelmingly common one (315 of 330) and must
    not pay a round trip to prove it."""
    fake = _CountingSupabase(chunks_v2=_chunks(10))
    assert ls.use_article_surface(fake, REG_ID, _holed(100, 100)) is True
    assert "chunks_v2" not in fake.tables_queried


def test_an_empty_index_never_reaches_the_database() -> None:
    fake = _CountingSupabase(chunks_v2=_chunks(10))
    assert ls.use_article_surface(fake, REG_ID, []) is False
    assert fake.tables_queried == []


# ---- 7.3 unlock pricing ---------------------------------------------------


def test_a_trusted_index_is_priced_by_article_count() -> None:
    """68 contiguous مواد → ceil(68/25) = 3. Unchanged by this rule."""
    fake = FakeSupabase(seo_articles=_holed(68, 68), chunks_v2=_chunks(60))
    assert ls.unlock_cost(fake, "regulation", REG_ID) == 3


def test_a_flipped_regulation_is_priced_by_chunk_count() -> None:
    """اللائحة التنفيذية لنظام العمل ج2 again: the SAME 68 rows, but the index is
    distrusted, so it prices as the 60-chunk document it now renders as —
    ceil(60 / (25/3)) = 8 — and lands on `UNLOCK_COST_MAX`.

    3 → 8 is accepted, not an oversight (decision 2026-08-06): 4 of 187
    chunk-priced regulations sit at the cap either way, the same 4 as before. If
    the cap starts binding on documents it shouldn't, the lever is
    `CHUNKS_PER_UNLOCK`, not `UNLOCK_COST_MAX` and not the coverage threshold.
    """
    fake = FakeSupabase(seo_articles=_holed(232, 68), chunks_v2=_chunks(60))
    assert ls.unlock_cost(fake, "regulation", REG_ID) == ls.UNLOCK_COST_MAX == 8


def test_the_price_of_a_flipped_regulation_matches_its_rendered_surface() -> None:
    """The charge and the reading surface must be decided by the SAME predicate.

    Pricing a document as 68 مواد while rendering it as 60 chunks would be a
    quiet mismatch between what the user pays for and what they get.
    """
    articles, chunks = _holed(232, 68), _chunks(60)
    fake = FakeSupabase(seo_articles=articles, chunks_v2=chunks)
    assert ls.use_article_surface(fake, REG_ID, articles) is False
    assert ls.unlock_cost(fake, "regulation", REG_ID) != 3     # not the مواد price


def test_a_regulation_with_no_articles_and_no_chunks_costs_the_minimum() -> None:
    fake = FakeSupabase(seo_articles=[], chunks_v2=[])
    assert ls.unlock_cost(fake, "regulation", REG_ID) == ls.UNLOCK_COST_MIN


def test_a_non_regulation_content_type_costs_the_minimum() -> None:
    """Leaf types never reach either weighting branch — no article index, no
    chunk count, no round trip."""
    fake = _CountingSupabase(seo_articles=_holed(232, 68), chunks_v2=_chunks(60))
    for ct in ("article", "judgment", "circular", "form", "service"):
        assert ls.unlock_cost(fake, ct, "x") == ls.UNLOCK_COST_MIN, ct
    assert fake.tables_queried == []


# ---- 7.4 REGRESSION GUARD: the surface the reader actually gets ------------


def _reg_doc_fake(articles: list[dict[str, Any]], n_chunks: int) -> FakeSupabase:
    """A regulation complete enough for `get_regulation_doc` to render it.

    `seo_tier='open'` so the payload carries EVERY section rather than the
    3-section gated preview — the point of these two tests is which surface the
    whole document renders from, and a 3-row preview would prove it for 3 rows.
    """
    return FakeSupabase(
        seo_item_meta=[{"content_type": "regulation", "content_id": REG_ID,
                        "slug": "nizam-test", "seo_tier": "open",
                        "gate_override": None}],
        regulations_v2=[{"id": REG_ID, "reg_ref": "17900_reg_128_p2",
                         "clean_title": "اللائحة التنفيذية لنظام العمل وملحقاتها الجزء 2",
                         "title": None, "entity_name": "وزارة الموارد البشرية",
                         "doc_type_bucket": "executive_regulation",
                         "status_class": "in_force", "legal_authority": None,
                         "start_date": None, "sectors": [], "summary": "ملخص",
                         "llm_summary": None, "landing_url": None, "pdf_url": None}],
        seo_articles=articles,
        chunks_v2=_chunks(n_chunks),
    )


def test_a_healthy_regulation_still_renders_article_sections() -> None:
    """Regression guard for the 315 regulations this rule must NOT touch.

    `art-*` ids are the article surface's signature — the frontend detects it
    with exactly that test (`app/regulations/[slug]/page.tsx:123`
    `s.id.startsWith("art-")`), so an id prefix regression here silently changes
    how every untouched نظام renders.
    """
    # Single-digit numbering on purpose: the service does not sort: it relies on
    # PostgREST's `.order("article_no")`, and FakeSupabase orders by str(), where
    # "10" sorts before "2". Keeping every article_no to one digit makes the
    # fake's ordering faithful instead of asserting around it.
    doc = ls.get_regulation_doc(_reg_doc_fake(_holed(9, 9), n_chunks=6),
                                "nizam-test")
    assert doc is not None
    ids = [s["id"] for s in doc["visible_sections"]]
    assert ids == [f"art-{n}" for n in range(1, 10)]
    assert all(i.startswith("art-") for i in ids), ids
    # …and the TOC is the مادة slug list, not the chunk list.
    assert [t["id"] for t in doc["toc"]] == [f"m-{n}" for n in range(1, 10)]


def test_a_flipped_regulation_renders_chunk_sections() -> None:
    """The other half of the guard: the SAME fixture with a holed index renders
    chunk uuids — never `art-*` — for both the sections and the TOC."""
    fake = _reg_doc_fake(_holed(232, 68), n_chunks=6)
    doc = ls.get_regulation_doc(fake, "nizam-test")
    assert doc is not None

    chunk_ids = {c["id"] for c in fake.tables["chunks_v2"]}
    ids = [s["id"] for s in doc["visible_sections"]]
    assert set(ids) == chunk_ids
    assert not any(i.startswith("art-") for i in ids), ids
    assert {t["id"] for t in doc["toc"]} == chunk_ids
    # The document is open end-to-end, so nothing is withheld from either surface.
    assert doc["hidden_section_count"] == 0


# ---- 7.5 chunk STREAM ORDER — the fallback must land a readable document ----
#
# The fallback is only an improvement if the chunks it renders are in document
# order, and for 1,184 regulations they are not under a naive sort. `position` is
# scoped PER STREAM: a regulation's appendix chunks (`corpus='appendix'`,
# chunk_ref `_apx_NNN`) restart at 1 alongside the body chunks
# (`without_articles` / `with_articles`, `_chunk_NNN`). Ordering by `position`
# alone therefore INTERLEAVES them — on 17900_reg_128_p2 «ملحق رقم (1)» lands
# between المادة السادسة and المادة الثانية عشرة — and with no tiebreaker the
# pairing order is not stable between requests, which an ISR page then bakes in.
#
# 86 published regulations carry both streams: 46 chunk-only ones render this way
# on live pages TODAY, and 3 more would have joined them the moment the coverage
# rule flipped them — including the labour لائحة that motivated the rule.


def _two_stream_chunks() -> list[dict[str, Any]]:
    """A regulation whose body and appendix streams BOTH number 1..3.

    Single-digit positions on purpose: the fake sorts with `str()`, so "10" would
    precede "2" and the assertion would be testing the fake, not the service.
    """
    body = [
        {"id": f"bbbbbbbb-0000-0000-0000-{i:012d}", "regulation_id": REG_ID,
         "title": f"المادة {i}", "position": i, "content": f"نص المادة {i}",
         "corpus": "without_articles", "chunk_ref": f"17900_reg_128_p2_chunk_{i:03d}"}
        for i in (1, 2, 3)
    ]
    apx = [
        {"id": f"aaaaaaaa-0000-0000-0000-{i:012d}", "regulation_id": REG_ID,
         "title": f"ملحق رقم ({i})", "position": i, "content": f"نص الملحق {i}",
         "corpus": "appendix", "chunk_ref": f"17900_reg_128_p2_apx_{i:03d}"}
        for i in (1, 2, 3)
    ]
    return apx + body          # inserted appendix-first, so a no-op sort fails


def test_chunk_stream_order_puts_body_before_appendix() -> None:
    """The alphabetical dependency `_ordered_chunk_query` leans on, pinned.

    `corpus DESC` is what puts the body first, and that works only because every
    body-stream name sorts AFTER 'appendix'. A new corpus value that doesn't —
    'annex' is the obvious trap — would silently reorder every document with an
    appendix, with no error anywhere. This test is the tripwire.
    """
    for body in ls._CHUNK_BODY_CORPORA:
        assert body > ls._CHUNK_APPENDIX_CORPUS, (
            f"corpus '{body}' does not sort after "
            f"'{ls._CHUNK_APPENDIX_CORPUS}' — `.order('corpus', desc=True)` in "
            f"_ordered_chunk_query would put appendices in the middle of the body"
        )


def test_a_flipped_regulation_renders_its_body_before_its_appendices() -> None:
    fake = _reg_doc_fake(_holed(232, 68), n_chunks=0)
    fake.tables["chunks_v2"] = _two_stream_chunks()

    doc = ls.get_regulation_doc(fake, "nizam-test")
    assert doc is not None

    titles = [s["title"] for s in doc["visible_sections"]]
    assert titles == ["المادة 1", "المادة 2", "المادة 3",
                      "ملحق رقم (1)", "ملحق رقم (2)", "ملحق رقم (3)"], titles
    # The TOC is built from a second query — it must agree, or the rail links to
    # a different reading order than the page renders.
    assert [t["title"] for t in doc["toc"]] == titles


def test_a_gated_preview_shows_the_first_three_body_chunks_not_an_interleave() -> None:
    """The 3-chunk preview is the whole first impression of a gated نظام.

    Under `position`-only ordering the fake returns appendix-first at every
    position, so the preview would have opened with «ملحق رقم (1)» — an appendix
    presented as the opening of the نظام.
    """
    fake = _reg_doc_fake(_holed(232, 68), n_chunks=0)
    fake.tables["seo_item_meta"][0]["seo_tier"] = "gated"
    fake.tables["chunks_v2"] = _two_stream_chunks()

    doc = ls.get_regulation_doc(fake, "nizam-test")
    assert doc is not None
    assert [s["title"] for s in doc["visible_sections"]] == [
        "المادة 1", "المادة 2", "المادة 3",
    ]
    assert doc["hidden_section_count"] == 3


# ---- 7.6 الملاحق ON THE ARTICLE SURFACE ------------------------------------
#
# `seo_articles` is built from مواد and carries NOTHING from the appendix stream
# (measured 2026-08-24: 49,724 rows from `with_articles`, 1,200 from
# `without_articles`, 0 from `appendix`). So an article-rendered نظام used to
# ship its مواد and drop its ملاحق — on «اللائحة الفنية الخليجية للعب الأطفال»
# that was 89 of 109 sections, and for a لائحة فنية the annexes ARE the
# operative content. 188 published أنظمة were in that state.
#
# 7.5 above guards the CHUNK path's appendix ORDERING. This guards the ARTICLE
# path's appendix EXISTENCE — a different failure with the same cause.


def _appendix_chunks(n: int, *, content: Optional[str] = None) -> list[dict[str, Any]]:
    """`n` appendix-stream chunk rows.

    Single-digit `n` on purpose: the fake sorts by `str()`, where "10" sorts
    before "2". Keeping the run to one digit makes the fake's ordering faithful
    instead of asserting around it.
    """
    assert 1 <= n <= 9
    return [
        {"id": f"dddddddd-0000-0000-0000-{i:012d}", "regulation_id": REG_ID,
         "title": f"ملحق ({i})", "position": i,
         "content": content if content is not None else f"نص الملحق {i}",
         "corpus": "appendix", "chunk_ref": f"17393_reg_029_apx_{i:03d}"}
        for i in range(1, n + 1)
    ]


def _reg_with_appendix(
    articles: list[dict[str, Any]],
    n_apx: int,
    *,
    tier: Optional[str] = "open",
    apx_content: Optional[str] = None,
) -> FakeSupabase:
    fake = _reg_doc_fake(articles, n_chunks=0)
    fake.tables["seo_item_meta"][0]["seo_tier"] = tier
    fake.tables["chunks_v2"] = _appendix_chunks(n_apx, content=apx_content)
    return fake


def test_the_appendix_lands_after_the_last_article() -> None:
    """The whole point: ملاحق exist on the article surface, and they come last."""
    doc = ls.get_regulation_doc(_reg_with_appendix(_article_rows([1, 2, 3]), 3),
                                "nizam-test")
    assert doc is not None
    assert [s["id"] for s in doc["visible_sections"]] == [
        "art-1", "art-2", "art-3", "apx-1", "apx-2", "apx-3",
    ]
    assert [s["title"] for s in doc["visible_sections"]][3:] == [
        "ملحق (1)", "ملحق (2)", "ملحق (3)",
    ]
    # `apx-` must not collide with the frontend's article-surface probe
    # (`s.id.startsWith("art-")`, app/regulations/[slug]/page.tsx).
    assert not any(s["id"].startswith("art-")
                   for s in doc["visible_sections"][3:])


def test_appendix_toc_positions_continue_past_the_last_article() -> None:
    """The TOC's own sort key must reproduce the TOC's own order.

    The page re-sorts by `position`, so a ملحق numbered from 1 alongside مواد
    1..N shuffles straight back into the body. `start_position` is
    `max(article_no)` and NOT `len(articles)`: this index is HOLED but trusted
    (3 rows, highest number 5), so the two differ and only one of them lands
    past the last مادة.
    """
    fake = _reg_with_appendix(_article_rows([1, 2, 5]), 2)
    doc = ls.get_regulation_doc(fake, "nizam-test")
    assert doc is not None

    positions = [t["position"] for t in doc["toc"]]
    assert positions == [1, 2, 5, 6, 7], positions
    assert [t["kind"] for t in doc["toc"]] == [
        "article", "article", "article", "appendix", "appendix",
    ]
    # Sorting the payload by its own key must not move anything.
    assert [t["id"] for t in sorted(doc["toc"], key=lambda t: t["position"])] == \
        [t["id"] for t in doc["toc"]]


def test_a_gated_preview_is_still_three_articles() -> None:
    """A ملحق is never a teaser — but the CTA count stops lying about it."""
    fake = _reg_with_appendix(_article_rows([1, 2, 3, 4, 5]), 4, tier="gated")
    doc = ls.get_regulation_doc(fake, "nizam-test")
    assert doc is not None

    assert [s["id"] for s in doc["visible_sections"]] == ["art-1", "art-2", "art-3"]
    # 5 مواد + 4 ملاحق - 3 rendered. Before this the count read 2 and the reader
    # was told six sections did not exist.
    assert doc["hidden_section_count"] == 6
    # …and the ملاحق are still LISTED — the rail is the free layer.
    assert [t["id"] for t in doc["toc"]][-4:] == ["apx-1", "apx-2", "apx-3", "apx-4"]


def test_the_full_reveal_carries_the_appendix() -> None:
    """Where 187 of the 188 affected أنظمة actually deliver: the paid reveal."""
    payload = ls.get_full_regulation(
        _reg_with_appendix(_article_rows([1, 2, 3]), 2, tier="gated"), "nizam-test"
    )
    assert payload is not None
    assert [s["id"] for s in payload["sections"]] == [
        "art-1", "art-2", "art-3", "apx-1", "apx-2",
    ]
    assert "نص الملحق 2" in json.dumps(payload, ensure_ascii=False)


def test_the_public_page_and_the_reveal_agree_on_the_appendix() -> None:
    """The broken-purchase guard, extended to the ملاحق.

    `use_article_surface` exists so the crawler and the paying reader get the
    same document. That guarantee is worth nothing if one surface appends the
    annexes and the other stops at the last مادة.
    """
    articles = _article_rows([1, 2, 3])
    doc = ls.get_regulation_doc(_reg_with_appendix(articles, 3), "nizam-test")
    full = ls.get_full_regulation(_reg_with_appendix(articles, 3), "nizam-test")
    assert doc is not None and full is not None
    assert [s["id"] for s in doc["visible_sections"]] == \
        [s["id"] for s in full["sections"]]


def test_html_comments_never_reach_the_reader_on_the_article_surface() -> None:
    """Ingestion markers are appendix-exclusive and render as literal HTML.

    `ArticleBody plain` does no markdown parsing and `toLegalBlocks` treats an
    unknown line as a paragraph, so «<!-- converted table -->» prints on the page.
    """
    dirty = ("<!-- Page 19 -->\n\n# الملحق (1)\n\n"
             "<!-- converted table -->\n- بند: 1\n<!-- نهاية الجدول -->\n")
    doc = ls.get_regulation_doc(
        _reg_with_appendix(_article_rows([1]), 1, apx_content=dirty), "nizam-test"
    )
    assert doc is not None
    body = doc["visible_sections"][-1]["text"]
    assert "<!--" not in body and "-->" not in body, body
    assert "الملحق (1)" in body and "بند: 1" in body


def test_html_comments_never_reach_the_reader_on_the_chunk_surface() -> None:
    """The same defect, live today on the 253 chunk-path regulations."""
    fake = _reg_doc_fake(_holed(232, 68), n_chunks=0)
    fake.tables["chunks_v2"] = [
        {"id": "cccccccc-0000-0000-0000-000000000001", "regulation_id": REG_ID,
         "title": "الفصل 1", "position": 1, "corpus": "with_articles",
         "chunk_ref": "r_chunk_001",
         "content": "نص\n\n<!-- converted table -->\n- بند\n<!-- end table -->"},
    ]
    doc = ls.get_regulation_doc(fake, "nizam-test")
    assert doc is not None
    assert "<!--" not in doc["visible_sections"][0]["text"]

    full = ls.get_full_regulation(fake, "nizam-test")
    assert full is not None
    assert "<!--" not in full["sections"][0]["text"]


def test_a_regulation_with_an_appendix_is_priced_for_it() -> None:
    """The price follows the render — the rule `unlock_cost` already states.

    40 مواد alone → 2. Add annex chunks and the same نظام serves a bigger
    document for the same unlock unless the weighting sees them.
    """
    plain = _reg_doc_fake(_article_rows(list(range(1, 41))), n_chunks=0)
    assert ls.unlock_cost(plain, "regulation", REG_ID) == 2

    with_apx = _reg_with_appendix(_article_rows(list(range(1, 41))), 9)
    # 40 + 9*3 = 67 → ceil(67/25) = 3.
    assert ls.unlock_cost(with_apx, "regulation", REG_ID) == 3


def test_an_appendix_lookup_failure_still_renders_the_regulation() -> None:
    """Fail-soft costs the annexes, never the statute.

    Opposite polarity to the article path on purpose: there an empty result means
    «render chunks», here it means «this نظام has no ملاحق», which is the common
    case. The price falls DOWN to today's number, never up.
    """
    fake = _reg_with_appendix(_article_rows([1, 2, 3]), 2)
    fake.fail_tables = {"chunks_v2"}

    doc = ls.get_regulation_doc(fake, "nizam-test")
    assert doc is not None
    assert [s["id"] for s in doc["visible_sections"]] == ["art-1", "art-2", "art-3"]
    assert all(t["kind"] == "article" for t in doc["toc"])
    assert ls.unlock_cost(fake, "regulation", REG_ID) == 1


# ===========================================================================
# 8. CHUNK TABLES — the grid instead of the flattening
# `.claude/plans/chunk_table_rendering.md` §3.1 (D8), §3.2, §3.3 · §6 tests 7-11
# ===========================================================================
#
# Every table in the regulation corpus was OCR'd and then CONVERTED TO PROSE
# before ingestion — that prose is what BM25 indexes and what the model reads.
# `chunks_v2.content_display` is the same text with each confidently-resolved
# table collapsed to a whole-line `TBL_…` token, and `chunk_tables_v2` holds the
# HTML behind each token. Measured 2026-08-24: 24,511 tables, 8,855 chunks
# carrying a display body, 532 published أنظمة affected.
#
# Feeding that display body to `truncate_for_gate` breaks in TWO directions at
# once, and both are measured:
#
#   * 6,920 of 8,855 chunks (78.1%) carry a token inside the first 600 chars,
#     and on 191 of them a 600-char cut lands MID-TOKEN — a raw
#     `TBL_17630_reg_5…` fragment shipped to an anonymous crawler, worse than a
#     whole raw token because the renderer's own regex will not even match it.
#   * A whole token costs ~30 chars of budget and renders ~880 chars of law
#     (mean `table_md` 878, p95 2,483), so a 600-char preview holding two tables
#     quietly TRIPLES its own exposure.
#
# `truncate_segments_for_gate` is the fix and this section is its proof.

from shared.library.chunk_tables import (          # noqa: E402
    TABLE_PLACEHOLDER,
    split_body,
    tables_by_ref,
    visible_text,
)

# NOTE — there is no longer a slack constant here, and that is the point.
# `split_body` consumes the newline on either side of a token as a line
# separator, so the walk used to arrive at each later segment a few chars richer
# than the string cut, which rounded up to a whole word. Charging that separator
# in `truncate_segments_for_gate` removed the drift, so every comparison below
# against `truncate_for_gate` is an EXACT equality.

# 7 chars. The word length is load-bearing in `_neutrality_fixture` — see there.
_W7 = "التعاقد"


def _run(n_words: int) -> str:
    """`n_words` space-separated 7-char words → exactly ``8 * n_words - 1`` chars."""
    return " ".join([_W7] * n_words)


TBL_1 = "TBL_17393_reg_029_chunk_001_1"
TBL_2 = "TBL_17393_reg_029_chunk_001_2"

# A real merged-cell grid — `rowspan`/`colspan` survive the sanitizer, so the
# fixture exercises the same shape 34.1% of the corpus has.
_HTML_1 = (
    '<table><tr><th colspan="2">حدود الهجرة</th></tr>'
    "<tr><td>الرصاص</td><td>90</td></tr>"
    "<tr><td>الزئبق</td><td>60</td></tr></table>"
)
_HTML_2 = "<table><tr><th>البند</th></tr><tr><td>ملحق</td></tr></table>"


def _wide_grid(*, rows: int) -> str:
    """A violation-fine grid shaped like `17405_reg_603_chunk_019`'s.

    م / المخالفة / حد قيمة الغرامة, with a `colspan` header group — the shape
    that carries real law in its cells and nothing in its `table_md`.
    """
    body = "".join(
        f"<tr><td>{i}</td><td>مخالفة رقم {i}</td><td>{i * 500} ريال</td></tr>"
        for i in range(1, rows + 1)
    )
    return (
        '<table><tr><th colspan="3">جدول الغرامات</th></tr>'
        "<tr><th>م</th><th>المخالفة</th><th>حد قيمة الغرامة</th></tr>"
        f"{body}</table>"
    )


def _table_row(
    ref: str, md: str, *, html: str = _HTML_1, chunk_id: str = "", reg: str = REG_ID
) -> dict[str, Any]:
    """One ``chunk_tables_v2`` row, in the shape PostgREST actually returns."""
    return {
        "table_ref": ref,
        "chunk_id": chunk_id,
        "regulation_id": reg,
        "table_html": html,
        "table_md": md,
    }


def _display_chunk(
    chunk_id: str,
    *,
    content: str,
    content_display: Optional[str],
    title: str = "الفصل 1",
    corpus: str = "without_articles",
    position: int = 1,
) -> dict[str, Any]:
    """A ``chunks_v2`` row carrying BOTH bodies — the agent view and the user view."""
    return {
        "id": chunk_id,
        "regulation_id": REG_ID,
        "title": title,
        "position": position,
        "corpus": corpus,
        "chunk_ref": f"17393_reg_029_chunk_{position:03d}",
        "content": content,
        "content_display": content_display,
    }


CHUNK_ID = "eeeeeeee-0000-0000-0000-000000000001"


def _reg_with_tables(
    chunks: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    *,
    tier: str = "gated",
) -> FakeSupabase:
    """A CHUNK-SURFACE نظام (holed index → flip) carrying real table rows."""
    fake = _reg_doc_fake(_holed(232, 68), n_chunks=0)
    fake.tables["seo_item_meta"][0]["seo_tier"] = tier
    fake.tables["chunks_v2"] = chunks
    fake.tables["chunk_tables_v2"] = tables
    return fake


def _prose_text(section: dict[str, Any]) -> str:
    """The section's rendered prose, `TBL_…` tokens removed.

    A token is not legal text — it is a placement instruction for the client —
    so it is stripped before any measurement.
    """
    return TABLE_PLACEHOLDER.sub("", section["text"])


def _visible_legal_text(section: dict[str, Any]) -> str:
    """Prose + the PROSE FORM of each rendered grid (``table_md``).

    The measure D8 is written against: how much law the section stands for,
    expressed in the same units `truncate_for_gate` spends.
    """
    return _prose_text(section) + "".join(
        t["md"] for t in section["tables"].values()
    )


def _reader_visible_text(section: dict[str, Any]) -> str:
    """Prose + the text a reader actually reads OFF each rendered grid.

    The security measure, and the one with no tolerance on it. For the 244
    tables whose `table_md` is the ingestion error placeholder «[خطأ في التحويل
    - انتهت المهلة]» this is the only honest count — `md` reports 31 characters
    for a 3.2 KB penalty schedule, which is the hole `table_weight`'s ``max``
    closes.
    """
    return _prose_text(section) + "".join(
        visible_text(t["html"]) for t in section["tables"].values()
    )


def _nonspace(text: str) -> int:
    """Non-whitespace characters — «how much law», immune to layout.

    The two views deliberately disagree about whitespace: `content` spends two
    newlines around a flattened block where `project_segments` re-emits one.
    Comparing raw lengths turns an exposure assertion into a whitespace assay.
    """
    return len("".join(text.split()))


def _visible_legal_chars(section: dict[str, Any]) -> int:
    return len(_visible_legal_text(section))


def _reader_visible_chars(section: dict[str, Any]) -> int:
    return len(_reader_visible_text(section))


# ---- 8.1 D8 — THE GATE IS NEUTRAL TO TABLES -------------------------------


def _neutrality_fixture() -> tuple[FakeSupabase, str]:
    """A chunk whose two views hold the SAME law, sized for an EXACT comparison.

    ``content``          = P + BLANK + MD + BLANK + S   (the prose, agent view)
    ``content_display``  = P + BLANK + TOKEN + BLANK + S (the user view)

    with ``MD`` = ``table_md``, i.e. byte-identical to the block the token
    replaced. That identity is the whole basis of D8.

    A BLANK line on each side of the token, because that is what the corpus has:
    86.2% of the 24,511 tokens carry two newlines before them and 74.1% two
    after (measured 2026-08-25). It is also the shape the gate charges exactly —
    ``sep_cost`` is 2 — so the segment walk and the string cut reach ``S`` with
    the same budget and cut at the same space.

    ``MD`` is a REAL conversion — long enough that ``table_weight``'s ``max``
    resolves to ``len(md)`` — so this fixture measures the neutral 97.8% of the
    corpus. The conversion-error tail gets its own test, and there the gate is
    deliberately stricter than the prose baseline.
    """
    p, md, s = _run(12), _run(25), _run(200)      # 95, 199, 1599 chars
    content = p + "\n\n" + md + "\n\n" + s
    content_display = p + "\n\n" + TBL_1 + "\n\n" + s
    fake = _reg_with_tables(
        [_display_chunk(CHUNK_ID, content=content, content_display=content_display)],
        [_table_row(TBL_1, md, chunk_id=CHUNK_ID)],
    )
    return fake, content


def test_the_gate_is_neutral_to_tables() -> None:
    """THE headline assertion — D8 made checkable.

    A table is charged ``max(len(table_md), len(visible_text(table_html)))``. The
    ``md`` half is the original D8: ``table_md`` is *literally the prose that
    occupied those bytes* before ingestion collapsed it to a token, so the
    600-char preview buys the reader the same quantity of law it bought before
    this feature existed. This is the assertion that answers «does this leak
    more».

    ⚠ MEASURED IN NON-WHITESPACE CHARACTERS, AND THAT IS DELIBERATE. The two
    views do not agree on whitespace and must not be asked to: `content` spends
    two newlines on each side of the flattened block, while `project_segments`
    re-emits one. Counting raw bytes would make this test a whitespace assay.
    Non-space characters are «how much law», they are exactly invariant here,
    and they are what an exposure budget is actually about.

    ⚠ The companion claim is `<=`, NOT `==`, and that is also the point. Strict
    equality on every table would have to hold for the **244 whose ``table_md``
    is the ingestion error placeholder** «[خطأ في التحويل - انتهت المهلة]» — 31
    characters standing in for a full penalty schedule. There the prose baseline
    was itself leaking a grid for free, so matching it is the last thing this
    gate should do; it is deliberately STRICTER.
    """
    fake, content = _neutrality_fixture()
    doc = ls.get_regulation_doc(fake, "nizam-test")
    assert doc is not None
    section = doc["visible_sections"][0]

    # The grid really did render — otherwise this test proves nothing.
    assert section["tables"], "fixture rendered no table"
    assert section["is_truncated"] is True

    today = ls.truncate_for_gate(content, "gated", free_chars=600)["visible_text"]

    # (a) THE SECURITY CLAIM: what an anonymous crawler actually reads off the
    #     page — prose plus the text inside each grid — never exceeds what the
    #     prose gate served it. Hard `<=`, no tolerance.
    assert _nonspace(_reader_visible_text(section)) <= _nonspace(today)
    # (b) THE NEUTRALITY CLAIM, in the units the budget is spent in. EXACT.
    assert _nonspace(_visible_legal_text(section)) == _nonspace(today)


def test_the_gate_is_stricter_than_prose_on_a_conversion_error_table() -> None:
    """The 244 tables that never got a prose conversion — the hole `max` closes.

    Their ``table_md`` is «[خطأ في التحويل - انتهت المهلة]», 31 chars, standing
    in for a real grid. `17405_reg_603_chunk_019` is one: two 3.2 KB
    violation-fine grids (م / المخالفة / حد قيمة الغرامة) whose entire prose form
    is that sentence, twice.

    Charged at ``len(md)`` the token costs 31 characters of a 600-char budget and
    ships a complete penalty schedule to an anonymous crawler. Charged at
    ``max(len(md), len(visible))`` the GRID does not fit, so it degrades to the
    prose it replaced — which for these rows is the error string, i.e. precisely
    the bytes that are live today. Ugly, deliberately not special-cased, and a
    corpus-side bug to file upstream (D8a).
    """
    err = "[خطأ في التحويل - انتهت المهلة]"
    grid = _wide_grid(rows=60)               # ~900 chars of reader-visible law
    assert len(visible_text(grid)) > 600 > len(err)

    p, s = _run(7), _run(60)
    content = p + "\n" + err + "\n" + s
    fake = _reg_with_tables(
        [
            _display_chunk(
                CHUNK_ID, content=content, content_display=p + "\n" + TBL_1 + "\n" + s
            )
        ],
        [_table_row(TBL_1, err, html=grid, chunk_id=CHUNK_ID)],
    )
    section = ls.get_regulation_doc(fake, "nizam-test")["visible_sections"][0]

    # Under a `len(md)` charge the grid costs 31 chars and sails through …
    assert len(p) + len(err) < 600
    # … under the corrected charge no grid ships at all, and the reader gets
    # byte-for-byte what that section renders today.
    assert section["tables"] == {}
    assert "TBL_" not in section["text"]
    assert section["text"] == ls.truncate_for_gate(
        content, "gated", free_chars=600
    )["visible_text"]
    assert _reader_visible_chars(section) <= len(
        ls.truncate_for_gate(content, "gated", free_chars=600)["visible_text"]
    )


@pytest.mark.parametrize("sep", ["\n", "\n\n"], ids=["one-newline", "blank-line"])
def test_the_walk_never_spends_more_than_its_budget(sep: str) -> None:
    """Neutrality in its BUDGET form — the same claim, at every cut position.

    ``test_the_gate_is_neutral_to_tables`` pins the exact character count on one
    tuned fixture. This is the general statement behind it: a table is charged
    ``len(table_md)``, i.e. what its prose cost, so the walk can NEVER hand out
    more weighted characters than ``free_chars`` — at any budget, on either
    separator shape, with the cut landing before / inside / after a grid.

    Stated as a spend rather than as a diff against ``truncate_for_gate`` on
    purpose. `split_body` consumes the newlines around a token as line
    separators, so the walk is not charged the 2–4 chars the string cut spends on
    them, and `project_segments` puts a single one back; comparing the two
    rendered lengths therefore wobbles by a word at the cut boundary while the
    thing that actually governs exposure — the spend — does not wobble at all.
    """
    md1, md2 = _run(9), _run(113)
    body = sep.join([_run(4), TBL_1, _run(7), TBL_2, _run(30)])
    segments = split_body(
        body,
        # TBL_2 is a conversion-error table: `md` says 31 chars, the grid says
        # ~900. The sweep therefore covers both charge regimes at once.
        tables_by_ref(
            [
                _table_row(TBL_1, md1),
                _table_row(TBL_2, md2, html=_wide_grid(rows=60)),
            ]
        ),
    )
    assert sum(1 for seg in segments if seg["kind"] == "table") == 2

    ceiling = len(body) + sum(
        seg["weight"] for seg in segments if seg["kind"] == "table"
    )
    for budget in range(0, ceiling + 2):
        cut = ls.truncate_segments_for_gate(segments, "gated", free_chars=budget)
        spend = sum(
            seg["weight"] if seg["kind"] == "table" else len(seg["text"])
            for seg in cut["visible_segments"]
        )
        assert spend <= budget, (budget, spend)
        # …and «is_truncated» is honest: false ⇒ every segment is represented,
        # each either as itself or as the prose its grid degraded to. Identity
        # is the WRONG assertion now — a grid that does not fit is replaced by a
        # text segment carrying its `md`, and that is not a truncation.
        if not cut["is_truncated"]:
            assert len(cut["visible_segments"]) == len(segments), budget
            for was, now in zip(segments, cut["visible_segments"]):
                if was != now:
                    assert was["kind"] == "table" and now["kind"] == "text"
                    assert now["text"] == was["md"], budget

    # THE FALLBACK PATH obeys the same ceiling. `_chunk_section_body` swaps in
    # the prose body whenever the walk yields nothing, and a preview that
    # restores content must not restore MORE than the budget buys.
    over_budget = _run(230)                  # 1,839 chars — the p50 table_md
    prose_row = _display_chunk(
        CHUNK_ID,
        content=sep.join([over_budget, _run(100)]),
        content_display=sep.join([TBL_1, _run(100)]),
    )
    tmap = ls._ChunkTableMap(
        {CHUNK_ID: tables_by_ref([_table_row(TBL_1, over_budget)])}
    )
    for budget in range(0, 1400):
        out = ls._chunk_section_body(
            prose_row, tmap, gate="gated", free_chars=budget
        )
        if out["tables"]:                    # the walk ran — weigh the grid
            spent = len(TABLE_PLACEHOLDER.sub("", out["text"])) + sum(
                max(len(t["md"]), len(visible_text(t["html"])))
                for t in out["tables"].values()
            )
            # `project_segments` re-adds one separator newline per token that
            # the walk never charged for; that is the whole of the slack.
            assert spent <= budget + len(out["tables"]), (budget, spent)
        else:                                # the fallback ran — plain prose
            assert len(out["text"]) <= budget, (budget, len(out["text"]))


# ---- 8.2 A table is atomic ------------------------------------------------


def test_a_grid_is_never_cut_through() -> None:
    """ATOMICITY, and it survives the degrade rule intact.

    What D8 protects is that a GRID is never cut: half a fines table reads as
    the whole schedule, so a partial grid misrepresents the data. A budget that
    lands mid-table therefore draws NO grid — it renders the prose the table was
    flattened into, cut by the ordinary whitespace rule. Prose cuts fine, and it
    is what `truncate_for_gate` cuts today for that same region.
    """
    p, md, s = _run(12), _run(25), _run(200)
    body = p + "\n" + TBL_1 + "\n" + s
    tables = tables_by_ref([_table_row(TBL_1, md)])
    segments = split_body(body, tables)
    assert [seg["kind"] for seg in segments] == ["text", "table", "text"]

    # len(p)=95, len(md)=199 — a budget that clears the prose and lands halfway
    # into the grid.
    cut = ls.truncate_segments_for_gate(segments, "gated", free_chars=len(p) + 100)
    text, rendered = ls.project_segments(cut["visible_segments"])

    # No grid, whole or partial — and no token pointing at one.
    assert rendered == {}
    assert "<table" not in text and "TBL_" not in text
    assert all(seg["kind"] == "text" for seg in cut["visible_segments"])
    assert cut["is_truncated"] is True
    # The grid became its prose, cut at a word boundary — and because the
    # inter-segment separator is charged, the result is BYTE-FOR-BYTE the
    # string cut `truncate_for_gate` performs on `content` at the same budget.
    content = p + "\n" + md + "\n" + s
    assert text == ls.truncate_for_gate(
        content, "gated", free_chars=len(p) + 100
    )["visible_text"]
    # …and the trailing prose is still withheld: the walk stopped.
    assert not text.endswith(s)


def test_a_degraded_grid_is_weighed_into_the_placeholder_bars() -> None:
    """The bars size off what is actually left, not off the token's 30 chars.

    Sizing them off the raw ``content_display`` remainder would tell a reader
    «~1 line is behind this gate» about a 900-char annex table.
    """
    md = _run(113)                       # 903 chars of law
    lead = _run(6)                       # 47
    segments = split_body(lead + "\n" + TBL_1, tables_by_ref([_table_row(TBL_1, md)]))
    assert segments[-1]["weight"] == len(md)   # a real conversion: `md` dominates

    cut = ls.truncate_segments_for_gate(segments, "gated", free_chars=100)
    # 100 - the lead - the 1-char separator the projection re-inserts.
    shown = ls.truncate_for_gate(md, "gated", free_chars=100 - len(lead) - 1)

    assert cut["is_truncated"] is True
    assert cut["hidden_placeholder_lines"] == math.ceil(
        (len(md) - len(shown["visible_text"])) / 90
    )
    # The token's own ~29 chars would have sized this at ONE bar.
    assert cut["hidden_placeholder_lines"] > 1


def test_no_token_survives_truncation() -> None:
    """PROPERTY: at EVERY budget from 0 to len(body), `text` holds no loose token.

    This is the 191-chunk bug — the chunks where a 600-char cut lands mid-token
    and ships a `TBL_17630_reg_5…` fragment to a crawler. Sweeping every single
    cut position covers that shape and every other straddle with it, so the
    guarantee is structural rather than anecdotal: the literal string ``TBL_``
    may appear in the payload ONLY as a whole line that ``tables`` can resolve.
    """
    md1, md2 = _run(9), _run(14)
    body = f"{_run(4)}\n{TBL_1}\n{_run(7)}\n\n{TBL_2}\n\n{_run(5)}"
    tables = tables_by_ref(
        [_table_row(TBL_1, md1), _table_row(TBL_2, md2, html=_HTML_2)]
    )
    segments = split_body(body, tables)
    assert sum(1 for s in segments if s["kind"] == "table") == 2

    # The fixture really does carry BOTH shapes of the bug, so what follows is
    # not vacuous:
    #
    # (a) a RAW character slice lands mid-token and ships `TBL_17393_reg…` as
    #     prose — worse than a whole raw token, because it is no longer a whole
    #     line and the renderer's own regex will not even recognise it;
    assert any(
        (tail := body[:b].rsplit("\n", 1)[-1]).startswith("TBL_")
        and tail not in (TBL_1, TBL_2)
        for b in range(len(body) + 1)
    ), "fixture does not reproduce the mid-token cut"
    # (b) even the whitespace-aware `truncate_for_gate` — which cannot split a
    #     token, because a token line is bounded by newlines — happily ships a
    #     WHOLE raw one with nothing to resolve it (D3, the 6,920-chunk case).
    assert any(
        TABLE_PLACEHOLDER.search(
            ls.truncate_for_gate(body, "gated", free_chars=b)["visible_text"]
        )
        for b in range(len(body) + 1)
    ), "fixture does not reproduce the raw-token preview"

    for budget in range(0, len(body) + 1):
        cut = ls.truncate_segments_for_gate(segments, "gated", free_chars=budget)
        text, rendered = ls.project_segments(cut["visible_segments"])
        tokens = TABLE_PLACEHOLDER.findall(text)
        # Every trace of `TBL_` is a whole-line token …
        assert text.count("TBL_") == len(tokens), (budget, text)
        # … and every one of them resolves.
        assert set(tokens) <= set(rendered), (budget, tokens, list(rendered))


def test_the_token_set_and_the_table_map_agree() -> None:
    """§3.3's invariant, both directions, at every budget.

    A token with no entry renders RAW on a statute page (D3). An entry with no
    token renders NOWHERE and is dead weight baked into an ISR payload for 24h.
    Neither is allowed, and «tokens ⊆ tables» alone would let the second through.
    """
    md1, md2 = _run(9), _run(14)
    body = f"{_run(4)}\n{TBL_1}\n{_run(7)}\n\n{TBL_2}\n\n{_run(5)}"
    segments = split_body(
        body,
        tables_by_ref([_table_row(TBL_1, md1), _table_row(TBL_2, md2, html=_HTML_2)]),
    )

    for gate, budget in [("open", 600), ("gated", 0), ("gated", 40),
                         ("gated", 200), ("gated", 10_000)]:
        cut = ls.truncate_segments_for_gate(segments, gate, free_chars=budget)
        text, rendered = ls.project_segments(cut["visible_segments"])
        assert set(TABLE_PLACEHOLDER.findall(text)) == set(rendered), (gate, budget)
        for ref, payload in rendered.items():
            assert payload["html"].startswith("<table"), ref
            assert set(payload) == {"html", "md"}


def test_an_unresolvable_token_reaches_no_payload() -> None:
    """D3 at the service boundary: a token with no row leaves NO trace.

    Corpus-wide this is unreachable today (0 unresolvable tokens across all
    24,511), but the local corpus runs ahead of the DB and re-ingests recur.
    """
    fake = _reg_with_tables(
        [
            _display_chunk(
                CHUNK_ID,
                content=f"{_run(4)}\n{_run(6)}\n{_run(4)}",
                content_display=f"{_run(4)}\n{TBL_1}\n{_run(4)}",
            )
        ],
        [],                                  # …and not one chunk_tables_v2 row
        tier="open",
    )
    section = ls.get_regulation_doc(fake, "nizam-test")["visible_sections"][0]
    assert "TBL_" not in section["text"]
    assert section["tables"] == {}


# ---- 8.4 Fail-soft, in the direction that is NOT obvious ------------------


def test_a_tables_read_failure_falls_back_to_prose() -> None:
    """§3.2's named hazard: a degradation may cost fidelity, never CONTENT.

    The obvious fallback is the wrong one. Rendering `content_display` with an
    empty table map lets every token resolve to nothing (D3), which does not
    degrade the نظام — it silently DELETES a table from it. Falling back to
    `content` costs the grid and keeps every word of the law.
    """
    p, md, s = _run(12), _run(25), _run(20)
    content = f"{p}\n{md}\n{s}"
    fake = _reg_with_tables(
        [
            _display_chunk(
                CHUNK_ID, content=content, content_display=f"{p}\n{TBL_1}\n{s}"
            )
        ],
        [_table_row(TBL_1, md, chunk_id=CHUNK_ID)],
        tier="open",
    )
    fake.fail_tables = {"chunk_tables_v2"}

    section = ls.get_regulation_doc(fake, "nizam-test")["visible_sections"][0]

    assert section["text"] == content       # the prose, tables intact as text
    assert section["tables"] == {}
    assert "TBL_" not in section["text"]
    # The flattened table survived — which is the entire point.
    assert md in section["text"]


def test_a_successful_read_with_no_rows_is_not_a_failure() -> None:
    """The other half: an EMPTY map from a healthy read is the 82% case.

    39,535 of 48,390 chunks carry no table at all. Treating «no rows» as «read
    failed» would push every one of them down the fallback path forever.
    """
    body = f"{_run(4)}\n{_run(6)}"
    fake = _reg_with_tables(
        [_display_chunk(CHUNK_ID, content=body, content_display=None)], [], tier="open"
    )
    section = ls.get_regulation_doc(fake, "nizam-test")["visible_sections"][0]
    assert section["text"] == body
    assert section["tables"] == {}


# ---- 8.5 «gated» still MEANS something ------------------------------------


def test_a_gated_preview_still_withholds() -> None:
    """MIN_WITHHELD_* still hold once tables are weighted in — the D8 payoff.

    THE HAZARD, made concrete: the token is only 29 characters, so a naive
    string cut at 600 leaves it whole inside the preview — and a client that
    resolved it would draw a 1,999-char grid bought with 29 chars of budget.
    Weighted properly the grid does not fit, degrades to its prose, and the
    preview withholds a real remainder of the document.
    """
    p, md, s = _run(7), _run(250), _run(250)      # 55 / 1,999 / 1,999
    content = p + "\n" + md + "\n" + s
    content_display = p + "\n" + TBL_1 + "\n" + s

    naive = ls.truncate_for_gate(content_display, "gated", free_chars=600)
    assert TBL_1 in naive["visible_text"]         # the raw token, shipped

    fake = _reg_with_tables(
        [_display_chunk(CHUNK_ID, content=content, content_display=content_display)],
        [_table_row(TBL_1, md, chunk_id=CHUNK_ID)],
    )
    section = ls.get_regulation_doc(fake, "nizam-test")["visible_sections"][0]

    total = len(p) + max(len(md), len(visible_text(_HTML_1))) + len(s)
    withheld = total - _visible_legal_chars(section)
    assert section["is_truncated"] is True
    assert withheld >= ls.MIN_WITHHELD_CHARS
    assert withheld / total >= ls.MIN_WITHHELD_RATIO
    # No grid was drawn, so no grid was bought.
    assert section["tables"] == {}
    assert "TBL_" not in section["text"]


def test_an_open_chunk_regulation_ships_its_grids() -> None:
    """The bulk of the prize: 5,156 of v1's 8,017 tables render on this path."""
    md = _run(20)
    fake = _reg_with_tables(
        [
            _display_chunk(
                CHUNK_ID,
                content=f"{_run(4)}\n{md}\n{_run(4)}",
                content_display=f"{_run(4)}\n{TBL_1}\n{_run(4)}",
            )
        ],
        [_table_row(TBL_1, md, chunk_id=CHUNK_ID)],
        tier="open",
    )
    section = ls.get_regulation_doc(fake, "nizam-test")["visible_sections"][0]

    assert set(section["tables"]) == {TBL_1}
    assert section["tables"][TBL_1]["md"] == md
    assert 'colspan="2"' in section["tables"][TBL_1]["html"]
    # THE TOKEN STAYS IN THE TEXT — it is how the client knows where the grid goes.
    assert f"\n{TBL_1}\n" in section["text"]
    assert section["is_truncated"] is False


def test_the_appendix_surface_renders_its_grids() -> None:
    """1,396 + 1,465 tables ride the ملاحق, and on a لائحة فنية they ARE the نظام."""
    md = _run(20)
    apx = _display_chunk(
        "dddddddd-0000-0000-0000-000000000001",
        content=f"{_run(3)}\n{md}",
        content_display=f"{_run(3)}\n{TBL_2}",
        title="ملحق (1)",
        corpus="appendix",
    )
    fake = _reg_doc_fake(_article_rows([1, 2, 3]), n_chunks=0)
    fake.tables["seo_item_meta"][0]["seo_tier"] = "open"
    fake.tables["chunks_v2"] = [apx]
    fake.tables["chunk_tables_v2"] = [
        _table_row(TBL_2, md, html=_HTML_2, chunk_id=apx["id"])
    ]

    doc = ls.get_regulation_doc(fake, "nizam-test")
    apx_section = doc["visible_sections"][-1]
    assert apx_section["id"] == "apx-1"
    assert set(apx_section["tables"]) == {TBL_2}
    assert apx_section["text"].endswith(TBL_2)

    # …and the paid reveal renders the SAME grid from the same rows.
    full = ls.get_full_regulation(fake, "nizam-test")
    assert full["sections"][-1]["tables"] == apx_section["tables"]


def test_the_reveal_truncates_nothing_and_keeps_every_grid() -> None:
    """`get_full_regulation`'s chunk branch — gate='open', so no walk can bite."""
    md1, md2 = _run(9), _run(113)
    fake = _reg_with_tables(
        [
            _display_chunk(
                CHUNK_ID,
                content=f"{_run(4)}\n{md1}\n{_run(4)}\n{md2}",
                content_display=f"{_run(4)}\n{TBL_1}\n{_run(4)}\n{TBL_2}",
            )
        ],
        [
            _table_row(TBL_1, md1, chunk_id=CHUNK_ID),
            _table_row(TBL_2, md2, html=_HTML_2, chunk_id=CHUNK_ID),
        ],
    )
    full = ls.get_full_regulation(fake, "nizam-test")
    section = full["sections"][0]
    assert set(section["tables"]) == {TBL_1, TBL_2}
    assert set(TABLE_PLACEHOLDER.findall(section["text"])) == {TBL_1, TBL_2}


def test_an_article_fallback_chunk_renders_its_grids() -> None:
    """The `_chunk_row_map` → `_article_sections` path (a sliver, but free).

    An extracted `article_text` is a slice of `content` and can carry no token —
    §3.4's named limit — so only the CHUNK-FALLBACK bodies resolve tables here.
    """
    md = _run(20)
    art = {
        "regulation_id": REG_ID, "article_no": 1, "article_label": "المادة 1",
        "slug": "m-1", "chunk_id": CHUNK_ID, "article_text": None,
        "extraction_status": "pending",
    }
    fake = _reg_doc_fake([art], n_chunks=0)
    fake.tables["seo_item_meta"][0]["seo_tier"] = "open"
    fake.tables["chunks_v2"] = [
        _display_chunk(
            CHUNK_ID,
            content=f"{_run(4)}\n{md}",
            content_display=f"{_run(4)}\n{TBL_1}",
            corpus="with_articles",
        )
    ]
    fake.tables["chunk_tables_v2"] = [_table_row(TBL_1, md, chunk_id=CHUNK_ID)]

    doc = ls.get_regulation_doc(fake, "nizam-test")
    section = doc["visible_sections"][0]
    assert section["id"] == "art-1"
    assert set(section["tables"]) == {TBL_1}


def test_an_extracted_article_is_untouched_and_carries_no_tables() -> None:
    """The ~50k مواد this feature must not perturb: byte-identical, `tables={}`."""
    doc = ls.get_regulation_doc(_reg_doc_fake(_holed(3, 3), n_chunks=0), "nizam-test")
    for section in doc["visible_sections"]:
        assert section["tables"] == {}
        assert section["text"].startswith("نص المادة")


def test_the_tables_read_is_one_round_trip_per_document() -> None:
    """ONE batched read filtered on `regulation_id` — never one per chunk.

    `idx_chunk_tables_reg` covers it. Per-chunk would be 60 round trips on a
    chunk-surface نظام, at page-render latency, for a payload the ISR bake keeps
    for 24h.
    """
    md = _run(6)
    chunks = [
        _display_chunk(
            f"eeeeeeee-0000-0000-0000-{i:012d}",
            content=f"{_run(3)}\n{md}",
            content_display=f"{_run(3)}\n{TBL_1}",
            position=i,
        )
        for i in (1, 2, 3)
    ]
    fake = _CountingSupabase(
        seo_item_meta=[{"content_type": "regulation", "content_id": REG_ID,
                        "slug": "nizam-test", "seo_tier": "open",
                        "gate_override": None}],
        regulations_v2=[_bare_reg_row()],
        seo_articles=_holed(232, 68),
        chunks_v2=chunks,
        chunk_tables_v2=[_table_row(TBL_1, md, chunk_id=chunks[0]["id"])],
    )
    ls.get_regulation_doc(fake, "nizam-test")
    assert fake.tables_queried.count("chunk_tables_v2") == 1


def test_an_all_extracted_regulation_never_reads_the_tables_table() -> None:
    """No chunk-shaped body ⇒ no round trip. The common shape pays nothing."""
    fake = _CountingSupabase(
        seo_item_meta=[{"content_type": "regulation", "content_id": REG_ID,
                        "slug": "nizam-test", "seo_tier": "open",
                        "gate_override": None}],
        regulations_v2=[_bare_reg_row()],
        seo_articles=_article_rows([1, 2, 3]),
        chunks_v2=[],
    )
    ls.get_regulation_doc(fake, "nizam-test")
    assert "chunk_tables_v2" not in fake.tables_queried


def _bare_reg_row() -> dict[str, Any]:
    return {"id": REG_ID, "reg_ref": "r", "clean_title": "نظام", "title": None,
            "entity_name": None, "doc_type_bucket": None,
            "status_class": "in_force", "legal_authority": None,
            "start_date": None, "sectors": [], "summary": None,
            "llm_summary": None, "landing_url": None, "pdf_url": None}


# ---- 8.7 The wire ----------------------------------------------------------


def test_the_response_model_declares_tables() -> None:
    """`response_model` strips undeclared keys — the `kind`-on-`TocEntry` trap.

    A service that emits `tables` and a model that does not list it produces a
    payload whose `text` still carries the tokens and whose grids are GONE: naked
    `TBL_…` lines on a statute page, i.e. exactly D3's failure arriving through
    the serializer instead of the renderer.
    """
    from backend.app.api.public_library import LibraryFullSection, VisibleSection

    assert "tables" in VisibleSection.model_fields
    assert "tables" in LibraryFullSection.model_fields
    # Absent ⇒ `{}`, never None: one shape on the wire.
    assert VisibleSection(id="s", text="", is_truncated=False,
                          hidden_placeholder_lines=0).tables == {}
    assert LibraryFullSection(id="s", text="").tables == {}

    section = VisibleSection(
        id="s", text=f"نص\n{TBL_1}", is_truncated=False,
        hidden_placeholder_lines=0,
        tables={TBL_1: {"html": _HTML_1, "md": "بند"}},
    )
    assert section.model_dump()["tables"][TBL_1]["html"] == _HTML_1


# ---- 8.8 An oversized grid degrades — the SECTION never does (D8a) --------
#
# A grid that does not fit degrades to the prose it replaced, and only THAT
# table degrades. The earlier rule withheld the table and stopped the walk,
# which punished the whole section for one oversized grid: measured over all
# 8,855 display chunks at `free_chars=600` it blanked 2,640 (29.8%) outright and
# left another 2,077 showing a median of 83 prose characters where ~600 ship
# today — 182 published أنظمة between them. The granularity was the bug.
#
# Case 3 always fills the budget, so a section now serves ~free_chars exactly as
# it does today, AND tables that fit still render as grids in the same section —
# which no section-level fallback can do.


def _oversized_fixture(
    *, sep: str = "\n", tier: str = "gated"
) -> tuple[FakeSupabase, str]:
    """A chunk whose FIRST segment is a grid bigger than the whole budget."""
    md, s = _run(230), _run(100)              # 1,839 chars — the p50 table_md
    assert len(md) > 600
    content = sep.join([md, s])
    fake = _reg_with_tables(
        [
            _display_chunk(
                CHUNK_ID, content=content, content_display=sep.join([TBL_1, s])
            )
        ],
        [_table_row(TBL_1, md, chunk_id=CHUNK_ID)],
        tier=tier,
    )
    return fake, content


@pytest.mark.parametrize("sep", ["\n", "\n\n"], ids=["one-newline", "blank-line"])
def test_an_oversized_grid_degrades_to_its_prose(sep: str) -> None:
    """Case 3 — and the end of the 2,640-blank / 2,077-thin regression.

    «Withhold the grid» must not degrade into «withhold the نظام». The grid is
    not drawn (atomicity holds), its prose is, and the budget comes out full.
    """
    fake, _ = _oversized_fixture(sep=sep)
    section = ls.get_regulation_doc(fake, "nizam-test")["visible_sections"][0]

    assert section["text"].strip(), "blank preview — the 161-page regression"
    assert len(section["text"]) > 500          # the budget is FILLED, not spent
    assert len(section["text"]) <= 600
    # No grid, whole or partial, and no token pointing at one.
    assert section["tables"] == {}
    assert "TBL_" not in section["text"]
    assert "<table" not in section["text"]
    assert section["is_truncated"] is True


@pytest.mark.parametrize("sep", ["\n", "\n\n"], ids=["one-newline", "blank-line"])
def test_a_degraded_grid_is_byte_for_byte_what_ships_today(sep: str) -> None:
    """THE no-regression proof, and it is an exact equality (unlike §6 test 8).

    Case 3 renders `table_md` cut by the ordinary whitespace rule, and `md` is
    byte-identical to the block it replaced inside `content`. With the grid
    leading the body nothing has been charged before it, so there is not even
    separator slack: both sides of this assertion are the SAME string through
    the SAME function at the SAME budget. It cannot leak and it cannot regress.

    This is the 2,640-chunk population — the one that used to render blank.
    """
    fake, content = _oversized_fixture(sep=sep)
    section = ls.get_regulation_doc(fake, "nizam-test")["visible_sections"][0]

    today = ls.truncate_for_gate(content, "gated", free_chars=600)
    assert section["text"] == today["visible_text"]
    assert section["is_truncated"] == today["is_truncated"]
    assert section["hidden_placeholder_lines"] == today["hidden_placeholder_lines"]


def test_a_grid_that_fits_still_renders_beside_one_that_does_not() -> None:
    """THE POINT OF THE WHOLE RULE: only the offending table degrades.

    One section, two grids. The first fits and is drawn; the second does not and
    becomes prose. A section-level fallback cannot express this — it would trade
    the first grid away to rescue the second, on the very documents the feature
    was built for.
    """
    lead, small, big = _run(4), _run(9), _run(230)
    row = _display_chunk(
        CHUNK_ID,
        content=lead + "\n" + small + "\n" + big,
        content_display=lead + "\n" + TBL_1 + "\n" + TBL_2,
    )
    out = ls._chunk_section_body(
        row,
        ls._ChunkTableMap(
            {
                CHUNK_ID: tables_by_ref(
                    [_table_row(TBL_1, small), _table_row(TBL_2, big, html=_HTML_2)]
                )
            }
        ),
        gate="gated",
        free_chars=600,
    )

    # The grid that fits: drawn, with its token in place.
    assert set(out["tables"]) == {TBL_1}
    assert TABLE_PLACEHOLDER.findall(out["text"]) == [TBL_1]
    # The grid that does not: prose, cut — no second token, no second grid.
    assert TBL_2 not in out["text"]
    assert big[:40] in out["text"]
    assert out["is_truncated"] is True


def test_a_table_whose_prose_fits_does_not_stop_the_walk() -> None:
    """Case 2 — a grid too heavy to draw whose prose still fits costs nothing.

    The conversion-error shape is exactly this: `md` is 31 chars, the grid holds
    ~900. Charging the grid's weight refuses to draw it; charging `md` lets the
    walk carry straight on — and a LATER grid still renders in the same section,
    which is the whole reason case 2 exists as a separate step.
    """
    err = "[خطأ في التحويل - انتهت المهلة]"
    row = _display_chunk(
        CHUNK_ID,
        content=err + "\n" + _run(4) + "\n" + _run(9),
        content_display=TBL_2 + "\n" + _run(4) + "\n" + TBL_1,
    )
    out = ls._chunk_section_body(
        row,
        ls._ChunkTableMap(
            {
                CHUNK_ID: tables_by_ref(
                    [
                        _table_row(TBL_2, err, html=_wide_grid(rows=60)),
                        _table_row(TBL_1, _run(9)),
                    ]
                )
            }
        ),
        gate="gated",
        free_chars=600,
    )

    assert out["is_truncated"] is False        # the walk reached the end
    assert err in out["text"]                  # case 2: the prose, not the grid
    assert TBL_2 not in out["text"]
    # …and the later grid, which case 2 made reachable, was drawn.
    assert set(out["tables"]) == {TBL_1}


def test_the_blank_guard_is_unreachable() -> None:
    """`_chunk_section_body`'s blank guard is a BACKSTOP; the walk prevents this.

    Swept over every shape that used to blank — a leading oversized grid, two
    grids, a conversion-error grid — at every budget from 1 to 1,200: the walk
    itself always yields text, so the guard never has to fire. If this ever goes
    red the walk has regressed, and the walk is what to fix.
    """
    err = "[خطأ في التحويل - انتهت المهلة]"
    bodies = [
        (TBL_1 + "\n" + _run(100), [_table_row(TBL_1, _run(230))]),
        (TBL_1 + "\n" + TBL_2,
         [_table_row(TBL_1, _run(230)), _table_row(TBL_2, _run(9), html=_HTML_2)]),
        (TBL_2 + "\n" + _run(20),
         [_table_row(TBL_2, err, html=_wide_grid(rows=60))]),
    ]
    for body, rows in bodies:
        segments = split_body(body, tables_by_ref(rows))
        for budget in range(1, 1200):
            cut = ls.truncate_segments_for_gate(segments, "gated", free_chars=budget)
            text, _ = ls.project_segments(cut["visible_segments"])
            if not text.strip():
                # Only legitimate when the budget cannot hold one whole word.
                assert budget < 10, (body[:20], budget)


def test_the_full_reveal_draws_every_grid() -> None:
    """`gate='open'` truncates nothing, so no grid can degrade — case 1 always.

    The reveal is where the grid is the whole point: it is what the unlock buys.
    """
    fake, _ = _oversized_fixture()
    section = ls.get_full_regulation(fake, "nizam-test")["sections"][0]

    assert set(section["tables"]) == {TBL_1}
    assert TABLE_PLACEHOLDER.findall(section["text"]) == [TBL_1]


def test_an_empty_body_stays_empty() -> None:
    """A genuinely empty chunk must not acquire text out of nowhere.

    The dict grew two keys when figures shipped (`images`, `next_index` — plan
    §3.2), and the assertion is still an EXACT equality on purpose: it is the one
    place that pins the whole return shape, so a sixth key cannot be added
    without a reviewer seeing it here. `next_index` comes back as the
    `start_index` it went in with — an empty chunk consumes no numbers.
    """
    out = ls._chunk_section_body(
        {"id": CHUNK_ID, "content": "", "content_display": None},
        ls._ChunkTableMap(),
        gate="gated",
        free_chars=600,
    )
    assert out == {
        "text": "",
        "tables": {},
        "images": {},
        "is_truncated": False,
        "hidden_placeholder_lines": 0,
        "next_index": 1,
    }


def test_a_rendered_grid_is_never_swapped_for_prose() -> None:
    """A grid the budget PAID for is never taken back.

    Once a grid fits and is drawn, no later shortfall may swap it for the
    flattened list this whole feature exists to retire.
    """
    small, big = _run(4), _run(230)
    row = _display_chunk(
        CHUNK_ID,
        content=small + "\n" + big,
        content_display=TBL_1 + "\n" + TBL_2,
    )
    out = ls._chunk_section_body(
        row,
        ls._ChunkTableMap(
            {
                CHUNK_ID: tables_by_ref(
                    [_table_row(TBL_1, small), _table_row(TBL_2, big, html=_HTML_2)]
                )
            }
        ),
        gate="gated",
        free_chars=600,
    )
    assert set(out["tables"]) == {TBL_1}      # the grid the budget paid for
    assert TABLE_PLACEHOLDER.findall(out["text"]) == [TBL_1]
    assert out["is_truncated"] is True


# ===========================================================================
# 9. CHUNK IMAGES — the figure instead of the filename
# `.claude/plans/chunk_image_rendering.md` §3.1 (D10/D11), §3.2, §3.3, §3.5
# · §6 tests 11–16 and 21–25
# ===========================================================================
#
# This one is a LIVE BUG, not a missed improvement. 1,839 chunks carry
#
#     ![img-1.jpeg](images/page_005_img_001.jpeg)
#
# and nothing in this repository has ever looked at it, so the library prints
# that literal string as body text on **168 published أنظمة (1,956 spans)** and
# 52 `seo_articles` rows print it inside a مادة. `public.chunk_images` (5,347
# rows, ingested 2026-08-29) holds the pixels and the words behind each span.
#
# Two rules carry the whole section and they pull in opposite directions from
# their table equivalents:
#
#   * D3 — an UNRESOLVED span emits NOTHING. For tables this was defensive (0 of
#     24,511 tokens were unresolvable); here it FIRES — 656 chunks carry markup
#     with no row at all, 298 of those spans on published pages. Deleting them IS
#     the fix.
#   * §3.2's fail-soft INVERTS. A failed `chunk_tables_v2` read falls back to
#     `content` so the flattened tables survive; a failed `chunk_images` read
#     must leave the spans unresolved so D3 removes them. A failed read may never
#     leave `![img-1.jpeg](images/…)` on the page — that is the bug this feature
#     exists to kill.

from shared.config import get_settings                       # noqa: E402
from shared.library.chunk_images import (                    # noqa: E402
    IMAGE_SPAN,
    IMAGE_TOKEN,
    image_weight,
    images_by_chunk,
)

# The bucket prefix the service builds every URL against (D7). Read from the
# same place `library_service` reads it, so a project restore moves both.
_IMAGE_BASE = get_settings().SUPABASE_URL

# A real span, in the corpus's own shape (REFERENCE.md §3.1). 43 chars — the
# live population is mean 41, p50 44, max 68.
_BASENAME_1 = "page_005_img_001.jpeg"
_BASENAME_2 = "page_012_img_003.png"
_GHOST_BASENAME = "page_099_img_009.jpeg"      # the 656-chunk / 298-span case


def _span(basename: str) -> str:
    """``![img-1.jpeg](images/NAME)`` — the markup, exactly as `content` holds it."""
    return f"![img-1.jpeg](images/{basename})"


SPAN_1 = _span(_BASENAME_1)
SPAN_2 = _span(_BASENAME_2)
GHOST_SPAN = _span(_GHOST_BASENAME)

# 12 chars. Short on purpose — `title` runs 4–77 with a mean of 31, and the
# caption «الصورة {n}: {title}» is the ONLY figure text that reaches the DOM.
_TITLE = "مخطط الترخيص"
_DESC = (
    "مخطط تدفق يوضح مراحل إصدار الترخيص بدءًا من تقديم الطلب عبر البوابة "
    "الإلكترونية ومرورًا بالفحص الفني وانتهاءً بتسليم الرخصة للمنشأة."
)


def _image_row(
    *,
    basename: str = _BASENAME_1,
    n: int = 1,
    chunk_id: str = CHUNK_ID,
    title: str = _TITLE,
    description: str = _DESC,
    transcript: str = "",
    origin: str = "cited",
    uploaded_at: Optional[str] = "2026-08-29T09:14:22.117+00:00",
    reg: str = REG_ID,
    ext: str = "jpeg",
) -> dict[str, Any]:
    """One ``chunk_images`` row, in the shape PostgREST actually returns.

    ``origin``/``n``/``width``/``height`` sit inside ``meta`` because the batched
    read (§3.2) selects ``meta`` whole. ``regulation_id`` is the column the read
    filters on and is never used for resolution — that is
    ``(chunk_id, source_basename)``, and only that (D4).
    """
    ref = f"17393_reg_029_img_{n}"
    return {
        "regulation_id": reg,
        "chunk_id": chunk_id,
        "image_ref": ref,
        "source_basename": basename,
        "title": title,
        "description": description,
        "transcribed_text": transcript,
        "contains_text": bool(transcript),
        # D7 — the URL is built from `storage_path`, never `image_ref + ".jpeg"`.
        "storage_path": f"17393_reg_029/{ref}.{ext}",
        "mime_type": "image/png" if ext == "png" else "image/jpeg",
        "uploaded_at": uploaded_at,
        "meta": {
            "origin": origin,
            "position_source": "exact" if origin == "cited" else "predicted",
            "n": n,
            "width": 1240,
            "height": 880,
        },
    }


def _orphan_row(*, n: int, **kwargs: Any) -> dict[str, Any]:
    """A recovered figure with no markup to ride — 1,670 of the 5,347 rows."""
    return _image_row(basename="", n=n, origin="orphan", **kwargs)


def _image_map(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """``{chunk_id: [ChunkImage]}`` the way the service builds it. No DB."""
    return images_by_chunk(rows, base_url=_IMAGE_BASE)


def _weight_of(row: dict[str, Any]) -> int:
    """D10's charge for one figure — READ from the shared module, never re-derived.

    ``max(span_len, len(caption) + len(transcribed_text))``. Recomputing it here
    would let this file and `chunk_images.image_weight` drift, which is the exact
    failure `_table_charge`'s docstring warns about one module over.
    """
    images = _image_map([row])[str(row["chunk_id"])]
    span_len = len(_span(row["source_basename"])) if row["source_basename"] else 0
    return max(span_len, image_weight(images[0]))


def _reg_with_images(
    chunks: list[dict[str, Any]],
    images: list[dict[str, Any]],
    *,
    tier: str = "gated",
) -> FakeSupabase:
    """A CHUNK-SURFACE نظام (holed index → flip) carrying real `chunk_images` rows."""
    fake = _reg_with_tables(chunks, [], tier=tier)
    fake.tables["chunk_images"] = images
    return fake


def _tokens(text: str) -> list[str]:
    """Every whole-line ``IMG_{n}`` token in a rendered body, in order."""
    return IMAGE_TOKEN.findall(text or "")


def _figure_prose(section: dict[str, Any]) -> str:
    """The section's prose with BOTH token shapes removed.

    A token is not legal text — it is a placement instruction for the client —
    so it comes out before anything is measured, exactly as `_prose_text` does
    for `TBL_…`.
    """
    return IMAGE_TOKEN.sub("", TABLE_PLACEHOLDER.sub("", section["text"]))


def _captions(section: dict[str, Any]) -> str:
    """What a rendered figure actually puts in front of a reader.

    The caption and nothing else: `description` is the `alt`, `transcribed_text`
    is not on this wire at all (D9), and the pixels are not characters.
    """
    return "".join(
        f"الصورة {img['n']}: {img['title']}"
        for img in section["images"].values()
    )


def _figure_visible_chars(section: dict[str, Any]) -> int:
    """Non-whitespace characters a reader gets: prose + every caption."""
    return _nonspace(_figure_prose(section) + _captions(section))


def _all_images(sections: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for section in sections:
        merged.update(section.get("images") or {})
    return merged


def _all_tokens(sections: list[dict[str, Any]]) -> list[str]:
    return [tok for section in sections for tok in _tokens(section.get("text") or "")]


# ---- 9.1 §6.11 THE HEADLINE — the gate never serves more than today --------


def test_the_gate_never_serves_more_than_today() -> None:
    """Property-style over `free_chars` 0..len(body). Three claims, no tolerance.

    A figure is charged ``max(span_len, len(caption) + len(transcribed_text))``
    (D10) and renders only its caption, so it can never buy more exposure than
    the 43 characters of dead markup it replaced. Stated three ways, because one
    of them alone is checkable-but-weak:

      (a) THE SECURITY CLAIM — what a reader actually gets (prose + captions)
          never exceeds what the string cut serves them today, at ANY budget.
          Today's number INCLUDES the raw span, because that is what the page
          literally prints right now: 1,956 of them across 168 published أنظمة.
      (b) THE SPEND CLAIM — the walk never hands out more weighted characters
          than the budget, at any budget. This is the general form of «every
          span's length is still charged».
      (c) THE SPAN CLAIM, in its sharpest form — the same section as it would be
          if that span had never been in the corpus at all always shows MORE
          prose, by the span's own length give or take one word of cut-boundary
          rounding. If a withheld figure rode free, the two would agree.

    ⚠ Measured in non-whitespace characters, for the reason
    `test_the_gate_is_neutral_to_tables` gives: `content` spends two newlines on
    each side of a span where the projection re-emits one, so counting raw bytes
    would turn an exposure assertion into a whitespace assay.

    ⚠ AND «today» IS THE SAME WALK OVER THE SAME BODY WITH THE SPAN LEFT AS
    PLAIN CHARACTERS — not `truncate_for_gate` on the raw string. The two differ
    by up to one word at a budget that lands exactly on a segment boundary,
    because `truncate_segments_for_gate` renders a segment WHOLE when it fits
    while `truncate_for_gate` still cuts at the last whitespace strictly inside
    its window. That is a property of the segment walk, it predates figures, it
    is already asserted for prose by `test_the_walk_never_spends_more_than_its
    _budget`, and comparing against it here would measure it instead of the
    thing this test is about. The control charges the span exactly the
    characters `content` spends on it, which is what the page prints today.
    """
    p, s = _run(10), _run(40)                       # 79 / 319 chars
    transcript = _run(30)                           # 239 chars of photographed law
    body = f"{p}\n\n{SPAN_1}\n\n{s}"
    row = _image_row(transcript=transcript)
    weight = _weight_of(row)

    # The fixture is only interesting if the figure costs MORE than its markup —
    # that is the whole of D10, and the p95 transcription is 755 chars.
    assert weight > len(SPAN_1) and weight == len("الصورة ") + len(": ") + len(
        _TITLE
    ) + len(transcript)

    chunk = _display_chunk(CHUNK_ID, content=body, content_display=None)
    imap = _image_map([row])

    # The sweep must reach both regimes or the claims below are about one of
    # them: the figure has to RENDER at some budget and be WITHHELD at another.
    rendered_at, withheld_at = 0, 0

    # «Today», in the same units and through the same walk: the span left as the
    # 43 plain characters the page currently prints.
    todays_segments = [
        {"kind": "text", "text": p},
        {"kind": "text", "text": "x" * len(SPAN_1)},
        {"kind": "text", "text": s},
    ]

    for budget in range(0, len(body) + 2):
        out = ls._chunk_section_body(
            chunk, ls._ChunkTableMap(), gate="gated", free_chars=budget, images=imap
        )
        today, _t, _i = ls._project(
            ls.truncate_segments_for_gate(
                todays_segments, "gated", free_chars=budget
            )["visible_segments"]
        )

        rendered_at += bool(out["images"])
        withheld_at += bool(out["is_truncated"] and not out["images"])

        # (a) the security claim
        assert _figure_visible_chars(out) <= _nonspace(today), budget
        # …and its absolute form, which needs no comparand at all: a reader
        # never gets more law than the budget bought, because prose is charged
        # 1:1 and a figure is charged at least what it renders.
        assert _figure_visible_chars(out) <= budget, budget
        # …and no path may print the markup itself, ever (§6.12's rule, asserted
        # here too because this sweep is the widest one in the file).
        assert "](images/" not in out["text"], budget

    assert rendered_at and withheld_at, (rendered_at, withheld_at)

    # (b) the spend claim, over the segments themselves — the same shape
    # `test_the_walk_never_spends_more_than_its_budget` uses for grids.
    segments, _next = ls.place_images(split_body(body, {}), imap[CHUNK_ID])
    assert [seg["kind"] for seg in segments] == ["text", "image", "text"]
    for budget in range(0, len(body) + weight + 2):
        cut = ls.truncate_segments_for_gate(segments, "gated", free_chars=budget)
        spend = sum(
            seg["weight"] if seg["kind"] == "image" else len(seg["text"])
            for seg in cut["visible_segments"]
        )
        assert spend <= budget, (budget, spend)

    # (c) THE SPAN CLAIM. `zero` is the identical section as it would be if that
    # span had never been written into the corpus — same prose, same segments,
    # same walk, one charge missing. The span's own characters are therefore the
    # ONLY difference between the two, which is what makes this exact and
    # boundary-proof: a smaller window over the same trailing text can never
    # print more of it.
    zero = [seg for seg in segments if seg["kind"] != "image"]
    gaps = []
    for budget in range(0, len(body) + weight + 2):
        ours, _t, _i = ls._project(
            ls.truncate_segments_for_gate(segments, "gated", free_chars=budget)[
                "visible_segments"
            ]
        )
        none_, _t, _i = ls._project(
            ls.truncate_segments_for_gate(zero, "gated", free_chars=budget)[
                "visible_segments"
            ]
        )
        ours_prose = IMAGE_TOKEN.sub("", ours)
        # In non-whitespace characters, for the reason above: a rendered figure
        # puts a token on its own line, so the two projections spend a newline
        # differently and nothing else.
        assert _nonspace(ours_prose) <= _nonspace(none_), budget
        withheld = not IMAGE_TOKEN.findall(ours)
        if withheld and len(p) < len(ours_prose) < len(p) + len(s):
            gaps.append(len(none_) - len(ours_prose))

    # …and the SIZE of that difference is the span itself, not a rounding
    # accident. Measured over every budget at which the figure was WITHHELD and
    # both walks were cutting inside the trailing prose: ±8 is one whole `_W7`
    # word, because both sides cut at the last whitespace inside their own
    # window and the two windows differ by exactly the span plus the separator
    # the walk charges for it.
    assert gaps, "the sweep never withheld the figure mid-prose"
    assert min(gaps) >= len(SPAN_1) - 8, min(gaps)
    assert max(gaps) <= len(SPAN_1) + 8, max(gaps)


def test_a_figure_is_charged_more_than_the_markup_it_replaced() -> None:
    """D10 in one line, at the seam the gate reads it from.

    The whole argument for weighing a figure is that it can carry a full
    specification table in pixels: `transcribed_text` runs p50 31, p95 755, max
    4,854 — a fines schedule photographed whole. Charging the 43-char span alone
    would let an anonymous crawler collect one against a 600-char budget.
    """
    transcript = _run(80)                            # 639 chars
    row = _image_row(transcript=transcript)
    [image] = _image_map([row])[CHUNK_ID]
    segments, _ = ls.place_images(
        split_body(f"{_run(4)}\n\n{SPAN_1}", {}), [image]
    )
    figure = segments[-1]

    assert figure["kind"] == "image"
    assert figure["span_len"] == len(SPAN_1) == 43
    assert figure["weight"] == _weight_of(row) > figure["span_len"]
    # The gate READS the weight off the segment — it never recomputes it.
    assert ls._image_charge(figure) == figure["weight"]
    assert ls._image_span_len(figure) == len(SPAN_1)


# ---- 9.2 §6.12 No raw markup, at any cut position -------------------------


def test_no_raw_markup_survives_truncation() -> None:
    """`](images/` never appears in `visible_text`, at ANY `free_chars`.

    The mid-span-cut analogue of the tables plan's 191-chunk bug, plus D3's
    live population in the same fixture: one span that RESOLVES and one that
    never will (656 chunks carry markup with no row; 298 of those spans sit on
    published pages, because the vision pass judged those figures decorative or
    they sit in regulation front matter).

    The fixture is not vacuous: a raw character slice of this body ships the
    markup at plenty of budgets, and so does the whitespace-aware string cut —
    a span contains no whitespace at all, so `truncate_for_gate` can only serve
    it whole or not at all, and «whole» is exactly today's bug.
    """
    body = f"{_run(4)}\n\n{SPAN_1}\n\n{_run(6)}\n\n{GHOST_SPAN}\n\n{_run(5)}"
    chunk = _display_chunk(CHUNK_ID, content=body, content_display=None)
    imap = _image_map([_image_row(transcript=_run(5))])

    # (a) the string cut really does ship the markup today …
    assert any(
        "](images/" in ls.truncate_for_gate(body, "gated", free_chars=b)["visible_text"]
        for b in range(len(body) + 1)
    ), "fixture does not reproduce the raw-markup preview"

    for budget in range(0, len(body) + 2):
        out = ls._chunk_section_body(
            chunk, ls._ChunkTableMap(), gate="gated", free_chars=budget, images=imap
        )
        # (b) … and no budget lets any of it through here.
        assert "](images/" not in out["text"], budget
        assert "![" not in out["text"], budget
        assert _GHOST_BASENAME not in out["text"], budget
        # Every token that survived resolves — a token with no entry renders raw.
        assert set(_tokens(out["text"])) == set(out["images"]), budget

    # The ghost span is never resolvable, at any budget, so its figure exists on
    # no surface at all — that IS the fix, not a fallback (D3).
    everything = ls._chunk_section_body(
        chunk, ls._ChunkTableMap(), gate="open", free_chars=0, images=imap
    )
    assert list(everything["images"]) == ["IMG_1"]
    assert _tokens(everything["text"]) == ["IMG_1"]


# ---- 9.3 §6.13/§6.14 D11 — the figure degrades, the section never does -----


def test_a_withheld_figure_marks_truncated() -> None:
    """A figure skipped for budget sets `is_truncated` even when no prose was cut.

    Otherwise the page claims it showed everything while hiding a diagram — and
    on a لائحة فنية the diagram IS the operative content. The prose is untouched
    by the skip, which is the other half of D11: a section may never blank on
    account of a figure.
    """
    p = _run(6)                                       # 47 chars
    body = f"{p}\n\n{SPAN_1}"
    row = _image_row(transcript=_run(60))             # weight ≫ any sane budget
    out = ls._chunk_section_body(
        _display_chunk(CHUNK_ID, content=body, content_display=None),
        ls._ChunkTableMap(),
        gate="gated",
        # Room for every character of prose and for the span's own 43 — and
        # nowhere near the figure's weight.
        free_chars=len(p) + len(SPAN_1) + 10,
        images=_image_map([row]),
    )

    assert out["text"] == p, out["text"]              # not one word was cut …
    assert out["images"] == {}                        # … and the figure is gone
    assert out["is_truncated"] is True                # …and the page says so.
    assert out["hidden_placeholder_lines"] > 0


def test_the_first_skip_closes_the_channel() -> None:
    """D11's monotone rule: figures 1 and 2 render, 3 does not fit, 4 is skipped too.

    Not an optimisation — it is what keeps «الصورة {N}» honest. Letting a later
    small figure slip through after a big one was withheld prints «الصورة 1، 2،
    4» around a hole the reader cannot see and cannot account for. Numbering
    stays 1, 2.
    """
    small = "و"                                        # a 1-char caption tail
    rows = [
        _image_row(basename=f"page_00{i}_img_001.jpeg", n=i, title=small)
        for i in (1, 2, 4)
    ]
    # Figure 3 is the wall: 1,000 chars of photographed schedule.
    rows.insert(2, _image_row(basename="page_003_img_001.jpeg", n=3,
                              transcript=_run(125)))
    body = "\n\n".join(
        [
            _run(2),
            _span("page_001_img_001.jpeg"),
            _span("page_002_img_001.jpeg"),
            _span("page_003_img_001.jpeg"),
            _span("page_004_img_001.jpeg"),
        ]
    )
    imap = _image_map(rows)

    # A budget that clears the lead, both small figures, the SPAN the withheld
    # third one is still charged, and — critically — the fourth figure too. Only
    # the third does not fit. Anything tighter and this test passes for the
    # wrong reason: figure 4 would have been refused on budget anyway.
    cheap = _weight_of(rows[0])
    budget = len(_run(2)) + 4 * (cheap + 2) + 10
    assert cheap == len(SPAN_1)                     # no transcription to charge
    assert budget < _weight_of(rows[2])             # …and the wall is unpayable

    out = ls._chunk_section_body(
        _display_chunk(CHUNK_ID, content=body, content_display=None),
        ls._ChunkTableMap(),
        gate="gated",
        free_chars=budget,
        images=imap,
    )

    assert _tokens(out["text"]) == ["IMG_1", "IMG_2"]
    assert set(out["images"]) == {"IMG_1", "IMG_2"}
    assert [img["n"] for img in out["images"].values()] == [1, 2]
    assert out["is_truncated"] is True

    # THE CHANNEL IS WHAT CLOSED, NOT THE BUDGET — and this is the half that
    # makes the test mean something. Take the wall away and, at the SAME budget
    # over the SAME body, the fourth figure renders: it always fitted.
    without_the_wall = ls._chunk_section_body(
        _display_chunk(CHUNK_ID, content=body, content_display=None),
        ls._ChunkTableMap(),
        gate="gated",
        free_chars=budget,
        images=_image_map([rows[0], rows[1], rows[3]]),
    )
    assert _tokens(without_the_wall["text"]) == ["IMG_1", "IMG_2", "IMG_3"]
    # …and it is numbered 3 there, because the withheld wall never existed.
    # With the wall present the sequence is 1, 2 and STOPS — never 1, 2, 4.
    assert "IMG_4" not in out["text"] and "IMG_3" not in out["text"]


def test_a_figure_never_blanks_its_section() -> None:
    """The failure the tables plan spent a revision fixing, pre-empted here.

    A figure has no prose it replaced to degrade into — the space it occupied was
    43 characters of dead markup — so «degrade the figure» means degrade it to
    nothing and let the prose keep filling the budget. Swept over every budget
    that can hold a word.
    """
    body = f"{SPAN_1}\n\n{_run(80)}"                   # the figure comes FIRST
    chunk = _display_chunk(CHUNK_ID, content=body, content_display=None)
    imap = _image_map([_image_row(transcript=_run(200))])

    for budget in range(20, 640):
        out = ls._chunk_section_body(
            chunk, ls._ChunkTableMap(), gate="gated", free_chars=budget, images=imap
        )
        assert out["text"].strip(), budget
        assert out["images"] == {}, budget            # the figure never fits …
        # … and the prose still fills the budget it was given.
        assert len(out["text"]) > budget - len(SPAN_1) - 12, budget


def test_the_plain_string_path_deletes_the_markup_it_cannot_render() -> None:
    """`_strip_image_spans` — D3 for the paths that have nowhere to put a figure.

    `_chunk_section_body`'s blank guard renders a PLAIN STRING, and a plain
    string cannot carry an `IMG_{n}` token with a map beside it. So the spans are
    DELETED rather than printed, and the whitespace they orphaned goes with them:
    a span alone on its line takes its line, an inline one collapses to a single
    space, and neither leaves a doubled paragraph break where the figure stood.

    The guard itself is a backstop that `test_the_blank_guard_is_unreachable`
    shows never fires — but «the markup must never reach a reader» is absolute,
    so the branch that could print it is the one branch that may not be left
    untested.
    """
    assert ls._strip_image_spans("") == ""
    # Nothing to do ⇒ byte-identical, no allocation, the 96.7% case.
    plain = "المادة الأولى: نص بلا صور."
    assert ls._strip_image_spans(plain) is plain

    whole_line = f"قبل\n\n{SPAN_1}\n\nبعد"
    assert ls._strip_image_spans(whole_line) == "قبل\n\nبعد"

    # The 47-span case: the sentence survives, with ONE space where the span was.
    inline = f"يُرخَّص {SPAN_1} وفق المخطط."
    assert ls._strip_image_spans(inline) == "يُرخَّص وفق المخطط."

    for shape in (whole_line, inline, SPAN_1, f"{SPAN_1}{GHOST_SPAN}"):
        assert "](images/" not in ls._strip_image_spans(shape)


# ---- 9.4 §6.15 Fail-soft, and it INVERTS ----------------------------------


def test_an_images_read_failure_degrades_to_prose() -> None:
    """§3.2's inversion, and the one somebody will "fix" to match the neighbour.

    A failed `chunk_tables_v2` read falls back to `content` so the flattened
    tables survive. A failed `chunk_images` read must do the OPPOSITE — leave the
    spans unresolved so D3 removes them — because the only other option is to
    leave `![img-1.jpeg](images/…)` on the page, which is precisely today's bug.
    An image read failure therefore degrades to *the prose without its figures*,
    which is strictly better than what ships now.
    """
    lead, tail = _run(4), _run(5)
    body = f"{lead}\n\n{SPAN_1}\n\n{tail}"
    fake = _reg_with_images(
        [_display_chunk(CHUNK_ID, content=body, content_display=None)],
        [_image_row()],
        tier="open",
    )
    fake.fail_tables = {"chunk_images"}

    section = ls.get_regulation_doc(fake, "nizam-test")["visible_sections"][0]

    # The statute survives, whole …
    assert lead in section["text"] and tail in section["text"]
    # … the figure does not …
    assert section["images"] == {}
    assert _tokens(section["text"]) == []
    # … and NOT ONE CHARACTER of the markup reaches the reader.
    assert "](images/" not in section["text"]
    assert "img-1.jpeg" not in section["text"]
    # The hole closes cleanly: no blank-line artifact where the span stood.
    assert "\n\n\n" not in section["text"]


def test_an_empty_images_read_is_not_a_failure() -> None:
    """The other half: 96.7% of chunks carry no figure and must pay nothing.

    46,831 of 48,429. Treating «no rows» as «read failed» would be invisible
    here — both render the same prose — which is exactly why the fail-soft
    direction had to be chosen so that the two ARE the same.
    """
    body = f"{_run(4)}\n\n{_run(6)}"
    fake = _reg_with_images(
        [_display_chunk(CHUNK_ID, content=body, content_display=None)], [], tier="open"
    )
    section = ls.get_regulation_doc(fake, "nizam-test")["visible_sections"][0]
    assert section["text"] == body
    assert section["images"] == {}


# ---- 9.5 §6.16 The counter's precondition ---------------------------------


def test_sections_are_built_in_reading_order() -> None:
    """«الصورة {N}» is render order, so the render must BE the reading order.

    A counter threaded through an unordered loop numbers figures in whatever
    order the rows arrived — and `position` is scoped PER STREAM, so a naive
    sort interleaves a نظام's ملاحق with its body (7.5 above). The numbering is
    therefore an independent check on the same ordering rule: body chunk 1, body
    chunk 2, then the ملحق, and the figures numbered 1, 2, 3 in that order.
    """
    chunks = [
        _display_chunk(
            "cccccccc-0000-0000-0000-000000000001",
            content=f"{_run(3)}\n\n{_span('page_001_img_001.jpeg')}",
            content_display=None,
            corpus="without_articles",
            position=1,
        ),
        _display_chunk(
            "cccccccc-0000-0000-0000-000000000002",
            content=f"{_run(3)}\n\n{_span('page_002_img_001.jpeg')}",
            content_display=None,
            corpus="without_articles",
            position=2,
        ),
        _display_chunk(
            "dddddddd-0000-0000-0000-000000000001",
            content=f"{_run(3)}\n\n{_span('page_003_img_001.jpeg')}",
            content_display=None,
            title="ملحق (1)",
            corpus="appendix",
            position=1,
        ),
    ]
    rows = [
        _image_row(basename="page_001_img_001.jpeg", n=1, chunk_id=chunks[0]["id"]),
        _image_row(basename="page_002_img_001.jpeg", n=2, chunk_id=chunks[1]["id"]),
        _image_row(basename="page_003_img_001.jpeg", n=3, chunk_id=chunks[2]["id"]),
    ]
    doc = ls.get_regulation_doc(_reg_with_images(chunks, rows, tier="open"), "nizam-test")
    sections = doc["visible_sections"]

    # The document renders body-before-appendix …
    assert [s["id"] for s in sections] == [c["id"] for c in chunks]
    # … and the counter follows it: one figure each, numbered 1, 2, 3.
    assert [_tokens(s["text"]) for s in sections] == [["IMG_1"], ["IMG_2"], ["IMG_3"]]
    assert [list(s["images"]) for s in sections] == [["IMG_1"], ["IMG_2"], ["IMG_3"]]
    numbers = [img["n"] for s in sections for img in s["images"].values()]
    assert numbers == sorted(numbers) == [1, 2, 3]
    # Never `meta->>'n'` and never `n_in_chunk` — the corpus numbers are 1,2,3
    # here by construction, so the real proof is 9.9's gap fixture.


def test_a_chunk_fallback_article_threads_the_counter() -> None:
    """The other branch of `_article_sections`, and it consumes numbers too.

    A مادة whose extraction failed renders its whole owning CHUNK through
    `_chunk_section_body` (plan §3.4: 2 chunks, 2 figures — free, because that
    path already existed). If it does not hand `next_index` on, the مادة after
    it renumbers over the top of what the fallback just printed.
    """
    fallback_chunk = "bbbbbbbb-0000-0000-0000-000000000001"
    articles = [
        {
            "regulation_id": REG_ID, "article_no": 1, "article_label": "المادة 1",
            "slug": "m-1", "chunk_id": fallback_chunk, "article_text": None,
            "extraction_status": "pending",
        },
        _extracted_article(2, f"المادة 2\n\n{_span('page_009_img_001.jpeg')}"),
    ]
    fake = _article_surface_fake(
        articles,
        [
            _image_row(basename="page_001_img_001.jpeg", n=1, chunk_id=fallback_chunk),
            _image_row(basename="page_002_img_001.jpeg", n=2, chunk_id=fallback_chunk),
            _image_row(basename="page_009_img_001.jpeg", n=3, chunk_id=ART_CHUNK),
        ],
        chunks=[
            {
                "id": fallback_chunk, "regulation_id": REG_ID, "title": "الفصل الأول",
                "position": 1, "corpus": "with_articles",
                "chunk_ref": "17393_reg_029_chunk_001",
                "content": (
                    f"نص المقطع\n\n{_span('page_001_img_001.jpeg')}"
                    f"\n\n{_span('page_002_img_001.jpeg')}"
                ),
                "content_display": None, "has_images": True,
            },
            {
                "id": ART_CHUNK, "regulation_id": REG_ID, "title": "الفصل الثاني",
                "position": 2, "corpus": "with_articles",
                "chunk_ref": "17393_reg_029_chunk_002",
                "content": "نص", "content_display": None, "has_images": True,
            },
        ],
    )
    by_id = {s["id"]: s for s in ls.get_regulation_doc(fake, "nizam-test")["visible_sections"]}

    assert _tokens(by_id["art-1"]["text"]) == ["IMG_1", "IMG_2"]
    # …and the extracted مادة that follows continues from 3 — never back to 1.
    assert _tokens(by_id["art-2"]["text"]) == ["IMG_3"]
    assert by_id["art-2"]["images"]["IMG_3"]["n"] == 3


def test_the_number_is_never_the_corpus_number() -> None:
    """D8, at the service boundary: «الصورة 402» must be impossible.

    `meta->>'n'` is the index within the REGULATION and **120 of 418 regulations
    have gaps in it** — the 9,310 decorative figures were counted and then not
    ingested. Worst case a نظام whose figures are numbered 1, 47, …, 414 for 31
    actual images, a gap of **383**: a reader would see «الصورة 402» and conclude
    401 figures were missing.
    """
    body = f"{_run(3)}\n\n{_span('page_001_img_001.jpeg')}\n\n" \
           f"{_span('page_002_img_001.jpeg')}"
    rows = [
        _image_row(basename="page_001_img_001.jpeg", n=47),
        _image_row(basename="page_002_img_001.jpeg", n=414),
    ]
    section = ls.get_regulation_doc(
        _reg_with_images(
            [_display_chunk(CHUNK_ID, content=body, content_display=None)],
            rows,
            tier="open",
        ),
        "nizam-test",
    )["visible_sections"][0]

    assert _tokens(section["text"]) == ["IMG_1", "IMG_2"]
    assert [img["n"] for img in section["images"].values()] == [1, 2]
    assert "IMG_47" not in section["text"] and "IMG_414" not in section["text"]
    # The caption prints the render number, so the token and the label agree.
    assert all(f"IMG_{img['n']}" in section["images"] for img in section["images"].values())


# ---- 9.6 The wire ----------------------------------------------------------


def test_the_response_model_declares_images() -> None:
    """`response_model` strips undeclared keys — the `tables`/`kind` trap, again.

    A service that emits `images` and a model that does not list it produces a
    payload whose `text` still carries `IMG_{n}` token lines and whose figures
    are GONE. The client strips a token it cannot resolve, so the page renders
    prose with the diagram missing, silently, on all FOUR payloads (§3.5.1).
    """
    from backend.app.api.public_library import (
        LibraryFullResponse,
        LibraryFullSection,
        RegulationArticleResponse,
        VisibleSection,
    )

    for model in (
        VisibleSection,
        LibraryFullSection,
        RegulationArticleResponse,
        LibraryFullResponse,
    ):
        assert "images" in model.model_fields, model.__name__

    # Absent ⇒ `{}`, never None: one shape on the wire.
    assert VisibleSection(id="s", text="", is_truncated=False,
                          hidden_placeholder_lines=0).images == {}
    assert LibraryFullSection(id="s", text="").images == {}

    payload = {
        "image_ref": "17393_reg_029_img_5", "n": 3, "title": _TITLE,
        "description": _DESC, "url": f"{_IMAGE_BASE}/x.png",
        "width": 1240, "height": 880,
    }
    section = VisibleSection(
        id="s", text="نص\nIMG_3", is_truncated=False, hidden_placeholder_lines=0,
        images={"IMG_3": payload},
    )
    # The seven fields the frontend's `RegulationImage` declares — no more (a
    # `transcribed_text` on this wire is a gate decision nobody has taken, D9)
    # and no fewer (`width`/`height` reserve the box before the bytes land).
    assert section.model_dump()["images"]["IMG_3"] == payload


def test_the_token_set_and_the_image_map_agree() -> None:
    """§3.3's invariant, both directions, at every budget.

    A token with no entry renders RAW on a statute page. An entry with no token
    renders NOWHERE and is dead weight baked into an ISR payload for 24h.
    «tokens ⊆ images» alone would let the second through.
    """
    body = "\n\n".join(
        [_run(4), _span("page_001_img_001.jpeg"), _run(7),
         _span("page_002_img_001.jpeg"), _run(5)]
    )
    imap = _image_map(
        [
            _image_row(basename="page_001_img_001.jpeg", n=1, transcript=_run(3)),
            _image_row(basename="page_002_img_001.jpeg", n=2),
        ]
    )
    segments, _ = ls.place_images(split_body(body, {}), imap[CHUNK_ID])

    for gate, budget in [("open", 600), ("gated", 0), ("gated", 40),
                         ("gated", 200), ("gated", 10_000)]:
        cut = ls.truncate_segments_for_gate(segments, gate, free_chars=budget)
        text, tables, images = ls._project(cut["visible_segments"])
        assert set(IMAGE_TOKEN.findall(text)) == set(images), (gate, budget)
        assert tables == {}
        for token, payload in images.items():
            assert token == f"IMG_{payload['n']}"
            assert payload["url"].startswith(_IMAGE_BASE)
            assert set(payload) == {
                "image_ref", "n", "title", "description", "url", "width", "height"
            }
    # `project_segments` keeps its two-value shape for every caller that has no
    # figures to ship, and `project_images` is the same walk.
    text, tables = ls.project_segments(segments)
    assert set(IMAGE_TOKEN.findall(text)) == set(ls.project_images(segments))


def test_a_figure_with_no_url_is_dropped_whole() -> None:
    """D3 at the projection: half a figure is worse than none of it.

    `images_by_chunk` already refuses to build a `ChunkImage` without
    `storage_path` or `uploaded_at` — a URL for absent bytes is a 404 inside a
    statute — so this branch is unreachable through `place_images` today. It is
    code and not a comment for the same reason `project_segments` drops a table
    missing its `html`: a segment that reached the projection half-built would
    otherwise put an `IMG_{n}` line in `text` with nothing to resolve it, which
    renders raw on a statute page.
    """
    hand_built = [
        {"kind": "text", "text": "قبل"},
        {"kind": "image", "image_ref": "r", "n": 1, "title": "ت", "description": "",
         "url": "", "width": 0, "height": 0, "weight": 43, "span_len": 43},
        {"kind": "image", "image_ref": "r2", "n": 0, "title": "ت", "description": "",
         "url": f"{_IMAGE_BASE}/x.jpeg", "width": 1, "height": 1,
         "weight": 43, "span_len": 43},
        {"kind": "text", "text": "بعد"},
    ]
    text, tables, images = ls._project(hand_built)

    assert images == {} and tables == {}
    assert _tokens(text) == []
    assert "IMG_" not in text
    assert text == "قبل\nبعد"


def test_a_figure_cited_twice_is_numbered_once() -> None:
    """26 basenames appear more than once in one chunk — the same figure, twice.

    It must not become «الصورة 3» and «الصورة 7». Both occurrences render, both
    carry the same token, and the map holds ONE entry.
    """
    body = f"{_run(3)}\n\n{SPAN_1}\n\n{_run(3)}\n\n{SPAN_1}"
    out = ls._chunk_section_body(
        _display_chunk(CHUNK_ID, content=body, content_display=None),
        ls._ChunkTableMap(),
        gate="open",
        free_chars=0,
        images=_image_map([_image_row()]),
    )
    assert _tokens(out["text"]) == ["IMG_1", "IMG_1"]
    assert list(out["images"]) == ["IMG_1"]
    assert out["next_index"] == 2


# ---- 9.7 §3.5 THE ARTICLE SURFACE (§6.21–§6.25) ---------------------------
#
# The tables plan stopped at the article surface. This one crosses it, and it
# turned out to be cheap: `article_text` is cut out of `content` **and the span
# is cut out with it** — which is precisely WHY 52 `seo_articles` rows print
# filenames today — so `(chunk_id, source_basename)` resolves it with the
# machinery §2 already has. 217 spans on 10 أنظمة, of which 11 spans on 8 مادة
# pages across 7 published أنظمة are reachable by a reader.


ART_CHUNK = "aaaaaaaa-1111-0000-0000-000000000001"


def _extracted_article(
    no: int, text: str, *, chunk_id: str = ART_CHUNK
) -> dict[str, Any]:
    return {
        "regulation_id": REG_ID,
        "article_no": no,
        "article_label": f"المادة {no}",
        "slug": f"m-{no}",
        "chunk_id": chunk_id,
        "article_text": text,
        "extraction_status": "extracted",
    }


def _article_surface_fake(
    articles: list[dict[str, Any]],
    images: list[dict[str, Any]],
    *,
    tier: str = "open",
    chunks: Optional[list[dict[str, Any]]] = None,
) -> FakeSupabase:
    """An ARTICLE-surface نظام whose مواد carry spans, plus the article sidecars.

    Complete enough for BOTH `get_regulation_doc` and `get_regulation_article`:
    the مادة page is opt-in and 404s without a published `seo_item_meta` row
    (`content_type='article'`, `content_id='{reg}#{no}'`, slug set).
    """
    fake = _reg_doc_fake(articles, n_chunks=0)
    fake.tables["seo_item_meta"][0]["seo_tier"] = tier
    fake.tables["seo_item_meta"].extend(
        {
            "content_type": "article",
            "content_id": f"{REG_ID}#{int(a['article_no'])}",
            "slug": a["slug"],
            "seo_tier": None,
            "gate_override": None,
        }
        for a in articles
    )
    fake.tables["chunks_v2"] = chunks if chunks is not None else [
        {
            "id": ART_CHUNK,
            "regulation_id": REG_ID,
            "title": "الفصل الأول",
            "position": 1,
            "corpus": "with_articles",
            "chunk_ref": "17393_reg_029_chunk_001",
            "content": "نص المقطع كاملاً",
            "content_display": None,
            # D12's cost bound — the مادة page reads figures ONLY when this is
            # set, on a boolean that is already on the row it reads for
            # `context_title`.
            "has_images": True,
        }
    ]
    fake.tables["chunk_images"] = images
    fake.tables["seo_sharh"] = []
    return fake


def test_an_extracted_article_resolves_its_own_span() -> None:
    """§6.21 — the 11-published-span case, on both surfaces that render a مادة.

    The row above was written as a hard bucket («the article surface is out of
    scope») and re-measuring said otherwise: 131 of the 217 spans inside
    `article_text` resolve against their own chunk's rows, keyed by
    `seo_articles.chunk_id`. There is no «span matching inside the slice» to
    invent — it is the same `(chunk_id, source_basename)` lookup.
    """
    text = f"المادة الأولى: يُرخَّص وفق المخطط الآتي.\n\n{SPAN_1}\n\nويُعمل به من تاريخه."
    fake = _article_surface_fake([_extracted_article(1, text)], [_image_row(chunk_id=ART_CHUNK)])

    # (a) the DOC page
    section = ls.get_regulation_doc(fake, "nizam-test")["visible_sections"][0]
    assert section["id"] == "art-1"
    assert _tokens(section["text"]) == ["IMG_1"]
    assert set(section["images"]) == {"IMG_1"}
    assert section["images"]["IMG_1"]["title"] == _TITLE
    assert section["images"]["IMG_1"]["url"].endswith(".jpeg")
    assert "](images/" not in section["text"]
    # …and the prose around it is intact, in order. (The «المادة الأولى:»
    # heading line is stripped by `_clean_article_display_text` before any of
    # this — it duplicates the section title — so the body opens on the sentence
    # that followed it.)
    assert section["text"].startswith("يُرخَّص وفق المخطط")
    assert section["text"].endswith("ويُعمل به من تاريخه.")
    # §3.4's untouched limit: this plan reaches no table inside `article_text`.
    assert section["tables"] == {}

    # (b) the مادة PAGE, which reads its own figures for that ONE chunk
    art = ls.get_regulation_article(fake, "nizam-test", "m-1")
    assert art is not None
    assert _tokens(art["text"]) == ["IMG_1"]
    assert set(art["images"]) == {"IMG_1"}
    assert art["images"]["IMG_1"]["image_ref"] == "17393_reg_029_img_1"
    assert "](images/" not in art["text"]

    # (c) the REVEAL of that one مادة — §3.5.1's fourth payload.
    full = ls.get_full_article(fake, "nizam-test", "m-1")
    assert _tokens(full["text"]) == ["IMG_1"]
    assert set(full["images"]) == {"IMG_1"}


def test_a_figureless_article_is_byte_for_byte_what_ships_today() -> None:
    """D18 moved ~50k مواد onto a different code path. It must not move ONE of them.

    The extracted branch used to cut a plain string with `truncate_for_gate`; it
    now walks `split_body` → `place_images` → `truncate_segments_for_gate` so a
    figure cannot ride the gate free. Every مادة that carries no span — 49,000-odd
    of them — has to come out the other side unchanged: same text, same
    `is_truncated`, same placeholder count, at every budget the surface uses.

    Verified against live data on 2026-08-30 before this was written: 1,000 real
    `article_text` rows × 3 (gate, budget) combinations, **3,000 identical, 0
    differing**. This is that check in a form CI can run.

    ⚠ ONE difference exists and it is named rather than hidden: `split_body`
    trims BLANK LINES at the two ends of the body — its own long-standing rule,
    which every chunk-shaped body has always been through — so a مادة that opens
    or closes on an empty line loses it. Not one of the 1,000 live rows does
    (`_clean_article_display_text` already collapses the leading whitespace it
    leaves behind), the trimmed characters are whitespace and never law, and
    `toLegalBlocks` renders a leading blank line as nothing either way. The
    fixture below carries that shape ON PURPOSE, and the comparison normalises
    exactly it and nothing else.
    """

    def blank_edges_trimmed(text: str) -> str:
        lines = text.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    bodies = [
        "المادة الأولى: يُقصد بالألفاظ الآتية المعاني المبيّنة أمامها.",
        "المادة الثانية: " + _run(120),
        "\n\n" + _run(30) + "\n\n\n" + _run(90) + "\n\n",
        _run(4),
        "بند\n- أولاً: " + _run(60) + "\n- ثانياً: " + _run(60),
        "",
    ]
    for body in bodies:
        cleaned = ls._clean_article_display_text(body)
        for gate, budget in (
            ("gated", 600),
            ("gated", ls.ARTICLE_FREE_CHARS),
            ("gated", 0),
            ("open", 0),
        ):
            old = ls.truncate_for_gate(cleaned, gate, free_chars=budget)
            new = ls._article_body_cut(cleaned, [], gate=gate, free_chars=budget)
            where = (repr(body[:24]), gate, budget)
            assert new["text"] == blank_edges_trimmed(old["visible_text"]), where
            assert new["is_truncated"] == old["is_truncated"], where
            assert (
                new["hidden_placeholder_lines"] == old["hidden_placeholder_lines"]
            ), where
            assert new["images"] == {}, where
            # No figures placed ⇒ no numbers consumed, so a figure-free مادة
            # cannot push the document's counter along.
            assert new["next_index"] == 1, where


def test_an_article_never_shows_an_orphan() -> None:
    """§6.22 / D16 — an orphan's position is a claim a مادة cannot make.

    1,670 figures have no markup at all: they were recovered from the source PDF
    and placed by line provenance against the WHOLE CHUNK. A مادة is a fragment
    of that chunk, so appending a figure to it because it was predicted
    *somewhere in the chunk that contains it* is a placement claim the data does
    not support. 26 published orphan figures on 25 extracted-article chunks stay
    invisible — exactly as they are today — and that cost is on the record.
    """
    text = f"المادة الأولى: يُرخَّص وفق المخطط.\n\n{SPAN_1}"
    fake = _article_surface_fake(
        [_extracted_article(1, text)],
        [
            _image_row(chunk_id=ART_CHUNK, n=1),
            _orphan_row(n=2, chunk_id=ART_CHUNK, title="صورة مرفقة"),
            _orphan_row(n=3, chunk_id=ART_CHUNK, title="صورة أخرى"),
        ],
    )

    section = ls.get_regulation_doc(fake, "nizam-test")["visible_sections"][0]
    art = ls.get_regulation_article(fake, "nizam-test", "m-1")

    for surface in (section, art):
        # The cited figure rides its own markup …
        assert set(surface["images"]) == {"IMG_1"}
        # … and neither orphan appears, on either surface.
        assert "صورة مرفقة" not in str(surface["images"])
        assert _tokens(surface["text"]) == ["IMG_1"]

    # A CHUNK-shaped body is not a fragment, so it keeps its orphans — the same
    # rows, rendered through `_chunk_section_body`.
    whole_chunk = ls._chunk_section_body(
        {"id": ART_CHUNK, "content": text, "content_display": None},
        ls._ChunkTableMap(),
        gate="open",
        free_chars=0,
        images=_image_map(
            [
                _image_row(chunk_id=ART_CHUNK, n=1),
                _orphan_row(n=2, chunk_id=ART_CHUNK, title="صورة مرفقة"),
            ]
        ),
    )
    assert set(whole_chunk["images"]) == {"IMG_1", "IMG_2"}


def test_the_article_gate_charges_the_figure() -> None:
    """§6.23 / D18 — the extracted branch walks segments, so nothing rides free.

    This one FAILS TODAY BY CONSTRUCTION: the branch used to cut a plain string
    with `truncate_for_gate`, which charges the 43-char span and hands over
    whatever the diagram carries in pixels. D10's whole argument — a photographed
    penalty schedule against a 500-char budget — applies to a مادة unchanged.
    """
    lead = _run(10)                                    # 79 chars
    tail = _run(90)                                    # 719 chars
    text = f"{lead}\n\n{SPAN_1}\n\n{tail}"
    row = _image_row(transcript=_run(30))              # weight 260
    weight = _weight_of(row)
    assert weight > ls.ARTICLE_FREE_CHARS // 2

    fake = _article_surface_fake(
        [_extracted_article(1, text)], [_image_row(chunk_id=ART_CHUNK, transcript=_run(30))],
        tier="gated",
    )
    art = ls.get_regulation_article(fake, "nizam-test", "m-1")
    assert art["gate"] == "gated"

    # THE OLD BEHAVIOUR, for the size of the difference: a plain-string cut
    # spends 43 on the span and buys the reader the rest of the budget in prose.
    old = ls.truncate_for_gate(text, "gated", free_chars=ls.ARTICLE_FREE_CHARS)
    assert "](images/" in old["visible_text"], "fixture does not reproduce the bug"

    prose = _figure_prose(art)
    # The figure is charged its WEIGHT, so the prose shrinks by about that much.
    assert len(prose) < len(old["visible_text"]) - weight + len(SPAN_1) + 8
    assert art["is_truncated"] is True
    # …and whichever way the budget fell, the reader never gets more than today.
    assert _nonspace(prose + _captions(art)) <= _nonspace(old["visible_text"])
    assert "](images/" not in art["text"]


def test_the_reveal_shows_every_figure_the_preview_showed() -> None:
    """§6.24 — the ⚠ in §3.5.1, and the one this plan is most likely to ship broken.

    `FullSection` declared `tables?` and nothing else, and `renderFull` passed
    only `tables`. An article-surface reveal builds its ملاحق through
    `_appendix_sections`, so its `text` carries `IMG_{n}` token lines the moment
    §3 ships — with no map, the client's unconditional strip eats every one and
    the reader who JUST UNLOCKED the document sees fewer figures than the
    anonymous preview of the same page.

    ⚠ ASSERTED OVER TOKENS AND MAP KEYS, NOT SECTION IDS.
    `test_the_public_page_and_the_reveal_agree_on_the_appendix` compares ids and
    sails straight through this bug.
    """
    text = f"المادة الأولى: انظر المخطط.\n\n{SPAN_1}"
    apx = _display_chunk(
        "dddddddd-0000-0000-0000-000000000001",
        content=f"ملحق (1)\n\n{_span('page_020_img_002.jpeg')}",
        content_display=None,
        title="ملحق (1)",
        corpus="appendix",
    )
    body_chunk = {
        "id": ART_CHUNK, "regulation_id": REG_ID, "title": "الفصل الأول",
        "position": 1, "corpus": "with_articles",
        "chunk_ref": "17393_reg_029_chunk_001",
        "content": "نص", "content_display": None, "has_images": True,
    }
    images = [
        _image_row(chunk_id=ART_CHUNK, n=1),
        _image_row(basename="page_020_img_002.jpeg", n=2, chunk_id=apx["id"],
                   title="جدول الحدود", ext="png"),
    ]
    fake = _article_surface_fake(
        [_extracted_article(1, text)], images, chunks=[body_chunk, apx]
    )

    doc = ls.get_regulation_doc(fake, "nizam-test")
    full = ls.get_full_regulation(fake, "nizam-test")

    doc_tokens, full_tokens = _all_tokens(doc["visible_sections"]), _all_tokens(full["sections"])
    doc_images, full_images = _all_images(doc["visible_sections"]), _all_images(full["sections"])

    # The fixture is not vacuous: a مادة figure AND a ملحق figure, on both.
    assert doc_tokens == ["IMG_1", "IMG_2"], doc_tokens
    # THE ASSERTION. The reveal shows every figure the preview showed …
    assert full_tokens == doc_tokens
    assert set(full_images) == set(doc_images) == set(doc_tokens)
    # … resolved to the same bytes …
    assert full_images == doc_images
    # … and every token in the reveal resolves, section by section (a token with
    # no entry is what the client strips).
    for section in full["sections"]:
        assert set(_tokens(section["text"])) == set(section["images"]), section["id"]
    # The PNG kept its own extension — `image_ref + ".jpeg"` would 404 (D7).
    assert full_images["IMG_2"]["url"].endswith(".png")


def test_the_chunk_reveal_carries_its_figures() -> None:
    """§3.5.1 on the OTHER reveal branch — the one 1,562 of the figures ride.

    `get_full_regulation` has two branches and both build their own section
    dicts. The article branch is covered by
    `test_the_reveal_shows_every_figure_the_preview_showed`; this is the chunk
    one, where the `without_articles` body stream lives — the لوائح فنية that
    are mostly diagrams, and the bulk of what this feature exists to fix.

    The نظام is GATED, which is the case that matters: the anon preview withholds
    (its budget cannot hold the figure), and the reveal is the only place the
    reader ever sees it. If the map is missing there, the person who paid gets a
    naked token — or, once the client strips it, prose with a hole.
    """
    body = f"{_run(4)}\n\n{SPAN_1}\n\n{_run(60)}"
    fake = _reg_with_images(
        [_display_chunk(CHUNK_ID, content=body, content_display=None)],
        [_image_row(transcript=_run(90))],       # weight ≫ the 600-char budget
        tier="gated",
    )

    preview = ls.get_regulation_doc(fake, "nizam-test")["visible_sections"][0]
    assert preview["images"] == {}                # withheld, as the gate should
    assert preview["is_truncated"] is True
    assert "](images/" not in preview["text"]

    section = ls.get_full_regulation(fake, "nizam-test")["sections"][0]
    assert _tokens(section["text"]) == ["IMG_1"]
    # THE ASSERTION: the token resolves for the reader who paid.
    assert set(section["images"]) == {"IMG_1"}
    assert section["images"]["IMG_1"]["url"].startswith(_IMAGE_BASE)
    assert "](images/" not in section["text"]


def test_the_article_counter_is_page_scoped() -> None:
    """§6.25 / D17 — the same figure is «الصورة 7» in the document, «الصورة 1» alone.

    D8's rule is *render order within the render scope*, and a reader counting
    figures on a مادة page can only count the ones on that page. The document
    threads ONE number across its sections in reading order — مواد first, then
    the ملاحق, which must keep counting from where the مواد stopped rather than
    restarting at 1 on the same page.
    """
    spans = [f"page_{i:03d}_img_001.jpeg" for i in range(1, 9)]
    articles = [
        _extracted_article(1, "المادة 1\n\n" + "\n\n".join(_span(b) for b in spans[0:3])),
        _extracted_article(2, "المادة 2\n\n" + "\n\n".join(_span(b) for b in spans[3:6])),
        _extracted_article(3, "المادة 3\n\n" + _span(spans[6])),
    ]
    apx = _display_chunk(
        "dddddddd-0000-0000-0000-000000000001",
        content=f"ملحق (1)\n\n{_span(spans[7])}",
        content_display=None,
        title="ملحق (1)",
        corpus="appendix",
    )
    body_chunk = {
        "id": ART_CHUNK, "regulation_id": REG_ID, "title": "الفصل الأول",
        "position": 1, "corpus": "with_articles",
        "chunk_ref": "17393_reg_029_chunk_001",
        "content": "نص", "content_display": None, "has_images": True,
    }
    images = [
        _image_row(basename=b, n=i, chunk_id=(apx["id"] if i == 8 else ART_CHUNK))
        for i, b in enumerate(spans, start=1)
    ]
    fake = _article_surface_fake(articles, images, chunks=[body_chunk, apx])

    doc = ls.get_regulation_doc(fake, "nizam-test")
    by_id = {s["id"]: s for s in doc["visible_sections"]}

    # ONE numbering across the whole page, in reading order …
    assert _tokens(by_id["art-1"]["text"]) == ["IMG_1", "IMG_2", "IMG_3"]
    assert _tokens(by_id["art-2"]["text"]) == ["IMG_4", "IMG_5", "IMG_6"]
    assert _tokens(by_id["art-3"]["text"]) == ["IMG_7"]
    # … and the ملاحق keep counting from where the مواد stopped. Restarting here
    # would print «الصورة 1» twice on one page.
    assert _tokens(by_id["apx-1"]["text"]) == ["IMG_8"]
    assert by_id["apx-1"]["images"]["IMG_8"]["n"] == 8

    # THE SAME FIGURE, on its own page, is «الصورة 1» — D17.
    art = ls.get_regulation_article(fake, "nizam-test", "m-3")
    assert _tokens(art["text"]) == ["IMG_1"]
    assert art["images"]["IMG_1"]["n"] == 1
    assert (
        art["images"]["IMG_1"]["image_ref"]
        == by_id["art-3"]["images"]["IMG_7"]["image_ref"]
    )
    # …and the reveal of that مادة agrees with its own public page, not with the
    # document's numbering.
    full_art = ls.get_full_article(fake, "nizam-test", "m-3")
    assert set(full_art["images"]) == {"IMG_1"}


# ---- 9.8 The cost bound (D12) ---------------------------------------------


def test_the_images_read_is_one_round_trip_per_document() -> None:
    """ONE batched read filtered on `regulation_id` — never one per chunk.

    Per-chunk would be 60 round trips on a chunk-surface نظام, at page-render
    latency, for a payload the ISR bake keeps for 24 hours.
    """
    chunks = [
        _display_chunk(
            f"eeeeeeee-0000-0000-0000-{i:012d}",
            content=f"{_run(3)}\n\n{SPAN_1}",
            content_display=None,
            position=i,
        )
        for i in (1, 2, 3)
    ]
    fake = _CountingSupabase(
        seo_item_meta=[{"content_type": "regulation", "content_id": REG_ID,
                        "slug": "nizam-test", "seo_tier": "open",
                        "gate_override": None}],
        regulations_v2=[_bare_reg_row()],
        seo_articles=_holed(232, 68),
        chunks_v2=chunks,
        chunk_images=[_image_row(chunk_id=chunks[0]["id"])],
    )
    ls.get_regulation_doc(fake, "nizam-test")
    assert fake.tables_queried.count("chunk_images") == 1


def test_an_all_extracted_regulation_with_no_span_never_reads_the_images_table() -> None:
    """D12's cost bound on the article surface: no chunk body, no span, no query.

    Only 175 of 1,689 published أنظمة carry a figure at all, and an extracted
    مادة can only carry one if its own slice still holds the markup — 52 rows
    corpus-wide — which is free to check in memory.
    """
    fake = _CountingSupabase(
        seo_item_meta=[{"content_type": "regulation", "content_id": REG_ID,
                        "slug": "nizam-test", "seo_tier": "open",
                        "gate_override": None}],
        regulations_v2=[_bare_reg_row()],
        seo_articles=_article_rows([1, 2, 3]),
        chunks_v2=[],
    )
    ls.get_regulation_doc(fake, "nizam-test")
    assert "chunk_images" not in fake.tables_queried


def test_a_span_inside_an_extracted_article_forces_the_read() -> None:
    """The other half of that bound: a مادة carrying markup MUST be resolved.

    This is the whole of §3.5 — 52 rows print filenames today precisely because
    the span survived the slice, so «no chunk-shaped body» must not mean «no
    figures».
    """
    fake = _CountingSupabase(
        seo_item_meta=[{"content_type": "regulation", "content_id": REG_ID,
                        "slug": "nizam-test", "seo_tier": "open",
                        "gate_override": None}],
        regulations_v2=[_bare_reg_row()],
        seo_articles=[_extracted_article(1, f"المادة 1\n\n{SPAN_1}")],
        chunks_v2=[],
        chunk_images=[_image_row(chunk_id=ART_CHUNK)],
    )
    doc = ls.get_regulation_doc(fake, "nizam-test")
    assert fake.tables_queried.count("chunk_images") == 1
    assert set(doc["visible_sections"][0]["images"]) == {"IMG_1"}


def test_a_mada_page_with_no_figures_never_reads_the_images_table() -> None:
    """`has_images` is the bound on the مادة surface — 96.7% of chunks are false."""
    fake = _article_surface_fake(
        [_extracted_article(1, "المادة الأولى: نص بلا صور.")], []
    )
    counting = _CountingSupabase(**{k: list(v) for k, v in fake.tables.items()})
    counting.tables["chunks_v2"][0]["has_images"] = False
    art = ls.get_regulation_article(counting, "nizam-test", "m-1")
    assert art is not None
    assert art["images"] == {}
    assert "chunk_images" not in counting.tables_queried
