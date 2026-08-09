"""«الجهة القضائية» — the court axis of /judgments as a SECTION, not a filter.

Plan: ``.claude/plans/library_court_sections_publish_ramp.md`` §0 (the blocker) ·
§1.2 (the lister rewire) · §2.2 (the closed vocabulary) · §2.3 (the backend) ·
§5 (success criteria). Migrations 123 (`library_judgments_ranked`) and 124
(`library_sector_counts_published`) are the DB half.

TWO THINGS ARE UNDER TEST HERE AND BOTH FAIL SILENTLY IF THEY REGRESS.

1. §2.3.3 — ``court`` MUST STAY OUT OF THE ``filtered`` FLAG. ``filtered`` drives
   the enumeration-oracle clamp (``_wall_total_pages`` / ``_visible_total_pages``),
   which pins an anonymous caller's ``total_pages`` to 2. A closed, server-owned
   12-value vocabulary yields 12 FIXED numbers that move only when the corpus
   does — the same argument that made a validated sector a section on 2026-08-01
   — so it is not an oracle and it gets real counts. Put ``court`` in that flag
   and every court page prints «1 2» over a 20,335-judgment section, with nothing
   erroring and page 1 still rendering perfectly. Hence
   ``test_an_anon_court_wall_reports_the_REAL_page_count`` and its served-page
   twin: those two ARE success criterion §5.1.

2. §0/§1.2 — THE LISTER READS THE PUBLISHED RELATION. Until migration 123 the
   hub paginated the CORPUS above ``SAMPLE_MODE_MAX_IDS`` and dropped unslugged
   rows AFTER paging, so at ~10,000 of 30,531 published a nine-card page would
   have rendered about three cards over ~3,393 mostly-empty pages. The same bug
   shipped once already on /regulations (503 published crossed the then-300
   ceiling; prod returned ``items: 0``). ``test_a_court_page_is_never_short`` and
   ``test_the_hub_never_reads_the_corpus_table`` pin the fix.

The vocabulary itself is IMPORTED from ``shared/library/courts.py`` throughout —
never retyped. Several raw ``cases.court`` values differ only by a city name or
(row 9) by an invisible double space, so a retyped string is a bucket that
silently matches nothing.

Fixture style is the access-tiers / sector-wing one: an in-memory PostgREST
stand-in holding real rows, so tier resolution and the filters are the real
thing. ⚠ ``test_library_judgments.py`` is NOT importable from here — it is
local-only (``.gitignore``), so its richer fake is re-declared in miniature
below rather than imported.
"""
from __future__ import annotations

import math
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import public_library as pl
from backend.app.deps import get_current_user_optional, get_supabase
from backend.app.errors import LunaHTTPException, luna_exception_handler
from backend.app.middleware import rate_limit
from backend.app.middleware.route_limits import library_rate_limit
from backend.app.services import case_service, library_service as ls
from shared.library.courts import (
    COURT_LABELS,
    COURT_ORDER,
    COURT_SLUG_VOCAB,
    COURT_VARIANTS,
    RESERVED_COURT_SLUGS,
)
from shared.library.sectors import SECTOR_SLUGS

from backend.tests.test_library_gating import (  # noqa: F401
    USER,
    FakeSupabase,
    quota_row,
)

AUTH_ID = "auth-0000-1111"

HUB = "/api/v1/public/library"
JUD_HUB = f"{HUB}/judgments"
COURTS = f"{HUB}/judgments/courts"

# The biggest bucket and the most-variants bucket, both picked FROM the map so
# that reordering or re-splitting it cannot leave this file asserting against a
# court that no longer exists.
COMMERCIAL = COURT_ORDER[0]                       # المحكمة التجارية — 20,335 rows
MULTI_VARIANT = max(COURT_ORDER, key=lambda s: len(COURT_VARIANTS[s]))
SINGLE_VARIANT = next(s for s in COURT_ORDER if len(COURT_VARIANTS[s]) == 1)

# A raw sector name, for the "two sections together are filtered again" case.
SECTOR_SLUG = "commercial-transactions"

# Stubbed PUBLISHED counts per court. Deliberately NOT the corpus numbers in
# ``courts.py``'s comments: the wing publishes a subset, and every number the
# switcher prints has to describe what a paginator can actually walk.
COURT_PUBLISHED = {slug: 0 for slug in COURT_ORDER}
COURT_PUBLISHED[COMMERCIAL] = 6142
COURT_PUBLISHED[COURT_ORDER[1]] = 1000
COURT_PUBLISHED[COURT_ORDER[-1]] = 0  # المحكمة العمالية — 35 corpus-wide, may publish none

COURT_PAGES = {
    slug: (max(1, math.ceil(n / 9)) if n else 1) for slug, n in COURT_PUBLISHED.items()
}

# What the stubbed lister/counter report for any request — the "real" total a
# SECTION is entitled to see.
TRUE_TOTAL_PAGES = 40


class _User:
    """Stands in for AuthUser — the routes only ever read ``auth_id``."""

    auth_id = AUTH_ID
    email = "lawyer@example.com"
    role = "authenticated"


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """Module-level TTL caches + the route limiter's process-global fallback.
    The court memo is the point of several tests below, so a leak between tests
    would make them assert nothing."""
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()
    pl._total_pages_memo.clear()
    pl._reset_sector_memos()
    library_rate_limit._fallback.reset()
    yield
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()
    pl._total_pages_memo.clear()
    pl._reset_sector_memos()
    library_rate_limit._fallback.reset()


@pytest.fixture(autouse=True)
def _map_auth_id_to_user_id(monkeypatch):
    monkeypatch.setattr(
        case_service,
        "get_user_id",
        lambda supabase, auth_id: USER if auth_id == AUTH_ID else None,
    )


def _app(supabase: Any, user: Optional[_User] = None) -> FastAPI:
    app = FastAPI()
    app.state.redis = None
    app.add_exception_handler(LunaHTTPException, luna_exception_handler)
    app.include_router(pl.router)
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[get_current_user_optional] = lambda: user
    app.dependency_overrides[library_rate_limit] = lambda: None
    return app


def _hub_fake(**qrow: Any) -> FakeSupabase:
    fake = FakeSupabase()
    fake.quota_row = quota_row(**qrow) if qrow else quota_row()
    return fake


def _client(supabase: Any = None, user: Optional[_User] = None) -> TestClient:
    fake = supabase if supabase is not None else _hub_fake()
    return TestClient(_app(fake, user), client=("8.8.8.8", 51000))


def _stub_item(n: int) -> dict[str, Any]:
    return {
        "slug": f"judgment-{n}",
        "title": "حكم",
        "court": "التجارية",
        "court_slug": COMMERCIAL,
        "snippet": "",
    }


class _Exploding:
    """Any DB touch is a test failure."""

    def table(self, *_a: Any, **_k: Any):
        raise AssertionError("the database was touched")

    def rpc(self, *_a: Any, **_k: Any):
        raise AssertionError("the database was touched")


@pytest.fixture
def stubs(monkeypatch):
    """Stub the judgments lister + counter and the court-count reader.

    Returns a dict recording what was called with what — the WIRING is under
    test here, not the SQL. ``calls['court_counts']`` is how the memo tests count
    refreshes.
    """
    calls: dict[str, Any] = {"listers": [], "counter": 0, "court_counts": 0}

    def _lister(_supabase: Any, **kw: Any) -> dict[str, Any]:
        calls["listers"].append(kw)
        return {
            "items": [_stub_item(i) for i in range(9)],
            "page": int(kw.get("page") or 1),
            "total_pages": TRUE_TOTAL_PAGES,
        }

    def _counter(*_a: Any, **_k: Any) -> int:
        calls["counter"] += 1
        return TRUE_TOTAL_PAGES

    def _court_counts(_supabase: Any) -> dict[str, int]:
        calls["court_counts"] += 1
        return dict(COURT_PUBLISHED)

    monkeypatch.setattr(ls, "list_judgments_hub", _lister)
    monkeypatch.setattr(ls, "judgments_hub_total_pages", _counter)
    monkeypatch.setattr(ls, "court_counts", _court_counts)
    return calls


def _is_arabic_refusal(res, status: int = 400) -> None:
    """The project's standard envelope, an Arabic message, nothing cacheable."""
    assert res.status_code == status, res.text
    body = res.json()
    assert body["error"]["status"] == status
    message = body["error"]["message"]
    assert message == body["detail"]
    assert message and not any("a" <= ch.lower() <= "z" for ch in message), message
    assert any("؀" <= ch <= "ۿ" for ch in message), message


# ===========================================================================
# 1. §2.2 / §12.7 — the vocabulary resolves IN MEMORY, before any DB work
# ===========================================================================


def test_every_one_of_the_twelve_slugs_resolves() -> None:
    """The map is IMPORTED, never retyped: a 13th bucket added to
    ``shared/library/courts.py`` must not need a second edit in the API layer to
    become servable."""
    assert len(COURT_ORDER) == 12
    for slug in COURT_ORDER:
        variants, key = pl._court_section(slug)
        assert key == slug
        assert variants, slug


def test_the_resolver_returns_the_maps_own_variant_tuple() -> None:
    """⚠ EXACT MATCHING IS THE WHOLE DESIGN (§2.2). ``cases.court`` is free text
    — 30 distinct values, several differing only by a city — so the predicate is
    ``in.(variants)`` and never a LIKE/regex. An identity check, not equality:
    the route must hand the service the map's own tuple, not a copy some
    normalisation step has been through."""
    variants, _ = pl._court_section(MULTI_VARIANT)
    assert variants is COURT_VARIANTS[MULTI_VARIANT]
    # The multi-variant bucket is the city collapse the user asked for: several
    # raw strings, ONE section, and the label never names a city.
    assert len(variants) > 1
    assert len(set(variants)) == len(variants)
    assert "مدينة" not in COURT_LABELS[MULTI_VARIANT]


def test_a_single_variant_bucket_is_still_a_tuple_not_a_string() -> None:
    """``in.("ديوان المظالم — الدائرة الجزائية")`` and ``in.(د,ي,و,...)`` are one
    typo apart: a bare string is iterable, so a bucket flattened into characters
    would match nothing and raise nothing."""
    variants, _ = pl._court_section(SINGLE_VARIANT)
    assert isinstance(variants, tuple)
    assert len(variants) == 1


@pytest.mark.parametrize("reserved", sorted(RESERVED_COURT_SLUGS))
def test_a_reserved_segment_is_never_a_court(reserved) -> None:
    """``/judgments/courts/page/{n}`` is the paginator and must never resolve as
    a court in either namespace. Enforced in the BACKEND as well as the Next
    route table, so the two cannot drift."""
    assert reserved not in COURT_SLUG_VOCAB
    with pytest.raises(LunaHTTPException) as exc:
        pl._court_section(reserved)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("bogus", ["zzz", "commercial", "المحكمة التجارية", "%2e%2e", "*"])
def test_an_unknown_court_is_refused(stubs, bogus) -> None:
    res = _client().get(JUD_HUB, params={"court": bogus})
    _is_arabic_refusal(res)
    assert res.json()["detail"] == pl.MSG_INVALID_COURT


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_court_is_not_a_filter_and_not_an_error(stubs, blank) -> None:
    """An unscoped hub is the NORMAL case — it is what the ISR renderer asks
    for."""
    assert _client().get(JUD_HUB, params={"court": blank}).status_code == 200
    assert pl._court_section(blank) == (None, None)


@pytest.mark.parametrize("bad", ["zzz", *sorted(RESERVED_COURT_SLUGS)])
def test_a_rejected_court_never_reaches_the_database(stubs, bad) -> None:
    """§12.7 — validation runs before tier resolution and before any query, so
    probing the 12-value namespace costs a dict lookup. A DB round-trip per probe
    would make the 400 its own load generator."""
    assert _client(_Exploding()).get(
        JUD_HUB, params={"court": bad}
    ).status_code == 400


def test_a_court_rejection_is_never_shared_cached(stubs) -> None:
    """A 400 parked in the hour-cache would serve that refusal to everyone who
    asks for the same court until it expired."""
    res = _client().get(JUD_HUB, params={"court": "zzz"})
    assert res.headers["cache-control"] == "private, no-store"


def test_every_slug_is_accepted_by_the_hub(stubs) -> None:
    client = _client()
    for slug in COURT_ORDER:
        assert client.get(JUD_HUB, params={"court": slug}).status_code == 200, slug


def test_the_slug_reaches_the_service_as_RAW_COURT_VARIANTS(stubs) -> None:
    """The corpus column stores the raw court strings; the slug is a URL
    affordance only. Passing the slug through to ``in.()`` would match zero rows
    on every court page at once — the failure mode that hit the sector axis."""
    _client().get(JUD_HUB, params={"court": MULTI_VARIANT})
    assert stubs["listers"][-1]["court_variants"] == COURT_VARIANTS[MULTI_VARIANT]


# ===========================================================================
# 2. §2.3.3 — a validated court is a SECTION: real counts, unchanged depth caps
#
# ⚠ THIS BLOCK IS SUCCESS CRITERION §5.1. If ``court`` ever lands in the
# ``filtered`` flag these are the tests that fail, and nothing else will.
# ===========================================================================


def test_an_anon_court_wall_reports_the_REAL_page_count(stubs) -> None:
    """Under the FILTERED rule this body would carry ``_ANON_WALL_TOTAL_PAGES``
    (2) — «1 2» over 6,142 published judgments. The count is one of 12 fixed
    numbers over a closed, server-validated vocabulary: it steers with nothing,
    so anon gets it."""
    body = _client().get(JUD_HUB, params={"page": 2, "court": COMMERCIAL}).json()

    assert body["cap_reached"] is True
    assert body["items"] == []
    assert body["total_pages"] == COURT_PAGES[COMMERCIAL]


def test_an_anon_caller_can_page_past_two_on_a_court_route(stubs) -> None:
    """THE INVARIANT, stated as bluntly as it deserves: the paginator an
    anonymous reader is handed must reach past page 2. Both the served body and
    the wall body carry a real total — clamping only one of them was never a fix,
    because page 1 leaks the same number at the same granularity."""
    served = _client().get(JUD_HUB, params={"page": 1, "court": COMMERCIAL}).json()
    wall = _client().get(JUD_HUB, params={"page": 2, "court": COMMERCIAL}).json()

    assert served["total_pages"] > pl._ANON_WALL_TOTAL_PAGES
    assert wall["total_pages"] > pl._ANON_WALL_TOTAL_PAGES
    assert served["total_pages"] == TRUE_TOTAL_PAGES
    assert wall["total_pages"] == COURT_PAGES[COMMERCIAL]


def test_a_SERVED_court_page_reports_the_listers_real_total(stubs) -> None:
    body = _client().get(JUD_HUB, params={"page": 1, "court": COMMERCIAL}).json()
    assert body["cap_reached"] is False
    assert body["total_pages"] == TRUE_TOTAL_PAGES


def test_a_court_COMBINED_with_a_filter_is_filtered_again(stubs) -> None:
    """The section is the base set; ``court_level`` on top of it is still a probe
    whose answer moves with attacker-chosen input."""
    body = _client().get(
        JUD_HUB, params={"page": 2, "court": COMMERCIAL, "court_level": "appeal"}
    ).json()
    assert body["total_pages"] == pl._ANON_WALL_TOTAL_PAGES


def test_a_court_COMBINED_with_a_sector_is_filtered_again(stubs) -> None:
    """⚠ TWO SECTIONS MULTIPLY. 12 courts × 38 sectors = 456 combinations, which
    is 456 numbers that are neither memoised nor memoisable at this TTL — an
    unmemoised count on the anon path is exactly the round-trip §2.1 removed.
    Same rule as «a sector combined with doc_type»."""
    body = _client().get(
        JUD_HUB, params={"page": 2, "court": COMMERCIAL, "sector_slug": SECTOR_SLUG}
    ).json()
    assert body["total_pages"] == pl._ANON_WALL_TOTAL_PAGES


def test_a_SERVED_court_plus_a_filter_page_is_still_clamped(stubs) -> None:
    body = _client().get(
        JUD_HUB, params={"page": 1, "court": COMMERCIAL, "court_level": "appeal"}
    ).json()
    assert body["total_pages"] == pl._ANON_WALL_TOTAL_PAGES


def test_a_court_plus_an_anonymous_q_stays_a_SECTION(stubs) -> None:
    """D9 drops an anonymous ``q``, which leaves a request whose only narrowing
    is a validated section — so it keeps the real count. Anything else would let
    a shared search link make a court page look eighteen items deep."""
    body = _client().get(
        JUD_HUB, params={"page": 2, "court": COMMERCIAL, "q": "نظام"}
    ).json()
    assert body["total_pages"] == COURT_PAGES[COMMERCIAL]


def test_the_anon_DEPTH_cap_is_untouched_by_the_court_axis(stubs) -> None:
    """A section changes what the wall SAYS, never where it stands. Page 2 is
    still a wall for anon, with zero items, on a court page exactly as
    anywhere else."""
    body = _client().get(JUD_HUB, params={"page": 2, "court": COMMERCIAL}).json()
    assert body["cap_reached"] is True
    assert body["items"] == []
    assert body["max_page"] == ls.ANON_HUB_MAX_PAGE == 1


def test_a_free_account_still_stops_at_page_three_on_a_court_page(stubs) -> None:
    body = _client(_hub_fake(plan="free", limit=10), _User()).get(
        JUD_HUB, params={"page": 4, "court": COMMERCIAL}
    ).json()
    assert body["cap_reached"] is True
    assert body["max_page"] == ls.FREE_HUB_MAX_PAGE == 3


def test_an_authed_wall_uses_the_real_counter_not_the_memo(stubs) -> None:
    """An identity-bearing caller is metered by the item budget, so their wall
    runs the wing's own counter with the court applied — the memo exists for the
    anon path, which has no identity to meter. (A PAID caller is unbounded and
    never walls at all, which is why this probes the free cap.)"""
    _client(_hub_fake(plan="free", limit=10), _User()).get(
        JUD_HUB, params={"page": 4, "court": COMMERCIAL}
    )
    assert stubs["counter"] == 1
    assert stubs["court_counts"] == 0


def test_an_unfiltered_wall_still_uses_the_per_section_counter(stubs) -> None:
    """The court branch must not have stolen the existing one. No court → the
    wing's own counter, memoised by section, exactly as before."""
    body = _client().get(JUD_HUB, params={"page": 2}).json()
    assert body["total_pages"] == TRUE_TOTAL_PAGES
    assert stubs["counter"] == 1
    assert stubs["court_counts"] == 0


def test_an_empty_court_section_reports_one_page_not_zero(stubs) -> None:
    """المحكمة العمالية holds 35 judgments corpus-wide and may publish none. A
    zero-page paginator is a rendering bug; the listers already return 1 for an
    empty set."""
    empty = COURT_ORDER[-1]
    assert COURT_PUBLISHED[empty] == 0
    body = _client().get(JUD_HUB, params={"page": 2, "court": empty}).json()
    assert body["total_pages"] == 1


# ===========================================================================
# 3. §2.3.4 — GET /public/library/judgments/courts
# ===========================================================================


def test_the_courts_endpoint_lists_all_twelve_in_the_servers_order(stubs) -> None:
    """ORDER IS THE SERVER'S: corpus volume descending, i.e. ``COURT_ORDER``
    insertion order. Alphabetical would bury المحكمة التجارية (20,335) under
    المحكمة العامة (69), and the frontend renders the list as given."""
    body = _client().get(COURTS).json()
    assert [c["slug"] for c in body["courts"]] == COURT_ORDER
    assert [c["label"] for c in body["courts"]] == [COURT_LABELS[s] for s in COURT_ORDER]
    assert len(body["courts"]) == 12


def test_a_court_tile_carries_its_published_count_and_page_count(stubs) -> None:
    tile = next(c for c in _client().get(COURTS).json()["courts"] if c["slug"] == COMMERCIAL)
    assert tile == {
        "slug": COMMERCIAL,
        "label": COURT_LABELS[COMMERCIAL],
        "count": COURT_PUBLISHED[COMMERCIAL],
        "total_pages": COURT_PAGES[COMMERCIAL],
    }


def test_the_switcher_count_and_the_wall_count_agree(stubs) -> None:
    """⚠ §12.2. One number, two surfaces: a switcher saying «٦٬١٤٢» beside a
    paginator that ends at a different page is a contradiction a reader sees
    inside one session. Both read the same memo."""
    tile = next(c for c in _client().get(COURTS).json()["courts"] if c["slug"] == COMMERCIAL)
    wall = _client().get(JUD_HUB, params={"page": 2, "court": COMMERCIAL}).json()
    assert tile["total_pages"] == wall["total_pages"]


def test_the_court_counts_are_memoised(stubs) -> None:
    """12 count queries per request on the anon path is the round-trip §2.1
    removed. One refresh fills every entry of both memos."""
    client = _client()
    for _ in range(3):
        client.get(COURTS)
    for slug in COURT_ORDER[:4]:
        client.get(JUD_HUB, params={"page": 2, "court": slug})

    assert stubs["court_counts"] == 1
    assert pl._court_total_pages_memo == COURT_PAGES


def test_an_expired_court_memo_refreshes(stubs) -> None:
    client = _client()
    client.get(COURTS)
    pl._court_memo_at["at"] -= pl._TOTAL_PAGES_TTL_SECONDS + 1
    client.get(COURTS)
    assert stubs["court_counts"] == 2


def test_the_court_memo_is_never_handed_out_by_reference(stubs) -> None:
    """A handler mutating the returned dict would corrupt what every other
    request reads for the rest of the TTL (the F5 fix on the sector memo)."""
    import asyncio

    _client().get(COURTS)
    snapshot = asyncio.run(pl._court_counts(_hub_fake()))
    snapshot[COMMERCIAL] = -1
    snapshot.pop(COURT_ORDER[1], None)

    assert pl._court_counts_memo[COMMERCIAL] == COURT_PUBLISHED[COMMERCIAL]
    assert COURT_ORDER[1] in pl._court_counts_memo


def test_the_courts_route_is_not_shadowed_by_the_document_route(stubs) -> None:
    """⚠ FastAPI matches in DECLARATION order. Declared after
    ``/judgments/{slug}``, this path would be swallowed by the document route and
    answer 404 «الحكم غير موجود» — a 404 on the endpoint the whole switcher is
    built from."""
    paths = [getattr(r, "path", "") for r in _app(_hub_fake()).routes]
    assert paths.index("/api/v1/public/library/judgments/courts") < paths.index(
        "/api/v1/public/library/judgments/{slug}"
    )

    res = _client().get(COURTS)
    assert res.status_code == 200
    assert "courts" in res.json()


@pytest.mark.parametrize("reserved", sorted(RESERVED_COURT_SLUGS))
def test_a_reserved_segment_under_courts_404s_without_touching_the_database(
    stubs, reserved
) -> None:
    """``/judgments/courts/page`` is the frontend's paginator segment. The
    backend hosts no such route, so it must 404 outright — never fall through to
    a lookup, and never resolve as a judgment slug."""
    res = _client(_Exploding()).get(f"{COURTS}/{reserved}")
    assert res.status_code == 404, res.text


def test_the_courts_list_keeps_the_shared_hour_cache_for_anon(stubs) -> None:
    res = _client().get(COURTS)
    assert res.headers["cache-control"] == "public, max-age=3600"
    assert "Authorization" in res.headers.get("vary", "")


def test_an_authed_courts_list_is_never_shared_cached(stubs) -> None:
    """⚠ The correctness property of the tier design: an authed body left in the
    shared hour-cache is replayed to the next anonymous visitor. A counts
    endpoint is not exempt."""
    res = _client(_hub_fake(plan="pro", limit=100), _User()).get(COURTS)
    assert res.headers["cache-control"] == "private, no-store"


def test_the_courts_route_shares_the_judgments_item_rate_limit_bucket() -> None:
    """It must not buy a caller a SECOND budget alongside the judgment document
    pages — the same reason the sector item route was collapsed (F2)."""
    normalized = rate_limit.normalize_rate_limit_path(COURTS)
    assert normalized == f"{HUB}/judgments/{rate_limit.ITEM_PLACEHOLDER}"
    assert rate_limit.is_public_library_item_path(normalized)


def test_the_courts_endpoint_yields_no_items_so_it_is_not_metered(stubs, monkeypatch) -> None:
    charged: list[Any] = []

    async def _charge(_request, _user_id, keys, **_kw):
        charged.extend(list(keys))

    monkeypatch.setattr(pl.library_budget, "charge_items", _charge)
    _client(_hub_fake(plan="pro", limit=100), _User()).get(COURTS)
    assert charged == []


# ===========================================================================
# 4. The service layer — the predicate, the relation, and the short page
#
# A miniature in-memory PostgREST. ``test_library_judgments.py`` has a fuller
# one, but that file is local-only (.gitignore), so importing it would make this
# suite un-runnable in CI.
# ===========================================================================


class _Result:
    def __init__(self, data: Any, count: Optional[int] = None) -> None:
        self.data = data
        self.count = count


class _Chain:
    def __init__(self, fake: "_ViewFake", table: str) -> None:
        self._fake = fake
        self._table = table
        self._filters: list[tuple[str, str, Any]] = []
        self._orders: list[tuple[str, bool, Optional[bool]]] = []
        self._range: Optional[tuple[int, int]] = None
        self._limit: Optional[int] = None
        self._count: Optional[str] = None

    def select(self, *_cols: Any, count: Optional[str] = None, **_k: Any) -> "_Chain":
        self._count = count
        return self

    def eq(self, col: str, val: Any) -> "_Chain":
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col: str, vals: list[Any]) -> "_Chain":
        vals = list(vals)
        self._fake.in_calls.append((self._table, col, vals))
        self._filters.append(("in", col, vals))
        return self

    def contains(self, col: str, vals: list[Any]) -> "_Chain":
        self._filters.append(("contains", col, list(vals)))
        return self

    def ilike(self, col: str, pattern: str) -> "_Chain":  # pragma: no cover
        raise AssertionError("the court predicate must be in.(), never a LIKE")

    like = ilike

    def order(
        self,
        col: str,
        *,
        desc: bool = False,
        nullsfirst: Optional[bool] = None,
        **_k: Any,
    ) -> "_Chain":
        self._orders.append((col, desc, nullsfirst))
        self._fake.orders.append((self._table, col, desc, nullsfirst))
        return self

    def range(self, start: int, end: int) -> "_Chain":
        self._range = (start, end)
        return self

    def limit(self, n: int) -> "_Chain":
        self._limit = n
        return self

    def _matches(self, row: dict[str, Any]) -> bool:
        for op, col, val in self._filters:
            cell = row.get(col)
            if op == "eq":
                if cell is None or str(cell) != str(val):
                    return False
            elif op == "in":
                if cell is None or str(cell) not in {str(v) for v in val}:
                    return False
            elif op == "contains":
                if not set(map(str, val)) <= set(map(str, cell or [])):
                    return False
        return True

    def execute(self) -> _Result:
        self._fake.reads.append(self._table)
        rows = [
            dict(r) for r in self._fake.tables.get(self._table, []) if self._matches(r)
        ]
        # Postgres NULL defaults (ASC → NULLS LAST, DESC → NULLS FIRST) unless
        # nullsfirst was passed explicitly — which is the bug this reproduces.
        for col, desc, nullsfirst in reversed(self._orders):
            nf = desc if nullsfirst is None else nullsfirst
            non_null = [r for r in rows if r.get(col) is not None]
            nulls = [r for r in rows if r.get(col) is None]
            non_null.sort(key=lambda r: str(r.get(col)), reverse=desc)
            rows = (nulls + non_null) if nf else (non_null + nulls)

        count = len(rows) if self._count == "exact" else None
        if self._range is not None:
            start, end = self._range
            rows = rows[start : end + 1]
        if self._limit is not None and self._count != "exact":
            rows = rows[: self._limit]
        return _Result(rows, count)


class _ViewFake:
    """Row-backed fake over ``library_judgments_ranked``."""

    def __init__(self, **tables: list[dict[str, Any]]) -> None:
        self.tables = {k: list(v) for k, v in tables.items()}
        self.in_calls: list[tuple[str, str, list[Any]]] = []
        self.orders: list[tuple[str, str, bool, Optional[bool]]] = []
        self.reads: list[str] = []

    def table(self, name: str) -> _Chain:
        assert name != "cases", (
            "the /judgments hub must read library_judgments_ranked, not the "
            "corpus — reading `cases` is the ~3-cards-per-page bug (§0)"
        )
        return _Chain(self, name)

    def rpc(self, *_a: Any, **_k: Any):  # pragma: no cover
        raise AssertionError("browse mode must issue no RPC")


def _view_row(n: int, *, court: str, date: Optional[str] = None) -> dict[str, Any]:
    return {
        "id": f"{n:08d}-0000-0000-0000-000000000000",
        "case_ref": f"17642_fi_{n}",
        "court": court,
        "court_level": "first_instance",
        "city": "الرياض",
        "case_number": str(n),
        "judgment_number": None,
        "date_hijri": "15 ربيع الأول 1445",
        "date_gregorian": date,
        "legal_domains": ["المعاملات التجارية"],
        "short_summary": f"- نزاع رقم {n}.\n- قضت المحكمة برفض الدعوى.",
        "summary": None,
        "facts": "وقائع",
        "ruling": "منطوق",
        "slug": f"حكم-{n}",
    }


def test_the_court_predicate_is_an_exact_in_never_a_pattern() -> None:
    """Unit level: the filter must land on ``court``, as ``in.()``, with the
    map's variants verbatim. ``_Chain.ilike`` raises, so a LIKE is a test
    failure rather than a slow drift."""
    fake = _ViewFake(library_judgments_ranked=[])
    qb = fake.table("library_judgments_ranked").select("id")
    ls._apply_judgment_filters(qb, None, None, court_variants=COURT_VARIANTS[MULTI_VARIANT])

    assert fake.in_calls == [
        ("library_judgments_ranked", "court", list(COURT_VARIANTS[MULTI_VARIANT]))
    ]


@pytest.mark.parametrize("empty", [None, (), []])
def test_an_absent_court_is_a_no_op(empty) -> None:
    """Only value-vs-``None`` distinguishes "no court asked for". A no-op here is
    what keeps the unfiltered hub — the ISR renderer's request — one query."""
    fake = _ViewFake(library_judgments_ranked=[])
    qb = fake.table("library_judgments_ranked").select("id")
    ls._apply_judgment_filters(qb, None, None, court_variants=empty)
    assert fake.in_calls == []


def _mixed_court_fake() -> _ViewFake:
    """25 published judgments in the commercial bucket + 4 elsewhere."""
    rows = [
        _view_row(i, court=COURT_VARIANTS[COMMERCIAL][0], date=f"2024-01-{i:02d}")
        for i in range(1, 26)
    ]
    rows += [
        _view_row(90 + i, court=COURT_VARIANTS[SINGLE_VARIANT][0], date="2023-05-05")
        for i in range(4)
    ]
    return _ViewFake(library_judgments_ranked=rows)


def test_the_hub_never_reads_the_corpus_table() -> None:
    """⚠ §0. ``_ViewFake.table`` asserts on ``cases``: paginating the corpus and
    dropping unslugged rows afterwards is the bug that would render ~3 cards per
    page at a 10,000-of-30,531 publish."""
    fake = _mixed_court_fake()
    ls.list_judgments_hub(fake, page=1, court_variants=COURT_VARIANTS[COMMERCIAL])
    assert set(fake.reads) == {"library_judgments_ranked"}


def test_a_court_page_is_never_short() -> None:
    """Every row in the relation is published by construction, so a full page is
    a full page: 9 + 9 + 7 over 25 rows, no overlap, no holes."""
    fake = _mixed_court_fake()
    variants = COURT_VARIANTS[COMMERCIAL]
    pages = [
        ls.list_judgments_hub(fake, page=n, court_variants=variants) for n in (1, 2, 3)
    ]

    assert [len(p["items"]) for p in pages] == [9, 9, 7]
    assert all(p["total_pages"] == 3 for p in pages)
    slugs = [i["slug"] for p in pages for i in p["items"]]
    assert len(slugs) == len(set(slugs)) == 25


def test_the_court_section_excludes_every_other_court() -> None:
    fake = _mixed_court_fake()
    out = ls.list_judgments_hub(fake, page=1, court_variants=COURT_VARIANTS[SINGLE_VARIANT])
    assert len(out["items"]) == 4
    assert {i["court_slug"] for i in out["items"]} == {SINGLE_VARIANT}


def test_the_card_carries_the_court_slug_for_the_pill_link() -> None:
    """30 raw values map onto 12 buckets and the frontend cannot derive that, so
    the backend hands it over — otherwise the court pill is dead text."""
    fake = _mixed_court_fake()
    item = ls.list_judgments_hub(fake, page=1)["items"][0]
    assert item["court_slug"] == COMMERCIAL
    assert item["court"] in COURT_VARIANTS[COMMERCIAL]


def test_an_unclaimed_court_value_renders_without_a_slug() -> None:
    """One ``court = ''`` row exists in the corpus and a new source feed would
    add more. It stays reachable through the unfiltered hub, with a null slug so
    the pill renders as plain text rather than a broken link."""
    fake = _ViewFake(
        library_judgments_ranked=[_view_row(1, court="جهة لم تُصنَّف", date="2024-01-01")]
    )
    item = ls.list_judgments_hub(fake, page=1)["items"][0]
    assert item["court_slug"] is None
    assert item["court"] == "جهة لم تُصنَّف"


def test_the_hub_orders_newest_first_with_dateless_judgments_last() -> None:
    """⚠ ``nullsfirst=False`` IS LOAD-BEARING: Postgres puts NULLs FIRST on DESC
    by default, which would open the wing with the ~11,400 dateless
    ديوان/زكاة/تأمين rows. The fake reproduces the Postgres default, so dropping
    the flag fails here."""
    fake = _ViewFake(
        library_judgments_ranked=[
            _view_row(1, court="التجارية", date=None),
            _view_row(2, court="التجارية", date="2021-01-01"),
            _view_row(3, court="التجارية", date="2024-05-05"),
        ]
    )
    out = ls.list_judgments_hub(fake, page=1)
    assert [i["date_gregorian"] for i in out["items"]] == ["2024-05-05", "2021-01-01", None]
    assert ("library_judgments_ranked", "date_gregorian", True, False) in fake.orders


def test_the_page_count_is_taken_over_the_same_relation_as_the_page() -> None:
    """§12.2: the wall's number and the served page must come from one set."""
    fake = _mixed_court_fake()
    variants = COURT_VARIANTS[COMMERCIAL]
    counted = ls.judgments_hub_total_pages(fake, court_variants=variants)
    served = ls.list_judgments_hub(fake, page=1, court_variants=variants)["total_pages"]
    assert counted == served == 3


def test_court_counts_returns_every_slug_seeded_to_zero() -> None:
    """A court with nothing published still renders (at zero) rather than
    vanishing from the switcher — the same contract ``sector_counts`` holds."""
    counts = ls.court_counts(_mixed_court_fake())

    assert set(counts) == set(COURT_ORDER)
    assert list(counts) == COURT_ORDER
    assert counts[COMMERCIAL] == 25
    assert counts[SINGLE_VARIANT] == 4
    assert counts[COURT_ORDER[-1]] == 0


def test_court_counts_reads_the_published_relation() -> None:
    fake = _mixed_court_fake()
    ls.court_counts(fake)
    assert set(fake.reads) == {"library_judgments_ranked"}
    assert {c[1] for c in fake.in_calls} == {"court"}


def test_court_counts_is_fail_soft_per_bucket() -> None:
    """One failing count costs that number, not the page: the switcher is
    rendered on every /judgments page, and a 500 there takes the wing down."""

    class _Flaky(_ViewFake):
        def table(self, name: str):
            if len(self.reads) == 3:
                self.reads.append(name)
                raise RuntimeError("PostgREST hiccup")
            return super().table(name)

    counts = ls.court_counts(_Flaky(library_judgments_ranked=[]))
    assert set(counts) == set(COURT_ORDER)


# ===========================================================================
# 5. §1.3 — the counts describe what is SERVABLE at any publish size
# ===========================================================================


def test_the_ranked_wings_never_scan_the_sidecar_id_list() -> None:
    """⚠ §1.3. ``_published_sample_counts`` short-circuits for a wing with a
    ranked view: an id-list scan cannot survive ~10,000 published judgments, and
    the published RPC answers the same question exactly."""
    for section in ("regulations", "judgments"):
        assert section in ls._RANKED_HUB_VIEWS
        assert ls._published_sample_counts(_Exploding(), section) is None


def test_the_sector_counts_rpc_is_the_PUBLISHED_one() -> None:
    """⚠ ONE WORD, EVERY NUMBER ON /library. ``library_sector_counts()``
    (migration 109) counts the CORPUS and is still installed; above the sample
    ceiling it would advertise 3,951 أنظمة and 30,531 أحكام for wings that serve
    1,188 and ~10,000, with nothing erroring."""
    assert ls._SECTOR_COUNTS_RPC == "library_sector_counts_published"

    class _Rpc:
        def __init__(self) -> None:
            self.names: list[str] = []

        def rpc(self, name: str, _params: dict):
            self.names.append(name)
            return self

        def execute(self):
            return _Result([])

    fake = _Rpc()
    counts = ls.sector_counts(fake)
    assert fake.names == ["library_sector_counts_published"]
    assert set(counts) == set(SECTOR_SLUGS.values())


def test_the_wing_totals_count_the_ranked_views() -> None:
    """The tab chip sizes a paginator that walks the published set, so it counts
    the published relation — not ``cases`` (30,531) or ``regulations_v2``
    (3,951)."""
    fake = _ViewFake(
        library_regulations_ranked=[{"id": f"r{i}"} for i in range(7)],
        library_judgments_ranked=[{"id": f"j{i}"} for i in range(4)],
        circulars=[{"id": "c1"}],
        seo_item_meta=[],
    )
    counts = ls.library_corpus_counts(fake)
    assert counts["regulations"] == 7
    assert counts["judgments"] == 4
    assert "cases" not in fake.reads
    assert "regulations_v2" not in fake.reads
