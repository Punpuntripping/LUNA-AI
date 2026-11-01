"""The conversation memory block through the deep search pipeline.

Plan: ``.claude/plans/conversation_memory.md`` §3 (D2), §4.3, §5, §8. What is
pinned here:

* **Transport.** The block is force-forwarded first, whatever the planner lists
  in ``context_labels``, and survives ``_with_case_documents``.
* **Rerankers never see it.** Both reranker nodes are run for real over a bundle
  built by ``_select_forwarded_blocks``.
* **The four renderers** (reg expander, case expander, sector picker, aggregator)
  print it outside ``<context_blocks>`` and do not escape it twice. The case
  expander's ``cap`` block stays last.
* **Planner and responder.** The decider reads it first. The responder reads it
  above ``CONTEXT_HEADER`` and with ``router_context=None``, exactly once.
* **No memory, no change.** Every surface is byte-identical without the block.

CI has no pytest-asyncio, so coroutines go through ``asyncio.run``, and this
file imports no gitignored test helper.
"""
from __future__ import annotations

import asyncio
import html
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.deep_search_v4 import orchestrator as orch
from agents.deep_search_v4.aggregator import prompts as agg_prompts
from agents.deep_search_v4.aggregator.models import AggregatorInput, AggregatorLLMOutput
from agents.deep_search_v4.aggregator.postvalidator import validate_llm_output
from agents.deep_search_v4.case_search import loop as case_loop
from agents.deep_search_v4.case_search import prompts as case_prompts
from agents.deep_search_v4.case_search.models import (
    CaseSearchDeps,
    ChannelCandidate,
    TypedQuery,
)
from agents.deep_search_v4.case_search.models import LoopState as CaseLoopState
from agents.deep_search_v4.case_search.models import RerankerQueryResult as CaseRQR
from agents.deep_search_v4.orchestrator import (
    FullLoopDeps,
    _select_forwarded_blocks,
    _with_case_documents,
)
from agents.deep_search_v4.planner import prompts as planner_prompts
from agents.deep_search_v4.planner.apply import build_retrieval_config
from agents.deep_search_v4.planner.deps import PlannerDeps, build_planner_deps
from agents.deep_search_v4.planner.models import PlannerDecision
from agents.deep_search_v4.planner.runner import _run_unified_responder
from agents.deep_search_v4.reg_compliance_search import loop as reg_loop
from agents.deep_search_v4.reg_compliance_search import prompts as reg_prompts
from agents.deep_search_v4.reg_compliance_search import reranker as reg_reranker
from agents.deep_search_v4.reg_compliance_search.models import (
    LoopState as RegLoopState,
    RegComplianceSearchDeps,
    RerankerQueryResult as RegRQR,
)
from agents.deep_search_v4.sector_picker.prompts import build_sector_picker_user_message
from agents.deep_search_v4.shared.context import ContextBlock
from agents.deep_search_v4.shared.query_cap import render_cap_block
from agents.responder.context import (
    CONTEXT_HEADER,
    MEMORY_NOTE,
    render_context_block,
    render_router_context,
)
from agents.responder.models import DeepSearchArtifact, ResponderInput
from agents.utils import conversation_memory as cm
from agents.utils.conversation_memory import (
    CONVERSATION_MEMORY_LABEL,
    MemoryEntry,
    render_conversation_memory,
)

# Carries XML-significant characters on purpose: the renderer escapes them once,
# and a second pass would turn «&amp;» into «&amp;amp;».
_ENTRY = "المبلغ 210,000 & ليس 310,000 <قراءة خاطئة>"
_ESCAPED_ENTRY = html.escape(_ENTRY, quote=False)
MEMORY_MD = render_conversation_memory(
    [
        MemoryEntry(1, "fact", _ENTRY),
        MemoryEntry(2, "legal_position", "لا يلزم الإنذار", True),
    ]
)
_BRIEF = "موجز المخطط"


async def _embed(_text: str) -> list[float]:
    return [0.0]


def _memory_block() -> ContextBlock:
    return ContextBlock(
        label=CONVERSATION_MEMORY_LABEL, body=MEMORY_MD, persistence="conversation"
    )


def _case_brief_block() -> ContextBlock:
    return ContextBlock(label="case_brief", body="ملخص القضية", persistence="case")


def _document_block() -> ContextBlock:
    return ContextBlock(
        label="case_document", body="وثيقة", persistence="turn", source_item_id="id-1"
    )


def _decision(planner_brief: str = "", context_labels=None):
    return SimpleNamespace(
        planner_brief=planner_brief,
        context_labels=[] if context_labels is None else context_labels,
    )


def _deps(memory: str | None = MEMORY_MD, case_brief: str = ""):
    return SimpleNamespace(
        conversation_memory_md=memory,
        case_brief=case_brief,
        prior_searches=[],
        _flushed_articles=[],
    )


def _labels(blocks) -> list[str]:
    return [b.label for b in blocks]


# ---------------------------------------------------------------------------
# The four renderers, behind one signature
# ---------------------------------------------------------------------------


def _reg(blocks) -> str:
    return reg_prompts.build_expander_user_message("سؤال", "سياق", context_blocks=blocks)


def _case(blocks) -> str:
    return case_prompts.build_expander_user_message("سؤال", "سياق", context_blocks=blocks)


def _picker(blocks) -> str:
    return build_sector_picker_user_message(
        "سؤال", "reg_compliance_led", planner_brief=_BRIEF, context_blocks=blocks
    )


def _aggregator(blocks) -> str:
    agg_input = AggregatorInput(
        original_query="سؤال",
        sub_queries=[],
        detail_level="medium",
        context_blocks=list(blocks or []),
    )
    return agg_prompts.build_aggregator_user_message(agg_input, references=[])


RENDERERS = {
    "reg_expander": _reg,
    "case_expander": _case,
    "sector_picker": _picker,
    "aggregator": _aggregator,
}
every_renderer = pytest.mark.parametrize(
    "render", RENDERERS.values(), ids=RENDERERS.keys()
)


def _memory_part(render) -> str:
    """Exactly what the block adds to a message: itself and its separator."""
    if render is _aggregator:
        return f"{MEMORY_MD}\n{agg_prompts._MEMORY_CONFLICT_NOTE}\n\n"
    return f"{MEMORY_MD}\n\n"


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def test_the_block_is_built_with_its_label_and_persistence():
    blocks = _select_forwarded_blocks(_decision(), _deps(), include_planner_brief=True)
    assert blocks == [_memory_block()]


@pytest.mark.parametrize("labels", [[], ["prior_search_lessons"], ["unknown"]])
@pytest.mark.parametrize("include_planner_brief", [True, False])
def test_forced_even_when_context_labels_omits_it(labels, include_planner_brief):
    blocks = _select_forwarded_blocks(
        _decision(context_labels=labels),
        _deps(),
        include_planner_brief=include_planner_brief,
    )
    assert CONVERSATION_MEMORY_LABEL in _labels(blocks)


def test_forced_without_a_decision():
    """Editorial and replay paths reach here with no decision at all."""
    blocks = _select_forwarded_blocks(None, _deps(), include_planner_brief=True)
    assert _labels(blocks) == [CONVERSATION_MEMORY_LABEL]


def test_it_leads_the_canonical_order():
    blocks = _select_forwarded_blocks(
        _decision(planner_brief=_BRIEF),
        _deps(case_brief="ملخص القضية"),
        include_planner_brief=True,
    )
    assert _labels(blocks) == [CONVERSATION_MEMORY_LABEL, "case_brief", "planner_brief"]


@pytest.mark.parametrize("memory", [None, ""])
def test_no_memory_builds_no_block(memory):
    blocks = _select_forwarded_blocks(
        _decision(), _deps(memory=memory), include_planner_brief=True
    )
    assert blocks == []


def test_a_deps_object_without_the_field_builds_no_block():
    deps = SimpleNamespace(case_brief="", prior_searches=[], _flushed_articles=[])
    assert _select_forwarded_blocks(_decision(), deps, include_planner_brief=True) == []


def test_case_documents_still_land_right_after_case_brief():
    spliced = _with_case_documents(
        [_memory_block(), _case_brief_block()], [_document_block()]
    )
    assert _labels(spliced) == [
        CONVERSATION_MEMORY_LABEL, "case_brief", "case_document",
    ]


@every_renderer
def test_case_documents_at_the_front_do_not_disturb_the_block(render):
    """No ``case_brief``: the document goes ahead of the memory in the list, and
    the message reads the same as with the memory first."""
    spliced = _with_case_documents([_memory_block()], [_document_block()])
    assert _labels(spliced) == ["case_document", CONVERSATION_MEMORY_LABEL]
    assert render(spliced) == render([_memory_block(), _document_block()])
    assert render(spliced).index(MEMORY_MD) < render(spliced).index("<context_blocks>")


def test_run_retrieval_hands_the_block_to_the_picker_and_the_loop(monkeypatch):
    captured: dict = {}

    async def _sector_picker(**kwargs):
        captured["picker"] = list(kwargs.get("context_blocks") or [])

    async def _full_loop(query, query_id, deps, prompt_key="prompt_1"):
        await asyncio.sleep(0)  # let the picker task run
        captured["loop"] = deps
        return SimpleNamespace(references=[], confidence="medium")

    monkeypatch.setattr(orch, "run_sector_picker", _sector_picker)
    monkeypatch.setattr(orch, "run_full_loop", _full_loop)

    decision = PlannerDecision(mode="full", rationale="r", planner_brief=_BRIEF)
    deps = PlannerDeps(
        supabase=object(),  # type: ignore[arg-type]
        embedding_fn=_embed,
        conversation_memory_md=MEMORY_MD,
        _decision=decision,
    )
    asyncio.run(orch.run_retrieval("سؤال", build_retrieval_config(decision), deps))

    assert _labels(captured["picker"]) == [CONVERSATION_MEMORY_LABEL]
    assert _labels(captured["loop"].context_blocks) == [
        CONVERSATION_MEMORY_LABEL, "planner_brief",
    ]


def test_each_call_site_of_the_full_loop_passes_the_block_on(monkeypatch):
    captured: dict = {}

    async def _graph_run(_node, *, state, deps):
        captured["reg"] = state.context_blocks

    async def _case_search(**kwargs):
        captured["case"] = kwargs.get("context_blocks")
        return SimpleNamespace(inner_usage=[], queries_used=[], retrieval={})

    async def _handle_aggregator(agg_input, _deps):
        captured["aggregator"] = agg_input.context_blocks
        return SimpleNamespace(references=[], confidence="medium", model_used=None)

    async def _no_enrich(*_args, **_kwargs):
        return None

    monkeypatch.setattr(orch, "reg_compliance_graph", SimpleNamespace(run=_graph_run))
    monkeypatch.setattr(orch, "create_run_dir", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "save_run_overview_md", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "save_run_json", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "run_case_search", _case_search)
    monkeypatch.setattr(orch, "case_to_rqr", lambda _result: [])
    monkeypatch.setattr(orch, "handle_aggregator_turn", _handle_aggregator)
    monkeypatch.setattr(orch, "enrich_ura", _no_enrich)

    deps = FullLoopDeps(
        supabase=object(),  # type: ignore[arg-type]
        embedding_fn=_embed,
        context_blocks=[_memory_block(), _case_brief_block()],
        case_document_blocks={
            r: [_document_block()] for r in ("reg", "case", "aggregator")
        },
    )
    asyncio.run(orch.run_full_loop("سؤال", query_id=0, deps=deps))

    for recipient, render in (("reg", _reg), ("case", _case), ("aggregator", _aggregator)):
        assert _labels(captured[recipient]) == [
            CONVERSATION_MEMORY_LABEL, "case_brief", "case_document",
        ]
        message = render(captured[recipient])
        assert message.count("<conversation_memory>") == 1
        assert message.index(MEMORY_MD) < message.index("<context_blocks>")


# ---------------------------------------------------------------------------
# Rerankers: the real nodes, over a real bundle
# ---------------------------------------------------------------------------


def _bundle(planner_brief: str) -> list[ContextBlock]:
    """What an executor's ``LoopState.context_blocks`` holds on a real turn."""
    selected = _select_forwarded_blocks(
        _decision(planner_brief=planner_brief),
        _deps(case_brief="ملخص القضية"),
        include_planner_brief=True,
    )
    return _with_case_documents(selected, [_document_block()])


def _run_reg_reranker(blocks, monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def _reranker(**kwargs):
        calls.append(kwargs)
        result = RegRQR(
            query=kwargs["query"], rationale="", sufficient=True, results=[],
            dropped_count=0, summary_note="ملاحظة",
        )
        return result, [], []

    monkeypatch.setattr(reg_reranker, "run_reranker_for_query", _reranker)
    state = RegLoopState(
        focus_instruction="سؤال",
        user_context="",
        round_count=1,
        context_blocks=blocks,
        search_results_log=[
            {"round": 1, "query": "استعلام", "chunks": [{"id": "c"}], "max_score": 0.5}
        ],
    )
    deps = RegComplianceSearchDeps(supabase=object(), embedding_fn=_embed)  # type: ignore[arg-type]
    asyncio.run(reg_loop.RerankerNode().run(SimpleNamespace(state=state, deps=deps)))
    return calls


def _run_case_reranker(blocks, monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def _reranker(**kwargs):
        calls.append(kwargs)
        result = CaseRQR(
            query=kwargs["query"], rationale=kwargs["rationale"], sufficient=True,
            results=[], dropped_count=0, summary_note="ملاحظة",
        )
        return result, [], []

    monkeypatch.setattr(case_loop, "run_reranker_for_query", _reranker)
    monkeypatch.setattr(case_loop, "assemble_kept_cases", AsyncMock(return_value=[]))
    candidate = ChannelCandidate(
        case_id="c1", channel="basis", rank=1, score=0.9,
        row={"court": "المحكمة التجارية", "court_level": "supreme"},
        topics=[{"topic_ref": "T1", "kind": "basis", "text": "سند", "attrs": {},
                 "score": 0.9}],
    )
    state = CaseLoopState(
        focus_instruction="سؤال",
        user_context="",
        context_blocks=blocks,
        per_query_candidates=[
            (TypedQuery(text="استعلام", channel="basis", rationale="ر"), [candidate])
        ],
    )
    deps = CaseSearchDeps(supabase=object(), embedding_fn=_embed)  # type: ignore[arg-type]
    asyncio.run(case_loop.SectionedRerankerNode().run(SimpleNamespace(state=state, deps=deps)))
    return calls


RERANKERS = {
    "reg": (_run_reg_reranker, reg_prompts.build_reranker_user_message),
    "case": (_run_case_reranker, case_prompts.build_reranker_user_message),
}
every_reranker = pytest.mark.parametrize(
    "run_node,build_message", RERANKERS.values(), ids=RERANKERS.keys()
)


@every_reranker
def test_reranker_gets_the_brief_and_never_the_block(run_node, build_message, monkeypatch):
    blocks = _bundle(_BRIEF)
    assert _labels(blocks)[0] == CONVERSATION_MEMORY_LABEL  # the bait is really there

    calls = run_node(blocks, monkeypatch)

    assert calls, "the reranker was never called"
    for call in calls:
        assert call["planner_brief"] == _BRIEF
        dumped = repr(call)
        assert "conversation_memory" not in dumped
        assert "210,000" not in dumped

        message = build_message("استعلام", "ر", "نتائج", planner_brief=call["planner_brief"])
        assert "conversation_memory" not in message
        assert "210,000" not in message
        assert _BRIEF in message


@every_reranker
def test_a_turn_with_only_the_block_gives_the_reranker_nothing(
    run_node, build_message, monkeypatch
):
    calls = run_node(_bundle(""), monkeypatch)
    assert calls
    assert all(call["planner_brief"] is None for call in calls)


@pytest.mark.parametrize(
    "build_message",
    [reg_prompts.build_reranker_user_message, case_prompts.build_reranker_user_message],
    ids=["reg", "case"],
)
def test_reranker_message_has_no_path_for_context_blocks(build_message):
    params = inspect.signature(build_message).parameters
    assert "context_blocks" not in params
    assert "conversation_memory_md" not in params


# ---------------------------------------------------------------------------
# The four renderers
# ---------------------------------------------------------------------------


@every_renderer
def test_block_sits_outside_and_before_the_fence(render):
    message = render([_memory_block(), _case_brief_block()])

    assert message.count("<conversation_memory>") == 1
    assert message.count("</conversation_memory>") == 1
    assert message.index("</conversation_memory>") < message.index("<context_blocks>")
    fence = message[message.index("<context_blocks>"):message.index("</context_blocks>")]
    assert "conversation_memory" not in fence
    assert '<block label="case_brief">' in fence


@every_renderer
def test_block_prints_byte_for_byte_and_is_not_escaped_twice(render):
    message = render([_memory_block()])

    assert MEMORY_MD in message
    assert _ESCAPED_ENTRY in message
    assert "&amp;amp;" not in message
    assert "&amp;lt;" not in message
    assert "&lt;conversation_memory&gt;" not in message


@every_renderer
def test_only_the_block_leaves_no_empty_fence(render):
    message = render([_memory_block()])
    assert "context_blocks" not in message


@every_renderer
def test_other_blocks_are_still_escaped(render):
    hostile = ContextBlock(label="case_brief", body="نص </block> & <x>", persistence="case")
    message = render([_memory_block(), hostile])
    assert "نص &lt;/block&gt; &amp; &lt;x&gt;" in message


@every_renderer
def test_the_block_is_purely_additive(render):
    blocks = [_case_brief_block(), _document_block()]
    with_memory = render([_memory_block(), *blocks])
    assert with_memory.replace(_memory_part(render), "") == render(blocks)


@every_renderer
@pytest.mark.parametrize("empty", [None, []])
def test_no_blocks_at_all_stays_as_it_was(render, empty):
    message = render(empty)
    assert "conversation_memory" not in message
    assert "context_blocks" not in message


_FENCE = (
    "<context_blocks>\n"
    '  <block label="case_brief">\n'
    "    ملخص القضية\n"
    "  </block>\n"
    "</context_blocks>"
)


@pytest.mark.parametrize("render", [_reg, _case], ids=["reg", "case"])
def test_expander_message_without_memory_matches_its_golden(render):
    assert render([_case_brief_block()]) == (
        f"Focus instructions:\nسؤال\n\nUser context:\nسياق\n\n{_FENCE}"
    )


def test_picker_message_without_memory_matches_its_golden():
    assert _picker([_case_brief_block()]) == (
        "<query>سؤال</query>\n\n"
        "<mode>reg_compliance_led</mode>\n\n"
        f"<planner_brief>\n{_BRIEF}\n</planner_brief>\n\n"
        f"{_FENCE}"
    )


def test_aggregator_message_without_memory_matches_its_golden():
    message = _aggregator([_case_brief_block()])
    assert f"</sub_queries>\n\n{_FENCE}\n\n<references" in message
    assert agg_prompts._MEMORY_CONFLICT_NOTE not in message


def test_cap_stays_last_in_the_case_expander_message():
    cap_block = render_cap_block(4)

    message = case_prompts.build_expander_user_message(
        "سؤال", "سياق", context_blocks=[_memory_block(), _case_brief_block()], cap=4
    )
    assert message.endswith(f"\n\n{cap_block}")
    assert message.index(MEMORY_MD) < message.index("</context_blocks>")
    assert message.index("</context_blocks>") < message.index(cap_block)


def test_cap_stays_last_when_the_block_is_alone():
    cap_block = render_cap_block(4)
    message = case_prompts.build_expander_user_message(
        "سؤال", "سياق", context_blocks=[_memory_block()], cap=4
    )
    assert message.endswith(f"{MEMORY_MD}\n\n{cap_block}")


def test_aggregator_prints_the_block_before_context_and_references():
    message = _aggregator([_memory_block(), _case_brief_block()])
    assert (
        message.index("</sub_queries>")
        < message.index(MEMORY_MD)
        < message.index("<context_blocks>")
        < message.index('<references count="')
    )


# ---------------------------------------------------------------------------
# The aggregator's static bullet, conflict note and post-validator
# ---------------------------------------------------------------------------

_STATIC_BULLET = (
    "- **`<conversation_memory>` is the exception to the rule against content "
    "outside the references.** Its facts are the facts of the matter, stated by "
    "the user: use them even though no reference contains them, and where one "
    "disagrees with a `case_document`, the memory wins. Follow a legal position "
    "marked as insisted in the same way. Never cite either with `[n]`.\n"
)


def test_static_bullet_is_exact_and_closes_the_case_document_rules():
    role = agg_prompts._SHARED_ROLE_AR
    assert role.count(_STATIC_BULLET) == 1
    assert role.endswith(
        "say so in the gaps section.\n" + _STATIC_BULLET
    )


@pytest.mark.parametrize(
    "key", ["prompt_mode_case", "prompt_mode_reg_compliance", "prompt_mode_full"]
)
def test_every_mode_prompt_carries_the_static_bullet(key):
    assert agg_prompts.get_aggregator_prompt(key).count(_STATIC_BULLET) == 1


def test_conflict_note_names_the_legal_label_and_not_the_insisted_one():
    """Its label must match the rendered entries byte for byte, and an insisted
    entry must not match it, or the aggregator would be told to warn again."""
    label = cm._KIND_LABELS[cm.KIND_LEGAL_POSITION]
    assert f"[{label}]" in agg_prompts._MEMORY_CONFLICT_NOTE
    assert f"[{cm._INSISTED_LABEL}]" not in agg_prompts._MEMORY_CONFLICT_NOTE
    assert f"[{label}]" not in f"[{cm._INSISTED_LABEL}]"


def test_an_uncited_sentence_passes_the_post_validator():
    """An insisted point has no citation by rule, so nothing may flag it."""
    output = AggregatorLLMOutput(
        synthesis_md="## الخلاصة\nيبنى الجواب على المبلغ 210,000 ريال ولا يلزم الإنذار.",
        used_refs=[],
        gaps=[],
        confidence="medium",
    )
    agg_input = AggregatorInput(original_query="سؤال", sub_queries=[])

    report = validate_llm_output(
        output, [], agg_input, {}, "prompt_mode_reg_compliance"
    )

    assert report.passed
    assert report.dangling_citations == []
    assert report.ungrounded_snippets == []


# ---------------------------------------------------------------------------
# Planner: deps, decider, old responder
# ---------------------------------------------------------------------------


def _planner_deps(**over) -> PlannerDeps:
    base = dict(supabase=object(), embedding_fn=_embed)
    base.update(over)
    return build_planner_deps(**base)


def _message():
    return SimpleNamespace(role="user", content="رسالة", created_at="2026-11-01")


def _prior_search():
    return SimpleNamespace(
        wi_seq=3, title="بحث", describe_query="سؤال", confidence="high", summary="خلاصة"
    )


def test_deps_default_to_no_memory():
    assert _planner_deps().conversation_memory_md is None
    assert _planner_deps(conversation_memory_md="").conversation_memory_md is None


def test_builder_keeps_the_string_as_it_came():
    assert _planner_deps(conversation_memory_md=MEMORY_MD).conversation_memory_md is MEMORY_MD


def test_decider_reads_the_block_first():
    rendered = planner_prompts.build_decider_instructions(
        _planner_deps(
            conversation_memory_md=MEMORY_MD,
            case_brief="ملخص",
            compaction_summary_md="ما سبق",
            recent_messages=[_message()],
        )
    )
    assert rendered.startswith(
        f"{planner_prompts._DECIDER_CONTEXT_HEADER}\n\n{MEMORY_MD}\n"
    )
    assert rendered.index("</conversation_memory>") < rendered.index("<case_brief>")
    assert rendered.count("<conversation_memory>") == 1


def test_decider_does_not_escape_the_block_twice():
    rendered = planner_prompts.build_decider_instructions(
        _planner_deps(conversation_memory_md=MEMORY_MD)
    )
    assert _ESCAPED_ENTRY in rendered
    assert "&amp;amp;" not in rendered


def test_the_block_alone_adds_no_brief_rules_and_no_empty_stub():
    rendered = planner_prompts.build_decider_instructions(
        _planner_deps(conversation_memory_md=MEMORY_MD)
    )
    assert planner_prompts._PLANNER_BRIEF_DETAIL_RULES not in rendered
    assert planner_prompts._CASE_DOCUMENTS_RULES not in rendered
    assert "لا سياقَ" not in rendered


def test_a_real_brief_source_still_adds_the_rules_beside_the_block():
    rendered = planner_prompts.build_decider_instructions(
        _planner_deps(conversation_memory_md=MEMORY_MD, prior_searches=[_prior_search()])
    )
    assert planner_prompts._PLANNER_BRIEF_DETAIL_RULES in rendered


def test_decider_without_memory_is_byte_identical():
    context = dict(
        case_brief="ملخص",
        compaction_summary_md="ما سبق",
        recent_messages=[_message()],
        prior_searches=[_prior_search()],
    )
    with_memory = planner_prompts.build_decider_instructions(
        _planner_deps(conversation_memory_md=MEMORY_MD, **context)
    )
    without = planner_prompts.build_decider_instructions(_planner_deps(**context))

    added = f"{MEMORY_MD}\n{planner_prompts._DECIDER_MEMORY_NOTE}\n\n"
    assert with_memory.replace(added, "") == without
    assert "conversation_memory" not in without
    assert planner_prompts._DECIDER_MEMORY_NOTE not in without


def test_decider_with_nothing_at_all_keeps_its_stub():
    rendered = planner_prompts.build_decider_instructions(_planner_deps())
    assert "لا سياقَ" in rendered


def test_old_responder_context_puts_the_block_above_its_header():
    rendered = planner_prompts.render_responder_context(
        _planner_deps(conversation_memory_md=MEMORY_MD, case_brief="ملخص")
    )
    assert rendered.startswith(
        f"{MEMORY_MD}\n\n{planner_prompts._RESPONDER_CONTEXT_HEADER}"
    )


def test_old_responder_context_with_only_the_block():
    deps = _planner_deps(conversation_memory_md=MEMORY_MD)
    assert planner_prompts.render_responder_context(deps) == f"{MEMORY_MD}\n\n"


def test_old_responder_context_can_leave_the_block_out():
    deps = _planner_deps(conversation_memory_md=MEMORY_MD, case_brief="ملخص")
    without = planner_prompts.render_responder_context(deps, include_memory=False)
    assert "conversation_memory" not in without
    assert without == planner_prompts.render_responder_context(
        _planner_deps(case_brief="ملخص")
    )


def test_old_responder_context_without_memory_is_empty_when_nothing_else_is_there():
    assert planner_prompts.render_responder_context(_planner_deps()) == ""


@pytest.mark.parametrize("agg_output", [None, "ready"], ids=["degraded", "normal"])
def test_old_responder_instructions_carry_the_block(agg_output):
    deps = _planner_deps(conversation_memory_md=MEMORY_MD)
    if agg_output:
        deps._agg_output = SimpleNamespace(
            references=[], gaps=[], synthesis_md="جواب", confidence="medium"
        )
    deps._decision = SimpleNamespace(mode="full", planner_brief="")

    rendered = planner_prompts.build_responder_instructions(deps)

    assert rendered.count("<conversation_memory>") == 1
    assert rendered.startswith(MEMORY_MD)


# ---------------------------------------------------------------------------
# Unified responder wiring
# ---------------------------------------------------------------------------


def _capture_responder_input(deps, monkeypatch) -> ResponderInput:
    captured: dict = {}

    async def _run_responder(inp, model_override=None):
        captured["inp"] = inp

    monkeypatch.setattr("agents.responder.run_responder", _run_responder)
    monkeypatch.setattr(
        "agents.responder.wiring.load_profession_group", lambda *_a: None
    )
    monkeypatch.setattr("agents.responder.wiring.load_style_profile", lambda *_a: None)

    agg_output = SimpleNamespace(
        synthesis_md="جواب", confidence="medium", gaps=[], key_findings=[], references=[]
    )
    decision = SimpleNamespace(mode="full", planner_brief="")
    asyncio.run(_run_unified_responder("سؤال", deps, agg_output, decision))
    return captured["inp"]


def _router_ctx():
    return SimpleNamespace(
        user_call_name=None,
        user_preferences=None,
        case_memory_md="ملف القضية",
        compaction_summary_md=None,
    )


def test_unified_runner_sets_the_field(monkeypatch):
    inp = _capture_responder_input(
        _planner_deps(conversation_memory_md=MEMORY_MD, router_context=_router_ctx()),
        monkeypatch,
    )
    assert inp.conversation_memory_md is MEMORY_MD


def test_on_resume_the_block_renders_once_not_twice(monkeypatch):
    """The fallback and the field both come from ``deps``, so a resume leg would
    print the block twice if the fallback kept its copy."""
    inp = _capture_responder_input(
        _planner_deps(conversation_memory_md=MEMORY_MD, case_brief="ملخص"), monkeypatch
    )
    assert inp.router_context is None
    assert "conversation_memory" not in inp.fallback_context

    rendered = render_context_block(inp)
    assert rendered.count("<conversation_memory>") == 1
    assert rendered.startswith(MEMORY_MD)
    assert "<case_brief>" in rendered


def test_unified_runner_without_memory(monkeypatch):
    inp = _capture_responder_input(_planner_deps(case_brief="ملخص"), monkeypatch)
    assert inp.conversation_memory_md is None
    assert inp.fallback_context == planner_prompts.render_responder_context(
        _planner_deps(case_brief="ملخص")
    )


# ---------------------------------------------------------------------------
# Responder context
# ---------------------------------------------------------------------------


def _responder_input(**over) -> ResponderInput:
    base = dict(
        mode="deep_search",
        artifact=DeepSearchArtifact(query="سؤال", synthesis_md="جواب"),
    )
    base.update(over)
    return ResponderInput(**base)


def test_block_stays_above_the_header_that_forbids_instructions():
    rendered = render_context_block(
        _responder_input(router_context=_router_ctx(), conversation_memory_md=MEMORY_MD)
    )
    assert rendered.startswith(MEMORY_MD)
    assert rendered.index("</conversation_memory>") < rendered.index(CONTEXT_HEADER)
    header_on = rendered[rendered.index(CONTEXT_HEADER):]
    assert "conversation_memory" not in header_on


def test_resume_with_a_fallback_gets_the_block():
    rendered = render_context_block(
        _responder_input(
            router_context=None,
            fallback_context="## سياق ضيق\nسطر\n",
            conversation_memory_md=MEMORY_MD,
        )
    )
    assert rendered == f"{MEMORY_MD}\n{MEMORY_NOTE}\n\n## سياق ضيق\nسطر"


def test_resume_with_nothing_else_still_gets_the_block():
    rendered = render_context_block(
        _responder_input(router_context=None, conversation_memory_md=MEMORY_MD)
    )
    assert rendered == f"{MEMORY_MD}\n{MEMORY_NOTE}"


def test_the_block_is_not_escaped_again():
    rendered = render_context_block(_responder_input(conversation_memory_md=MEMORY_MD))
    assert _ESCAPED_ENTRY in rendered
    assert "&amp;amp;" not in rendered


@pytest.mark.parametrize("memory", [None, ""])
def test_no_memory_leaves_the_router_branch_byte_identical(memory):
    ctx = _router_ctx()
    rendered = render_context_block(
        _responder_input(router_context=ctx, conversation_memory_md=memory)
    )
    assert rendered == render_router_context(ctx)
    assert MEMORY_NOTE not in rendered


@pytest.mark.parametrize("memory", [None, ""])
def test_no_memory_leaves_the_fallback_branch_byte_identical(memory):
    assert (
        render_context_block(
            _responder_input(fallback_context=" ## سياق\n", conversation_memory_md=memory)
        )
        == "## سياق"
    )
    assert render_context_block(_responder_input(conversation_memory_md=memory)) == ""
