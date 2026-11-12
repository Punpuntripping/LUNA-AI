"""Wiring of segmented delivery (attachment_segmentation.md §4.6.4, §4.7):
``wait_and_load_sections``, ``_build_case_document_lists(…, sections_by_id)`` and
the writer's ``_attachment_bodies``.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agents.deep_search_v4 import orchestrator as ds_orch
from agents.models import WorkspaceItemSnapshot
from agents.utils import attachment_sections
from agents.utils.attachment_delivery import Section


def _doc(seq: int, content: str) -> WorkspaceItemSnapshot:
    return WorkspaceItemSnapshot(
        item_id=f"id-{seq}",
        kind="attachment",
        title=f"وثيقة {seq}",
        content_md=content,
        summary="ملخص",
        metadata={"doc_type": "judgment"},
        wi_seq=seq,
    )


SECTIONS = [
    Section(1, "metadata", "بيانات الحكم", "المحكمة العامة"),
    Section(2, "core", "أسباب الحكم", "وحيث إن"),
    Section(3, "supporting", "الوقائع", "ادعى المدعي"),
]


# ---------------------------------------------------------------------------
# _build_case_document_lists
# ---------------------------------------------------------------------------


def test_build_lists_without_sections_is_the_old_path():
    deps = SimpleNamespace(attached_items=[_doc(1, "س" * 20_000)])
    decision = SimpleNamespace(case_full_text_wis=["WI-1"], reg_full_text_wis=[])
    lists, tel = ds_orch._build_case_document_lists(decision, deps)
    assert "[انتهى المعروض: 12000" in lists["case"][0].body
    assert not any(k.startswith("case_docs.segmented") for k in tel)


def test_build_lists_with_sections_is_segmented_for_every_recipient():
    deps = SimpleNamespace(attached_items=[_doc(1, "س" * 20_000)])
    decision = SimpleNamespace(case_full_text_wis=["WI-1"], reg_full_text_wis=[])
    lists, tel = ds_orch._build_case_document_lists(decision, deps, {"id-1": SECTIONS})
    assert "〔أسباب الحكم〕" in lists["case"][0].body
    assert "〔أسباب الحكم〕" in lists["aggregator"][0].body
    assert "〔أسباب الحكم〕" not in lists["reg"][0].body  # not named for reg → summary
    assert tel["case_docs.segmented.case"] == ["WI-1"]
    assert tel["case_docs.trim_factor.aggregator"] == 1.0


def test_build_lists_with_empty_sections_falls_back_within_the_caps():
    deps = SimpleNamespace(attached_items=[_doc(1, "س\n" * 60_000)])
    decision = SimpleNamespace(case_full_text_wis=[], reg_full_text_wis=[])
    lists, tel = ds_orch._build_case_document_lists(decision, deps, {})
    assert tel["case_docs.fallback.aggregator"] == ["WI-1"]
    assert "#### النص الكامل" in lists["aggregator"][0].body
    # 120k document → cut at the 100k per-document cap, back to a line break
    assert 99_000 < tel["case_docs.full_text_chars.aggregator"] <= 100_000
    assert "[انتهى المعروض:" in lists["aggregator"][0].body


# ---------------------------------------------------------------------------
# wait_and_load_sections
# ---------------------------------------------------------------------------


class _Stored:
    def __init__(self, segments):
        self.segments = segments


def _patch_frame(monkeypatch, *, maps, calls):
    import agents.helpers as helpers
    import agents.memory.attachment_segmenter.store as store

    async def fake_ensure_ready(helper, item_ids, **kw):
        calls.append((helper, list(item_ids), kw["consumer"]))
        return {i: SimpleNamespace(status="ready") for i in item_ids}

    monkeypatch.setattr(helpers, "ensure_ready", fake_ensure_ready)
    monkeypatch.setattr(store, "load_segment_maps", lambda sb, ids, user_id=None: maps)


def test_wait_and_load_waits_as_the_consumer_and_converts_rows(monkeypatch):
    calls: list = []
    rows = [SimpleNamespace(seq=1, value="core", what="أسباب", content_md="نص", label="grounds")]
    _patch_frame(monkeypatch, maps={"id-1": _Stored(rows)}, calls=calls)
    out, tel = asyncio.run(
        attachment_sections.wait_and_load_sections(
            ["id-1", "id-1"], consumer="aggregator", supabase=None, user_id="u"
        )
    )
    assert calls == [("segmenter", ["id-1"], "aggregator")]
    assert out["id-1"][0].text == "نص" and out["id-1"][0].value == "core"
    assert tel["segments.loaded"] == ["id-1"]
    assert tel["segments.readiness"] == ["id-1:ready"]


def test_wait_and_load_with_no_ids_does_nothing(monkeypatch):
    calls: list = []
    _patch_frame(monkeypatch, maps={}, calls=calls)
    out, _ = asyncio.run(
        attachment_sections.wait_and_load_sections([], consumer="writer", supabase=None, user_id="u")
    )
    assert out == {} and calls == []


def test_wait_and_load_encodes_for_deep_search(monkeypatch):
    calls: list = []
    rows = [SimpleNamespace(seq=1, value="core", what="محمد", content_md="قال محمد", label="")]
    _patch_frame(monkeypatch, maps={"id-1": _Stored(rows)}, calls=calls)
    import backend.app.services.masking_service as ms

    monkeypatch.setattr(ms, "active_codec", lambda: object())
    monkeypatch.setattr(ms, "encode_active", lambda t: t.replace("محمد", "شخص-1"))
    out, _ = asyncio.run(
        attachment_sections.wait_and_load_sections(
            ["id-1"], consumer="aggregator", supabase=None, user_id="u", encode=True
        )
    )
    assert out["id-1"][0].text == "قال شخص-1" and out["id-1"][0].what == "شخص-1"


def test_wait_and_load_survives_a_failing_wait(monkeypatch):
    import agents.helpers as helpers
    import agents.memory.attachment_segmenter.store as store

    async def boom(*a, **k):
        raise RuntimeError("frame down")

    monkeypatch.setattr(helpers, "ensure_ready", boom)
    rows = [SimpleNamespace(seq=1, value="core", what="أ", content_md="ن", label="")]
    monkeypatch.setattr(store, "load_segment_maps", lambda sb, ids, user_id=None: {"id-1": _Stored(rows)})
    out, _ = asyncio.run(
        attachment_sections.wait_and_load_sections(["id-1"], consumer="writer", supabase=None, user_id="u")
    )
    assert "id-1" in out  # whatever is stored is still read


# ---------------------------------------------------------------------------
# writer — _attachment_bodies
# ---------------------------------------------------------------------------


def _wi(kind: str, content: str) -> dict:
    return {"kind": kind, "content_md": content}


def test_writer_bodies_off_by_default(monkeypatch):
    from agents.writer_planner import walkers
    import agents.utils.attachment_delivery as ad

    monkeypatch.setattr(ad, "delivery_enabled", lambda uid: False)
    deps = SimpleNamespace(user_id="u", supabase=None, emit_sse=None)
    out = asyncio.run(walkers._attachment_bodies(["a"], {"a": _wi("attachment", "س" * 9_000)}, deps))
    assert out == {}


def test_writer_bodies_on_uses_metadata_core_supporting(monkeypatch):
    from agents.writer_planner import walkers
    import agents.utils.attachment_delivery as ad

    monkeypatch.setattr(ad, "delivery_enabled", lambda uid: True)
    seen: dict = {}

    async def fake_wait(ids, **kw):
        seen["ids"], seen["consumer"] = list(ids), kw["consumer"]
        return {"a": SECTIONS}, {}

    monkeypatch.setattr(attachment_sections, "wait_and_load_sections", fake_wait)
    deps = SimpleNamespace(user_id="u", supabase=None, emit_sse=None)
    wi_index = {
        "a": _wi("attachment", "س" * 9_000),
        "b": _wi("attachment", "قصير"),
        "c": _wi("agent_search", "س" * 9_000),
    }
    out = asyncio.run(walkers._attachment_bodies(["a", "b", "c"], wi_index, deps))
    # every selected attachment is asked for (small ones may be in a bundle)
    assert seen == {"ids": ["a", "b"], "consumer": "writer"}
    assert "〔بيانات الحكم〕" in out["a"] and "〔الوقائع〕" in out["a"]
    assert out["b"] == "قصير"
    assert "c" not in out  # non-attachments keep their content_md


def test_writer_keeps_templates_and_prior_drafts_whole(monkeypatch):
    """Review fix 2026-11-12: a template is the skeleton and a prior draft is the text
    being revised — their formulas and signature blocks must not be cut as boilerplate."""
    from agents.writer_planner import walkers
    import agents.utils.attachment_delivery as ad

    monkeypatch.setattr(ad, "delivery_enabled", lambda uid: True)
    seen: dict = {}

    async def fake_wait(ids, **kw):
        seen["ids"] = list(ids)
        return {i: SECTIONS for i in ids}, {}

    monkeypatch.setattr(attachment_sections, "wait_and_load_sections", fake_wait)
    deps = SimpleNamespace(user_id="u", supabase=None, emit_sse=None)
    wi_index = {k: _wi("attachment", "س" * 9_000) for k in ("src", "tpl", "draft", "ref")}
    roles = {"src": "source", "tpl": "template", "draft": "prior_draft", "ref": "reference"}
    out = asyncio.run(walkers._attachment_bodies(list(wi_index), wi_index, deps, roles))
    assert seen["ids"] == ["src", "ref"]
    assert set(out) == {"src", "ref"}  # template and draft keep content_md


@pytest.mark.parametrize("flag", [False, True])
def test_run_retrieval_source_waits_only_when_delivery_is_on(flag):
    """The wait sits before the case-document lists and behind delivery_enabled."""
    import inspect

    src = inspect.getsource(ds_orch.run_retrieval)
    wait_at = src.index("wait_and_load_sections(")
    build_at = src.index("_build_case_document_lists(")
    assert src.index("delivery_enabled(") < wait_at < build_at
    assert 'consumer="aggregator"' in src
