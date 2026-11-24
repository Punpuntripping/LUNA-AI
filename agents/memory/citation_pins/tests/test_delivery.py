"""Official articles printed under the attachment segment that cites them.

Plan ``.claude/plans/completion_search.md`` §4.7, owner decisions C19, C22, C23 and the
"tests that must exist" list of §7. Synthetic text only.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

import agents.utils.attachment_sections as sections_mod
from agents.utils.attachment_delivery import (
    DEEP_SEARCH_VALUES,
    LOOSE_PINS_HEADING,
    PIN_ARTICLE_CAP,
    PIN_CUT_NOTE,
    PIN_DOC_NET,
    PINS_ONLY_VALUE,
    WRITER_VALUES,
    Section,
    attach_pins,
    has_kept,
    kept_chars,
    proportional_ratios,
    render_loose_pins,
    render_sections,
    sections_from_rows,
    writer_bodies,
)


@dataclass(frozen=True)
class Pin:
    char_start: int
    article_id: str
    title: str
    text: str


def sec(seq: int, start: int, end: int, value: str = "core", what: str = "وقائع") -> Section:
    return Section(seq=seq, value=value, what=f"{what} {seq}", text=f"نص المقطع {seq}", char_start=start, char_end=end)


A77 = Pin(120, "a77", "المادة 77 من نظام العمل", "نص المادة السابعة والسبعين.")
A80 = Pin(150, "a80", "المادة 80 من نظام العمل", "نص المادة الثمانين.")
SECTIONS = [sec(1, 0, 100), sec(2, 100, 200), sec(3, 200, 300)]


# --------------------------------------------------------------------------- #
# Where an article lands
# --------------------------------------------------------------------------- #


def test_an_article_goes_under_the_section_that_cites_it():
    out = attach_pins(SECTIONS, [A77])
    assert [len(s.pins) for s in out] == [0, 1, 0]
    text, _ = render_sections(out, DEEP_SEARCH_VALUES)
    assert text == (
        "〔وقائع 1〕\nنص المقطع 1\n\n"
        "〔وقائع 2〕\nنص المقطع 2\n\n"
        "〔من المكتبة النظامية، لا من المستند — المادة 77 من نظام العمل〕\nنص المادة السابعة والسبعين.\n\n"
        "〔وقائع 3〕\nنص المقطع 3"
    )


def test_cited_twice_in_one_section_it_is_printed_once():
    out = attach_pins(SECTIONS, [A77, Pin(180, "a77", A77.title, A77.text)])
    assert [p.article_id for p in out[1].pins] == ["a77"]


def test_cited_in_two_sections_it_is_printed_in_full_under_each():
    out = attach_pins(SECTIONS, [A77, Pin(250, "a77", A77.title, A77.text)])
    assert [len(s.pins) for s in out] == [0, 1, 1]
    text, _ = render_sections(out, DEEP_SEARCH_VALUES)
    assert text.count("نص المادة السابعة والسبعين.") == 2


def test_articles_keep_document_order_inside_a_section():
    out = attach_pins(SECTIONS, [A80, A77])
    assert [p.article_id for p in out[1].pins] == ["a77", "a80"]


def test_a_section_that_is_not_delivered_brings_no_pinned_text():
    sections = [sec(1, 0, 100), sec(2, 100, 200, value="supporting"), sec(3, 200, 300, value="boilerplate")]
    out = attach_pins(sections, [A77, Pin(250, "a80", A80.title, A80.text)])
    deep, _ = render_sections(out, DEEP_SEARCH_VALUES)
    assert "المكتبة النظامية" not in deep                 # deep search reads core only
    writer, _ = render_sections(out, WRITER_VALUES)
    assert "المادة 77" in writer and "المادة 80" not in writer


def test_a_position_outside_every_section_is_dropped():
    out = attach_pins(SECTIONS, [Pin(999, "a77", A77.title, A77.text)])
    assert all(not s.pins for s in out)


def test_no_pins_changes_nothing():
    plain, _ = render_sections(SECTIONS, DEEP_SEARCH_VALUES)
    assert render_sections(attach_pins(SECTIONS, []), DEEP_SEARCH_VALUES)[0] == plain
    assert attach_pins(SECTIONS, []) == SECTIONS


# --------------------------------------------------------------------------- #
# Size — 10,000 per article, 30,000 per document, outside the caps (C23)
# --------------------------------------------------------------------------- #


def test_one_article_is_cut_at_ten_thousand_characters():
    long = Pin(120, "big", "المادة 1 من نظام طويل", "كلمة " * 4_000)          # 20,000 characters
    out = attach_pins(SECTIONS, [long])
    text = out[1].pins[0].text
    assert text.endswith(PIN_CUT_NOTE)
    assert len(text) <= PIN_ARTICLE_CAP + len(PIN_CUT_NOTE) + 1
    assert PIN_ARTICLE_CAP == 10_000


def test_a_short_article_is_never_cut():
    assert attach_pins(SECTIONS, [A77])[1].pins[0].text == A77.text


def test_the_document_net_gives_every_article_the_same_share():
    pins = [Pin(10 + i * 60, f"a{i}", f"المادة {i}", "كلمة " * 1_800) for i in range(5)]   # 5 × 9,000
    out = attach_pins(SECTIONS, pins)
    texts = [p.text for s in out for p in s.pins]
    assert len(texts) == 5
    assert sum(len(t) for t in texts) <= PIN_DOC_NET + 5 * (len(PIN_CUT_NOTE) + 1)
    assert max(len(t) for t in texts) - min(len(t) for t in texts) < 20
    assert all(t.endswith(PIN_CUT_NOTE) for t in texts)


def test_the_caps_are_computed_exactly_as_before():
    with_pins = attach_pins(SECTIONS, [Pin(120, "big", "المادة 1", "كلمة " * 1_900)])
    assert kept_chars(with_pins, DEEP_SEARCH_VALUES) == kept_chars(SECTIONS, DEEP_SEARCH_VALUES)
    same = {"doc": kept_chars(SECTIONS, DEEP_SEARCH_VALUES)}
    assert proportional_ratios({"doc": kept_chars(with_pins, DEEP_SEARCH_VALUES)}, 0) == proportional_ratios(same, 0)


def test_trimming_a_section_does_not_trim_its_article():
    big = [Section(seq=1, value="core", what="وقائع", text="سطر من النص\n" * 200, char_start=0, char_end=5_000)]
    out = attach_pins(big, [Pin(10, "a77", A77.title, A77.text)])
    text, trimmed = render_sections(out, DEEP_SEARCH_VALUES, ratio=0.5)
    assert trimmed and A77.text in text


# --------------------------------------------------------------------------- #
# A document with no sections
# --------------------------------------------------------------------------- #


def test_no_sections_gives_one_carrier_with_each_article_once():
    out = attach_pins([], [A77, Pin(900, "a77", A77.title, A77.text), A80])
    assert len(out) == 1 and out[0].value == PINS_ONLY_VALUE
    assert [p.article_id for p in out[0].pins] == ["a77", "a80"]
    assert not has_kept(out, DEEP_SEARCH_VALUES) and not has_kept(out, WRITER_VALUES)
    assert render_sections(out, WRITER_VALUES) == ("", False)          # the carrier names nothing
    block = render_loose_pins(out)
    assert block.startswith(LOOSE_PINS_HEADING) and block.count("〔من المكتبة النظامية") == 2


def test_the_writer_gets_the_articles_after_a_short_document():
    carrier = attach_pins([], [A77])
    bodies, stats = writer_bodies({"doc": "خطاب قصير يشير إلى المادة 77 من نظام العمل."}, {"doc": carrier})
    assert bodies["doc"].startswith("خطاب قصير")
    assert bodies["doc"].endswith(A77.text) and LOOSE_PINS_HEADING in bodies["doc"]
    assert stats["segmented"] == []


def test_the_writer_gets_the_articles_after_a_long_document_with_no_map():
    content = "سطر من المستند\n" * 600                                       # over 5,000 characters
    bodies, stats = writer_bodies({"doc": content}, {"doc": attach_pins([], [A77])})
    assert stats["fallback"] == ["doc"] and bodies["doc"].endswith(A77.text)


def test_a_document_without_pins_is_byte_identical():
    content = "خطاب قصير."
    assert writer_bodies({"doc": content}, {})[0]["doc"] == content


def test_deep_search_gets_the_articles_after_a_document_with_no_map():
    from types import SimpleNamespace

    from agents.deep_search_v4.shared.case_documents import build_case_document_blocks_segmented

    def doc(item_id: str, content: str):
        return SimpleNamespace(item_id=item_id, title="خطاب", summary="ملخص", content_md=content,
                               metadata={"doc_type": "letter"}, wi_seq=1, kind="attachment")

    docs = [doc("d1", "خطاب قصير يشير إلى المادة 77."), doc("d2", "مستند آخر.")]
    sections = {"d1": attach_pins([], [A77]), "d2": attach_pins([], [A80])}
    blocks = build_case_document_blocks_segmented(docs, ["d1"], sections)
    by_id = {b.source_item_id: b.body for b in blocks}
    assert LOOSE_PINS_HEADING in by_id["d1"] and A77.text in by_id["d1"]
    assert "المكتبة النظامية" not in by_id["d2"]       # only its summary is shown — no text, no articles


# --------------------------------------------------------------------------- #
# Rows → sections, masking, and the combined wait
# --------------------------------------------------------------------------- #


def test_sections_carry_their_character_range():
    rows = [{"seq": 2, "value": "core", "what": "ب", "content_md": "نص", "char_start": 50, "char_end": 90},
            {"seq": 1, "value": "core", "what": "أ", "content_md": "نص"}]
    out = sections_from_rows(rows)
    assert [(s.seq, s.char_start, s.char_end) for s in out] == [(1, -1, -1), (2, 50, 90)]


def test_pinned_text_passes_the_encoder_unchanged(monkeypatch):
    import backend.app.services.masking_service as masking

    monkeypatch.setattr(masking, "active_codec", lambda: object())
    monkeypatch.setattr(masking, "encode_active", lambda text: f"<{text}>")
    statute = Pin(120, "a1", "المادة 1", "السجل رقم 1010123456 يقيد خلال 30 يوماً.")
    out = sections_mod._encode_sections(attach_pins(SECTIONS, [statute]))
    assert out[1].text == "<نص المقطع 2>" and out[1].what == "<وقائع 2>"
    assert out[1].pins[0].text == statute.text            # official text: digits untouched
    assert (out[1].char_start, out[1].char_end) == (100, 200)


class _Stored:
    def __init__(self, segments):
        self.segments = segments


def _wire(monkeypatch, *, pins_on: bool, maps: dict, pins: dict):
    waited: list[str] = []

    async def ensure_ready(helper, ids, **_kw):
        waited.append(helper)
        return {i: "ready" for i in ids}

    import agents.helpers as helpers
    import agents.memory.attachment_segmenter.store as seg_store
    import agents.memory.citation_pins.store as pin_store

    monkeypatch.setattr(helpers, "ensure_ready", ensure_ready)
    monkeypatch.setattr(seg_store, "load_segment_maps", lambda sb, ids, user_id=None: maps)
    monkeypatch.setattr(pin_store, "load_pins", lambda sb, ids, user_id=None: pins)
    monkeypatch.setattr(sections_mod, "_pins_on", lambda user_id: pins_on)
    return waited


def test_one_combined_wait_then_pins_are_attached(monkeypatch):
    rows = [{"seq": 1, "value": "core", "what": "وقائع", "content_md": "نص", "char_start": 0, "char_end": 200}]
    waited = _wire(monkeypatch, pins_on=True, maps={"i1": _Stored(rows)}, pins={"i1": [A77], "i2": [A80]})
    out, telemetry = asyncio.run(sections_mod.wait_and_load_sections(
        ["i1", "i2"], consumer="writer", supabase=object(), user_id="u1"))
    assert sorted(waited) == ["citation_pins", "segmenter"]
    assert [p.article_id for p in out["i1"][0].pins] == ["a77"]
    assert out["i2"][0].value == PINS_ONLY_VALUE          # no map: a carrier for the fallback
    assert telemetry["pins.items"] == 2 and telemetry["pins.articles"] == 2


def test_with_the_flag_off_nothing_about_pins_runs(monkeypatch):
    rows = [{"seq": 1, "value": "core", "what": "وقائع", "content_md": "نص", "char_start": 0, "char_end": 200}]
    waited = _wire(monkeypatch, pins_on=False, maps={"i1": _Stored(rows)}, pins={"i1": [A77]})
    out, telemetry = asyncio.run(sections_mod.wait_and_load_sections(
        ["i1"], consumer="writer", supabase=object(), user_id="u1"))
    assert waited == ["segmenter"]
    assert out["i1"][0].pins == () and "pins.items" not in telemetry


def test_a_failed_pin_read_still_delivers_the_sections(monkeypatch):
    rows = [{"seq": 1, "value": "core", "what": "وقائع", "content_md": "نص", "char_start": 0, "char_end": 200}]
    _wire(monkeypatch, pins_on=True, maps={"i1": _Stored(rows)}, pins={})
    import agents.memory.citation_pins.store as pin_store

    def boom(*_a, **_k):
        raise RuntimeError("down")

    monkeypatch.setattr(pin_store, "load_pins", boom)
    out, _ = asyncio.run(sections_mod.wait_and_load_sections(
        ["i1"], consumer="writer", supabase=object(), user_id="u1"))
    assert out["i1"][0].text == "نص" and out["i1"][0].pins == ()
