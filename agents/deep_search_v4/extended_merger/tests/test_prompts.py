"""Unit tests of the extended-search merger's prompt and user-message builder.

Pure: no model, no network, no database.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from agents.deep_search_v4.aggregator.models import AggregatorOutput, Reference
from agents.deep_search_v4.aggregator.prompts import _MEMORY_CONFLICT_NOTE, _SHARED_ROLE_AR
from agents.deep_search_v4.extended_merger import prompts as P
from agents.deep_search_v4.extended_merger.models import (
    ANGLE_STATES,
    KIND_AR,
    MODE_AR,
    STRENGTH_AR,
    AngleStatus,
    LeftOutAngle,
    MergerAngle,
    MergerDocument,
    MergerInput,
    MergerLLMOutput,
    MergerReport,
    Resolution,
)
from agents.deep_search_v4.extended_merger.prompts import (
    MERGER_SYSTEM_PROMPT,
    PROMPT_VERSION,
    RETRY_MSG,
    build_merger_user_message,
    render_angle_line,
    scoreboard_angle_ids,
)
from shared.library.reg_status import REPEALED_LABEL_AR

_ARABIC_INDIC = re.compile(r"[٠-٩۰-۹]")
_MERGER_PART = MERGER_SYSTEM_PROMPT[len(_SHARED_ROLE_AR):]


class _Ref(Reference):
    """``Reference`` with the in-process ``status_note`` (not on origin/master's model yet)."""

    status_note: str = ""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ref(**kw) -> Reference:
    base = dict(n=99, source_type="chunk", regulation_title="نظام العمل", title="نظام العمل",
                snippet="", relevance="high")
    base.update(kw)
    return _Ref(**base)


def _output(body: str = "## الخلاصة\nنص [1]", confidence: str = "medium", gaps=None) -> AggregatorOutput:
    return AggregatorOutput(synthesis_md=body, confidence=confidence, gaps=gaps or [])


def _angles() -> dict[str, MergerAngle]:
    return {
        "A1": MergerAngle(id="A1", kind="strengthen", strength="strong", title="عقد مكتوب",
                          argument="العقد يثبت الأجر.", legal_question="هل يثبت العقد الأجر؟",
                          weakness="التوقيع ناقص", for_side="claimant", for_party="المدعي",
                          sections=["D1.S7 (الموكّل: المدعي)", "D1.S12 (الخصم: الشركة)"]),
        "A2": MergerAngle(id="A2", kind="answer", strength="medium", title="دفع التقادم",
                          argument="الخصم يدفع بالتقادم.", legal_question="متى يسقط الحق بالتقادم؟",
                          for_side="claimant", for_party="المدعي"),
        "A3": MergerAngle(id="A3", kind="attack", strength="weak", title="فاتورة مشكوك فيها",
                          argument="الفاتورة بلا ختم.", legal_question="ما حجية الفاتورة؟",
                          for_side="respondent", for_party="الشركة", ocr_suspect=True),
        "A7": MergerAngle(id="A7", kind="strengthen", strength="weak", title="شهادة الشهود",
                          argument="شاهد واحد.", legal_question="نصاب الشهادة؟"),
    }


def _input(**kw) -> MergerInput:
    base = dict(
        query="هل موقفي قوي؟",
        reports=[
            MergerReport(title="الأجر والعقد", mode="full", focus="إثبات الأجر", angle_ids=["A1", "A2"],
                         why="أقوى الأوجه", output=_output(gaps=["لم يُعثر على سابقة"])),
            MergerReport(title="الفاتورة", mode="case_led", focus="حجية الفاتورة", angle_ids=["A3", "A9"],
                         why="", output=None),
        ],
        angles=_angles(),
        documents=[MergerDocument(title="عقد العمل", doc_type="contract", summary="ملخص آلي",
                                  text="〔S7 · المدعي · جوهري〕\nنص العقد")],
        recent_messages=[{"role": "user", "content": "رفعت دعوى"}, {"role": "assistant", "content": "حسناً"}],
        understanding="المستخدم هو المدعي.",
        client_side="claimant",
        client_party="المدعي",
        left_out=[LeftOutAngle(angle_id="A7", why="ضعيف ولم يُسأل عنه")],
    )
    base.update(kw)
    return MergerInput(**base)


def _build(inp: MergerInput | None = None, refs=None, bodies=None) -> str:
    inp = inp or _input()
    refs = [_ref(n=5, snippet="المادة 77 ...")] if refs is None else refs
    bodies = ["## الخلاصة\nالأجر ثابت [1]", None] if bodies is None else bodies
    return build_merger_user_message(inp, shared_refs=refs, report_bodies=bodies)


def _report_block(msg: str, n: int) -> str:
    m = re.search(rf'<report n="{n}" .*?</report>', msg, re.S)
    assert m, f"report {n} not rendered"
    return m.group(0)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def test_prompt_version():
    assert PROMPT_VERSION == "merger-v1"


def test_shared_role_is_imported_first():
    assert MERGER_SYSTEM_PROMPT.startswith(_SHARED_ROLE_AR)
    assert MERGER_SYSTEM_PROMPT.count(_SHARED_ROLE_AR) == 1


@pytest.mark.parametrize("needle", [
    "## الخلاصة",                         # opening
    "Organise by the user's question, not by report",
    "«التقرير الأول…»",                    # forbidden phrasing named
    "remove only repetition",
    "re-answer the user's whole question",  # plan §6 item 9
    "never silently",
    "Never settle it by majority",
    "never by an angle's strength label",
    "〔D1·S7 · …〕",                           # code markers
    "machine analysis",
    "follow the text",
    "Nothing new",
    "normally shorter than the reports put together",
    "drop a gap another report closed",
    "signature fields are blank",          # §6 item 3
    "takes effect only once signed",
    "cover email",                          # §6 item 5
    "may never exceed the highest `confidence` among the reports",
    "## Strengths and weaknesses (نقاط القوة ونقاط الضعف)",  # §4.9 c
    "Never drop a weakness because another report did not mention it",
    "A weakness no report answered stays in the answer, marked open",
    "## The angle scoreboard — `angle_status`",
    "failed reports included",
    "must agree with `synthesis_md`",
    "Never write findings in the answer about an angle no report researched",
    "`settled`",
    "Never present findings on a left-out angle, never list it in `gaps`",
    "<draft>",
    "at most 4 numbers per tag",
    "Western digit",
    "Article and system numbers are written in prose",
    "Every number in `used_refs` must appear as `[n]` in `synthesis_md`",
    "legal disclaimer",
    "«المراجع»",
])
def test_key_rules_present(needle):
    assert needle in MERGER_SYSTEM_PROMPT


def test_strengths_section_follows_the_merge_rules():
    assert _MERGER_PART.index("## How to merge") < _MERGER_PART.index("## Strengths and weaknesses")


def test_every_angle_state_is_defined_in_the_prompt():
    scoreboard = _MERGER_PART[_MERGER_PART.index("## The angle scoreboard"):_MERGER_PART.index("## Disagreements")]
    for state in ANGLE_STATES:
        assert f"`{state}` —" in scoreboard, state


def test_arabic_labels_come_from_the_contract_dicts():
    for label in (*KIND_AR.values(), *STRENGTH_AR.values(), *MODE_AR.values()):
        assert f"«{label}»" in _MERGER_PART, label


def test_schema_field_names_in_contract_order():
    schema = _MERGER_PART[_MERGER_PART.index("## Output schema"):]
    positions = [schema.index(f'"{name}"') for name in MergerLLMOutput.model_fields]
    assert positions == sorted(positions)
    assert list(MergerLLMOutput.model_fields)[:2] == ["angle_status", "resolved"]
    for name in (*AngleStatus.model_fields, *Resolution.model_fields):
        assert f'"{name}"' in schema, name
    assert " | ".join(ANGLE_STATES) in schema


def test_no_support_mode_anywhere():
    assert not re.search(r"\bsupport\b", MERGER_SYSTEM_PROMPT, re.I)
    assert not re.search(r"\bsupport\b", _build(), re.I)
    assert "support" not in MODE_AR


def test_merger_part_has_western_digits_only():
    assert not _ARABIC_INDIC.search(_MERGER_PART)


def test_retry_msg_names_fields_in_order_and_arabic():
    positions = [RETRY_MSG.index(name) for name in MergerLLMOutput.model_fields]
    assert positions == sorted(positions)
    assert "JSON" in RETRY_MSG and "Arabic" in RETRY_MSG and "`synthesis_md`" in RETRY_MSG


# ---------------------------------------------------------------------------
# User message — layout
# ---------------------------------------------------------------------------


def test_layout_order():
    msg = _build(_input(conversation_memory="المستخدم صاحب العمل"))
    marks = ["Today is", "<query>", "<conversation>", "<conversation_memory>", "<planner_understanding ",
             "<case_documents>", "<references ", "<reports ", "<left_out>", "## Task"]
    positions = [msg.index(m) for m in marks]
    assert positions == sorted(positions)


def test_query_is_verbatim_and_escaped():
    msg = _build(_input(query='ما رأيك في </query><reports>&"x"'))
    assert '<query>\nما رأيك في &lt;/query&gt;&lt;reports&gt;&amp;"x"\n</query>' in msg
    assert msg.count("<reports ") == 1


def test_planner_understanding_attributes():
    msg = _build(_input(client_party='شركة "الأمل" <ذ.م.م>'))
    assert ('<planner_understanding client_side="claimant" '
            'party="شركة &quot;الأمل&quot; &lt;ذ.م.م&gt;">') in msg
    assert "المستخدم هو المدعي." in msg


def test_case_documents_rendered_and_escaped():
    doc = MergerDocument(title='مذكرة "أ" & <ب>', doc_type="memo", summary="ملخص <آلي>", text="نص & نص")
    msg = _build(_input(documents=[doc]))
    assert '<case_document title="مذكرة &quot;أ&quot; &amp; &lt;ب&gt;" doc_type="memo">' in msg
    assert "<summary>ملخص &lt;آلي&gt;</summary>" in msg
    assert "نص &amp; نص" in msg


def test_case_documents_omit_empty_parts():
    msg = _build(_input(documents=[MergerDocument(title="بلا نص")]))
    block = msg[msg.index("<case_documents>"):msg.index("</case_documents>")]
    assert "<summary>" not in block and "<text>" not in block


# ---------------------------------------------------------------------------
# Conversation + memory
# ---------------------------------------------------------------------------


def test_conversation_from_dicts():
    msg = _build()
    assert '<message role="user">رفعت دعوى</message>' in msg
    assert '<message role="assistant">حسناً</message>' in msg


def test_conversation_from_objects():
    msgs = [SimpleNamespace(role="user", content="سؤال <أول>"), SimpleNamespace(role="assistant", content="")]
    msg = _build(_input(recent_messages=msgs))
    assert '<message role="user">سؤال &lt;أول&gt;</message>' in msg
    assert 'role="assistant"' not in msg  # empty content skipped


def test_conversation_omitted_when_empty():
    assert "<conversation>" not in _build(_input(recent_messages=[]))


def test_memory_absent_when_empty():
    msg = _build(_input(conversation_memory="  "))
    assert "<conversation_memory>" not in msg
    assert _MEMORY_CONFLICT_NOTE not in msg


def test_memory_pre_rendered_block_kept_as_is():
    block = '<conversation_memory>\nقواعد\n\n<entry id="M-1" kind="واقعة">أجر &amp; بدل</entry>\n</conversation_memory>'
    msg = _build(_input(conversation_memory=block))
    assert block in msg  # never escaped twice
    assert _MEMORY_CONFLICT_NOTE in msg


def test_memory_raw_text_is_fenced_and_escaped():
    msg = _build(_input(conversation_memory="أجر <5000> & بدل"))
    assert "<conversation_memory>\nأجر &lt;5000&gt; &amp; بدل\n</conversation_memory>" in msg


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


def test_references_numbered_by_position_not_own_n():
    refs = [_ref(n=7, title="أ", regulation_title="أ"), _ref(n=2, title="ب", regulation_title="ب")]
    msg = _build(refs=refs)
    assert '<references count="2">' in msg
    assert msg.index('<reference n="1" type="chunk">\n    <title>أ</title>') < msg.index('<reference n="2"')
    assert '<reference n="7"' not in msg


def test_reference_source_only_when_different():
    same = _ref(title="نظام العمل", regulation_title="نظام العمل")
    other = _ref(source_type="case", title="فصل تعسفي", regulation_title="المحكمة العمالية")
    msg = _build(refs=[same, other])
    first = msg[msg.index('<reference n="1"'):msg.index('<reference n="2"')]
    second = msg[msg.index('<reference n="2"'):msg.index("</references>")]
    assert "<source>" not in first
    assert "<source>المحكمة العمالية</source>" in second


def test_reference_ruling_year_number_and_excerpt():
    ruling = _ref(source_type="case", title="حكم", regulation_title="محكمة", case_number="434939",
                  hijri_year="١٤٤٣", snippet="المحكمة رأت أن <الأجر> ثابت")
    msg = _build(refs=[ruling])
    assert "<case_number>434939</case_number>" in msg
    assert "<year>سنة القضية: 1443هـ</year>" in msg
    assert "<excerpt>المحكمة رأت أن &lt;الأجر&gt; ثابت</excerpt>" in msg


def test_reference_year_skipped_when_excerpt_has_one():
    ruling = _ref(source_type="case", title="حكم", hijri_year="1443",
                  snippet="المحكمة: العمالية\n\nسنة القضية: 2021\n\nالملخص")
    msg = _build(refs=[ruling])
    assert "<year>" not in msg
    assert "سنة القضية: 2021" in msg


def test_reference_without_year_excerpt_or_status():
    msg = _build(refs=[_ref(snippet="")])
    block = msg[msg.index('<reference n="1"'):msg.index("</references>")]
    for tag in ("<year>", "<excerpt>", "<status>", "<draft>", "<case_number>", "<decisions>"):
        assert tag not in block, tag


def test_reference_repealed_status_and_draft():
    repealed = _ref(status_note=f"{REPEALED_LABEL_AR} (النص الأصلي: ملغى)")
    draft = _ref(status_note="مسودة طرحت للاستطلاع العام — لم يتم التحقق من صدورها بصيغتها النهائية")
    msg = _build(refs=[repealed, draft])
    first = msg[msg.index('<reference n="1"'):msg.index('<reference n="2"')]
    second = msg[msg.index('<reference n="2"'):msg.index("</references>")]
    assert f"<status>{REPEALED_LABEL_AR}" in first and "<draft>" not in first
    assert "<draft>مسودة طرحت" in second and "<status>" not in second


def test_reference_without_status_note_attribute():
    plain = Reference(n=1, source_type="chunk", regulation_title="ن", title="ن", relevance="medium")
    msg = _build(refs=[plain])
    assert "<status>" not in msg and "<draft>" not in msg


def test_reference_principle_decisions():
    principle = _ref(source_type="official_principle", title="المبدأ رقم 2", regulation_title="المحكمة العليا",
                     decision_line="القرار 162/5 بتاريخ 24/3/1415هـ")
    msg = _build(refs=[principle])
    assert "<decisions>القرار 162/5 بتاريخ 24/3/1415هـ</decisions>" in msg
    assert "<source>المحكمة العليا</source>" in msg


def test_reference_type_attribute_escaped():
    msg = _build(refs=[SimpleNamespace(source_type='x"><y', title="t", regulation_title="t", snippet="")])
    assert 'type="x&quot;&gt;&lt;y"' in msg


# ---------------------------------------------------------------------------
# Reports + angles
# ---------------------------------------------------------------------------


def test_ok_report_attributes_body_and_gaps():
    block = _report_block(_build(), 1)
    assert block.startswith(f'<report n="1" title="الأجر والعقد" side="{MODE_AR["full"]}" '
                            'confidence="medium" status="ok">')
    assert "<focus>إثبات الأجر</focus>" in block and "<why>أقوى الأوجه</why>" in block
    assert "<body>\n## الخلاصة\nالأجر ثابت [1]\n    </body>" in block
    assert "<gap>لم يُعثر على سابقة</gap>" in block


def test_failed_report_has_status_failed_and_no_body():
    block = _report_block(_build(bodies=["نص", "نص لا يجب أن يظهر"]), 2)
    assert f'side="{MODE_AR["case_led"]}"' in block
    assert 'status="failed"' in block
    assert "confidence=" not in block
    assert "<body>" not in block and "<gaps>" not in block
    assert "نص لا يجب أن يظهر" not in block
    assert "<focus>حجية الفاتورة</focus>" in block and "<why>" not in block
    assert "وجه A3" in block  # its angles still listed


def test_report_title_escaped_in_attribute():
    inp = _input()
    inp.reports[0].title = 'العقد "الأول" <و> & الملحق'
    block = _report_block(_build(inp), 1)
    assert 'title="العقد &quot;الأول&quot; &lt;و&gt; &amp; الملحق"' in block


def test_report_body_escaped():
    block = _report_block(_build(bodies=["أ <b> & ج [1]", None]), 1)
    assert "أ &lt;b&gt; &amp; ج [1]" in block


def test_report_bodies_length_must_match():
    with pytest.raises(ValueError):
        _build(bodies=["نص"])


def test_unknown_mode_has_no_side_attribute():
    inp = _input()
    inp.reports[0].mode = "mystery"
    assert "side=" not in _report_block(_build(inp), 1)


def test_angle_line_layout_with_arabic_labels():
    block = _report_block(_build(), 1)
    expected = ("- وجه A1 [يقوّي · قوي]: عقد مكتوب — العقد يثبت الأجر. سؤال البحث: هل يثبت العقد الأجر؟ "
                "ما يضعفه: التوقيع ناقص الأقسام: D1.S7 (الموكّل: المدعي)، D1.S12 (الخصم: الشركة)")
    assert expected in block
    assert f"- وجه A2 [{KIND_AR['answer']} · {STRENGTH_AR['medium']}]: دفع التقادم" in block


def test_angle_line_omits_empty_weakness_and_sections():
    line = render_angle_line("A2", _angles()["A2"], "claimant")
    assert "ما يضعفه" not in line and "الأقسام" not in line


def test_angle_line_flags_other_side_and_ocr():
    line = render_angle_line("A3", _angles()["A3"], "claimant")
    assert line.startswith(f"وجه A3 [{KIND_AR['attack']} · {STRENGTH_AR['weak']} · لصالح الشركة · "
                           f"{P.OCR_SUSPECT_AR}]: ")
    # the user's own side and an unknown side are not tagged
    assert "لصالح" not in render_angle_line("A1", _angles()["A1"], "claimant")
    assert "لصالح" not in render_angle_line("A7", _angles()["A7"], "claimant")
    # with the user's side unknown, every sided angle names its party
    assert "لصالح المدعي" in render_angle_line("A1", _angles()["A1"], "unknown")


def test_missing_angle_id_rendered_by_id_only():
    block = _report_block(_build(), 2)
    assert "- وجه A9\n" in block


def test_angle_text_escaped():
    angles = _angles()
    angles["A1"].title = 'عقد <موقّع> & "مختوم"'
    block = _report_block(_build(_input(angles=angles)), 1)
    assert 'عقد &lt;موقّع&gt; &amp; "مختوم"' in block


# ---------------------------------------------------------------------------
# Left out + task line
# ---------------------------------------------------------------------------


def test_left_out_present_with_title_and_why():
    msg = _build()
    block = msg[msg.index("<left_out>"):msg.index("</left_out>")]
    assert '<angle id="A7">' in block
    assert "<title>شهادة الشهود</title>" in block
    assert "<why>ضعيف ولم يُسأل عنه</why>" in block


def test_left_out_unknown_id_and_absent_when_empty():
    msg = _build(_input(left_out=[LeftOutAngle(angle_id='A"8', why="")]))
    assert '<angle id="A&quot;8">\n  </angle>' in msg
    assert "<left_out>" not in _build(_input(left_out=[]))


def test_scoreboard_ids_include_failed_reports_exclude_left_out():
    inp = _input()
    assert scoreboard_angle_ids(inp.reports) == ["A1", "A2", "A3", "A9"]
    task = _build(inp)[_build(inp).index("## Task"):]
    assert "A1, A2, A3, A9" in task
    assert "A7" not in task


def test_task_line_without_angles_or_references():
    inp = _input(reports=[MergerReport(title="ت", mode="reg_compliance_led", focus="ف", output=_output())],
                 left_out=[])
    msg = build_merger_user_message(inp, shared_refs=[], report_bodies=["نص"])
    task = msg[msg.index("## Task"):]
    assert "an empty list" in task
    assert "write no `[n]` tag" in task
    assert '<references count="0">' in msg


def test_task_line_cites_reference_range():
    msg = _build(refs=[_ref(), _ref(), _ref()])
    assert "(1–3)" in msg[msg.index("## Task"):]


# ---------------------------------------------------------------------------
# Western digits in code-written parts
# ---------------------------------------------------------------------------


def test_no_arabic_indic_digits_in_code_written_parts():
    refs = [_ref(source_type="case", title="حكم", hijri_year="١٤٤٤", case_number="12"), _ref(), _ref()]
    msg = _build(refs=refs)
    assert not _ARABIC_INDIC.search(msg)
    assert "سنة القضية: 1444هـ" in msg


def test_today_line_opens_the_message(monkeypatch):
    monkeypatch.setattr(P, "today_line", lambda: "Today is 24 نوفمبر 2026م (Saudi time).")
    assert _build().startswith("Today is 24 نوفمبر 2026م (Saudi time).\n\n<query>")


def test_case_document_ref_attribute_names_the_document():
    from agents.deep_search_v4.extended_merger.models import MergerDocument, MergerInput
    from agents.deep_search_v4.extended_merger.prompts import (
        MERGER_SYSTEM_PROMPT,
        build_merger_user_message,
    )

    inp = MergerInput(
        query="س",
        reports=[],
        documents=[
            MergerDocument(title="الحكم", text="نص", ref="D1"),
            MergerDocument(title="المذكرة", text="نص"),
        ],
    )
    msg = build_merger_user_message(inp, shared_refs=[], report_bodies=[])
    assert '<case_document ref="D1" title="الحكم">' in msg
    assert '<case_document title="المذكرة">' in msg
    assert "whose `ref` is `D1`" in MERGER_SYSTEM_PROMPT
