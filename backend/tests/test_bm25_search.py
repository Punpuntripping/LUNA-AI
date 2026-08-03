"""Shared BM25 navigation search — Wave B (backend).

Plan: ``.claude/plans/bm25_navigation_search.md`` D3 · D8 · D9 · §5.1–§5.4
Migration: ``shared/db/migrations/111_bm25_search_index.sql``

Covers:

    backend.app.services.search_service   (validation, whitelists, the RPC call)
    backend.app.api.search                (/api/v1/search · /api/v1/search/mine)

The load-bearing assertions, in the order they would hurt if they broke:

  * ``test_anonymous_cannot_search`` — D9 is a SERVER-SIDE rule. The UI's CTA
    modal cannot bind anyone calling the API directly, so this is the only thing
    that actually gates search.
  * ``test_a_hit_carries_no_snippet_field`` — D3 option 2. The moment a snippet
    appears on a hit, someone has to decide per hit whether the caller may see
    it, and the gating apparatus this design deleted has to come back.
  * ``test_a_capped_total_is_reported_as_inexact`` — ``total_count`` is a count
    over the candidate set. Printing a ceiling as a total is lying to the reader.
  * ``test_search_charges_the_same_keys_as_browsing`` — §5.4: a document found by
    searching and the same document found by browsing are ONE item against the
    budget, or search becomes a way to buy extra corpus reach.
  * ``test_an_rpc_failure_is_an_arabic_error_not_an_empty_result`` — a search box
    that answers «لا توجد نتائج» when the index is down is indistinguishable from
    one that works.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import search as search_api
from backend.app.deps import get_current_user, get_supabase
from backend.app.errors import ErrorCode, LunaHTTPException, luna_exception_handler
from backend.app.models.search import SearchHit, SearchResponse
from backend.app.services import case_service, search_service as ss

AUTH_ID = "auth-0000-1111"
USER_ID = "11111111-2222-3333-4444-555555555555"


class _User:
    """Stands in for AuthUser — the routes only ever read ``auth_id``."""

    auth_id = AUTH_ID
    email = "lawyer@example.com"
    role = "authenticated"


# ---------------------------------------------------------------------------
# Fake RPC surface
# ---------------------------------------------------------------------------


class _RpcResult:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self.data = data

    def execute(self) -> "_RpcResult":
        return self


class FakeSupabase:
    """Records the ``bm25_search`` params and replays a scripted hit list.

    The RANKING is not simulated. What these tests own is the contract — which
    params the RPC is handed, what comes back on the wire, and what the route
    does with it. Ranking quality is a SQL property and belongs to Wave F.
    """

    def __init__(self, hits: Optional[list[dict[str, Any]]] = None,
                 *, total: Optional[int] = None, fail: bool = False) -> None:
        self.hits = hits or []
        self.total = len(self.hits) if total is None else total
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> _RpcResult:
        assert name == "bm25_search", name
        self.calls.append(dict(params))
        if self.fail:
            raise RuntimeError("simulated: relation bm25_search does not exist")
        wanted = set(params.get("p_corpora") or [])
        rows = [h for h in self.hits if h.get("corpus") in wanted]
        offset = int(params.get("p_offset") or 0)
        limit = int(params.get("p_limit") or 20)
        return _RpcResult(
            [{**r, "total_count": self.total} for r in rows[offset : offset + limit]]
        )

    def table(self, *_a: Any, **_k: Any):  # pragma: no cover - not used here
        raise AssertionError("these tests must not touch a table")


def _hit(corpus: str, n: int, score: float = 1.0) -> dict[str, Any]:
    return {
        "corpus": corpus,
        "content_id": f"{corpus}-{n}",
        "slug": f"{corpus}-slug-{n}",
        "title": f"عنوان {n}",
        "facets": {"sectors": ["المعاملات التجارية"]},
        "score": score,
    }


@pytest.fixture(autouse=True)
def _map_auth_id_to_user_id(monkeypatch):
    monkeypatch.setattr(
        case_service, "get_user_id",
        lambda supabase, auth_id: USER_ID if auth_id == AUTH_ID else None,
    )


def _app(supabase: Any, user: Optional[_User]) -> FastAPI:
    app = FastAPI()
    app.state.redis = None
    app.add_exception_handler(LunaHTTPException, luna_exception_handler)
    app.include_router(search_api.router)
    app.dependency_overrides[get_supabase] = lambda: supabase
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return app


def _client(supabase: Any = None, user: Optional[_User] = None) -> TestClient:
    return TestClient(_app(supabase if supabase is not None else FakeSupabase(), user))


# ===========================================================================
# 1. D9 — registered-only, enforced server-side
# ===========================================================================


@pytest.mark.parametrize("path", ["/api/v1/search", "/api/v1/search/mine"])
def test_anonymous_cannot_search(path) -> None:
    """No bearer → 401. The CTA modal is decoration; this is the gate.

    ⚠ Deliberately DIFFERENT from the public hubs, which DROP an anonymous ``q``
    and serve the wing (a stranger may legitimately land on a shared hub URL).
    Nobody lands on this endpoint by accident."""
    res = _client(user=None).get(path, params={"q": "نظام العمل"})
    assert res.status_code == 401
    assert res.json()["error"]["status"] == 401


def test_a_signed_in_caller_can_search() -> None:
    fake = FakeSupabase([_hit("regulation", 1)])
    res = _client(fake, _User()).get("/api/v1/search", params={"q": "نظام العمل"})
    assert res.status_code == 200, res.text
    assert res.json()["items"][0]["slug"] == "regulation-slug-1"


# ===========================================================================
# 2. §2.1 — the 3-character floor, one definition
# ===========================================================================


@pytest.mark.parametrize("term", ["ن", "نظ", "  ", "", "ab"])
def test_a_short_or_blank_query_is_refused_in_arabic(term) -> None:
    """A search without a usable term is meaningless, so blank refuses here even
    though blank is a NO-OP on a hub (where it means "just list the wing")."""
    res = _client(FakeSupabase(), _User()).get("/api/v1/search", params={"q": term})
    assert res.status_code == 400
    message = res.json()["error"]["message"]
    assert message == ss.MSG_SEARCH_TOO_SHORT
    assert not any("a" <= ch.lower() <= "z" for ch in message), message


def test_the_floor_has_one_definition() -> None:
    """``public_library`` re-exports these rather than keeping its own copy: the
    hubs and /search must refuse at the same length with the same sentence."""
    from backend.app.api import public_library as pl

    assert pl._MIN_SEARCH_CHARS == ss.MIN_QUERY_CHARS == 3
    assert pl.MSG_SEARCH_TOO_SHORT == ss.MSG_SEARCH_TOO_SHORT


def test_an_over_long_query_is_truncated_not_refused() -> None:
    """Every extra word is another lexeme ANDed into the tsquery, so a pasted
    paragraph matches nothing and costs something. Bounding it silently is kinder
    than a 400 on a paste."""
    assert len(ss.normalize_query("ن" * 5000)) == ss.MAX_QUERY_CHARS


# ===========================================================================
# 3. D3 — no snippet, no gate, anywhere in the search path
# ===========================================================================


def test_a_hit_carries_no_snippet_field() -> None:
    """THE STRUCTURAL PROPERTY. Only always-free text is indexed, so a search
    response cannot carry gated bytes — and the way that stays true is that there
    is nowhere on the wire to put them."""
    fields = set(SearchHit.model_fields)
    for forbidden in ("snippet", "match_in_body", "excerpt", "highlight", "lead"):
        assert forbidden not in fields, forbidden


def test_the_search_service_holds_no_gating_logic() -> None:
    """A regression guard with teeth: the moment someone reaches for the unlock
    ledger or a tier check in here, the leak surface D3 deleted is back.

    The module docstring and the comments are excluded on purpose — they NAME
    these things in order to say "not here", and a guard that forbade discussing
    the rule would be the second-worst outcome after the rule being broken."""
    import inspect

    source = inspect.getsource(ss)
    source = source.replace(ss.__doc__ or "", "")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    for forbidden in (
        "_find_unlock_row", "seo_tier", "ts_headline", "resolve_gate",
        "library_unlocks", "truncate_for_gate",
    ):
        assert forbidden not in code, forbidden


# ===========================================================================
# 4. The RPC contract (migration 111 §10)
# ===========================================================================


def test_the_rpc_is_called_with_the_migrations_parameter_names() -> None:
    fake = FakeSupabase([_hit("regulation", 1)])
    _client(fake, _User()).get("/api/v1/search", params={"q": "نظام العمل"})

    assert fake.calls, "the RPC was never called"
    params = fake.calls[0]
    assert set(params) == {
        "p_corpora", "p_query", "p_owner", "p_facets",
        "p_limit", "p_offset", "p_candidates",
    }
    assert params["p_query"] == "نظام العمل"
    assert params["p_owner"] is None          # public corpora only
    assert params["p_candidates"] == ss.DEFAULT_CANDIDATES


def test_a_public_search_never_passes_an_owner() -> None:
    """``p_owner`` is the ownership SWITCH, not a filter: a non-null value would
    return that owner's PRIVATE rows and nothing else."""
    fake = FakeSupabase()
    _client(fake, _User()).get("/api/v1/search", params={"q": "نظام"})
    assert fake.calls[0]["p_owner"] is None


def test_only_public_corpora_are_reachable_from_the_public_endpoint() -> None:
    """Asking for ``blog`` on /search must not widen the call — the corpus list
    is intersected with what the endpoint allows, never unioned."""
    fake = FakeSupabase()
    _client(fake, _User()).get(
        "/api/v1/search", params=[("q", "نظام"), ("corpus", "blog"), ("corpus", "template")]
    )
    # Nothing survived the intersection → no RPC at all, empty page.
    assert fake.calls == []


def test_a_known_corpus_filter_narrows_the_call() -> None:
    fake = FakeSupabase()
    _client(fake, _User()).get(
        "/api/v1/search", params=[("q", "نظام"), ("corpus", "judgment")]
    )
    assert fake.calls[0]["p_corpora"] == ["judgment"]


def test_an_rpc_failure_is_an_arabic_error_not_an_empty_result() -> None:
    """Includes the deploy-order case: a backend shipped ahead of migration 111.
    Failing loudly is what makes that visible in minutes instead of reading as
    "search finds nothing"."""
    res = _client(FakeSupabase(fail=True), _User()).get(
        "/api/v1/search", params={"q": "نظام العمل"}
    )
    assert res.status_code == 500
    assert res.json()["error"]["message"] == ss.MSG_SEARCH_FAILED


# ===========================================================================
# 5. Honest totals
# ===========================================================================


def test_an_uncapped_total_is_reported_as_exact() -> None:
    fake = FakeSupabase([_hit("regulation", n) for n in range(3)], total=3)
    body = _client(fake, _User()).get("/api/v1/search", params={"q": "نظام"}).json()
    assert body["total"] == 3
    assert body["total_is_exact"] is True


def test_a_capped_total_is_reported_as_inexact() -> None:
    """``total_count`` counts the CANDIDATE set (``p_candidates``), so at the
    ceiling it is a floor. A UI printing «٥٠٠ نتيجة» would be inventing it."""
    fake = FakeSupabase([_hit("regulation", 1)], total=ss.DEFAULT_CANDIDATES)
    body = _client(fake, _User()).get("/api/v1/search", params={"q": "نظام"}).json()
    assert body["total"] == ss.DEFAULT_CANDIDATES
    assert body["total_is_exact"] is False


def test_deep_paging_is_bounded() -> None:
    """Paging past the result ceiling yields nothing and costs nothing. Deep
    paging through search results is a traversal technique, not a reading
    pattern (§5.4)."""
    fake = FakeSupabase([_hit("regulation", 1)])
    body = _client(fake, _User()).get(
        "/api/v1/search",
        params={"q": "نظام", "page": 999, "page_size": ss.MAX_PAGE_SIZE},
    ).json()
    assert body["items"] == []
    assert fake.calls == []


def test_page_size_is_clamped_not_refused() -> None:
    fake = FakeSupabase()
    _client(fake, _User()).get(
        "/api/v1/search", params={"q": "نظام", "page_size": 5000}
    )
    assert fake.calls[0]["p_limit"] <= ss.MAX_PAGE_SIZE


# ===========================================================================
# 6. §5.4 — metering, with no search exemption
# ===========================================================================


def test_search_charges_the_same_keys_as_browsing(monkeypatch) -> None:
    """ONE item budget across browse and search. The keys are ``section:slug``, so
    the same document reached two ways is charged once, under one name."""
    charged: list[list[str]] = []

    async def _spy(_request, _user_id, members, **_kw):
        charged.append(list(members))
        return len(members)

    monkeypatch.setattr(search_api.library_budget, "charge_items", _spy)

    fake = FakeSupabase([_hit("circular", 1), _hit("regulation", 2)])
    res = _client(fake, _User()).get("/api/v1/search", params={"q": "رخصة تجارية"})
    assert res.status_code == 200, res.text

    flat = [key for batch in charged for key in batch]
    assert "circulars:circular-slug-1" in flat
    assert "regulations:regulation-slug-2" in flat


def test_the_budget_is_enforced_before_the_query(monkeypatch) -> None:
    """A refusal must not cost a DB round-trip — the same ordering rule the hubs
    follow (§2.2)."""

    async def _refuse(_request, _user_id, _tier=None):
        raise LunaHTTPException(
            status_code=429, code=ErrorCode.RATE_LIMITED, detail="تم تجاوز الحد"
        )

    monkeypatch.setattr(search_api.library_budget, "enforce_item_budget", _refuse)

    fake = FakeSupabase([_hit("regulation", 1)])
    res = _client(fake, _User()).get("/api/v1/search", params={"q": "نظام"})
    assert res.status_code == 429
    assert fake.calls == []


def test_search_responses_are_never_shared_cached() -> None:
    """Every byte is per-caller (metered against their budget), so none of it may
    reach an ISR or edge cache."""
    res = _client(FakeSupabase(), _User()).get("/api/v1/search", params={"q": "نظام"})
    assert res.headers["cache-control"] == "private, no-store"


# ===========================================================================
# 7. Pure helpers
# ===========================================================================


def test_every_public_corpus_has_a_section_and_a_url() -> None:
    """A corpus that ranks must be chargeable and linkable. A missing section
    entry silently forks the item budget; a missing URL prefix renders a hit that
    cannot be opened.

    ⚠ ``service`` IS NOT A PUBLIC CORPUS ANY MORE (2026-08-03) — the /compliance
    wing it linked into was retired, so every service hit would have been a 404.
    It is pinned out of all three tables below, together."""
    assert "service" not in ss.PUBLIC_CORPORA
    assert "service" not in ss.CORPUS_SECTION
    assert ss.public_url("service", "x") is None
    assert set(ss.CORPUS_SECTION) == set(ss.PUBLIC_CORPORA)


@pytest.mark.parametrize(
    "corpus,slug,expected",
    [
        ("regulation", "x", "/regulations/x"),
        ("judgment", "x", "/judgments/x"),
        ("circular", "x", "/circulars/x"),
        ("service", "x", None),
        ("blog", "tok", "/blog/tok"),
        ("regulation", None, None),
        ("nonsense", "x", None),
    ],
)
def test_public_url(corpus, slug, expected) -> None:
    assert ss.public_url(corpus, slug) == expected


def test_facets_outside_a_corpus_vocabulary_are_dropped() -> None:
    """``p_facets`` reaches a jsonb ``@>`` containment test, so an unknown key
    cannot match a row — it can only make a working search look broken."""
    cleaned = ss.clean_facets(["regulation"], {"entity_name": "وزارة", "court": "X"})
    assert cleaned == {"entity_name": "وزارة"}


def test_a_facet_held_by_one_corpus_in_scope_survives() -> None:
    """On a cross-wing search a key only some wings index is legitimate: the RPC
    applies containment uniformly, so it correctly excludes the wings without
    it."""
    cleaned = ss.clean_facets(["regulation", "judgment"], {"court": "التجارية"})
    assert cleaned == {"court": "التجارية"}


def test_an_unknown_corpus_is_dropped_not_refused() -> None:
    """A corpus name is a UI affordance, not a secret: a stale frontend build
    should degrade to searching the rest, not 400 the box."""
    assert ss.clean_corpora(["regulation", "nope"], ss.PUBLIC_CORPORA) == ["regulation"]
    assert ss.clean_corpora(None, ss.PUBLIC_CORPORA) == list(ss.PUBLIC_CORPORA)
    assert ss.clean_corpora([], ss.PUBLIC_CORPORA) == list(ss.PUBLIC_CORPORA)


def test_rank_map_preserves_bm25_order() -> None:
    """Rows come back from PostgREST in its own order, in up to two chunks, so
    the ranking has to be re-imposed or it is lost."""
    assert ss.rank_map(["b", "a", "c"]) == {"b": 0, "a": 1, "c": 2}


def test_the_response_model_does_not_promise_a_page_count() -> None:
    """A ``total_pages`` derived from a CEILING total would paginate to pages
    that do not exist."""
    assert "total_pages" not in SearchResponse.model_fields
