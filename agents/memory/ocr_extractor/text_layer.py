"""Read a digital PDF's own text layer — the letters it stores — without OCR.

A PDF made from Word or InDesign stores every page's letters; only a scanned
PDF is pictures. Reading those letters takes seconds for a whole book, costs
nothing, and does not depend on Mistral. It is the fallback when Mistral fails
or times out (owner 2026-11-16, after two live big-file reads stalled), and the
index for finding which pages mention what the user asked about.

Arabic in PDF text layers needs cleaning before an agent can use it:

* presentation forms (ﻳﺨــﺮج) → base letters (يخرج)        — NFKC
* tatweel / kashida stretch marks (رقــــــم) dropped
* a space before a diacritic (المد َّعون) closed up
* brackets flipped by right-to-left glyph order — «)بعض ورثة(» — swapped back,
  but only when a page shows that pattern (a PDF stored in logical order keeps
  its brackets as they are).

What cleaning cannot fix: digits and slashes inside a right-to-left line can
come out in reverse order («ه1441//ر510» for «510/ر/1441هـ»), and tables
flatten into lines. Mistral stays the main route; this is the backup.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

_TATWEEL = "ـ"
_ARABIC_LETTER = re.compile(r"[ء-يٱ-ۓ]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")
# A space (or several) before an Arabic diacritic / dagger alef.
_SPACE_BEFORE_MARK = re.compile(r"[ \t]+([ً-ٰٟ])")
_SPACES = re.compile(r"[ \t ]{2,}")
# Bracket pairs as the text layer stores them in a flipped RTL run, and as
# they read in logical order.
_FLIPPED = re.compile(r"\)[^()\n]{1,120}\(")
_LOGICAL = re.compile(r"\([^()\n]{1,120}\)")
_MIRROR = str.maketrans({"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{"})
# Arabic-Indic digit runs. Some exporters store them in reverse inside a
# right-to-left line («٩٣٤١» for ١٤٣٩, «٦٢» for ٢٦ in a contents page); Latin
# digits in the same files come out in order.
_AR_DIGIT_RUN = re.compile(r"[٠-٩]{2,}")
# A Hijri (13xx/14xx) or Gregorian (19xx/20xx) year, as stored and reversed.
_YEAR = re.compile(r"(?<![٠-٩])(?:١[٣٤]|١٩|٢٠)[٠-٩]{2}(?![٠-٩])")
_YEAR_REVERSED = re.compile(r"(?<![٠-٩])[٠-٩]{2}(?:[٣٤]١|٩١|٠٢)(?![٠-٩])")

#: A page with fewer letters and digits than this has no usable text layer (a
#: scan, an image-only cover) and needs OCR. Digits count too: a page of
#: figures is not an image.
MIN_LETTERS = 20
_DIGIT = re.compile(r"[0-9٠-٩]")
#: Pages sampled to decide a document's digit order (text_layer.sample_digits_reversed).
DIGIT_SAMPLE_PAGES = 20


@dataclass(frozen=True)
class TextLayerPage:
    page_no: int          # 1-based
    text: str             # cleaned
    letters: int          # letters in the cleaned text (Arabic + Latin)
    digits: int = 0       # digits in the cleaned text (Latin + Arabic-Indic)

    @property
    def usable(self) -> bool:
        return self.letters + self.digits >= MIN_LETTERS


def _arabic_line(line: str) -> bool:
    return len(_ARABIC_LETTER.findall(line)) > len(_LATIN_LETTER.findall(line))


def digits_reversed(raw_pages: Sequence[str]) -> bool:
    """Whether a document stores Arabic-Indic numbers backwards in its Arabic
    lines — decided once per document from its years: «٩٣٤١» (١٤٣٩ reversed)
    outnumbering «١٤٣٩». A contents page has no years of its own, so the
    decision is made over every page being read, not page by page."""
    straight = flipped = 0
    for raw in raw_pages:
        text = unicodedata.normalize("NFKC", raw or "")
        for ln in text.splitlines():
            if _arabic_line(ln):
                straight += len(_YEAR.findall(ln))
                flipped += len(_YEAR_REVERSED.findall(ln))
    return flipped > straight


def clean_text_layer(raw: str, *, reverse_digits: bool = False) -> str:
    """Make a page's raw text layer readable (see the module docstring).

    ``reverse_digits``: put Arabic-Indic digit runs in Arabic lines back in
    order — pass :func:`digits_reversed` of the document's pages.
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw).replace(_TATWEEL, "")
    text = _SPACE_BEFORE_MARK.sub(r"\1", text)
    lines = text.splitlines()
    arabic = [ln for ln in lines if _arabic_line(ln)]
    flipped = sum(len(_FLIPPED.findall(ln)) for ln in arabic)
    logical = sum(len(_LOGICAL.findall(ln)) for ln in arabic)
    if flipped > logical:
        lines = [ln.translate(_MIRROR) if _arabic_line(ln) else ln for ln in lines]
    if reverse_digits:
        lines = [
            _AR_DIGIT_RUN.sub(lambda m: m.group(0)[::-1], ln) if _arabic_line(ln) else ln
            for ln in lines
        ]
    lines = [_SPACES.sub(" ", ln).strip() for ln in lines]
    return "\n".join(ln for ln in lines if ln).strip()


def count_letters(text: str) -> int:
    return len(_ARABIC_LETTER.findall(text)) + len(_LATIN_LETTER.findall(text))


def _raw(reader, p: int) -> str:
    if p < 1:  # 1-based: never let 0 / negatives wrap to the last page
        return ""
    try:
        return reader.pages[p - 1].extract_text() or ""
    except Exception:  # noqa: BLE001 — one bad page must not lose the rest
        return ""


def sample_digits_reversed(reader, pages: Sequence[int], k: int = DIGIT_SAMPLE_PAGES) -> bool:
    """:func:`digits_reversed` over ``k`` pages spread evenly across ``pages`` —
    a per-document decision for callers that read the text layer in pieces
    (one cut at a time), so a piece with no years of its own (a contents page)
    is cleaned the same way as the rest. Blocking."""
    pages = list(pages)
    if len(pages) > k:
        step = len(pages) / k
        pages = [pages[int(i * step)] for i in range(k)]
    return digits_reversed([_raw(reader, p) for p in pages])


def read_pages(
    reader, pages: Sequence[int], *, reverse_digits: bool | None = None,
) -> list[TextLayerPage]:
    """The cleaned text layer of ``pages`` (1-based) of an open pypdf reader.

    ``reverse_digits``: the document's digit order (see
    :func:`sample_digits_reversed`); None = decide over these pages.
    Blocking — run it in a thread. A page pypdf cannot parse comes back empty
    (unusable), never raises.
    """
    raws = [_raw(reader, p) for p in pages]
    flip_digits = digits_reversed(raws) if reverse_digits is None else reverse_digits
    out: list[TextLayerPage] = []
    for p, raw in zip(pages, raws):
        text = clean_text_layer(raw, reverse_digits=flip_digits)
        out.append(TextLayerPage(
            page_no=p, text=text, letters=count_letters(text),
            digits=len(_DIGIT.findall(text)),
        ))
    return out
