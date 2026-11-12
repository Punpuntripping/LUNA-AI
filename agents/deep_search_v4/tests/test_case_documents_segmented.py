"""Segmented delivery — ``agents/utils/attachment_delivery.py`` and
``build_case_document_blocks_segmented`` (attachment_segmentation.md §4.6).
"""
from __future__ import annotations

import pytest

from agents.deep_search_v4.shared.case_documents import (
    build_case_document_blocks,
    build_case_document_blocks_segmented,
)
from agents.models import WorkspaceItemSnapshot
from agents.utils import attachment_delivery as ad
from agents.utils.attachment_delivery import (
    DEEP_SEARCH_VALUES,
    LEFT_OUT_PREFIX,
    TRIM_NOTE,
    WRITER_VALUES,
    Section,
    proportional_ratios,
    render_sections,
    sections_from_rows,
    trim_text,
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


def _line(word: str, n: int) -> str:
    """``n`` chars of text made of short lines (so trimming has line breaks)."""
    unit = (word + " ") * 9 + "\n"
    return (unit * (n // len(unit) + 1))[:n]


JUDGMENT_SECTIONS = [
    Section(1, "metadata", "بيانات الحكم والمحكمة", "المحكمة العامة بالرياض"),
    Section(2, "supporting", "وقائع الدعوى كما عرضها المدعي", "ادعى المدعي ..."),
    Section(3, "boilerplate", "ترويسة مكررة", "وزارة العدل"),
    Section(4, "core", "أسباب الحكم", "وحيث إن المحكمة ..."),
    Section(5, "repeat", "نسخة مكررة من الأسباب", "وحيث إن المحكمة ..."),
    Section(6, "core", "منطوق الحكم", "حكمت المحكمة برفض الدعوى"),
    Section(7, "supporting", "طرق الاعتراض", "للمحكوم عليه الاعتراض خلال ثلاثين يوماً"),
]


# ===========================================================================
# render_sections
# ===========================================================================


def test_deep_search_gets_core_only_with_tags_and_left_out_markers():
    text, trimmed = render_sections(JUDGMENT_SECTIONS, DEEP_SEARCH_VALUES)
    assert not trimmed
    assert "〔أسباب الحكم〕\nوحيث إن المحكمة ..." in text
    assert "〔منطوق الحكم〕\nحكمت المحكمة برفض الدعوى" in text
    # metadata + supporting before the first core section → one marker naming both
    assert f"{LEFT_OUT_PREFIX}بيانات الحكم والمحكمة، وقائع الدعوى كما عرضها المدعي" in text
    # boilerplate and repeat are never named
    assert "ترويسة مكررة" not in text and "نسخة مكررة" not in text
    # the trailing supporting section is named after the last core section
    assert text.endswith(f"{LEFT_OUT_PREFIX}طرق الاعتراض")
    # the repeat sits between two core sections and is silent → no marker between them
    between = text.split("〔منطوق الحكم〕")[0].split("〔أسباب الحكم〕")[1]
    assert LEFT_OUT_PREFIX not in between


def test_writer_gets_metadata_core_and_supporting():
    text, _ = render_sections(JUDGMENT_SECTIONS, WRITER_VALUES)
    for what in ("بيانات الحكم والمحكمة", "وقائع الدعوى كما عرضها المدعي",
                 "أسباب الحكم", "منطوق الحكم", "طرق الاعتراض"):
        assert f"〔{what}〕" in text
    assert LEFT_OUT_PREFIX not in text  # only silent values were left out
    assert text.index("بيانات الحكم") < text.index("أسباب الحكم") < text.index("منطوق الحكم")


def test_left_out_marker_caps_the_names():
    secs = [Section(i, "supporting", f"مقطع {i}", "نص") for i in range(1, 10)]
    secs.append(Section(10, "core", "الخلاصة", "النتيجة"))
    text, _ = render_sections(secs, DEEP_SEARCH_VALUES)
    marker = text.split("\n\n")[0]
    assert marker.count("مقطع") == ad.MAX_LEFT_OUT_NAMES
    assert marker.endswith(ad.LEFT_OUT_MORE)


def test_ratio_trims_every_kept_section_from_its_end():
    secs = [Section(1, "core", "أ", _line("أول", 1000)), Section(2, "core", "ب", _line("ثان", 1000))]
    text, trimmed = render_sections(secs, DEEP_SEARCH_VALUES, ratio=0.5)
    assert trimmed
    assert text.count(TRIM_NOTE) == 2
    for chunk in text.split("\n\n"):
        body = chunk.split("\n", 1)[1].replace(TRIM_NOTE, "").strip()
        assert 250 <= len(body) <= 500


def test_sections_from_rows_accepts_stored_rows():
    rows = [
        {"seq": 2, "value": "core", "what": "ب", "content_md": "نص ٢", "label": "ruling"},
        {"seq": 1, "value": "metadata", "what": "أ", "content_md": "نص ١", "label": "header"},
    ]
    secs = sections_from_rows(rows)
    assert [s.seq for s in secs] == [1, 2]
    assert secs[1].text == "نص ٢" and secs[1].label == "ruling"


# ===========================================================================
# trimming maths
# ===========================================================================


def test_trim_text_cuts_back_to_a_line_break():
    text = "سطر أول\nسطر ثان طويل جداً جداً\nسطر ثالث"
    out = trim_text(text, 0.6)
    assert out == "سطر أول\nسطر ثان طويل جداً جداً"[: len(out)]
    assert not out.endswith(" ")


def test_trim_text_full_ratio_is_identity():
    assert trim_text("abc def", 1.0) == "abc def"
    assert trim_text("abc def", 0.0) == ""


def test_owner_example_400k_against_300k_keeps_75_percent():
    ratios, factor = proportional_ratios(
        {"a": 200_000, "b": 100_000, "c": 100_000}, 0, per_doc_cap=10**9, total_cap=300_000
    )
    assert factor == pytest.approx(0.75)
    assert all(r == pytest.approx(0.75) for r in ratios.values())


def test_per_doc_cap_applies_before_the_total():
    ratios, factor = proportional_ratios({"big": 200_000, "small": 50_000}, 0)
    assert ratios["big"] == pytest.approx(0.5)  # 100k / 200k
    assert ratios["small"] == pytest.approx(1.0)
    assert factor == pytest.approx(1.0)  # 150k fits 300k


def test_fixed_text_shrinks_the_room():
    ratios, factor = proportional_ratios({"a": 100_000}, 250_000)
    assert factor == pytest.approx(0.5)  # room 50k


def test_no_room_gives_factor_zero():
    _, factor = proportional_ratios({"a": 10_000}, 300_000)
    assert factor == 0.0


# ===========================================================================
# build_case_document_blocks_segmented
# ===========================================================================


def test_short_document_without_sections_travels_whole():
    doc = _doc(1, "نص قصير")
    blocks = build_case_document_blocks_segmented([doc], ["id-1"], {})
    assert "#### النص الكامل\nنص قصير" in _body(blocks, "id-1")


def test_short_document_segmented_in_a_bundle_gets_its_sections():
    """Owner rule 2026-11-12: the small files of one message are segmented together."""
    doc = _doc(1, "نص قصير")
    blocks = build_case_document_blocks_segmented([doc], ["id-1"], {"id-1": JUDGMENT_SECTIONS})
    body = _body(blocks, "id-1")
    assert "〔أسباب الحكم〕" in body and "#### النص الكامل" not in body


def test_long_document_with_core_gets_sections():
    doc = _doc(1, _line("حكم", 20_000))
    stats: dict = {}
    blocks = build_case_document_blocks_segmented(
        [doc], ["id-1"], {"id-1": JUDGMENT_SECTIONS}, stats=stats
    )
    body = _body(blocks, "id-1")
    assert "#### مقاطع من النص بنصها الأصلي" in body
    assert "〔منطوق الحكم〕" in body
    assert "#### النص الكامل" not in body
    assert stats["segmented"] == ["WI-1"] and stats["fallback"] == []
    assert stats["trim_factor"] == 1.0


def test_long_document_without_map_falls_back_to_full_text_within_100k():
    content = _line("نص", 150_000)
    stats: dict = {}
    blocks = build_case_document_blocks_segmented([_doc(1, content)], ["id-1"], {}, stats=stats)
    body = _body(blocks, "id-1")
    assert "#### النص الكامل" in body
    assert "[انتهى المعروض:" in body
    assert stats["fallback"] == ["WI-1"] and stats["truncated"] == ["WI-1"]
    assert 90_000 <= stats["full_text_chars"] <= 100_000


def test_map_without_core_is_treated_as_no_map():
    secs = [Section(1, "supporting", "وقائع", "نص"), Section(2, "boilerplate", "ختم", "ختم")]
    stats: dict = {}
    build_case_document_blocks_segmented(
        [_doc(1, _line("نص", 8_000))], ["id-1"], {"id-1": secs}, stats=stats
    )
    assert stats["fallback"] == ["WI-1"] and stats["segmented"] == []


def test_not_named_documents_get_summary_only():
    blocks = build_case_document_blocks_segmented(
        [_doc(1, _line("نص", 20_000)), _doc(2, _line("نص", 20_000))],
        ["id-1"],
        {"id-1": JUDGMENT_SECTIONS, "id-2": JUDGMENT_SECTIONS},
    )
    assert "مقاطع من النص" in _body(blocks, "id-1")
    second = _body(blocks, "id-2")
    assert "مقاطع من النص" not in second and "#### النص الكامل" not in second
    assert "#### الملخص\nملخص" in second


def test_total_cap_trims_core_and_fallback_together():
    core = [Section(1, "core", "أسباب", _line("أسباب", 200_000))]
    docs = [_doc(1, _line("حكم", 250_000)), _doc(2, _line("عقد", 200_000))]
    stats: dict = {}
    build_case_document_blocks_segmented(
        docs, ["id-1", "id-2"], {"id-1": core}, stats=stats
    )
    # per doc: core 200k → 100k, fallback 200k → 100k; total 200k fits 300k
    assert stats["trim_factor"] == 1.0
    assert sorted(stats["truncated"]) == ["WI-1", "WI-2"]
    assert stats["full_text_chars"] <= 205_000


def test_short_documents_count_first_then_long_ones_share_the_rest():
    shorts = [_doc(i, _line("ق", 5_000)) for i in range(1, 3)]  # 10k fixed
    long_doc = _doc(9, _line("ط", 400_000))
    stats: dict = {}
    build_case_document_blocks_segmented(
        [*shorts, long_doc], ["id-1", "id-2", "id-9"], {}, total_cap=50_000, stats=stats
    )
    # fallback 400k → 100k per doc → room 40k → factor 0.4
    assert stats["trim_factor"] == pytest.approx(0.4)
    assert stats["full_text_chars"] <= 50_000


def test_flag_off_builder_is_untouched():
    """The old builder still cuts the first 12k — the segmented one is separate."""
    doc = _doc(1, _line("نص", 20_000))
    old = build_case_document_blocks([doc], ["id-1"])
    assert "[انتهى المعروض: " in _body(old, "id-1")


# ===========================================================================
# writer_bodies (plan §4.7)
# ===========================================================================


def test_writer_short_attachment_without_sections_is_untouched():
    bodies, stats = ad.writer_bodies({"a": "نص قصير"}, {})
    assert bodies["a"] == "نص قصير" and stats["segmented"] == []


def test_writer_short_attachment_from_a_bundle_gets_its_sections():
    bodies, stats = ad.writer_bodies({"a": "نص قصير"}, {"a": JUDGMENT_SECTIONS})
    assert "〔بيانات الحكم والمحكمة〕" in bodies["a"] and stats["segmented"] == ["a"]


def test_writer_long_attachment_gets_metadata_core_supporting_uncapped():
    big = [Section(1, "core", "أسباب", _line("أسباب", 250_000)),
           Section(2, "supporting", "وقائع", _line("وقائع", 100_000))]
    bodies, stats = ad.writer_bodies({"a": _line("نص", 400_000)}, {"a": big})
    assert stats["segmented"] == ["a"]
    assert TRIM_NOTE not in bodies["a"]  # no cap on segmented writer text (D12)
    assert len(bodies["a"]) > 350_000


def test_writer_fallback_is_capped_per_doc_and_overall():
    contents = {k: _line("نص", 250_000) for k in ("a", "b", "c", "d")}
    bodies, stats = ad.writer_bodies(contents, {})
    assert sorted(stats["fallback"]) == ["a", "b", "c", "d"]
    # 4 × 100k after the per-doc cap → 300k total → factor 0.75
    assert stats["fallback_factor"] == pytest.approx(0.75)
    shown = sum(len(b.split("\n[انتهى المعروض:")[0]) for b in bodies.values())
    assert shown <= 300_000
    assert all("[انتهى المعروض:" in b for b in bodies.values())


# ===========================================================================
# delivery_enabled
# ===========================================================================


class _Settings:
    def __init__(self, enabled, users=None):
        self.ATTACHMENT_SEGMENT_DELIVERY_ENABLED = enabled
        self.ATTACHMENT_SEGMENT_DELIVERY_USERS = users


@pytest.mark.parametrize(
    "enabled,users,user,expected",
    [
        (False, None, "u1", False),
        (True, None, "u1", True),
        (True, "u1, u2", "u2", True),
        (True, "u1", "u3", False),
    ],
)
def test_delivery_enabled(monkeypatch, enabled, users, user, expected):
    monkeypatch.setattr(ad, "get_settings", lambda: _Settings(enabled, users))
    assert ad.delivery_enabled(user) is expected
