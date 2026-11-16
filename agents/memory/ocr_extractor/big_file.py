"""OCR by cutting: send Mistral only the pages the budget chose.

Mistral OCR refuses a document over 1,000 pages — even when only a few pages
are asked for ("document_parser_too_many_pages", seen 2026-11-16 on a
1,183-page file) — and documents over 50 MB. So when a PDF is read in part, is
over either limit, or has an unknown page count, this module:

1. downloads it (streamed to a temp file),
2. counts its real pages (the client's count is only an estimate) and, if the
   plan was made against a different count, re-picks the same number of pages
   for the real length (``ocr_budget.pick_pages``),
3. cuts the chosen pages into small PDFs — dropping each page's ``/PieceInfo``,
   the authoring tool's private data, which is never drawn and can make a
   10-page cut weigh 40 MB (InDesign/Illustrator exports; the same 10 pages
   come to 0.5 MB without it) — and
4. OCRs the cuts concurrently as ``data:`` URLs, mapping every returned page
   back to its real page number.

Memory stays bounded: cuts are made as their turn comes (``CONCURRENCY`` in
flight), not all up front.

The result has the same shape as ``mistral_ocr.ocr_document``'s, plus the real
``total_pages``. Raises on failure; the runner marks the row ``failed``.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import tempfile
from typing import Sequence

import httpx

from shared.quota.ocr_budget import pick_pages

from .mistral_ocr import join_pages, ocr_document
from .models import OcrDocumentResult, OcrPage

logger = logging.getLogger(__name__)

#: A cut is at most this many pages and (unless it is a single page) this many
#: bytes — small enough for a ``data:`` URL and for one call's timeout.
CHUNK_MAX_PAGES = 50
CHUNK_MAX_BYTES = 20 * 1024 * 1024
#: Cuts OCR'd at once. A Mistral call took 45–60 s whatever its size
#: (1 page: 47 s, 30 pages: 57 s; 2026-11-16) and parallel calls overlap
#: (4 × 10 pages: 60 s wall), so wall time is set by the number of rounds.
CONCURRENCY = 8
#: Streaming download of the original from storage.
DOWNLOAD_TIMEOUT_S = 180.0
#: Files cut at once in one server process. Opening and cutting a 202 MB,
#: 1,183-page PDF peaked at ~280 MB of memory (100 pages) to ~440 MB (666
#: pages), so a burst of big uploads waits its turn instead of stacking up.
MAX_FILES_AT_ONCE = 2
#: One semaphore per event loop (the server runs one; tests run many).
_file_slots: dict[int, asyncio.Semaphore] = {}


def _slots() -> asyncio.Semaphore:
    key = id(asyncio.get_running_loop())
    sem = _file_slots.get(key)
    if sem is None:
        sem = _file_slots[key] = asyncio.Semaphore(MAX_FILES_AT_ONCE)
    return sem

_PDF_MIME = "application/pdf"
#: Page keys that are never drawn and can be huge.
_UNDRAWN_PAGE_KEYS = ("/PieceInfo",)


async def _download(url: str, dest: str) -> None:
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as fh:
                async for chunk in resp.aiter_bytes(1024 * 1024):
                    fh.write(chunk)


def _strip_undrawn(page) -> None:
    """Drop what OCR never sees but a cut would drag along.

    * ``/PieceInfo`` — the authoring tool's private data (InDesign/Illustrator).
    * ``/Link`` annotations — a link's target page is copied into the cut with
      it: one table-of-contents page with 27 links came to 41 MB, 0.08 MB
      without them. Other annotations (stamps, form fields, notes) are kept —
      they can carry visible text.
    """
    from pypdf.generic import ArrayObject, NameObject

    for key in _UNDRAWN_PAGE_KEYS:
        if key in page:
            del page[NameObject(key)]
    annots = page.get("/Annots")
    if annots is None:
        return
    kept = ArrayObject(
        a for a in annots.get_object() if a.get_object().get("/Subtype") != "/Link"
    )
    if kept:
        page[NameObject("/Annots")] = kept
    else:
        del page[NameObject("/Annots")]


def _write(reader, pages: Sequence[int]) -> bytes:
    """One PDF holding ``pages`` (1-based) of ``reader``, undrawn data dropped."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for p in pages:
        page = reader.pages[p - 1]
        _strip_undrawn(page)
        writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def cut_group(reader, pages: Sequence[int]) -> list[tuple[tuple[int, ...], bytes]]:
    """Cut ``pages`` into PDFs no heavier than ``CHUNK_MAX_BYTES`` (halving a
    group that comes out too heavy; a single page goes as it is)."""
    data = _write(reader, pages)
    if len(data) > CHUNK_MAX_BYTES and len(pages) > 1:
        mid = len(pages) // 2
        return cut_group(reader, pages[:mid]) + cut_group(reader, pages[mid:])
    return [(tuple(pages), data)]


def choose_pages(total: int, pages: Sequence[int] | None, planned_total: int | None) -> tuple[int, ...]:
    """The pages to read once the real length is known."""
    if pages is None:
        return tuple(range(1, total + 1))
    if planned_total != total:
        # Planned against the client's estimate (or none): the same number of
        # pages, re-picked for the real length.
        return pick_pages(total, len(pages))
    return tuple(pages)


async def ocr_big_pdf(
    file_url: str,
    pages: Sequence[int] | None,
    planned_total: int | None,
) -> OcrDocumentResult:
    """OCR the chosen ``pages`` of the PDF at ``file_url`` by cutting them out.

    ``pages``: 1-based page numbers the budget chose (None = every page).
    ``planned_total``: the page count the plan was made against (None = unknown).
    """
    async with _slots():
        return await _ocr_big_pdf(file_url, pages, planned_total)


async def _ocr_big_pdf(
    file_url: str,
    pages: Sequence[int] | None,
    planned_total: int | None,
) -> OcrDocumentResult:
    from pypdf import PdfReader

    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        await _download(file_url, path)
        with open(path, "rb") as fh:
            reader = await asyncio.to_thread(PdfReader, fh)
            total = await asyncio.to_thread(lambda: len(reader.pages))
            chosen = choose_pages(total, pages, planned_total)
            groups = [chosen[i:i + CHUNK_MAX_PAGES] for i in range(0, len(chosen), CHUNK_MAX_PAGES)]

            sem = asyncio.Semaphore(CONCURRENCY)
            pdf_lock = asyncio.Lock()  # one pypdf write at a time (shared reader)

            async def run(group: tuple[int, ...]) -> list[tuple[tuple[int, ...], OcrDocumentResult]]:
                async with sem:
                    async with pdf_lock:
                        cuts = await asyncio.to_thread(cut_group, reader, group)
                    out = []
                    for cut_pages, data in cuts:
                        url = "data:application/pdf;base64," + base64.b64encode(data).decode("ascii")
                        res = await ocr_document(url, _PDF_MIME, expected_pages=len(cut_pages))
                        out.append((cut_pages, res))
                    return out

            batches = await asyncio.gather(*(run(g) for g in groups))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    numbered: list[tuple[int, str]] = []
    model = ""
    for batch in batches:
        for cut_pages, res in batch:
            model = model or res.model
            # Mistral numbers a cut's pages from 0 in cut order; map them back.
            for local, page in enumerate(res.pages):
                idx = page.page_no - 1 if 0 < page.page_no <= len(cut_pages) else local
                if idx < len(cut_pages):
                    numbered.append((cut_pages[idx], page.markdown))
    numbered.sort(key=lambda t: t[0])

    text, offsets = join_pages([md for _, md in numbered])
    logger.info(
        "OCR big-file: %d of %d pages in %d group(s), %d chars",
        len(numbered), total, len(groups), len(text),
    )
    return OcrDocumentResult(
        text=text,
        page_count=len(numbered),
        model=model,
        pages=[
            OcrPage(page_no=n, markdown=md, char_start=off)
            for (n, md), off in zip(numbered, offsets)
        ],
        total_pages=total,
    )
