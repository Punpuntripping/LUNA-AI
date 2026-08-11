"""The sector axis of the public library — «القطاع» as a SECTION, not a filter.

Plan: ``.claude/plans/library_sectors.md`` §5 (the cap-policy amendment) · §7.1
(the circulars sector filter) · §7.2 / §7.3 (the new endpoints) · D8 · D9 · D15 ·
traps T1 / T4 / T6.

The security-relevant change under test is §5. Until 2026-08-01 the CTA-wall rule
drew its line at FILTERED vs UNFILTERED, so ANY filtered anonymous request got
``_ANON_WALL_TOTAL_PAGES`` (= 2) and the count was never issued. A sector page is
"filtered" under that rule, which would have shown an anonymous reader «1 2» over
20,182 items — the exact failure the 2026-07-30 revision was written to fix.

The amendment: a VALIDATED sector is a section. The oracle §2.1 closes is
free-text ``q``, whose answer moves with attacker-chosen input; a closed 38-value
vocabulary checked server-side yields 152 FIXED numbers that move only when the
corpus does. So a sector page gets real counts — and the depth caps do not move
at all (anon 1 · free 3 · paid unbounded).

Two properties here are easy to break and cost the whole argument if they go:

  * the raw-Arabic ``sector`` / ``domain`` params must be validated TOO, and must
    behave identically to the Latin ``sector_slug``. Capping one spelling and not
    the other is theatre — a caller just switches spelling.
  * a sector combined with ``q`` / ``entity`` / ``doc_type`` is filtered AGAIN.
    The section is the base set; the rest is still a probe.

Fixture style + the in-memory PostgREST stand-in are REUSED from the access-tiers
files, so tier resolution here is the real thing rather than a mock.
"""
from __future__ import annotations

import asyncio
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
from shared.library.sectors import SECTOR_SLUGS, SLUG_TO_SECTOR

from backend.tests.test_library_gating import (  # noqa: F401
    USER,
    FakeSupabase,
    quota_row,
)

AUTH_ID = "auth-0000-1111"

HUB = "/api/v1/public/library"
REG_HUB = f"{HUB}/regulations"
CIRC_HUB = f"{HUB}/circulars"
JUD_HUB = f"{HUB}/judgments"
SECTORS = f"{HUB}/sectors"

# The three wings, with the query param each one spells the sector axis with. The
# judgments wing filters ``cases.legal_domains`` and calls it ``domain``; it is
# the SAME 38-value vocabulary, which is exactly why it takes the same rule.
# ``/compliance`` was a fourth until 2026-08-03 — the wing was retired entirely.
WINGS = [
    (REG_HUB, "regulations", "sector", "regulations_hub_total_pages"),
    (CIRC_HUB, "circulars", "sector", "circulars_hub_total_pages"),
    (JUD_HUB, "judgments", "domain", "judgments_hub_total_pages"),
]
WING_PATHS = [w[0] for w in WINGS]

# A real sector, both spellings. 20,182 items in the live corpus — the one whose
# «1 2» paginator §5 exists to prevent.
COMMERCE_SLUG = "commercial-transactions"
COMMERCE_AR = "المعاملات التجارية"

# The §3 numbers for that sector, and the page counts they imply at 9/page.
# ``compliance`` is 0 and STAYS 0 until `compliance_table` ships. It is absent
# from ``ls.SECTOR_COUNT_SECTIONS`` (no table to count), so the zero here comes
# from the response MODEL's default — which is exactly the contract under test:
# the wing is present on the wire and empty.
COMMERCE_COUNTS = {
    "regulations": 693,
    "judgments": 18879,
    "compliance": 0,
    "circulars": 162,
}
# What ``ls.sector_counts()`` itself returns: the COUNTED wings only. The
# `compliance: 0` above is added a layer up by ``SectorCounts``'s model default,
# because the wing has no table to count yet.
COUNTED_COMMERCE = {
    k: v for k, v in COMMERCE_COUNTS.items() if k in ls.SECTOR_COUNT_SECTIONS
}
COMMERCE_PAGES = {k: math.ceil(v / 9) for k, v in COUNTED_COMMERCE.items()}

# The unfiltered corpus totals (§7.3). ⚠ judgments is 30,531 — the TRUE corpus
# total, NOT the 20,671 the per-sector judgment column sums to (D10).
CORPUS_COUNTS = {
    "regulations": 3373,
    "judgments": 30531,
    "compliance": 0,
    "circulars": 1843,
}

# What the stubbed hub counters/listers report for an UNSCOPED request.
TRUE_TOTAL_PAGES = 40

# ⚠ ``q`` IS NO LONGER AN ANON FILTER (bm25_navigation_search.md D9): search is
# registered-only and an anonymous ``?q=`` is silently dropped. The "a sector
# COMBINED with a filter is filtered again" property is unchanged — it just has
# to be probed with a param anon can still send. One per wing, each valid against
# its real vocabulary.
ANON_FILTER = {
    REG_HUB: {"doc_type": "law_statute"},
    CIRC_HUB: {"entity": "3f8c1d2e-0000-4000-8000-000000000001"},
    JUD_HUB: {"court_level": "appeal"},
}


class _User:
    """Stands in for AuthUser — the routes only ever read ``auth_id``."""

    auth_id = AUTH_ID
    email = "lawyer@example.com"
    role = "authenticated"


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """Module-level TTL caches + the route limiter's process-global fallback
    window. The sector memos are the point of several tests below, so a leak
    between tests would make them assert nothing."""
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


def _paid_client() -> TestClient:
    """A signed-in caller on a PAID plan — the only tier a sector slice serves.

    ⚠ THE SECTION GATE (2026-08-11) IS WHY HALF THIS FILE NEEDS ONE. Everything
    about a SERVED sector page — the real total, the slug→Arabic translation, the
    lister actually being reached — used to be assertable through an anonymous
    client, because anon got page 1. It does not any more: a section-scoped
    request is refused below ``paid`` at every page, so an anonymous body carries
    ``cap_reached`` and no items and can no longer witness any of it. The
    behaviours themselves are unchanged, so the tests moved tier rather than
    being deleted; the gate itself gets its own section (§10) below.
    """
    return _client(_hub_fake(plan="pro", limit=100), _User())


def _stub_item(n: int) -> dict[str, Any]:
    """One card, valid for EVERY hub model (each response model reads only the
    fields it declares)."""
    return {
        "slug": f"item-{n}",
        "title": "عنوان",
        "status": "active",
        "court": "المحكمة",
        "body_snippet": "",
        "use_case_snippet": "",
        "intro_snippet": "",
        "snippet": "",
    }


def _sector_counts_table() -> dict[str, dict[str, int]]:
    """A full 38-slug count table: the real §3 numbers for المعاملات التجارية,
    zeros everywhere else (so a miss is visible rather than plausible)."""
    table = {
        slug: {s: 0 for s in ls.SECTOR_COUNT_SECTIONS} | {"total": 0}
        for slug in SECTOR_SLUGS.values()
    }
    # COUNTED_COMMERCE, not COMMERCE_COUNTS: this stubs ``ls.sector_counts()``,
    # which only ever knows the wings that have a table. Seeding a `compliance`
    # key here would fake a count the real function cannot produce.
    table[COMMERCE_SLUG] = dict(COUNTED_COMMERCE) | {
        "total": sum(COUNTED_COMMERCE.values())
    }
    return table


@pytest.fixture
def stubs(monkeypatch):
    """Stub every wing lister + counter and both cross-wing count readers.

    Returns a dict recording what was called with what — the wiring is under test
    here, not the SQL. ``calls['sector_counts']`` is how the memo tests count RPC
    refreshes.
    """
    calls: dict[str, Any] = {"listers": [], "counters": {}, "sector_counts": 0,
                             "corpus_counts": 0}

    for _path, _section, _param, counter in WINGS:
        def _counter(*_a: Any, _name: str = counter, **_k: Any) -> int:
            calls["counters"][_name] = calls["counters"].get(_name, 0) + 1
            return TRUE_TOTAL_PAGES

        monkeypatch.setattr(ls, counter, _counter)

    for lister in (
        "list_regulations_hub",
        "list_circulars_hub",
        "list_judgments_hub",
    ):
        def _lister(_supabase: Any, _name: str = lister, **kw: Any) -> dict[str, Any]:
            calls["listers"].append((_name, kw))
            return {
                "items": [_stub_item(i) for i in range(9)],
                "page": int(kw.get("page") or 1),
                "total_pages": TRUE_TOTAL_PAGES,
            }

        monkeypatch.setattr(ls, lister, _lister)

    def _sector_counts(_supabase: Any) -> dict[str, dict[str, int]]:
        calls["sector_counts"] += 1
        return _sector_counts_table()

    def _corpus_counts(_supabase: Any) -> dict[str, int]:
        calls["corpus_counts"] += 1
        return dict(CORPUS_COUNTS)

    monkeypatch.setattr(ls, "sector_counts", _sector_counts)
    monkeypatch.setattr(ls, "library_corpus_counts", _corpus_counts)
    return calls


def _is_arabic_refusal(res, status: int = 400) -> None:
    """The project's standard envelope, an Arabic message, nothing cacheable."""
    assert res.status_code == status, res.text
    body = res.json()
    assert body["error"]["status"] == status
    assert body["error"]["code"] == "VALIDATION_ERROR"
    message = body["error"]["message"]
    assert message == body["detail"]
    assert message and not any("a" <= ch.lower() <= "z" for ch in message), message
    assert any("؀" <= ch <= "ۿ" for ch in message), message


class _Exploding:
    """Any DB touch is a test failure."""

    def table(self, *_a: Any, **_k: Any):
        raise AssertionError("the database was touched")

    def rpc(self, *_a: Any, **_k: Any):
        raise AssertionError("the database was touched")


# ===========================================================================
# 1. §12.7 — an unknown sector 404s without a DB round-trip
# ===========================================================================


@pytest.mark.parametrize("slug", ["zzz", "commercial", "المعاملات-التجارية", "%2e%2e"])
def test_an_unknown_sector_404s_without_touching_the_database(stubs, slug) -> None:
    """The 38 slugs resolve in memory (``shared/library/sectors.py``), so probing
    the namespace costs a dict lookup. A DB round-trip per probe would make the
    404 its own load generator."""
    res = _client(_Exploding()).get(f"{SECTORS}/{slug}")
    _is_arabic_refusal(res, status=404)
    assert res.json()["detail"] == "القطاع غير موجود"


@pytest.mark.parametrize("reserved", ["mine", "page"])
def test_the_reserved_segments_are_never_a_sector(stubs, reserved) -> None:
    """⚠ T2. ``/library/mine`` is the AUTHED shelf and ``/library/page/{n}`` is the
    unfiltered paginator. If either resolved as a sector, the backend would serve
    sector content under a per-user URL — so the refusal is enforced here as well
    as in the Next route table."""
    res = _client(_Exploding()).get(f"{SECTORS}/{reserved}")
    assert res.status_code == 404, res.text


def test_a_sector_404_is_never_shared_cached(stubs) -> None:
    """A 404 parked in the hour-cache would outlive the deploy that adds the
    sector — same reasoning the reveal endpoint's 404s carry."""
    res = _client(_Exploding()).get(f"{SECTORS}/zzz")
    assert res.headers["cache-control"] == "private, no-store"


# ===========================================================================
# 2. §5 — the sector vocabulary is validated on every wing, both spellings
# ===========================================================================


@pytest.mark.parametrize("path", WING_PATHS)
@pytest.mark.parametrize("bogus", ["zzz", "commercial", "MINE", "page", "*", "1 or 1=1"])
def test_an_unknown_sector_slug_is_refused_on_every_wing(stubs, path, bogus) -> None:
    res = _client().get(path, params={"sector_slug": bogus})
    _is_arabic_refusal(res)
    assert res.json()["detail"] == pl.MSG_INVALID_SECTOR


@pytest.mark.parametrize("path,_section,param,_counter", WINGS)
@pytest.mark.parametrize("bogus", ["قطاع", "عقارات", "labor-employment", "%"])
def test_an_unknown_RAW_sector_name_is_refused(
    stubs, path, _section, param, _counter, bogus
) -> None:
    """⚠ THE ONE THAT USED TO PASS SILENTLY. Before 2026-08-01 these params were
    deliberately unvalidated (an empty list came back) on the grounds that
    nothing linked them. This plan links all 38, so an unbounded value here would
    be an unbounded supply of fresh page 1s — and an unmemoisable count.

    Note ``labor-employment`` in the list: a LATIN slug in the raw-Arabic param is
    not a sector name and must not resolve."""
    _is_arabic_refusal(_client().get(path, params={param: bogus}))


@pytest.mark.parametrize("path,_section,param,_counter", WINGS)
def test_the_real_vocabulary_is_accepted_in_both_spellings(
    stubs, path, _section, param, _counter
) -> None:
    assert _client().get(
        path, params={"sector_slug": COMMERCE_SLUG}
    ).status_code == 200
    assert _client().get(path, params={param: COMMERCE_AR}).status_code == 200


@pytest.mark.parametrize("path,_section,param,_counter", WINGS)
def test_the_two_spellings_may_be_combined_only_when_they_agree(
    stubs, path, _section, param, _counter
) -> None:
    """One axis, two spellings. A request that names two different sectors means
    two different things; guessing which would make the memo key disagree with
    the rows actually returned."""
    ok = _client().get(
        path, params={"sector_slug": COMMERCE_SLUG, param: COMMERCE_AR}
    )
    assert ok.status_code == 200, ok.text

    clash = _client().get(
        path, params={"sector_slug": COMMERCE_SLUG, param: "الإسكان"}
    )
    _is_arabic_refusal(clash)


@pytest.mark.parametrize("path", WING_PATHS)
@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_sector_is_not_a_filter_and_not_an_error(stubs, path, blank) -> None:
    """An unscoped hub is the NORMAL case — it is what the ISR renderer asks for."""
    assert _client().get(path, params={"sector_slug": blank}).status_code == 200


def test_the_slug_lookup_is_case_and_whitespace_tolerant(stubs) -> None:
    """Slugs arrive as a URL path segment that a proxy or a human may have
    mangled; a case difference must not 400 a real page."""
    assert _client().get(
        REG_HUB, params={"sector_slug": " Commercial-Transactions "}
    ).status_code == 200


def test_a_rejected_sector_never_reaches_the_database(stubs) -> None:
    """Validation runs before tier resolution and before any query (§2.1
    ordering) — a refusal must cost one dict lookup."""
    assert _client(_Exploding()).get(
        REG_HUB, params={"sector_slug": "zzz"}
    ).status_code == 400


def test_a_sector_rejection_is_never_shared_cached(stubs) -> None:
    res = _client().get(REG_HUB, params={"sector_slug": "zzz"})
    assert res.headers["cache-control"] == "private, no-store"


def test_every_one_of_the_38_slugs_is_accepted(stubs) -> None:
    """The vocabulary is IMPORTED, never retyped: a slug added to the map must
    not need a second edit here to become servable."""
    client = _client()
    for slug in SLUG_TO_SECTOR:
        assert client.get(
            REG_HUB, params={"sector_slug": slug}
        ).status_code == 200, slug


# ===========================================================================
# 3. §5 — a validated sector is a SECTION: real counts, unchanged depth caps
# ===========================================================================


@pytest.mark.parametrize("path,section,_param,_counter", WINGS)
def test_an_anon_sector_wall_reports_the_REAL_page_count(
    stubs, path, section, _param, _counter
) -> None:
    """⚠ THE POINT OF §5. Under the old FILTERED-vs-UNFILTERED rule this body
    carried ``_ANON_WALL_TOTAL_PAGES`` (2) — «1 2» over 20,182 items. The count is
    one of 152 fixed numbers over a closed, server-validated vocabulary, so it
    steers with nothing and anon gets it."""
    body = _client().get(
        path, params={"page": 2, "sector_slug": COMMERCE_SLUG}
    ).json()

    assert body["cap_reached"] is True
    assert body["items"] == []
    assert body["total_pages"] == COMMERCE_PAGES[section]
    assert body["total_pages"] > pl._ANON_WALL_TOTAL_PAGES


@pytest.mark.parametrize("path,section,param,_counter", WINGS)
def test_the_raw_spelling_gets_the_same_real_count(
    stubs, path, section, param, _counter
) -> None:
    """No spelling arbitrage. If the Arabic param were still treated as a filter,
    a caller wanting the flat ceiling lifted would just switch to the slug — and
    a caller wanting to probe would just switch to the Arabic. One axis, one
    rule."""
    body = _client().get(path, params={"page": 2, param: COMMERCE_AR}).json()
    assert body["total_pages"] == COMMERCE_PAGES[section]


@pytest.mark.parametrize("path", WING_PATHS)
def test_a_sector_COMBINED_with_a_filter_is_filtered_again(stubs, path) -> None:
    """The section is the base set; a filter on top of it is still a probe whose
    answer moves with attacker-chosen input. That is the oracle §2.1 closes, and
    §5 does not reopen it.

    ⚠ The probe used to be ``q``. Since ``bm25_navigation_search.md`` D9 an
    anonymous ``q`` is DROPPED, so this now walks a filter anon can still send
    (``ANON_FILTER``) — the property is unchanged, the param is not."""
    params = {"page": 2, "sector_slug": COMMERCE_SLUG, **ANON_FILTER[path]}
    body = _client().get(path, params=params).json()

    assert body["cap_reached"] is True
    assert body["total_pages"] == pl._ANON_WALL_TOTAL_PAGES


@pytest.mark.parametrize("path", WING_PATHS)
def test_a_sector_plus_an_anonymous_q_stays_a_SECTION(stubs, path) -> None:
    """The D9 corollary on a sector page: the dropped ``q`` leaves a request whose
    only narrowing is a validated section, so it keeps the real per-sector count
    rather than falling to the flat ceiling. Anything else would let a shared
    search link make a sector page look eighteen items deep."""
    body = _client().get(
        path, params={"page": 2, "sector_slug": COMMERCE_SLUG, "q": "نظام"}
    ).json()

    assert body["cap_reached"] is True
    assert body["total_pages"] != pl._ANON_WALL_TOTAL_PAGES


def test_a_sector_combined_with_a_closed_vocabulary_filter_is_filtered_again(
    stubs,
) -> None:
    """``doc_type`` is closed too, but it MULTIPLIES the page-1 surface (38
    sectors × 21 buckets), so it stays on the filtered side."""
    body = _client().get(
        REG_HUB,
        params={"page": 2, "sector_slug": COMMERCE_SLUG, "doc_type": "law_statute"},
    ).json()
    assert body["total_pages"] == pl._ANON_WALL_TOTAL_PAGES


@pytest.mark.parametrize("path", WING_PATHS)
def test_the_anon_DEPTH_cap_is_untouched_by_the_amendment(stubs, path) -> None:
    """§5 changes what the wall SAYS, never where it stands. Page 2 is still a
    wall for anon, with zero items, on a sector page exactly as anywhere else."""
    body = _client().get(
        path, params={"page": 2, "sector_slug": COMMERCE_SLUG}
    ).json()

    assert body["cap_reached"] is True
    assert body["items"] == []
    assert body["max_page"] == ls.ANON_HUB_MAX_PAGE == 1


def test_a_free_account_still_stops_at_page_three_on_a_sector_page(stubs) -> None:
    body = _client(_hub_fake(plan="free", limit=10), _User()).get(
        REG_HUB, params={"page": 4, "sector_slug": COMMERCE_SLUG}
    ).json()
    assert body["cap_reached"] is True
    assert body["max_page"] == ls.FREE_HUB_MAX_PAGE == 3


@pytest.mark.parametrize("path", WING_PATHS)
def test_a_SERVED_sector_page_reports_the_real_total(stubs, path) -> None:
    """Clamping only the wall would not have closed the old oracle and must not
    now shrink a legitimate section: page 1 carries the lister's real total,
    unclamped, because a section is not a filter.

    PAID, since the section gate — see ``_paid_client``. The §5 rule this asserts
    is about COUNTS and is untouched by the gate; only who can witness it moved."""
    body = _paid_client().get(
        path, params={"page": 1, "sector_slug": COMMERCE_SLUG}
    ).json()

    assert body["cap_reached"] is False
    assert body["total_pages"] == TRUE_TOTAL_PAGES


@pytest.mark.parametrize("path", WING_PATHS)
def test_an_anon_sector_plus_a_filter_still_reports_the_flat_ceiling(
    stubs, path
) -> None:
    """§2.1's oracle stays shut on a sector page, through whichever path answers.

    It used to be the SERVED one: clamping the wall alone never closed the
    oracle, because anon got page 1 and it carried the same filtered total at the
    same granularity, so `_visible_total_pages` clamped it. Since the section
    gate anon reaches the WALL here instead, and `_wall_total_pages` clamps it on
    the `filtered=True` branch. Two mechanisms, one number — which is why this
    test asserts the NUMBER and no longer names a path."""
    params = {"page": 1, "sector_slug": COMMERCE_SLUG, **ANON_FILTER[path]}
    body = _client().get(path, params=params).json()
    assert body["total_pages"] == pl._ANON_WALL_TOTAL_PAGES


def test_an_unfiltered_wall_still_uses_the_per_section_counter(stubs) -> None:
    """The amendment adds a branch; it must not have stolen the existing one. No
    sector → the wing's own counter, memoised by section, exactly as before."""
    body = _client().get(REG_HUB, params={"page": 2}).json()
    assert body["total_pages"] == TRUE_TOTAL_PAGES
    assert stubs["counters"] == {"regulations_hub_total_pages": 1}
    assert stubs["sector_counts"] == 0


# ===========================================================================
# 4. §5 — the memo: ONE grouped query per refresh, keyed per section×sector
# ===========================================================================


def test_the_sector_count_is_memoised_per_section_and_sector(stubs) -> None:
    """152 lazily-filled entries would be 152 queries in the five minutes after a
    deploy, on the anon path — the round-trip §2.1 removed. One RPC fills every
    entry of both memos."""
    client = _client()
    client.get(REG_HUB, params={"page": 2, "sector_slug": COMMERCE_SLUG})

    assert stubs["sector_counts"] == 1
    for section, pages in COMMERCE_PAGES.items():
        assert pl._sector_total_pages_memo[f"{section}:{COMMERCE_SLUG}"] == pages
    # …and every other sector×section pair is present too, from the same call.
    assert len(pl._sector_total_pages_memo) == len(SECTOR_SLUGS) * len(
        ls.SECTOR_COUNT_SECTIONS
    )


def test_one_rpc_serves_every_sector_and_every_wing(stubs) -> None:
    """Walking the whole grid inside the TTL must not walk the database."""
    client = _client()
    for path in WING_PATHS:
        for slug in list(SLUG_TO_SECTOR)[:6]:
            client.get(path, params={"page": 2, "sector_slug": slug})

    assert stubs["sector_counts"] == 1
    assert stubs["counters"] == {}


def test_the_memo_keys_do_not_collide_across_sections(stubs) -> None:
    """A memo keyed by sector alone would hand the أنظمة page count to the أحكام
    tab — 77 pages where there are 2,098."""
    _client().get(REG_HUB, params={"page": 2, "sector_slug": COMMERCE_SLUG})

    regs = pl._sector_total_pages_memo[f"regulations:{COMMERCE_SLUG}"]
    juds = pl._sector_total_pages_memo[f"judgments:{COMMERCE_SLUG}"]
    assert regs != juds
    assert (regs, juds) == (COMMERCE_PAGES["regulations"], COMMERCE_PAGES["judgments"])


def test_an_expired_memo_refreshes(stubs) -> None:
    client = _client()
    client.get(REG_HUB, params={"page": 2, "sector_slug": COMMERCE_SLUG})
    pl._sector_memo_at["at"] -= pl._TOTAL_PAGES_TTL_SECONDS + 1
    client.get(REG_HUB, params={"page": 2, "sector_slug": COMMERCE_SLUG})
    assert stubs["sector_counts"] == 2


def test_an_empty_sector_section_reports_one_page_not_zero(stubs) -> None:
    """A zero-page paginator is a rendering bug, and the hub listers already
    return 1 for an empty result set. D9 handles the thin/empty tab in the UI;
    the backend just must not emit 0."""
    body = _client().get(
        REG_HUB, params={"page": 2, "sector_slug": "human-rights"}
    ).json()
    assert body["total_pages"] == 1


# ===========================================================================
# 5. §7.1 — the circulars wing finally has a sector filter
# ===========================================================================


def test_the_circulars_sector_filter_reaches_the_service(stubs) -> None:
    """``CircularsFilters`` was ``entity`` + ``q`` only despite
    ``circulars.sectors`` being 100% populated (1,843/1,843), so the التعاميم tab
    on a sector page could not scope at all.

    PAID — a gated request never reaches a lister at all (see ``_paid_client``)."""
    _paid_client().get(CIRC_HUB, params={"sector_slug": COMMERCE_SLUG})

    name, kwargs = stubs["listers"][-1]
    assert name == "list_circulars_hub"
    assert kwargs["sector"] == COMMERCE_AR


@pytest.mark.parametrize(
    "path,lister,key",
    [
        (REG_HUB, "list_regulations_hub", "sector"),
        (CIRC_HUB, "list_circulars_hub", "sector"),
        (JUD_HUB, "list_judgments_hub", "domain"),
    ],
)
def test_the_slug_is_translated_to_the_raw_arabic_name_for_the_query(
    stubs, path, lister, key
) -> None:
    """The corpus columns store the ARABIC name; the slug is a URL affordance
    only (D4/D6). Passing the slug through to ``.contains()`` would match zero
    rows on every wing at once.

    PAID — a gated request never reaches a lister at all (see ``_paid_client``)."""
    _paid_client().get(path, params={"sector_slug": COMMERCE_SLUG})
    name, kwargs = stubs["listers"][-1]
    assert name == lister
    assert kwargs[key] == COMMERCE_AR


def test_the_circulars_sector_filter_is_applied_by_the_service_layer() -> None:
    """Unit-level: the filter must land on ``sectors``, array-contains, and be a
    no-op when blank (an unscoped hub is the normal case)."""
    seen: list[tuple[str, list[str]]] = []

    class _QB:
        def contains(self, col, val):
            seen.append((col, val))
            return self

        def in_(self, *_a, **_k):
            return self

        def ilike(self, *_a, **_k):
            return self

    ls._apply_circular_filters(_QB(), None, None, COMMERCE_AR)
    assert seen == [("sectors", [COMMERCE_AR])]

    seen.clear()
    ls._apply_circular_filters(_QB(), None, None, "   ")
    assert seen == []


# ===========================================================================
# 6. §7.3 — the unified hub's tab counts
# ===========================================================================


def test_the_unified_hub_reports_the_four_unfiltered_totals(stubs) -> None:
    res = _client().get(HUB)
    assert res.status_code == 200, res.text
    assert res.json() == {"counts": CORPUS_COUNTS}


def test_the_hub_judgment_count_is_the_CORPUS_total_not_the_sector_sum(stubs) -> None:
    """⚠ THE ONE THAT LOOKS LIKE A BUG AND IS NOT. The hub count is the corpus
    count; it is not derivable from the sector counts in either direction. Only
    67.7% of ``cases`` carry a ``legal_domains`` value (20,671 of 30,531 — D10),
    and the per-sector column simultaneously OVER-counts (31,924 live) because a
    judgment can carry several domains. This endpoint feeds a tab whose paginator
    walks the WHOLE corpus, so it counts the corpus."""
    hub = _client().get(HUB).json()["counts"]["judgments"]
    sector_sum = sum(
        s["counts"]["judgments"] for s in _client().get(SECTORS).json()["sectors"]
    )
    assert hub == CORPUS_COUNTS["judgments"]
    assert sector_sum != hub


def test_the_hub_counts_are_memoised(stubs) -> None:
    client = _client()
    for _ in range(3):
        client.get(HUB)
    assert stubs["corpus_counts"] == 1


# ===========================================================================
# 7. §7.2 — the sectors list + one sector's overview
# ===========================================================================


def test_the_sectors_endpoint_lists_all_38_in_the_servers_order(stubs) -> None:
    """ORDER IS THE SERVER'S: corpus volume descending, i.e. ``SECTOR_SLUGS``
    insertion order. Alphabetical would bury a 20,182-item sector under a
    753-item one, and the frontend renders the list as given."""
    body = _client().get(SECTORS).json()
    assert [s["slug"] for s in body["sectors"]] == list(SECTOR_SLUGS.values())
    assert [s["name_ar"] for s in body["sectors"]] == list(SECTOR_SLUGS)
    assert len(body["sectors"]) == 38


def test_a_sector_tile_carries_the_four_counts_and_their_total(stubs) -> None:
    tile = next(
        s for s in _client().get(SECTORS).json()["sectors"] if s["slug"] == COMMERCE_SLUG
    )
    assert tile["name_ar"] == COMMERCE_AR
    assert tile["counts"] == dict(COMMERCE_COUNTS) | {
        "total": sum(COMMERCE_COUNTS.values())
    }


def test_the_sector_overview_returns_counts_plus_a_preview_of_each_wing(stubs) -> None:
    body = _client().get(f"{SECTORS}/{COMMERCE_SLUG}").json()

    assert body["slug"] == COMMERCE_SLUG
    assert body["name_ar"] == COMMERCE_AR
    assert body["counts"]["total"] == sum(COMMERCE_COUNTS.values())
    # ``compliance`` is present and EMPTY — the overview never calls its lister,
    # so the wing costs a sector page nothing while it has no table.
    assert set(body["preview"]) == {
        "regulations",
        "judgments",
        "compliance",
        "circulars",
    }
    assert body["preview"]["compliance"] == []
    for key, items in body["preview"].items():
        if key == "compliance":
            continue
        assert len(items) == pl._SECTOR_PREVIEW_ITEMS
        assert items[0]["slug"] and items[0]["title"]


def test_the_overview_scopes_every_wing_to_the_sector(stubs) -> None:
    _client().get(f"{SECTORS}/{COMMERCE_SLUG}")
    scoped = {
        name: kwargs.get("sector") or kwargs.get("domain")
        for name, kwargs in stubs["listers"]
    }
    assert scoped == {
        "list_regulations_hub": COMMERCE_AR,
        "list_judgments_hub": COMMERCE_AR,
        "list_circulars_hub": COMMERCE_AR,
    }


def test_the_overview_normalises_the_slug_it_echoes_back(stubs) -> None:
    """The canonical slug is the map's, not the caller's casing — the frontend
    builds its tab hrefs from this field."""
    body = _client().get(f"{SECTORS}/{COMMERCE_SLUG.upper()}").json()
    assert body["slug"] == COMMERCE_SLUG


# ===========================================================================
# 8. Cache-control — the new endpoints follow the existing hub rule
# ===========================================================================


@pytest.mark.parametrize(
    "path", [HUB, SECTORS, f"{SECTORS}/{COMMERCE_SLUG}", REG_HUB]
)
def test_an_anonymous_response_keeps_the_shared_hour_cache(stubs, path) -> None:
    res = _client().get(path)
    assert res.headers["cache-control"] == "public, max-age=3600"
    assert "Authorization" in res.headers.get("vary", "")


@pytest.mark.parametrize("path", [HUB, SECTORS, f"{SECTORS}/{COMMERCE_SLUG}"])
def test_an_authed_response_is_never_shared_cached(stubs, path) -> None:
    """⚠ The correctness property of the whole tier design: an authed body in the
    shared hour-cache is replayed to the next anonymous visitor. The new
    endpoints are not exempt just because they mostly carry counts."""
    res = _client(_hub_fake(plan="pro", limit=100), _User()).get(path)
    assert res.headers["cache-control"] == "private, no-store"


# ===========================================================================
# 9. §2.2 — the overview yields items, so it is metered
# ===========================================================================


def test_the_overview_charges_the_items_it_yields(stubs, monkeypatch) -> None:
    """38 overview pages × 12 items would be 456 items outside the per-user
    budget if this endpoint skipped the meter."""
    charged: list[tuple[str, int]] = []

    async def _charge(_request, _user_id, keys, **_kw):
        keys = list(keys)
        if keys:
            charged.append((keys[0].split(":")[0], len(keys)))

    monkeypatch.setattr(pl.library_budget, "charge_items", _charge)
    monkeypatch.setattr(
        pl.library_budget, "item_keys",
        lambda section, items: [f"{section}:{it['slug']}" for it in items],
    )

    _client(_hub_fake(plan="pro", limit=100), _User()).get(
        f"{SECTORS}/{COMMERCE_SLUG}"
    )

    assert dict(charged) == {
        "regulations": pl._SECTOR_PREVIEW_ITEMS,
        "judgments": pl._SECTOR_PREVIEW_ITEMS,
        "circulars": pl._SECTOR_PREVIEW_ITEMS,
    }


def test_the_overview_enforces_the_budget_before_it_queries(stubs, monkeypatch) -> None:
    """A refusal must not cost a DB round-trip (§2.2 ordering rule 4)."""
    order: list[str] = []

    async def _enforce(_request, _user_id, _tier=None):
        order.append("enforce")

    monkeypatch.setattr(pl.library_budget, "enforce_item_budget", _enforce)
    before = len(stubs["listers"])

    _client(_hub_fake(plan="pro", limit=100), _User()).get(
        f"{SECTORS}/{COMMERCE_SLUG}"
    )

    assert order == ["enforce"]
    assert len(stubs["listers"]) == before + 3


# ===========================================================================
# 10. The service layer — sector_counts() maps the RPC onto the 38 slugs
# ===========================================================================


class _RpcOnly:
    """Answers exactly one RPC and nothing else."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    def rpc(self, name: str, params: dict) -> "_RpcOnly":
        self.calls.append(name)
        self._name = name
        return self

    def execute(self):
        class _Res:
            data = self.rows

        return _Res()


@pytest.fixture
def steady_state(monkeypatch):
    """Every wing past ``SAMPLE_MODE_MAX_IDS`` — the end state, where the RPC is
    the whole story."""
    monkeypatch.setattr(ls, "_published_ids", lambda _supabase, _ct: None)


def test_sector_counts_seeds_every_slug_and_drops_unknown_sector_values(
    steady_state,
) -> None:
    """The VOCABULARY is ``shared/library/sectors.py``, not the corpus. A sector
    value the pipeline invents has no slug, therefore no public page, therefore
    no row — and a sector with zero rows anywhere still appears, at zero, so the
    browse grid never loses a tile (and the frontend gets the zero it needs to
    drop the tab and skip prerendering, D9)."""
    fake = _RpcOnly(
        [
            {"sector": COMMERCE_AR, **COMMERCE_COUNTS},
            {"sector": "قطاع مخترع", "regulations": 5, "judgments": 5,
             "circulars": 5},
        ]
    )
    counts = ls.sector_counts(fake)

    assert fake.calls == ["library_sector_counts_published"]
    assert set(counts) == set(SECTOR_SLUGS.values())
    assert counts[COMMERCE_SLUG] == dict(COUNTED_COMMERCE) | {
        "total": sum(COUNTED_COMMERCE.values())
    }
    assert counts["human-rights"] == {
        "regulations": 0, "judgments": 0, "circulars": 0, "total": 0,
    }


def test_steady_state_uses_exactly_one_grouped_query(steady_state) -> None:
    """§5: 152 counts, ONE query. ``_RpcOnly`` has no ``table()``, so any
    per-sector fallback would raise."""
    fake = _RpcOnly([{"sector": COMMERCE_AR, **COMMERCE_COUNTS}])
    ls.sector_counts(fake)
    assert len(fake.calls) == 1


def test_a_failing_rpc_becomes_the_standard_arabic_hub_error(steady_state) -> None:
    """A missing/dropped function must surface as this file's normal hub failure
    — never as a silent page of zeros, which would bake an empty library into ISR
    for an hour (T3)."""

    class _Broken:
        def rpc(self, *_a, **_k):
            raise RuntimeError("function public.library_sector_counts() does not exist")

    with pytest.raises(LunaHTTPException) as exc:
        ls.sector_counts(_Broken())
    assert exc.value.status_code == 500
    assert any("؀" <= ch <= "ۿ" for ch in exc.value.detail)


# ===========================================================================
# 10b. SERVABLE counts — the numbers describe what the wing can actually serve
#
# The listers already paginate the PUBLISHED set while a wing is sampled, so
# corpus-based counts were the only thing on the site describing the corpus. That
# was not merely cosmetic: the frontend derives its D9 thin-page `noindex`
# decision AND its `generateStaticParams` filter from these counts, so a sector
# with 695 أنظمة in the corpus and 0 published passed the "fat enough to index"
# test and got prerendered as a static, indexable, EMPTY page.
# ===========================================================================


class _SectorFake(FakeSupabase):
    """``FakeSupabase`` that also answers the ``library_sector_counts_published`` RPC.

    ⚠ The RPC name is asserted, not merely recorded. ``library_sector_counts()``
    (migration 109) is still installed and counts the CORPUS; swapping it back in
    is a one-word edit that no page would visibly fail on — it would just quietly
    advertise 3,951 أنظمة and 30,531 حكم again. This assert is the tripwire.
    """

    def __init__(self, *, sector_rows: Optional[list[dict[str, Any]]] = None,
                 **tables: Any) -> None:
        super().__init__(**tables)
        self.sector_rows = sector_rows or []
        self.rpc_names: list[str] = []

    def rpc(self, name: str, params: dict) -> Any:
        self.rpc_names.append(name)
        assert name == "library_sector_counts_published", name
        return _RpcOnly(self.sector_rows).rpc(name, params)


def _sidecar(content_type: str, ids: list[str]) -> list[dict[str, Any]]:
    return [
        {"content_type": content_type, "content_id": i, "slug": f"{content_type}-{i}"}
        for i in ids
    ]


def _sample_fake(**kw: Any) -> _SectorFake:
    """Three published تعاميم (two commercial, one housing) and nothing else
    published — 100%-of-nothing for the other three wings.

    ⚠ THE SAMPLED WING HERE IS ``circulars``, NOT ``regulations``. It used to be
    regulations, and re-pointing it back would silently stop testing anything:
    regulations and judgments both moved onto published-only RANKED VIEWS
    (migrations 116 and 123), so ``_published_sample_counts`` short-circuits them
    to ``None`` before it touches the sidecar and they have no sample mode left to
    pin. Circulars and services are the wings ``SAMPLE_MODE_MAX_IDS`` still
    governs — 100 published each — so circulars is what section 10b can still
    assert against. The invariant under test is unchanged; only the wing that
    still exhibits it moved.
    """
    return _SectorFake(
        seo_item_meta=_sidecar("circular", ["c1", "c2", "c3"]),
        circulars=[
            {"id": "c1", "title": "أ", "sectors": [COMMERCE_AR]},
            {"id": "c2", "title": "ب", "sectors": [COMMERCE_AR, "الإسكان"]},
            {"id": "c3", "title": "ج", "sectors": ["الإسكان"]},
            # In the corpus, NOT published — must not be counted.
            {"id": "c9", "title": "د", "sectors": [COMMERCE_AR]},
        ],
        **kw,
    )


def test_a_sampled_wing_counts_only_what_it_can_serve() -> None:
    """⚠ THE FIX. ``c9`` is in the corpus and has no slug, so no page of any
    paginator can ever show it. Counting it produced «77 pages» over 3 real
    items."""
    counts = ls.sector_counts(_sample_fake())

    assert counts[COMMERCE_SLUG]["circulars"] == 2
    assert counts["housing"]["circulars"] == 2
    assert counts["health"]["circulars"] == 0


def test_a_sampled_wing_never_takes_its_number_from_the_rpc() -> None:
    """One ``id IN (...)`` read covers that wing's whole 38-sector column, and the
    RPC's answer for it would be wrong anyway.

    ⚠ This assertion used to be ``fake.rpc_names == []``, which held only while
    EVERY wing was sampled. Regulations and judgments now ride ranked views and
    MUST consult the RPC, so a mixed state issues exactly one grouped call. The
    invariant that survived the change is the one that mattered: the sampled
    wing's number is computed from its published ids, never read off the RPC row.
    """
    fake = _sample_fake(sector_rows=[{"sector": COMMERCE_AR, **COMMERCE_COUNTS}])
    counts = ls.sector_counts(fake)

    # One grouped call for the ranked wings — never one per wing, never per sector.
    assert fake.rpc_names == ["library_sector_counts_published"]
    # …and the RPC's 162 تعميم loses to the 2 this wing can actually serve.
    assert COMMERCE_COUNTS["circulars"] == 162
    assert counts[COMMERCE_SLUG]["circulars"] == 2


def test_a_row_is_counted_once_per_sector_it_carries() -> None:
    """``c2`` is both commercial and housing, and the sector filter returns it
    for either — so both columns count it. This is the same over-count the
    grouped ``unnest`` produces, on purpose: it matches what a scoped page
    serves."""
    counts = ls.sector_counts(_sample_fake())
    assert counts[COMMERCE_SLUG]["circulars"] + counts["housing"]["circulars"] == 4


def test_a_sector_with_nothing_published_reports_zero_not_the_corpus_count() -> None:
    """المواصفات والمقاييس measured 695 in the corpus and 0 servable on
    2026-08-01. 695 made it an indexable static page with no items on it."""
    counts = ls.sector_counts(_sample_fake())
    assert counts["standards-metrology"]["circulars"] == 0


def test_the_wings_decide_independently_and_the_rpc_covers_only_the_steady_ones(
    monkeypatch,
) -> None:
    """A mixed state is NORMAL — ``build_seo_slugs`` finishes one wing at a time —
    and the sampled wing's number must not be overwritten by the RPC column.

    Three states coexist here, which is the point: judgments ride the RANKED VIEW
    (never sampled), circulars ride their published sample, and the RPC supplies
    only what it should."""
    monkeypatch.setattr(
        ls,
        "_published_ids",
        lambda _supabase, ct: ["c1", "c2", "c3"] if ct == "circular" else None,
    )
    fake = _sample_fake(sector_rows=[{"sector": COMMERCE_AR, **COMMERCE_COUNTS}])
    counts = ls.sector_counts(fake)

    assert fake.rpc_names == ["library_sector_counts_published"]
    # judgments (ranked view → steady) rides the RPC…
    assert counts[COMMERCE_SLUG]["judgments"] == COMMERCE_COUNTS["judgments"]
    # …while circulars (sampled) keeps its servable count, NOT the RPC's 162.
    assert counts[COMMERCE_SLUG]["circulars"] == 2


def test_a_wing_self_heals_into_corpus_counts_when_it_is_published(
    monkeypatch,
) -> None:
    """Nothing to unwind later: crossing ``SAMPLE_MODE_MAX_IDS`` is the ONLY
    switch for a wing with no ranked view, and it flips on its own."""
    fake = _sample_fake(sector_rows=[{"sector": COMMERCE_AR, **COMMERCE_COUNTS}])
    assert ls.sector_counts(fake)[COMMERCE_SLUG]["circulars"] == 2

    ls._published_ids_cache.clear()
    monkeypatch.setattr(ls, "_published_ids", lambda _supabase, _ct: None)
    assert (
        ls.sector_counts(fake)[COMMERCE_SLUG]["circulars"]
        == COMMERCE_COUNTS["circulars"]
    )


def test_the_hub_tab_counts_are_servable_too(steady_state, monkeypatch) -> None:
    """Same rule, same reason — the tab chip sizes a paginator that walks exactly
    this set. TWO mechanisms now deliver that guarantee and both are asserted: a
    RANKED wing counts its published-only view, a SAMPLED wing counts its
    published ids. The corpus count is reachable by neither."""
    fake = _SectorFake(
        # 99 in the corpus, 7 in the view — the chip must say 7.
        library_regulations_ranked=[{"id": f"r{i}"} for i in range(7)],
        regulations_v2=[{"id": f"r{i}"} for i in range(99)],
        cases=[], services=[],
        circulars=[{"id": f"c{i}"} for i in range(7)],
    )
    assert ls.library_corpus_counts(fake)["regulations"] == 7

    ls._published_ids_cache.clear()
    monkeypatch.setattr(
        ls, "_published_ids", lambda _supabase, ct: ["c1", "c2"] if ct == "circular" else None
    )
    assert ls.library_corpus_counts(fake)["circulars"] == 2


def test_the_wall_count_and_the_served_page_agree_in_sample_mode() -> None:
    """⚠ §12.2. Page 1 reports the LISTER's total and page ≥2 reports the memo's;
    if the two are computed over different sets, an anon reader sees a paginator
    that contradicts itself within one session. Same ids, same reader, same
    number."""
    fake = _sample_fake()
    served = ls.list_circulars_hub(fake, page=1, sector=COMMERCE_AR)["total_pages"]
    counted = ls.sector_counts(fake)[COMMERCE_SLUG]["circulars"]

    assert served == max(1, math.ceil(counted / ls.HUB_PAGE_SIZE))


# ===========================================================================
# 11. Route shape — nothing moved
# ===========================================================================


def test_the_new_routes_are_mounted_and_the_old_ones_are_not_shadowed() -> None:
    paths = {getattr(r, "path", "") for r in _app(_hub_fake()).routes}
    for path in (HUB, SECTORS, f"{SECTORS}/{{slug}}", *WING_PATHS):
        assert path in paths
    assert "/api/v1/public/library/sitemap/{section}" in paths


def test_topic_map_is_never_queried(stubs) -> None:
    """⚠ D15 / T1. ``topic_map`` stays EMPTY: ``regulations_v2`` is a VIEW over the
    pipeline-owned schema, so a re-ingest would desynchronise a materialised join
    table with no error. Sector scoping goes through the array columns."""

    class _Watch(FakeSupabase):
        def table(self, name: str):
            assert name != "topic_map", "topic_map must never be queried (D15/T1)"
            return super().table(name)

    fake = _Watch()
    fake.quota_row = quota_row()
    client = _client(fake)
    client.get(HUB)
    client.get(SECTORS)
    client.get(f"{SECTORS}/{COMMERCE_SLUG}")
    client.get(REG_HUB, params={"sector_slug": COMMERCE_SLUG})


def test_the_section_resolver_is_a_pure_in_memory_lookup() -> None:
    """§12.7 at the unit level. ``_sector_section`` decides everything the cap
    policy keys off, and it must do so without a client, a request or a query."""
    assert pl._sector_section(None, None) == (None, None)
    assert pl._sector_section(COMMERCE_SLUG, None) == (COMMERCE_AR, COMMERCE_SLUG)
    assert pl._sector_section(None, COMMERCE_AR) == (COMMERCE_AR, COMMERCE_SLUG)
    with pytest.raises(LunaHTTPException):
        pl._sector_section("mine", None)
    with pytest.raises(LunaHTTPException):
        pl._sector_section(None, "قطاع مخترع")


# ===========================================================================
# 12. Security-review fixes (2026-08-01)
# ===========================================================================


def test_the_sector_item_route_shares_one_rate_limit_bucket(stubs) -> None:
    """F2. 38 slugs left uncollapsed = 38 × DEFAULT_RATE_LIMIT instead of the one
    shared library bucket — on the endpoint that runs all four hub listers per
    request, i.e. the most expensive call in the wing."""
    normalize = rate_limit.normalize_rate_limit_path
    collapsed = f"{SECTORS}/:item"

    assert normalize(f"{SECTORS}/{COMMERCE_SLUG}") == collapsed
    assert normalize(f"{SECTORS}/housing") == collapsed
    # The flat list has no tail and keeps its own key, like every hub list path.
    assert normalize(SECTORS) == SECTORS


def test_the_sector_count_memo_is_never_handed_out_by_reference(stubs) -> None:
    """F5. A handler mutating the returned dict would corrupt what every other
    request reads for the rest of the TTL."""
    client = _client()
    client.get(SECTORS)

    snapshot = asyncio.run(pl._sector_counts(_hub_fake()))
    snapshot[COMMERCE_SLUG]["regulations"] = -1
    snapshot.pop("housing", None)

    assert pl._sector_counts_memo[COMMERCE_SLUG]["regulations"] == (
        COMMERCE_COUNTS["regulations"]
    )
    assert "housing" in pl._sector_counts_memo


def test_the_overview_slug_is_indexed_not_defaulted(stubs, monkeypatch) -> None:
    """F6. The echoed ``slug`` is interpolated into ``href``s and ``og:url`` by
    the frontend. If the two maps ever stopped being exact inverses, a fallback
    would launder the raw request path segment into that field; a KeyError (500)
    is the correct failure for a broken invariant."""
    monkeypatch.setitem(pl.SECTOR_SLUGS, COMMERCE_AR, "canonical-not-the-url")
    body = _client().get(f"{SECTORS}/{COMMERCE_SLUG}").json()
    assert body["slug"] == "canonical-not-the-url"


def test_an_unslugged_sector_falls_back_to_the_filtered_branch(
    stubs, monkeypatch
) -> None:
    """F3. A sector in the vocabulary but not in the slug map has no memo key, so
    the SECTION branch would answer with the whole wing's total while the rows
    are scoped to one sector. It must take the FILTERED branch instead. Cannot
    happen today — ``test_library_sectors.py`` fails in CI the moment the two
    disagree — which is why the fail-safe is code, not a comment."""
    assert pl._sector_is_unslugged(COMMERCE_AR, None) is True
    assert pl._sector_is_unslugged(COMMERCE_AR, COMMERCE_SLUG) is False
    assert pl._sector_is_unslugged(None, None) is False

    monkeypatch.setattr(pl, "slug_for_sector", lambda _name: None)
    body = _client().get(REG_HUB, params={"page": 2, "sector": COMMERCE_AR}).json()

    assert body["total_pages"] == pl._ANON_WALL_TOTAL_PAGES
    assert stubs["sector_counts"] == 0


# ===========================================================================
# 10. THE SECTION GATE — a sector slice is a PAID surface (2026-08-11)
#
# The section axis multiplies the depth cap rather than being bounded by it: a
# free reader's 3 pages become 3 pages PER SLICE, 152 of them. So a request
# narrowed to a sector is refused below `paid` at every page, page 1 included.
#
# ⚠ THIS BLOCK IS THE GATE'S ONLY WITNESS, AND IT MUST NOT BE "FIXED" BY MOVING
# ITS CLIENTS TO PAID THE WAY §3's WERE. Every assertion here is about a caller
# who must NOT be served.
# ===========================================================================


@pytest.mark.parametrize("path", WING_PATHS)
def test_an_anon_caller_is_refused_a_sector_page_ONE(stubs, path) -> None:
    """Page 1 — not page 2. The depth cap already handled page 2; the whole
    point of the gate is that the FIRST page of a slice is refused too."""
    body = _client().get(path, params={"page": 1, "sector_slug": COMMERCE_SLUG}).json()

    assert body["cap_reached"] is True
    assert body["items"] == []


@pytest.mark.parametrize("path", WING_PATHS)
def test_a_free_account_is_refused_a_sector_page_ONE(stubs, path) -> None:
    """A free account's 3-page allowance is real and unchanged — it just does not
    apply to a pre-cut slice. Getting this wrong is silent: the reader sees cards
    and nothing errors."""
    body = _client(_hub_fake(plan="free", limit=10), _User()).get(
        path, params={"page": 1, "sector_slug": COMMERCE_SLUG}
    ).json()

    assert body["cap_reached"] is True
    assert body["items"] == []


@pytest.mark.parametrize("path", WING_PATHS)
def test_a_paid_caller_is_still_served_a_sector_page(stubs, path) -> None:
    """The gate is a tier boundary, not a shutdown. If this fails the wing is
    dead for everyone and the feature has no reader at all."""
    body = _paid_client().get(
        path, params={"page": 1, "sector_slug": COMMERCE_SLUG}
    ).json()

    assert body["cap_reached"] is False
    assert len(body["items"]) == 9


@pytest.mark.parametrize("path,_section,param,_counter", WINGS)
def test_the_RAW_ARABIC_spelling_is_gated_too(stubs, path, _section, param, _counter) -> None:
    """⚠ THE SPELLING ARBITRAGE. Both params name the same axis, and `?sector=` /
    `?domain=` carry the raw Arabic name — the door `/judgments?domain=…` walks
    through. A gate keyed on the SLUG would leave it wide open, which is why the
    handlers pass the resolved NAME."""
    body = _client().get(path, params={"page": 1, param: COMMERCE_AR}).json()

    assert body["cap_reached"] is True
    assert body["items"] == []


@pytest.mark.parametrize("path", WING_PATHS)
def test_the_UNFILTERED_wing_is_untouched_by_the_gate(stubs, path) -> None:
    """The blast radius is the section axis and nothing else. An anonymous reader
    still gets page 1 of the wing itself — that is the whole public library."""
    body = _client().get(path, params={"page": 1}).json()

    assert body["cap_reached"] is False
    assert len(body["items"]) == 9


@pytest.mark.parametrize("path", WING_PATHS)
def test_a_verified_crawler_does_NOT_get_past_the_section_gate(stubs, path) -> None:
    """§3.7 waives the DEPTH cap for a verified crawler, on the argument that the
    body is byte-identical to what a human reaches one tier up. That argument
    does not survive here: no signed-out human sees a section slice now, so
    serving one to Googlebot would be cloaking. The waiver must not carry."""
    body = _client().get(
        path,
        params={"page": 1, "sector_slug": COMMERCE_SLUG},
        headers={"user-agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"},
    ).json()

    assert body["cap_reached"] is True
    assert body["items"] == []


def test_a_crawler_refused_by_the_gate_still_leaves_a_SHARED_cache_body(
    stubs,
) -> None:
    """The crawler-bypass header rule is `private, no-store`, and it exists so an
    uncapped crawler body is never replayed to anonymous humans. A body the gate
    refused is not that body — it is the ordinary anonymous wall, identical for
    every caller at this tier — so it must stay shareable. Marking it private
    would cost the edge cache on the wing's most-crawled URLs for nothing."""
    res = _client().get(
        REG_HUB,
        params={"page": 1, "sector_slug": COMMERCE_SLUG},
        headers={"user-agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"},
    )

    assert res.headers["cache-control"] == pl._LIBRARY_CACHE_CONTROL
    assert res.headers["vary"] == "Authorization"


@pytest.mark.parametrize("path", WING_PATHS)
def test_a_gated_request_never_reaches_the_lister(stubs, path) -> None:
    """Refused before any DB work — the gate is a bound on cost as much as on
    bytes, and a refusal that still ran the query would make the wing a free
    load generator."""
    _client().get(path, params={"page": 1, "sector_slug": COMMERCE_SLUG})

    assert stubs["listers"] == []


def test_the_tier_predicate_is_pure_and_fails_closed() -> None:
    """`section_scope_allowed` is the whole rule, and an unknown tier string must
    be refused — the same fail-closed convention `hub_page_allowed` uses."""
    assert pl.section_scope_allowed("paid") is True
    assert pl.section_scope_allowed("free") is False
    assert pl.section_scope_allowed("anon") is False
    assert pl.section_scope_allowed("") is False
    assert pl.section_scope_allowed("PAID") is False
