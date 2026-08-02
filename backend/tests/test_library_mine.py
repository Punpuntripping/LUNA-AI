"""Access-tiers Phase B2 — «مكتبتي», the user's library shelf.

Plan: ``.claude/plans/access_tiers_gating.md`` PART 5B (§5B.1–§5B.5).
Decisions: ``.claude/plans/access_tiers_gating_DECISIONS.md`` D16 / D16.1 / D16.2.

Covers:

    backend.app.services.library_items_service   (record_use / save / unsave / list)
    backend.app.api.library_mine                 (the four endpoints + no-store)

No live DB. Supabase is an in-memory PostgREST stand-in (``FakeSupabase``, the
same shape ``test_library_gating.py`` uses, extended with UPDATE/DELETE and the
``item_row_id`` surrogate key) that holds real row dicts and actually applies the
filters — so the assertions below exercise the real query paths, not a scripted
result queue.

The load-bearing assertions are:
  * ``test_opening_a_service_shelves_it`` — services never produce an unlock row,
    so the الخدمات tab exists ONLY because opening one shelves it (§5B.2).
  * ``test_one_use_counts_exactly_once_for_a_gated_item`` — insert-then-update
    must not double-count (D16.2).
  * ``test_explicit_save_preserves_existing_counters`` — a «حفظ» must not clobber
    an ``source='auto'`` row's history.
  * ``test_downgraded_user_still_sees_every_row`` — §5B.4: a frozen library is
    never rendered as an empty page.
  * ``test_articles_nest_under_their_parent_regulation`` — §5B.1: a مادة without
    its statute reads as an orphan.
  * ``test_record_use_swallows_a_db_failure`` — a shelf write must never break a
    content read.
  * ``test_saving_never_writes_to_the_money_ledger`` — library_unlocks is MONEY.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

import pytest

from backend.app.services import library_items_service as lis
from backend.app.services import library_service as ls


# ---------------------------------------------------------------------------
# In-memory PostgREST stand-in
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data: Any, count: Optional[int] = None) -> None:
        self.data = data
        self.count = count


class _Chain:
    """The subset of PostgREST semantics this module uses."""

    def __init__(self, fake: "FakeSupabase", table: str) -> None:
        self._fake = fake
        self._table = table
        self._filters: list[tuple[str, str, Any]] = []
        self._orders: list[tuple[str, bool]] = []
        self._range: Optional[tuple[int, int]] = None
        self._limit: Optional[int] = None
        self._count: Optional[str] = None
        self._negate = False
        self._pending: Optional[tuple[str, Any]] = None

    # --- builders ---------------------------------------------------------
    def select(self, *_cols: Any, count: Optional[str] = None, **_k: Any) -> "_Chain":
        self._count = count
        return self

    def eq(self, col: str, val: Any) -> "_Chain":
        self._filters.append(("eq", col, val))
        return self

    def neq(self, col: str, val: Any) -> "_Chain":
        self._filters.append(("neq", col, val))
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
        self._fake.writes.append((self._table, "upsert", dict(json_body)))
        self._pending = ("upsert", (dict(json_body), on_conflict, ignore_duplicates))
        return self

    def insert(self, json_body: Any, **_k: Any) -> "_Chain":
        return self.upsert(json_body)

    def update(self, json_body: Any, **_k: Any) -> "_Chain":
        self._fake.writes.append((self._table, "update", dict(json_body)))
        self._pending = ("update", dict(json_body))
        return self

    def delete(self, **_k: Any) -> "_Chain":
        self._fake.writes.append((self._table, "delete", {}))
        self._pending = ("delete", None)
        return self

    # --- execution --------------------------------------------------------
    def _matches(self, row: dict[str, Any]) -> bool:
        for op, col, val in self._filters:
            cell = row.get(col)
            if op == "eq":
                if cell is None or str(cell) != str(val):
                    return False
            elif op == "neq":
                if cell is not None and str(cell) == str(val):
                    return False
            elif op == "in":
                if cell is None or str(cell) not in {str(v) for v in val}:
                    return False
            elif op == "ilike":
                needle = str(val).strip("%").lower()
                if needle and needle not in str(cell or "").lower():
                    return False
            elif op == "is":
                if val == "null" and cell is not None:
                    return False
            elif op == "not_is":
                if val == "null" and cell is None:
                    return False
        return True

    def _boom(self) -> None:
        if self._table in self._fake.fail_tables:
            raise RuntimeError(f"simulated PostgREST failure on {self._table}")

    def execute(self) -> _Result:
        if self._pending is not None:
            kind, payload = self._pending
            if kind == "upsert":
                return self._execute_upsert(*payload)
            self._boom()
            table = self._fake.tables.setdefault(self._table, [])
            hits = [r for r in table if self._matches(r)]
            if kind == "update":
                for r in hits:
                    r.update(payload)
                return _Result([dict(r) for r in hits])
            for r in hits:
                table.remove(r)
            return _Result([])

        self._boom()
        rows = [
            dict(r) for r in self._fake.tables.get(self._table, []) if self._matches(r)
        ]
        for col, desc in reversed(self._orders):
            rows.sort(key=lambda r: (r.get(col) is None, str(r.get(col))), reverse=desc)

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
        self._boom()
        table = self._fake.tables.setdefault(self._table, [])
        keys = [k.strip() for k in (on_conflict or "").split(",") if k.strip()]
        if keys and ignore_duplicates:
            for existing in table:
                if all(str(existing.get(k)) == str(body.get(k)) for k in keys):
                    return _Result([])  # ON CONFLICT DO NOTHING → zero rows
        row = dict(body)
        row.setdefault("item_row_id", f"i{len(table) + 1}")
        row.setdefault("unlock_id", f"u{len(table) + 1}")
        row.setdefault("source", "auto")
        row.setdefault("use_count", 0)
        row.setdefault("first_used_at", None)
        row.setdefault("last_used_at", None)
        row.setdefault("saved_at", "2026-07-27T00:00:00+00:00")
        table.append(row)
        return _Result([row])


class FakeSupabase:
    def __init__(self, *, quota_row: Optional[dict[str, Any]] = None, **tables: Any) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {
            k: list(v) for k, v in tables.items()
        }
        self.tables.setdefault("library_items", [])
        self.tables.setdefault("library_unlocks", [])
        self.tables.setdefault("seo_gate_defaults", [])
        self.quota_row = quota_row
        self.writes: list[tuple[str, str, dict]] = []
        self.fail_tables: set[str] = set()
        # RPC observability: `record_library_item_use` (migration 107) is the
        # ATOMIC shelf write. record_use() falls back to a read-modify-write when
        # it fails, so without recording calls here every test would pass through
        # the fallback and the real path would be untested.
        self.rpc_calls: list[tuple[str, dict]] = []
        self.fail_rpcs: set[str] = set()

    def table(self, name: str) -> _Chain:
        return _Chain(self, name)

    def rpc(self, name: str, params: dict) -> "_RpcChain":
        self.rpc_calls.append((name, dict(params)))
        return _RpcChain(self, name, params)

    # test helpers ---------------------------------------------------------
    def items(self) -> list[dict[str, Any]]:
        return self.tables["library_items"]

    def item(self, content_type: str, content_id: str) -> Optional[dict[str, Any]]:
        for r in self.items():
            if r["content_type"] == content_type and r["content_id"] == content_id:
                return r
        return None


class _RpcChain:
    def __init__(self, fake: FakeSupabase, name: str, params: dict) -> None:
        self._fake = fake
        self._name = name
        self._params = params

    def execute(self) -> _Result:
        if self._name in self._fake.fail_rpcs:
            raise RuntimeError(f"rpc {self._name} unavailable")

        if self._name == "get_user_quota_state":
            row = self._fake.quota_row
            return _Result([row] if row else [])

        if self._name == "record_library_item_use":
            return _Result([self._record_library_item_use()])

        if self._name == "bm25_search":
            return _Result(self._bm25_search())

        raise AssertionError(f"unexpected RPC {self._name}")

    def _bm25_search(self) -> list[dict[str, Any]]:
        """Stand-in for migration 111's ranking function.

        ⚠ SUBSTRING MATCH, NOT BM25 — see the same note in
        ``test_library_judgments.py``. Shelf search is an INTERSECTION (rank the
        public corpora, keep what is on this shelf), and the intersection is what
        these tests own; ranking quality is a SQL property.

        Matches against the seeded corpus rows' titles, which is the only text
        this fake's tables carry — and the only text the real index would hold
        for them beyond the always-free lead.
        """
        needle = (self._params.get("p_query") or "").strip()
        wanted = set(self._params.get("p_corpora") or [])
        table_for = {
            "regulation": ("regulations_v2", "clean_title"),
            "judgment": ("cases", "short_summary"),
            "circular": ("circulars", "title"),
            "service": ("services", "service_name_ar"),
        }
        rows: list[dict[str, Any]] = []
        for corpus in wanted:
            table, col = table_for.get(corpus, (None, None))
            if not table:
                continue
            for r in self._fake.tables.get(table, []):
                if needle and needle in str(r.get(col) or ""):
                    rows.append(
                        {
                            "corpus": corpus,
                            "content_id": str(r.get("id")),
                            "slug": None,
                            "title": str(r.get(col) or ""),
                            "facets": {},
                            "score": 1.0,
                            "total_count": 0,
                        }
                    )
        for r in rows:
            r["total_count"] = len(rows)
        return rows

    def _record_library_item_use(self) -> int:
        """Mirror migration 107's INSERT … ON CONFLICT DO UPDATE.

        Note the two asymmetries the real SQL has and this must keep: ``source``
        is NOT touched on conflict (an explicit 'manual' pin is never demoted
        back to 'auto' by a later read), and ``first_used_at`` is set only on
        insert.
        """
        now = lis._now_iso()
        user_id = self._params["p_user_id"]
        ct = self._params["p_content_type"]
        cid = self._params["p_content_id"]

        for row in self._fake.tables["library_items"]:
            if (
                row["user_id"] == user_id
                and row["content_type"] == ct
                and row["content_id"] == cid
            ):
                row["use_count"] = int(row.get("use_count") or 0) + 1
                row["last_used_at"] = now
                return row["use_count"]

        self._fake.tables["library_items"].append(
            {
                "item_row_id": str(uuid.uuid4()),
                "user_id": user_id,
                "content_type": ct,
                "content_id": cid,
                "source": "auto",
                "use_count": 1,
                "first_used_at": now,
                "last_used_at": now,
                "saved_at": now,
            }
        )
        return 1


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """library_service keeps module-level TTL caches; a seeded policy must never
    leak into the next test."""
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
# Fixtures — ids + the get_user_quota_state row shape (migration 105)
# ---------------------------------------------------------------------------

USER = "aaaaaaaa-0000-0000-0000-000000000001"
REG_ID = "11111111-2222-3333-4444-555555555555"
REG_ID_2 = "99999999-8888-7777-6666-555555555555"
SVC_ID = "22222222-3333-4444-5555-666666666666"
JUD_ID = "33333333-4444-5555-6666-777777777777"

FREE_PERIOD = "free:202607"
OLD_PAID_PERIOD = "pro:20260101:0"
RESETS_AT = "2026-08-01T00:00:00+00:00"


def quota_row(
    *,
    plan: str = "free",
    limit: Optional[int] = 10,
    used: int = 0,
    period_key: Optional[str] = FREE_PERIOD,
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
        "library_period_resets_at": None if locked else RESETS_AT,
    }


def unlock_row(
    *,
    content_type: str = "regulation",
    content_id: str = REG_ID,
    period_key: str = FREE_PERIOD,
    cost: int = 1,
) -> dict[str, Any]:
    return {
        "unlock_id": f"seeded-{content_type}-{content_id}",
        "user_id": USER,
        "content_type": content_type,
        "content_id": content_id,
        "period_key": period_key,
        "cost": cost,
        "surface": "library",
        "unlocked_at": "2026-07-01T00:00:00+00:00",
    }


def corpus_fake(**extra: Any) -> FakeSupabase:
    """A fake seeded with just enough corpus + sidecar to hydrate cards."""
    return FakeSupabase(
        quota_row=extra.pop("quota_row", quota_row()),
        regulations_v2=[
            {
                "id": REG_ID,
                "clean_title": "نظام العمل",
                "title": "نظام العمل",
                "entity_name": "وزارة الموارد البشرية",
                "status_class": "in_force",
                "doc_type_bucket": "law",
                "summary": "ملخص نظام العمل",
                "sectors": ["عمل"],
            },
            {
                "id": REG_ID_2,
                "clean_title": "نظام الشركات",
                "title": "نظام الشركات",
                "entity_name": "وزارة التجارة",
                "status_class": "in_force",
                "doc_type_bucket": "law",
                "summary": "ملخص نظام الشركات",
                "sectors": ["تجارة"],
            },
        ],
        services=[
            {
                "id": SVC_ID,
                "service_name_ar": "إصدار رخصة",
                "provider_name": "وزارة التجارة",
                "is_most_used": True,
                "sectors": ["تجارة"],
                "intro_description": "خدمة إصدار الرخصة التجارية",
            }
        ],
        cases=[
            {
                "id": JUD_ID,
                "case_ref": "17642_ap_1",
                "court": "المحكمة التجارية",
                "court_level": "appeal",
                "city": "الرياض",
                "case_number": "1",
                "judgment_number": "2",
                "date_hijri": "1445-01-01",
                "date_gregorian": "2023-07-19",
                "legal_domains": ["تجاري"],
                "short_summary": "- نزاع تجاري حول عقد توريد",
                "summary": "نزاع تجاري",
                "facts": "",
                "ruling": "",
            }
        ],
        seo_item_meta=[
            {"content_type": "regulation", "content_id": REG_ID,
             "slug": "نظام-العمل", "seo_tier": None, "gate_override": None,
             "updated_at": "2026-07-01T00:00:00+00:00"},
            {"content_type": "regulation", "content_id": REG_ID_2,
             "slug": "نظام-الشركات", "seo_tier": None, "gate_override": None,
             "updated_at": "2026-07-01T00:00:00+00:00"},
            {"content_type": "service", "content_id": SVC_ID,
             "slug": "إصدار-رخصة", "seo_tier": None, "gate_override": None,
             "updated_at": "2026-07-01T00:00:00+00:00"},
            {"content_type": "judgment", "content_id": JUD_ID,
             "slug": "نزاع-تجاري", "seo_tier": None, "gate_override": None,
             "updated_at": "2026-07-01T00:00:00+00:00"},
            {"content_type": "article", "content_id": f"{REG_ID}#74",
             "slug": "المادة-74", "seo_tier": None, "gate_override": None,
             "updated_at": "2026-07-01T00:00:00+00:00"},
            {"content_type": "article", "content_id": f"{REG_ID}#75",
             "slug": "المادة-75", "seo_tier": None, "gate_override": None,
             "updated_at": "2026-07-01T00:00:00+00:00"},
            {"content_type": "article", "content_id": f"{REG_ID_2}#3",
             "slug": "المادة-3", "seo_tier": None, "gate_override": None,
             "updated_at": "2026-07-01T00:00:00+00:00"},
        ],
        **extra,
    )


def find(items: list[dict[str, Any]], content_type: str, content_id: str):
    return next(
        (
            i
            for i in items
            if i["content_type"] == content_type and i["content_id"] == content_id
        ),
        None,
    )


# ===========================================================================
# 1. record_use — the implicit shelf (§5B.2)
# ===========================================================================


def test_opening_a_service_shelves_it() -> None:
    """THE reason the الخدمات tab works. Compliance services are policy-never-
    gated, so they never produce a library_unlocks row — opening one has to be
    enough to shelve it (§5B.2)."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "service", SVC_ID))

    row = fake.item("service", SVC_ID)
    assert row is not None
    assert row["use_count"] == 1
    assert row["source"] == "auto"
    assert row["first_used_at"] and row["last_used_at"]
    # and NOTHING was written to the money ledger.
    assert fake.tables["library_unlocks"] == []


def test_one_use_counts_exactly_once_for_a_gated_item() -> None:
    """D16.2 — one use increments use_count exactly once, not twice. The
    insert-then-update shape is exactly where a double count would come from."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))

    assert len(fake.items()) == 1
    assert fake.item("regulation", REG_ID)["use_count"] == 1


def test_a_second_use_increments_the_same_row() -> None:
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))
    first_used = fake.item("regulation", REG_ID)["first_used_at"]
    run(lis.record_use(fake, USER, "regulation", REG_ID))

    row = fake.item("regulation", REG_ID)
    assert len(fake.items()) == 1
    assert row["use_count"] == 2
    assert row["first_used_at"] == first_used  # first use is never rewritten


def test_record_use_never_touches_the_money_ledger() -> None:
    """A page view must never write to library_unlocks (D16)."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))
    assert not [w for w in fake.writes if w[0] == "library_unlocks"]


def test_record_use_swallows_a_db_failure() -> None:
    """A shelf-write failure must NEVER break a content read (D16.2).

    Both write paths have to fail to model an outage: the atomic RPC (migration
    107) is primary and the read-modify-write is its fallback, so failing only
    the table would still shelve the row via the RPC.
    """
    fake = corpus_fake()
    fake.fail_rpcs.add("record_library_item_use")
    fake.fail_tables.add("library_items")
    # Must not raise.
    run(lis.record_use(fake, USER, "regulation", REG_ID))
    assert fake.items() == []


def test_record_use_goes_through_the_atomic_rpc() -> None:
    """§5B.2's `SET use_count = use_count + 1` is not expressible over PostgREST,
    so migration 107 ships it as an RPC. The read-modify-write path loses an
    increment under a simultaneous double-click, so the RPC must be what actually
    runs — not merely what is available."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))

    assert ("record_library_item_use", {
        "p_user_id": USER,
        "p_content_type": "regulation",
        "p_content_id": REG_ID,
    }) in fake.rpc_calls
    # The fallback must NOT have run: no UPDATE/INSERT against the table.
    assert [w for w in fake.writes if w[1] == "library_items"] == []
    assert fake.item("regulation", REG_ID)["use_count"] == 1


def test_record_use_falls_back_when_the_rpc_is_unavailable() -> None:
    """A missing or failing RPC must degrade to an approximate counter, never to
    losing the shelf row — record_use swallows exceptions, so a hard failure here
    would be completely invisible."""
    fake = corpus_fake()
    fake.fail_rpcs.add("record_library_item_use")

    run(lis.record_use(fake, USER, "regulation", REG_ID))
    run(lis.record_use(fake, USER, "regulation", REG_ID))

    row = fake.item("regulation", REG_ID)
    assert row is not None, "fallback lost the shelf row entirely"
    assert row["use_count"] == 2


def test_the_atomic_rpc_never_demotes_a_manual_pin() -> None:
    """Migration 107's ON CONFLICT clause deliberately does not touch `source`.
    A later open must not turn «حفظ» back into an incidental 'auto' row."""
    fake = corpus_fake()
    run(lis.save_item(fake, USER, "regulation", REG_ID))
    assert fake.item("regulation", REG_ID)["source"] == "manual"

    run(lis.record_use(fake, USER, "regulation", REG_ID))

    row = fake.item("regulation", REG_ID)
    assert row["source"] == "manual"
    assert row["use_count"] == 1


def test_record_use_ignores_an_empty_reference() -> None:
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "", ""))
    assert fake.items() == []


def test_record_use_does_not_downgrade_a_manual_pin() -> None:
    fake = corpus_fake()
    run(lis.save_item(fake, USER, "regulation", REG_ID))
    run(lis.record_use(fake, USER, "regulation", REG_ID))

    row = fake.item("regulation", REG_ID)
    assert row["source"] == "manual"
    assert row["use_count"] == 1


# ===========================================================================
# 2. save / unsave — the explicit pin (§5B.2)
# ===========================================================================


def test_saving_never_writes_to_the_money_ledger() -> None:
    """Saving is FREE at every tier and grants NO access: it stores a pointer,
    never content — no unlock is charged, no ledger row appears."""
    fake = corpus_fake()
    run(lis.save_item(fake, USER, "regulation", REG_ID))

    assert fake.tables["library_unlocks"] == []
    assert not [w for w in fake.writes if w[0] == "library_unlocks"]
    row = fake.item("regulation", REG_ID)
    assert row["source"] == "manual"
    assert row["use_count"] == 0


def test_saving_a_gated_item_the_user_has_not_unlocked_is_allowed() -> None:
    """§5B.2 — it shows locked in مكتبتي, which is a useful intent signal."""
    fake = corpus_fake()
    run(lis.save_item(fake, USER, "regulation", REG_ID))

    data = run(lis.list_items(fake, USER, content_type="regulation"))
    row = find(data["items"], "regulation", REG_ID)
    assert row is not None
    assert row["source"] == "manual"
    assert row["was_unlocked"] is False   # never unlocked → no access granted
    assert row["is_frozen"] is False      # nothing to freeze — it was never paid
    assert data["stored_library_count"] == 0


def test_explicit_save_preserves_existing_counters() -> None:
    """An explicit save must not clobber a prior source='auto' row's counters."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))
    run(lis.record_use(fake, USER, "regulation", REG_ID))
    first_used = fake.item("regulation", REG_ID)["first_used_at"]

    run(lis.save_item(fake, USER, "regulation", REG_ID))

    row = fake.item("regulation", REG_ID)
    assert row["source"] == "manual"
    assert row["use_count"] == 2
    assert row["first_used_at"] == first_used


def test_unsave_keeps_a_used_row_and_demotes_it_to_auto() -> None:
    """Unpinning is not 'erase my history' — «الأكثر استخداماً» must not lose it."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))
    run(lis.save_item(fake, USER, "regulation", REG_ID))
    run(lis.unsave_item(fake, USER, "regulation", REG_ID))

    row = fake.item("regulation", REG_ID)
    assert row is not None
    assert row["source"] == "auto"
    assert row["use_count"] == 1


def test_unsave_removes_a_pin_only_row() -> None:
    fake = corpus_fake()
    run(lis.save_item(fake, USER, "regulation", REG_ID))
    run(lis.unsave_item(fake, USER, "regulation", REG_ID))
    assert fake.item("regulation", REG_ID) is None


def test_unsave_is_idempotent() -> None:
    fake = corpus_fake()
    run(lis.unsave_item(fake, USER, "regulation", REG_ID))  # must not raise
    assert fake.items() == []


def test_write_endpoints_validate_the_content_type() -> None:
    from backend.app.errors import LunaHTTPException

    fake = corpus_fake()
    with pytest.raises(LunaHTTPException) as exc:
        run(lis.save_item(fake, USER, "wikipedia", "x"))
    assert exc.value.status_code == 400
    assert exc.value.detail == "نوع المحتوى غير صالح"


# ===========================================================================
# 3. Listing — cards, entitlement badges, sorting (§5B.3–§5B.5)
# ===========================================================================


def test_listing_hydrates_hub_card_fields() -> None:
    """The row carries the SAME field names the public hub cards use, so the
    existing components drop straight in (§5B.5)."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "service", SVC_ID))
    run(lis.record_use(fake, USER, "judgment", JUD_ID))

    data = run(lis.list_items(fake, USER))
    svc = find(data["items"], "service", SVC_ID)
    jud = find(data["items"], "judgment", JUD_ID)

    assert svc["title"] == "إصدار رخصة"
    assert svc["provider_name"] == "وزارة التجارة"
    assert svc["url"] == "/compliance/إصدار-رخصة"
    assert svc["is_available"] is True

    assert jud["court"] == "المحكمة التجارية"
    assert jud["court_level_label"] == "استئناف"
    assert jud["url"] == "/judgments/نزاع-تجاري"
    assert "نزاع تجاري" in jud["snippet"]


def test_listing_never_ships_body_text() -> None:
    """Everything rendered is in the never-gated class (§1.3) — that is what
    makes listing a frozen item safe."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))
    data = run(lis.list_items(fake, USER))
    row = find(data["items"], "regulation", REG_ID)
    for banned in ("text", "sections", "body_md", "sharh_md", "content"):
        assert banned not in row


def test_listing_is_read_only() -> None:
    """Rendering the shelf must not bump use_count (and must not touch the
    ledger)."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))
    before = fake.item("regulation", REG_ID)["use_count"]
    run(lis.list_items(fake, USER))
    assert fake.item("regulation", REG_ID)["use_count"] == before
    assert not [w for w in fake.writes if w[0] == "library_unlocks"]


def test_was_unlocked_reflects_the_ledger() -> None:
    fake = corpus_fake()
    fake.tables["library_unlocks"].append(unlock_row(period_key=FREE_PERIOD))
    run(lis.record_use(fake, USER, "regulation", REG_ID))

    data = run(lis.list_items(fake, USER, content_type="regulation"))
    row = find(data["items"], "regulation", REG_ID)
    assert row["was_unlocked"] is True
    assert row["is_frozen"] is False        # same period → predicate passes
    assert data["stored_library_count"] == 1
    assert data["frozen_count"] == 0


def test_downgraded_user_still_sees_every_row() -> None:
    """§5B.4 — a frozen library rendered as an empty page is a worse product AND
    a worse conversion surface. Every row lists, with is_frozen=True on the
    paid-era ones and the shelf count for the «لديك {n} مصدراً» CTA."""
    fake = corpus_fake(quota_row=quota_row(plan="free", period_key=FREE_PERIOD))
    # Two paid-era unlocks + one from the current free period.
    fake.tables["library_unlocks"].extend(
        [
            unlock_row(content_id=REG_ID, period_key=OLD_PAID_PERIOD),
            unlock_row(content_type="judgment", content_id=JUD_ID,
                       period_key=OLD_PAID_PERIOD),
            unlock_row(content_id=REG_ID_2, period_key=FREE_PERIOD),
        ]
    )
    for ct, cid in (("regulation", REG_ID), ("judgment", JUD_ID),
                    ("regulation", REG_ID_2)):
        run(lis.record_use(fake, USER, ct, cid))

    data = run(lis.list_items(fake, USER))

    assert data["total"] == 3                       # nothing filtered out
    assert len(data["items"]) == 3
    assert find(data["items"], "regulation", REG_ID)["is_frozen"] is True
    assert find(data["items"], "judgment", JUD_ID)["is_frozen"] is True
    assert find(data["items"], "regulation", REG_ID_2)["is_frozen"] is False
    assert data["stored_library_count"] == 3
    assert data["frozen_count"] == 2                # drives the upgrade CTA
    assert data["is_paid"] is False


def test_re_upgrading_unfreezes_the_whole_shelf() -> None:
    """The §1.2 predicate's first clause: a paid caller reaches every row ever
    unlocked, so nothing is frozen."""
    fake = corpus_fake(quota_row=quota_row(plan="pro", limit=200,
                                           period_key="pro:20260701:0"))
    fake.tables["library_unlocks"].append(
        unlock_row(content_id=REG_ID, period_key=OLD_PAID_PERIOD)
    )
    run(lis.record_use(fake, USER, "regulation", REG_ID))

    data = run(lis.list_items(fake, USER))
    row = find(data["items"], "regulation", REG_ID)
    assert row["was_unlocked"] is True
    assert row["is_frozen"] is False
    assert data["frozen_count"] == 0
    assert data["is_paid"] is True


def test_frozen_badge_uses_the_layer_b_predicate() -> None:
    """Not a re-implementation: the badge must move with
    library_service._predicate_passes, which is the ONE §1.2 rule."""
    fake = corpus_fake()
    fake.tables["library_unlocks"].append(
        unlock_row(content_id=REG_ID, period_key=OLD_PAID_PERIOD)
    )
    run(lis.record_use(fake, USER, "regulation", REG_ID))

    from shared import quota

    state = run(quota.library_state(fake, USER))
    row = fake.tables["library_unlocks"][0]
    assert ls._predicate_passes(row, state) is False

    data = run(lis.list_items(fake, USER))
    assert find(data["items"], "regulation", REG_ID)["is_frozen"] is True


def test_listing_never_calls_resolve_access(monkeypatch) -> None:
    """resolve_access CHARGES — it must never run just to render a list."""
    called: list[Any] = []

    async def _boom(*a: Any, **k: Any):
        called.append(a)
        raise AssertionError("resolve_access must not be called from مكتبتي")

    monkeypatch.setattr(ls, "resolve_access", _boom)
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))
    run(lis.list_items(fake, USER))
    assert called == []


# ===========================================================================
# 4. §5B.1 nesting — مواد under their نظام
# ===========================================================================


def test_articles_nest_under_their_parent_regulation() -> None:
    """§5B.1 — 'a مادة without its statute reads as an orphan'. No bare article
    row at top level when its نظام is on the same page."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))
    run(lis.record_use(fake, USER, "article", f"{REG_ID}#74"))
    run(lis.record_use(fake, USER, "article", f"{REG_ID}#75"))

    data = run(lis.list_items(fake, USER, content_type="regulation"))

    assert [i["content_type"] for i in data["items"]] == ["regulation"]
    assert data["total"] == 1
    reg = data["items"][0]
    assert [c["content_id"] for c in reg["child_articles"]] == [
        f"{REG_ID}#74",
        f"{REG_ID}#75",
    ]
    assert reg["child_articles"][0]["article_no"] == 74
    assert reg["child_articles"][0]["article_label"] == "المادة 74"
    assert reg["child_articles"][0]["url"] == "/regulations/نظام-العمل/المادة-74"
    # The نظام group ranks on self + مواد.
    assert reg["group_use_count"] == 3
    # …and the tab counters still report مواد separately.
    assert data["counts"]["article"] == 2
    assert data["counts"]["regulation"] == 1


def test_an_orphan_article_gets_a_synthesized_parent() -> None:
    """A مادة saved without its statute still nests — under a نظام header the
    user never opened (is_shelf_row=False)."""
    fake = corpus_fake()
    run(lis.save_item(fake, USER, "article", f"{REG_ID_2}#3"))

    data = run(lis.list_items(fake, USER, content_type="regulation"))

    assert len(data["items"]) == 1
    parent = data["items"][0]
    assert parent["content_type"] == "regulation"
    assert parent["content_id"] == REG_ID_2
    assert parent["is_shelf_row"] is False
    assert parent["source"] is None
    assert parent["title"] == "نظام الشركات"
    assert [c["content_id"] for c in parent["child_articles"]] == [f"{REG_ID_2}#3"]


def test_article_filter_is_normalized_to_the_regulation_view() -> None:
    """مواد are never a top-level tab (§5B.1)."""
    assert lis.normalize_content_type("article") == "regulation"
    assert lis.normalize_content_type("") is None
    assert lis.normalize_content_type("judgment") == "judgment"


def test_a_madda_inherits_its_parents_unlock(monkeypatch) -> None:
    """D5 — unlocking a نظام implicitly covers all its مواد, so the shelf must
    not badge a covered مادة as locked."""
    fake = corpus_fake()
    fake.tables["library_unlocks"].append(
        unlock_row(content_id=REG_ID, period_key=FREE_PERIOD)
    )
    run(lis.record_use(fake, USER, "article", f"{REG_ID}#74"))

    data = run(lis.list_items(fake, USER, content_type="regulation"))
    child = data["items"][0]["child_articles"][0]
    assert child["was_unlocked"] is True
    assert child["is_frozen"] is False


# ===========================================================================
# 5. Sorting + paging (§5B.3)
# ===========================================================================


def test_sort_most_used_orders_by_use_count() -> None:
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "judgment", JUD_ID))
    for _ in range(3):
        run(lis.record_use(fake, USER, "service", SVC_ID))

    data = run(lis.list_items(fake, USER, sort="most_used"))
    assert [i["content_id"] for i in data["items"]] == [SVC_ID, JUD_ID]
    assert data["items"][0]["use_count"] == 3
    assert data["sort"] == "most_used"


def test_default_sort_is_recency() -> None:
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "service", SVC_ID))
    # Force a strictly later last_used_at on the judgment row.
    run(lis.record_use(fake, USER, "judgment", JUD_ID))
    fake.item("judgment", JUD_ID)["last_used_at"] = "2099-01-01T00:00:00+00:00"

    data = run(lis.list_items(fake, USER))
    assert data["items"][0]["content_id"] == JUD_ID
    assert data["sort"] == "recent"


def test_pin_only_rows_sort_last_under_recency() -> None:
    """A never-used pin has last_used_at NULL → NULLS LAST."""
    fake = corpus_fake()
    run(lis.save_item(fake, USER, "judgment", JUD_ID))
    run(lis.record_use(fake, USER, "service", SVC_ID))

    data = run(lis.list_items(fake, USER))
    assert [i["content_id"] for i in data["items"]] == [SVC_ID, JUD_ID]


def test_paging_reports_totals() -> None:
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "service", SVC_ID))
    run(lis.record_use(fake, USER, "judgment", JUD_ID))

    page1 = run(lis.list_items(fake, USER, page=1, page_size=1))
    page2 = run(lis.list_items(fake, USER, page=2, page_size=1))
    assert page1["total"] == 2 and page1["total_pages"] == 2
    assert len(page1["items"]) == 1 and len(page2["items"]) == 1
    assert page1["items"][0]["content_id"] != page2["items"][0]["content_id"]


def test_empty_shelf_is_an_empty_page_not_an_error() -> None:
    fake = corpus_fake()
    data = run(lis.list_items(fake, USER))
    assert data["items"] == []
    assert data["total"] == 0
    assert data["total_pages"] == 1
    assert data["stored_library_count"] == 0


def test_invalid_sort_is_rejected() -> None:
    from backend.app.errors import LunaHTTPException

    with pytest.raises(LunaHTTPException) as exc:
        lis.normalize_sort("alphabetical")
    assert exc.value.status_code == 400
    assert exc.value.detail == "ترتيب غير صالح"


def test_an_unhydratable_row_still_lists() -> None:
    """§5B.4 forbids filtering rows out — a de-slugged/unpublished item lists
    with is_available=False rather than vanishing."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "circular", "44444444-0000-0000-0000-000000000000"))

    data = run(lis.list_items(fake, USER))
    assert len(data["items"]) == 1
    assert data["items"][0]["is_available"] is False
    assert data["items"][0]["url"] is None


def test_listing_survives_a_quota_rpc_failure() -> None:
    """The badge degrades, the listing does not: everything here is never-gated
    metadata, so an unknown predicate leaks nothing."""
    fake = corpus_fake(quota_row=None)
    fake.tables["library_unlocks"].append(unlock_row(period_key=FREE_PERIOD))
    run(lis.record_use(fake, USER, "regulation", REG_ID))

    data = run(lis.list_items(fake, USER))
    row = find(data["items"], "regulation", REG_ID)
    assert row["was_unlocked"] is True
    assert len(data["items"]) == 1


# ===========================================================================
# 6. The endpoints — wiring, 204s, and the no-store header
# ===========================================================================


def _client(fake: FakeSupabase, monkeypatch):
    """A throwaway app with the real router and the auth/db deps overridden.

    ``case_service.get_user_id`` is patched through ``monkeypatch`` (never a bare
    module assignment) so a failing assertion can't leak the stub into the next
    test module — the auth_id → users.user_id mapping (D16.1) is case_service's
    job and is tested there."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import library_mine
    from backend.app.deps import get_current_user, get_supabase
    from backend.app.errors import LunaHTTPException, luna_exception_handler
    from backend.app.services import case_service
    from shared.auth.jwt import AuthUser

    monkeypatch.setattr(case_service, "get_user_id", lambda _sb, _auth: USER)

    app = FastAPI()
    app.include_router(library_mine.router)
    app.add_exception_handler(LunaHTTPException, luna_exception_handler)
    app.dependency_overrides[get_current_user] = lambda: AuthUser(
        auth_id="auth-1", email="a@b.com", role="authenticated"
    )
    app.dependency_overrides[get_supabase] = lambda: fake
    return TestClient(app)


def test_get_endpoint_returns_the_envelope_with_no_store(monkeypatch) -> None:
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "service", SVC_ID))
    res = _client(fake, monkeypatch).get("/api/v1/library/mine")

    assert res.status_code == 200
    assert res.headers["Cache-Control"] == "private, no-store"
    body = res.json()
    assert body["items"][0]["content_id"] == SVC_ID
    assert body["items"][0]["title"] == "إصدار رخصة"
    assert body["page"] == 1 and body["sort"] == "recent"
    assert body["stored_library_count"] == 0
    assert "counts" in body


def test_use_beacon_records_one_use_and_returns_204(monkeypatch) -> None:
    fake = corpus_fake()
    res = _client(fake, monkeypatch).post(
        "/api/v1/library/mine/use",
        json={"content_type": "service", "content_id": SVC_ID},
    )
    assert res.status_code == 204
    assert res.headers["Cache-Control"] == "private, no-store"
    assert fake.item("service", SVC_ID)["use_count"] == 1


def test_save_and_unsave_endpoints(monkeypatch) -> None:
    fake = corpus_fake()
    client = _client(fake, monkeypatch)
    ref = {"content_type": "regulation", "content_id": REG_ID}

    assert client.post("/api/v1/library/mine/save", json=ref).status_code == 204
    assert fake.item("regulation", REG_ID)["source"] == "manual"

    res = client.request("DELETE", "/api/v1/library/mine/save", json=ref)
    assert res.status_code == 204
    assert fake.item("regulation", REG_ID) is None


def test_unsave_accepts_query_params_too(monkeypatch) -> None:
    fake = corpus_fake()
    client = _client(fake, monkeypatch)
    client.post(
        "/api/v1/library/mine/save",
        json={"content_type": "regulation", "content_id": REG_ID},
    )
    res = client.delete(
        f"/api/v1/library/mine/save?content_type=regulation&content_id={REG_ID}"
    )
    assert res.status_code == 204
    assert fake.item("regulation", REG_ID) is None


def test_beacon_accepts_a_slug_because_public_pages_have_no_ids(monkeypatch) -> None:
    """No public doc-page payload exposes a corpus uuid, so the client sends the
    slug it has and the shelf still stores the canonical id — the id space
    library_unlocks and seo_item_meta are keyed on."""
    fake = corpus_fake()
    res = _client(fake, monkeypatch).post(
        "/api/v1/library/mine/use",
        json={"content_type": "service", "slug": "إصدار-رخصة"},
    )
    assert res.status_code == 204
    assert fake.item("service", SVC_ID)["use_count"] == 1


def test_a_madda_slug_resolves_through_its_parent_nizam(monkeypatch) -> None:
    """«المادة-74» repeats across statutes, so a مادة needs both slugs."""
    fake = corpus_fake(
        seo_articles=[
            {"regulation_id": REG_ID, "article_no": 74, "slug": "المادة-74"}
        ]
    )
    res = _client(fake, monkeypatch).post(
        "/api/v1/library/mine/save",
        json={
            "content_type": "article",
            "slug": "المادة-74",
            "parent_slug": "نظام-العمل",
        },
    )
    assert res.status_code == 204
    assert fake.item("article", f"{REG_ID}#74") is not None


def test_an_unresolvable_slug_is_a_404_on_save_but_a_204_on_the_beacon(
    monkeypatch,
) -> None:
    fake = corpus_fake()
    client = _client(fake, monkeypatch)
    ref = {"content_type": "service", "slug": "لا-يوجد"}

    assert client.post("/api/v1/library/mine/save", json=ref).status_code == 404
    assert client.post("/api/v1/library/mine/use", json=ref).status_code == 204
    assert fake.items() == []


def test_a_reference_with_neither_id_nor_slug_is_a_400(monkeypatch) -> None:
    fake = corpus_fake()
    res = _client(fake, monkeypatch).post(
        "/api/v1/library/mine/save", json={"content_type": "service"}
    )
    assert res.status_code == 400
    assert "نوع المحتوى ومعرّفه مطلوبان" in res.text


def test_a_draft_form_slug_cannot_be_shelved(monkeypatch) -> None:
    """The forms liability gate holds here too — a draft form is unresolvable."""
    fake = corpus_fake(
        forms=[
            {"id": "f-1", "slug": "عقد-عمل", "title_ar": "عقد عمل",
             "category": "عمل", "use_case_md": "", "review_status": "draft",
             "is_published": False}
        ]
    )
    res = _client(fake, monkeypatch).post(
        "/api/v1/library/mine/save",
        json={"content_type": "form", "slug": "عقد-عمل"},
    )
    assert res.status_code == 404
    assert fake.items() == []


def test_bad_content_type_is_a_400_in_arabic(monkeypatch) -> None:
    fake = corpus_fake()
    res = _client(fake, monkeypatch).get("/api/v1/library/mine?content_type=wikipedia")
    assert res.status_code == 400
    assert "نوع المحتوى غير صالح" in res.text


def test_use_beacon_never_500s_on_a_shelf_write_failure(monkeypatch) -> None:
    """The beacon is fire-and-forget by design (D16.2)."""
    fake = corpus_fake()
    fake.fail_tables.add("library_items")
    res = _client(fake, monkeypatch).post(
        "/api/v1/library/mine/use",
        json={"content_type": "service", "content_id": SVC_ID},
    )
    assert res.status_code == 204


# ===========================================================================
# «حفظ» IS AN UNGATING ACTION (user decision 2026-07-28)
# ===========================================================================
#
# §5B.2 originally made save free at every tier — "it stores a POINTER, never
# content" — and allowed saving a gated item you had not unlocked, which listed
# LOCKED in مكتبتي as an intent signal. That is reversed:
#
#     EVERYTHING IN مكتبتي IS UNGATED.
#
# Save, «عرض المصدر» and «اعرض النص كاملاً» are all ungating actions, charged the
# same once. A save that cannot unlock is REFUSED, not shelved as a locked row.
# The only lock badge left is the §5B.4 freeze, caused by a lapsed plan.
#
# NOTE the charge lives on the ROUTE, not in `lis.save_item` (which stays the
# pure shelf write, mirroring how /library/full charges). These tests therefore
# go through the API — a direct service call would bypass the meter.


def test_save_charges_an_unlock_and_shelves(monkeypatch) -> None:
    fake = corpus_fake()
    fake.quota_row = quota_row(limit=10, used=0)

    res = _client(fake, monkeypatch).post(
        "/api/v1/library/mine/save",
        json={"content_type": "regulation", "content_id": REG_ID},
    )

    assert res.status_code == 204
    assert len(fake.tables["library_unlocks"]) == 1, "save did not charge"
    assert fake.item("regulation", REG_ID)["source"] == "manual"


def test_save_is_refused_when_the_quota_is_exhausted(monkeypatch) -> None:
    """A refused save must leave NOTHING behind — no ledger row and, crucially,
    no shelf row, or مكتبتي would hold something the user cannot read."""
    fake = corpus_fake()
    fake.quota_row = quota_row(limit=10, used=10)

    res = _client(fake, monkeypatch).post(
        "/api/v1/library/mine/save",
        json={"content_type": "regulation", "content_id": REG_ID},
    )

    assert res.status_code == 402
    assert res.json()["reason"] == "quota_exhausted"
    assert fake.tables["library_unlocks"] == []
    assert fake.item("regulation", REG_ID) is None


def test_saving_an_already_unlocked_item_costs_nothing_more(monkeypatch) -> None:
    """Unlocks are permanent (§1.2): pinning something you already own is free."""
    fake = corpus_fake()
    fake.quota_row = quota_row(limit=10, used=1)
    fake.tables["library_unlocks"] = [unlock_row(period_key=FREE_PERIOD, cost=1)]

    res = _client(fake, monkeypatch).post(
        "/api/v1/library/mine/save",
        json={"content_type": "regulation", "content_id": REG_ID},
    )

    assert res.status_code == 204
    assert len(fake.tables["library_unlocks"]) == 1, "re-save double-charged"
    assert fake.item("regulation", REG_ID)["source"] == "manual"


def test_saving_a_never_gated_service_is_free(monkeypatch) -> None:
    """Services are policy-open (§1.3): pinning one costs no unlock at all, even
    with the allowance fully spent."""
    fake = corpus_fake()
    fake.quota_row = quota_row(limit=10, used=10)

    res = _client(fake, monkeypatch).post(
        "/api/v1/library/mine/save",
        json={"content_type": "service", "content_id": SVC_ID},
    )

    assert res.status_code == 204
    assert fake.tables["library_unlocks"] == []
    assert fake.item("service", SVC_ID) is not None


# ===========================================================================
# 8. Shelf search (bm25_navigation_search.md §5.2 · §6.2)
#
# مكتبتي is a JOIN, not a corpus: the rows are public documents this user
# happened to open or pin, so searching it means "rank the public index, keep
# what is on this shelf". The intersection is the part that has to be right —
# ranking quality is a SQL property (Wave F), and the fake above is explicit
# about standing in for it rather than reproducing it.
# ===========================================================================


def test_a_shelf_search_narrows_to_matching_rows() -> None:
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))      # نظام العمل
    run(lis.record_use(fake, USER, "regulation", REG_ID_2))    # نظام الشركات

    data = run(lis.list_items(fake, USER, q="نظام الشركات"))

    assert [i["content_id"] for i in data["items"]] == [REG_ID_2]
    assert data["total"] == 1
    assert data["q"] == "نظام الشركات"


def test_a_shelf_search_that_matches_nothing_is_an_empty_page() -> None:
    """Not "fall back to the whole shelf". An empty result is an ANSWER, and the
    ``q`` echo is what lets the UI say «لا نتائج» instead of «مكتبتك فارغة»."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))

    data = run(lis.list_items(fake, USER, q="لا-يوجد-شيء-كهذا"))

    assert data["items"] == []
    assert data["total"] == 0
    assert data["total_pages"] == 1
    assert data["q"] == "لا-يوجد-شيء-كهذا"


def test_counts_stay_WHOLE_shelf_during_a_search() -> None:
    """``counts`` drives TAB VISIBILITY (§5B.1). Narrowing it with the search
    would make tabs disappear as the user types, i.e. the shelf would look like
    it was being emptied by the act of searching it."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))
    run(lis.record_use(fake, USER, "service", SVC_ID))

    data = run(lis.list_items(fake, USER, q="نظام العمل"))

    assert [i["content_id"] for i in data["items"]] == [REG_ID]
    assert data["counts"]["regulation"] == 1
    assert data["counts"]["service"] == 1, "tab counts must not follow the filter"


def test_a_shelf_search_matches_a_مادة_through_its_parent() -> None:
    """مواد are NOT indexed (D6), and they are not displayed on their own either
    (§5B.1 nests them under their statute) — so a مادة matches when its نظام
    matches. The display rule and the search rule agree, which is the point."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "article", f"{REG_ID}#74"))

    data = run(lis.list_items(fake, USER, q="نظام العمل"))

    assert len(data["items"]) == 1
    parent = data["items"][0]
    assert parent["content_id"] == REG_ID
    assert [c["content_id"] for c in parent["child_articles"]] == [f"{REG_ID}#74"]


def test_search_shelf_returns_hit_shaped_rows() -> None:
    """The ``/search/mine`` projection. Same intersection, different envelope —
    one ranking path feeding two surfaces."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))

    hits, exact = lis.search_shelf(fake, USER, "نظام العمل")

    assert [h["corpus"] for h in hits] == ["regulation"]
    assert hits[0]["content_id"] == REG_ID
    assert exact is True
    # D3: an address, never an excerpt.
    assert "snippet" not in hits[0]


def test_an_unsearched_shelf_listing_is_byte_identical_to_before() -> None:
    """The ``q``-absent path must be untouched: ``sort`` still decides, and the
    two search fields are inert."""
    fake = corpus_fake()
    run(lis.record_use(fake, USER, "regulation", REG_ID))
    run(lis.record_use(fake, USER, "service", SVC_ID))

    data = run(lis.list_items(fake, USER, sort="recent"))

    assert len(data["items"]) == 2
    assert data["q"] is None
