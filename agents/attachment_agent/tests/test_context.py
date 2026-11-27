"""Loading and the outline: sections, folded boilerplate, the angle map, the cap,
short files whole, which files are picked."""
from __future__ import annotations

from agents.attachment_agent import context as ctx_mod
from agents.attachment_agent.context import (
    conversation_rows,
    load_context,
    load_recent_messages,
    outline_lines,
    render_files,
    render_threads,
    render_turn_message,
)
from agents.attachment_agent.deps import FileDoc, SectionRow
from agents.attachment_agent.models import AttachmentAgentCall
from agents.attachment_agent.text import MaskedText

from .fakes import (
    CONVO,
    LEASE,
    PAGE_STARTS,
    USER,
    angle_map,
    attachment,
    big_text,
    lease_sections,
    make_db,
    segment_map,
)


def _call(**kw) -> AttachmentAgentCall:
    return AttachmentAgentCall(user_id=USER, conversation_id=CONVO, user_message="متى يبدأ العقد؟", **kw)


def _sec(seq: int, value: str, *, what: str = "", owner: str = "", page: int | None = None, label: str = "x") -> SectionRow:
    return SectionRow(seq=seq, label=label, value=value, what=what, owner=owner, brought_by="",
                      char_start=seq * 10, char_end=seq * 10 + 10, page_from=page, page_to=page)


def _file(sections: list[SectionRow], text: str = "x" * 200) -> FileDoc:
    return FileDoc(label="F1", item_id="i1", wi_seq=3, title="ملف", doc_type="judgment", summary="",
                   view=MaskedText(text), sections=sections)


# -- outline ------------------------------------------------------------------


def test_outline_one_line_per_section_and_boilerplate_runs_folded():
    f = _file([
        _sec(1, "metadata", what="بيانات الدعوى", owner="المحكمة", page=1, label="header"),
        _sec(2, "boilerplate", page=1),
        _sec(3, "boilerplate", page=2),
        _sec(4, "repeat", page=2),
        _sec(5, "core", what="طلبات المدعي", owner="المدعي", page=3, label="claim"),
        _sec(6, "boilerplate", what="ترويسة", page=4, label="letterhead"),
    ])
    lines = outline_lines(f)
    assert lines == [
        "F1·S1 · header · metadata · بيانات الدعوى · المحكمة · p 1",
        "F1·S2–S4 · 3 sections: boilerplate ×2, repeat ×1 · p 1–2",
        "F1·S5 · claim · core · طلبات المدعي · المدعي · p 3",
        # a single boilerplate section keeps its own line
        "F1·S6 · letterhead · boilerplate · ترويسة · p 4",
    ]


def test_outline_capped_keeps_core_and_metadata_and_counts_the_rest():
    f = _file([
        _sec(1, "metadata", what="بيانات", page=1),
        _sec(2, "supporting", what="سرد", page=1),
        _sec(3, "supporting", what="سرد آخر", page=2),
        _sec(4, "boilerplate", page=2),
        _sec(5, "core", what="الحكم", page=3),
    ])
    lines = outline_lines(f, capped=True)
    assert lines == [
        "F1·S1 · x · metadata · بيانات · p 1",
        "F1·S2–S4 · 3 sections not listed: supporting ×2, boilerplate ×1 · p 1–2",
        "F1·S5 · x · core · الحكم · p 3",
    ]


def test_render_files_applies_the_cap(monkeypatch):
    sections = [_sec(i, "core" if i % 5 == 0 else "supporting", what="و" * 80, page=i) for i in range(1, 41)]
    f = _file(sections, text="ن" * 20_000)
    full = render_files([f])
    assert full.count("F1·S") == 40
    monkeypatch.setattr(ctx_mod, "OUTLINE_MAX_CHARS", 2_000)
    f2 = _file(sections, text="ن" * 20_000)
    capped = render_files([f2])
    assert "F1·S5 · x · core" in capped
    assert "F1·S2 · x · supporting" not in capped
    assert "sections not listed: supporting ×4" in capped


def test_render_files_hard_cut_when_even_capped_outline_is_too_long(monkeypatch):
    sections = [_sec(i, "core", what="و" * 80, page=i) for i in range(1, 41)]
    monkeypatch.setattr(ctx_mod, "OUTLINE_MAX_CHARS", 1_000)
    out = render_files([_file(sections, text="ن" * 20_000)])
    assert "[outline cut here" in out
    assert "use find" in out


# -- the angle map --------------------------------------------------------------


def test_threads_block_maps_refs_to_file_labels():
    files = {"i1": _file([_sec(2, "core"), _sec(5, "core")])}
    stored = angle_map(
        "map1", ["i1", "i9"],
        [
            {"title": "بطلان الشرط", "for_side": "respondent", "for_party": "شركة الأفق",
             "strength": "high", "weakness": "لا يوجد توقيع", "argument": "الشرط باطل لأن…",
             "sections": ["D1.S2", "D2.S4"]},
            {"title": "التقادم", "sections": ["D1.S5"]},
        ],
        client={"side": "respondent", "party": "شركة الأفق", "basis": "المستأجر في العقد"},
        gaps=[{"kind": "missing_document", "issue": "لا يوجد إشعار الإخلاء", "sections": ["D1.S5"]}],
    )
    block = render_threads(stored, files, None)
    assert block.splitlines()[0] == "## Threads «الخيوط» — analysis of F1 (+1 file not given here)"
    assert "Client: respondent «شركة الأفق» — المستأجر في العقد" in block
    assert ("A1 · «بطلان الشرط» · for: respondent «شركة الأفق» · strength: high · "
            "sections: F1·S2 (+1 in files not given here)") in block
    assert "   weakness: لا يوجد توقيع" in block
    assert "A2 · «التقادم» · sections: F1·S5" in block
    assert "G1 · missing_document: لا يوجد إشعار الإخلاء · sections: F1·S5" in block


# -- loading -------------------------------------------------------------------------


def _lease_db(**kw):
    big = big_text()
    rows = [
        attachment("i1", "عقد الإيجار", LEASE, wi_seq=1, created_at="2026-11-01T10:00:00"),
        attachment("i2", "مذكرة الدفاع", big, wi_seq=2, created_at="2026-11-01T11:00:00", doc_type="memo"),
        attachment("i3", "تقرير بحث", "نص تقرير", wi_seq=3, kind="agent_search"),
        attachment("i4", "ملف محذوف", "نص", wi_seq=4, deleted_at="2026-11-02T00:00:00"),
        attachment("i5", "ملف محادثة أخرى", "نص", conversation_id="c2"),
    ]
    big_secs = [{"seq": 1, "char_start": 0, "char_end": len(big) // 2, "label": "facts", "value": "core",
                 "what": "الوقائع"},
                {"seq": 2, "char_start": len(big) // 2, "char_end": len(big), "label": "defence",
                 "value": "core", "what": "الدفوع"}]
    return make_db(
        rows,
        maps=[segment_map("i1", LEASE, lease_sections()), segment_map("i2", big, big_secs)],
        pages={"i1": PAGE_STARTS},
        **kw,
    )


def test_empty_item_ids_means_every_live_attachment_of_the_conversation():
    loaded = load_context(_lease_db(), _call())
    assert [(f.label, f.item_id) for f in loaded.files] == [("F1", "i1"), ("F2", "i2")]
    assert loaded.files[0].wi == "WI-1" and loaded.files[0].pages[1] == (2, PAGE_STARTS[1])
    assert [s.seq for s in loaded.files[0].sections] == [1, 2, 3, 4, 5, 6]
    assert loaded.files[1].pages == []


def test_picked_item_ids_only():
    loaded = load_context(_lease_db(), _call(item_ids=["i2", "i3"]))
    assert [f.item_id for f in loaded.files] == ["i2"]


def test_text_comes_from_user_files_when_the_read_switch_is_on():
    """A library file attached here: the pointer's copy is stale, the file holds the
    text, and the segment map and pages hang on the file id (migration 223)."""
    from shared.files import flags as files_flags

    pointer = {**attachment("i7", "نسخة قديمة", ""), "file_id": "f1"}
    db = make_db(
        [pointer, attachment("f1", "عقد الإيجار", LEASE, conversation_id="c0")],
        maps=[segment_map("f1", LEASE, lease_sections())],
        pages={"f1": PAGE_STARTS},
    )
    db.tables["user_files"] = [{"file_id": "f1", "user_id": USER, "title": "عقد الإيجار", "summary": "ملخص",
                                "content_md": LEASE, "doc_type": "contract", "deleted_at": None}]
    with files_flags.override(read=True):
        loaded = load_context(db, _call())
    [f] = loaded.files
    assert (f.item_id, f.title, f.summary, f.chars) == ("i7", "عقد الإيجار", "ملخص", len(LEASE))
    assert [s.seq for s in f.sections] == [1, 2, 3, 4, 5, 6] and len(f.pages) == 3


def test_attachment_without_text_is_named_not_read():
    db = make_db([attachment("i1", "صورة العقد", ""), attachment("i2", "عقد", LEASE)])
    loaded = load_context(db, _call())
    assert [f.item_id for f in loaded.files] == ["i2"]
    assert loaded.without_text == ["صورة العقد"]


def test_short_file_whole_long_file_outline_in_the_turn_message():
    loaded = load_context(_lease_db(), _call())
    message = render_turn_message(_call(), loaded, [{"role": "user", "content": "مرحبا"}])
    lease, memo = loaded.files
    assert lease.given_whole and lease.read_whole()
    assert '<text file="F1">' in message and "سجل تجاري رقم 1010234567" in message
    assert "## F1 — «عقد الإيجار» · WI-1 · contract · " in message and "3 pages · whole text below" in message
    assert not memo.given_whole and not memo.seen and not memo.read_whole()
    assert "## F2 — «مذكرة الدفاع» · WI-2 · memo" in message and "outline of 2 sections" in message
    assert "F2·S1 · facts · core · الوقائع" in message
    assert "<threads>none" in message
    assert '<message role="user">مرحبا</message>' in message


def test_whole_short_file_keeps_its_outline_for_thread_refs():
    loaded = load_context(_lease_db(), _call())
    message = render_turn_message(_call(), loaded, [])
    assert "whole text below + outline of 6 sections" in message
    assert "F1·S3 · rent · core · دفع الأجرة · المؤجر · p 2" in message


def test_current_message_is_masked_even_if_passed_unmasked():
    import random

    from backend.app.services import masking_service
    from shared.privacy.codec import PrivacyCodec

    codec = PrivacyCodec(rng=random.Random(3))
    token = masking_service.set_active_codec(codec)
    try:
        call = AttachmentAgentCall(user_id=USER, conversation_id=CONVO, user_message="ما رقم السجل 1010234567؟")
        message = render_turn_message(call, load_context(_lease_db(), call), [])
    finally:
        masking_service.reset_active_codec(token)
    assert "1010234567" not in message
    assert codec.real_to_fake["1010234567"] in message


def test_file_with_no_segment_map_is_read_with_find_and_pages():
    big = big_text()
    loaded = load_context(make_db([attachment("i1", "مذكرة", big)]), _call())
    message = render_turn_message(_call(), loaded, [])
    assert "no outline: read it with find and read_pages" in message
    assert "no page numbers (" in message


def test_angle_map_is_loaded_once_per_map():
    stored = angle_map("map1", ["i1", "i2"], [{"title": "خيط", "sections": ["D1.S2", "D2.S1"]}])
    loaded = load_context(_lease_db(angle_maps=[stored]), _call())
    assert len(loaded.angle_maps) == 1 and len(loaded.threads) == 1
    assert "sections: F1·S2, F2·S1" in loaded.threads[0]
    assert all(f.has_angle_map for f in loaded.files)


def test_everything_reaching_the_model_is_escaped():
    db = make_db([attachment("i1", "عقد <b>", "نص فيه <script>x</script> & رمز")])
    loaded = load_context(db, _call())
    message = render_turn_message(_call(), loaded, [])
    assert "<script>" not in message and "&lt;script&gt;" in message


# -- recent messages -------------------------------------------------------------------


def test_recent_messages_stop_before_the_current_message():
    msgs = [
        {"message_id": f"m{i}", "conversation_id": CONVO, "role": role, "content": content,
         "created_at": f"2026-11-01T10:0{i}:00"}
        for i, (role, content) in enumerate([
            ("user", "سؤال أول"), ("assistant", "جواب أول"), ("user", "متى يبدأ العقد؟"),
            ("assistant", ""),
        ])
    ]
    db = make_db([], messages=msgs)
    rows = load_recent_messages(db, _call(message_id="m2"))
    assert rows == [{"role": "user", "content": "سؤال أول"}, {"role": "assistant", "content": "جواب أول"}]
    # no message id: the trailing copy of the current message is dropped
    rows = load_recent_messages(db, _call())
    assert [r["content"] for r in rows] == ["سؤال أول", "جواب أول"]


def test_router_rows_drop_the_current_message():
    rows = conversation_rows(
        [{"role": "user", "content": "أ"}, {"role": "assistant", "content": "ب"},
         {"role": "user", "content": "متى يبدأ العقد؟"}],
        "متى يبدأ العقد؟",
    )
    assert [r["content"] for r in rows] == ["أ", "ب"]
