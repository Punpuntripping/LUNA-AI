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

from agents.deep_search_v4.source_viewer import ChunkSourceView
from agents.deep_search_v4.ura.schema import (
    CaseURAResult,
    CircularURAResult,
    RegURAResult,
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
from backend.tests.test_library_gating import FakeSupabase, quota_row


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
    assert res.json()["unlocked"]["article_no"] == 6
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
    # Services are never gated (§1.3) but they DO have a page — «فتح الخدمة في
    # ريحان» must work on a free reveal exactly as it does on a charged one.
    assert run(lis.public_page_url(supabase, "service", SVC_ID)) == (
        "/compliance/istikhraj-sak"
    )


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
