"""Navigation hardening at the public-library HTTP boundary.

Plan: ``.claude/plans/cloudflare_navigation_hardening.md`` §2.1 · §3.2b · §3.7

Three controls, all of them in ``backend/app/api/public_library.py``, all of them
about ENUMERATION rather than entitlement (the unlock ledger already owns the
gated bytes — see ``test_library_enforcement.py``):

  * §2.1 — the filter hole. Every distinct filter value is a fresh page 1, so the
    depth cap alone bounds nothing: ~125 two-character ``q`` values walk the whole
    regulations corpus without ever asking for page 2. ``q`` now needs >= 3 chars,
    ``entity`` / ``doc_type`` / ``court_level`` / ``category`` are checked against
    their real vocabularies, and an anon CTA wall no longer reports (or even
    counts) the true corpus size.
  * §3.2b — the sitemap feed, 5,000 URLs a page, gated to internal callers behind
    an env flag that is OFF until Railway private networking lands.
  * §3.7 — verified search crawlers browse past the anon depth cap, because a
    capped Googlebot plus a gated sitemap is a crawler with no discovery path.

Fixture style + the in-memory PostgREST stand-in are REUSED from the access-tiers
files so tier resolution here is the real thing, not a mock. The app under test
is a throwaway FastAPI carrying only this router.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import public_library as pl
from backend.app.deps import get_current_user_optional, get_supabase
from backend.app.errors import LunaHTTPException, luna_exception_handler
from backend.app.middleware.route_limits import library_rate_limit
from backend.app.services import case_service, library_service as ls
from shared.seo.judgment_naming import COURT_LEVEL_LABELS

from backend.tests.test_library_gating import (  # noqa: F401
    USER,
    FakeSupabase,
    quota_row,
)

AUTH_ID = "auth-0000-1111"

# A real entity id shape + a real entity_ref token (the live corpus's are 4–6
# digit source refs — verified 2026-07-28, 132 distinct, all numeric).
ENTITY_UUID = "3f8c1d2e-0000-4000-8000-000000000001"
ENTITY_REF = "17900"


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """Module-level TTL caches + the route limiter's process-global fallback
    window; one test's state must never leak into the next."""
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


class _User:
    """Stands in for AuthUser — the routes only ever read ``auth_id``."""

    auth_id = AUTH_ID
    email = "lawyer@example.com"
    role = "authenticated"


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


def _client(
    supabase: Any = None,
    user: Optional[_User] = None,
    *,
    peer: str = "8.8.8.8",
) -> TestClient:
    """``peer`` is the socket address the app sees — public by default, so a test
    has to ASK for a private one (the §3.2b gate keys off it).

    ⚠ NOT one of the documentation ranges (``203.0.113.0/24`` & co): Python's
    ``ip_address().is_private`` follows the IANA special-purpose registry and
    calls those private, so a "public" test peer taken from RFC 5737 would sail
    straight through the gate and the test would assert nothing.
    """
    fake = supabase if supabase is not None else _hub_fake()
    return TestClient(_app(fake, user), client=(peer, 51000))


def _hub_fake(**qrow: Any) -> FakeSupabase:
    fake = FakeSupabase()
    fake.quota_row = quota_row(**qrow) if qrow else quota_row()
    return fake


# ---------------------------------------------------------------------------
# Hub stubs — the wiring is under test here, not the queries.
# ---------------------------------------------------------------------------

HUBS = [
    ("/api/v1/public/library/regulations", "list_regulations_hub",
     "regulations_hub_total_pages"),
    ("/api/v1/public/library/compliance", "list_compliance_hub",
     "compliance_hub_total_pages"),
    ("/api/v1/public/library/circulars", "list_circulars_hub",
     "circulars_hub_total_pages"),
    ("/api/v1/public/library/judgments", "list_judgments_hub",
     "judgments_hub_total_pages"),
    ("/api/v1/public/library/forms", "list_forms_hub", "forms_hub_total_pages"),
]

HUB_PATHS = [h[0] for h in HUBS]

REG_HUB = HUBS[0][0]
CIRC_HUB = HUBS[2][0]
JUD_HUB = HUBS[3][0]
FORMS_HUB = HUBS[4][0]
COMPLIANCE_HUB = HUBS[1][0]

# The true corpus size every stubbed counter reports. Anon must never see it.
TRUE_TOTAL_PAGES = 40

# ⚠ ``q`` IS NO LONGER AN ANON FILTER (bm25_navigation_search.md D9). Search is
# registered-only, and an anonymous ``?q=`` is DROPPED rather than refused — a
# shared search link must degrade to "here is the wing", not to an Arabic error
# page for a query the recipient never typed. So every test below that needs "an
# anonymous caller with A FILTER applied" uses a filter anon can still send; the
# ``q`` rules moved to the authed cases. One filter per hub, all validated
# against their real vocabularies:
ANON_FILTER = {
    HUBS[0][0]: {"doc_type": "law_statute"},   # regulations — closed vocab
    HUBS[1][0]: {"provider": "وزارة"},          # compliance  — free text, still 400s short
    HUBS[2][0]: {"entity": ENTITY_UUID},        # circulars   — authority id
    HUBS[3][0]: {"court_level": "appeal"},      # judgments   — closed vocab
    HUBS[4][0]: {"category": ls.FORM_CATEGORIES[0]},  # forms — closed vocab
}


def _stub_item(page: int) -> dict[str, Any]:
    """One card, valid for EVERY hub model (each response model only reads the
    fields it declares; the extras are ignored by pydantic's construction)."""
    return {
        "slug": f"item-{page}",
        "title": "عنوان",
        "status": "active",
        "court": "المحكمة",
        "body_snippet": "",
        "use_case_snippet": "",
        "intro_snippet": "",
        "snippet": "",
    }


@pytest.fixture
def stub_hubs(monkeypatch):
    """Every lister returns one card; every counter reports 40 pages and records
    that it was called (the anon wall must not call it at all)."""
    calls: dict[str, int] = {}

    for _path, lister, counter in HUBS:
        monkeypatch.setattr(
            ls, lister,
            lambda _supabase, **kw: {
                "items": [_stub_item(int(kw.get("page") or 1))],
                "page": int(kw.get("page") or 1),
                "total_pages": TRUE_TOTAL_PAGES,
            },
        )

        def _counter(*_a: Any, _name: str = counter, **_k: Any) -> int:
            calls[_name] = calls.get(_name, 0) + 1
            return TRUE_TOTAL_PAGES

        monkeypatch.setattr(ls, counter, _counter)

    return calls


def _body(res) -> dict[str, Any]:
    return res.json()


def _is_arabic_refusal(res, status: int = 400) -> None:
    """The project's standard envelope, an Arabic message, and nothing an
    intermediary may keep."""
    assert res.status_code == status, res.text
    body = _body(res)
    assert body["error"]["status"] == status
    assert body["error"]["code"] == "VALIDATION_ERROR"
    message = body["error"]["message"]
    assert message == body["detail"]
    assert message and not any("a" <= ch.lower() <= "z" for ch in message), message
    assert any("؀" <= ch <= "ۿ" for ch in message), message


# ===========================================================================
# 1. §2.1 — free-text filters need >= 3 characters
# ===========================================================================


@pytest.mark.parametrize("path", HUB_PATHS)
@pytest.mark.parametrize("term", ["ن", "نظ", " نظ ", "ab"])
def test_a_short_q_is_refused_for_a_signed_in_caller(stub_hubs, path, term) -> None:
    """THE HOLE. A two-character ``q`` partitions an Arabic corpus efficiently:
    ~125 of them yield the whole regulations wing, 9 items at a time, from page 1
    only — which is exactly the page the depth cap allows.

    The floor is now enforced for the callers whose ``q`` actually DOES anything,
    i.e. authenticated ones (D9). The contract itself is unchanged: >= 3, else a
    400 in Arabic."""
    res = _client(_hub_fake(), _User()).get(path, params={"q": term})
    _is_arabic_refusal(res)


@pytest.mark.parametrize("path", HUB_PATHS)
def test_three_characters_is_enough(stub_hubs, path) -> None:
    """>= 3 is the threshold, so exactly 3 must pass — an off-by-one here would
    break every real search box in the wing."""
    res = _client(_hub_fake(), _User()).get(path, params={"q": "نظا"})
    assert res.status_code == 200, res.text
    assert res.json()["items"]


# ===========================================================================
# 1b. D9 — search is registered-only, and anon's ``q`` is DROPPED not refused
# (.claude/plans/bm25_navigation_search.md D9)
# ===========================================================================


@pytest.mark.parametrize("path", HUB_PATHS)
@pytest.mark.parametrize("term", ["ن", "نظام العمل", "'; drop table--"])
def test_an_anonymous_q_is_ignored_never_refused(stub_hubs, path, term) -> None:
    """A registered user WILL share a ``?q=`` URL. The anonymous recipient must
    land on the wing, not on an error page for a query they never typed — so the
    param is silently dropped and page 1 is served. Length does not matter:
    nothing is measured because nothing is used."""
    res = _client().get(path, params={"q": term})
    assert res.status_code == 200, res.text
    assert res.json()["items"]


@pytest.mark.parametrize("path,lister", [(h[0], h[1]) for h in HUBS])
def test_an_anonymous_q_never_reaches_the_lister(
    stub_hubs, monkeypatch, path, lister
) -> None:
    """Enforcement is SERVER-SIDE (the UI's CTA modal is decoration). The wing's
    lister must be called with no search at all, or an anon caller would still be
    filtering — just without an error message."""
    seen: list[Any] = []
    inner = getattr(ls, lister)

    def _spy(_supabase: Any, **kw: Any):
        seen.append(kw.get("q"))
        return inner(_supabase, **kw)

    monkeypatch.setattr(ls, lister, _spy)

    _client().get(path, params={"q": "نظام العمل"})
    assert seen == [None], seen


@pytest.mark.parametrize("path,lister", [(h[0], h[1]) for h in HUBS])
def test_a_signed_in_q_DOES_reach_the_lister(
    stub_hubs, monkeypatch, path, lister
) -> None:
    """The other half of the same contract: an account is what makes the box
    work, so a signed-in caller's term must arrive intact (trimmed only)."""
    seen: list[Any] = []
    inner = getattr(ls, lister)

    def _spy(_supabase: Any, **kw: Any):
        seen.append(kw.get("q"))
        return inner(_supabase, **kw)

    monkeypatch.setattr(ls, lister, _spy)

    _client(_hub_fake(), _User()).get(path, params={"q": " نظام العمل "})
    assert seen == ["نظام العمل"], seen


def test_an_anonymous_dropped_q_is_never_shared_cached(stub_hubs) -> None:
    """The body is the ordinary unfiltered page, but the URL space is
    attacker-chosen and unbounded — shared-caching it mints one edge entry per
    query string, all holding the same page."""
    res = _client().get(REG_HUB, params={"q": "نظام العمل"})
    assert res.headers["cache-control"] == "private, no-store"


def test_an_unfiltered_anon_hub_is_still_shared_cached(stub_hubs) -> None:
    """The rule above must not have swallowed the normal anon path — the ISR
    bake depends on it."""
    res = _client().get(REG_HUB)
    assert res.headers["cache-control"] == "public, max-age=3600"


@pytest.mark.parametrize("path", HUB_PATHS)
@pytest.mark.parametrize("term", ["", "   "])
def test_a_blank_q_is_not_a_filter_and_not_an_error(stub_hubs, path, term) -> None:
    """An unfiltered hub is the NORMAL case (it is what the ISR renderer asks
    for). Blank must stay a no-op, never a 400."""
    res = _client().get(path, params={"q": term})
    assert res.status_code == 200, res.text


def test_provider_is_free_text_and_takes_the_same_rule(stub_hubs) -> None:
    """``provider`` is an ``ilike`` on ``provider_name`` — a second search box on
    the compliance hub, and just as good a partitioning key as ``q``."""
    assert _client().get(COMPLIANCE_HUB, params={"provider": "ال"}).status_code == 400
    assert _client().get(
        COMPLIANCE_HUB, params={"provider": "وزارة"}
    ).status_code == 200


def test_a_rejected_filter_never_reaches_the_database(stub_hubs) -> None:
    """Validation runs BEFORE tier resolution and before any query — a refusal
    must cost nothing, or the 400 becomes its own cheap load generator.

    Authed, because D9 means only an authed ``q`` is validated at all; the
    ORDERING property under test (validate, then resolve the tier, then query) is
    what keeps that true."""

    class _Exploding:
        def table(self, *_a: Any, **_k: Any):
            raise AssertionError("the database was touched for a rejected filter")

    res = _client(_Exploding(), _User()).get(REG_HUB, params={"q": "نظ"})
    assert res.status_code == 400


def test_a_rejection_is_never_shared_cached(stub_hubs) -> None:
    """A rejection is a property of the REQUEST. One parked in the edge cache
    under a hub URL would be replayed to everyone asking for that filter."""
    res = _client(_hub_fake(), _User()).get(REG_HUB, params={"q": "نظ"})
    assert res.headers["cache-control"] == "private, no-store"


# ===========================================================================
# 2. §2.1 — closed vocabularies
# ===========================================================================


def test_doc_type_vocabulary_is_the_live_bucket_map() -> None:
    """The vocabulary is IMPORTED, never retyped: a new pipeline bucket must not
    become a 400 by being added in one place and forgotten here. Verified against
    all 3,373 corpus rows on 2026-07-28: 21 distinct buckets, zero nulls, zero
    values outside this map."""
    assert pl._DOC_TYPE_VOCAB == frozenset(ls.DOC_TYPE_BUCKET_LABELS)
    assert "law_statute" in pl._DOC_TYPE_VOCAB
    assert len(pl._DOC_TYPE_VOCAB) == 21


@pytest.mark.parametrize("bucket", sorted(ls.DOC_TYPE_BUCKET_LABELS))
def test_every_live_doc_type_bucket_is_accepted(stub_hubs, bucket) -> None:
    res = _client().get(REG_HUB, params={"doc_type": bucket})
    assert res.status_code == 200, res.text


@pytest.mark.parametrize(
    "bogus",
    ["نظام", "law", "law_statute ; select", "%", "unknown_bucket", "LAW_STATUTE"],
)
def test_an_unknown_doc_type_is_refused(stub_hubs, bogus) -> None:
    """Case included on purpose: the column stores the raw lowercase enum, so
    ``LAW_STATUTE`` would match nothing while still minting a fresh cache key."""
    _is_arabic_refusal(_client().get(REG_HUB, params={"doc_type": bogus}))


@pytest.mark.parametrize("value", [ENTITY_UUID, ENTITY_REF, "5000", "1"])
def test_a_real_entity_token_is_accepted(stub_hubs, value) -> None:
    """``entity`` matches ``entity_id`` when it is a UUID, else ``entity_ref`` —
    which the live corpus fills with 4–6 digit numeric source refs."""
    assert _client().get(REG_HUB, params={"entity": value}).status_code == 200


@pytest.mark.parametrize(
    "bogus",
    ["وزارة", "ministry", "17900a", "123456789012", "*", "1 or 1=1"],
)
def test_an_entity_outside_the_token_space_is_refused(stub_hubs, bogus) -> None:
    """An exact ``eq`` on a value of the wrong SHAPE cannot match a row — all it
    can do is mint another cache key and another page 1."""
    _is_arabic_refusal(_client().get(REG_HUB, params={"entity": bogus}))


def test_the_circulars_entity_filter_is_free_text_in_disguise(stub_hubs) -> None:
    """No denormalized entity column on that wing, so a non-UUID value is
    resolved ``ilike`` against ``entities.entity_name``: «ا» matches most of the
    authority list. Same >= 3 rule; a UUID is an exact id and passes."""
    assert _client().get(CIRC_HUB, params={"entity": "ا"}).status_code == 400
    assert _client().get(CIRC_HUB, params={"entity": "وزارة"}).status_code == 200
    assert _client().get(CIRC_HUB, params={"entity": ENTITY_UUID}).status_code == 200


@pytest.mark.parametrize("level", sorted(COURT_LEVEL_LABELS))
def test_every_real_court_level_is_accepted(stub_hubs, level) -> None:
    assert _client().get(JUD_HUB, params={"court_level": level}).status_code == 200


@pytest.mark.parametrize("bogus", ["ابتدائي", "first-instance", "cassation", "1"])
def test_an_unknown_court_level_is_refused(stub_hubs, bogus) -> None:
    _is_arabic_refusal(_client().get(JUD_HUB, params={"court_level": bogus}))


@pytest.mark.parametrize("category", sorted(ls.FORM_CATEGORIES))
def test_every_real_form_category_is_accepted(stub_hubs, category) -> None:
    assert _client().get(FORMS_HUB, params={"category": category}).status_code == 200


@pytest.mark.parametrize("bogus", ["labour", "عمالي", "*"])
def test_an_unknown_form_category_is_refused(stub_hubs, bogus) -> None:
    _is_arabic_refusal(_client().get(FORMS_HUB, params={"category": bogus}))


# ===========================================================================
# 3. §2.1 — the anon CTA wall stops leaking (and stops counting)
# ===========================================================================


@pytest.mark.parametrize("path", HUB_PATHS)
def test_the_anon_wall_does_not_report_a_FILTERED_corpus_size(stub_hubs, path) -> None:
    """It used to ship the real filtered count with zero items — i.e. a counting
    oracle for any filter, readable without ever being served a row.

    ⚠ SCOPED to filtered requests 2026-07-30. The oracle is a count that MOVES
    with the probe; a single fixed number for the whole section is not one, and
    withholding it left the paginator dead-ending at page 2 on a 30,000-row
    corpus. Unfiltered now reports the truth — asserted directly below.

    ⚠ The probe is no longer ``q`` (D9 drops it for anon, which closes this hole
    for that param outright); it is whichever filter the wing still lets an
    anonymous caller send — see ``ANON_FILTER``."""
    params = {"page": 2, **ANON_FILTER[path]}
    body = _body(_client().get(path, params=params))

    assert body["cap_reached"] is True
    assert body["items"] == []
    assert body["total_pages"] == pl._ANON_WALL_TOTAL_PAGES
    assert body["total_pages"] < TRUE_TOTAL_PAGES


@pytest.mark.parametrize("path", HUB_PATHS)
def test_an_anon_q_wall_reports_the_UNFILTERED_size(stub_hubs, path) -> None:
    """The D9 corollary. A dropped ``q`` leaves an UNFILTERED request, so the
    wall answers with the section total like any other unfiltered one — the same
    number for every query string, which is what makes ``q`` useless as a probe
    rather than merely capped."""
    body = _body(_client().get(path, params={"page": 2, "q": "نظام"}))

    assert body["cap_reached"] is True
    assert body["total_pages"] == TRUE_TOTAL_PAGES


@pytest.mark.parametrize("path", HUB_PATHS)
def test_the_anon_wall_reports_the_real_size_when_UNFILTERED(stub_hubs, path) -> None:
    """The whole-section total is public information (nav copy, hub blurbs, the
    sitemap) and is what lets the paginator show a clickable last page. One fixed
    number per section steers with nothing, so anon gets it."""
    body = _body(_client().get(path, params={"page": 2}))

    assert body["cap_reached"] is True
    assert body["items"] == []
    assert body["total_pages"] == TRUE_TOTAL_PAGES


@pytest.mark.parametrize("path", HUB_PATHS)
def test_a_FILTERED_anon_wall_does_not_even_run_the_count(stub_hubs, path) -> None:
    """Not computed, not returned: the filtered count query IS the oracle, so the
    fix is to skip it — which also keeps a DB round-trip off the filtered path."""
    _client().get(path, params={"page": 2, **ANON_FILTER[path]})
    assert stub_hubs == {}


def test_the_unfiltered_count_is_memoised_per_section(stub_hubs) -> None:
    """Serving the real unfiltered total must not put a COUNT on every anon wall
    render — the round-trip §2.1 removed. It is computed once per section and
    held for the TTL."""
    client = _client()
    for _ in range(3):
        client.get(REG_HUB, params={"page": 2})

    assert stub_hubs == {"regulations_hub_total_pages": 1}


def test_the_count_oracle_is_gone_across_filters(stub_hubs) -> None:
    """The attack this closes: walk filter values, read ``total_pages`` off the
    wall, and map the corpus without ever being handed an item. Every filter
    answers with the same flat number.

    Walked over ``doc_type`` rather than ``q``, because ``q`` is no longer a
    filter for this caller at all (D9) — and a closed vocabulary is the stronger
    test anyway: it is the axis anon CAN still probe."""
    client = _client()
    answers = {
        client.get(REG_HUB, params={"page": 2, "doc_type": bucket}).json()["total_pages"]
        for bucket in ("law_statute", "regulation_generic", "decision", "circular")
        if bucket in pl._DOC_TYPE_VOCAB
    }
    assert answers == {pl._ANON_WALL_TOTAL_PAGES}


def test_walking_q_values_as_anon_reads_one_fixed_number(stub_hubs) -> None:
    """And the ``q`` axis specifically: since the param is dropped, every term
    returns the SAME unfiltered total. Not "capped to a flat ceiling" — actually
    unmoving, because the input is not used."""
    client = _client()
    answers = {
        client.get(REG_HUB, params={"page": 2, "q": term}).json()["total_pages"]
        for term in ("نظام", "لائحة", "تنظيم", "قرار")
    }
    assert answers == {TRUE_TOTAL_PAGES}


def test_an_authed_wall_keeps_the_real_count(stub_hubs) -> None:
    """A signed-in caller has an identity and is metered per-user (§2.2); the
    upgrade wall's copy is sized from this number, so it stays real."""
    body = _body(
        _client(_hub_fake(plan="free", limit=10), _User()).get(
            REG_HUB, params={"page": 4}
        )
    )
    assert body["cap_reached"] is True
    assert body["total_pages"] == TRUE_TOTAL_PAGES
    assert stub_hubs.get("regulations_hub_total_pages") == 1


# ===========================================================================
# 4. §3.7 — verified crawlers browse past the anon depth cap
# ===========================================================================

GOOGLEBOT_UA = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)


@pytest.mark.parametrize("path", HUB_PATHS)
def test_a_verified_crawler_browses_past_the_anon_cap(stub_hubs, path) -> None:
    """With the sitemap gated (§3.2b) a capped Googlebot has NO discovery path
    at all. This is the safety net."""
    body = _body(
        _client().get(path, params={"page": 9}, headers={"user-agent": GOOGLEBOT_UA})
    )
    assert body["cap_reached"] is False
    assert body["items"]
    assert body["page"] == 9


@pytest.mark.parametrize(
    "ua",
    [
        "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
        "DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)",
        "Mozilla/5.0 (compatible; YandexBot/3.0)",
        "Mozilla/5.0 (compatible; Baiduspider/2.0)",
        "Mozilla/5.0 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)",
        "PerplexityBot/1.0",
        "Mozilla/5.0 (compatible; Google-InspectionTool/1.0)",
    ],
)
def test_the_whole_verified_engine_list_is_exempt(stub_hubs, ua) -> None:
    """Exactly the engines WAF rule 2 lets through to the sitemap, plus §3.12's
    allowed AI *search* agents, plus Search Console's live-test fetcher (PART 4's
    first verification step is a GSC URL inspection)."""
    body = _body(_client().get(REG_HUB, params={"page": 9}, headers={"user-agent": ua}))
    assert body["cap_reached"] is False


@pytest.mark.parametrize(
    "ua",
    [
        "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
        "Mozilla/5.0 (compatible; SemrushBot/7~bl)",
        "Mozilla/5.0 (compatible; DotBot/1.2)",
        "Mozilla/5.0 (compatible; MJ12bot/v1.4.8)",
        "CCBot/2.0",
        "python-requests/2.31.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
    ],
)
def test_seo_tools_and_humans_stay_capped(stub_hubs, ua) -> None:
    """⚠ Cloudflare's Verified Bots list INCLUDES AhrefsBot / SemrushBot / DotBot
    — WAF rule 0 blocks them first for exactly that reason. One of these tokens
    in the allowlist would hand a paginated corpus dump to the companies that
    resell URL inventories."""
    body = _body(_client().get(REG_HUB, params={"page": 9}, headers={"user-agent": ua}))
    assert body["cap_reached"] is True
    assert body["items"] == []


def test_the_exempted_body_is_never_shared_cached(stub_hubs) -> None:
    """⚠ THE LOAD-BEARING ONE. The edge cache keys on the URL, so a crawler's
    page-9 body left cacheable would be replayed to every anonymous human asking
    for page 9 for an hour — silently undoing the depth cap for everyone."""
    res = _client().get(
        REG_HUB, params={"page": 9}, headers={"user-agent": GOOGLEBOT_UA}
    )
    assert res.json()["cap_reached"] is False
    assert res.headers["cache-control"] == "private, no-store"


def test_a_crawler_inside_the_cap_keeps_the_shared_hour_cache(stub_hubs) -> None:
    """Page 1 is what everybody gets anyway, so the exemption changed nothing and
    the SEO surface must stay cacheable."""
    res = _client().get(
        REG_HUB, params={"page": 1}, headers={"user-agent": GOOGLEBOT_UA}
    )
    assert res.headers["cache-control"] == "public, max-age=3600"
    assert "Authorization" in res.headers.get("vary", "")


def test_a_free_account_is_not_helped_by_a_crawler_user_agent(stub_hubs) -> None:
    """The exemption is anon-only. A signed-in caller has a tier and a per-user
    budget; letting a UA string lift their cap would make §2.2 opt-out."""
    body = _body(
        _client(_hub_fake(plan="free", limit=10), _User()).get(
            REG_HUB, params={"page": 4}, headers={"user-agent": GOOGLEBOT_UA}
        )
    )
    assert body["cap_reached"] is True


def test_the_edge_bot_header_is_ignored_while_untrusted(stub_hubs) -> None:
    """Grey cloud: ``X-Verified-Bot`` is forgeable by anyone, so it counts for
    nothing. Same trust boundary that gates ``CF-Connecting-IP``."""
    body = _body(
        _client().get(
            REG_HUB,
            params={"page": 9},
            headers={"user-agent": "curl/8.0", "x-verified-bot": "1"},
        )
    )
    assert body["cap_reached"] is True


def test_a_trusted_edge_header_grants_the_exemption(stub_hubs, monkeypatch) -> None:
    monkeypatch.setenv("TRUST_CF_HEADERS", "true")
    body = _body(
        _client().get(
            REG_HUB,
            params={"page": 9},
            headers={"user-agent": "curl/8.0", "x-verified-bot": "true"},
        )
    )
    assert body["cap_reached"] is False


def test_behind_a_trusted_edge_the_header_is_authoritative(stub_hubs, monkeypatch) -> None:
    """Once Cloudflare is in front, it has already verified the crawler by
    reverse DNS + published ranges. It said no, so a Googlebot UA is a forgery and
    the spoofable fallback must not get a second vote."""
    monkeypatch.setenv("TRUST_CF_HEADERS", "true")
    body = _body(
        _client().get(REG_HUB, params={"page": 9}, headers={"user-agent": GOOGLEBOT_UA})
    )
    assert body["cap_reached"] is True


# ===========================================================================
# 5. §3.2b — the sitemap feed gate
# ===========================================================================

SITEMAP_STATIC = "/api/v1/public/library/sitemap/static"


def test_the_sitemap_is_open_by_default(stub_hubs) -> None:
    """⚠ ORDERING CONSTRAINT. §3.2b depends on §3.2 (Railway private
    networking), which is NOT done — the frontend still reaches the backend over
    the public internet. Enforcing internal-only today would 404 every sitemap
    section at once, so the gate ships OFF."""
    res = _client().get(SITEMAP_STATIC)
    assert res.status_code == 200, res.text
    assert res.json()["urls"]


def test_the_gate_refuses_a_public_caller_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("LIBRARY_SITEMAP_INTERNAL_ONLY", "true")
    res = _client().get(SITEMAP_STATIC)
    _is_arabic_refusal(res, status=404)


def test_the_gate_lets_the_railway_internal_host_through(monkeypatch) -> None:
    """The signal §3.2 actually creates: the frontend calls
    ``luna-backend.railway.internal`` and the Host header survives the hop."""
    monkeypatch.setenv("LIBRARY_SITEMAP_INTERNAL_ONLY", "true")
    res = _client().get(
        SITEMAP_STATIC, headers={"host": "luna-backend.railway.internal:8080"}
    )
    assert res.status_code == 200, res.text


def test_the_gate_lets_local_dev_through(monkeypatch) -> None:
    """Loopback peer, no proxy hop — ``npm run dev`` against ``localhost:8000``
    must keep working with the flag on."""
    monkeypatch.setenv("LIBRARY_SITEMAP_INTERNAL_ONLY", "true")
    res = _client(peer="127.0.0.1").get(SITEMAP_STATIC)
    assert res.status_code == 200, res.text


def test_a_public_edge_hop_beats_a_private_peer_address(monkeypatch) -> None:
    """⚠ THE TRAP THIS GATE IS MOST EXPOSED TO. On Railway the public edge proxy
    dials the container from a PRIVATE address, so a naive "is the peer private?"
    test would call the entire public internet internal and the gate would be a
    silent no-op. The hop marker has to win."""
    monkeypatch.setenv("LIBRARY_SITEMAP_INTERNAL_ONLY", "true")
    client = _client(peer="10.0.0.5")

    assert client.get(
        SITEMAP_STATIC, headers={"x-forwarded-for": "203.0.113.9"}
    ).status_code == 404
    assert client.get(
        SITEMAP_STATIC, headers={"cf-connecting-ip": "203.0.113.9"}
    ).status_code == 404
    # …and without a hop marker the same private peer IS internal.
    assert client.get(SITEMAP_STATIC).status_code == 200


def test_a_forged_internal_host_does_not_beat_a_hop_marker(monkeypatch) -> None:
    """The hop check runs BEFORE the Host check for this reason: a caller out on
    the public internet claiming ``Host: x.railway.internal`` arrives through the
    edge, and the edge's own header gives it away."""
    monkeypatch.setenv("LIBRARY_SITEMAP_INTERNAL_ONLY", "true")
    res = _client().get(
        SITEMAP_STATIC,
        headers={
            "host": "luna-backend.railway.internal",
            "x-forwarded-for": "198.18.0.9",
        },
    )
    assert res.status_code == 404


def test_a_refusal_is_indistinguishable_from_an_unknown_section(monkeypatch) -> None:
    """An enumeration surface should not confirm its own existence — a 403 would
    tell a prober the feed is there and worth attacking."""
    monkeypatch.setenv("LIBRARY_SITEMAP_INTERNAL_ONLY", "true")
    refused = _client().get(SITEMAP_STATIC)
    unknown = _client().get("/api/v1/public/library/sitemap/does-not-exist")

    assert refused.status_code == unknown.status_code == 404
    assert refused.json() == unknown.json()


@pytest.mark.parametrize("flag", ["false", "0", "", "off", "no"])
def test_only_a_truthy_flag_arms_the_gate(monkeypatch, flag) -> None:
    """Fail-OPEN on junk: a typo in the env var must not take the sitemaps down.
    Same truthy vocabulary as every other kill switch in the backend."""
    monkeypatch.setenv("LIBRARY_SITEMAP_INTERNAL_ONLY", flag)
    assert _client().get(SITEMAP_STATIC).status_code == 200


# ===========================================================================
# 6. Route shape — the hardening must not have moved anything
# ===========================================================================


def test_every_hardened_route_is_still_mounted() -> None:
    paths = {getattr(r, "path", "") for r in _app(_hub_fake()).routes}
    for path, _lister, _counter in HUBS:
        assert path in paths
    assert "/api/v1/public/library/sitemap/{section}" in paths
