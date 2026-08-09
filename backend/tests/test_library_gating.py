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
