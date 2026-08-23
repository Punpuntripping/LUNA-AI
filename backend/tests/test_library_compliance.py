"""/compliance service-guides wing tests.

Plan: ``.claude/plans/compliance_service_guides.md`` (§0 §1 §4.1 §4.2 §6)
Data contract: ``agentic_for_ministry/ingestion/service_guides/REFERENCE.md``

The wing publishes 169 SERVICE GUIDES — ريحان's own authored rewrite of each
issuing entity's official PDF user-guide, with our own screenshots — in full and
UNGATED. So unlike every other library test module, nothing here asserts about
truncation or hidden bytes. The load-bearing assertions are the other three:

  * ``source_pdf_url`` NEVER reaches the payload, at any layer. The guide is our
    rewrite of that PDF and the PDF is never surfaced; the ONLY outbound link is
    the entity's own service page (``service_url``).
  * NO RAW HOLE TOKEN EVER SHIPS. A hole is a line that is only ``\\d+_\\d+``;
    one that has no image row is blanked server-side. A raw ``223719_1`` on a
    user-facing page is the failure mode the whole design exists to prevent.
  * The wing is OPEN — ``get_compliance_guide`` resolves no gate and truncates
    nothing (pinned by a test that makes ``resolve_gate`` explode).

⚠ «nothing here asserts about truncation» NEEDED A CAVEAT ON 2026-08-23. It still
holds for BYTES — no guide is ever cut, and that is the whole SEO bet. But the
hub's ``q`` stopped being a Python substring pass over ``title + summary`` that
day and became BM25 over the ``compliance`` corpus in ``search_index``
(``.claude/plans/compliance_entity_sections.md`` §6.5, migration 144). So the
suite now DOES assert about a truncated RESULT SET: a ranked id list cut at
``search_service.HUB_SEARCH_LIMIT`` makes ``total_count`` a floor rather than a
total. Two unrelated kinds of truncation; only the second one exists in this
wing, and the ``rpc`` stand-in below is what makes it reachable.

⚠ And the corpus is no longer the 169 guides named above — plan §1 verified 337
on 2026-08-22, every one published. That growth is not trivia here: at 169 rows a
200-hit cap could never be hit, so «the count is always exact» was true by
accident as much as by design. It is 337 now, and the cap is reachable.

No live DB. Supabase is the same in-memory PostgREST stand-in the other library
suites use (``FakeSupabase``): real row dicts per table, with the filters,
ordering, ``in.()`` chunking and range actually applied — a scripted result queue
could not catch the two things this wing is most exposed to, namely NUMERIC
ordering (``image_index`` 1..10, ``most_used_rank``) and rows that must be
dropped for having no slug.
"""
from __future__ import annotations

import json
import math
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import public_library as pl
from backend.app.deps import get_current_user_optional, get_supabase
from backend.app.errors import LunaHTTPException, luna_exception_handler
from backend.app.middleware.route_limits import library_rate_limit
from backend.app.services import library_budget_service as lb
from backend.app.services import library_service as ls
from backend.app.services import search_service
from shared.config import get_settings


# ---------------------------------------------------------------------------
# In-memory PostgREST stand-in
# ---------------------------------------------------------------------------


def _cmp_key(value: Any) -> tuple[int, float, str]:
    """Total order that does not explode on mixed/None columns.

    Numbers sort NUMERICALLY — the judgments suite's fake stringifies every sort
    key, which is harmless for its ISO dates and catastrophic here: ``image_index``
    10 would land between 1 and 2, and a screenshot list silently out of order is
    precisely the kind of bug this module is supposed to catch.
    """
    if isinstance(value, bool):
        return (1, 0.0, str(value))
    if isinstance(value, (int, float)):
        return (0, float(value), "")
    return (1, 0.0, str(value))


class _Result:
    def __init__(self, data: Any, count: Optional[int] = None) -> None:
        self.data = data
        self.count = count


class _Chain:
    """Applies the subset of PostgREST semantics this wing uses."""

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
    def select(self, *cols: Any, count: Optional[str] = None, **_k: Any) -> "_Chain":
        self._count = count
        self._fake.selects.append((self._table, cols[0] if cols else ""))
        return self

    def eq(self, col: str, val: Any) -> "_Chain":
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col: str, vals: list[Any]) -> "_Chain":
        vals = list(vals)
        self._fake.in_calls.append((self._table, col, vals))
        self._filters.append(("in", col, vals))
        return self

    def ilike(self, col: str, pattern: str) -> "_Chain":
        self._filters.append(("ilike", col, pattern))
        return self

    def contains(self, col: str, vals: list[Any]) -> "_Chain":
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
        self._fake.orders.append((self._table, col, desc))
        return self

    def range(self, start: int, end: int) -> "_Chain":
        self._range = (start, end)
        return self

    def limit(self, n: int) -> "_Chain":
        self._limit = n
        return self

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
                if not set(map(str, val)) <= set(map(str, cell or [])):
                    return False
            elif op == "is":
                if val == "null" and cell is not None:
                    return False
            elif op == "not_is":
                if val == "null" and cell is None:
                    return False
        return True

    def execute(self) -> _Result:
        if self._table in self._fake.missing:
            raise RuntimeError(f"relation {self._table} does not exist")
        rows = [
            dict(r) for r in self._fake.tables.get(self._table, []) if self._matches(r)
        ]
        for col, desc in reversed(self._orders):
            rows.sort(key=lambda r: _cmp_key(r.get(col)), reverse=desc)
        count = len(rows) if self._count == "exact" else None
        if self._range is not None:
            start, end = self._range
            rows = rows[start : end + 1]
        if self._limit is not None and self._count != "exact":
            rows = rows[: self._limit]
        return _Result(rows, count)


class _RpcResult:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self._data = data

    def execute(self) -> "_RpcResult":
        return self

    @property
    def data(self) -> list[dict[str, Any]]:
        return self._data


class FakeSupabase:
    """Row-backed fake: seed tables, then the service queries them for real."""

    def __init__(self, **tables: list[dict[str, Any]]) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {
            k: list(v) for k, v in tables.items()
        }
        self.missing: set[str] = set()
        self.in_calls: list[tuple[str, str, list[Any]]] = []
        self.orders: list[tuple[str, str, bool]] = []
        self.selects: list[tuple[str, str]] = []
        # What ``library_sector_counts_published()`` reports for the wings that
        # reach the RPC. Its ``compliance`` column counts the ``services`` corpus
        # — a DIFFERENT number from this wing's guides — so it is seeded absurd on
        # purpose: any test that sees 9_999 has read the wrong source.
        #
        # ⚠ THIS IS THE SECTOR-COUNTS RPC ONLY. ``bm25_search`` hits are NOT
        # seeded here and must not be: ``_bm25_search`` DERIVES them from the
        # seeded ``library_compliance_v`` + ``seo_item_meta`` rows, exactly as
        # migration 144's index build does, so a search test seeds GUIDES and
        # never a pre-baked result list it could accidentally agree with.
        self.rpc_rows: list[dict[str, Any]] = []

    def table(self, name: str) -> _Chain:
        return _Chain(self, name)

    def rpc(self, name: str, params: dict[str, Any]) -> _RpcResult:
        """The two RPCs this wing reaches — and the second one is new.

        ⚠ ``bm25_search`` WAS AN ``assert`` FAILURE HERE UNTIL 2026-08-23, and
        that was correct at the time, not an oversight: the guides were
        deliberately absent from ``search_index``, the hub's ``q`` was a Python
        substring pass over ``title + summary``, and a call to the ranking RPC
        from this wing would have been a bug worth failing loudly on. §6.5 of
        ``.claude/plans/compliance_entity_sections.md`` REVERSED that premise —
        the 337 guides joined the index as the ``compliance`` corpus (migration
        144) and ``q`` now takes the same ``corpus_search_ids`` → ``rank_map``
        path every other wing takes. So the assert became a BRANCH; the
        sector-counts behaviour below is untouched and still hard-asserts, so an
        unexpected THIRD RPC name is as loud as it ever was.
        """
        if name == "bm25_search":
            return _RpcResult(self._bm25_search(params))
        assert name == ls._SECTOR_COUNTS_RPC, name
        return _RpcResult(self.rpc_rows)

    def _bm25_search(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Stand-in for ``public.bm25_search()`` (migrations 111 + 144).

        ⚠ THIS IS A WEIGHTED TERM COUNT, NOT BM25, and it is not trying to be.
        What these tests own is the WIRING: that ``q`` leaves the service through
        the RPC, that the ranked ORDER survives the corpus fetch instead of being
        re-sorted by ``most_used_rank``, that the wing's own predicates run as
        POST-filters over the ranked set, and that the ``HUB_SEARCH_LIMIT`` cut is
        reported honestly. Ranking QUALITY is a property of the SQL and is
        calibrated against a real query set — never against a Python fake, which
        could only ever prove it agrees with itself. (The judgments suite's fake
        carries the same disclaimer, for the same reason.)

        THE INDEXED DOCUMENT MIRRORS MIGRATION 144's ``elsif p_corpus =
        'compliance'`` BRANCH FIELD FOR FIELD — a fake that indexes something
        else, or answers with different row keys, passes for the wrong reason:

          * ``title`` → weight A, hence the ``× 3`` below: the RPC multiplies
            A-weighted term frequency by ``p_title_boost`` (default 3.0), which
            no caller overrides.
          * ``provider_name`` → B. ``service_ref`` + ``sectors`` → the facets
            text. The WHOLE ``guide_md`` → D (the wing is ungated end to end, so
            unlike ``circular`` there is no free-floor to compute).
          * The image-hole LINES are stripped from the body with
            ``ls._GUIDE_HOLE_RE`` — the same rule, in the same role, as the SQL's
            ``regexp_replace(..., '^[ \\t]*\\d+_\\d+[ \\t]*$', '', 'gn')`` (§6.2:
            a bare ``223719_1`` is otherwise a searchable term on text no reader
            can ever see, and it inflates ``doc_len``).
          * ``summary`` is NOT indexed. §6.2 measured it a VERBATIM substring of
            ``guide_md`` on 337/337 guides, so the index reaches it through the
            body or not at all.

        Only guides carrying a slugged ``seo_item_meta`` row are in the index
        (the migration's ``join``) — which is precisely what lets
        ``_bm25_hub_rows`` skip the ``_published_ids`` intersection in search
        mode, so the fake has to honour it too.

        Row KEYS are the RPC's ``returns table``: ``corpus, content_id, slug,
        title, facets, score, total_count``. ``total_count`` is a window over the
        whole scored set, so it is stamped BEFORE the limit/offset slice; and
        ``p_limit`` / ``p_offset`` are honoured, which is what makes the 200-hit
        cap reachable in a test rather than theoretical.
        """
        if "compliance" not in list(params.get("p_corpora") or []):
            return []
        # One call is wholly public or wholly one owner's — never both (the RPC
        # matches ``owner_user_id IS NULL`` when ``p_owner`` is null). A guide is
        # never owned, so an owner-scoped call finds nothing here either.
        if params.get("p_owner"):
            return []
        terms = [t for t in str(params.get("p_query") or "").split() if t]
        if not terms:
            return []

        slugs = {
            str(m.get("content_id")): m.get("slug")
            for m in self.tables.get("seo_item_meta", [])
            if m.get("content_type") == "compliance" and m.get("slug")
        }
        scored: list[dict[str, Any]] = []
        for g in self.tables.get("library_compliance_v", []):
            slug = slugs.get(str(g.get("id")))
            if not slug:
                continue
            title = str(g.get("title") or "")
            rest = " ".join(
                [
                    str(g.get("provider_name") or ""),
                    str(g.get("service_ref") or ""),
                    " ".join(str(s) for s in (g.get("sectors") or [])),
                    ls._GUIDE_HOLE_RE.sub("", str(g.get("guide_md") or "")),
                ]
            )
            # AND semantics, like the RPC's ``tsq`` CTE: «تجديد السجل» means both
            # terms, not either.
            if any(t not in title and t not in rest for t in terms):
                continue
            scored.append(
                {
                    "corpus": "compliance",
                    "content_id": str(g.get("id")),
                    "slug": slug,
                    "title": title,
                    "facets": {
                        "provider_name": g.get("provider_name"),
                        "service_ref": g.get("service_ref"),
                        "sectors": list(g.get("sectors") or []),
                    },
                    "score": float(
                        sum(3 * title.count(t) + rest.count(t) for t in terms)
                    ),
                    "total_count": 0,
                }
            )
        # ``order by s.score desc, s.content_id`` — the RPC's tiebreak verbatim,
        # because an unstable ranking would make the ordering assertions below
        # flap for a reason that has nothing to do with the wing.
        scored.sort(key=lambda r: (-r["score"], r["content_id"]))
        for row in scored:
            row["total_count"] = len(scored)
        offset = max(0, int(params.get("p_offset") or 0))
        limit = int(params.get("p_limit") or 20)
        return scored[offset : offset + limit]


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """``_published_ids`` is a module-level TTL cache keyed by content_type; one
    test's published set must never leak into the next.

    ``library_budget_service.reset_process_state()`` is in here for the NEXT
    module's benefit, not this one's: that item-budget window is process-global
    and keyed by user, so a suite that never clears it accumulates across FILES
    until a later module's authed hub request gets a spurious 429 (reproduced
    2026-08-19 — running ``courts`` then ``sector_wing`` then ``enforcement``
    fails 22 tests that pass in any other order). This module refuses to add to
    that pile.
    """
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()
    pl._total_pages_memo.clear()
    pl._reset_sector_memos()
    library_rate_limit._fallback.reset()
    lb.reset_process_state()
    yield
    ls._published_ids_cache.clear()
    pl._total_pages_memo.clear()
    pl._reset_sector_memos()
    library_rate_limit._fallback.reset()
    lb.reset_process_state()


# ---------------------------------------------------------------------------
# Fixtures — one guide, its screenshots, its sidecar row
# ---------------------------------------------------------------------------

GUIDE_ID = "aaaaaaaa-0000-4000-8000-000000000001"
SLUG = "issue-work-visa"
SECTOR_AR = "المعاملات التجارية"
SECTOR_SLUG = "commercial-transactions"

# The corpus title. ALL 169 live titles open with this exact prefix; the «بالصور»
# rewrite is the frontend's (guideDisplayTitle), so the backend ships the corpus
# string untouched and only supplies the `image_count` that decision reads.
TITLE = "الدليل الشامل: إصدار تأشيرة عمل"

# A realistic body: prose, holes on their own lines, one hole whose image row does
# not exist, one token INSIDE a sentence, and one «الصورة N» reference in prose.
# The last two must survive verbatim — REFERENCE.md §4.1/§4.2.
GUIDE_MD = "\n".join(
    [
        "## الخطوة الأولى",
        "",
        "ادخل إلى المنصة كما هو موضح في الصورة 1 أدناه.",
        "",
        "223719_1",
        "",
        "## الخطوة الثانية",
        "",
        "اضغط على زر الدخول الموحد.",
        "",
        "  223719_2  ",
        "",
        "## الخطوة الثالثة",
        "",
        "هذه الخطوة فقدت صورتها في إعادة بناء المعرض.",
        "",
        "223719_9",
        "",
        "راجع 223719_2 المذكورة في هذا السطر للمقارنة.",
    ]
)

GUIDE_ROW = {
    "id": GUIDE_ID,
    "service_id": "bbbbbbbb-0000-4000-8000-000000000001",
    "service_ref": "1014104",
    "title": TITLE,
    "summary": "دليل مبسط لإصدار تأشيرة عمل لمنشأة قائمة.",
    "guide_md": GUIDE_MD,
    "image_count": 3,
    "most_used_rank": 12,
    "provider_name": "وزارة الموارد البشرية والتنمية الاجتماعية",
    "service_url": "https://www.my.gov.sa/wps/portal/snp/servicesDirectory/x",
    "sectors": [SECTOR_AR, "العمل والموارد البشرية"],
    # ⚠ NOT A COLUMN OF `library_compliance_v` — seeded here deliberately so the
    # tests below prove the SERVICE drops it rather than merely that the view
    # never offered it. Belt and braces on the one field that must never ship.
    "source_pdf_url": "https://storage.example.gov.sa/guides/223719.pdf",
}

# Seeded OUT OF ORDER: the reader must impose `image_index`, not trust insertion.
IMAGE_ROWS = [
    {
        "guide_id": GUIDE_ID,
        "image_ref": "223719_3",
        "image_index": 3,
        "description": "شاشة تأكيد إصدار التأشيرة بعد اكتمال الطلب.",
        "storage_path": "223719/223719_3.jpeg",
        "width": 1280,
        "height": 720,
    },
    {
        "guide_id": GUIDE_ID,
        "image_ref": "223719_1",
        "image_index": 1,
        "description": "الصفحة الرئيسية للمنصة مع زر تسجيل الدخول.",
        "storage_path": "223719/223719_1.jpeg",
        "width": 1440,
        "height": 900,
    },
    {
        "guide_id": GUIDE_ID,
        "image_ref": "223719_2",
        "image_index": 2,
        "description": "نافذة الدخول الموحد مع خيارات المصادقة الأربعة.",
        "storage_path": "223719/223719_2.jpeg",
        "width": 1440,
        "height": 900,
    },
]

META_ROW = {
    "content_type": "compliance",
    "content_id": GUIDE_ID,
    "slug": SLUG,
    "rank": 12,
    "indexable": True,
    "updated_at": "2026-08-19T00:00:00+00:00",
}


def _guide(n: int, **over: Any) -> dict[str, Any]:
    """The nth synthetic guide — distinct id, title, rank and provider."""
    gid = f"aaaaaaaa-0000-4000-8000-{n:012d}"
    row = {
        **GUIDE_ROW,
        "id": gid,
        "service_ref": f"10141{n:02d}",
        "title": f"{TITLE} {n}",
        "summary": f"ملخص الدليل رقم {n}.",
        "most_used_rank": n,
        "guide_md": "لا توجد صور في هذا الدليل.",
        "image_count": 0,
    }
    row.update(over)
    return row


# "not supplied" must be distinguishable from an explicit ``slug=None`` — the
# unslugged (unpublished) guide is a case these tests have to be able to build.
_UNSET = object()


def _meta(guide: dict[str, Any], slug: Any = _UNSET, **over: Any) -> dict:
    row = {
        **META_ROW,
        "content_id": guide["id"],
        "slug": f"guide-{guide['service_ref']}" if slug is _UNSET else slug,
    }
    row.update(over)
    return row


def _fake(**extra: Any) -> FakeSupabase:
    """The single-guide fixture: one published guide with three screenshots."""
    return FakeSupabase(
        library_compliance_v=[GUIDE_ROW],
        seo_item_meta=[META_ROW],
        service_guide_images=IMAGE_ROWS,
        **extra,
    )


def _wing(guides: list[dict[str, Any]], metas: list[dict[str, Any]]) -> FakeSupabase:
    return FakeSupabase(library_compliance_v=guides, seo_item_meta=metas)


def _app(supabase: Any, user: Any = None) -> FastAPI:
    app = FastAPI()
    app.state.redis = None
    app.add_exception_handler(LunaHTTPException, luna_exception_handler)
    app.include_router(pl.router)
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[get_current_user_optional] = lambda: user
    app.dependency_overrides[library_rate_limit] = lambda: None
    return app


# ---------------------------------------------------------------------------
# Wiring inventory — the flips that must all land in one deploy
# ---------------------------------------------------------------------------


def test_compliance_routes_registered() -> None:
    from backend.app.main import create_app

    paths = {getattr(r, "path", "") for r in create_app().routes}
    assert "/api/v1/public/library/compliance" in paths
    assert "/api/v1/public/library/compliance/{slug}" in paths


def test_wing_ready_flag_replaced_the_lying_name() -> None:
    """``COMPLIANCE_TABLE_READY`` is gone: there is no `compliance_table` and
    never will be — ``service_guides`` is the table the wing was waiting for."""
    assert ls.COMPLIANCE_WING_READY is True
    assert not hasattr(ls, "COMPLIANCE_TABLE_READY")
    assert "COMPLIANCE_WING_READY" in ls.__all__
    assert "get_compliance_guide" in ls.__all__


def test_compliance_sitemap_section_is_served() -> None:
    """The section is back (2026-08-19), keyed by the NEW content_type.

    ``'compliance'``, not ``'service'``: the stale ``'service'`` sidecar rows are
    Arabic slugs keyed by ``services.id`` from the retired wing and resolve to
    nothing. Feeding a crawler those would be several thousand 404s — which is
    exactly why the section was pulled in the first place.
    """
    assert pl._LIBRARY_SITEMAP_SECTIONS["compliance"] == ("compliance", "compliance")


def test_sitemap_section_lists_only_slugged_indexable_guides() -> None:
    listed = {**META_ROW, "content_id": "g-1", "slug": "renew-cr", "indexable": True}
    noindex = {**META_ROW, "content_id": "g-2", "slug": "hidden", "indexable": False}
    unslugged = {**META_ROW, "content_id": "g-3", "slug": None, "indexable": True}
    fake = FakeSupabase(seo_item_meta=[listed, noindex, unslugged])

    urls, _pages = ls.sitemap_library_urls(
        fake, "https://x.test", "compliance", "compliance"
    )

    assert [u["loc"] for u in urls] == ["https://x.test/compliance/renew-cr"]


def test_section_sources_carries_the_view() -> None:
    """The sector axis reads ``library_compliance_v`` — the join that puts
    ``services.sectors`` next to the guide body."""
    assert ls._SECTION_SOURCES["compliance"] == (
        "library_compliance_v",
        "compliance",
        "sectors",
    )
    assert "compliance" in ls.SECTOR_COUNT_SECTIONS


def test_compliance_is_not_in_the_authed_reveal_types() -> None:
    """Nothing to unlock: the public wing already served the whole guide."""
    assert "compliance" not in pl._FULL_CONTENT_TYPES


# ---------------------------------------------------------------------------
# Hub lister — ordering, filters, pagination
# ---------------------------------------------------------------------------


def test_hub_orders_by_most_used_rank_ascending() -> None:
    """Most-used first. The corpus supplies the ranking (the government portal's
    own popularity order), so page 1 is the services people actually file — not
    an alphabetical accident."""
    guides = [_guide(30), _guide(10), _guide(20)]
    fake = _wing(guides, [_meta(g) for g in guides])

    items = ls.list_compliance_hub(fake)["items"]

    assert [it["title"] for it in items] == [
        f"{TITLE} 10",
        f"{TITLE} 20",
        f"{TITLE} 30",
    ]


def test_hub_tiebreaks_equal_ranks_by_title_then_id() -> None:
    a = _guide(1, most_used_rank=5, title="ب دليل")
    b = _guide(2, most_used_rank=5, title="أ دليل")
    fake = _wing([a, b], [_meta(a), _meta(b)])

    items = ls.list_compliance_hub(fake)["items"]

    assert [it["title"] for it in items] == ["أ دليل", "ب دليل"]


def test_hub_sorts_a_null_rank_last() -> None:
    """``most_used_rank`` has zero NULLs live; the ordering must still be TOTAL.

    A partial order here would let two requests for the same page return the same
    rows in a different sequence, which reads as a broken paginator.
    """
    ranked = _guide(7, most_used_rank=7)
    unranked = _guide(8, most_used_rank=None)
    fake = _wing([unranked, ranked], [_meta(ranked), _meta(unranked)])

    items = ls.list_compliance_hub(fake)["items"]

    assert [it["title"] for it in items] == [f"{TITLE} 7", f"{TITLE} 8"]


def test_hub_filters_by_provider_substring() -> None:
    hrsd = _guide(1, provider_name="وزارة الموارد البشرية والتنمية الاجتماعية")
    moc = _guide(2, provider_name="وزارة التجارة")
    fake = _wing([hrsd, moc], [_meta(hrsd), _meta(moc)])

    items = ls.list_compliance_hub(fake, provider="التجارة")["items"]

    assert [it["provider_name"] for it in items] == ["وزارة التجارة"]


def test_hub_filters_by_sector_containment() -> None:
    """§7.1's convention, spelled the same way as every other wing: the RAW
    Arabic sector name, matched against the row's ``sectors`` array."""
    inside = _guide(1, sectors=[SECTOR_AR, "الطاقة"])
    outside = _guide(2, sectors=["الطاقة"])
    fake = _wing([inside, outside], [_meta(inside), _meta(outside)])

    items = ls.list_compliance_hub(fake, sector=SECTOR_AR)["items"]

    assert [it["title"] for it in items] == [f"{TITLE} 1"]


def test_hub_q_returns_the_bm25_ranking_not_a_title_and_summary_substring() -> None:
    """§6.5 — THE REVERSAL, 2026-08-23.

    SUPERSEDED PREMISE, kept on the record rather than deleted: this test was
    ``test_hub_q_matches_title_and_summary``, and it asserted a Python substring
    pass over ``title + summary`` — the behaviour the source guarded with «``q``
    HERE IS NOT BM25 AND MUST NOT BECOME IT». That was the honest answer for
    exactly as long as the guides were absent from ``search_index``. They joined
    it as the ``compliance`` corpus on 2026-08-23 (migration 144, plan §6), so
    ``q`` is now the same ``corpus_search_ids`` → ``rank_map`` path every other
    wing takes — and the two things that changed are the two things asserted
    here:

      * ORDER IS RELEVANCE, AND IT OVERRIDES ``most_used_rank``. The seed is
        adversarial on purpose — the strongest match is the LEAST-used guide — so
        a lister that quietly re-sorted the ranked set by the browse contract
        would hand these back the other way round. The second half of the test
        pins the browse order to the OPPOSITE sequence over the same rows, which
        is what makes "one contract per mode" an assertion and not a comment.
      * THE WHOLE ``guide_md`` BODY IS SEARCHABLE. ``body_only`` carries the term
        nowhere but its body — a column the hub select does not even fetch — so
        the old substring could not have found it, and the index is the only
        thing that can. (Its ``summary`` is deliberately silent on the term:
        §6.2 keeps ``summary`` out of the index because it is a verbatim
        substring of the body on all 337 live guides.)

    ``q`` is registered-only, then and now: the ROUTE drops it for an anonymous
    caller before this module sees it (``_search_query``), which is why there is
    still no anon check here.
    """
    body_only = _guide(1, title="حجز موعد", guide_md="خطوات تجديد السجل التجاري.")
    title_hit = _guide(30, title="تجديد السجل التجاري", guide_md="تجديد السجل.")
    neither = _guide(2, title="إصدار رخصة", summary="لا علاقة.", guide_md="لا علاقة.")
    guides = [body_only, title_hit, neither]
    fake = _wing(guides, [_meta(g) for g in guides])

    items = ls.list_compliance_hub(fake, q="تجديد")["items"]

    assert [it["title"] for it in items] == ["تجديد السجل التجاري", "حجز موعد"]
    # …and BROWSE is untouched: ``most_used_rank`` ASC, which for this seed is the
    # exact reverse. Search did not become the wing's ordering contract; it
    # replaces it for one request.
    browsed = ls.list_compliance_hub(fake)["items"]
    assert [it["title"] for it in browsed] == [
        "حجز موعد",
        "إصدار رخصة",
        "تجديد السجل التجاري",
    ]


def test_hub_search_reports_an_exact_total_below_the_cap() -> None:
    """§6.5 (2026-08-23) — SUPERSEDED PREMISE, kept in place.

    This docstring used to read: «this wing's ``q`` is an exhaustive pass over
    169 published rows, NEVER a BM25 set truncated at ``HUB_SEARCH_LIMIT`` — so
    the count is always exact». Every clause of that is now backwards. ``q`` IS a
    BM25 set and it IS cut at ``HUB_SEARCH_LIMIT``; the count is exact only
    BELOW the cut, which is the half this test pins and the half the sibling test
    pins from the other side. A UI may print «N نتيجة» only while
    ``total_count_is_exact`` is True.
    """
    guides = [_guide(n) for n in range(1, 4)]
    fake = _wing(guides, [_meta(g) for g in guides])

    data = ls.list_compliance_hub(fake, q="تأشيرة")

    assert data["total_count"] == 3
    assert data["total_count_is_exact"] is True
    assert len(data["items"]) == 3


def test_hub_search_at_the_cap_reports_a_floor_not_a_total() -> None:
    """The other half of the §6.5 reversal (2026-08-23), and the whole reason
    ``_compliance_published_rows`` grew a ``truncated`` return value.

    While ``q`` was an exhaustive substring pass, truncation was UNREACHABLE on
    this wing and ``total_count_is_exact`` was a constant True. It is reachable
    now: the ranked id set stops at ``search_service.HUB_SEARCH_LIMIT`` (200)
    over a 337-guide corpus, so a broad query gets a FLOOR — strictly fewer than
    the guides that actually match — and the envelope has to say which of the two
    it is handing over. A card grid printing 200 as «200 نتيجة» would be
    asserting a number the backend does not know.

    Seeded ONE FULL PAGE above the cap on purpose. At exactly the cap the flag
    would flip for an ambiguous reason (``len(ids) >= HUB_SEARCH_LIMIT`` is true
    both when the set was cut and when it merely happened to fill), and a smaller
    overshoot would leave the searched and browsed page counts numerically equal
    — which is a test that cannot tell the two totals apart.
    """
    over_cap = search_service.HUB_SEARCH_LIMIT + ls.HUB_PAGE_SIZE
    guides = [_guide(n) for n in range(1, over_cap + 1)]
    fake = _wing(guides, [_meta(g) for g in guides])

    data = ls.list_compliance_hub(fake, q="تأشيرة")

    assert data["total_count"] == search_service.HUB_SEARCH_LIMIT
    assert data["total_count"] < over_cap          # a floor, and it says so
    assert data["total_count_is_exact"] is False
    # The paginator walks the RANKED set, not the corpus: 200 hits at 9 a page.
    assert data["total_pages"] == math.ceil(
        search_service.HUB_SEARCH_LIMIT / ls.HUB_PAGE_SIZE
    )
    assert len(data["items"]) == ls.HUB_PAGE_SIZE
    # A browse over the SAME rows is unaffected — no cap, no floor, all 209 —
    # and it is a page LONGER than the search, which is the visible shape of the
    # cut. (Browse pages the whole published set; only ``q`` goes through BM25.)
    ls._published_ids_cache.clear()
    browsed = ls.list_compliance_hub(fake)
    assert browsed["total_pages"] == math.ceil(over_cap / ls.HUB_PAGE_SIZE)
    assert browsed["total_pages"] > data["total_pages"]
    assert browsed["total_count"] is None
    assert browsed["total_count_is_exact"] is True


def test_hub_paginates_at_nine_and_counts_agree() -> None:
    guides = [_guide(n) for n in range(1, 12)]  # 11 published guides
    metas = [_meta(g) for g in guides]
    fake = _wing(guides, metas)

    page1 = ls.list_compliance_hub(fake, page=1)
    page2 = ls.list_compliance_hub(fake, page=2)

    assert len(page1["items"]) == ls.HUB_PAGE_SIZE
    assert len(page2["items"]) == 11 - ls.HUB_PAGE_SIZE
    assert page1["total_pages"] == page2["total_pages"] == math.ceil(11 / 9)
    # The counter walks the SAME set the lister paginates — a wall reporting one
    # total while the paginator walks another is the §12.2 failure.
    assert ls.compliance_hub_total_pages(fake) == page1["total_pages"]
    # No card appears on two pages.
    assert not {i["slug"] for i in page1["items"]} & {i["slug"] for i in page2["items"]}


def test_page_past_the_end_is_empty_but_keeps_the_real_total() -> None:
    guides = [_guide(n) for n in range(1, 4)]
    fake = _wing(guides, [_meta(g) for g in guides])

    data = ls.list_compliance_hub(fake, page=9)

    assert data["items"] == []
    assert data["total_pages"] == 1


def test_pagination_floors_at_one_page_when_nothing_matches() -> None:
    """Never 0: the paginator and the CTA wall both read this as "how many pages
    exist", and zero renders as a broken paginator rather than one empty page."""
    fake = _wing([], [])

    assert ls.list_compliance_hub(fake)["total_pages"] == 1
    assert ls.compliance_hub_total_pages(fake) == 1
    assert ls.compliance_hub_total_pages(fake, provider="لا-أحد") == 1


def test_hub_skips_a_guide_with_no_slug() -> None:
    """A guide with no sidecar slug has no public URL, so it cannot be a card —
    and it must not be counted either, or the last page would be short."""
    published = _guide(1)
    draft = _guide(2)
    fake = _wing([published, draft], [_meta(published), _meta(draft, slug=None)])

    data = ls.list_compliance_hub(fake)

    assert [it["title"] for it in data["items"]] == [f"{TITLE} 1"]
    assert data["total_pages"] == 1
    assert ls.compliance_hub_total_pages(fake) == 1


# ---------------------------------------------------------------------------
# Hub card shape
# ---------------------------------------------------------------------------


def test_hub_item_shape_is_the_frontend_contract() -> None:
    fake = _fake()

    (item,) = ls.list_compliance_hub(fake)["items"]

    assert set(item) == {"slug", "title", "provider_name", "summary", "image_count"}
    assert item["slug"] == SLUG
    assert item["title"] == TITLE
    assert item["provider_name"] == GUIDE_ROW["provider_name"]
    assert item["summary"] == GUIDE_ROW["summary"]
    assert item["image_count"] == 3


def test_hub_item_survives_the_response_model() -> None:
    fake = _fake()

    (item,) = ls.list_compliance_hub(fake)["items"]
    dumped = pl.ComplianceHubItem(**item).model_dump()

    assert dumped["image_count"] == 3
    assert "source_pdf_url" not in json.dumps(dumped, ensure_ascii=False)


def test_hub_card_never_carries_the_source_pdf() -> None:
    """The wing lists guides, and a guide's provenance PDF is not a link we
    publish — not on the card, not on the page."""
    fake = _fake()

    body = json.dumps(ls.list_compliance_hub(fake), ensure_ascii=False)

    assert "source_pdf_url" not in body
    assert ".pdf" not in body


def test_hub_never_fetches_the_guide_body() -> None:
    """169 markdown bodies for a 9-card grid that renders none of them."""
    fake = _fake()

    ls.list_compliance_hub(fake)

    view_selects = [c for t, c in fake.selects if t == "library_compliance_v"]
    assert view_selects
    assert all("guide_md" not in cols for cols in view_selects)


# ---------------------------------------------------------------------------
# The guide page
# ---------------------------------------------------------------------------


def test_guide_payload_shape_is_exactly_the_contract() -> None:
    doc = ls.get_compliance_guide(_fake(), SLUG)

    assert set(doc) == {
        "slug",
        "title",
        "summary",
        "provider_name",
        "service_url",
        "image_count",
        "guide_md",
        "images",
        # «اقرأ تاليًا» (read_next_related_items.md §5.1). NOTE what is still
        # absent: `cited_regulations`. تعاميم and خدمات carry no citation data
        # anywhere in the corpus (D14), so the field does not exist on this
        # wing — it is not an empty list.
        "related_next",
    }
    # No `related_items` rows in the fake → an EMPTY STRIP, never an error.
    assert doc["related_next"] == []
    assert doc["slug"] == SLUG
    assert doc["title"] == TITLE
    assert doc["summary"] == GUIDE_ROW["summary"]
    assert doc["provider_name"] == GUIDE_ROW["provider_name"]
    assert doc["service_url"] == GUIDE_ROW["service_url"]
    assert set(doc["images"][0]) == {
        "image_ref",
        "description",
        "url",
        "width",
        "height",
    }


def test_the_source_pdf_url_never_appears_anywhere_in_the_response() -> None:
    """THE hard product decision (plan §0 decision #4), asserted at every layer.

    The guide is our rewrite of the entity's official PDF and that PDF is never
    surfaced; the only outbound link is the entity's own service page. The fixture
    row CARRIES ``source_pdf_url`` on purpose — so this proves the service drops
    it, not merely that the view never offered it.
    """
    doc = ls.get_compliance_guide(_fake(), SLUG)

    assert "source_pdf_url" not in doc
    serialized = json.dumps(doc, ensure_ascii=False)
    assert "source_pdf_url" not in serialized
    assert GUIDE_ROW["source_pdf_url"] not in serialized
    assert ".pdf" not in serialized

    # ...and it cannot be reintroduced through the response model either.
    model_json = pl.ComplianceGuideDoc(**doc).model_dump_json()
    assert "source_pdf_url" not in model_json
    assert "source_pdf_url" not in pl.ComplianceGuideDoc.model_fields

    # ...nor asked for over the wire: the real PostgREST select is the half of
    # this guarantee the fake cannot exercise.
    assert "source_pdf_url" not in ls._COMPLIANCE_DOC_SELECT
    assert "source_pdf_url" not in ls._COMPLIANCE_HUB_SELECT


def test_the_only_outbound_link_is_the_service_page() -> None:
    doc = ls.get_compliance_guide(_fake(), SLUG)

    external = [
        v
        for v in (doc["service_url"],)
        if isinstance(v, str) and v.startswith("http")
    ]
    assert external == [GUIDE_ROW["service_url"]]


def test_images_are_ordered_by_image_index() -> None:
    """Seeded 3,1,2 — the reader imposes the order.

    ``image_index`` is a stable LABEL, not document order (28% of guides place
    their holes out of numeric sequence), so this order is for LISTING only; hole
    resolution still goes through ``image_ref``.
    """
    doc = ls.get_compliance_guide(_fake(), SLUG)

    assert [im["image_ref"] for im in doc["images"]] == [
        "223719_1",
        "223719_2",
        "223719_3",
    ]
    assert ("service_guide_images", "image_index", False) in _fake_orders()


def _fake_orders() -> list[tuple[str, str, bool]]:
    fake = _fake()
    ls.get_compliance_guide(fake, SLUG)
    return fake.orders


def test_image_url_is_the_public_bucket_path() -> None:
    doc = ls.get_compliance_guide(_fake(), SLUG)

    base = f"{get_settings().SUPABASE_URL}/storage/v1/object/public/service-guide-images"
    assert doc["images"][0]["url"] == f"{base}/223719/223719_1.jpeg"
    # Public bucket ⇒ no signature, no expiry, identical bytes for every caller —
    # which is what makes the shared hour-cache on this route safe.
    assert "token=" not in doc["images"][0]["url"]
    assert "/sign/" not in doc["images"][0]["url"]


def test_image_url_base_comes_from_config_not_a_hardcoded_project_ref(
    monkeypatch,
) -> None:
    """A restore into another Supabase project must find its own images."""

    class _S:
        SUPABASE_URL = "https://other-project.supabase.co"

    monkeypatch.setattr(ls, "get_settings", lambda: _S())

    doc = ls.get_compliance_guide(_fake(), SLUG)

    assert doc["images"][0]["url"].startswith(
        "https://other-project.supabase.co/storage/v1/object/public/"
        "service-guide-images/"
    )


def test_image_description_is_the_alt_text() -> None:
    """A real Arabic sentence, never a filename: it is what keeps the guide
    usable with images off entirely."""
    doc = ls.get_compliance_guide(_fake(), SLUG)

    first = doc["images"][0]
    assert first["description"] == IMAGE_ROWS[1]["description"]
    assert first["width"] == 1440 and first["height"] == 900


def test_a_zero_image_guide_is_not_a_failure() -> None:
    """10 of the 169 are legitimately text-only. ``image_count`` is 0 so the
    frontend keeps «الدليل الشامل» and does NOT promise «بالصور»."""
    text_only = {**GUIDE_ROW, "guide_md": "نص فقط، بلا صور.", "image_count": 0}
    fake = FakeSupabase(
        library_compliance_v=[text_only],
        seo_item_meta=[META_ROW],
        service_guide_images=[],
    )

    doc = ls.get_compliance_guide(fake, SLUG)

    assert doc["images"] == []
    assert doc["image_count"] == 0
    assert doc["guide_md"] == "نص فقط، بلا صور."


def test_image_count_reports_what_the_payload_actually_carries() -> None:
    """The corpus counter says 3; the payload ships 3. If they ever disagree the
    payload wins — «بالصور» must be backed by bytes."""
    doc = ls.get_compliance_guide(_fake(), SLUG)

    assert doc["image_count"] == len(doc["images"]) == 3


def test_a_guide_whose_images_vanished_reports_zero_not_the_stale_counter() -> None:
    stale = {**GUIDE_ROW, "image_count": 3}
    fake = FakeSupabase(
        library_compliance_v=[stale],
        seo_item_meta=[META_ROW],
        service_guide_images=[],
    )

    doc = ls.get_compliance_guide(fake, SLUG)

    assert doc["image_count"] == 0
    assert doc["images"] == []


# ---------------------------------------------------------------------------
# Hole hygiene — the failure mode the whole design exists to prevent
# ---------------------------------------------------------------------------


def test_an_unresolvable_hole_is_stripped_from_the_body() -> None:
    """``223719_9`` has no image row, so its LINE goes. A raw token rendered onto
    a user-facing page is THE failure this wing's contract is written around."""
    doc = ls.get_compliance_guide(_fake(), SLUG)

    assert "223719_9" not in doc["guide_md"]
    holes = set(ls._GUIDE_HOLE_RE.findall(doc["guide_md"]))
    assert holes == {"223719_1", "223719_2"}
    # Every surviving hole resolves.
    refs = {im["image_ref"] for im in doc["images"]}
    assert holes <= refs


def test_resolvable_holes_and_surrounding_prose_survive_untouched() -> None:
    doc = ls.get_compliance_guide(_fake(), SLUG)
    body = doc["guide_md"]

    assert "\n223719_1\n" in body
    # «الصورة N» inside prose is NOT an anchor (2,804 live occurrences) and an
    # in-sentence token is not a hole (the regex is whole-line only).
    assert "كما هو موضح في الصورة 1 أدناه." in body
    assert "راجع 223719_2 المذكورة في هذا السطر للمقارنة." in body
    assert "## الخطوة الثالثة" in body


def test_strip_unresolved_holes_is_a_pure_whole_line_rule() -> None:
    md = "\n".join(
        [
            "قبل",
            "223719_1",
            "  223719_2\t",
            "223719_9",
            "نص فيه 223719_9 داخل جملة",
            "بعد",
        ]
    )

    out = ls._strip_unresolved_holes(md, {"223719_1", "223719_2"})

    assert "\n223719_1\n" in out
    assert "223719_2" in out
    assert "نص فيه 223719_9 داخل جملة" in out          # inline ⇒ not a hole
    assert ls._GUIDE_HOLE_RE.findall(out) == ["223719_1", "223719_2"]
    assert out.count("223719_9") == 1                   # only the inline one left


def test_strip_unresolved_holes_drops_every_hole_when_nothing_resolves() -> None:
    md = "أ\n223719_1\nب\n223719_2\nج"

    out = ls._strip_unresolved_holes(md, set())

    assert ls._GUIDE_HOLE_RE.findall(out) == []
    assert "أ" in out and "ب" in out and "ج" in out


def test_no_raw_token_survives_serialization() -> None:
    """End-to-end version of the rule, through the real response model."""
    doc = ls.get_compliance_guide(_fake(), SLUG)

    dumped = pl.ComplianceGuideDoc(**doc).model_dump_json()
    payload = json.loads(dumped)
    unresolved = [
        ref
        for ref in ls._GUIDE_HOLE_RE.findall(payload["guide_md"])
        if ref not in {im["image_ref"] for im in payload["images"]}
    ]
    assert unresolved == []


# ---------------------------------------------------------------------------
# The wing is OPEN
# ---------------------------------------------------------------------------


def test_the_guide_body_is_never_truncated() -> None:
    """No gate, no free-char budget, no placeholder bars. Every byte of the
    guide ships to an anonymous reader — that is the wing's whole SEO bet."""
    long_body = "\n".join(["فقرة طويلة جدا " * 40] * 60)  # ~35k chars
    big = {**GUIDE_ROW, "guide_md": long_body}
    fake = FakeSupabase(
        library_compliance_v=[big], seo_item_meta=[META_ROW], service_guide_images=[]
    )

    doc = ls.get_compliance_guide(fake, SLUG)

    assert doc["guide_md"] == long_body
    assert "is_truncated" not in doc
    assert "gate" not in doc and "gate_effective" not in doc


def test_the_guide_reader_never_resolves_a_gate(monkeypatch) -> None:
    """Pinned by explosion: if someone wires ``resolve_gate`` into this wing the
    test fails loudly instead of the wing silently going gated."""

    def _boom(*_a: Any, **_k: Any):
        raise AssertionError("the /compliance wing must not resolve a gate")

    monkeypatch.setattr(ls, "resolve_gate", _boom)
    monkeypatch.setattr(ls, "truncate_for_gate", _boom)

    assert ls.get_compliance_guide(_fake(), SLUG)["title"] == TITLE
    assert ls.list_compliance_hub(_fake())["items"]


# ---------------------------------------------------------------------------
# Lookups that must fail soft
# ---------------------------------------------------------------------------


def test_unknown_slug_returns_none() -> None:
    assert ls.get_compliance_guide(_fake(), "no-such-guide") is None


def test_blank_slug_returns_none_without_a_query() -> None:
    fake = _fake()

    assert ls.get_compliance_guide(fake, "") is None
    assert ls.get_compliance_guide(fake, "   ") is None
    assert fake.selects == []


def test_a_slug_pointing_at_a_missing_guide_returns_none() -> None:
    """A sidecar row can outlive its guide (the ingest rebuilds the corpus). That
    is a 404, not a 500."""
    fake = FakeSupabase(
        library_compliance_v=[],
        seo_item_meta=[META_ROW],
        service_guide_images=IMAGE_ROWS,
    )

    assert ls.get_compliance_guide(fake, SLUG) is None


def test_a_db_failure_raises_the_arabic_500() -> None:
    fake = _fake()
    fake.missing.add("library_compliance_v")

    with pytest.raises(LunaHTTPException) as exc:
        ls.get_compliance_guide(fake, SLUG)

    assert exc.value.status_code == 500
    assert exc.value.detail == "حدث خطأ أثناء جلب الدليل"


# ---------------------------------------------------------------------------
# Cross-wing counts
# ---------------------------------------------------------------------------


def test_sector_counts_include_the_guides_and_ignore_the_rpc_column() -> None:
    """The RPC's ``compliance`` column counts the ``services`` corpus (4,746
    rows of procedures this wing does not publish), so it must never be read for
    this wing. The fake seeds it absurd; seeing 9_999 means the wrong source won.
    """
    inside = _guide(1, sectors=[SECTOR_AR])
    outside = _guide(2, sectors=["الطاقة"])
    fake = _wing([inside, outside], [_meta(inside), _meta(outside)])
    fake.rpc_rows = [
        {
            "sector": SECTOR_AR,
            "regulations": 693,
            "judgments": 18879,
            "compliance": 9_999,
            "circulars": 162,
        }
    ]

    counts = ls.sector_counts(fake)

    assert counts[SECTOR_SLUG]["compliance"] == 1
    assert counts[SECTOR_SLUG]["regulations"] == 693
    assert all(c["compliance"] != 9_999 for c in counts.values())


def test_rpc_excluded_sections_are_named_not_guessed() -> None:
    assert "compliance" in ls._RPC_SECTOR_COUNT_EXCLUDED


def test_corpus_counts_size_the_compliance_tab() -> None:
    guides = [_guide(n) for n in range(1, 5)]
    metas = [_meta(g) for g in guides[:3]]  # the 4th is unpublished
    fake = _wing(guides, metas)

    assert ls.library_corpus_counts(fake)["compliance"] == 3


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


def test_guide_route_serves_the_payload_with_the_shared_hour_cache() -> None:
    """The body is identical for every caller (no tier decides a byte of it, and
    the image URLs are unsigned), so the anon hour-cache is correct here."""
    with TestClient(_app(_fake())) as client:
        res = client.get(f"/api/v1/public/library/compliance/{SLUG}")

    assert res.status_code == 200
    assert res.headers["Cache-Control"] == pl._LIBRARY_CACHE_CONTROL
    body = res.json()
    assert body["slug"] == SLUG
    assert body["image_count"] == 3
    assert len(body["images"]) == 3
    assert "source_pdf_url" not in res.text


def test_guide_route_404s_in_arabic() -> None:
    with TestClient(_app(_fake())) as client:
        res = client.get("/api/v1/public/library/compliance/no-such-guide")

    assert res.status_code == 404
    assert "الدليل غير موجود" in res.text


def test_hub_route_ships_image_count_to_an_anonymous_caller() -> None:
    with TestClient(_app(_fake())) as client:
        res = client.get("/api/v1/public/library/compliance")

    assert res.status_code == 200
    body = res.json()
    assert body["items"][0]["image_count"] == 3
    assert body["items"][0]["slug"] == SLUG
