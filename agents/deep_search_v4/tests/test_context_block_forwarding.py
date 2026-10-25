"""Unit tests for ``orchestrator._select_forwarded_blocks``.

Locks the gate-hardening fix: ``case_brief`` and ``planner_brief`` are
*force-forwarded* downstream whenever non-empty — even when the planner forgot
to list them in ``context_labels`` — while ``prior_search_lessons`` stays
opt-in. Regression scar: convo ccd1afea, where the planner wrote a full
``planner_brief`` (three fetched articles) but emitted
``context_labels=["prior_search_lessons"]``, so the brief reached neither the
expander nor the aggregator.

The sector_picker path (``include_planner_brief=False``) must EXCLUDE
``planner_brief`` from the block list, because the picker renders it via its own
dedicated ``<planner_brief>`` tag and would otherwise double-render it.

``case_document`` (case_documents_forwarding.md §3.2) — the uploaded case papers
travel on per-recipient lists spliced in right after ``case_brief`` at the three
call sites (reg expander, case expander, aggregator); the sector_picker never
sees them, and a turn without one renders exactly what it did before.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.deep_search_v4 import orchestrator as orch
from agents.deep_search_v4.orchestrator import (
    FullLoopDeps,
    _build_case_document_lists,
    _select_forwarded_blocks,
    _with_case_documents,
)
from agents.deep_search_v4.shared.context import ContextBlock
from agents.models import WorkspaceItemSnapshot


def _decision(planner_brief: str = "", context_labels=None):
    return SimpleNamespace(
        planner_brief=planner_brief,
        context_labels=[] if context_labels is None else context_labels,
    )


def _deps(case_brief: str = "", prior_searches=None, flushed_articles=None):
    return SimpleNamespace(
        case_brief=case_brief,
        prior_searches=prior_searches or [],
        # The surviving snapshot flush_statute_package leaves behind — the source
        # of the statute_articles block. Empty by default: most turns fetch no
        # article and must emit no block.
        _flushed_articles=flushed_articles or [],
    )


def _prior_search():
    return SimpleNamespace(
        title="بحث سابق",
        describe_query="سؤال",
        confidence="high",
        summary="خلاصة",
    )


def _labels(blocks):
    return [b.label for b in blocks]


def test_forgotten_planner_brief_label_still_forwards():
    """The bug: non-empty planner_brief + label omitted → must still forward."""
    decision = _decision(planner_brief="نص المادة...", context_labels=["prior_search_lessons"])
    deps = _deps()

    blocks = _select_forwarded_blocks(decision, deps, include_planner_brief=True)

    assert "planner_brief" in _labels(blocks)


def test_sector_picker_path_excludes_planner_brief():
    """include_planner_brief=False → planner_brief NOT in the block list.

    The picker renders planner_brief via its own <planner_brief> tag, so
    including it here would double-render it.
    """
    decision = _decision(planner_brief="نص المادة...", context_labels=[])
    deps = _deps(case_brief="ملخص القضية")

    blocks = _select_forwarded_blocks(decision, deps, include_planner_brief=False)

    labels = _labels(blocks)
    assert "planner_brief" not in labels
    # case_brief is still force-forwarded on the picker path.
    assert "case_brief" in labels


def test_case_brief_force_forwarded_without_declaration():
    decision = _decision(planner_brief="", context_labels=[])
    deps = _deps(case_brief="ملخص القضية")

    for include in (True, False):
        blocks = _select_forwarded_blocks(decision, deps, include_planner_brief=include)
        assert "case_brief" in _labels(blocks)


def test_prior_search_lessons_stays_opt_in():
    deps = _deps(prior_searches=[_prior_search()])

    # Not declared → not forwarded.
    undeclared = _select_forwarded_blocks(
        _decision(context_labels=[]), deps, include_planner_brief=True
    )
    assert "prior_search_lessons" not in _labels(undeclared)

    # Declared → forwarded.
    declared = _select_forwarded_blocks(
        _decision(context_labels=["prior_search_lessons"]), deps, include_planner_brief=True
    )
    assert "prior_search_lessons" in _labels(declared)


def test_empty_planner_brief_produces_no_block():
    decision = _decision(planner_brief="   ", context_labels=["planner_brief"])
    deps = _deps()

    blocks = _select_forwarded_blocks(decision, deps, include_planner_brief=True)

    assert "planner_brief" not in _labels(blocks)


def test_canonical_order_preserved():
    decision = _decision(
        planner_brief="موجز",
        context_labels=["prior_search_lessons"],
    )
    deps = _deps(case_brief="ملخص القضية", prior_searches=[_prior_search()])

    blocks = _select_forwarded_blocks(decision, deps, include_planner_brief=True)

    assert _labels(blocks) == ["case_brief", "planner_brief", "prior_search_lessons"]


# ===========================================================================
# statute_articles — the structural channel for fetched article text
# ===========================================================================


ART_31 = (
    "تختص المحاكم العامة بنظر جميع الدعاوى والقضايا الخارجة عن اختصاص المحاكم "
    "الأخرى، ولها بوجه خاص النظر في الآتي: أ- الدعاوى المتعلقة بالعقار."
)
ART_22 = "1- ينفسخ العقد إذا هلك الأصل المؤجر هلاكًا كليًّا."


def _articles():
    """The two articles the attached judgment in convo 631a69af reasons from."""
    return [
        {"regulation": "نظام المرافعات الشرعية", "article_number": "31",
         "content": ART_31, "confidence": "high"},
        {"regulation": "نظام الايجار التمويلي", "article_number": "22",
         "content": ART_22, "confidence": "high"},
    ]


def test_statute_articles_forwards_without_any_label():
    """Force-forwarded, like the two briefs — and for a stronger reason.

    Since the decider prompt stopped asking the planner to retype article bodies
    into planner_brief, this block is the ONLY path those bodies have downstream.
    A forgotten label here would not degrade the context, it would empty it.
    """
    blocks = _select_forwarded_blocks(
        _decision(context_labels=[]),
        _deps(flushed_articles=_articles()),
        include_planner_brief=True,
    )
    assert "statute_articles" in _labels(blocks)


def test_statute_articles_carries_both_bodies_verbatim():
    """The executors must receive the article text in full, not a summary."""
    blocks = _select_forwarded_blocks(
        _decision(), _deps(flushed_articles=_articles()), include_planner_brief=True
    )
    body = next(b.body for b in blocks if b.label == "statute_articles")
    assert ART_31 in body
    assert ART_22 in body
    # Rendered by the same builder that writes the pinned workspace item.
    assert "نص المادة 31 من نظام المرافعات الشرعية" in body
    assert "نص المادة 22 من نظام الايجار التمويلي" in body


def test_statute_articles_absent_when_nothing_was_fetched():
    """The common turn fetches no article and must pay for no block."""
    blocks = _select_forwarded_blocks(
        _decision(planner_brief="نص"), _deps(), include_planner_brief=True
    )
    assert "statute_articles" not in _labels(blocks)


def test_statute_articles_survives_an_empty_planner_brief():
    """The decoupling, stated as a test.

    The planner is no longer asked to quote articles, so a turn can legitimately
    fetch two articles and write NO brief at all. Under the old design that
    combination meant the text reached nobody; it must now reach everybody.
    """
    blocks = _select_forwarded_blocks(
        _decision(planner_brief=""),
        _deps(flushed_articles=_articles()),
        include_planner_brief=True,
    )
    labels = _labels(blocks)
    assert "planner_brief" not in labels
    assert "statute_articles" in labels


def test_statute_articles_precedes_planner_brief():
    """Source before gloss: the verbatim rule, then the planner's framing."""
    blocks = _select_forwarded_blocks(
        _decision(planner_brief="من الحكم المرفق: ..."),
        _deps(case_brief="ملخص القضية", flushed_articles=_articles()),
        include_planner_brief=True,
    )
    labels = _labels(blocks)
    assert labels.index("statute_articles") < labels.index("planner_brief")
    assert labels.index("case_brief") < labels.index("statute_articles")


def test_statute_articles_also_reaches_the_sector_picker():
    """include_planner_brief=False suppresses only planner_brief.

    The picker renders planner_brief through its own tag, which is why that one
    is excluded; statute_articles has no such dedicated tag, so withholding it
    would simply hide the article text from the sector decision.
    """
    blocks = _select_forwarded_blocks(
        _decision(planner_brief="نص"),
        _deps(flushed_articles=_articles()),
        include_planner_brief=False,
    )
    labels = _labels(blocks)
    assert "planner_brief" not in labels
    assert "statute_articles" in labels


# ===========================================================================
# case_document — per-recipient lists (case_documents_forwarding.md §3.2)
# ===========================================================================


def _att(seq: int, doc_type: str | None = "judgment", *, kind: str = "attachment",
         content: str = "", summary: str = "") -> WorkspaceItemSnapshot:
    return WorkspaceItemSnapshot(
        item_id=f"id-{seq}",
        kind=kind,
        title=f"وثيقة {seq}",
        content_md=content or f"النص الكامل للوثيقة {seq}",
        summary=summary or f"ملخص الوثيقة {seq}",
        metadata={"doc_type": doc_type} if doc_type else {},
        wi_seq=seq,
    )


def _papers():
    """A judgment, a contract and a memo — plus a chat that never travels."""
    return [_att(1, "judgment"), _att(2, "contract"), _att(3, "memo"), _att(4, "chat")]


def _doc_deps(attached=None, *, case_brief: str = ""):
    deps = _deps(case_brief=case_brief)
    deps.attached_items = list(attached or [])
    return deps


def _wis_decision(case=(), reg=(), planner_brief: str = ""):
    return SimpleNamespace(
        planner_brief=planner_brief,
        context_labels=[],
        case_full_text_wis=list(case),
        reg_full_text_wis=list(reg),
    )


def _full_text_of(blocks) -> list[str]:
    """source_item_ids of the blocks that carry their document's full text."""
    return [
        b.source_item_id for b in blocks
        if "#### النص الكامل" in b.body and "النص الكامل للوثيقة" in b.body
    ]


def test_case_documents_lists_differ_per_recipient():
    decision = _wis_decision(case=["WI-1"], reg=["WI-2"])
    lists, telemetry = _build_case_document_lists(decision, _doc_deps(_papers()))

    assert set(lists) == {"case", "reg", "aggregator"}
    # Every recipient gets every forwarded document — never the chat.
    for blocks in lists.values():
        assert sorted(b.source_item_id for b in blocks) == ["id-1", "id-2", "id-3"]
        assert all(b.label == "case_document" for b in blocks)
    # …but the full text differs: each expander only what the planner named
    # for it, the aggregator everything (planner-named first).
    assert _full_text_of(lists["case"]) == ["id-1"]
    assert _full_text_of(lists["reg"]) == ["id-2"]
    assert _full_text_of(lists["aggregator"]) == ["id-1", "id-2", "id-3"]
    assert [b.source_item_id for b in lists["case"]] == ["id-1", "id-2", "id-3"]
    assert [b.source_item_id for b in lists["reg"]] == ["id-2", "id-1", "id-3"]

    assert telemetry["case_docs.count"] == 3
    assert telemetry["case_docs.full_text.case"] == ["WI-1"]
    assert telemetry["case_docs.full_text.reg"] == ["WI-2"]
    assert telemetry["case_docs.full_text.aggregator"] == ["WI-1", "WI-2", "WI-3"]
    assert telemetry["case_docs.truncated"] == []
    assert telemetry["case_docs.over_total"] == []


def test_alias_of_a_non_forwarded_item_buys_no_full_text():
    decision = _wis_decision(case=["WI-4", "WI-99"])  # the chat + a ghost
    lists, _ = _build_case_document_lists(decision, _doc_deps(_papers()))
    assert _full_text_of(lists["case"]) == []


def test_no_decision_means_summaries_for_expanders_full_for_aggregator():
    """Editorial / replay paths reach run_retrieval with ``_decision=None``."""
    lists, _ = _build_case_document_lists(None, _doc_deps(_papers()))
    assert _full_text_of(lists["case"]) == []
    assert _full_text_of(lists["reg"]) == []
    assert _full_text_of(lists["aggregator"]) == ["id-1", "id-2", "id-3"]


def test_aggregator_cap_overflow_is_reported_per_recipient():
    big = "ن" * 20_000
    docs = [_att(1, content=big), _att(2, content=big), _att(3, content=big)]
    _, telemetry = _build_case_document_lists(None, _doc_deps(docs))
    assert telemetry["case_docs.full_text.aggregator"] == ["WI-1", "WI-2"]
    assert telemetry["case_docs.over_total"] == ["aggregator:WI-3"]
    assert telemetry["case_docs.truncated"] == ["WI-1", "WI-2"]
    assert telemetry["case_docs.full_text_chars.aggregator"] == 24_000
    assert telemetry["case_docs.full_text_chars.case"] == 0


@pytest.mark.parametrize(
    "attached",
    [[], [_att(4, "chat"), _att(5, "reference")], [_att(6, kind="agent_search")]],
)
def test_no_forwardable_document_builds_nothing(attached):
    lists, telemetry = _build_case_document_lists(
        _wis_decision(case=["WI-4"]), _doc_deps(attached)
    )
    assert lists == {}
    assert telemetry == {}


def _blk(label: str) -> ContextBlock:
    return ContextBlock(label=label, body=label, persistence="turn")


def test_case_documents_are_inserted_right_after_case_brief():
    base = [_blk("case_brief"), _blk("statute_articles"), _blk("planner_brief"),
            _blk("prior_search_lessons")]
    docs = [_blk("case_document"), _blk("case_document")]
    assert _labels(_with_case_documents(base, docs)) == [
        "case_brief", "case_document", "case_document", "statute_articles",
        "planner_brief", "prior_search_lessons",
    ]


def test_case_documents_lead_when_there_is_no_case_brief():
    base = [_blk("planner_brief")]
    assert _labels(_with_case_documents(base, [_blk("case_document")])) == [
        "case_document", "planner_brief",
    ]
    assert _labels(_with_case_documents([], [_blk("case_document")])) == ["case_document"]


def test_no_case_documents_leaves_the_list_as_it_was():
    base = [_blk("case_brief"), _blk("planner_brief")]
    for extra in (None, []):
        out = _with_case_documents(base, extra)
        assert out == base
        assert out is not base  # always a fresh list — the shared bundle stays put
    assert _with_case_documents([], None) == []
    assert _with_case_documents(None, None) == []


def test_sector_picker_block_list_never_carries_case_documents():
    deps = _doc_deps(_papers(), case_brief="ملخص القضية")
    decision = _wis_decision(case=["WI-1"], planner_brief="موجز")
    for include in (True, False):
        blocks = _select_forwarded_blocks(decision, deps, include_planner_brief=include)
        assert "case_document" not in _labels(blocks)


# ---------------------------------------------------------------------------
# run_retrieval wiring — FullLoopDeps carries the lists; the picker gets none
# ---------------------------------------------------------------------------


async def _embed(_q: str) -> list[float]:  # pragma: no cover - never invoked
    return []


def _planner_deps(attached, decision, *, case_brief: str = "ملخص القضية"):
    from agents.deep_search_v4.planner.deps import PlannerDeps

    return PlannerDeps(
        supabase=object(),  # type: ignore[arg-type]
        embedding_fn=_embed,
        case_brief=case_brief,
        attached_items=list(attached),
        _decision=decision,
    )


def _config(decision):
    from agents.deep_search_v4.planner.apply import build_retrieval_config

    return build_retrieval_config(decision)


@pytest.fixture
def retrieval_capture(monkeypatch):
    """Run ``run_retrieval`` with the sector_picker and the full loop stubbed."""
    captured: dict = {}

    async def _picker(**kwargs):
        captured["picker_blocks"] = list(kwargs.get("context_blocks") or [])
        return None

    async def _full_loop(query, query_id, deps, prompt_key="prompt_1"):
        # Let the picker task run before the loop "finishes".
        await orch.asyncio.sleep(0)
        captured["full_deps"] = deps
        return SimpleNamespace(references=[], confidence="medium")

    monkeypatch.setattr(orch, "run_sector_picker", _picker)
    monkeypatch.setattr(orch, "run_full_loop", _full_loop)
    return captured


@pytest.mark.asyncio
async def test_run_retrieval_threads_case_documents_but_not_to_the_picker(retrieval_capture):
    from agents.deep_search_v4.planner.models import PlannerDecision

    decision = PlannerDecision(
        mode="full", rationale="r", planner_brief="موجز",
        case_full_text_wis=["WI-1"], reg_full_text_wis=["WI-2"],
    )
    deps = _planner_deps(_papers(), decision)
    await orch.run_retrieval("سؤال", _config(decision), deps)

    full_deps = retrieval_capture["full_deps"]
    assert set(full_deps.case_document_blocks) == {"case", "reg", "aggregator"}
    assert _full_text_of(full_deps.case_document_blocks["case"]) == ["id-1"]
    assert _full_text_of(full_deps.case_document_blocks["reg"]) == ["id-2"]
    # The shared bundle is untouched — documents are spliced per recipient.
    assert "case_document" not in _labels(full_deps.context_blocks)
    assert full_deps._case_docs_telemetry["case_docs.count"] == 3
    # The sector_picker sees the bundle without any case document.
    assert "case_brief" in _labels(retrieval_capture["picker_blocks"])
    assert "case_document" not in _labels(retrieval_capture["picker_blocks"])


@pytest.mark.asyncio
async def test_run_retrieval_without_attachments_is_unchanged(retrieval_capture):
    from agents.deep_search_v4.planner.models import PlannerDecision

    decision = PlannerDecision(mode="full", rationale="r", planner_brief="موجز")
    deps = _planner_deps([], decision)
    await orch.run_retrieval("سؤال", _config(decision), deps)

    full_deps = retrieval_capture["full_deps"]
    assert full_deps.case_document_blocks == {}
    assert full_deps._case_docs_telemetry == {}
    assert full_deps.context_blocks == _select_forwarded_blocks(
        decision, deps, include_planner_brief=True
    )


# ---------------------------------------------------------------------------
# The three call sites — what each expander and the aggregator actually receive
# ---------------------------------------------------------------------------


def _loop_deps(*, context_blocks, case_document_blocks=None) -> FullLoopDeps:
    return FullLoopDeps(
        supabase=object(),  # type: ignore[arg-type]
        embedding_fn=_embed,
        context_blocks=list(context_blocks),
        case_document_blocks=dict(case_document_blocks or {}),
    )


@pytest.fixture
def phase_capture(monkeypatch):
    """Stub the executors + aggregator just below the orchestrator's call sites."""
    captured: dict = {}

    async def _graph_run(_node, *, state, deps):
        captured["reg"] = state.context_blocks

    async def _case_search(**kwargs):
        captured["case"] = kwargs.get("context_blocks")
        return SimpleNamespace(inner_usage=[], queries_used=[], retrieval={})

    async def _agg(agg_input, _agg_deps):
        captured["aggregator"] = agg_input.context_blocks
        return SimpleNamespace(references=[], confidence="medium", model_used=None)

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(orch, "reg_compliance_graph", SimpleNamespace(run=_graph_run))
    monkeypatch.setattr(orch, "create_run_dir", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "save_run_overview_md", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "save_run_json", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "run_case_search", _case_search)
    monkeypatch.setattr(orch, "case_to_rqr", lambda _r: [])
    monkeypatch.setattr(orch, "handle_aggregator_turn", _agg)
    monkeypatch.setattr(orch, "enrich_ura", _noop)
    return captured


def _doc_lists():
    """Distinct per-recipient lists, so a mix-up between call sites shows."""
    return {
        r: [ContextBlock(label="case_document", body=f"وثيقة {r}",
                         persistence="turn", source_item_id=f"id-{r}")]
        for r in ("case", "reg", "aggregator")
    }


@pytest.mark.asyncio
async def test_each_call_site_gets_its_own_documents_after_case_brief(phase_capture):
    shared = [_blk("case_brief"), _blk("planner_brief")]
    deps = _loop_deps(context_blocks=shared, case_document_blocks=_doc_lists())

    await orch.run_full_loop("س", query_id=0, deps=deps)

    for recipient in ("case", "reg", "aggregator"):
        got = phase_capture[recipient]
        assert _labels(got) == ["case_brief", "case_document", "planner_brief"]
        assert got[1].source_item_id == f"id-{recipient}"
    # The shared bundle itself was never mutated.
    assert _labels(deps.context_blocks) == ["case_brief", "planner_brief"]


@pytest.mark.asyncio
async def test_no_case_documents_call_sites_receive_exactly_what_they_did(phase_capture):
    """No attachment → the same values the pre-change call sites passed."""
    shared = [_blk("case_brief"), _blk("planner_brief")]
    await orch.run_full_loop("س", query_id=0, deps=_loop_deps(context_blocks=shared))
    assert phase_capture["reg"] == shared
    assert phase_capture["case"] == shared
    assert phase_capture["aggregator"] == shared


@pytest.mark.asyncio
async def test_no_blocks_at_all_keeps_the_empty_shapes(phase_capture):
    """Reg got ``[]``, case got ``None``, the aggregator kept its default ``[]``."""
    await orch.run_full_loop("س", query_id=0, deps=_loop_deps(context_blocks=[]))
    assert phase_capture["reg"] == []
    assert phase_capture["case"] is None
    assert phase_capture["aggregator"] == []


def test_no_case_documents_expander_messages_are_byte_identical():
    """Rendered through the real builders: splicing nothing changes nothing."""
    from agents.deep_search_v4.case_search.prompts import (
        build_expander_user_message as case_msg,
    )
    from agents.deep_search_v4.reg_compliance_search.prompts import (
        build_expander_user_message as reg_msg,
    )

    shared = [
        ContextBlock(label="case_brief", body="ملخص القضية", persistence="case"),
        ContextBlock(label="planner_brief", body="موجز", persistence="turn"),
    ]
    spliced = _with_case_documents(shared, [])
    assert case_msg("س", "", context_blocks=spliced) == case_msg("س", "", context_blocks=shared)
    assert reg_msg("س", "", context_blocks=spliced) == reg_msg("س", "", context_blocks=shared)
