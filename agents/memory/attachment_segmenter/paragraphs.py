"""Deterministic paragraphizer — the units the segmenter numbers and code cuts at.

Plan: ``.claude/plans/attachment_segmentation.md`` §4.3. Rule v2 — the Phase 0 final
rule, mirrored from ``experiments/make_paras.py`` (``phase0_results.md`` §10):

- every non-empty line of ``content_md`` is a paragraph (stripped);
- a line longer than the type's max is split, sentence-aware, at the LAST good break
  inside the window ``[40% of max, max]``: first a sentence end («.» «؟» «!» «:» «؛»
  followed by whitespace), then a clause break («،» «,» «;» followed by whitespace),
  then any space in ``[50% of max, max]``; a hard cut at max when there is none. The
  break character stays with the left piece;
- max = 500 for judgment-like types (judgment, hearing_minutes — P9), 1,200 for every
  other type.

Ids ``P001…`` are reproducible from ``content_md`` + ``doc_type`` alone — page rows
only add page numbers, they never move a boundary. Every paragraph also carries its
exact offsets: ``text == content_md[char_start:char_end]``. ``PARAGRAPH_RULE_VERSION``
is stored on every map; bump it whenever the rule or a max changes.

Every later cut (segments, trimming, fallback) snaps to these boundaries.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

#: Stored on ``attachment_segment_maps.paragraph_rule_version``; part of the
#: helper's version (``agents/helpers/registry.py``).
PARAGRAPH_RULE_VERSION = "v2-sentence-500j-1200"

#: Longest paragraph, in characters, unless the type says otherwise.
DEFAULT_MAX_PARAGRAPH_CHARS = 1_200

#: Per ``doc_type`` max (Phase 0, P9). Change it here, as one change, together with a
#: ``PARAGRAPH_RULE_VERSION`` bump.
MAX_PARAGRAPH_CHARS_BY_TYPE: dict[str, int] = {
    "judgment": 500,
    "hearing_minutes": 500,
}

# make_paras.py's patterns, verbatim
_SENT = re.compile(r"[.؟!:؛](?=\s)")
_CLAUSE = re.compile(r"[،,;](?=\s)")


def max_paragraph_chars(doc_type: str | None) -> int:
    return MAX_PARAGRAPH_CHARS_BY_TYPE.get(str(doc_type or ""), DEFAULT_MAX_PARAGRAPH_CHARS)


@dataclass(frozen=True)
class Paragraph:
    """One paragraph. ``text == content_md[char_start:char_end]`` always."""

    n: int                 # 1-based number (P001 = 1)
    char_start: int
    char_end: int
    text: str
    page: int | None = None

    @property
    def pid(self) -> str:
        return pid(self.n)


def pid(n: int) -> str:
    """``P001`` style id (3 digits minimum, more when needed)."""
    return f"P{n:03d}"


@dataclass(frozen=True)
class PageRow:
    """An ``attachment_pages`` row, as far as the paragraphizer cares."""

    page_no: int
    char_start: int
    content_md: str = ""


def _last_break(line: str, rx: re.Pattern, lo: int, hi: int) -> int:
    """Index just after the last ``rx`` match ending inside ``(lo, hi]``; -1 if none.

    Verbatim from make_paras.py, including ``finditer(line, 0, hi)``: the search
    stops at ``hi``, so the whitespace lookahead must also fall before ``hi``.
    """
    best = -1
    for m in rx.finditer(line, 0, hi):
        end = m.end()
        if lo < end <= hi:
            best = end
    return best


def _split_line(line: str, start: int, max_chars: int) -> list[tuple[int, str]]:
    """``(offset, piece)`` for one stripped line that starts at ``start``.

    make_paras.py's ``split_line`` with offsets: the same cut each round, the piece
    stripped (only its right side can change — the line is already stripped) and
    the remainder stripped (only its left side can change).
    """
    out: list[tuple[int, str]] = []
    while len(line) > max_chars:
        cut = _last_break(line, _SENT, int(max_chars * 0.4), max_chars)
        if cut < 0:
            cut = _last_break(line, _CLAUSE, int(max_chars * 0.4), max_chars)
        if cut < 0:
            sp = line.rfind(" ", 0, max_chars)
            cut = sp if sp > max_chars // 2 else max_chars
        piece = line[:cut].rstrip()
        if piece:
            out.append((start, piece))
        rest = line[cut:]
        skipped = len(rest) - len(rest.lstrip())
        start += cut + skipped
        line = rest.lstrip()
    if line:
        out.append((start, line))
    return out


def _valid_pages(content_md: str, pages: Sequence[PageRow]) -> list[PageRow]:
    """The page rows, sorted, when they fit ``content_md``; else ``[]``.

    A page fits when its first non-blank characters sit at ``char_start``. Rows
    from an older ``content_md`` (re-OCR, manual edit) fail this and are ignored,
    so a stale page table can never mis-number pages.
    """
    rows = sorted(pages, key=lambda p: p.page_no)
    if not rows:
        return []
    prev = -1
    for row in rows:
        if row.char_start < prev or row.char_start > len(content_md):
            return []
        prev = row.char_start
        head = (row.content_md or "").strip()[:40]
        if head and not content_md[row.char_start:].lstrip().startswith(head):
            return []
    return rows


def coerce_pages(rows: Sequence[Mapping[str, Any] | PageRow] | None) -> list[PageRow]:
    """``attachment_pages`` dict rows (or ``PageRow``s) → ``PageRow``s; bad rows dropped."""
    out: list[PageRow] = []
    for row in rows or []:
        if isinstance(row, PageRow):
            out.append(row)
            continue
        try:
            out.append(
                PageRow(
                    page_no=int(row["page_no"]),
                    char_start=int(row["char_start"]),
                    content_md=str(row.get("content_md") or ""),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


def paragraphize(
    content_md: str,
    *,
    doc_type: str | None = None,
    pages: Sequence[PageRow] | None = None,
    max_chars: int | None = None,
) -> list[Paragraph]:
    """Number ``content_md`` into paragraphs ``P001…`` with offsets and pages.

    ``pages`` (from ``attachment_pages``) only sets ``Paragraph.page``: the page
    whose ``char_start`` is the last at or before the paragraph's start. Without
    valid page rows every ``page`` is ``None``.
    """
    text = content_md or ""
    limit = max_chars or max_paragraph_chars(doc_type)
    page_rows = _valid_pages(text, pages or [])
    out: list[Paragraph] = []
    pos = 0
    for raw_line in text.split("\n"):
        line_start = pos
        pos += len(raw_line) + 1
        stripped = raw_line.strip()
        if not stripped:
            continue
        start = line_start + (len(raw_line) - len(raw_line.lstrip()))
        for off, piece in _split_line(stripped, start, limit):
            out.append(Paragraph(n=len(out) + 1, char_start=off, char_end=off + len(piece), text=piece))
    if page_rows:
        out = _assign_pages(out, page_rows)
    return out


def _assign_pages(paragraphs: list[Paragraph], pages: list[PageRow]) -> list[Paragraph]:
    starts = [p.char_start for p in pages]
    numbers = [p.page_no for p in pages]
    out: list[Paragraph] = []
    i = 0
    for para in paragraphs:
        while i + 1 < len(starts) and starts[i + 1] <= para.char_start:
            i += 1
        page = numbers[i] if starts[i] <= para.char_start else numbers[0]
        out.append(Paragraph(para.n, para.char_start, para.char_end, para.text, page))
    return out


def render_numbered(paragraphs: Sequence[Paragraph], first: int = 1, last: int | None = None) -> str:
    """``[P001] text`` lines for paragraphs ``first..last`` (1-based, inclusive)."""
    last = len(paragraphs) if last is None else last
    return "\n".join(f"[{p.pid}] {p.text}" for p in paragraphs[first - 1:last])


__all__ = [
    "DEFAULT_MAX_PARAGRAPH_CHARS",
    "MAX_PARAGRAPH_CHARS_BY_TYPE",
    "PARAGRAPH_RULE_VERSION",
    "PageRow",
    "Paragraph",
    "coerce_pages",
    "max_paragraph_chars",
    "paragraphize",
    "pid",
    "render_numbered",
]
