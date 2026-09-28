"""Tests for ``agents.writer.exhibits`` — «مرفق رقم n» instead of ``WI-{seq}``.

Fixture shape mirrors convo a951fd93 (the brief that leaked WI-13/9/12/8 into
a Najiz filing): four attachments, a duplicate upload, and research items.
"""
from __future__ import annotations

from agents.writer.exhibits import (
    annotate_chat_aliases,
    renumber_chat_refs,
    build_alias_map,
    exhibits_metadata,
    format_exhibit_guide_ar,
    sanitize_document,
)
from agents.writer.models import ExhibitRef, WriterSection

ALIASES = build_alias_map([
    {"wi_seq": 2, "kind": "attachment", "title": "مخالصة عمل نهائية (نسخة أولى)", "item_id": "i2"},
    {"wi_seq": 7, "kind": "agent_search", "title": "دفوع الورثة", "item_id": "i7"},
    {"wi_seq": 8, "kind": "attachment", "title": "مخالصة عمل نهائية – شركة زاد الوفود", "item_id": "i8"},
    {"wi_seq": 9, "kind": "attachment", "title": "عقود عمل موسمية — شركة زاد الوقود", "item_id": "i9"},
    {"wi_seq": 10, "kind": "agent_writing", "title": "مذكرة سابقة", "item_id": "i10"},
    {"wi_seq": 12, "kind": "attachment", "title": "كشف حساب بنكي", "item_id": "i12"},
    {"wi_seq": 13, "kind": "attachment", "title": "عقد تأسيس شركة زاد الوفود", "item_id": "i13"},
])

LEAKY_BODY = (
    "بموجب عقد التأسيس منح المورث صلاحيات واسعة (WI-13).\n"
    "وقّع المورث عقود العمل (WI-9)، وصرف الشيكين (WI-12).\n"
    "حرّر المخالصة النهائية (WI-8).\n"
    "المستندات — عقد التأسيس (WI-13)، والعقود (WI-9)، والمخالصة (WI-8)."
)


def _run(sections, exhibits=None, subtype="defense_brief", title="مذكرة دفاع"):
    return sanitize_document(
        title=title,
        sections=sections,
        exhibits=exhibits or [],
        aliases=ALIASES,
        subtype=subtype,
    )


def _body(res) -> str:
    return "\n".join(s.body_md for s in res.sections)


def test_leaky_brief_without_exhibits_gets_numbered_by_first_mention():
    res = _run([WriterSection(heading_ar="## الوقائع", body_md=LEAKY_BODY)])
    body = _body(res)
    assert "WI-" not in body
    assert [(e.n, e.wi) for e in res.exhibits] == [
        (1, "WI-13"), (2, "WI-9"), (3, "WI-12"), (4, "WI-8"),
    ]
    assert "صلاحيات واسعة (مرفق رقم 1)." in body
    assert "عقود العمل (مرفق رقم 2)، وصرف الشيكين (مرفق رقم 3)." in body
    assert res.stats.leaks_attachment == 7
    assert res.stats.exhibits_added == 4
    # «المرفقات» appended as the last section, matching exhibits.
    assert res.sections[-1].heading_ar == "## المرفقات"
    assert res.sections[-1].body_md.splitlines()[0].startswith("1. عقد تأسيس")


def test_writer_exhibits_are_trusted_and_leaks_map_onto_them():
    exhibits = [
        ExhibitRef(n=1, wi="WI-13", label_ar="صورة من عقد تأسيس الشركة"),
        ExhibitRef(n=2, wi="WI-9", label_ar="عقود العمل الموسمية"),
        ExhibitRef(n=3, wi="WI-8", label_ar="المخالصة النهائية", also_wi=["WI-2"]),
    ]
    sections = [WriterSection(heading_ar="## الوقائع", body_md="المخالصة (WI-2) والعقود (WI-9).")]
    res = _run(sections, exhibits)
    # WI-2 → the writer's exhibit 3 (via also_wi); then renumbered to first
    # mention: 3→1, 2→2, 1 (never mentioned)→3.
    assert _body(res).startswith("المخالصة (مرفق رقم 1) والعقود (مرفق رقم 2).")
    assert res.stats.exhibits_added == 0
    assert res.renumber == {3: 1, 2: 2, 1: 3}
    assert [(e.n, e.wi, e.also_wi) for e in res.exhibits] == [
        (1, "WI-8", ["WI-2"]), (2, "WI-9", []), (3, "WI-13", []),
    ]


def test_non_attachment_aliases_are_stripped_cleanly():
    sections = [WriterSection(
        heading_ar="## الدفوع",
        body_md="الأصل براءة الذمة (WI-7). وقد سبق بيانه (WI-10)، ويؤكده النص (3).",
    )]
    res = _run(sections)
    assert _body(res).startswith("الأصل براءة الذمة. وقد سبق بيانه، ويؤكده النص (3).")
    assert res.stats.leaks_stripped == 2
    assert res.exhibits == []
    # No exhibits → no «المرفقات» section.
    assert all(s.heading_ar != "## المرفقات" for s in res.sections)


def test_unknown_alias_is_stripped_and_counted():
    res = _run([WriterSection(heading_ar="## الوقائع", body_md="واقعة ثابتة (WI-99).")])
    assert _body(res).startswith("واقعة ثابتة.")
    assert res.stats.leaks_unknown == 1


def test_grouped_and_bare_and_lowercase_aliases():
    sections = [WriterSection(
        heading_ar="## الوقائع",
        body_md="المستندات (WI-13، WI-7 وwi-9). كما ورد في WI-12 وفي WI-7.",
    )]
    res = _run(sections)
    body = _body(res)
    assert "المستندات (مرفق رقم 1، مرفق رقم 2)." in body
    assert "كما ورد في المرفق رقم 3 وفي ما سبق بيانه." in body
    assert "WI" not in body.upper()


def test_llm_exhibits_section_rebuilt_from_structured_list_and_moved_last():
    exhibits = [ExhibitRef(n=1, wi="WI-13", label_ar="عقد التأسيس")]
    sections = [
        WriterSection(heading_ar="## الوقائع", body_md="بموجب العقد (مرفق رقم 1) و(WI-12)."),
        WriterSection(heading_ar="المرفقات", body_md="1. شيء قديم"),
        WriterSection(heading_ar="## الطلبات", body_md="رفض الدعوى."),
    ]
    res = _run(sections, exhibits)
    assert [s.heading_ar for s in res.sections] == ["## الوقائع", "## الطلبات", "## المرفقات"]
    assert res.sections[-1].body_md == "1. عقد التأسيس\n2. كشف حساب بنكي"


def test_bogus_exhibit_entries_dropped():
    exhibits = [
        ExhibitRef(n=1, wi="WI-13", label_ar="عقد التأسيس"),
        ExhibitRef(n=2, wi="WI-7", label_ar="بحث"),        # not an attachment
        ExhibitRef(n=1, wi="WI-9", label_ar="مكرر الرقم"),  # duplicate n
        ExhibitRef(n=4, wi="WI-13", label_ar="مكرر"),       # duplicate alias
    ]
    res = _run([WriterSection(heading_ar="## الوقائع", body_md="نص.")], exhibits)
    assert [(e.n, e.wi) for e in res.exhibits] == [(1, "WI-13")]
    assert res.stats.exhibits_dropped == 3


def test_contract_gets_no_exhibits_section_but_aliases_still_rewritten():
    res = _run(
        [WriterSection(heading_ar="## التمهيد", body_md="وفق العقد (WI-13).")],
        subtype="contract",
    )
    assert _body(res) == "وفق العقد (مرفق رقم 1)."
    assert all(s.heading_ar != "## المرفقات" for s in res.sections)


def test_title_is_sanitized():
    res = _run([WriterSection(heading_ar="## أ", body_md="ب")], title="مذكرة (WI-7)")
    assert res.title == "مذكرة"


def test_idempotent():
    first = _run([WriterSection(heading_ar="## الوقائع", body_md=LEAKY_BODY)])
    second = sanitize_document(
        title=first.title, sections=first.sections, exhibits=first.exhibits,
        aliases=ALIASES, subtype="defense_brief",
    )
    assert [s.model_dump() for s in second.sections] == [s.model_dump() for s in first.sections]
    assert second.stats.total_leaks == 0


def test_metadata_guide_and_chat_annotation():
    exhibits = [
        ExhibitRef(n=1, wi="WI-13", label_ar="عقد التأسيس"),
        ExhibitRef(n=2, wi="WI-8", label_ar="المخالصة النهائية", also_wi=["WI-2"]),
    ]
    meta = exhibits_metadata(exhibits, ALIASES)
    assert meta[0] == {"n": 1, "wi": "WI-13", "item_id": "i13", "label_ar": "عقد التأسيس", "also_wi": []}

    guide = format_exhibit_guide_ar(meta)
    lines = guide.splitlines()
    assert lines[0] == "**ما تُرفقه مع المستند:**"
    assert lines[1] == "• مرفق (1) — عقد التأسيس ← WI-13"
    assert "مرفوع أيضاً باسم WI-2" in lines[2]
    assert format_exhibit_guide_ar([]) == ""

    bullet = "صرف المبلغ بموجب المخالصة (WI-2) والبحث (WI-7)."
    once = annotate_chat_aliases(bullet, meta)
    assert once == "صرف المبلغ بموجب المخالصة (مرفق 2 · WI-2) والبحث (WI-7)."
    assert annotate_chat_aliases(once, meta) == once


def test_renumbered_to_first_mention_like_live_run():
    """Live run 2026-09-28: the writer cited «مرفق رقم 4» first. The document
    must read 1, 2, 3 … and the LLM's chat text must follow the same map."""
    exhibits = [
        ExhibitRef(n=1, wi="WI-9", label_ar="عقود العمل"),
        ExhibitRef(n=2, wi="WI-12", label_ar="كشف الحساب"),
        ExhibitRef(n=3, wi="WI-8", label_ar="المخالصة"),
        ExhibitRef(n=4, wi="WI-13", label_ar="عقد التأسيس"),
    ]
    sections = [
        WriterSection(heading_ar="## الوقائع", body_md=(
            "الصلاحيات (مرفق رقم 4). العقود (مرفق رقم 1). "
            "الصرف (مرفق رقم 2). المخالصة (مرفق رقم 3). وانظر المرفق رقم (4)."
        )),
        WriterSection(heading_ar="## المرفقات", body_md="1. قديم"),
    ]
    res = _run(sections, exhibits)
    assert res.sections[0].body_md == (
        "الصلاحيات (مرفق رقم 1). العقود (مرفق رقم 2). "
        "الصرف (مرفق رقم 3). المخالصة (مرفق رقم 4). وانظر المرفق رقم (1)."
    )
    assert res.sections[-1].body_md == "1. عقد التأسيس\n2. عقود العمل\n3. كشف الحساب\n4. المخالصة"
    assert renumber_chat_refs("المخالصة (مرفق 3) وعقد التأسيس (مرفق رقم 4)", res.renumber) == (
        "المخالصة (مرفق 4) وعقد التأسيس (مرفق رقم 1)"
    )
    # Already in order → no renumbering, and idempotent on the output.
    again = _run(res.sections, res.exhibits)
    assert again.renumber == {} and not again.stats.renumbered


def test_arabic_indic_document_keeps_its_digit_script():
    """Live run 2026-09-28: the writer wrote «مرفق رقم ١». Renumbered refs, the
    «المرفقات» list and leaked-alias rewrites must not switch to Latin."""
    exhibits = [
        ExhibitRef(n=1, wi="WI-9", label_ar="عقود العمل"),
        ExhibitRef(n=2, wi="WI-13", label_ar="عقد التأسيس"),
    ]
    sections = [WriterSection(heading_ar="## الوقائع", body_md=(
        "الصلاحيات (مرفق رقم ٢) في ١٤٤٦هـ. العقود (مرفق رقم ١) والصرف (WI-12)."
    ))]
    res = _run(sections, exhibits)
    assert res.sections[0].body_md == (
        "الصلاحيات (مرفق رقم ١) في ١٤٤٦هـ. العقود (مرفق رقم ٢) والصرف (مرفق رقم ٣)."
    )
    assert res.sections[-1].body_md == "١. عقد التأسيس\n٢. عقود العمل\n٣. كشف حساب بنكي"
