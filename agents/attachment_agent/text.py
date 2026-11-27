"""A file's text as the model reads it, and where the model has looked.

Masking (وضع السرية): every byte that reaches the model is encoded with the turn
codec. A file is encoded ONCE, line by line, into :class:`MaskedText`, and every
tool serves slices of that one masked string. So what the model quotes is always
a substring of ``MaskedText.text``, and quotes are checked there — in the masked
space, never by decoding (a decoded fake comes back without its separators,
which would no longer match the file).

Line by line is safe: the codec joins digit groups across dashes and spaces only
(never a line break) and an e-mail never spans one, so each run is detected as in
the whole text. The only difference is the money window (16 chars) not seeing a
money word on the line before, which masks MORE, never less.

Numbers keep their length when masked; an e-mail may not. Offsets map both ways
by line; inside a line whose length changed they are clamped to the line (good
enough for a section or a page).

The user's text fixes (``attachment_text_corrections``, plan §3.3) are applied
BEFORE masking: the model, ``find``, the read tools and quote checks all see the
corrected text. Section and page offsets stay in the ORIGINAL text, so
:class:`FixMap` carries offsets between the original and the corrected text
(a fix may change lengths); :class:`MaskedText` composes the two maps, so
``to_orig`` / ``to_masked`` still speak ORIGINAL offsets.
"""
from __future__ import annotations

import bisect
import logging
from typing import Any, Callable, Iterable, Optional

from agents.memory.artifact_analyzer.verify import _clean_with_map, normalize
from agents.tool_repository.citation_scan import find_folded, fold
from shared.attachments.text_corrections import _in_order, _occurrences, _pair

logger = logging.getLogger(__name__)

Encoder = Callable[[str], str]


# ---------------------------------------------------------------------------
# Original ↔ corrected offsets
# ---------------------------------------------------------------------------


class FixMap:
    """The original text with its corrections applied, and offsets both ways.

    Same result as ``text_corrections.apply_text_corrections`` (each ``wrong`` →
    ``right``, every non-overlapping occurrence, oldest fix first): it is built
    with the same ordering and occurrence helpers. Each fix is one step; a step
    keeps the start offsets of the spans it replaced. A position inside a
    replaced span maps to the same distance into the other span, clamped to it;
    span starts and ends map exactly.
    """

    __slots__ = ("original", "text", "_steps")

    def __init__(self, original: str, corrections: Iterable[Any] | None = None) -> None:
        self.original = original or ""
        text = self.original
        steps: list[tuple[list[int], list[int], int, int]] = []
        for c in _in_order(corrections or []):
            wrong, right = _pair(c)
            if not wrong or wrong == right:
                continue
            starts = _occurrences(text, wrong)
            if not starts:
                continue
            d = len(right) - len(wrong)
            steps.append((starts, [s + i * d for i, s in enumerate(starts)], len(wrong), len(right)))
            text = text.replace(wrong, right)
        self.text = text
        self._steps = steps

    @property
    def changed(self) -> bool:
        return bool(self._steps)

    @staticmethod
    def _back(pos: int, step: tuple[list[int], list[int], int, int]) -> int:
        starts, new_starts, lw, lr = step
        k = bisect.bisect_right(new_starts, pos) - 1
        if k < 0:
            return pos
        inside = pos - new_starts[k]
        if inside < lr:
            return starts[k] + min(inside, lw)
        return pos - (k + 1) * (lr - lw)

    @staticmethod
    def _fwd(pos: int, step: tuple[list[int], list[int], int, int]) -> int:
        starts, new_starts, lw, lr = step
        k = bisect.bisect_right(starts, pos) - 1
        if k < 0:
            return pos
        inside = pos - starts[k]
        if inside < lw:
            return new_starts[k] + min(inside, lr)
        return pos + (k + 1) * (lr - lw)

    def to_orig(self, pos: int) -> int:
        """Corrected offset → original offset."""
        pos = max(0, min(pos, len(self.text)))
        for step in reversed(self._steps):
            pos = self._back(pos, step)
        return max(0, min(pos, len(self.original)))

    def to_corrected(self, pos: int) -> int:
        """Original offset → corrected offset."""
        pos = max(0, min(pos, len(self.original)))
        for step in self._steps:
            pos = self._fwd(pos, step)
        return max(0, min(pos, len(self.text)))


class MaskedText:
    """``original`` (the file's ``content_md``), ``corrected`` (with the user's
    text fixes) and ``text`` (``corrected`` masked — what the model reads).
    ``to_orig`` / ``to_masked`` map between ``text`` and ``original``."""

    __slots__ = ("original", "corrected", "text", "_fix", "_o", "_m", "_same")

    def __init__(
        self,
        original: str,
        encode: Optional[Encoder] = None,
        corrections: Iterable[Any] | None = None,
    ) -> None:
        self.original = original or ""
        fix = FixMap(self.original, corrections) if corrections else None
        self._fix: FixMap | None = fix if fix is not None and fix.changed else None
        self.corrected = self._fix.text if self._fix else self.original
        self._o: list[int] | None = None
        self._m: list[int] | None = None
        self._same: list[bool] | None = None
        if encode is None or not self.corrected:
            self.text = self.corrected
            return
        o_starts: list[int] = []
        m_starts: list[int] = []
        same: list[bool] = []
        parts: list[str] = []
        o = m = 0
        changed = False
        for line in self.corrected.splitlines(keepends=True):
            try:
                enc = encode(line)
            except Exception:  # noqa: BLE001 — as unfold: masking never breaks a read
                logger.debug("attachment_agent: line encode failed", exc_info=True)
                enc = line
            o_starts.append(o)
            m_starts.append(m)
            same.append(len(enc) == len(line))
            changed = changed or len(enc) != len(line)
            parts.append(enc)
            o += len(line)
            m += len(enc)
        self.text = "".join(parts)
        if changed:
            self._o, self._m, self._same = o_starts, m_starts, same

    def _map(self, pos: int, src: list[int], dst: list[int], dst_len: int) -> int:
        k = max(0, bisect.bisect_right(src, pos) - 1)
        delta = pos - src[k]
        line_end = dst[k + 1] if k + 1 < len(dst) else dst_len
        if self._same is not None and self._same[k]:
            return min(dst[k] + delta, dst_len)
        return min(dst[k] + delta, line_end)

    def to_corrected(self, pos: int) -> int:
        """Masked offset → offset in ``corrected``."""
        pos = max(0, min(pos, len(self.text)))
        if self._m is None:
            return pos
        return self._map(pos, self._m, self._o, len(self.corrected))

    def to_orig(self, pos: int) -> int:
        """Masked offset → offset in ``original``."""
        pos = self.to_corrected(pos)
        return self._fix.to_orig(pos) if self._fix else pos

    def to_masked(self, pos: int) -> int:
        """Offset in ``original`` → masked offset."""
        pos = max(0, min(pos, len(self.original)))
        if self._fix:
            pos = self._fix.to_corrected(pos)
        if self._o is None:
            return pos
        return self._map(pos, self._o, self._m, len(self.text))


# ---------------------------------------------------------------------------
# Ranges the model has seen (masked offsets)
# ---------------------------------------------------------------------------


def merged(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for start, end in sorted(r for r in ranges if r[1] > r[0]):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def covers(ranges: list[tuple[int, int]], start: int, end: int) -> bool:
    """``[start, end)`` lies inside one merged range."""
    return any(a <= start and end <= b for a, b in merged(ranges))


def uncovered_is_blank(text: str, ranges: list[tuple[int, int]]) -> bool:
    """Every character outside ``ranges`` is whitespace: the whole text was seen."""
    cursor = 0
    for a, b in merged(ranges):
        if text[cursor:a].strip():
            return False
        cursor = max(cursor, b)
    return not text[cursor:].strip()


# ---------------------------------------------------------------------------
# Finding a quote
# ---------------------------------------------------------------------------


def locate(text: str, quote: str) -> list[tuple[int, int]]:
    """Every place ``quote`` sits in ``text``, as ``(start, end)`` offsets in ``text``.

    Layout is ignored (whitespace runs, ``**`` bold markers, ``[n]`` markers, HTML
    entities, the order of combining marks — ``artifact_analyzer.verify.normalize``)
    and so are letter and digit shapes (tashkeel, alef / hamza forms, ة/ه, ى/ي,
    Arabic-Indic vs Latin digits — ``citation_scan.find_folded``). Nothing else:
    a quote that differs by a word is a rephrase, and fails.
    """
    q = normalize(quote)
    if not q or not text:
        return []
    hits = [(s, _past_marks(text, e)) for s, e, _whole in find_folded(text, q)]
    if hits:
        return hits
    clean, positions = _clean_with_map(text)
    out: list[tuple[int, int]] = []
    for s, e, _whole in find_folded(clean, q):
        if e > s:
            out.append((positions[s], _past_marks(text, positions[e - 1] + 1)))
    return out


def _past_marks(text: str, end: int) -> int:
    """A match ends at its last letter; the tashkeel / tatweel on it belong to it
    (``fold`` drops them, so ``find_folded`` stops before them)."""
    while end < len(text) and not fold(text[end]):
        end += 1
    return end


__all__ = [
    "Encoder", "FixMap", "MaskedText", "covers", "locate", "merged", "normalize", "uncovered_is_blank",
]
