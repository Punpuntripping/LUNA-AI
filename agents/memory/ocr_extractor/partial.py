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
        return f"〔[نظام] ما بعد الصفحة {first - 1} لم يُقرأ.〕"
    if first == last:
        return f"〔[نظام] الصفحة {first} لم تُقرأ.〕"
    return f"〔[نظام] الصفحات {first}–{last} ({last - first + 1} صفحة) لم تُقرأ.〕"


def partial_header(read: Sequence[int], total_pages: int | None) -> str:
    """The notice that opens a partial read's text."""
    if total_pages is None:
        return (
            f"〔[نظام] قُرئت أول {len(read)} صفحة فقط من هذا الملف بسبب حدّ رصيد "
            "القراءة لدى المستخدم، وقد تكون فيه صفحات أخرى لم تُقرأ، فلا يُبنى على "
            "ما لم يُقرأ ولا يُفترض.〕"
        )
    return (
        f"〔[نظام] قُرئت {len(read)} صفحة فقط من أصل {total_pages} صفحة في هذا "
        "الملف بسبب حدّ رصيد القراءة لدى المستخدم. الصفحات المقروءة: "
        f"{format_ranges(read)}. لم تُقرأ بقية الصفحات، فلا يُبنى على محتواها "
        "ولا يُفترض.〕"
    )


def assemble_partial(
    pages: Sequence[OcrPage], total_pages: int | None
) -> tuple[str, list[OcrPage]]:
    """``content_md`` for a partial read, plus each page re-anchored in it.

    ``pages`` are the pages OCR returned (any ``char_start`` on them is ignored).
    ``total_pages`` None = the document's length is unknown; the text then says
    there may be more after the last page read.
    """
    ordered = sorted(pages, key=lambda p: p.page_no)
    read = [p.page_no for p in ordered]
    parts: list[str] = [partial_header(read, total_pages)]
    page_slots: list[int] = []
    prev = 0
    for p in ordered:
        if p.page_no > prev + 1:
            parts.append(_gap_marker(prev + 1, p.page_no - 1))
        page_slots.append(len(parts))
        parts.append(p.markdown)
        prev = p.page_no
    if total_pages is None:
        parts.append(_gap_marker(prev + 1, None))
    elif prev < total_pages:
        parts.append(_gap_marker(prev + 1, total_pages))

    text, offsets = join_pages(parts)
    return text, [
        OcrPage(page_no=p.page_no, markdown=p.markdown, char_start=offsets[slot])
        for p, slot in zip(ordered, page_slots)
    ]
