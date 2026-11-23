"""Text of a PARTIAL read — the pages the budget allowed, with the skips marked.

When the OCR page budget (``shared/quota/ocr_budget.py``) reads only some pages
of a file, every agent that later reads ``content_md`` must know which pages it
is looking at and that the rest were never read (owner rule D8a, 2026-11-16).
So the text carries:

* a header naming what was read and of how many pages, and
* a marker in place of every run of skipped pages (and after the last block,
  when the document goes on).

Both use the app's ``〔[نظام] …〕`` form, which the agents already read as "from
the app, not from anyone in the chat". Page offsets are computed over the text
WITH the markers, so ``attachment_pages.char_start`` still points at each page
(the segmenter checks this in ``coerce_pages``).

A complete read never comes through here: its text stays byte-identical to
``mistral_ocr.join_pages``.
"""
from __future__ import annotations

from typing import Sequence

from shared.quota.ocr_budget import format_ranges

from .mistral_ocr import join_pages
from .models import OcrPage


def _gap_marker(first: int, last: int | None) -> str:
    if last is None:
        return f"〔[نظام] ما بعد الصفحة {first - 1} لم يُستخرج.〕"
    if first == last:
        return f"〔[نظام] الصفحة {first} لم تُستخرج.〕"
    return f"〔[نظام] الصفحات {first}–{last} ({last - first + 1} صفحة) لم تُستخرج.〕"


def partial_header(
    read: Sequence[int], total_pages: int | None, *, overflow: bool = False,
) -> str:
    """The notice that opens a partial read's text (owner's wording 2026-11-16:
    «استخراج», not «قراءة»). ``overflow``: the pages past the budget were read
    from the text layer, so what is still missing had no text there (or was past
    ``TEXT_LAYER_MAX_PAGES``)."""
    if overflow and total_pages is not None:
        return (
            f"〔[نظام] استُخرجت {len(read)} صفحة فقط من أصل {total_pages} صفحة في هذا "
            f"الملف. الصفحات المستخرجة: {format_ranges(read)}. بقية الصفحات تجاوزت حدّ "
            "رصيد الاستخراج لدى المستخدم ولم يُستخرج لها نص من الملف، فلا يُبنى على "
            "محتواها ولا يُفترض.〕"
        )
    if total_pages is None:
        return (
            f"〔[نظام] استُخرجت أول {len(read)} صفحة فقط من هذا الملف بسبب حدّ رصيد "
            "الاستخراج لدى المستخدم، وقد تكون فيه صفحات أخرى لم تُستخرج، فلا يُبنى "
            "على ما لم يُستخرج ولا يُفترض.〕"
        )
    return (
        f"〔[نظام] استُخرجت {len(read)} صفحة فقط من أصل {total_pages} صفحة في هذا "
        "الملف بسبب حدّ رصيد الاستخراج لدى المستخدم. الصفحات المستخرجة: "
        f"{format_ranges(read)}. لم تُستخرج بقية الصفحات، فلا يُبنى على محتواها "
        "ولا يُفترض.〕"
    )


#: What every agent must know about text-layer pages (owner, 2026-11-23: do not
#: trust their dates, numbers or order of events). Shared by the notices here and
#: the segmenter's per-section warning.
TEXT_LAYER_CAUTION = (
    "كلماتها في الغالب صحيحة، لكن التواريخ والأرقام والمبالغ وأرقام المواد والقضايا "
    "وترتيب الأحداث فيها غير موثوقة (قد تكون مبعثرة أو مقلوبة)، فلا يُعتمد على شيء "
    "منها ولا يُبنى عليه إلا مع التنبيه إلى أنه غير مؤكد، أو بعد تأكيده من المستخدم."
)


def text_layer_header(pages: Sequence[int]) -> str:
    """The notice for pages read from the PDF's own text layer (Mistral failed)."""
    return (
        f"〔[نظام] تعذّر OCR على الصفحات {format_ranges(pages)} فاستُخرج نصها من "
        f"طبقة النص المخزّنة في الملف نفسه لا بـOCR: {TEXT_LAYER_CAUTION}〕"
    )


def overflow_header(pages: Sequence[int]) -> str:
    """The notice for pages past the OCR page budget, read from the text layer."""
    return (
        f"〔[نظام] الصفحات {format_ranges(pages)} تجاوزت حدّ رصيد الاستخراج (OCR) لدى "
        "المستخدم، فاستُخرج نصها من طبقة النص المخزّنة في الملف نفسه لا بـOCR: "
        f"{TEXT_LAYER_CAUTION}〕"
    )


def _lost_marker(first: int, last: int) -> str:
    if first == last:
        return f"〔[نظام] تعذّر استخراج الصفحة {first}.〕"
    return f"〔[نظام] تعذّر استخراج الصفحات {first}–{last}.〕"


def _text_layer_marker(first: int, last: int) -> str:
    span = str(first) if first == last else f"{first}–{last}"
    return f"〔[نظام] من طبقة نص الملف: {'الصفحة' if first == last else 'الصفحات'} {span}.〕"


def _runs(pages: Sequence[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for p in sorted(pages):
        if runs and p == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], p)
        else:
            runs.append((p, p))
    return runs


def assemble_marked(
    pages: Sequence[OcrPage],
    total_pages: int | None,
    *,
    budget_partial: bool,
    text_layer_pages: Sequence[int] = (),
    lost_pages: Sequence[int] = (),
    overflow_pages: Sequence[int] = (),
) -> tuple[str, list[OcrPage]]:
    """``content_md`` for a read that is partial (budget), mixed (some pages
    from the text layer) or lossy (pages neither source could read), plus each
    page re-anchored in it.

    ``pages`` are the pages read, from either source (any ``char_start`` on
    them is ignored). ``total_pages`` None = the document's length is unknown.
    ``overflow_pages`` — the text-layer pages read because the budget did not
    cover them (the rest of ``text_layer_pages`` are Mistral failures); each
    reason gets its own notice.
    Missing pages are marked as never extracted (budget) or as failed (lost);
    a run of text-layer pages is announced where it starts.
    """
    ordered = sorted(pages, key=lambda p: p.page_no)
    read = [p.page_no for p in ordered]
    lost = set(lost_pages)
    from_layer = set(text_layer_pages)
    past_budget = set(overflow_pages) & from_layer
    layer_starts = {first for first, _ in _runs(from_layer)}
    layer_run_end = dict(_runs(from_layer))

    parts: list[str] = []
    if budget_partial:
        parts.append(partial_header(read, total_pages, overflow=bool(past_budget)))
    if from_layer - past_budget:
        parts.append(text_layer_header(sorted(from_layer - past_budget)))
    if past_budget:
        parts.append(overflow_header(sorted(past_budget)))

    def gap(first: int, last: int | None) -> None:
        if last is None:
            parts.append(_gap_marker(first, None))
            return
        # Split the missing run into lost pages and pages the budget skipped.
        p = first
        while p <= last:
            kind = p in lost
            q = p
            while q + 1 <= last and ((q + 1) in lost) == kind:
                q += 1
            parts.append(_lost_marker(p, q) if kind else _gap_marker(p, q))
            p = q + 1

    page_slots: list[int] = []
    prev = 0
    for p in ordered:
        if p.page_no > prev + 1:
            gap(prev + 1, p.page_no - 1)
        if p.page_no in layer_starts:
            parts.append(_text_layer_marker(p.page_no, layer_run_end[p.page_no]))
        page_slots.append(len(parts))
        parts.append(p.markdown)
        prev = p.page_no
    if total_pages is None:
        if budget_partial:
            gap(prev + 1, None)
    elif prev < total_pages:
        gap(prev + 1, total_pages)

    text, offsets = join_pages(parts)
    return text, [
        OcrPage(page_no=p.page_no, markdown=p.markdown, char_start=offsets[slot])
        for p, slot in zip(ordered, page_slots)
    ]


def assemble_partial(
    pages: Sequence[OcrPage], total_pages: int | None
) -> tuple[str, list[OcrPage]]:
    """``content_md`` for a partial (budget) read — see :func:`assemble_marked`."""
    return assemble_marked(pages, total_pages, budget_partial=True)
