"""Validator + repair of a segment map, split planning, and stitching.

Plan §4.4 / P6 / P7; split + stitch logic ported from
``experiments/run_split.py``. Pure functions — no LLM, no DB.

Repair (on one map, for the paragraph range ``a..b`` it must cover) = the Phase 0
recipe (``experiments/eval_phase0.py::repair``, ``phase0_results.md`` §9.1 — it made
93/93 maps valid and matches the model's evident intent):

- keep the segment order (sorted by ``(from_p, to_p)``);
- trust each ``to_p`` (clamped to ``b``) and set every ``from_p`` to the previous
  ``to_p + 1`` (the first starts at ``a``) — an overlap trims the LATER segment, a gap
  is absorbed by the segment AFTER it;
- drop a segment left empty;
- end the last segment at ``b``.

On top of the recipe, one safety gate: the map is **invalid** — the runner
re-attempts once, then fails — when it has no segment, or the paragraphs no segment
claimed hold more than ``MAX_GAP_SHARE`` of the range's characters (a model that
stopped half way would otherwise have its last label stretched blindly over the rest).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Sequence

from .models import EmbeddedDoc, KeyParagraph, Segment, SegmentMap
from .paragraphs import Paragraph

#: Above this many characters of ``content_md`` → two calls (P7).
SPLIT_THRESHOLD_CHARS = 30_000
#: Read-only context paragraphs given to each part from its neighbour.
OVERLAP_PARAGRAPHS = 12
#: Shared first page shown (read-only) to part 2, at most this many chars.
FIRST_PAGE_CAP_CHARS = 2_500
#: Without page rows, the "first page" is this many paragraphs (then capped).
FIRST_PAGE_FALLBACK_PARAGRAPHS = 15
#: A page start is only used as the cut when it lies in this middle band.
CUT_BAND = (0.25, 0.75)
#: Gap paragraphs above this share of the range's chars make the map invalid.
MAX_GAP_SHARE = 0.10
MAX_KEY_PARAGRAPHS = 15
MAX_KEY_PARAGRAPHS_PER_PART = 8


class InvalidMap(ValueError):
    """The model's map cannot be repaired into a usable one."""


@dataclass
class RepairStats:
    reversed: int = 0
    out_of_range: int = 0
    overlaps: int = 0
    dropped: int = 0
    gaps: int = 0
    gap_paragraphs: int = 0
    gap_chars: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)

    def add(self, other: "RepairStats") -> "RepairStats":
        return RepairStats(**{k: v + getattr(other, k) for k, v in asdict(self).items()})

    @property
    def changed(self) -> bool:
        return any(asdict(self).values())


def _chars(paragraphs: Sequence[Paragraph], a: int, b: int) -> int:
    return sum(len(paragraphs[p - 1].text) for p in range(a, b + 1))


def repair_segments(
    segments: Sequence[Segment],
    a: int,
    b: int,
    paragraphs: Sequence[Paragraph],
) -> tuple[list[Segment], RepairStats]:
    """Segments covering ``a..b`` exactly once, in order. Raises :class:`InvalidMap`."""
    stats = RepairStats()
    if not segments:
        raise InvalidMap("no segment")
    claimed = [False] * (b - a + 1)
    for seg in segments:
        f, t = seg.from_p, seg.to_p
        if f > t:
            stats.reversed += 1
            f, t = t, f
        if f < a or t > b:
            stats.out_of_range += 1
        for p in range(max(f, a), min(t, b) + 1):
            claimed[p - a] = True

    # the Phase 0 recipe: keep order, trust to_p, from_p = previous to_p + 1
    out: list[Segment] = []
    prev = a - 1
    for seg in sorted(segments, key=lambda s: (s.from_p, s.to_p)):
        lo, hi = prev + 1, min(seg.to_p, b)
        if hi < lo:
            stats.dropped += 1
            continue
        if lo > seg.from_p >= a:
            stats.overlaps += 1
        elif lo < seg.from_p:
            stats.gaps += 1
        out.append(seg.model_copy(update={"from_p": lo, "to_p": hi}))
        prev = hi
    if not out:
        raise InvalidMap("no segment inside the range")
    if prev < b:
        stats.gaps += 1
        out[-1] = out[-1].model_copy(update={"to_p": b})

    gap_ps = [a + i for i, c in enumerate(claimed) if not c]
    stats.gap_paragraphs = len(gap_ps)
    stats.gap_chars = sum(len(paragraphs[p - 1].text) for p in gap_ps)
    total = _chars(paragraphs, a, b)
    if total and stats.gap_chars > MAX_GAP_SHARE * total:
        raise InvalidMap(f"unclaimed paragraphs hold {stats.gap_chars} of {total} chars")
    return out, stats


def _clean_docs(docs: Sequence[EmbeddedDoc], a: int, b: int) -> list[EmbeddedDoc]:
    out: list[EmbeddedDoc] = []
    for d in docs:
        f, t = sorted((d.from_p, d.to_p))
        f, t = max(f, a), min(t, b)
        if f <= t:
            out.append(d.model_copy(update={"from_p": f, "to_p": t}))
    return sorted(out, key=lambda d: (d.from_p, d.to_p))


def _clean_keys(keys: Sequence[KeyParagraph], a: int, b: int, cap: int) -> list[KeyParagraph]:
    seen: set[int] = set()
    out: list[KeyParagraph] = []
    for k in keys:
        if a <= k.p <= b and k.p not in seen:
            seen.add(k.p)
            out.append(k)
    return out[:cap]


def repair_map(
    smap: SegmentMap,
    a: int,
    b: int,
    paragraphs: Sequence[Paragraph],
    *,
    key_cap: int = MAX_KEY_PARAGRAPHS,
) -> tuple[SegmentMap, RepairStats]:
    """The whole map for range ``a..b``: segments repaired, documents and key
    paragraphs clipped to the range. Raises :class:`InvalidMap`."""
    segs, stats = repair_segments(smap.segments, a, b, paragraphs)
    return (
        SegmentMap(
            documents_in_file=_clean_docs(smap.documents_in_file, a, b),
            segments=segs,
            key_paragraphs=_clean_keys(smap.key_paragraphs, a, b, key_cap),
        ),
        stats,
    )


# ---------------------------------------------------------------------------
# Split planning (P7, D6)
# ---------------------------------------------------------------------------


def page_starts(paragraphs: Sequence[Paragraph]) -> list[int]:
    """Numbers of paragraphs that open a new page (needs page rows)."""
    return [
        p.n
        for prev, p in zip(paragraphs, paragraphs[1:])
        if p.page is not None and prev.page is not None and p.page != prev.page
    ]


def choose_cut(paragraphs: Sequence[Paragraph]) -> tuple[int, str]:
    """The first paragraph of part 2, and how it was chosen.

    The page start nearest the middle of the text when one lies in the middle
    band (page rows make this exact); otherwise the paragraph boundary nearest
    the middle. Always ``2 <= cut <= len(paragraphs)``.
    """
    n = len(paragraphs)
    if n < 2:
        raise ValueError("cannot split fewer than 2 paragraphs")
    begin, end = paragraphs[0].char_start, paragraphs[-1].char_end
    span = max(end - begin, 1)
    mid = begin + span / 2
    lo, hi = begin + CUT_BAND[0] * span, begin + CUT_BAND[1] * span
    starts = [s for s in page_starts(paragraphs) if lo <= paragraphs[s - 1].char_start <= hi]
    if starts:
        return min(starts, key=lambda s: abs(paragraphs[s - 1].char_start - mid)), "page_start"
    return min(range(2, n + 1), key=lambda s: abs(paragraphs[s - 1].char_start - mid)), "paragraph"


def first_page_end(paragraphs: Sequence[Paragraph], cut: int) -> int:
    """Last paragraph of the shared first page (shown read-only to part 2)."""
    first = paragraphs[0].page
    if first is not None:
        end = max(p.n for p in paragraphs if p.page == first)
    else:
        end = FIRST_PAGE_FALLBACK_PARAGRAPHS
    end = max(1, min(end, cut - 1, len(paragraphs)))
    while end > 3 and _chars(paragraphs, 1, end) > FIRST_PAGE_CAP_CHARS:
        end -= 1
    return end


# ---------------------------------------------------------------------------
# Stitching (experiments/run_split.py::stitch, on integer ranges)
# ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


@dataclass
class SeamStats:
    merged_at_seam: int = 0
    repeat_metadata_fixed: int = 0
    duplicate_docs_merged: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def stitch(
    part1: SegmentMap,
    part2: SegmentMap,
    paragraphs: Sequence[Paragraph],
) -> tuple[SegmentMap, SeamStats]:
    """Join two repaired part maps (part 1 = ``1..cut-1``, part 2 = ``cut..n``).

    1. a section cut by the seam (same label and value on both sides) → one;
    2. part 2 re-rating the first page's header as ``metadata`` (≥ 60% of its
       paragraphs' text already in part 1's metadata) → ``boilerplate``;
    3. an embedded document listed by both parts (same name, or overlapping
       ranges) → one entry;
    4. key paragraphs → part 1's then part 2's, at most ``MAX_KEY_PARAGRAPHS``.
    """
    seam = SeamStats()
    first = list(part1.segments)
    second = list(part2.segments)
    if first and second:
        last, head = first[-1], second[0]
        if last.label == head.label and last.value == head.value and last.to_p + 1 == head.from_p:
            first[-1] = last.model_copy(update={"to_p": head.to_p})
            second = second[1:]
            seam.merged_at_seam += 1

    first_meta = {
        _norm(paragraphs[p - 1].text)
        for s in first
        if s.value == "metadata"
        for p in range(s.from_p, s.to_p + 1)
    }
    fixed: list[Segment] = []
    for s in second:
        if s.value == "metadata" and first_meta:
            ps = range(s.from_p, s.to_p + 1)
            same = sum(1 for p in ps if _norm(paragraphs[p - 1].text) in first_meta)
            if same and same >= 0.6 * len(ps):
                seam.repeat_metadata_fixed += 1
                s = s.model_copy(update={"value": "boilerplate"})
        fixed.append(s)

    docs: list[EmbeddedDoc] = []
    for d in list(part1.documents_in_file) + list(part2.documents_in_file):
        same_name = bool(_norm(d.name)) and _norm(docs[-1].name) == _norm(d.name) if docs else False
        if docs and (same_name or d.from_p <= docs[-1].to_p):
            docs[-1] = docs[-1].model_copy(update={"to_p": max(docs[-1].to_p, d.to_p)})
            seam.duplicate_docs_merged += 1
        else:
            docs.append(d)

    keys = _clean_keys(
        list(part1.key_paragraphs) + list(part2.key_paragraphs), 1, len(paragraphs), MAX_KEY_PARAGRAPHS
    )
    return SegmentMap(documents_in_file=docs, segments=first + fixed, key_paragraphs=keys), seam


__all__ = [
    "CUT_BAND",
    "FIRST_PAGE_CAP_CHARS",
    "InvalidMap",
    "MAX_GAP_SHARE",
    "OVERLAP_PARAGRAPHS",
    "RepairStats",
    "SPLIT_THRESHOLD_CHARS",
    "SeamStats",
    "choose_cut",
    "first_page_end",
    "page_starts",
    "repair_map",
    "repair_segments",
    "stitch",
]
