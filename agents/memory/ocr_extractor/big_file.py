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
   back to its real page number. A cut Mistral fails on (timeout, dropped
   connection, error) is read from the PDF's own text layer instead
   (text_layer.py) — free, never billed; its pages that have no text layer
   (image pages) are reported as lost.

With ``overflow`` (owner, 2026-11-23) the pages the budget did NOT choose are
read from the text layer too — free, alongside the Mistral cuts — up to
``TEXT_LAYER_MAX_PAGES`` pages for the whole file. A file with no budget left
comes here with no pages chosen and is read from its text layer alone.

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
from dataclasses import dataclass
from typing import Sequence

import httpx

from shared.quota.ocr_budget import pick_pages

from .mistral_ocr import join_pages, ocr_document
from .models import OcrDocumentResult, OcrPage
from .text_layer import TextLayerPage, read_pages, sample_digits_reversed

#: ``OcrDocumentResult.model`` when every page came from the text layer.
TEXT_LAYER_MODEL = "pdf-text-layer"

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
#: Mistral's share of the 10-minute OCR wait (helper OCR_CEILING_S = 600 s),
#: counted from the moment this file's cuts start: no Mistral call runs past
#: it, and a cut whose turn comes after it goes straight to the text layer —
#: so the fallback lands inside the wait even when the plan is many rounds of
#: cuts (owner: Mistral stalled for minutes on 2026-11-16).
MISTRAL_DEADLINE_S = 480.0
#: A call needs at least this much of the deadline left to be worth starting.
MIN_CALL_S = 30.0
#: Pages extracted from one file at most, OCR and text layer together, when the
#: text layer fills in past the budget. The largest read the pipeline already
#: handled (Max plan 666, or 1,000 within 7 days of the plan's end; Mistral's own
#: per-document limit). Past it a 10,000-page PDF would outrun the 10-minute OCR
#: wait (~0.1 s a page) and fan out into ~120 segmenter calls.
TEXT_LAYER_MAX_PAGES = 1_000


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


def overflow_pages(
    total: int, chosen: Sequence[int], ceiling: int | None = None,
) -> tuple[int, ...]:
    """The pages the budget did not choose, in page order, so that ``chosen``
    plus these come to at most ``ceiling`` pages (``TEXT_LAYER_MAX_PAGES``)."""
    room = max(0, (TEXT_LAYER_MAX_PAGES if ceiling is None else ceiling) - len(chosen))
    taken = set(chosen)
    out: list[int] = []
    for p in range(1, total + 1):
        if len(out) >= room:
            break
        if p not in taken:
            out.append(p)
    return tuple(out)


async def ocr_big_pdf(
    file_url: str,
    pages: Sequence[int] | None,
    planned_total: int | None,
    *,
    overflow: bool = False,
) -> OcrDocumentResult:
    """OCR the chosen ``pages`` of the PDF at ``file_url`` by cutting them out.

    ``pages``: 1-based page numbers the budget chose (None = every page; ``()``
    = none — with ``overflow`` the file is then read from its text layer alone).
    ``planned_total``: the page count the plan was made against (None = unknown).
    ``overflow``: also read the pages the budget did not choose, from the text
    layer (see :func:`overflow_pages`).
    """
    async with _slots():
        return await _ocr_big_pdf(file_url, pages, planned_total, overflow=overflow)


async def _ocr_big_pdf(
    file_url: str,
    pages: Sequence[int] | None,
    planned_total: int | None,
    *,
    overflow: bool = False,
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
            extra = overflow_pages(total, chosen) if overflow else ()
            groups = [chosen[i:i + CHUNK_MAX_PAGES] for i in range(0, len(chosen), CHUNK_MAX_PAGES)]

            sem = asyncio.Semaphore(CONCURRENCY)
            pdf_lock = asyncio.Lock()  # one pypdf call at a time (shared reader)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + MISTRAL_DEADLINE_S
            # Set by the first outage-type failure: the rest of the file skips
            # Mistral. The digit order is decided once per file, on first use.
            state: dict = {"mistral_down": False, "flip_digits": None}
            every_read = tuple(sorted({*chosen, *extra}))

            async def text_layer(cut_pages: tuple[int, ...], *, past_budget: bool = False) -> _CutResult:
                async with pdf_lock:
                    if state["flip_digits"] is None:
                        state["flip_digits"] = await asyncio.to_thread(
                            sample_digits_reversed, reader, every_read,
                        )
                    read = await asyncio.to_thread(
                        read_pages, reader, cut_pages, reverse_digits=state["flip_digits"],
                    )
                return _from_text_layer(read, past_budget=past_budget)

            async def run_overflow() -> list[_CutResult]:
                # One piece at a time: never more than one piece queued for the
                # reader lock ahead of a Mistral cut.
                return [
                    await text_layer(extra[i:i + CHUNK_MAX_PAGES], past_budget=True)
                    for i in range(0, len(extra), CHUNK_MAX_PAGES)
                ]

            async def one_cut(cut_pages: tuple[int, ...], data: bytes) -> _CutResult:
                left = deadline - loop.time()
                if state["mistral_down"] or left < MIN_CALL_S:
                    return await text_layer(cut_pages)
                url = "data:application/pdf;base64," + base64.b64encode(data).decode("ascii")
                try:
                    res = await ocr_document(
                        url, _PDF_MIME, expected_pages=len(cut_pages), timeout_s=left,
                    )
                    return _from_mistral(cut_pages, res)
                except Exception as exc:  # noqa: BLE001 — Mistral failed: text layer
                    if _is_outage(exc):
                        state["mistral_down"] = True
                    logger.warning(
                        "OCR big-file: Mistral failed on pages %s–%s — %s %s: %s — text layer",
                        cut_pages[0], cut_pages[-1], type(exc).__name__,
                        getattr(exc, "status_code", ""), str(exc)[:300],
                    )
                    return await text_layer(cut_pages)

            async def run(group: tuple[int, ...]) -> list[_CutResult]:
                async with sem:
                    async with pdf_lock:
                        cuts = await asyncio.to_thread(cut_group, reader, group)
                    return [await one_cut(cut_pages, data) for cut_pages, data in cuts]

            batches = await asyncio.gather(run_overflow(), *(run(g) for g in groups))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    return _assemble([c for batch in batches for c in batch], total, len(groups), chosen)


def _is_outage(exc: BaseException) -> bool:
    """Mistral is down or stalled (not a problem with this one cut): a timeout,
    a dropped or refused connection, a 5xx or a 429. The rest of the file then
    goes straight to the text layer."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError, httpx.HTTPError)):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status >= 500 or status == 429)


async def text_layer_document(
    file_url: str,
    pages: Sequence[int] | None,
    planned_total: int | None,
    *,
    overflow: bool = False,
) -> OcrDocumentResult:
    """The chosen ``pages`` of the PDF at ``file_url``, read from its own text
    layer only — the fallback when Mistral failed on the whole file (the URL
    path, files within Mistral's 50 MB). Free: nothing here is billed. Not
    queued behind the big-file slots, which a Mistral outage keeps busy.
    ``overflow``: also the pages the plan left out (the file turned out longer
    than the client counted). Raises if the file cannot be opened."""
    from pypdf import PdfReader

    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        await _download(file_url, path)
        with open(path, "rb") as fh:
            reader = await asyncio.to_thread(PdfReader, fh)
            total = await asyncio.to_thread(lambda: len(reader.pages))
            chosen = choose_pages(total, pages, planned_total)
            extra = overflow_pages(total, chosen) if overflow else ()
            flip = await asyncio.to_thread(
                sample_digits_reversed, reader, tuple(sorted({*chosen, *extra})),
            )
            read = await asyncio.to_thread(read_pages, reader, chosen, reverse_digits=flip)
            read_extra = (
                await asyncio.to_thread(read_pages, reader, extra, reverse_digits=flip)
                if extra else []
            )
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return _assemble(
        [_from_text_layer(read), _from_text_layer(read_extra, past_budget=True)],
        total, 1, chosen,
    )


# ---------------------------------------------------------------------------
# Assembly: Mistral cuts and text-layer cuts, in page order
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CutResult:
    pages: list[tuple[int, str]]      # (real page number, text)
    model: str = ""
    text_layer: tuple[int, ...] = ()  # pages taken from the text layer
    lost: tuple[int, ...] = ()        # planned pages with no text at all
    overflow: tuple[int, ...] = ()    # text-layer pages past the budget
    blank: tuple[int, ...] = ()       # pages past the budget with no text layer


def _from_mistral(cut_pages: tuple[int, ...], res: OcrDocumentResult) -> _CutResult:
    numbered: list[tuple[int, str]] = []
    # Mistral numbers a cut's pages from 0 in cut order; map them back.
    for local, page in enumerate(res.pages):
        idx = page.page_no - 1 if 0 < page.page_no <= len(cut_pages) else local
        if idx < len(cut_pages):
            numbered.append((cut_pages[idx], page.markdown))
    return _CutResult(pages=numbered, model=res.model)


def _from_text_layer(read: Sequence[TextLayerPage], *, past_budget: bool = False) -> _CutResult:
    """``past_budget``: pages the budget did not choose — a blank one is simply
    not extracted (a budget gap), never "lost" (a planned page that failed)."""
    usable = tuple(p.page_no for p in read if p.usable)
    unusable = tuple(p.page_no for p in read if not p.usable)
    return _CutResult(
        pages=[(p.page_no, p.text) for p in read if p.usable],
        model=TEXT_LAYER_MODEL,
        text_layer=usable,
        lost=() if past_budget else unusable,
        overflow=usable if past_budget else (),
        blank=unusable if past_budget else (),
    )


def _assemble(
    cuts: Sequence[_CutResult], total: int, groups: int, chosen: Sequence[int],
) -> OcrDocumentResult:
    numbered = sorted((pg for c in cuts for pg in c.pages), key=lambda t: t[0])
    text_layer = tuple(sorted(p for c in cuts for p in c.text_layer))
    lost = tuple(sorted(p for c in cuts for p in c.lost))
    overflow = tuple(sorted(p for c in cuts for p in c.overflow))
    blank = tuple(sorted(p for c in cuts for p in c.blank))
    # Not extracted because of the budget: neither chosen nor read past it.
    covered = {*chosen, *overflow}
    unread = tuple(p for p in range(1, total + 1) if p not in covered)
    # The Mistral model id when any cut used it, else the text-layer marker.
    model = next((c.model for c in cuts if c.model and c.model != TEXT_LAYER_MODEL), "") \
        or (TEXT_LAYER_MODEL if text_layer else "")
    text, offsets = join_pages([md for _, md in numbered])
    logger.info(
        "OCR big-file: %d of %d pages in %d group(s) (%d from the text layer, %d of them "
        "past the budget, %d lost, %d not extracted), %d chars",
        len(numbered), total, groups, len(text_layer), len(overflow), len(lost),
        len(unread), len(text),
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
        text_layer_pages=text_layer,
        lost_pages=lost,
        overflow_pages=overflow,
        overflow_blank=blank,
        unread_pages=unread,
    )
