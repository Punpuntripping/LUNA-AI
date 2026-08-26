"""Access-tiers Phase C — reference unification (plan PART 6, DECISIONS D15/D15.1).

Covers what this phase actually changed:

    references_service.fetch_item_references(with_source_views=False by default)
    references_service.fetch_item_references_payload  (+ the ``has_source`` bit)
    references_service.fetch_reference_row / build_reference_source_view
    reference_resolver.resolve_ref / resolve_ref_id      (the D15 mapping)
    GET /api/v1/workspace/{item_id}/references/{n}/source (the metered reveal)

The headline assertion is ``test_references_list_carries_no_source_bodies``: the
list response must no longer contain a single byte of source body, because
everything else in this phase (the meter, the ledger, the ref panel's reveal
flow) is pointless if the panel already has the text.

No live DB / Redis. Supabase is the in-memory PostgREST stand-in from
``test_library_gating`` — reused deliberately rather than re-faked, because it
implements real ``ON CONFLICT DO NOTHING`` semantics, which is what makes
"charged exactly once" a real assertion instead of a scripted one.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient

from agents.deep_search_v4.source_viewer import (
    ArticleFullSourceView,
    ChunkSourceView,
    RegulationSummarySourceView,
)
from agents.deep_search_v4.ura.enrich import enrich_ura
from agents.deep_search_v4.ura.schema import (
    CaseURAResult,
    CircularURAResult,
    RegURAResult,
    UnifiedRetrievalArtifact,
)
from backend.app.api import workspace as workspace_api
from backend.app.deps import get_current_user, get_supabase
from backend.app.middleware.route_limits import (
    LIBRARY_ROUTE_RATE_LIMIT,
    library_rate_limit,
)
from backend.app.services import library_service as ls
from backend.app.services import reference_resolver, references_service

# The row-backed PostgREST fake + quota fixtures Phase A already ships.
from backend.tests.test_library_gating import FakeSupabase, _Chain, quota_row


# ---------------------------------------------------------------------------
# Fixtures / ids
# ---------------------------------------------------------------------------

AUTH_A = "auth-aaaa"
AUTH_B = "auth-bbbb"
USER_A = "aaaaaaaa-0000-0000-0000-00000000000a"
USER_B = "bbbbbbbb-0000-0000-0000-00000000000b"

WI_A = "11111111-1111-4111-8111-111111111111"
WI_B = "22222222-2222-4222-8222-222222222222"

CHUNK_MULTI = "aaaa1111-1111-4111-8111-111111111111"   # owns 3 مواد
CHUNK_SINGLE = "aaaa2222-2222-4222-8222-222222222222"  # owns 1 مادة
REG_ID = "cccc3333-3333-4333-8333-333333333333"
CASE_ID = "dddd4444-4444-4444-8444-444444444444"
CIRC_ID = "eeee5555-5555-4555-8555-555555555555"
SVC_ID = "ffff6666-6666-4666-8666-666666666666"
# simple_search (migration 136): an articles_v2 row keyed directly, plus one
# whose article_number is compound and therefore has no published مادة page.
ART_ID = "aaaa7777-7777-4777-8777-777777777777"
ART_COMPOUND_ID = "aaaa8888-8888-4888-8888-888888888888"
ART_MUKARRAR_ID = "aaaa9999-9999-4999-8999-999999999999"


def run(coro):
    """Run one coroutine to completion (no pytest-asyncio dependency)."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolate_module_state():
    """Reset every piece of process-global state these tests touch.

    * ``library_service`` gate caches are module-level TTL caches — a seeded
      policy leaking into the next test would silently flip a gate.
    * ``library_rate_limit`` is a shared module-level singleton with a
      process-local fallback window (Redis is absent here, so the fail-CLOSED
      path is what runs). Without a reset, the 20/min budget is consumed across
      the whole file and unrelated tests start 429-ing.
    """
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()
    library_rate_limit._fallback.reset()
    yield
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()
    library_rate_limit._fallback.reset()


def ref_row(
    *,
    n: int = 1,
    wi_id: str = WI_A,
    ref_id: str = f"reg:{CHUNK_MULTI}",
    domain: str = "regulations",
    item_id: Optional[str] = CHUNK_MULTI,
    used: bool = True,
) -> dict[str, Any]:
    return {
        "ref_pk": f"p{n}",
        "wi_id": wi_id,
        "item_id": item_id,
        "ref_id": ref_id,
        "domain": domain,
        "n": n,
        "relevance": "high",
        "used": used,
        "sub_queries": [],
    }


def base_supabase(**tables: Any) -> FakeSupabase:
    """FakeSupabase seeded with the corpus rows the resolver reads."""
    quota = tables.pop("quota_row", None) or quota_row()
    seeded: dict[str, Any] = {
        "chunks_v2": [
            {
                "id": CHUNK_MULTI,
                "regulation_id": REG_ID,
                "owns": {"MADDA": [7, 8, 9]},
                "content": "نص المقطع.",
            },
            {
                "id": CHUNK_SINGLE,
                "regulation_id": REG_ID,
                "owns": {"MADDA": [6]},
                "content": "نص المقطع المفرد.",
            },
        ],
        "cases": [
            {
                "id": CASE_ID,
                "case_ref": "case-ref-1",
                "court": "المحكمة التجارية",
                "case_number": "123/45",
                "date_hijri": "1445/06/01",
            }
        ],
        "circulars": [
            {"id": CIRC_ID, "title": "تعميم مهم", "content": "ن" * 5000},
        ],
        "articles_v2": [
            {"id": ART_ID, "regulation_id": REG_ID, "article_number": "6"},
            # «7-4» exists in the corpus and has NO published مادة page.
            {"id": ART_COMPOUND_ID, "regulation_id": REG_ID, "article_number": "7-4"},
            # …and so does «81 مكرر». 487 of 51,792 مواد (0.94%, across 7 أنظمة)
            # carry a number no int can hold (§12a C4 / §13).
            {"id": ART_MUKARRAR_ID, "regulation_id": REG_ID,
             "article_number": "81 مكرر"},
        ],
        "regulations_v2": [
            {"id": REG_ID, "title": "نظام العمل الصادر بالمرسوم", "clean_title": "نظام العمل"},
        ],
        "seo_item_meta": [],
        "seo_articles": [],
    }
    seeded.update(tables)
    return FakeSupabase(quota_row=quota, **seeded)


# ===========================================================================
# 1. The ref_id resolver (§6.3 / D15 / D15.1)
# ===========================================================================


def test_chunk_owning_three_madda_resolves_to_the_regulation() -> None:
    """D15.1: only 2,140 of 11,455 chunks own exactly one مادة, so ~81% of
    ``reg:`` citations land on the نظام. Intended, not a bug — and the ledger
    row it produces grants the WHOLE statute (D5)."""
    supabase = base_supabase()
    resolved = run(reference_resolver.resolve_ref(supabase, f"reg:{CHUNK_MULTI}"))

    assert resolved is not None
    assert resolved.as_tuple() == ("regulation", REG_ID)
    assert resolved.article_no is None
    assert resolved.parent_regulation_id == REG_ID
    assert resolved.always_free is False


def test_chunk_owning_one_madda_resolves_to_that_article() -> None:
    supabase = base_supabase()
    resolved = run(reference_resolver.resolve_ref(supabase, f"reg:{CHUNK_SINGLE}"))

    assert resolved is not None
    # The sidecar key shape published مواد use: '{regulation_id}#{article_no}'.
    assert resolved.as_tuple() == ("article", f"{REG_ID}#6")
    assert resolved.article_no == 6
    # Passed to resolve_access so D5 (نظام covers مادة) can fire without
    # re-deriving the parent from the key.
    assert resolved.parent_regulation_id == REG_ID


def test_chunk_owning_no_madda_resolves_to_the_regulation() -> None:
    supabase = base_supabase(
        chunks_v2=[{"id": CHUNK_MULTI, "regulation_id": REG_ID, "owns": {}}]
    )
    resolved = run(reference_resolver.resolve_ref(supabase, f"reg:{CHUNK_MULTI}"))
    assert resolved is not None and resolved.as_tuple() == ("regulation", REG_ID)


def test_non_numeric_madda_falls_back_to_the_regulation() -> None:
    """Compound refs («36-3») have no published مادة page, so no sidecar key can
    be minted for them — the chunk must lift to the نظام, never be dropped."""
    supabase = base_supabase(
        chunks_v2=[{"id": CHUNK_MULTI, "regulation_id": REG_ID, "owns": {"MADDA": ["36-3"]}}]
    )
    resolved = run(reference_resolver.resolve_ref(supabase, f"reg:{CHUNK_MULTI}"))
    assert resolved is not None and resolved.as_tuple() == ("regulation", REG_ID)


def test_case_ref_resolves_to_the_judgment_uuid() -> None:
    """``case:<case_ref>`` → ``cases.id`` — the sidecar content_id
    ``build_judgment_slugs.py`` writes."""
    supabase = base_supabase()
    resolved = run(
        reference_resolver.resolve_ref(supabase, "case:case-ref-1", domain="cases")
    )
    assert resolved is not None
    assert resolved.as_tuple() == ("judgment", CASE_ID)
    assert "المحكمة التجارية" in resolved.title


def test_case_uses_item_id_without_a_lookup() -> None:
    """``workspace_item_references.item_id`` is already cases.id, so a row that
    has it costs zero round-trips."""
    supabase = base_supabase(cases=[])  # any lookup would fail
    resolved = run(
        reference_resolver.resolve_ref(
            supabase, "case:whatever", domain="cases", item_id=CASE_ID
        )
    )
    assert resolved is not None and resolved.as_tuple() == ("judgment", CASE_ID)


def test_long_circular_resolves_and_is_chargeable() -> None:
    supabase = base_supabase()
    resolved = run(reference_resolver.resolve_ref(supabase, f"circular:{CIRC_ID}"))
    assert resolved is not None
    assert resolved.as_tuple() == ("circular", CIRC_ID)
    assert resolved.always_free is False
    assert resolved.title == "تعميم مهم"


def test_short_circular_is_free_by_policy() -> None:
    """A <=800-char تعميم renders FULLY OPEN on the anonymous /circulars page
    (``effective_circular_gate``). Charging an unlock for the same bytes in chat
    would make signing in strictly worse than not — the §5.1 trick feeling."""
    supabase = base_supabase(
        circulars=[{"id": CIRC_ID, "title": "تعميم قصير", "content": "ن" * 200}]
    )
    resolved = run(reference_resolver.resolve_ref(supabase, f"circular:{CIRC_ID}"))
    assert resolved is not None
    assert resolved.always_free is True
    assert resolved.free_reason == "short_circular"


def test_compliance_resolves_to_a_free_service() -> None:
    supabase = base_supabase()
    resolved = run(
        reference_resolver.resolve_ref(
            supabase, "compliance:deadbeefcafebabe", domain="compliance", item_id=SVC_ID
        )
    )
    assert resolved is not None
    assert resolved.as_tuple() == ("service", SVC_ID)
    assert resolved.always_free is True


def test_bare_sha1_without_a_prefix_is_still_a_service() -> None:
    supabase = base_supabase()
    resolved = run(
        reference_resolver.resolve_ref(supabase, "deadbeefcafebabe", item_id=SVC_ID)
    )
    assert resolved is not None and resolved.content_type == "service"


@pytest.mark.parametrize(
    "ref_id, domain",
    [
        ("", None),
        ("reg:not-a-uuid", "regulations"),
        ("case:unknown-ref", "cases"),
        (f"circular:{CIRC_ID}", "circulars"),   # circulars table emptied below
        ("wat:12345", None),
        ("just-some-text", None),
    ],
)
def test_unresolvable_ref_ids_fail_closed(ref_id: str, domain: Optional[str]) -> None:
    """Every unknown shape returns None. The endpoint turns that into a 402 —
    a resolver that failed OPEN would hand out corpus bytes for free."""
    supabase = base_supabase(circulars=[], cases=[])
    assert run(reference_resolver.resolve_ref(supabase, ref_id, domain=domain)) is None
    assert run(reference_resolver.resolve_ref_id(supabase, ref_id, domain=domain)) is None


def test_deleted_chunk_is_unresolvable() -> None:
    """A re-chunked / deleted chunk has nothing to unlock — refuse, never charge
    for a phantom."""
    supabase = base_supabase(chunks_v2=[])
    assert run(reference_resolver.resolve_ref(supabase, f"reg:{CHUNK_MULTI}")) is None


def test_resolve_ref_id_projects_to_the_pinned_tuple() -> None:
    supabase = base_supabase()
    assert run(
        reference_resolver.resolve_ref_id(supabase, f"reg:{CHUNK_SINGLE}")
    ) == ("article", f"{REG_ID}#6")


# ---------------------------------------------------------------------------
# 1b. The two simple_search prefixes (migration 136, plan §6.1a / §7.3)
# ---------------------------------------------------------------------------


def test_article_ref_resolves_to_the_same_tuple_a_chunk_would() -> None:
    """``article:<articles_v2.id>`` short-circuits to the EXISTING ``article``
    ledger type — no new metering vocabulary.

    The proof that matters is the equality below: a مادة reached by lookup and
    the same مادة reached through its chunk must produce the identical
    ``(content_type, content_id)``, or the user would be charged twice for one
    document and D5's نظام-covers-مادة grant would stop applying.
    """
    supabase = base_supabase()

    via_lookup = run(reference_resolver.resolve_ref(supabase, f"article:{ART_ID}"))
    via_chunk = run(reference_resolver.resolve_ref(supabase, f"reg:{CHUNK_SINGLE}"))

    assert via_lookup is not None and via_chunk is not None
    assert via_lookup.as_tuple() == via_chunk.as_tuple() == ("article", f"{REG_ID}#6")
    assert via_lookup.article_no == 6
    assert via_lookup.parent_regulation_id == REG_ID


def test_compound_article_number_lifts_to_the_regulation() -> None:
    """«7-4» has no published مادة page, so no sidecar key can be minted — the
    ref lifts to the نظام rather than being dropped. Same policy
    ``_owned_article_numbers`` applies to a chunk's ``owns`` map."""
    supabase = base_supabase()
    resolved = run(reference_resolver.resolve_ref(supabase, f"article:{ART_COMPOUND_ID}"))

    assert resolved is not None
    assert resolved.as_tuple() == ("regulation", REG_ID)
    assert resolved.article_no is None
    assert resolved.parent_regulation_id == REG_ID


def test_regdoc_ref_resolves_to_the_regulation_and_names_it() -> None:
    """``regdoc:<regulations_v2.id>`` → ``('regulation', id)``. The lookup is not
    redundant: it proves the نظام exists (fail closed) and supplies the title the
    unlock toast shows instead of a uuid."""
    supabase = base_supabase()
    resolved = run(reference_resolver.resolve_ref(supabase, f"regdoc:{REG_ID}"))

    assert resolved is not None
    assert resolved.as_tuple() == ("regulation", REG_ID)
    assert resolved.title == "نظام العمل"          # clean_title, not title
    assert resolved.parent_regulation_id == REG_ID


def test_new_prefixes_are_metered_exactly_like_reg_chunks() -> None:
    """NOT ``always_free``.

    D12's "regulations are not metered" governs what the AGENT reads while
    composing an answer. This module governs the USER-facing «عرض المصدر» reveal,
    where a نظام has always cost what a ``reg:`` chunk of it costs — and the two
    paths are two doors onto one document. Only a compliance service and a short
    circular are policy-open (§1.3).
    """
    supabase = base_supabase()
    for ref_id in (f"article:{ART_ID}", f"regdoc:{REG_ID}", f"reg:{CHUNK_SINGLE}"):
        resolved = run(reference_resolver.resolve_ref(supabase, ref_id))
        assert resolved is not None, ref_id
        assert resolved.always_free is False, ref_id
        assert resolved.free_reason == "", ref_id


def test_prefixless_rows_fall_back_to_the_new_domains() -> None:
    """A row whose ref_id lost its prefix is disambiguated by ``domain``, exactly
    like the four older domains."""
    supabase = base_supabase()
    assert run(
        reference_resolver.resolve_ref(supabase, ART_ID, domain="articles")
    ).as_tuple() == ("article", f"{REG_ID}#6")
    assert run(
        reference_resolver.resolve_ref(supabase, REG_ID, domain="regulation_docs")
    ).as_tuple() == ("regulation", REG_ID)


@pytest.mark.parametrize(
    "ref_id, domain",
    [
        ("article:not-a-uuid", "articles"),
        ("regdoc:not-a-uuid", "regulation_docs"),
        # Right shape, vanished row — a re-ingested corpus must not mint free
        # access to a phantom.
        (f"article:{'9' * 8}-9999-4999-8999-{'9' * 12}", "articles"),
        (f"regdoc:{'9' * 8}-9999-4999-8999-{'9' * 12}", "regulation_docs"),
    ],
)
def test_new_prefixes_fail_closed(ref_id: str, domain: str) -> None:
    supabase = base_supabase()
    assert run(reference_resolver.resolve_ref(supabase, ref_id, domain=domain)) is None
    assert run(reference_resolver.resolve_ref_id(supabase, ref_id, domain=domain)) is None


def test_regdoc_prefix_is_not_swallowed_by_the_reg_branch() -> None:
    """``regdoc`` must not be matched as ``reg``. If it were, the whole-نظام uuid
    would be read against ``chunks_v2``, find nothing, and refuse a valid
    citation — the mirror image of §9 trap 4."""
    supabase = base_supabase(chunks_v2=[])   # any chunk lookup would fail
    resolved = run(reference_resolver.resolve_ref(supabase, f"regdoc:{REG_ID}"))
    assert resolved is not None and resolved.content_type == "regulation"


def test_a_reg_prefixed_regulation_uuid_still_fails_closed() -> None:
    """The trap itself, asserted from the resolver's side: a regulations_v2 uuid
    wearing ``reg:`` is looked up as a CHUNK, is not found, and is refused. That
    is why the write path mints ``regdoc:`` instead."""
    supabase = base_supabase()
    assert run(reference_resolver.resolve_ref(supabase, f"reg:{REG_ID}")) is None


# ===========================================================================
# 2. THE HEADLINE — the references LIST carries no source bodies (§6.2 step 1)
# ===========================================================================


_BIG_CIRCULAR = "ت" * 168_000   # the live outlier PART 9 trap 8 names
_BIG_CASE = "ح" * 40_000
_BIG_CHUNK = "م" * 20_000


def _patch_big_shells(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three references whose sources are enormous, across three domains."""
    monkeypatch.setattr(
        references_service,
        "_select_reference_rows",
        lambda *a, **kw: [
            ref_row(n=1, ref_id=f"reg:{CHUNK_MULTI}", domain="regulations"),
            ref_row(n=2, ref_id="case:case-ref-1", domain="cases", item_id=CASE_ID),
            ref_row(n=3, ref_id=f"circular:{CIRC_ID}", domain="circulars", item_id=CIRC_ID),
        ],
    )

    async def _reg_shells(*_a, **_kw):
        return {
            1: RegURAResult(
                ref_id=f"reg:{CHUNK_MULTI}", source_type="reg_chunk", relevance="high",
                reg_title="نظام العمل", chunk_content=_BIG_CHUNK,
                landing_url="https://laws.boe.gov.sa/x",
            )
        }

    async def _case_shells(*_a, **_kw):
        return {
            2: CaseURAResult(
                ref_id="case:case-ref-1", source_type="case", relevance="high",
                case_number="123/45", entity_name="المحكمة التجارية",
                case_content=_BIG_CASE,
            )
        }

    async def _circ_shells(*_a, **_kw):
        return {
            3: CircularURAResult(
                ref_id=f"circular:{CIRC_ID}", source_type="circular", relevance="medium",
                title="تعميم مهم", content=_BIG_CIRCULAR,
            )
        }

    monkeypatch.setattr(references_service, "_build_reg_shells", _reg_shells)
    monkeypatch.setattr(references_service, "_build_case_shells", _case_shells)
    monkeypatch.setattr(references_service, "_build_circular_shells", _circ_shells)


def test_references_list_carries_no_source_bodies(monkeypatch, capsys) -> None:
    """THE headline test for Phase C.

    Before: ``fetch_item_references`` called ``_attach_source_views`` on every
    read, so one panel load shipped every full case body, every full chunk and
    every UNCAPPED circular (168 KB outliers) before the user clicked anything —
    which made metering structurally impossible.

    After: the list is the citation mesh only. This asserts BOTH directions —
    the bodies are absent from the default payload, and still reachable when a
    caller explicitly opts in — plus the size collapse.
    """
    _patch_big_shells(monkeypatch)

    payload = run(references_service.fetch_item_references_payload(object(), WI_A))
    metered = json.dumps(payload, ensure_ascii=False)

    # 1. Not one byte of any source body.
    for body in (_BIG_CIRCULAR, _BIG_CASE, _BIG_CHUNK):
        assert body not in metered
    # Even a 1 KB slice of them must not appear (guards against a partial leak
    # through some other field).
    for body in (_BIG_CIRCULAR, _BIG_CASE, _BIG_CHUNK):
        assert body[:1000] not in metered

    # 2. The content-carrying field is present-but-null (so an un-migrated
    #    client renders "no reveal button" instead of crashing), and the new
    #    has_source bit tells the panel a reveal exists.
    assert [r["n"] for r in payload] == [1, 2, 3]
    assert all(r["source_view"] is None for r in payload)
    assert all(r["has_source"] is True for r in payload)

    # 3. The mesh itself survives — that stays free by policy (§1.3).
    assert payload[0]["title"] == "نظام العمل"
    assert payload[0]["landing_url"] == "https://laws.boe.gov.sa/x"
    assert payload[0]["ref_id"] == f"reg:{CHUNK_MULTI}"
    assert payload[0]["domain"] == "regulations"
    assert all(r["snippet"] for r in payload)
    # Snippets are hover-sized, not bodies.
    assert all(len(r["snippet"]) <= 520 for r in payload)

    # 4. The size collapse, measured. The opt-in shape is what today's code
    #    would have returned for the same three references.
    legacy_refs = run(
        references_service.fetch_item_references(object(), WI_A, with_source_views=True)
    )
    legacy = json.dumps(
        [r.model_dump(mode="json") for r in legacy_refs], ensure_ascii=False
    )
    assert len(metered) * 50 < len(legacy)           # >98% smaller
    assert len(metered) < 5_000                      # a mesh, not a corpus dump
    # ASCII only: pytest -s writes to a cp1252 console on this project's Windows
    # dev box, and a decorative arrow would crash the test that measures the win.
    print(
        f"\n[payload] 3 refs: legacy {len(legacy):,} chars -> metered "
        f"{len(metered):,} chars ({100 * (1 - len(metered) / len(legacy)):.2f}% drop)"
    )


def test_opt_in_still_attaches_full_bodies(monkeypatch) -> None:
    """``with_source_views=True`` is the escape hatch; the machinery is intact."""
    _patch_big_shells(monkeypatch)
    refs = run(
        references_service.fetch_item_references(object(), WI_A, with_source_views=True)
    )
    assert refs[0].source_view is not None
    assert refs[0].source_view.content.startswith("م")


def test_default_call_is_the_safe_shape(monkeypatch) -> None:
    """The blog snapshot (``blog.py``/``deepsearch_api``) calls
    ``fetch_item_references`` with no keyword and lands in
    ``blog_posts.references_json``, which the ANONYMOUS
    ``GET /public/blog/{token}`` serves. If the default ever flips back to
    attaching bodies, that snapshot becomes a permanent unmetered mirror of the
    corpus — so the default itself is the assertion."""
    _patch_big_shells(monkeypatch)
    refs = run(references_service.fetch_item_references(object(), WI_A, used_only=True))
    assert all(r.source_view is None for r in refs)


def test_has_source_is_false_for_a_stub_reference(monkeypatch) -> None:
    """A reference whose source row is gone renders as a stub card — the panel
    must not offer a reveal that can only fail (and would burn a round-trip)."""
    monkeypatch.setattr(
        references_service,
        "_select_reference_rows",
        lambda *a, **kw: [ref_row(n=1)],
    )

    async def _no_shells(*_a, **_kw):
        return {}

    monkeypatch.setattr(references_service, "_build_reg_shells", _no_shells)

    payload = run(references_service.fetch_item_references_payload(object(), WI_A))
    assert payload[0]["has_source"] is False
    assert payload[0]["source_view"] is None
    assert payload[0]["title"] == references_service._STUB_TITLE


def test_build_reference_source_view_reuses_the_shell_builders(monkeypatch) -> None:
    """The per-item path must go through the SAME projection the list used, so
    the revealed body cannot drift from what the panel used to show."""
    async def _circ_shells(_supabase, rows):
        assert len(rows) == 1  # single-row call, not a full-panel fetch
        return {
            3: CircularURAResult(
                ref_id=f"circular:{CIRC_ID}", source_type="circular",
                relevance="medium", title="تعميم مهم", content=_BIG_CIRCULAR,
            )
        }

    monkeypatch.setattr(references_service, "_build_circular_shells", _circ_shells)

    async def _fake_build(_supabase, shell):
        # source_viewer is exercised in its own suite; assert the handoff.
        assert shell.ref_id == f"circular:{CIRC_ID}"
        return ChunkSourceView(title="ok", content=shell.content)

    monkeypatch.setattr(references_service, "build_source_view", _fake_build)

    row = ref_row(n=3, ref_id=f"circular:{CIRC_ID}", domain="circulars", item_id=CIRC_ID)
    view = run(references_service.build_reference_source_view(object(), row))
    assert view is not None and view.content == _BIG_CIRCULAR


def test_build_reference_source_view_returns_none_when_source_is_gone(monkeypatch) -> None:
    async def _no_shells(*_a, **_kw):
        return {}

    monkeypatch.setattr(references_service, "_build_reg_shells", _no_shells)
    assert run(references_service.build_reference_source_view(object(), ref_row())) is None


# ===========================================================================
# 3. The metered reveal endpoint
# ===========================================================================


class _FakeAuth:
    def __init__(self, auth_id: str) -> None:
        self.auth_id = auth_id


def _install_fake_library_items(monkeypatch) -> list[tuple]:
    """Record every ``record_use`` call the handler makes (D16.2).

    Patched ON the real Phase-B2 module rather than swapped into ``sys.modules``,
    so the handler's deferred
    ``from backend.app.services import library_items_service`` still imports the
    genuine module — that import path IS the contract — while the shelf write
    itself is captured instead of hitting the DB fake.
    """
    from backend.app.services import library_items_service

    calls: list[tuple] = []

    async def _record_use(_supabase, user_id, content_type, content_id):
        calls.append((user_id, content_type, content_id))

    monkeypatch.setattr(library_items_service, "record_use", _record_use)
    return calls


def _client(
    monkeypatch,
    supabase: FakeSupabase,
    *,
    auth_id: str = AUTH_A,
    owner_auth: str = AUTH_A,
    user_id: str = USER_A,
    source_view: Any = None,
) -> TestClient:
    """App wired with the fake user/DB and a stubbed source-view builder.

    ``owner_auth`` is who the workspace item belongs to; a mismatch makes the
    ownership check raise the same 404 the real service raises.
    """
    from backend.app.errors import ErrorCode, LunaHTTPException
    from backend.app.main import create_app

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _FakeAuth(auth_id)
    app.dependency_overrides[get_supabase] = lambda: supabase

    def _get_item(_supabase, requesting_auth_id, item_id):
        if requesting_auth_id != owner_auth:
            raise LunaHTTPException(
                status_code=404,
                code=ErrorCode.ARTIFACT_NOT_FOUND,
                detail="العنصر غير موجود",
            )
        return {"item_id": item_id, "kind": "agent_search", "user_id": user_id}

    monkeypatch.setattr(workspace_api.workspace_service, "get_workspace_item", _get_item)
    monkeypatch.setattr(workspace_api, "get_user_id", lambda _s, _auth: user_id)

    async def _build(_supabase, _row):
        return source_view or ChunkSourceView(
            title="نظام العمل", content="النص الكامل", regulation_title="نظام العمل"
        )

    monkeypatch.setattr(workspace_api, "build_reference_source_view", _build)
    return TestClient(app)


def _url(wi_id: str = WI_A, n: int = 1) -> str:
    return f"/api/v1/workspace/{wi_id}/references/{n}/source"


def test_reveal_charges_exactly_once_then_is_free(monkeypatch) -> None:
    """§1.2: unlocks are permanent and idempotent. The second reveal of the same
    نظام must cost nothing — re-charging a user for what they already opened is
    precisely the trick feeling §5.1 forbids."""
    supabase = base_supabase(
        workspace_item_references=[ref_row(n=1, ref_id=f"reg:{CHUNK_MULTI}")],
    )
    uses = _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    first = client.get(_url())
    assert first.status_code == 200, first.text
    body = first.json()

    assert body["source_view"]["content"] == "النص الكامل"
    assert body["unlocked"]["content_type"] == "regulation"
    assert body["unlocked"]["content_id"] == REG_ID
    assert body["unlocked"]["charged"] is True
    assert body["unlocked"]["cost"] == 1
    # D15.1 — the toast must name the نظام, never the chunk.
    assert body["unlocked"]["title"] == "نظام العمل"
    assert body["balance"]["limit"] == 10
    assert first.headers["cache-control"] == "private, no-store"

    ledger = supabase.tables["library_unlocks"]
    assert len(ledger) == 1
    assert ledger[0]["content_type"] == "regulation"
    assert ledger[0]["content_id"] == REG_ID
    assert ledger[0]["cost"] == 1
    # `surface` is analytics ONLY — it must never alter the charge (migration 104).
    assert ledger[0]["surface"] == "reference"

    second = client.get(_url())
    assert second.status_code == 200, second.text
    assert second.json()["unlocked"]["charged"] is False
    assert second.json()["unlocked"]["reason"] == "already_unlocked"
    assert len(supabase.tables["library_unlocks"]) == 1  # still ONE row

    # One use per action, recorded inside the handler (D16.2) — the frontend
    # must NOT also fire the مكتبتي beacon for these.
    assert uses == [
        (USER_A, "regulation", REG_ID),
        (USER_A, "regulation", REG_ID),
    ]


def test_unlocking_the_nizam_covers_its_madda(monkeypatch) -> None:
    """D5 — a chunk citing مادة 6 of a نظام the user already unlocked is free."""
    supabase = base_supabase(
        workspace_item_references=[ref_row(n=1, ref_id=f"reg:{CHUNK_SINGLE}",
                                           item_id=CHUNK_SINGLE)],
        library_unlocks=[{
            "unlock_id": "seed", "user_id": USER_A, "content_type": "regulation",
            "content_id": REG_ID, "period_key": "free:202607", "cost": 1,
            "surface": "library",
        }],
    )
    _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    res = client.get(_url())
    assert res.status_code == 200, res.text
    assert res.json()["unlocked"]["content_type"] == "article"
    assert res.json()["unlocked"]["content_id"] == f"{REG_ID}#6"
    assert res.json()["unlocked"]["article_no"] == "6"   # §12a C4 — a string
    assert res.json()["unlocked"]["charged"] is False
    assert len(supabase.tables["library_unlocks"]) == 1  # no second row


def test_exhausted_quota_refuses_with_the_d14_body_and_no_content(monkeypatch) -> None:
    supabase = base_supabase(
        workspace_item_references=[ref_row(n=1)],
        quota_row=quota_row(limit=10, used=10),
    )
    uses = _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    res = client.get(_url())
    assert res.status_code == 402
    body = res.json()
    assert body["reason"] == "quota_exhausted"
    assert body["error"]["code"] == "LIBRARY_QUOTA_EXCEEDED"
    assert body["limit"] == 10 and body["used"] == 10
    assert body["resets_at"]
    assert "source_view" not in body
    assert res.headers["cache-control"] == "private, no-store"
    # A refusal is not a use.
    assert uses == []
    assert supabase.tables["library_unlocks"] == []


def test_unresolvable_ref_refuses_402_with_no_content(monkeypatch) -> None:
    """Fail closed. An id we cannot map to a sidecar identity cannot be metered,
    so it must not be served."""
    supabase = base_supabase(
        workspace_item_references=[
            ref_row(n=1, ref_id="reg:not-a-uuid", item_id=None)
        ],
    )
    uses = _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    res = client.get(_url())
    assert res.status_code == 402
    body = res.json()
    assert body["reason"] == "unresolvable"
    assert body["error"]["code"] == "LIBRARY_UNRESOLVABLE"
    assert "source_view" not in body
    assert uses == []
    assert supabase.tables["library_unlocks"] == []


def test_service_reveal_is_free_and_writes_no_ledger_row(monkeypatch) -> None:
    """Compliance services are never gated (§1.3): no charge, no ledger row,
    ever — but the use IS shelved, which is what makes the الخدمات tab in
    «مكتبتي» populate from chat citations (§5B.2)."""
    supabase = base_supabase(
        workspace_item_references=[
            ref_row(n=1, ref_id="compliance:deadbeefcafebabe",
                    domain="compliance", item_id=SVC_ID)
        ],
    )
    uses = _install_fake_library_items(monkeypatch)
    client = _client(
        monkeypatch, supabase,
        source_view=ChunkSourceView(title="خدمة", content="تفاصيل الخدمة"),
    )

    res = client.get(_url())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["unlocked"]["content_type"] == "service"
    assert body["unlocked"]["charged"] is False
    assert body["unlocked"]["reason"] == "open"
    # The quota was never consulted, so the balance chip must not be told a
    # number it would misread as "unlimited".
    assert body["balance"] is None
    assert supabase.tables["library_unlocks"] == []
    assert uses == [(USER_A, "service", SVC_ID)]


def test_short_circular_reveal_is_free(monkeypatch) -> None:
    supabase = base_supabase(
        circulars=[{"id": CIRC_ID, "title": "تعميم قصير", "content": "ن" * 100}],
        workspace_item_references=[
            ref_row(n=1, ref_id=f"circular:{CIRC_ID}", domain="circulars", item_id=CIRC_ID)
        ],
    )
    _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    res = client.get(_url())
    assert res.status_code == 200, res.text
    assert res.json()["unlocked"]["charged"] is False
    assert supabase.tables["library_unlocks"] == []


# --- build BEFORE charge (plan §7.3 / §9 trap 6) ---------------------------


def test_an_unbuildable_source_costs_nothing(monkeypatch) -> None:
    """THE ordering fix.

    ``resolve_access`` used to run at step 4 and the build at step 5, so a source
    that failed to build charged a real, PERMANENT unlock and then answered 404:
    the reader paid for a document they never received, and the unlock is not
    refundable. Building first makes that impossible.

    Asserted the only way that can't be faked: through the real ledger table.
    """
    supabase = base_supabase(
        workspace_item_references=[ref_row(n=1, ref_id=f"reg:{CHUNK_MULTI}")],
    )
    uses = _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    async def _no_view(_supabase, _row):
        return None

    monkeypatch.setattr(workspace_api, "build_reference_source_view", _no_view)

    res = client.get(_url())
    assert res.status_code == 404
    assert res.json()["detail"] == "تعذّر عرض هذا المصدر"
    # Not one ledger row, not one shelf write, not one point spent.
    assert supabase.tables["library_unlocks"] == []
    assert uses == []


def test_an_entitled_reveal_still_charges_after_the_reorder(monkeypatch) -> None:
    """The reorder must not turn the meter off. Same request as
    ``test_reveal_charges_exactly_once_then_is_free``, asserted for the charge
    alone — build-first changes WHEN we charge, never WHETHER."""
    supabase = base_supabase(
        workspace_item_references=[ref_row(n=1, ref_id=f"reg:{CHUNK_MULTI}")],
    )
    _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    res = client.get(_url())
    assert res.status_code == 200, res.text
    assert res.json()["unlocked"]["charged"] is True
    assert len(supabase.tables["library_unlocks"]) == 1


def test_a_refused_reveal_returns_no_body_even_though_it_was_built(monkeypatch) -> None:
    """Build-before-charge means the view EXISTS when the refusal is written. It
    must still be discarded unread — a 402 that leaked the body would hand out
    exactly what the meter is bounding."""
    supabase = base_supabase(
        workspace_item_references=[ref_row(n=1)],
        quota_row=quota_row(limit=10, used=10),
    )
    _install_fake_library_items(monkeypatch)
    client = _client(
        monkeypatch, supabase,
        source_view=ChunkSourceView(title="نظام", content="سر لا يجب تسريبه"),
    )

    res = client.get(_url())
    assert res.status_code == 402
    assert "سر لا يجب تسريبه" not in res.text
    assert "source_view" not in res.json()
    assert supabase.tables["library_unlocks"] == []


# --- the two simple_search domains, end to end through the route -----------


def test_article_reveal_charges_the_madda_and_shelves_it(monkeypatch) -> None:
    """An ``articles`` row reveals through the same metered path as any other
    citation, on the same ledger types."""
    supabase = base_supabase(
        workspace_item_references=[
            ref_row(n=1, ref_id=f"article:{ART_ID}", domain="articles", item_id=ART_ID)
        ],
    )
    uses = _install_fake_library_items(monkeypatch)
    client = _client(
        monkeypatch, supabase,
        source_view=ArticleFullSourceView(
            title="المادة 6 من نظام العمل",
            article_num="6",
            content="نص المادة الكامل",
            regulation_title="نظام العمل",
        ),
    )

    res = client.get(_url())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["domain"] == "articles"
    assert body["source_view"]["source_type"] == "article_full"
    assert body["source_view"]["content"] == "نص المادة الكامل"
    assert body["unlocked"]["content_type"] == "article"
    assert body["unlocked"]["content_id"] == f"{REG_ID}#6"
    # §12a C4: a STRING on the wire, even when the number is a plain integer.
    assert body["unlocked"]["article_no"] == "6"
    assert body["unlocked"]["charged"] is True
    assert uses == [(USER_A, "article", f"{REG_ID}#6")]


# --- §12a C4: ``article_no`` is a STRING on the wire -----------------------
#
# ``articles_v2.article_number`` is TEXT. Typing this field as a number meant
# «1-1» / «81 مكرر» arrived as ``null``, and ``unlockedNotice`` fell through to
# its «بجميع مواده» branch — telling the reader they unlocked the whole نظام
# while they were looking at ONE مادة, AFTER the unlock was spent. Silent, and
# on a metered action.


@pytest.mark.parametrize(
    "article_id, number",
    [(ART_COMPOUND_ID, "7-4"), (ART_MUKARRAR_ID, "81 مكرر")],
)
def test_a_compound_madda_number_reaches_the_unlock_notice(
    monkeypatch, article_id: str, number: str
) -> None:
    """THE regression. A compound مادة must still NAME the مادة in the notice.

    The two halves are deliberately independent:

    * **Metering** lifts to the نظام — «7-4» has no published مادة page, so no
      sidecar key exists to charge against. That is correct and unchanged.
    * **The notice** still names «المادة 7-4», because the reader clicked one
      مادة and just paid for it. It reads the raw TEXT off the built
      ``ArticleFullSourceView``, which never went through the resolver's int
      gate.

    Without both, the reader spends an unlock on «المادة 81 مكرر» and is told
    they opened «نظام العمل كاملاً — بجميع مواده».
    """
    supabase = base_supabase(
        workspace_item_references=[
            ref_row(n=1, ref_id=f"article:{article_id}", domain="articles",
                    item_id=article_id)
        ],
    )
    _install_fake_library_items(monkeypatch)
    client = _client(
        monkeypatch, supabase,
        source_view=ArticleFullSourceView(
            title=f"المادة {number} من نظام العمل",
            article_num=number,
            content="نص المادة الكامل",
            regulation_title="نظام العمل",
        ),
    )

    res = client.get(_url())
    assert res.status_code == 200, res.text
    unlocked = res.json()["unlocked"]

    # The notice's article branch is `articleNo !== null` — this is what arms it.
    assert unlocked["article_no"] == number
    assert isinstance(unlocked["article_no"], str)
    # …while the CHARGE still lands on the نظام (D15.1 / D5), unchanged.
    assert unlocked["content_type"] == "regulation"
    assert unlocked["content_id"] == REG_ID
    assert unlocked["charged"] is True
    assert supabase.tables["library_unlocks"][0]["content_type"] == "regulation"


def test_a_single_madda_chunk_reports_its_number_as_a_string(monkeypatch) -> None:
    """The ``reg:`` path types the same field. Its number comes off the chunk's
    ``owns`` map (ints by construction) and is stringified at the payload edge,
    so the frontend sees ONE type on this field regardless of which door the
    citation came through."""
    supabase = base_supabase(
        workspace_item_references=[ref_row(n=1, ref_id=f"reg:{CHUNK_SINGLE}")],
    )
    _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    unlocked = client.get(_url()).json()["unlocked"]
    assert unlocked["article_no"] == "6"
    assert isinstance(unlocked["article_no"], str)
    assert unlocked["content_id"] == f"{REG_ID}#6"


@pytest.mark.parametrize(
    "ref_id, domain, item_id, view",
    [
        # A chunk owning 3 مواد — it does not know which one, and neither does
        # ``ChunkSourceView`` (it has no ``article_num`` field at all).
        (f"reg:{CHUNK_MULTI}", "regulations", CHUNK_MULTI, None),
        # A whole نظام — there is no مادة to name.
        (f"regdoc:{REG_ID}", "regulation_docs", REG_ID,
         RegulationSummarySourceView(title="نظام العمل", content="ملخص النظام")),
    ],
)
def test_a_reference_that_names_no_madda_still_sends_null(
    monkeypatch, ref_id: str, domain: str, item_id: str, view: Any
) -> None:
    """The other direction of the same guard, and the reason the fallback reads
    ``article_num`` off the VIEW rather than guessing from the domain: a source
    view that carries no مادة number must never acquire one, or the notice would
    claim a مادة for an unlock that really did cover «النظام كاملاً — بجميع
    مواده»."""
    supabase = base_supabase(
        workspace_item_references=[
            ref_row(n=1, ref_id=ref_id, domain=domain, item_id=item_id)
        ],
    )
    _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase, source_view=view)

    unlocked = client.get(_url()).json()["unlocked"]
    assert unlocked["article_no"] is None
    assert unlocked["content_type"] == "regulation"


def test_regdoc_reveal_names_the_nizam_in_the_unlock(monkeypatch) -> None:
    """D15.1's rule holds for the whole-نظام ref too: the toast names the
    document, never a uuid — and here the resolver's own title supplies it."""
    supabase = base_supabase(
        workspace_item_references=[
            ref_row(n=1, ref_id=f"regdoc:{REG_ID}", domain="regulation_docs",
                    item_id=REG_ID)
        ],
    )
    _install_fake_library_items(monkeypatch)
    client = _client(
        monkeypatch, supabase,
        source_view=RegulationSummarySourceView(
            title="نظام العمل", content="ملخص النظام"
        ),
    )

    res = client.get(_url())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["domain"] == "regulation_docs"
    assert body["source_view"]["source_type"] == "regulation_summary"
    assert body["unlocked"]["content_type"] == "regulation"
    assert body["unlocked"]["content_id"] == REG_ID
    assert body["unlocked"]["title"] == "نظام العمل"
    assert supabase.tables["library_unlocks"][0]["content_type"] == "regulation"


def test_a_regdoc_reveal_is_free_once_the_nizam_is_unlocked(monkeypatch) -> None:
    """§1.2 — unlocks are permanent and idempotent ACROSS surfaces. A user who
    unlocked this نظام through a ``reg:`` chunk must not pay again to open it as a
    whole document; the shared ``('regulation', id)`` tuple is what guarantees it,
    and is the reason no new metering type was invented."""
    supabase = base_supabase(
        workspace_item_references=[
            ref_row(n=1, ref_id=f"regdoc:{REG_ID}", domain="regulation_docs",
                    item_id=REG_ID)
        ],
        library_unlocks=[{
            "unlock_id": "seed", "user_id": USER_A, "content_type": "regulation",
            "content_id": REG_ID, "period_key": "free:202607", "cost": 1,
            "surface": "library",
        }],
    )
    _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    res = client.get(_url())
    assert res.status_code == 200, res.text
    assert res.json()["unlocked"]["charged"] is False
    assert res.json()["unlocked"]["reason"] == "already_unlocked"
    assert len(supabase.tables["library_unlocks"]) == 1   # still ONE row


def test_cross_user_item_id_is_refused_before_anything_else(monkeypatch) -> None:
    """IDOR guard. A reveal endpoint without the ownership check would hand out
    another lawyer's research — and would let an attacker mine the corpus
    through other people's item_ids, off their meter."""
    supabase = base_supabase(
        workspace_item_references=[ref_row(n=1, wi_id=WI_A)],
    )
    uses = _install_fake_library_items(monkeypatch)
    # User B asks for user A's item.
    client = _client(monkeypatch, supabase, auth_id=AUTH_B, owner_auth=AUTH_A)

    res = client.get(_url(WI_A))
    assert res.status_code == 404
    assert res.json()["detail"] == "العنصر غير موجود"
    # Nothing downstream ran: no ledger row, no shelf write, no body.
    assert supabase.tables["library_unlocks"] == []
    assert uses == []
    assert "source_view" not in res.json()


def test_unknown_n_is_404_and_costs_nothing(monkeypatch) -> None:
    supabase = base_supabase(workspace_item_references=[ref_row(n=1)])
    _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    res = client.get(_url(WI_A, n=99))
    assert res.status_code == 404
    assert res.json()["detail"] == "المرجع غير موجود"
    assert supabase.tables["library_unlocks"] == []


def test_reference_rows_of_another_item_are_not_reachable(monkeypatch) -> None:
    """``n`` is a small guessable integer, so the row lookup is scoped to the
    (already ownership-checked) wi_id. A ref that belongs to WI_B must not be
    served through WI_A."""
    supabase = base_supabase(
        workspace_item_references=[ref_row(n=1, wi_id=WI_B)],
    )
    _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    assert client.get(_url(WI_A, n=1)).status_code == 404


def test_locked_account_is_refused(monkeypatch) -> None:
    supabase = base_supabase(
        workspace_item_references=[ref_row(n=1)],
        quota_row=quota_row(locked=True),
    )
    _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    res = client.get(_url())
    assert res.status_code == 402
    assert res.json()["reason"] == "locked"
    assert supabase.tables["library_unlocks"] == []


# ===========================================================================
# 4. Rate limiting (D13.2 — the shared library budget)
# ===========================================================================


def test_route_declares_the_library_rate_limit_dependency() -> None:
    """The limiter must be the SHARED module-level singleton: ``/library/full``
    and this endpoint deliberately split ONE 20/min budget, so alternating
    between them cannot buy 40/min."""
    from backend.app.main import create_app

    app = create_app()
    route = next(
        r for r in app.routes
        if getattr(r, "path", "") == "/api/v1/workspace/{item_id}/references/{n}/source"
    )
    calls = [d.call for d in route.dependant.dependencies]
    assert library_rate_limit in calls, calls


def test_reveal_is_rate_limited_and_fails_closed_without_redis(monkeypatch) -> None:
    """No Redis in tests → the route limiter's process-local fallback runs, which
    is the D13.3 fail-CLOSED path. Over budget must be 429, not a free ride."""
    supabase = base_supabase(workspace_item_references=[ref_row(n=1)])
    _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    statuses = [
        client.get(_url()).status_code for _ in range(LIBRARY_ROUTE_RATE_LIMIT + 1)
    ]
    assert statuses[:LIBRARY_ROUTE_RATE_LIMIT] == [200] * LIBRARY_ROUTE_RATE_LIMIT
    assert statuses[-1] == 429


# ===========================================================================
# cross_refs[].content — the side-channel PART 6 left open
# ===========================================================================
#
# §6.2 says keep `cross_refs`, and §1.3 puts "citation lists (the mesh)" in the
# never-gated class. But CrossRef.content carries the RESOLVED BODY of each
# cross-referenced مادة, up to MAX_CROSS_REFS_REF per reference, shipped free in
# the citation list — measured at 21.7% of the post-Phase-C payload on live
# panels. §1.3 puts "regulation article bodies" in the PARTIALLY GATED class, so
# the mesh survives and the body is cut to the public article page's own free
# window. Chat must not expose more than a logged-out visitor already sees.


def test_cross_ref_bodies_are_cut_to_the_public_article_window():
    from agents.deep_search_v4.ura.schema import (
        CROSS_REF_REFERENCE_FREE_CHARS,
        CrossRef,
        RegURAResult,
    )

    long_body = "مادة " * 4000  # ~20k chars
    ura = RegURAResult(
        ref_id="reg:11111111-1111-1111-1111-111111111111",
        source_type="chunk",
        relevance="high",
        cross_refs=[
            CrossRef(
                target_type="article",
                target_reg_title="نظام العمل",
                target_number=77,
                relation="يحيل إلى",
                content=long_body,
            )
        ],
    )

    view = ura.for_reference()
    cr = view.cross_refs[0]

    assert len(cr.content) <= CROSS_REF_REFERENCE_FREE_CHARS + 2  # + " …"
    assert len(cr.content) < len(long_body)
    # The MESH itself is never gated — it must survive intact.
    assert cr.target_reg_title == "نظام العمل"
    assert cr.target_number == 77
    assert cr.target_type == "article"
    assert cr.relation == "يحيل إلى"


def test_short_cross_ref_bodies_are_left_alone():
    from agents.deep_search_v4.ura.schema import CrossRef, RegURAResult

    short = "نص قصير جداً"
    ura = RegURAResult(
        ref_id="reg:11111111-1111-1111-1111-111111111111",
        source_type="chunk",
        relevance="high",
        cross_refs=[CrossRef(target_type="article", content=short)],
    )
    assert ura.for_reference().cross_refs[0].content == short


def test_the_aggregator_view_keeps_full_cross_ref_bodies():
    """The model needs the whole text to reason with, and that payload never
    reaches the user — gating it would degrade answers for no privacy gain."""
    from agents.deep_search_v4.ura.schema import CrossRef, RegURAResult

    long_body = "مادة " * 4000
    ura = RegURAResult(
        ref_id="reg:11111111-1111-1111-1111-111111111111",
        source_type="chunk",
        relevance="high",
        cross_refs=[CrossRef(target_type="article", content=long_body)],
    )
    assert ura.for_aggregator().cross_refs[0].content == long_body


def test_case_referenced_regulations_are_gated_too():
    """The case domain projects `referenced_regulations` into the same
    ReferenceView.cross_refs field — the second path into the panel."""
    from agents.deep_search_v4.ura.schema import (
        CROSS_REF_REFERENCE_FREE_CHARS,
        CaseURAResult,
    )

    # NB: this field is a list[dict], not list[CrossRef] — the gate must handle
    # both shapes because both reach the same panel.
    long_body = "حكم " * 4000
    ura = CaseURAResult(
        ref_id="case:1234",
        source_type="case",
        relevance="high",
        referenced_regulations=[
            {"target_type": "article", "target_reg_title": "نظام", "content": long_body}
        ],
    )
    out = ura.for_reference().referenced_regulations[0]
    assert len(out["content"]) <= CROSS_REF_REFERENCE_FREE_CHARS + 2
    assert out["target_reg_title"] == "نظام"


# ===========================================================================
# 5. «فتح ال… في ريحان» — the citation → library-page link (2026-08-01)
#
# The other half of the D15 resolver: `reference_resolver` maps a citation onto
# the (content_type, content_id) pair the ledger speaks; `public_page_url` maps
# that pair onto the page a reader can actually open in our own library. A
# citation and a library page are the SAME document reached two ways, and this
# link is what finally says so in the product.
# ===========================================================================


def _meta(content_type: str, content_id: str, slug: str) -> dict[str, Any]:
    return {"content_type": content_type, "content_id": content_id, "slug": slug}


def test_each_wing_maps_to_its_public_page() -> None:
    from backend.app.services import library_items_service as lis

    supabase = base_supabase(
        seo_item_meta=[
            _meta("regulation", REG_ID, "nizam-al-amal"),
            _meta("judgment", CASE_ID, "hukm-tijari-123"),
            _meta("circular", CIRC_ID, "taamim-muhim"),
            _meta("service", SVC_ID, "istikhraj-sak"),
        ]
    )

    assert run(lis.public_page_url(supabase, "regulation", REG_ID)) == (
        "/regulations/nizam-al-amal"
    )
    assert run(lis.public_page_url(supabase, "judgment", CASE_ID)) == (
        "/judgments/hukm-tijari-123"
    )
    assert run(lis.public_page_url(supabase, "circular", CIRC_ID)) == (
        "/circulars/taamim-muhim"
    )
    # ⚠ A SERVICE HAS NO PAGE, even with a sidecar slug (2026-08-03). The
    # compliance wing was retired, so «فتح الخدمة في ريحان» must NOT appear — the
    # dialog drops that button on a `None`, which is the whole point of returning
    # one here rather than a path that 404s.
    assert run(lis.public_page_url(supabase, "service", SVC_ID)) is None


def test_madda_citation_links_to_its_nizam_page() -> None:
    """User decision 2026-08-01: a مادة-level citation opens the نظام page, NOT
    ``/regulations/{reg}/{article}``.

    81% of ``reg:`` refs already lift to the whole statute (D15.1), so deep
    linking the other 19% would make one button land in two structurally
    different places for a difference the reader cannot see — and the نظام page
    carries the مادة anyway."""
    from backend.app.services import library_items_service as lis

    supabase = base_supabase(
        seo_item_meta=[
            _meta("regulation", REG_ID, "nizam-al-amal"),
            # A published مادة page EXISTS and is still not what we link to.
            _meta("article", f"{REG_ID}#6", "al-madda-6"),
        ]
    )
    url = run(lis.public_page_url(supabase, "article", f"{REG_ID}#6", REG_ID))
    assert url == "/regulations/nizam-al-amal"


def test_unpublished_item_has_no_library_url() -> None:
    """No sidecar slug ⇒ no page ⇒ `None`, and the panel drops the button.

    Never a hub fallback: a button that promises the document and delivers a
    list is worse than one that isn't there."""
    from backend.app.services import library_items_service as lis

    supabase = base_supabase(seo_item_meta=[])
    assert run(lis.public_page_url(supabase, "regulation", REG_ID)) is None
    assert run(lis.public_page_url(supabase, "judgment", CASE_ID)) is None
    # An unknown / non-library type can never mint a URL either.
    assert run(lis.public_page_url(supabase, "calculator", REG_ID)) is None
    assert run(lis.public_page_url(supabase, "regulation", "")) is None


def test_reveal_response_carries_the_library_url(monkeypatch) -> None:
    supabase = base_supabase(
        workspace_item_references=[ref_row(n=1, ref_id=f"reg:{CHUNK_MULTI}")],
        seo_item_meta=[_meta("regulation", REG_ID, "nizam-al-amal")],
    )
    _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    body = client.get(_url()).json()
    assert body["library_url"] == "/regulations/nizam-al-amal"


def test_reveal_library_url_is_null_when_nothing_is_published(monkeypatch) -> None:
    """A missing link must never cost the reader the source they just paid for."""
    supabase = base_supabase(
        workspace_item_references=[ref_row(n=1, ref_id=f"reg:{CHUNK_MULTI}")],
        seo_item_meta=[],
    )
    _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)

    res = client.get(_url())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["library_url"] is None
    # The content itself is untouched by the absent link.
    assert body["source_view"]["content"] == "النص الكامل"


def test_reveal_survives_a_sidecar_failure(monkeypatch) -> None:
    """Fail-soft, in the direction that keeps the paid content flowing."""
    from backend.app.services import library_items_service as lis

    supabase = base_supabase(
        workspace_item_references=[ref_row(n=1, ref_id=f"reg:{CHUNK_MULTI}")],
    )
    _install_fake_library_items(monkeypatch)

    async def _boom(*_a, **_k):
        raise RuntimeError("sidecar down")

    monkeypatch.setattr(lis, "public_page_url", _boom)
    client = _client(monkeypatch, supabase)

    res = client.get(_url())
    assert res.status_code == 200, res.text
    assert res.json()["library_url"] is None
    assert res.json()["source_view"]["content"] == "النص الكامل"


def test_the_source_view_has_no_pdf_exit() -> None:
    """PDF signals were removed from the reference surface (2026-08-01): the
    popup exits to exactly two places, the official link and our own page."""
    view = ChunkSourceView(
        title="نظام العمل", content="النص", regulation_title="نظام العمل"
    )
    dumped = view.model_dump(mode="json")
    assert "regulation_pdf_link" not in dumped
    assert not any("pdf" in k.lower() for k in dumped)


# ===========================================================================
# 6. Chunk tables in the مراجع reveal
#    (`.claude/plans/chunk_table_rendering.md` §4, D1/D2/D10/D11)
# ===========================================================================
#
# Every table in the regulation corpus was OCR'd and then CONVERTED TO PROSE
# before ingestion, because prose is what BM25 indexes and what the model reads.
# `chunks_v2.content` keeps that prose (the AGENT view) and `content_display`
# carries the same text with each table collapsed to a one-line `TBL_…` token
# (the USER view), with `chunk_tables_v2` holding the markup behind each token.
#
# The whole design pressure here is that `ura.enrich._enrich_regulations` is
# shared by TWO callers: the live deep_search turn, and this reveal — which runs
# on a click. 29 MB of markup exists corpus-wide and only 7.7% of regulation
# citations point at a chunk that has any, so the live turn must fetch NOTHING
# extra and its prompt surface must not move by a byte.

TABLE_CHUNK = "aaaabbbb-0000-4000-8000-00000000cafe"
PLAIN_CHUNK = "aaaacccc-0000-4000-8000-00000000beef"

TBL_A = "TBL_17405_reg_603_chunk_019_1"
TBL_GHOST = "TBL_17405_reg_603_chunk_019_9"  # a token with no row — D3

_TABLE_MD = (
    "1. م: 1 — المخالفة: التأخر في السداد — حد قيمة الغرامة: 500 ريال.\n"
    "2. م: 2 — المخالفة: عدم الإفصاح — حد قيمة الغرامة: 1000 ريال."
)
_TABLE_HTML = (
    '<table><tr><th colspan="2">جدول الغرامات</th></tr>'
    '<tr><td rowspan="1">1</td><td>500 ريال</td></tr>'
    "<tr><td>2</td><td>1000 ريال</td></tr></table>"
)

#: The AGENT view — what `for_aggregator()` projects and BM25 indexes.
_CHUNK_PROSE = (
    "المادة الأولى: تسري أحكام هذه اللائحة على كل منشأة.\n"
    "\n"
    f"{_TABLE_MD}\n"
    "\n"
    "وتُطبق العقوبات وفق الجدول أعلاه."
)
#: The USER view — same law, the grid collapsed to a whole-line token. The
#: ghost token is deliberate: a re-ingest can run ahead of the DB, and a raw
#: `TBL_…` on a user surface is the one failure this design exists to prevent.
_CHUNK_DISPLAY = (
    "المادة الأولى: تسري أحكام هذه اللائحة على كل منشأة.\n"
    "\n"
    f"{TBL_A}\n"
    "\n"
    f"{TBL_GHOST}\n"
    "\n"
    "وتُطبق العقوبات وفق الجدول أعلاه."
)


def table_row(ref: str = TBL_A, *, chunk_id: str = TABLE_CHUNK) -> dict[str, Any]:
    """One raw ``chunk_tables_v2`` row, in the four columns the reveal selects."""
    return {
        "table_ref": ref,
        "chunk_id": chunk_id,
        "table_html": _TABLE_HTML,
        "table_md": _TABLE_MD,
    }


def table_supabase(**over: Any) -> FakeSupabase:
    """A FakeSupabase whose reg chunk carries a real table."""
    seeded: dict[str, Any] = {
        "chunks_v2": [
            {
                "id": TABLE_CHUNK,
                "regulation_id": REG_ID,
                "owns": {"MADDA": [1]},
                "content": _CHUNK_PROSE,
                "content_display": _CHUNK_DISPLAY,
                "context": "",
            },
            {
                "id": PLAIN_CHUNK,
                "regulation_id": REG_ID,
                "owns": {"MADDA": [2]},
                "content": "المادة الثانية: نص بلا جداول.",
                "content_display": None,
                "context": "",
            },
        ],
        "chunk_tables_v2": [table_row()],
    }
    seeded.update(over)
    return base_supabase(**seeded)


class _SpyChain(_Chain):
    """``_Chain`` that records ``(table, select-list)`` on the parent fake."""

    def select(self, *cols: Any, **kw: Any) -> "_SpyChain":
        self._fake.queries.append((self._table, ", ".join(str(c) for c in cols)))
        return super().select(*cols, **kw)  # type: ignore[return-value]


class SpySupabase(FakeSupabase):
    """FakeSupabase that logs every table read. Same rows, same semantics."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.queries: list[tuple[str, str]] = []

    def table(self, name: str) -> _SpyChain:  # type: ignore[override]
        return _SpyChain(self, name)

    def tables_queried(self) -> set[str]:
        return {t for t, _ in self.queries}

    def selects_for(self, table: str) -> list[str]:
        return [cols for t, cols in self.queries if t == table]


def spy_supabase(**over: Any) -> SpySupabase:
    base = table_supabase(**over)
    return SpySupabase(quota_row=base.quota_row, **base.tables)


def reg_shell(chunk_id: str = TABLE_CHUNK) -> RegURAResult:
    return RegURAResult(
        ref_id=f"reg:{chunk_id}", source_type="reg_chunk", relevance="high"
    )


def _reader_visible_payload(view: ChunkSourceView) -> str:
    """Everything in a reveal that a READER can end up looking at.

    A table segment's ``ref`` is excluded on purpose: it is the resolution key
    and the client's list key, it is the one place the ``TBL_…`` string is
    supposed to exist, and it is never rendered. Everything else — the prose,
    the grids, the copy text — is fair game for the D3 assertion.
    """
    parts = [view.content]
    for seg in view.display_segments:
        if seg["kind"] == "text":
            parts.append(seg["text"])
        else:
            parts.extend([seg["html"], seg["md"]])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 6.1 The live search turn — the assertion that protects the cost of a search
# ---------------------------------------------------------------------------


def test_the_live_search_turn_fetches_no_tables() -> None:
    """D10, and the headline. ``enrich_ura`` must not touch ``chunk_tables_v2``.

    This is the whole reason ``with_tables`` defaults to False. The corpus holds
    29.0 MB of table markup; only 7.7% of regulation citations point at a chunk
    that has any, and a search turn that pulled it for bodies nobody opens would
    pay for all of it — twice, because the URA is PERSISTED as a retrieval
    artifact.

    Asserted three ways, because "we did not mean to" is not a mechanism: zero
    queries against the table, no extra COLUMN on the chunks read, and both
    stored-only fields still at their defaults.
    """
    supabase = spy_supabase()
    ura = UnifiedRetrievalArtifact(high_results=[reg_shell()])

    run(enrich_ura(ura, supabase))

    assert "chunk_tables_v2" not in supabase.tables_queried()
    # …and not one extra column on the read it DOES make.
    chunk_selects = supabase.selects_for("chunks_v2")
    assert chunk_selects, "the live turn still reads chunks_v2"
    assert all("content_display" not in cols for cols in chunk_selects)

    res = ura.high_results[0]
    assert res.chunk_display == ""
    assert res.chunk_tables == []
    # The prose arrived, untouched — this turn is not degraded, just unloaded.
    assert res.chunk_content == _CHUNK_PROSE


def test_the_live_turn_column_list_is_exactly_todays() -> None:
    """Pins the select string itself, so a stray column cannot ride in later."""
    supabase = spy_supabase()
    run(enrich_ura(UnifiedRetrievalArtifact(high_results=[reg_shell()]), supabase))
    assert supabase.selects_for("chunks_v2") == [
        "id, regulation_id, title, summary, context, content, owns"
    ]


def test_for_aggregator_is_byte_identical() -> None:
    """D2 + the prompt cache. The synthesis surface must not move by a byte.

    ``content_display`` has table content REMOVED — prompting on it would
    silently hand the model a statute with its tables deleted. And even a
    harmless-looking addition to ``AggregatorItem`` would invalidate the
    prompt-cache prefix on every provider. So the two new fields are stored
    only, exactly as ``chunk_context`` / ``pdf_url`` / ``owns`` / ``doc_type``
    already are.
    """
    from agents.deep_search_v4.aggregator.preprocessor import (
        render_aggregator_content,
    )

    plain = reg_shell()
    plain.chunk_content = _CHUNK_PROSE
    plain.reg_title = "نظام العمل"

    loaded = reg_shell()
    loaded.chunk_content = _CHUNK_PROSE
    loaded.reg_title = "نظام العمل"
    loaded.chunk_display = _CHUNK_DISPLAY
    loaded.chunk_tables = [table_row()]

    before = plain.for_aggregator(n=3)
    after = loaded.for_aggregator(n=3)

    assert after.model_dump_json() == before.model_dump_json()
    rendered = render_aggregator_content(after)
    assert rendered == render_aggregator_content(before)
    # The prompt block carries the PROSE, and no display artifact of any kind.
    assert _TABLE_MD.splitlines()[0] in rendered
    assert "TBL_" not in rendered
    assert "<table" not in rendered
    # Neither field is even a member of the projected model.
    assert "chunk_display" not in type(after).model_fields
    assert "chunk_tables" not in type(after).model_fields


def test_the_reference_card_projection_is_untouched_too() -> None:
    """``for_reference()`` feeds the citation CARD, which is never a body."""
    loaded = reg_shell()
    loaded.chunk_display = _CHUNK_DISPLAY
    loaded.chunk_tables = [table_row()]
    dumped = loaded.for_reference().model_dump(mode="json")
    assert "chunk_display" not in dumped and "chunk_tables" not in dumped
    assert "TBL_" not in json.dumps(dumped, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 6.2 The reveal — where the grids are supposed to appear
# ---------------------------------------------------------------------------


def test_the_reveal_carries_segments() -> None:
    """``with_tables=True`` turns the token back into a grid — and ONLY there.

    The reveal runs on a click, on one row, and its entire output is a body the
    reader is about to read. That is the one place the 29 MB is worth touching.
    """
    supabase = spy_supabase()
    row = ref_row(n=1, ref_id=f"reg:{TABLE_CHUNK}", item_id=TABLE_CHUNK)

    view = run(references_service.build_reference_source_view(supabase, row))

    assert isinstance(view, ChunkSourceView)
    segments = view.display_segments
    assert segments, "a table-bearing chunk must segment"

    tables = [s for s in segments if s["kind"] == "table"]
    texts = [s for s in segments if s["kind"] == "text"]
    # One token resolved (TBL_A); the ghost token resolved to nothing (D3).
    assert len(tables) == 1
    assert tables[0]["ref"] == TBL_A
    assert tables[0]["md"] == _TABLE_MD
    assert len(segments) == len(tables) + len(texts)

    # The grid is real, merged cells intact, and it was SANITIZED on the way
    # through (`tables_by_ref` -> `sanitize_table_html`) — raw corpus markup
    # never reaches a view.
    assert tables[0]["html"].startswith("<table>")
    assert 'colspan="2"' in tables[0]["html"]
    assert "500 ريال" in tables[0]["html"]

    # …and `content` is still the PROSE. It is what «نسخ المحتوى» pastes (D11)
    # and what any consumer ignoring segments renders.
    assert _TABLE_MD.splitlines()[0] in view.content
    assert "TBL_" not in view.content
    assert "<table" not in view.content


def test_the_reveal_is_the_only_caller_that_reads_the_table() -> None:
    """The read happens on the click — and it is one batched, column-narrow hop."""
    supabase = spy_supabase()
    row = ref_row(n=1, ref_id=f"reg:{TABLE_CHUNK}", item_id=TABLE_CHUNK)

    run(references_service.build_reference_source_view(supabase, row))

    selects = supabase.selects_for("chunk_tables_v2")
    assert selects == ["table_ref, chunk_id, table_html, table_md"]
    # `content_display` rides ALONGSIDE `content`, never instead of it — the
    # fail-soft path needs the prose.
    chunk_cols = supabase.selects_for("chunks_v2")[0]
    assert "content_display" in chunk_cols and "content," in chunk_cols


def test_the_citation_list_read_fetches_no_tables() -> None:
    """The panel LIST serves no source bodies at all (Phase C), so it has no
    business pulling markup for N citations at once. The plan named
    ``_build_reg_shells`` as "the click"; it is not — the reveal is."""
    supabase = spy_supabase(
        workspace_item_references=[
            ref_row(n=1, ref_id=f"reg:{TABLE_CHUNK}", item_id=TABLE_CHUNK)
        ],
    )
    run(references_service.fetch_item_references(supabase, WI_A))
    assert "chunk_tables_v2" not in supabase.tables_queried()


def test_a_reveal_without_tables_emits_empty_segments() -> None:
    """The 82% case: no ``content_display``, so nothing to segment.

    ``[]`` is not a degraded state, it is THE state — it means "render
    ``content`` exactly as today", which is what makes every artifact persisted
    before this shipped keep working with no compatibility branch anywhere.
    """
    supabase = table_supabase()
    row = ref_row(n=2, ref_id=f"reg:{PLAIN_CHUNK}", item_id=PLAIN_CHUNK)

    view = run(references_service.build_reference_source_view(supabase, row))

    assert isinstance(view, ChunkSourceView)
    assert view.display_segments == []
    assert view.content == "المادة الثانية: نص بلا جداول."


def test_a_chunk_with_a_display_body_but_no_rows_renders_the_prose() -> None:
    """Fail-soft, in the direction that is NOT obvious.

    If the ``chunk_tables_v2`` read comes back empty for a chunk that HAS a
    display body, splitting that body against an empty map would let every token
    resolve to nothing — which does not degrade the نظام, it DELETES tables from
    it. The fallback is ``content``: the prose, tables intact as the flattened
    list they have always been.
    """
    supabase = table_supabase(chunk_tables_v2=[])
    row = ref_row(n=1, ref_id=f"reg:{TABLE_CHUNK}", item_id=TABLE_CHUNK)

    view = run(references_service.build_reference_source_view(supabase, row))

    assert view is not None and view.display_segments == []
    assert _TABLE_MD.splitlines()[0] in view.content
    assert "TBL_" not in view.content


def test_a_failed_tables_read_still_serves_the_source() -> None:
    """A PostgREST hiccup costs a grid, never the citation."""
    supabase = table_supabase()
    supabase.fail_tables.add("chunk_tables_v2")
    row = ref_row(n=1, ref_id=f"reg:{TABLE_CHUNK}", item_id=TABLE_CHUNK)

    view = run(references_service.build_reference_source_view(supabase, row))

    assert view is not None and view.display_segments == []
    assert "TBL_" not in view.content
    assert _TABLE_MD.splitlines()[0] in view.content


def test_segments_carry_no_raw_token() -> None:
    """D3, asserted on the literal string.

    A raw ``TBL_17405_reg_603_chunk_019_9`` printed inside a statute — or pasted
    into a memo — is the single failure mode this whole design exists to
    prevent, and the ghost token in the fixture is exactly the shape a re-ingest
    running ahead of the DB produces.
    """
    supabase = table_supabase()
    row = ref_row(n=1, ref_id=f"reg:{TABLE_CHUNK}", item_id=TABLE_CHUNK)

    view = run(references_service.build_reference_source_view(supabase, row))
    assert view is not None

    for seg in view.display_segments:
        if seg["kind"] == "text":
            assert "TBL_" not in seg["text"]
            # The dropped token leaves no scar either — no orphaned blank run.
            assert seg["text"].strip()
        else:
            # A table's `ref` IS the token — that is the lookup key and the
            # client's list key, and it is the ONE place the string is allowed
            # to live. Nothing a reader ever sees may carry it.
            assert "TBL_" not in seg["html"] and "TBL_" not in seg["md"]
    assert "TBL_" not in view.content
    assert "TBL_" not in _reader_visible_payload(view)


def test_the_tables_read_is_paged() -> None:
    """PostgREST clamps at 1000 rows and one نظام already carries 965.

    Past the clamp the missing rows do not error — they simply do not arrive,
    and the renderer drops every token it cannot resolve, so each one becomes a
    DELETED table. This is the bug that never announces itself.
    """
    refs = [f"TBL_paged_chunk_{i:05d}" for i in range(1, 1601)]
    body = "\n\n".join(refs)
    supabase = table_supabase(
        chunks_v2=[
            {
                "id": TABLE_CHUNK,
                "regulation_id": REG_ID,
                "owns": {},
                "content": "\n\n".join([_TABLE_MD] * len(refs)),
                "content_display": body,
                "context": "",
            }
        ],
        chunk_tables_v2=[table_row(ref) for ref in refs],
    )
    row = ref_row(n=1, ref_id=f"reg:{TABLE_CHUNK}", item_id=TABLE_CHUNK)

    view = run(references_service.build_reference_source_view(supabase, row))

    assert view is not None
    tables = [s for s in view.display_segments if s["kind"] == "table"]
    assert len(tables) == len(refs)          # ALL of them, not the first 1000
    assert {s["ref"] for s in tables} == set(refs)
    # Not one token slipped through as text — which is what a lost page looks
    # like from the reader's side: a table quietly gone, no error anywhere.
    assert "TBL_" not in _reader_visible_payload(view)


def test_the_enrich_flag_is_keyword_only_and_defaults_off() -> None:
    """The default is the whole safety property, so pin the signature.

    Positional ``with_tables`` would let a caller flip it by accident, and a
    True default would put 29 MB of markup on every search turn.
    """
    import inspect

    from agents.deep_search_v4.ura import enrich as enrich_mod

    param = inspect.signature(enrich_mod._enrich_regulations).parameters["with_tables"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is False

    shell_param = inspect.signature(
        references_service._build_reg_shells
    ).parameters["with_tables"]
    assert shell_param.kind is inspect.Parameter.KEYWORD_ONLY
    assert shell_param.default is False
