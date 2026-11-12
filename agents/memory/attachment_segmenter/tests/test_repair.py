"""Validator + repair (the Phase 0 recipe), split planning, stitching."""
from __future__ import annotations

import random

import pytest

from agents.memory.attachment_segmenter.models import EmbeddedDoc, KeyParagraph, Segment, SegmentMap
from agents.memory.attachment_segmenter.paragraphs import PageRow, paragraphize
from agents.memory.attachment_segmenter.repair import (
    FIRST_PAGE_CAP_CHARS,
    InvalidMap,
    choose_cut,
    first_page_end,
    repair_map,
    repair_segments,
    stitch,
)
from agents.memory.ocr_extractor.mistral_ocr import join_pages


def _paras(n: int, size: int = 100):
    text = "\n".join(f"فقرة {i} " + "ن" * (size - 8) for i in range(1, n + 1))
    return paragraphize(text)


def seg(f, t, value="core", label="grounds", what=""):
    return Segment(from_p=f, to_p=t, label=label, value=value, what=what)


def ranges(segs):
    return [(s.from_p, s.to_p) for s in segs]


def smap(segments, docs=(), keys=()):
    return SegmentMap(documents_in_file=list(docs), segments=list(segments), key_paragraphs=list(keys))


def phase0_repair(segs: list[tuple[int, int]], n: int) -> list[tuple[int, int]]:
    """experiments/eval_phase0.py::repair, verbatim logic on (from_p, to_p) pairs."""
    out, prev = [], 0
    for f, t in sorted(segs):
        a, b = prev + 1, min(t, n)
        if b < a:
            continue
        out.append((a, b))
        prev = b
    if out and prev < n:
        out[-1] = (out[-1][0], n)
    return out


PARAS = _paras(40)


def test_valid_map_is_untouched():
    out, stats = repair_segments([seg(1, 10), seg(11, 30), seg(31, 40)], 1, 40, PARAS)
    assert ranges(out) == [(1, 10), (11, 30), (31, 40)]
    assert not stats.changed


def test_gap_is_absorbed_by_the_next_segment():
    """Phase 0 recipe: trust each to_p, from_p = previous to_p + 1."""
    out, stats = repair_segments([seg(1, 10, "metadata"), seg(13, 40)], 1, 40, PARAS)
    assert ranges(out) == [(1, 10), (11, 40)]
    assert [s.value for s in out] == ["metadata", "core"]
    assert (stats.gaps, stats.gap_paragraphs) == (1, 2)


def test_leading_and_trailing_gaps():
    out, stats = repair_segments([seg(2, 39)], 1, 40, PARAS)
    assert ranges(out) == [(1, 40)]
    assert stats.gaps == 2 and stats.gap_paragraphs == 2


def test_overlap_trims_the_later():
    out, stats = repair_segments([seg(1, 12, "core"), seg(10, 40, "supporting")], 1, 40, PARAS)
    assert ranges(out) == [(1, 12), (13, 40)]
    assert [s.value for s in out] == ["core", "supporting"]
    assert stats.overlaps == 1


def test_contained_segment_is_dropped():
    out, stats = repair_segments([seg(1, 20), seg(5, 6, "boilerplate"), seg(21, 40)], 1, 40, PARAS)
    assert ranges(out) == [(1, 20), (21, 40)]
    assert stats.dropped == 1


def test_out_of_range_is_clamped_and_a_reversed_range_keeps_its_to_p():
    out, stats = repair_segments([seg(0, 20), seg(40, 21), seg(41, 50)], 1, 40, PARAS)
    assert ranges(out) == [(1, 20), (21, 21), (22, 40)]
    assert stats.out_of_range == 2 and stats.reversed == 1 and stats.dropped == 0


def test_the_phase0_recipe_on_random_maps():
    rng = random.Random(9)
    n = len(PARAS)
    checked = 0
    for _ in range(2000):
        cuts = sorted(rng.sample(range(1, n), rng.randint(1, 12)))
        bounds = [(a + 1, b) for a, b in zip([0] + cuts, cuts + [n])]
        noisy = []
        for f, t in bounds:  # the model's typical slips: ±1 at a boundary, a skipped line
            f += rng.choice([0, 0, 0, -1, 1])
            t += rng.choice([0, 0, 0, -1, 1])
            noisy.append((max(f, 0), t))
        try:
            out, _ = repair_segments([seg(f, t) for f, t in noisy], 1, n, PARAS)
        except InvalidMap:
            continue
        assert ranges(out) == phase0_repair(noisy, n)
        assert out[0].from_p == 1 and out[-1].to_p == n
        assert all(b.from_p == a.to_p + 1 for a, b in zip(out, out[1:]))
        checked += 1
    assert checked > 1500


def test_unordered_segments_are_sorted():
    out, _ = repair_segments([seg(21, 40), seg(1, 20)], 1, 40, PARAS)
    assert ranges(out) == [(1, 20), (21, 40)]


def test_big_gap_is_invalid():
    with pytest.raises(InvalidMap):
        repair_segments([seg(1, 30)], 1, 40, PARAS)  # 25% of the chars missing


def test_nothing_in_range_is_invalid():
    # the recipe would stretch the one segment over the whole range; nothing claimed
    # any of it, so the safety gate rejects it
    with pytest.raises(InvalidMap):
        repair_segments([seg(50, 60)], 1, 40, PARAS)
    with pytest.raises(InvalidMap):
        repair_segments([], 1, 40, PARAS)


def test_part_range():
    out, _ = repair_segments([seg(18, 30), seg(31, 40)], 21, 40, PARAS)
    assert ranges(out) == [(21, 30), (31, 40)]


def test_repair_map_clips_docs_and_keys():
    m = smap(
        [seg(1, 40)],
        docs=[EmbeddedDoc(from_p=1, to_p=45, name="حكم"), EmbeddedDoc(from_p=50, to_p=60, name="x")],
        keys=[KeyParagraph(p=3, why="a"), KeyParagraph(p=3, why="dup"), KeyParagraph(p=99, why="out")],
    )
    out, _ = repair_map(m, 1, 40, PARAS)
    assert [(d.from_p, d.to_p) for d in out.documents_in_file] == [(1, 40)]
    assert [k.p for k in out.key_paragraphs] == [3]


# ── split planning ────────────────────────────────────────────────────────────


def _paged(n_pages: int, per_page: int, size: int = 100):
    md = [
        "\n".join(f"ص{pg} فقرة {i} " + "ن" * (size - 12) for i in range(per_page))
        for pg in range(1, n_pages + 1)
    ]
    content, offsets = join_pages(md)
    rows = [PageRow(i + 1, off, m) for i, (off, m) in enumerate(zip(offsets, md))]
    return paragraphize(content, pages=rows)


def test_cut_at_the_page_start_nearest_the_middle():
    paras = _paged(10, 6)  # 60 paragraphs, a page every 6
    cut, how = choose_cut(paras)
    assert how == "page_start"
    assert paras[cut - 1].page != paras[cut - 2].page
    assert cut == 31  # page 6 starts at paragraph 31, the middle of 60


def test_cut_without_pages_is_the_nearest_paragraph():
    paras = _paras(60)
    cut, how = choose_cut(paras)
    assert how == "paragraph"
    assert cut in (30, 31)


def test_cut_ignores_a_page_start_far_from_the_middle():
    # only 2 pages: the page start is at 90% of the text → paragraph cut
    md = ["\n".join("أ" * 99 for _ in range(18)), "\n".join("ب" * 99 for _ in range(2))]
    content, offsets = join_pages(md)
    paras = paragraphize(content, pages=[PageRow(1, offsets[0], md[0]), PageRow(2, offsets[1], md[1])])
    _, how = choose_cut(paras)
    assert how == "paragraph"


def test_first_page_end():
    paged = _paged(10, 6)
    assert first_page_end(paged, 31) == 6
    unpaged = _paras(60, size=100)
    assert first_page_end(unpaged, 31) == 15
    big = _paras(60, size=600)
    end = first_page_end(big, 31)
    assert end == 4  # capped at FIRST_PAGE_CAP_CHARS (2,500) → 4 × ~600
    assert sum(len(p.text) for p in big[:end]) <= FIRST_PAGE_CAP_CHARS or end == 3
    assert first_page_end(_paras(60), 5) == 4  # never reaches the cut


# ── stitching ────────────────────────────────────────────────────────────────


def test_stitch_merges_a_section_cut_by_the_seam():
    p1 = smap([seg(1, 5, "metadata", "header"), seg(6, 20, "core", "grounds", "تسبيب")])
    p2 = smap([seg(21, 30, "core", "grounds"), seg(31, 40, "core", "ruling")])
    out, seam = stitch(p1, p2, PARAS)
    assert ranges(out.segments) == [(1, 5), (6, 30), (31, 40)]
    assert out.segments[1].what == "تسبيب"
    assert seam.merged_at_seam == 1


def test_stitch_keeps_different_sections_apart():
    p1 = smap([seg(1, 20, "core", "claim")])
    p2 = smap([seg(21, 40, "core", "defense")])
    out, seam = stitch(p1, p2, PARAS)
    assert ranges(out.segments) == [(1, 20), (21, 40)]
    assert seam.merged_at_seam == 0


def test_stitch_demotes_a_repeated_first_page_header():
    text = "\n".join(
        ["المحكمة العامة بالرياض", "رقم القضية 123"]
        + [f"نص {i}" for i in range(3, 21)]
        + ["المحكمة العامة بالرياض", "رقم القضية 123"]
        + [f"نص {i}" for i in range(23, 41)]
    )
    paras = paragraphize(text)
    p1 = smap([seg(1, 2, "metadata", "header"), seg(3, 20, "core")])
    p2 = smap([seg(21, 22, "metadata", "header"), seg(23, 40, "core")])
    out, seam = stitch(p1, p2, paras)
    assert [s.value for s in out.segments] == ["metadata", "core", "boilerplate", "core"]
    assert seam.repeat_metadata_fixed == 1


def test_stitch_merges_duplicate_documents_and_caps_keys():
    p1 = SegmentMap(
        documents_in_file=[EmbeddedDoc(from_p=1, to_p=20, name="الحكم")],
        segments=[seg(1, 20)],
        key_paragraphs=[KeyParagraph(p=i, why="") for i in range(1, 11)],
    )
    p2 = SegmentMap(
        documents_in_file=[EmbeddedDoc(from_p=21, to_p=35, name="الحكم"), EmbeddedDoc(from_p=36, to_p=40, name="مرفق")],
        segments=[seg(21, 40, "supporting")],
        key_paragraphs=[KeyParagraph(p=i, why="") for i in range(21, 31)],
    )
    out, seam = stitch(p1, p2, PARAS)
    assert [(d.from_p, d.to_p, d.name) for d in out.documents_in_file] == [(1, 35, "الحكم"), (36, 40, "مرفق")]
    assert seam.duplicate_docs_merged == 1
    assert len(out.key_paragraphs) == 15


def test_stitch_does_not_merge_two_unnamed_documents():
    p1 = smap([seg(1, 20)], docs=[EmbeddedDoc(from_p=1, to_p=20, name="")])
    p2 = smap([seg(21, 40)], docs=[EmbeddedDoc(from_p=21, to_p=40, name="")])
    out, _ = stitch(p1, p2, PARAS)
    assert len(out.documents_in_file) == 2
