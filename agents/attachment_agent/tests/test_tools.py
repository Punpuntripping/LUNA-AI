"""find / read_sections / read_pages: folding, locations, caps, the page-less
fallback, and the record of what the model has seen."""
from __future__ import annotations

from agents.attachment_agent import tools as tools_mod
from agents.attachment_agent.context import load_context
from agents.attachment_agent.deps import AttachmentDeps
from agents.attachment_agent.models import AttachmentAgentCall
from agents.attachment_agent.tools import find_text, parse_refs, read_pages_text, read_sections_text

from .fakes import (
    CONVO,
    LEASE,
    PAGE_1,
    PAGE_2,
    PAGE_STARTS,
    USER,
    attachment,
    big_text,
    lease_sections,
    make_db,
    segment_map,
    span_of,
)


def _deps(db, **kw) -> AttachmentDeps:
    call = AttachmentAgentCall(user_id=USER, conversation_id=CONVO, user_message="س", **kw)
    loaded = load_context(db, call)
    return AttachmentDeps(supabase=db, user_id=USER, conversation_id=CONVO,
                          files={f.label: f for f in loaded.files})


def _lease_deps(*, pages: bool = True) -> AttachmentDeps:
    db = make_db(
        [attachment("i1", "عقد الإيجار", LEASE, wi_seq=4)],
        maps=[segment_map("i1", LEASE, lease_sections())],
        pages={"i1": PAGE_STARTS} if pages else None,
    )
    return _deps(db)


# -- find ------------------------------------------------------------------------


def test_find_latin_digits_hit_arabic_indic_date_with_section_and_page():
    deps = _lease_deps()
    out = find_text(deps, "25/3/1447")
    assert out.startswith("find «25/3/1447» in F1: 1 hit.")
    assert "[F1·S2 · p 2]" in out
    assert "٢٥/٣/١٤٤٧" in out
    assert deps.searched == ["25/3/1447"]
    f = deps.files["F1"]
    start, end = span_of(LEASE, "٢٥/٣/١٤٤٧")
    assert f.was_seen(start, end)
    assert deps.chars_read > 0


def test_find_ignores_tashkeel_and_letter_forms():
    deps = _lease_deps()
    out = find_text(deps, "يدفع المستاجر الاجره")
    assert "[F1·S3 · p 2]" in out
    assert "يَدْفَعُ المستأجرُ الأجرةَ" in out


def test_find_no_hit_says_the_full_text_was_searched():
    deps = _lease_deps()
    out = find_text(deps, "غرامة تأخير")
    assert out.startswith("No hit for «غرامة تأخير» in the full text of F1.")
    assert deps.searched == ["غرامة تأخير"]
    assert not deps.files["F1"].seen


def test_find_caps_hits_and_says_so(monkeypatch):
    monkeypatch.setattr(tools_mod, "FIND_MAX_HITS", 5)
    text = "\n".join(f"فقرة {i}: " + "حشو " * 80 + "المدعي يطالب" for i in range(12))
    deps = _deps(make_db([attachment("i1", "مذكرة", text)]))
    out = find_text(deps, "المدعي يطالب")
    assert "12 hits, showing 5" in out
    assert out.count("\n[F1") == 5


def test_find_part_location_when_the_file_has_no_pages():
    deps = _deps(make_db([attachment("i1", "مذكرة", big_text())]))
    out = find_text(deps, "أقر المدعى عليه باستلام المبلغ")
    f = deps.files["F1"]
    assert f"part {len(f.parts)} of {len(f.parts)}" in out


def test_find_unknown_file():
    deps = _lease_deps()
    assert find_text(deps, "الأجرة", ["F9"]).startswith("Unknown file(s): F9")


# -- read_sections -----------------------------------------------------------------


def test_parse_refs_accepts_the_spellings():
    deps = _lease_deps()
    wanted, bad = parse_refs(deps, ["F1·S2", "f1.s3", "WI-4·S5", "S1", "F1·S2–S3", "F1·S99", "nonsense"])
    assert [s.seq for _f, s in wanted] == [2, 3, 5, 1]
    assert bad == ["F1·S99", "nonsense"]


def test_read_sections_returns_the_text_and_marks_it_seen():
    deps = _lease_deps()
    out = read_sections_text(deps, ["F1·S2", "F1·S5"])
    assert "### F1·S2 · term · core · مدة العقد · المؤجر · p 2" in out
    assert "البند الأول: مدة العقد سنة تبدأ من ٢٥/٣/١٤٤٧هـ." in out
    assert "البند الثالث: لا يجوز للمستأجر التأجير من الباطن" in out
    f = deps.files["F1"]
    assert f.was_seen(*span_of(LEASE, "مدة العقد سنة"))
    assert not f.was_seen(*span_of(LEASE, "يَدْفَعُ"))


def test_read_sections_joined_section_reads_the_stored_text():
    sections = lease_sections()
    # S2 + S3 joined across a letterhead: stored text skips the line between.
    s_start = sections[1]["char_start"]
    s_end = sections[2]["char_end"]
    joined = {"seq": 2, "char_start": s_start, "char_end": s_end, "label": "terms", "value": "core",
              "content_md": "البند الأول: مدة العقد سنة.\nالبند الثاني: الأجرة."}
    db = make_db([attachment("i1", "عقد", LEASE)], maps=[segment_map("i1", LEASE, [sections[0], joined])])
    deps = _deps(db)
    out = read_sections_text(deps, ["F1·S2"])
    assert "(page letterheads inside it left out)" in out
    assert "البند الأول: مدة العقد سنة.\nالبند الثاني: الأجرة." in out


def test_read_sections_cap(monkeypatch):
    monkeypatch.setattr(tools_mod, "READ_MAX_CHARS", 60)
    deps = _lease_deps()
    out = read_sections_text(deps, ["F1·S1", "F1·S2", "F1·S3"])
    assert "[cut here: the section goes on (pages 1–1)" in out
    assert "Not read (over this call's 60-character limit): F1·S2, F1·S3" in out


def test_read_sections_unknown_refs_hint_pages_for_files_without_sections():
    db = make_db([attachment("i1", "عقد", LEASE), attachment("i2", "مذكرة", big_text(), created_at="2026-11-02")],
                 maps=[segment_map("i1", LEASE, lease_sections())])
    deps = _deps(db)
    out = read_sections_text(deps, ["F2·S1"])
    assert "Unknown section refs: F2·S1" in out
    assert "F2 has no sections: read with find / read_pages." in out


# -- read_pages ------------------------------------------------------------------


def test_read_pages_with_page_rows_returns_exact_page_slices():
    deps = _lease_deps()
    out = read_pages_text(deps, "F1", 1, 2)
    assert "## F1 — page 1 of 3\n" + PAGE_1 in out
    assert "## F1 — page 2 of 3\n" + PAGE_2 in out
    assert "page 3" not in out
    f = deps.files["F1"]
    assert f.was_seen(*span_of(LEASE, PAGE_2))
    assert not f.read_whole()
    read_pages_text(deps, "WI-4", 3)
    assert f.read_whole()


def test_read_pages_out_of_range():
    deps = _lease_deps()
    assert read_pages_text(deps, "F1", 7, 9) == "F1 has pages 1–3."


def test_read_pages_without_page_rows_reads_parts():
    text = big_text(600)
    deps = _deps(make_db([attachment("i1", "مذكرة", text)]))
    f = deps.files["F1"]
    total = len(f.parts)
    assert total >= 3
    assert all(end - start <= 6_000 for start, end in f.parts)
    assert f.parts[0][0] == 0 and f.parts[-1][1] == len(text)
    assert all(text[end - 1] == "\n" for _s, end in f.parts[:-1])  # cut at a line break
    out = read_pages_text(deps, "F1", 2)
    assert out.startswith(f"## F1 — part 2 of {total} (this file has no page numbers)\n")
    assert f.was_seen(*f.parts[1]) and not f.was_seen(*f.parts[0])
    assert read_pages_text(deps, "F1", total + 3) == f"F1 has no page numbers; it is read in parts 1–{total}."


def test_read_pages_cap_stops_and_says_where(monkeypatch):
    monkeypatch.setattr(tools_mod, "READ_MAX_CHARS", len(PAGE_1) + 5)
    deps = _lease_deps()
    out = read_pages_text(deps, "F1", 1, 3)
    assert "## F1 — page 1 of 3" in out
    assert "call read_pages again from page 3." in out


def test_read_pages_unknown_file():
    deps = _lease_deps()
    assert read_pages_text(deps, "F7", 1).startswith("Unknown file 'F7'")
