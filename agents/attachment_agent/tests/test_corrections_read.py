"""The read side sees the user's text fixes: the model, find, the read tools and
quote checks all read the corrected text, while sections and pages — offsets in
the ORIGINAL text — still land right (``text.FixMap`` under ``MaskedText``)."""
from __future__ import annotations

import random

import pytest

from agents.attachment_agent.text import FixMap, MaskedText
from agents.attachment_agent.tools import find_text, read_pages_text, read_sections_text
from agents.attachment_agent.verify import locate_quote, quote_out
from agents.attachment_agent.output import QuoteLLM
from backend.app.services import masking_service
from shared.attachments import text_corrections
from shared.attachments.text_corrections import TextCorrection, apply_text_corrections
from shared.privacy.codec import PrivacyCodec

from .fakes import LEASE, PAGE_STARTS, USER, attachment, edit_deps, lease_sections, make_db, segment_map, span_of


@pytest.fixture(autouse=True)
def _no_backoff():
    text_corrections.reset_backoff()
    yield
    text_corrections.reset_backoff()


def _fix(wrong: str, right: str, at: str = "2026-11-27T10:00:00", i: int = 1) -> TextCorrection:
    return TextCorrection(id=f"c{i}", file_id="i1", wrong=wrong, right=right, occurrences=1, created_at=at)


# -- the offset map --------------------------------------------------------------------


def test_fixmap_matches_apply_text_corrections_and_maps_both_ways():
    original = "أ محمد ب محمد ج"
    fixes = [_fix("محمد", "محمد بن علي")]
    fm = FixMap(original, fixes)
    assert fm.text == apply_text_corrections(original, fixes) == "أ محمد بن علي ب محمد بن علي ج"
    # every character outside a fix maps exactly, both ways
    for word in ("أ ", " ب ", " ج"):
        o = original.index(word)
        c = fm.text.index(word)
        assert fm.to_corrected(o) == c and fm.to_orig(c) == o
    # a replaced span's start and end map exactly; inside it clamps to the span
    o_start, o_end = span_of(original, "محمد")
    c_start = fm.text.index("محمد بن علي")
    assert fm.to_corrected(o_start) == c_start and fm.to_corrected(o_end) == c_start + len("محمد بن علي")
    assert fm.to_orig(c_start + len("محمد بن علي")) == o_end
    assert fm.to_orig(c_start + 9) == o_end                    # inside the longer span → its end
    assert fm.to_orig(len(fm.text)) == len(original)


def test_fixmap_composes_fixes_in_time_order_and_shrinking_ones():
    original = "قال خالد: إن خالداً حضر. وقال سعيد."
    fixes = [_fix("سعيد", "سعد", "2026-11-27T10:05:00", 2), _fix("خالد", "خالد بن سعيد", "2026-11-27T10:00:00", 1)]
    fm = FixMap(original, fixes)
    assert fm.text == apply_text_corrections(original, fixes)
    assert fm.text == "قال خالد بن سعد: إن خالد بن سعداً حضر. وقال سعد."
    tail = " حضر. وقال "
    assert fm.to_orig(fm.text.index(tail)) == original.index(tail)
    assert fm.to_corrected(original.index(tail)) == fm.text.index(tail)


def test_masked_text_without_fixes_is_unchanged():
    view = MaskedText("نص", None, [])
    assert (view.original, view.corrected, view.text) == ("نص", "نص", "نص")


# -- loading with a stored fix ----------------------------------------------------------------


LONGER = "محمد بن عبدالله"


def _db():
    db = make_db([attachment("i1", "عقد الإيجار", LEASE, wi_seq=1)],
                 maps=[segment_map("i1", LEASE, lease_sections())], pages={"i1": PAGE_STARTS})
    db.tables["attachment_text_corrections"] = [
        {"id": "t1", "file_id": "i1", "user_id": USER, "wrong_text": "محمد", "right_text": LONGER,
         "occurrences": 1, "created_at": "2026-11-27T10:00:00", "reverted_at": None},
        {"id": "t2", "file_id": "i1", "user_id": USER, "wrong_text": "شركة", "right_text": "مؤسسة",
         "occurrences": 1, "created_at": "2026-11-27T10:01:00", "reverted_at": "2026-11-27T10:02:00"},
        {"id": "t3", "file_id": "other", "user_id": USER, "wrong_text": "البند", "right_text": "المادة",
         "occurrences": 3, "created_at": "2026-11-27T10:03:00", "reverted_at": None},
    ]
    return db


def test_the_model_reads_the_corrected_text_and_sections_stay_in_place():
    deps = edit_deps(_db(), "متى يبدأ العقد؟")
    f = deps.files["F1"]
    assert f.file_id == "i1" and [c.id for c in f.corrections] == ["t1"]     # reverted / other file not applied
    assert f"المؤجر: {LONGER} أحمد العتيبي" in f.view.text and "شركة الأفق" in f.view.text
    assert "البند الثالث" in f.view.text
    # find hits the corrected words, in the right section and page
    out = find_text(deps, "محمد بن عبدالله")
    assert "[F1·S1 · p 1]" in out
    # a hit after the (longer) fix still lands in its own section and page
    out = find_text(deps, "التأجير من الباطن")
    assert "[F1·S5 · p 3]" in out
    # sections are cut from the corrected text by their ORIGINAL offsets
    s1 = read_sections_text(deps, ["F1·S1"])
    assert f"المؤجر: {LONGER} أحمد العتيبي" in s1 and "سجل تجاري رقم 1010234567" in s1
    body = read_sections_text(deps, ["F1·S2"]).split("\n", 1)[1]
    assert body == "البند الأول: مدة العقد سنة تبدأ من ٢٥/٣/١٤٤٧هـ."
    assert read_pages_text(deps, "F1", 3).startswith("## F1 — page 3 of 3\nالبند الثالث")


def test_quotes_of_the_corrected_text_verify_and_locate_to_the_original():
    deps = edit_deps(_db(), "س")
    read_sections_text(deps, ["F1·S1", "F1·S5"])
    loc, why = locate_quote(deps, QuoteLLM(file="F1", text=f"المؤجر: {LONGER} أحمد العتيبي"))
    assert why == "" and quote_out(loc).section_ref == "S1" and quote_out(loc).page == 1
    loc, _ = locate_quote(deps, QuoteLLM(file="F1", text="لا يجوز للمستأجر التأجير من الباطن"))
    q = quote_out(loc)
    assert (q.section_ref, q.page) == ("S5", 3)
    # the old (wrong) wording is no longer in the file the model reads
    assert locate_quote(deps, QuoteLLM(file="F1", text="المؤجر: محمد أحمد"))[0] is None


def test_joined_section_text_is_corrected_too():
    sections = lease_sections()
    sections[0]["content_md"] = "عقد إيجار\nالمؤجر: محمد أحمد العتيبي"   # letterheads left out
    db = _db()
    db.tables["attachment_segments"] = [r for r in db.tables["attachment_segments"] if r["seq"] != 1]
    from .fakes import segment_map as sm

    _map, rows = sm("i1", LEASE, sections)
    db.tables["attachment_segments"].append(rows[0])
    deps = edit_deps(db, "س")
    s1 = deps.files["F1"].section(1)
    assert s1.joined_text == f"عقد إيجار\nالمؤجر: {LONGER} أحمد العتيبي"
    # the fix check sees the section's edges in the corrected REAL text
    start, end = deps.files["F1"].real_section_bounds()[0]
    assert deps.files["F1"].real_text()[start:end].endswith("سجل تجاري رقم 1010234567")


def test_section_labels_are_masked_like_the_rest():
    codec = PrivacyCodec(rng=random.Random(8))
    token = masking_service.set_active_codec(codec)
    try:
        db = _db()
        next(r for r in db.tables["attachment_segments"] if r["seq"] == 1)["label"] = "قيد 1010234567"
        deps = edit_deps(db, "س")
        assert deps.files["F1"].section(1).label == f"قيد {codec.real_to_fake['1010234567']}"
    finally:
        masking_service.reset_active_codec(token)


def test_fixes_and_masking_together():
    codec = PrivacyCodec(rng=random.Random(4))
    token = masking_service.set_active_codec(codec)
    try:
        deps = edit_deps(_db(), "س")
        f = deps.files["F1"]
        assert LONGER in f.view.text and "1010234567" not in f.view.text
        assert codec.real_to_fake["lessor.office@mail.com"] in f.view.text
        out = find_text(deps, "التوقيع: المؤجر")
        assert "[F1·S6 · p 3]" in out     # after a longer fix AND a length-changing e-mail mask
        assert f.real_text() == apply_text_corrections(LEASE, f.corrections)   # checks run on REAL text
    finally:
        masking_service.reset_active_codec(token)


def test_a_failed_corrections_read_shows_the_stored_text():
    db = _db()
    db.fail.add("attachment_text_corrections")
    deps = edit_deps(db, "س")
    assert "المؤجر: محمد أحمد العتيبي" in deps.files["F1"].view.text
