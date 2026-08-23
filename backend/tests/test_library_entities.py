"""«الجهة» — the entity axis of /compliance as an ANON-VISIBLE SECTION.

Plan: ``.claude/plans/compliance_entity_sections.md`` §2 (the three decisions) ·
§3 (the closed vocabulary) · §4 (the backend) · §8 (these tests).

THREE THINGS ARE UNDER TEST HERE AND ALL THREE FAIL SILENTLY IF THEY REGRESS.

1. §2/D1 — ``entity`` MUST STAY OUT OF ``section_scoped``. The section gate
   (``public_library.section_scope_allowed``) refuses a sector- or court-scoped
   hub slice below ``paid`` at page 1. /compliance's ENTITY axis is exempt, and
   the exemption is expressed as a ONE-LINE NON-EDIT — ``_hub_page_visible(...,
   section_scoped=bool(sector))``, with ``entity`` absent. The exemption is a
   property of THIS wing (100% published, ungated end to end, all 337 guide URLs
   already in the sitemap), not a change of mind about the rule, so the SECTOR
   axis on the same handler stays paid-only. Both halves are asserted below: get
   the first wrong and every entity page 404s for the anonymous readers and
   crawlers it exists for; get the second wrong and one wing silently un-gates a
   cross-wing rule.

2. §2/D1 — ``entity`` MUST STAY OUT OF THE ``filtered`` FLAG. ``filtered`` drives
   the enumeration-oracle clamp (``_wall_total_pages`` / ``_visible_total_pages``),
   which pins an anonymous caller's ``total_pages`` to 2. A closed, server-owned
   28-value vocabulary yields 28 FIXED numbers that move only when the corpus
   does — the same argument that made a validated sector a section on 2026-08-01
   and a validated court one on 2026-08-08 — so it is not an oracle and it gets
   real counts. Put ``entity`` in that flag and /compliance/ministry-of-justice
   prints «1 2» over 115 guides and 13 real pages, with nothing erroring and page
   1 still rendering perfectly.

3. §3 — THE VOCABULARY MATCHES THE CORPUS EXACTLY. The predicate is ``eq``, not
   ``ilike``, so a single retyped character means a section that renders empty
   and a browse tile that reads «0». This is not hypothetical: the plan's own §3
   table lost the FATHA in «الهيئة السعودية للمقَيّمين المعتمدين (تقييم)»
   (U+064E after ق), caught 2026-08-23 by exactly the live check below. The
   ``LIVE`` tests skip when Supabase is unreachable so an offline run stays
   green, but they are the only thing that can catch a pipeline re-ingest
   renaming a body — ``shared/library/entities.py`` logs and degrades on drift by
   design, precisely so it cannot crash the backend's boot.

The vocabulary itself is IMPORTED from ``shared/library/entities.py`` throughout,
never retyped — see failure mode 3.
"""
from __future__ import annotations

import math
import re
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
from backend.app.services import library_budget_service as lb
from backend.app.services import search_service
from shared.library.entities import (
    ENTITY_LABELS,
    ENTITY_ORDER,
    ENTITY_SLUG_VOCAB,
    RESERVED_SLUGS,
    name_for_slug,
    slug_for_name,
    unclaimed_entities,
)

from backend.tests.test_library_gating import (  # noqa: F401
    USER,
    FakeSupabase,
    quota_row,
)

AUTH_ID = "auth-0000-2222"

HUB = "/api/v1/public/library"
COMPLIANCE = f"{HUB}/compliance"
ENTITIES = f"{HUB}/compliance/entities"

# Picked FROM the map, so re-ordering or re-slugging it cannot leave this file
# asserting against an entity that no longer exists.
BIGGEST = ENTITY_ORDER[0]     # ministry-of-justice — 115 guides, 13 pages
SMALLEST = ENTITY_ORDER[-1]   # sfda — 1 guide, 1 page

# A raw sector slug, for the "the sector rule survived D1" case.
SECTOR_SLUG = "commercial-transactions"

_SLUG_RE = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")

# Stubbed PUBLISHED counts per entity. Deliberately NOT the numbers in
# ``entities.py``'s comments: those document the CORPUS on one day, while every
# number the grid prints has to describe what a paginator can actually walk.
ENTITY_PUBLISHED = {slug: 1 for slug in ENTITY_ORDER}
ENTITY_PUBLISHED[BIGGEST] = 115
ENTITY_PUBLISHED[ENTITY_ORDER[1]] = 53
ENTITY_PUBLISHED[SMALLEST] = 0  # an entity whose guides all lost their slugs

ENTITY_PAGES = {
    slug: (max(1, math.ceil(n / 9)) if n else 1) for slug, n in ENTITY_PUBLISHED.items()
}

# What the stubbed lister/counter report for any request — the "real" total a
# SECTION is entitled to see.
TRUE_TOTAL_PAGES = 13


class _User:
    """Stands in for AuthUser — the routes only ever read ``auth_id``."""

    auth_id = AUTH_ID
    email = "lawyer@example.com"
    role = "authenticated"


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """Module-level TTL caches + the route limiter's process-global fallback.

    The entity memo is the point of several tests below, so a leak between tests
    would make them assert nothing. ``lb.reset_process_state()`` is here for the
    NEXT module's benefit: the item-budget window is process-global and keyed by
    user, so a suite that never clears it accumulates across FILES until a later
    module's authed hub request gets a spurious 429.
    """
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()
    pl._total_pages_memo.clear()
    pl._reset_sector_memos()
    library_rate_limit._fallback.reset()
    lb.reset_process_state()
    yield
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()
    pl._total_pages_memo.clear()
    pl._reset_sector_memos()
    library_rate_limit._fallback.reset()
    lb.reset_process_state()


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
    return _client(_hub_fake(plan="pro", limit=100), _User())


def _free_client() -> TestClient:
    return _client(_hub_fake(plan="free", limit=10), _User())


def _stub_item(n: int) -> dict[str, Any]:
    return {
        "slug": f"guide-{n}",
        "title": "الدليل الشامل: خدمة",
        "provider_name": ENTITY_LABELS[BIGGEST],
        "summary": "",
        "image_count": 0,
    }


class _Exploding:
    """Any DB touch is a test failure."""

    def table(self, *_a: Any, **_k: Any):
        raise AssertionError("the database was touched")

    def rpc(self, *_a: Any, **_k: Any):
        raise AssertionError("the database was touched")


@pytest.fixture
def stubs(monkeypatch):
    """Stub the compliance lister + counter and the entity-count reader.

    Returns a dict recording what was called with what — the WIRING is under
    test here, not the SQL. ``calls['entity_counts']`` is how the memo tests
    count refreshes.
    """
    calls: dict[str, Any] = {
        "listers": [], "counter": [], "entity_counts": 0, "sector_counts": 0,
    }

    def _lister(_supabase: Any, **kw: Any) -> dict[str, Any]:
        calls["listers"].append(kw)
        return {
            "items": [_stub_item(i) for i in range(9)],
            "page": int(kw.get("page") or 1),
            "total_pages": TRUE_TOTAL_PAGES,
        }

    def _counter(*a: Any, **k: Any) -> int:
        calls["counter"].append((a[1:], k))
        return TRUE_TOTAL_PAGES

    def _entity_counts(_supabase: Any) -> dict[str, int]:
        calls["entity_counts"] += 1
        return dict(ENTITY_PUBLISHED)

    def _sector_counts(_supabase: Any) -> dict[str, dict[str, int]]:
        # Only reached by the SECTOR half of the D1 tests — the anon sector wall
        # asks the sector memo for its real total. Stubbed so those tests assert
        # the GATE rather than tripping over the counts RPC.
        calls["sector_counts"] += 1
        return {SECTOR_SLUG: {s: 9 for s in ls.SECTOR_COUNT_SECTIONS}}

    monkeypatch.setattr(ls, "list_compliance_hub", _lister)
    monkeypatch.setattr(ls, "compliance_hub_total_pages", _counter)
    monkeypatch.setattr(ls, "compliance_entity_counts", _entity_counts)
    monkeypatch.setattr(ls, "sector_counts", _sector_counts)
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
# 1. §3 — the vocabulary resolves IN MEMORY, before any DB work
# ===========================================================================


def test_all_twenty_eight_slugs_resolve() -> None:
    """The map is IMPORTED, never retyped: a 29th issuing body added to
    ``shared/library/entities.py`` must not need a second edit in the API layer
    to become servable."""
    assert len(ENTITY_ORDER) == 28
    for slug in ENTITY_ORDER:
        name = pl._entity_section(slug)
        assert name == ENTITY_LABELS[slug], slug
        assert name, slug


def test_slugs_are_latin_kebab_case_and_unique() -> None:
    """D2/§3: /compliance is the INDEXED wing, so structural segments are Latin —
    the inverse of the courts axis, which went Arabic because /judgments is
    ``noindex`` behind the PDPL gate and had no SEO neutrality left to buy. Latin
    also means Next hands the segment back unencoded: there is no decode dance
    and no ISR percent-encoding trap."""
    assert all(s.isascii() for s in ENTITY_ORDER)
    bad = [s for s in ENTITY_ORDER if not _SLUG_RE.match(s)]
    assert not bad, f"not url-safe kebab-case: {bad}"
    assert len(set(ENTITY_ORDER)) == 28


def test_every_label_is_distinct() -> None:
    """28 slugs, 28 issuing bodies. Two slugs sharing a ``provider_name`` would
    make one section a silent duplicate of the other, and both counts wrong."""
    assert len(set(ENTITY_LABELS.values())) == 28


def test_browse_order_is_by_volume_not_alphabetical() -> None:
    """§3: insertion order IS the browse order, and the SERVER owns it.
    Alphabetical would land وزارة العدل (115 guides) among nine authorities
    holding one guide each."""
    assert ENTITY_ORDER[0] == "ministry-of-justice"
    assert ENTITY_ORDER != sorted(ENTITY_ORDER)


def test_the_reverse_lookup_is_built_from_the_same_map() -> None:
    """``slug_for_name`` turns the provider line on a card into a link. It must
    round-trip every entry, or a card would link to a section that lists it
    nowhere."""
    for slug in ENTITY_ORDER:
        assert slug_for_name(ENTITY_LABELS[slug]) == slug


def test_an_unclaimed_provider_name_is_reported_not_raised() -> None:
    """Drift is LOG-AND-OMIT. ``unclaimed_entities`` is the CI probe; the module
    must never raise at import, because a pipeline re-ingest of ``services``
    would then be able to crash the backend's boot."""
    assert unclaimed_entities(["وزارة العدل", "جهة جديدة تمامًا"]) == [
        "جهة جديدة تمامًا"
    ]
    assert unclaimed_entities(ENTITY_LABELS.values()) == []


@pytest.mark.parametrize("reserved", sorted(RESERVED_SLUGS))
def test_a_reserved_segment_is_never_an_entity(reserved) -> None:
    """`/compliance/page/2`, `/compliance/entities` and `/compliance/mine` must
    never resolve as an entity, in EITHER namespace — Next resolves static
    segments first, but the backend refuses them too so the two cannot collide.
    ``name_for_slug`` returns None even if a future edit adds one to the map."""
    assert name_for_slug(reserved) is None


def test_reserved_and_entity_slugs_do_not_overlap() -> None:
    assert not (ENTITY_SLUG_VOCAB & RESERVED_SLUGS)


@pytest.mark.parametrize(
    "bogus", ["zzz", "ministry", "وزارة العدل", "%2e%2e", "*", "ministry_of_justice"]
)
def test_an_unknown_entity_is_refused(stubs, bogus) -> None:
    _is_arabic_refusal(_client().get(COMPLIANCE, params={"entity_slug": bogus}))


@pytest.mark.parametrize("reserved", sorted(RESERVED_SLUGS))
def test_a_reserved_entity_slug_is_refused_by_the_hub(stubs, reserved) -> None:
    _is_arabic_refusal(_client().get(COMPLIANCE, params={"entity_slug": reserved}))


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_entity_is_not_a_section_and_not_an_error(stubs, blank) -> None:
    """An unfiltered hub is the normal case and must not 400."""
    res = _client().get(COMPLIANCE, params={"entity_slug": blank})
    assert res.status_code == 200
    assert stubs["listers"][-1]["entity"] is None


@pytest.mark.parametrize("bad", ["zzz", *sorted(RESERVED_SLUGS)])
def test_a_rejected_entity_never_reaches_the_database(bad) -> None:
    """§12.7: the whole decision is a dict lookup, so probing the namespace costs
    no round-trip and a refusal cannot become its own load generator."""
    _is_arabic_refusal(_client(_Exploding()).get(COMPLIANCE, params={"entity_slug": bad}))


def test_an_entity_rejection_is_never_shared_cached(stubs) -> None:
    """A rejection is a property of the REQUEST; one parked in the edge cache
    would be replayed to everyone who asks for that entity afterwards."""
    res = _client().get(COMPLIANCE, params={"entity_slug": "zzz"})
    assert "no-store" in res.headers.get("cache-control", "")


def test_the_slug_reaches_the_service_as_the_RAW_PROVIDER_NAME(stubs) -> None:
    """The route resolves the slug; the service never sees it. That is what keeps
    ``shared/library/entities.py`` the ONLY place the pairing exists."""
    _paid_client().get(COMPLIANCE, params={"entity_slug": BIGGEST})
    assert stubs["listers"][-1]["entity"] == ENTITY_LABELS[BIGGEST]


@pytest.mark.parametrize("spelling", ["  ministry-of-justice  ", "MINISTRY-OF-JUSTICE"])
def test_the_slug_is_normalised_before_lookup(stubs, spelling) -> None:
    """Whitespace and case must land on the SAME memo entry, or the wall reports
    «1 page» for a section holding 115 guides."""
    res = _client().get(COMPLIANCE, params={"entity_slug": spelling, "page": 2})
    assert res.status_code == 200
    assert res.json()["total_pages"] == ENTITY_PAGES[BIGGEST]


# ===========================================================================
# 2. §2/D1 — the entity axis is a SECTION, so anon gets REAL counts
# ===========================================================================


def test_an_anon_entity_wall_reports_the_REAL_page_count(stubs) -> None:
    """THE ``filtered`` TEST. Anon hits the depth cap at page 2 and the CTA-wall
    body must still carry the section's real total, from the memo — not the flat
    ``_ANON_WALL_TOTAL_PAGES`` ceiling. This is plan §8's first route criterion.
    """
    res = _client().get(COMPLIANCE, params={"entity_slug": BIGGEST, "page": 2})

    body = res.json()
    assert body["cap_reached"] is True
    assert body["total_pages"] == ENTITY_PAGES[BIGGEST] == 13
    assert body["total_pages"] != pl._ANON_WALL_TOTAL_PAGES


def test_an_entity_route_never_reports_a_two_page_corpus(stubs) -> None:
    """Every entity with more than 2 pages must report more than 2 pages to anon.
    The failure this guards is TOTALLY SILENT — page 1 still renders, nothing
    errors, and the paginator just lies."""
    walled = [
        s for s in ENTITY_ORDER if ENTITY_PAGES[s] > pl._ANON_WALL_TOTAL_PAGES
    ]
    assert walled, "the stub fixture must contain at least one deep entity"
    for slug in walled:
        body = _client().get(
            COMPLIANCE, params={"entity_slug": slug, "page": 2}
        ).json()
        assert body["total_pages"] == ENTITY_PAGES[slug], slug


def test_a_SERVED_anon_entity_page_reports_the_listers_real_total(stubs) -> None:
    """Clamping the wall alone would not close the oracle — page 1 carries the
    same number. ``_visible_total_pages`` must leave it alone for a section."""
    body = _client().get(COMPLIANCE, params={"entity_slug": BIGGEST}).json()

    assert body["cap_reached"] is False
    assert body["total_pages"] == TRUE_TOTAL_PAGES


def test_an_entity_COMBINED_with_a_filter_is_filtered_again(stubs) -> None:
    """The section is the base set; a free-text ``provider`` is still a probe, and
    the answer moves with it. So the pair takes the FILTERED branch."""
    body = _client().get(
        COMPLIANCE,
        params={"entity_slug": BIGGEST, "provider": "وزارة", "page": 2},
    ).json()

    assert body["total_pages"] == pl._ANON_WALL_TOTAL_PAGES


def test_a_SERVED_entity_plus_a_filter_page_is_still_clamped(stubs) -> None:
    body = _client().get(
        COMPLIANCE, params={"entity_slug": BIGGEST, "provider": "وزارة"}
    ).json()

    assert body["total_pages"] == pl._ANON_WALL_TOTAL_PAGES


def test_an_entity_plus_an_anonymous_q_stays_a_SECTION(stubs) -> None:
    """D9 DROPS an anon ``q`` rather than refusing it, so the request that
    actually runs is the plain section — and it must be counted as one. Treating
    the dropped param as a filter would wall a page nobody filtered."""
    body = _client().get(
        COMPLIANCE, params={"entity_slug": BIGGEST, "q": "تجديد", "page": 2}
    ).json()

    assert body["total_pages"] == ENTITY_PAGES[BIGGEST]


def test_an_empty_entity_section_reports_one_page_not_zero(stubs) -> None:
    """Nine of the 28 hold a single guide and one may publish none. A zero-page
    paginator renders broken; the listers already floor at 1 and the memo must
    agree."""
    body = _client().get(
        COMPLIANCE, params={"entity_slug": SMALLEST, "page": 2}
    ).json()

    assert ENTITY_PUBLISHED[SMALLEST] == 0
    assert body["total_pages"] == 1


def test_an_authed_wall_uses_the_real_counter_not_the_memo(stubs) -> None:
    """A signed-in caller keeps the real number throughout, and it comes from the
    counter (which sees the same ``entity`` predicate), never from the memo."""
    _free_client().get(COMPLIANCE, params={"entity_slug": BIGGEST, "page": 9})

    assert stubs["counter"], "the authed wall must call the counter"
    assert stubs["entity_counts"] == 0, "the memo is an ANON path only"
    # The counter is called positionally: (provider, sector, q, entity).
    args, _kw = stubs["counter"][-1]
    assert args[-1] == ENTITY_LABELS[BIGGEST]


# ===========================================================================
# 3. §2/D1 — the DEPTH cap and the SECTION gate both survived the exemption
# ===========================================================================


def test_an_anon_caller_IS_SERVED_an_entity_page_one(stubs) -> None:
    """THE D1 ACCEPTANCE TEST (plan §9.2). If this walls, the exemption was not
    wired and every entity page is a 28-way CTA wall for the readers and
    crawlers it exists for."""
    res = _client().get(COMPLIANCE, params={"entity_slug": BIGGEST})

    assert res.status_code == 200
    body = res.json()
    assert body["cap_reached"] is False
    assert len(body["items"]) == 9


def test_a_free_account_IS_SERVED_an_entity_page_one(stubs) -> None:
    """Free is the other tier the section gate refuses on the sector axis."""
    body = _free_client().get(COMPLIANCE, params={"entity_slug": BIGGEST}).json()

    assert body["cap_reached"] is False
    assert len(body["items"]) == 9


def test_the_anon_DEPTH_cap_is_untouched_by_the_entity_axis(stubs) -> None:
    """D1 exempts the SECTION gate, not the depth cap. Anon still gets page 1 and
    the wall at page 2 — وزارة العدل's 13 pages are not an anon-readable
    115-card list."""
    assert ls.ANON_HUB_MAX_PAGE == 1
    served = _client().get(COMPLIANCE, params={"entity_slug": BIGGEST}).json()
    walled = _client().get(
        COMPLIANCE, params={"entity_slug": BIGGEST, "page": 2}
    ).json()

    assert served["cap_reached"] is False
    assert walled["cap_reached"] is True
    assert walled["items"] == []


def test_a_free_account_still_stops_at_page_three_on_an_entity_page(stubs) -> None:
    body = _free_client().get(
        COMPLIANCE, params={"entity_slug": BIGGEST, "page": ls.FREE_HUB_MAX_PAGE + 1}
    ).json()

    assert body["cap_reached"] is True


def test_a_walled_entity_page_yields_nothing_so_it_is_not_metered(
    stubs, monkeypatch
) -> None:
    """Ordering rule: a walled response yields no items, so it must not be
    charged against the per-user item budget."""
    charged: list[Any] = []
    monkeypatch.setattr(
        lb,
        "charge_items",
        lambda *a, **k: charged.append((a, k)) or None,
    )
    _client().get(COMPLIANCE, params={"entity_slug": BIGGEST, "page": 2})
    assert not charged


# --- the OTHER half of D1: the sector axis did NOT move --------------------


def test_an_anon_caller_is_still_REFUSED_a_sector_scoped_compliance_page(
    stubs,
) -> None:
    """⚠ D1 IS ONE WING'S OWN AXIS, NOT A GENERAL UN-GATING. ``sector_slug``
    keeps feeding ``section_scoped`` on this very handler: sector is the
    cross-wing axis governed by the shared rule, entity is /compliance's own.
    Do not "harmonise" them without redoing plan §2."""
    body = _client().get(COMPLIANCE, params={"sector_slug": SECTOR_SLUG}).json()

    assert body["cap_reached"] is True


def test_entity_PLUS_sector_is_still_paid_only(stubs) -> None:
    """Plan §8's fourth route criterion: adding the exempt axis must not smuggle
    the gated one past the gate."""
    anon = _client().get(
        COMPLIANCE, params={"entity_slug": BIGGEST, "sector_slug": SECTOR_SLUG}
    ).json()
    free = _free_client().get(
        COMPLIANCE, params={"entity_slug": BIGGEST, "sector_slug": SECTOR_SLUG}
    ).json()
    paid = _paid_client().get(
        COMPLIANCE, params={"entity_slug": BIGGEST, "sector_slug": SECTOR_SLUG}
    ).json()

    assert anon["cap_reached"] is True
    assert free["cap_reached"] is True
    assert paid["cap_reached"] is False


def test_the_section_gate_itself_was_not_edited() -> None:
    """D1 is a NON-EDIT. ``section_scope_allowed`` and ``_SECTION_SCOPE_TIERS``
    must be exactly as they were, or the exemption leaked into every wing."""
    assert pl._SECTION_SCOPE_TIERS == frozenset({"paid"})
    assert pl.section_scope_allowed("paid") is True
    assert pl.section_scope_allowed("free") is False
    assert pl.section_scope_allowed("anon") is False
    assert pl.section_scope_allowed("bogus-tier") is False


# ===========================================================================
# 4. §4.1 — GET /public/library/compliance/entities
# ===========================================================================


def test_the_entities_endpoint_lists_all_28_in_the_servers_order(stubs) -> None:
    body = _client().get(ENTITIES).json()

    assert [e["slug"] for e in body["entities"]] == ENTITY_ORDER
    assert [e["label"] for e in body["entities"]] == [
        ENTITY_LABELS[s] for s in ENTITY_ORDER
    ]


def test_the_response_shape_is_exactly_what_the_frontend_codes_against(
    stubs,
) -> None:
    """``{"entities": [{slug, label, count, total_pages}]}`` — the literal shape
    ``lib/library/entities.ts`` consumes. Renaming a field here is a silent
    frontend break, since the grid would render 28 tiles with no numbers."""
    body = _client().get(ENTITIES).json()

    assert set(body) == {"entities"}
    assert set(body["entities"][0]) == {"slug", "label", "count", "total_pages"}


def test_an_entity_tile_carries_its_published_count_and_page_count(stubs) -> None:
    tiles = {e["slug"]: e for e in _client().get(ENTITIES).json()["entities"]}

    assert tiles[BIGGEST]["count"] == ENTITY_PUBLISHED[BIGGEST]
    assert tiles[BIGGEST]["total_pages"] == ENTITY_PAGES[BIGGEST]
    assert tiles[SMALLEST]["count"] == 0
    assert tiles[SMALLEST]["total_pages"] == 1


def test_the_grid_count_and_the_wall_count_agree(stubs) -> None:
    """Two surfaces, one memo. A tile advertising 13 pages beside a paginator
    that dead-ends at 2 is the §12.2 contradiction on a different axis."""
    tile = {e["slug"]: e for e in _client().get(ENTITIES).json()["entities"]}[BIGGEST]
    wall = _client().get(COMPLIANCE, params={"entity_slug": BIGGEST, "page": 2}).json()

    assert tile["total_pages"] == wall["total_pages"]


def test_the_entity_counts_are_memoised(stubs) -> None:
    """A real number on the ANON path must not cost a query per request, and this
    axis is anon-visible (D1) — so the memo carries more traffic here than the
    sector/court ones do."""
    client = _client()
    for _ in range(4):
        client.get(ENTITIES)
        client.get(COMPLIANCE, params={"entity_slug": BIGGEST, "page": 2})

    assert stubs["entity_counts"] == 1


def test_an_expired_entity_memo_refreshes(stubs) -> None:
    _client().get(ENTITIES)
    pl._entity_memo_at["at"] -= pl._TOTAL_PAGES_TTL_SECONDS + 1
    _client().get(ENTITIES)

    assert stubs["entity_counts"] == 2


def test_the_entity_memo_is_never_handed_out_by_reference(stubs) -> None:
    """Handing a handler the module dict lets one request corrupt what every
    other one reads for the rest of the TTL — the F5 fix on the sector memo,
    which cost a real bug hunt once."""
    import asyncio

    first = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        pl._entity_counts(_hub_fake())
    )
    first[BIGGEST] = -999
    assert pl._entity_counts_memo[BIGGEST] == ENTITY_PUBLISHED[BIGGEST]


def test_the_entities_route_is_not_shadowed_by_the_guide_route(stubs) -> None:
    """⚠ FastAPI matches in DECLARATION order. Declared below
    ``/compliance/{slug}``, the literal ``entities`` is swallowed as a guide slug
    and this endpoint answers 404 «الدليل غير موجود». The response body is the
    assertion, not the status: a 200 from the WRONG handler is impossible, but a
    404 would look like an ordinary missing guide."""
    res = _client().get(ENTITIES)

    assert res.status_code == 200
    assert "entities" in res.json()

    from backend.app.main import create_app

    paths = [getattr(r, "path", "") for r in create_app().routes]
    assert paths.index("/api/v1/public/library/compliance/entities") < paths.index(
        "/api/v1/public/library/compliance/{slug}"
    )


def test_the_entities_list_keeps_the_shared_hour_cache_for_anon(stubs) -> None:
    res = _client().get(ENTITIES)
    assert "public" in res.headers.get("cache-control", "")


def test_an_authed_entities_list_is_never_shared_cached(stubs) -> None:
    res = _paid_client().get(ENTITIES)
    assert "no-store" in res.headers.get("cache-control", "")


def test_the_entities_route_shares_the_compliance_item_rate_limit_bucket() -> None:
    """It must not buy a caller a SECOND budget alongside the guide pages."""
    assert rate_limit.normalize_rate_limit_path(ENTITIES) == (
        "/api/v1/public/library/compliance/:item"
    )


# ===========================================================================
# 5. D2.3 — the shared slug namespace, refused on the server too
# ===========================================================================


# ``entities`` is excluded here and only here: it never REACHES the guide
# handler, because the list route above claims that literal path outright. Both
# halves of D2.3 hold for it — the segment is reserved AND unreachable as a slug
# — but the observable answer is a 200 from the other handler, which
# ``test_the_entities_route_is_not_shadowed_by_the_guide_route`` asserts instead.
@pytest.mark.parametrize(
    "reserved", sorted((RESERVED_SLUGS | ENTITY_SLUG_VOCAB) - {"entities"})
)
def test_the_guide_route_refuses_the_reserved_set_without_touching_the_db(
    reserved,
) -> None:
    """The frontend dispatches entity-FIRST, so an entity slug SHADOWS a
    same-named guide. Refusing the same set server-side is what stops the two
    layers drifting into disagreeing about what a slug means."""
    res = _client(_Exploding()).get(f"{COMPLIANCE}/{reserved}")
    _is_arabic_refusal(res, status=404)


def test_a_normal_guide_slug_still_resolves(monkeypatch) -> None:
    """The refusal above must not become a filter on ordinary slugs."""
    monkeypatch.setattr(
        ls,
        "get_compliance_guide",
        lambda _s, slug: {
            "slug": slug,
            "title": "الدليل الشامل: تجديد السجل التجاري",
            "summary": "دليل مبسط.",
            "provider_name": ENTITY_LABELS[BIGGEST],
            "service_url": None,
            "image_count": 0,
            "guide_md": "نص",
            "images": [],
            "related_next": [],
        },
    )
    res = _client().get(f"{COMPLIANCE}/renew-commercial-registration")

    assert res.status_code == 200
    assert res.json()["slug"] == "renew-commercial-registration"


# ===========================================================================
# 6. §6.3/§6.5 — the guides joined the search index
# ===========================================================================


def test_compliance_is_a_public_search_corpus() -> None:
    assert "compliance" in search_service.PUBLIC_CORPORA


def test_the_search_section_key_matches_the_hub_budget_key() -> None:
    """⚠ This is what keeps a search hit and a browse hit on the SAME guide
    charging ONE item. ``_charge_hub_yield`` keys the hub on ``"compliance"``;
    get this wrong and the item budget forks silently."""
    assert search_service.CORPUS_SECTION["compliance"] == "compliance"


def test_a_compliance_hit_resolves_to_the_guide_url() -> None:
    assert search_service.public_url("compliance", "renew-cr") == "/compliance/renew-cr"


def test_the_legacy_service_corpus_stayed_exactly_where_it_was() -> None:
    """Plan §6.3/§10: ``service`` is 100 INERT rows keyed by ``services.id``
    carrying the retired wing's Arabic slugs. It is out of ``PUBLIC_CORPORA`` and
    has no URL prefix (every URL built from one is a 404); it is kept alive only
    for ``manual_search``'s rung-③ exact-title pin. Retiring it is a separate
    decision, and repointing it at the new corpus is NOT this plan's."""
    assert "service" not in search_service.PUBLIC_CORPORA
    assert search_service.public_url("service", "أي-شيء") is None


def test_the_compliance_facets_are_the_ones_the_index_writes() -> None:
    assert search_service.FACET_KEYS["compliance"] == frozenset(
        {"provider_name", "service_ref", "sectors"}
    )


def test_the_hub_q_now_takes_the_bm25_path(monkeypatch) -> None:
    """§6.5 — THE REVERSAL. ``_compliance_matches``'s ``q`` branch used to be a
    substring over ``title + summary``, under a comment reading «``q`` HERE IS NOT
    BM25 AND MUST NOT BECOME IT» — correct while the guides were absent from
    ``search_index``, void since they joined it as the ``compliance`` corpus. The
    ranked ids must come from the RPC, and the wing's own predicates must remain
    POST-filters over them."""
    seen: dict[str, Any] = {}

    def _ids(_supabase, corpus, query, **_k):
        seen["corpus"] = corpus
        seen["q"] = query
        return ["g-2", "g-1"]

    rows = {
        "g-1": {"id": "g-1", "provider_name": ENTITY_LABELS[BIGGEST], "title": "أ"},
        "g-2": {"id": "g-2", "provider_name": ENTITY_LABELS[SMALLEST], "title": "ب"},
    }
    monkeypatch.setattr(search_service, "corpus_search_ids", _ids)
    monkeypatch.setattr(
        ls,
        "_fetch_corpus_by_ids",
        lambda _s, _t, _c, ids, _f: [rows[i] for i in ids],
    )
    monkeypatch.setattr(ls, "_slug_map", lambda _s, _t, ids: {str(i): f"s-{i}" for i in ids})
    monkeypatch.setattr(
        ls, "_published_ids", lambda *a, **k: pytest.fail("browse path in search mode")
    )

    kept, _slugs, truncated = ls._compliance_published_rows(
        object(), "id, provider_name, title", q="تجديد"
    )

    assert seen == {"corpus": "compliance", "q": "تجديد"}
    assert truncated is False
    # BM25 order is preserved — NOT re-sorted by ``most_used_rank``.
    assert [r["id"] for r in kept] == ["g-2", "g-1"]

    # …and the entity predicate still post-filters the ranked candidates.
    kept2, _s2, _t2 = ls._compliance_published_rows(
        object(), "id, provider_name, title", q="تجديد", entity=ENTITY_LABELS[BIGGEST]
    )
    assert [r["id"] for r in kept2] == ["g-1"]


def test_the_entity_predicate_is_EXACT_never_a_substring() -> None:
    """⚠ THE ONE PREDICATE DIFFERENCE THAT MAKES THIS A SECTION. ``provider`` is
    an ``ilike`` substring facet; ``entity`` is ``==``. Make the second a
    substring and «وزارة» folds eleven ministries into one "section" whose total
    drifts with the query — at which point the counts stop being fixed and the
    exemption keeping it out of ``filtered`` no longer holds."""
    moj = {"provider_name": "وزارة العدل"}
    moc = {"provider_name": "وزارة التجارة"}

    # A substring of a real name matches NOTHING as an entity…
    assert ls._compliance_matches(moj, None, None, "وزارة") is False
    # …but the exact name matches, and only it.
    assert ls._compliance_matches(moj, None, None, "وزارة العدل") is True
    assert ls._compliance_matches(moc, None, None, "وزارة العدل") is False
    # ``provider`` keeps its substring semantics on the same column.
    assert ls._compliance_matches(moj, "وزارة", None, None) is True
    assert ls._compliance_matches(moc, "التجارة", None, None) is True


def test_the_sector_count_wiring_was_not_touched() -> None:
    """The entity axis does not go through sector counting — plan §4.2's last
    bullet. Both of these must read exactly as they did."""
    assert ls._SECTION_SOURCES["compliance"] == (
        "library_compliance_v",
        "compliance",
        "sectors",
    )
    assert "compliance" in ls._RPC_SECTOR_COUNT_EXCLUDED


# ===========================================================================
# 7. LIVE — the drift checks. Skipped when Supabase is unreachable.
#
# ⚠ THESE ARE THE ONLY TESTS THAT CAN CATCH A CORPUS RENAME. The vocabulary
# degrades on drift by design (log-and-omit, never raise at import), so nothing
# else fails when a pipeline re-ingest of ``services`` renames an issuing body —
# the section simply renders empty and its tile reads «0». The fatha in
# «الهيئة السعودية للمقَيّمين المعتمدين (تقييم)» was caught here on 2026-08-23,
# against a plan table that had retyped it away.
# ===========================================================================


def _live_guides() -> list[dict[str, Any]]:
    try:
        from shared.db.client import get_admin_client

        client = get_admin_client()
        rows = client.table("library_compliance_v").select("id, provider_name").execute()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"live Supabase unavailable: {e}")
    return rows.data or []


def _live_slugs() -> set[str]:
    try:
        from shared.db.client import get_admin_client

        client = get_admin_client()
        rows = (
            client.table("seo_item_meta")
            .select("slug")
            .eq("content_type", "compliance")
            .not_.is_("slug", "null")
            .limit(2000)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"live Supabase unavailable: {e}")
    return {(r.get("slug") or "").strip() for r in (rows.data or []) if r.get("slug")}


def test_LIVE_the_vocabulary_equals_the_corpus_exactly() -> None:
    """Set equality in BOTH directions. A missing name means that body has no
    section page; a stray means a section that matches zero rows and prints «0»
    on the grid. Both are invisible at runtime by design."""
    live = {(r.get("provider_name") or "").strip() for r in _live_guides()}

    assert unclaimed_entities(live) == [], "live provider_name(s) with no slug"
    assert sorted(set(ENTITY_LABELS.values()) - live) == [], "slugs matching no rows"
    assert len(live) == len(ENTITY_ORDER) == 28


def test_LIVE_no_guide_slug_collides_with_an_entity_slug_or_a_reserved_word() -> None:
    """D2's cost, paid: `/compliance/{slug}` is a shared namespace and the
    frontend dispatches entity-first, so a collision 404s a URL that is in the
    sitemap. ∅ verified over all 337 live slugs."""
    slugs = _live_slugs()

    assert slugs, "no published compliance slugs found"
    assert sorted(slugs & ENTITY_SLUG_VOCAB) == []
    assert sorted(slugs & RESERVED_SLUGS) == []


def test_LIVE_the_entity_counts_sum_to_the_whole_wing() -> None:
    """Plan §8: 28 rows summing to 337. Every guide belongs to exactly one
    entity, so the sum IS the wing — a shortfall means a ``provider_name`` no
    slug claims and guides missing from every section."""
    guides = _live_guides()
    counts: dict[str, int] = {slug: 0 for slug in ENTITY_ORDER}
    for row in guides:
        slug = slug_for_name(row.get("provider_name"))
        if slug:
            counts[slug] += 1

    assert len(counts) == 28
    assert sum(counts.values()) == len(guides)
    assert sum(counts.values()) == 337, (
        "the wing has changed size — re-verify plan §1 before editing this number"
    )
