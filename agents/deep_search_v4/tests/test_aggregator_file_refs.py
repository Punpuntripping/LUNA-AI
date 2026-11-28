"""The aggregator's file refs — the ready tags on its documents' sections, the flagged
prompt, and resolving the [F1·S7] / [F1] tags it writes (aggregator_file_refs.md
§4.1–§4.4, D11–D12: F + the file's fixed WI number + the section number).

No network, no model, no ``async def`` tests (CI has no pytest-asyncio). All text
invented.
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from agents.deep_search_v4 import orchestrator as ds_orch
from agents.deep_search_v4.aggregator import prompts as agg_prompts
from agents.deep_search_v4.aggregator import runner as agg_runner
from agents.deep_search_v4.aggregator.artifact_builder import build_artifact
from agents.deep_search_v4.aggregator.deps import AggregatorDeps
from agents.deep_search_v4.aggregator.file_refs import build_file_refs
from agents.deep_search_v4.aggregator.models import (
    AggregatorInput,
    AggregatorLLMOutput,
    AggregatorOutput,
    FileRef,
    Reference,
    ValidationReport,
)
from agents.deep_search_v4.aggregator.postvalidator import (
    check_arabic_only,
    extract_cited_numbers,
)
from agents.deep_search_v4.shared.case_documents import (
    FileRefDoc,
    build_case_document_blocks,
    build_case_document_blocks_segmented,
)
from agents.models import WorkspaceItemSnapshot
from agents.utils.attachment_delivery import (
    DEEP_SEARCH_VALUES,
    PIN_TAG,
    Section,
    SectionPin,
    render_sections,
)


def _doc(seq: int, content: str, *, summary: str = "ملخص", doc_type: str = "judgment"):
    return WorkspaceItemSnapshot(
        item_id=f"id-{seq}",
        kind="attachment",
        title=f"وثيقة {seq}",
        content_md=content,
        summary=summary,
        metadata={"doc_type": doc_type},
        wi_seq=seq,
    )


def _body(blocks, item_id):
    return next(b.body for b in blocks if b.source_item_id == item_id)


PIN = SectionPin("art-77", "المادة 77 من نظام العمل", "نص المادة")
SECTIONS = [
    Section(1, "metadata", "بيانات الحكم", "المحكمة العامة", char_start=0, char_end=15),
    Section(4, "core", "أسباب الحكم", "وحيث إن المحكمة", owner="المحكمة", char_start=15, char_end=40, pins=(PIN,)),
    Section(5, "boilerplate", "ترويسة", "وزارة العدل", char_start=40, char_end=52),
    Section(6, "core", "منطوق الحكم", "حكمت المحكمة برفض الدعوى", char_start=52, char_end=80),
    Section(7, "core", "قسم فارغ", "   ", char_start=80, char_end=83),
]


# ===========================================================================
# render_sections(file_wi=) — the ready tag on each kept section (D12)
# ===========================================================================


def test_sections_carry_their_ready_tag_and_pins_stay_untagged():
    text, _ = render_sections(SECTIONS, DEEP_SEARCH_VALUES, file_wi=3)
    assert "〔F3·S4 · أسباب الحكم — المحكمة〕\nوحيث إن المحكمة" in text
    assert "〔F3·S6 · منطوق الحكم〕\nحكمت المحكمة برفض الدعوى" in text
    # the pinned article under S4 keeps its own tag, with no file tag
    assert f"〔{PIN_TAG.format(title='المادة 77 من نظام العمل')}〕\nنص المادة" in text
    assert "·S1" not in text and "·S5" not in text and "F3·S7" not in text
    # the label's tag parses back to the canonical ref code resolves
    from shared.workspace.file_refs import file_tags
    assert file_tags("[F3·S4]") == ["F3.S4"]


def test_untagged_is_byte_identical():
    assert render_sections(SECTIONS, DEEP_SEARCH_VALUES) == render_sections(
        SECTIONS, DEEP_SEARCH_VALUES, file_wi=None
    )
    text, _ = render_sections(SECTIONS, DEEP_SEARCH_VALUES)
    assert "〔أسباب الحكم — المحكمة〕" in text and "〔F" not in text


# ===========================================================================
# Ready tags, documents keyed by WI number — segmented and plain builders
# ===========================================================================


def test_segmented_tags_key_documents_by_wi_and_skip_summary_only():
    long1 = "س\n" * 6_000
    docs = [_doc(1, long1), _doc(2, "نص قصير"), _doc(3, "")]
    numbered: list[FileRefDoc] = []
    blocks = build_case_document_blocks_segmented(
        docs, ["id-2", "id-1"], {"id-1": SECTIONS}, file_ref_docs=numbered,
    )
    # planner-named first: id-2 then id-1; id-3 has no text → summary only, not listed
    assert [(d.wi_seq, d.item_id) for d in numbered] == [(2, "id-2"), (1, "id-1")]
    assert numbered[0].whole and numbered[0].sections == ()
    assert numbered[1].sections == ((4, 15, 40), (6, 52, 80))
    # headings are today's: the tag's number IS the document's WI number
    assert _body(blocks, "id-2").startswith("### WI-2 · judgment · وثيقة 2")
    assert _body(blocks, "id-1").startswith("### WI-1 · judgment · وثيقة 1")
    assert "〔F1·S4 · أسباب الحكم — المحكمة〕" in _body(blocks, "id-1")
    # a document shown whole gets ONE ready-tag line over its text
    assert "#### النص الكامل\n〔F2〕\nنص قصير" in _body(blocks, "id-2")
    assert _body(blocks, "id-2").count("〔F2〕") == 1
    # summary only: no tag at all
    assert _body(blocks, "id-3").startswith("### WI-3 · judgment · وثيقة 3")
    assert "〔F" not in _body(blocks, "id-3")


def test_a_fallback_text_gets_the_whole_document_tag():
    long_text = "نص طويل بلا خريطة\n" * 600
    numbered: list[FileRefDoc] = []
    blocks = build_case_document_blocks_segmented(
        [_doc(4, long_text)], ["id-4"], {}, file_ref_docs=numbered,
    )
    assert [(d.wi_seq, d.whole) for d in numbered] == [(4, True)]
    assert "#### النص الكامل\n〔F4〕\nنص طويل بلا خريطة" in blocks[0].body


def test_a_document_without_a_wi_number_gets_no_tag_and_is_not_listed():
    doc = _doc(1, "س\n" * 6_000).model_copy(update={"wi_seq": None})
    short = _doc(2, "نص قصير").model_copy(update={"wi_seq": None})
    numbered: list[FileRefDoc] = []
    blocks = build_case_document_blocks_segmented(
        [doc, short], ["id-1", "id-2"], {"id-1": SECTIONS}, file_ref_docs=numbered,
    )
    assert numbered == []
    assert all("〔F" not in b.body for b in blocks)
    assert "〔أسباب الحكم — المحكمة〕" in blocks[0].body


def test_segmented_without_tags_is_byte_identical():
    docs = [_doc(1, "س\n" * 6_000), _doc(2, "نص قصير")]
    plain = build_case_document_blocks_segmented(docs, ["id-1", "id-2"], {"id-1": SECTIONS})
    again = build_case_document_blocks_segmented(
        docs, ["id-1", "id-2"], {"id-1": SECTIONS}, file_ref_docs=None,
    )
    assert [b.body for b in plain] == [b.body for b in again]
    assert all("〔F" not in b.body for b in plain)


def test_plain_builder_tags_full_text_documents_only():
    docs = [_doc(1, "نص الحكم"), _doc(2, "نص العقد")]
    numbered: list[FileRefDoc] = []
    blocks = build_case_document_blocks(docs, ["id-2"], file_ref_docs=numbered)
    assert [(d.wi_seq, d.item_id, d.whole) for d in numbered] == [(2, "id-2", True)]
    assert _body(blocks, "id-2").startswith("### WI-2 ·")
    assert "#### النص الكامل\n〔F2〕\nنص العقد" in _body(blocks, "id-2")
    assert "〔F" not in _body(blocks, "id-1")  # summary only
    plain = build_case_document_blocks(docs, ["id-2"])
    again = build_case_document_blocks(docs, ["id-2"], file_ref_docs=None)
    # off: byte-identical, no tag line
    assert [b.body for b in plain] == [b.body for b in again]
    assert all("〔F" not in b.body for b in plain)
    assert _body(plain, "id-2") == _body(blocks, "id-2").replace("〔F2〕\n", "")


def test_over_total_document_gets_no_tag():
    docs = [_doc(1, "أ" * 4_000), _doc(2, "ب" * 4_000)]
    numbered: list[FileRefDoc] = []
    blocks = build_case_document_blocks_segmented(
        docs, ["id-1", "id-2"], {}, total_cap=5_000, file_ref_docs=numbered,
    )
    assert [d.item_id for d in numbered] == ["id-1"]
    assert "〔F" not in _body(blocks, "id-2")


# ===========================================================================
# _build_case_document_lists — the aggregator only, never «بحث موسّع»
# ===========================================================================


def _decision(case=(), reg=()):
    return SimpleNamespace(case_full_text_wis=list(case), reg_full_text_wis=list(reg))


def test_lists_number_the_aggregator_only():
    deps = SimpleNamespace(attached_items=[_doc(1, "س\n" * 6_000)])
    numbered: list[FileRefDoc] = []
    lists, tel = ds_orch._build_case_document_lists(
        _decision(case=["WI-1"], reg=["WI-1"]), deps, {"id-1": SECTIONS}, file_ref_docs=numbered,
    )
    assert lists["aggregator"][0].body.startswith("### WI-1 ·")
    assert "〔F1·S4 · " in lists["aggregator"][0].body
    for expander in ("case", "reg"):
        assert "〔F" not in lists[expander][0].body
        assert "〔أسباب الحكم — المحكمة〕" in lists[expander][0].body
    assert [d.item_id for d in numbered] == ["id-1"]
    assert tel["case_docs.file_refs.docs"] == 1
    assert tel["case_docs.file_refs.sections"] == 2


def test_lists_without_numbering_are_unchanged():
    deps = SimpleNamespace(attached_items=[_doc(1, "س\n" * 6_000)])
    a, tel_a = ds_orch._build_case_document_lists(_decision(case=["WI-1"]), deps, {"id-1": SECTIONS})
    b, tel_b = ds_orch._build_case_document_lists(
        _decision(case=["WI-1"]), deps, {"id-1": SECTIONS}, file_ref_docs=None,
    )
    assert {k: [x.body for x in v] for k, v in a.items()} == {k: [x.body for x in v] for k, v in b.items()}
    assert tel_a == tel_b and not any(k.startswith("case_docs.file_refs") for k in tel_a)


def test_whole_file_delivery_is_never_numbered():
    deps = SimpleNamespace(attached_items=[_doc(1, "نص")], _case_doc_caps=(200_000, 300_000))
    numbered: list[FileRefDoc] = []
    lists, tel = ds_orch._build_case_document_lists(_decision(), deps, None, file_ref_docs=numbered)
    assert numbered == []
    assert "〔F" not in lists["aggregator"][0].body
    assert "case_docs.file_refs.docs" not in tel


def test_file_refs_on_needs_the_flag_and_the_ordinary_search(monkeypatch):
    monkeypatch.setattr(ds_orch, "file_refs_enabled", lambda uid: uid == "u1")
    assert ds_orch._file_refs_on(SimpleNamespace(user_id="u1")) is True
    assert ds_orch._file_refs_on(SimpleNamespace(user_id="u2")) is False
    assert ds_orch._file_refs_on(SimpleNamespace(user_id="u1", _case_doc_caps=(1, 2))) is False


def test_flag_reads_settings(monkeypatch):
    from agents.utils import aggregator_file_ref_flags as flags

    settings = SimpleNamespace(AGGREGATOR_FILE_REFS_ENABLED=False, AGGREGATOR_FILE_REFS_USERS=None)
    monkeypatch.setattr(flags, "get_settings", lambda: settings)
    assert flags.file_refs_enabled("u1") is False
    settings.AGGREGATOR_FILE_REFS_ENABLED = True
    assert flags.file_refs_enabled("u1") is True  # empty list = everyone
    settings.AGGREGATOR_FILE_REFS_USERS = "u2, u3"
    assert flags.file_refs_enabled("u1") is False
    assert flags.file_refs_enabled("u3") is True


def test_run_retrieval_and_run_full_loop_wiring():
    rr = inspect.getsource(ds_orch.run_retrieval)
    assert "file_ref_docs: list[FileRefDoc] | None = [] if _file_refs_on(deps) else None" in rr
    assert "case_document_file_refs=list(file_ref_docs or [])" in rr
    loop = inspect.getsource(ds_orch.run_full_loop)
    assert "agg_input.file_ref_docs = list(deps.case_document_file_refs)" in loop
    assert "agg_deps.file_refs = bool(agg_input.file_ref_docs)" in loop


# ===========================================================================
# Prompt — flag off byte-identical, flag on carries the rules
# ===========================================================================


@pytest.mark.parametrize("key", sorted(agg_prompts.AGGREGATOR_PROMPTS))
def test_prompt_off_is_the_stored_prompt(key):
    stored = agg_prompts.AGGREGATOR_PROMPTS[key]
    assert agg_prompts.get_aggregator_prompt(key) is stored
    assert agg_prompts.get_aggregator_prompt(key, file_refs=False) is stored
    assert "[F3·S7]" not in stored and "file tag" not in stored


@pytest.mark.parametrize("key", sorted(agg_prompts.AGGREGATOR_PROMPTS))
def test_prompt_on_carries_the_file_tag_rules(key):
    on = agg_prompts.get_aggregator_prompt(key, file_refs=True)
    assert "**Point to the part you used — file tags.**" in on
    assert "`〔F3·S7 · …〕`" in on and "`〔F4〕`" in on and "[F3·S7]" in on and "[F4]" in on
    assert "Copy the tag; never build one." in on
    assert "«من المكتبة النظامية، لا من المستند — …»" in on  # never on pinned text
    assert "only the summary of has no label" in on
    # WI-N is never written: the base rule stays, and the file-tag rules add no
    # WI-N mention and no WI tag form (D12)
    stored = agg_prompts.AGGREGATOR_PROMPTS[key]
    assert "never by its `WI-N` alias" in on
    assert on.count("WI-N") == stored.count("WI-N")
    assert "ONLY place a `WI-N`" not in on
    assert "[WI-" not in on and "`WI` number" not in on
    # the rules sit inside the case_document block, after the legal-authority rule
    assert on.index(agg_prompts._FILE_REFS_ANCHOR) < on.index("Point to the part you used")
    assert on.count("Point to the part you used") == 1


def test_create_agent_passes_the_switch(monkeypatch):
    from agents.deep_search_v4.aggregator import agent as agg_agent

    seen = []
    monkeypatch.setattr(agg_agent, "get_agent_model", lambda slot: "test")
    monkeypatch.setattr(agg_agent, "Agent", lambda *a, **kw: seen.append(kw["instructions"]) or kw)
    agg_agent.create_aggregator_agent("prompt_1")
    agg_agent.create_aggregator_agent("prompt_1", file_refs=True)
    assert seen[0] == agg_prompts.AGGREGATOR_PROMPTS["prompt_1"]
    assert "Point to the part you used" in seen[1]


# ===========================================================================
# Output format unchanged; file refs are code-side only
# ===========================================================================


def test_llm_output_schema_is_unchanged():
    assert list(AggregatorLLMOutput.model_fields) == ["synthesis_md", "used_refs", "gaps", "confidence"]
    assert (
        AggregatorLLMOutput.model_fields["synthesis_md"].description
        == "Arabic markdown body with inline [n] or [n,m] citations"
    )
    assert "WI-" not in str(AggregatorLLMOutput.model_json_schema())


def test_prompt_version_is_v3():
    assert agg_prompts.FILE_REFS_PROMPT_VERSION == "file-refs-v3"


def test_file_refs_never_serialised():
    out = AggregatorOutput(
        synthesis_md="x", confidence="low",
        file_refs=[FileRef(n=1, ref_id="F1.S7", item_id="id-1", wi_seq=1, section_seq=7)],
    )
    assert "file_refs" not in out.model_dump()
    assert "file_refs" not in out.model_dump_json()
    assert out.file_refs[0].ref_id == "F1.S7"


def test_library_checks_ignore_file_tags():
    body = "## الخلاصة\n\n«لم يقدّم ما يثبت السداد» [F1·S7]، وهذا يخالف المادة 77 [3]. وفي العقد [F2][1,2]."
    assert extract_cited_numbers(body) == [1, 2, 3]
    assert check_arabic_only(body)
    refs = [
        Reference(n=n, source_type="article", regulation_title="نظام", title="مادة", relevance="high")
        for n in (1, 2, 3, 4)
    ]
    art = build_artifact(
        AggregatorInput(original_query="س", sub_queries=[]), body, refs, "high", "", "prompt_1", "m",
    )
    assert art.metadata["cited_count"] == 3


# ===========================================================================
# build_file_refs — resolving the tags
# ===========================================================================

DOCS = [
    FileRefDoc(wi_seq=1, item_id="id-1", sections=((4, 15, 40), (6, 52, 80), (9, -1, -1))),
    FileRefDoc(wi_seq=2, item_id="id-2"),
]


def test_resolve_variants_in_order_of_first_mention():
    text = "«…» [F2] ثم [f-1-s06] ثم [F1·S4] ثم [F1.S6] ثم [F 1 S9]"
    refs = build_file_refs(text, DOCS)
    assert [(r.ref_id, r.n, r.item_id, r.wi_seq, r.section_seq) for r in refs] == [
        ("F2", 1, "id-2", 2, None),
        ("F1.S6", 2, "id-1", 1, 6),
        ("F1.S4", 3, "id-1", 1, 4),
        ("F1.S9", 4, "id-1", 1, 9),
    ]
    assert (refs[1].char_start, refs[1].char_end) == (52, 80)
    assert (refs[3].char_start, refs[3].char_end) == (None, None)  # -1 = no position
    assert refs[0].char_start is None


def test_unresolvable_tags_are_skipped_and_never_edited():
    # F3 not shown with text (a summary-only document, a report…), S5 not shown,
    # [F1] on a sectioned document, [F2·S1] on a whole one, the old D and WI forms
    text = "[F3·S1] [F1·S5] [F1] [F2·S1] [D1·S4] [WI-1·S4] [F1·S4]"
    refs = build_file_refs(text, DOCS)
    assert [r.ref_id for r in refs] == ["F1.S4"]
    assert refs[0].n == 1
    assert build_file_refs(text, []) == []
    assert build_file_refs("", DOCS) == []


# ===========================================================================
# The runner — file refs on the output, the switch on the agent
# ===========================================================================


def _run(monkeypatch, *, synthesis: str, docs, file_refs_switch: bool):
    seen: dict = {}
    ref = Reference(n=1, source_type="article", regulation_title="نظام", title="مادة", relevance="high")
    monkeypatch.setattr(agg_runner, "preprocess_references", lambda agg_input: ([ref], {1: [0]}))

    async def fake_primary(agg_input, deps, user_message, references):
        seen["file_refs"] = deps.file_refs
        return (
            AggregatorLLMOutput(synthesis_md=synthesis, used_refs=[1], gaps=[], confidence="high"),
            "m", {}, None,
        )

    monkeypatch.setattr(agg_runner, "_run_primary_path", fake_primary)
    monkeypatch.setattr(
        agg_runner, "_validate", lambda *a, **kw: ValidationReport(passed=True, cited_numbers=[1]),
    )
    agg_input = AggregatorInput(original_query="س", sub_queries=[])
    agg_input.file_ref_docs = list(docs)
    deps = AggregatorDeps(build_artifact=False, file_refs=file_refs_switch)
    out = asyncio.run(agg_runner.handle_aggregator_turn(agg_input, deps))
    return out, seen


def test_runner_builds_file_refs_and_keeps_the_text(monkeypatch):
    text = "## الخلاصة\n\n«…» [F1·S4] [1] و[F9·S1]."
    out, seen = _run(monkeypatch, synthesis=text, docs=DOCS, file_refs_switch=True)
    assert seen["file_refs"] is True
    assert [r.ref_id for r in out.file_refs] == ["F1.S4"]
    assert out.synthesis_md == text.strip()  # code never edits a tag (D3)


def test_runner_without_docs_has_no_file_refs(monkeypatch):
    out, seen = _run(monkeypatch, synthesis="## الخلاصة\n\nنص [1] [F1·S4]", docs=[], file_refs_switch=False)
    assert seen["file_refs"] is False
    assert out.file_refs == []
