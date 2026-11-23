"""Pages taken from the PDF's text layer — the segmenter's warning (owner, 2026-11-23).

The OCR runner reads some pages from the text stored inside the PDF instead of
by OCR (``agents/memory/ocr_extractor``): pages past the user's OCR budget, and
pages Mistral failed on. Their words are mostly right, their dates and numbers
are not to be trusted. It records them on the attachment as
``metadata.ocr_text_layer_ranges``.

Most agents never read the whole file — they read the segmenter's sections. So
the warning goes through the segmenter, twice:

* every segmenter call whose own paragraphs include such pages is told which
  paragraphs (:func:`call_notice`, in the user message), so its ``what``
  descriptions do not state their dates and numbers as facts;
* every stored section that overlaps such pages carries a ``〔[نظام] …〕``
  warning at the top of its text (:func:`stamp_rows`) — added by code, not left
  to the model, so every agent the section is delivered to sees it.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from agents.memory.ocr_extractor.partial import TEXT_LAYER_CAUTION
from shared.quota.ocr_budget import format_ranges

from .paragraphs import Paragraph, pid

#: Paragraph runs named in one call's notice before «… and N more».
MAX_NOTICE_RUNS = 30


def text_layer_pages(metadata: Any) -> frozenset[int]:
    """The pages ``metadata.ocr_text_layer_ranges`` names (``[[a, b], …]``)."""
    if not isinstance(metadata, Mapping):
        return frozenset()
    pages: set[int] = set()
    for run in metadata.get("ocr_text_layer_ranges") or []:
        try:
            a, b = int(run[0]), int(run[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        pages.update(range(a, b + 1))
    return frozenset(p for p in pages if p > 0)


def _runs(numbers: Iterable[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for n in sorted(numbers):
        if runs and n == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], n)
        else:
            runs.append((n, n))
    return runs


#: Per paragraph number: (its real page or None, its file's text-layer pages).
Marks = Mapping[int, tuple[int | None, frozenset[int]]]


def marks_for(
    paragraphs: Sequence[Paragraph], layer: frozenset[int], *, first_n: int = 1,
) -> dict[int, tuple[int | None, frozenset[int]]]:
    """:data:`Marks` for one file's own paragraphs (real pages), numbered from
    ``first_n`` — a bundle member's first global paragraph. ``{}`` without
    text-layer pages."""
    if not layer:
        return {}
    return {first_n + p.n - 1: (p.page, layer) for p in paragraphs}


def call_notice(a: int, b: int, marks: Marks) -> str:
    """The notice for one call mapping paragraphs ``a..b`` (1-based, global
    numbering in a bundle) — ``""`` when none of them is from the text layer.

    A paragraph with no page number in a file that has text-layer pages counts
    as one of them — it cannot be told apart.
    """
    hits: list[int] = []
    unplaced: set[int] = set()
    for n in range(a, b + 1):
        page, layer = marks.get(n, (None, frozenset()))
        if not layer:
            continue
        if page is None:
            hits.append(n)
            unplaced.update(layer)
        elif page in layer:
            hits.append(n)
    if not hits:
        return ""
    runs = _runs(hits)
    shown = ", ".join(pid(x) if x == y else f"{pid(x)}–{pid(y)}" for x, y in runs[:MAX_NOTICE_RUNS])
    if len(runs) > MAX_NOTICE_RUNS:
        shown += f", and {len(runs) - MAX_NOTICE_RUNS} more runs"
    where = f"Paragraphs {shown}"
    if unplaced:
        where += (
            f" (this file has no page numbers: pages {format_ranges(sorted(unplaced))} of it "
            "came from the text layer, and any of its paragraphs may be one of them)"
        )
    return "\n".join([
        "## Pages taken from the PDF's text layer, not by OCR (app notice — read before mapping)",
        f"{where} come from pages whose text was taken from the text layer stored inside the "
        "PDF, not read by OCR (the user's OCR allowance did not cover them, or OCR failed on "
        "them). Their words are mostly right, but their dates, numbers, amounts, article and "
        "case numbers and the order of events may be scrambled or reversed.",
        "- Map and rate these paragraphs exactly like every other paragraph.",
        "- In the `what` of a section drawn from them, never state a date, number or amount "
        "as a fact.",
        "- The app attaches a reliability warning to those sections itself — do not write one.",
    ])


def section_note(pages: Sequence[int] | None, file_pages: Sequence[int]) -> str:
    """The warning at the top of a section's text. ``pages``: the section's
    text-layer pages; None when the section's pages are not known."""
    if pages is None:
        return (
            f"〔[نظام] بعض صفحات هذا الملف ({format_ranges(sorted(file_pages))}) استُخرج "
            "نصها من طبقة النص المخزّنة في الملف لا بـOCR، وقد يكون هذا المقطع منها: "
            f"{TEXT_LAYER_CAUTION}〕"
        )
    which = "الصفحة" if len(pages) == 1 else "الصفحات"
    return (
        f"〔[نظام] نص {which} {format_ranges(sorted(pages))} في هذا المقطع مستخرج من "
        f"طبقة النص المخزّنة في الملف لا بـOCR: {TEXT_LAYER_CAUTION}〕"
    )


def stamp_rows(rows: list[dict], layer: frozenset[int]) -> tuple[list[dict], int]:
    """``(rows, stamped)`` — every section overlapping ``layer`` gets
    :func:`section_note` at the top of its ``content_md``. Runs after the joins
    (``page_from`` / ``page_to`` then span the joined section)."""
    if not layer:
        return rows, 0
    out: list[dict] = []
    stamped = 0
    for row in rows:
        first, last = row.get("page_from"), row.get("page_to")
        if first is None or last is None:
            note = section_note(None, sorted(layer))
        else:
            hit = [p for p in sorted(layer) if int(first) <= p <= int(last)]
            note = section_note(hit, sorted(layer)) if hit else ""
        if note:
            row = {**row, "content_md": f"{note}\n{row.get('content_md') or ''}"}
            stamped += 1
        out.append(row)
    return out, stamped


__all__ = [
    "MAX_NOTICE_RUNS",
    "Marks",
    "call_notice",
    "marks_for",
    "section_note",
    "stamp_rows",
    "text_layer_pages",
]
