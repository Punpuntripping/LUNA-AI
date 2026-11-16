"""Mistral OCR API wrapper.

A thin async wrapper over the ``mistralai`` SDK's ``client.ocr.process_async``.
The file is passed **by URL** (a Supabase Storage signed URL) — Mistral fetches
it server-side, so there's no download/upload round-trip.

This is a direct SDK call (like the project's embeddings / rerankers): OCR is
not a chat model, so it bypasses the tier / FallbackModel system entirely.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Sequence

from mistralai import Mistral

from shared.config import get_settings

logger = logging.getLogger(__name__)

# There is no page cap here any more (removed 2026-11-16, plan
# ocr_page_budget.md D1): which pages are read is decided per file by the OCR
# page budget (shared/quota/ocr_budget.py) and passed in as ``pages``. The only
# hard ceilings are Mistral's own per-document limits.
MISTRAL_MAX_PAGES = 1_000
MISTRAL_MAX_BYTES = 50 * 1024 * 1024

# Per-call OCR timeout (seconds): a base plus a per-page allowance, capped. On
# expiry ``asyncio.wait_for`` cancels the SDK call → TimeoutError escapes
# ocr_document → the runner marks the row ``failed`` (never retried) and the
# turn continues without it. Measured 2026-11-16: a call took 45–60 s whatever
# its size locally (1 page: 47 s; 30 pages: 57 s), but from production two
# 50-page calls did not return within 170 s and the file was lost. Owner
# 2026-11-16: 10 minutes; the user sees «ريحان يستخرج المستندات» meanwhile
# (agents/helpers/registry.OCR_CEILING_S waits as long).
# 9 minutes, not 10: when Mistral fails the text-layer fallback needs time
# inside the same 10-minute wait (helper OCR_CEILING_S). In practice Mistral's
# own gateway drops a stuck call at about 4 minutes (ReadError, 2026-11-16).
OCR_TIMEOUT_S = 540.0


def ocr_timeout_s(page_count: int | None = None) -> float:  # noqa: ARG001 — kept for callers
    """The timeout for one OCR call: 9 minutes, whatever its size."""
    return OCR_TIMEOUT_S

# MIME types Mistral OCR accepts in this module.
_PDF_MIME = "application/pdf"
_IMAGE_MIMES = {"image/png", "image/jpeg"}

from .models import OcrDocumentResult, OcrPage


def _build_document(file_url: str, mime_type: str) -> dict:
    """Map a (url, mime) pair to the SDK's ``document`` argument shape."""
    if mime_type == _PDF_MIME:
        return {"type": "document_url", "document_url": file_url}
    if mime_type in _IMAGE_MIMES:
        return {"type": "image_url", "image_url": file_url}
    # Defensive: the runner already filters mime types, but never silently
    # mis-route an unexpected type.
    raise ValueError(f"unsupported mime type for OCR: {mime_type!r}")


async def ocr_document(
    file_url: str,
    mime_type: str,
    pages: Sequence[int] | None = None,
    *,
    expected_pages: int | None = None,
    timeout_s: float | None = None,
) -> OcrDocumentResult:
    """Run Mistral OCR on a single document given its URL.

    Args:
        file_url: A fetchable URL — a Supabase Storage signed URL, a Mistral
            file-store signed URL, or a ``data:`` URL.
        mime_type: One of ``application/pdf``, ``image/png``, ``image/jpeg``.
        pages: 1-based page numbers to read, ascending. None = every page.
            Ignored for images (one page).
        expected_pages: the document's length when ``pages`` is None and it
            is known (a cut) — sizes the timeout.
        timeout_s: overrides the timeout (a caller with its own deadline).

    Returns:
        An ``OcrDocumentResult`` with the joined page markdown, the number of
        pages actually extracted, and the model id used.

    Raises:
        Any exception from the Mistral SDK on API failure — the caller
        (``run_ocr_extraction``) catches it and marks the row ``failed``.
    """
    settings = get_settings()
    model = settings.MISTRAL_OCR_MODEL
    document = _build_document(file_url, mime_type)

    kwargs: dict = {}
    if pages is not None and mime_type == _PDF_MIME:
        # Mistral numbers pages from 0.
        kwargs["pages"] = [p - 1 for p in pages]

    client = Mistral(api_key=settings.MISTRAL_API_KEY)
    try:
        response = await asyncio.wait_for(
            client.ocr.process_async(model=model, document=document, **kwargs),
            timeout=timeout_s if timeout_s is not None
            else ocr_timeout_s(len(pages) if pages is not None else expected_pages),
        )
    finally:
        # The SDK holds an httpx client; close it so we don't leak sockets.
        try:
            await client.sdk_configuration.async_client.aclose()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    pages = list(getattr(response, "pages", None) or [])
    markdowns = [(getattr(p, "markdown", None) or "") for p in pages]
    text, offsets = join_pages(markdowns)
    page_count = len(pages)
    used_model = getattr(response, "model", None) or model
    numbers = _page_numbers(pages)

    logger.info(
        "Mistral OCR done: %d pages extracted, %d chars (model=%s)",
        page_count,
        len(text),
        used_model,
    )
    return OcrDocumentResult(
        text=text,
        page_count=page_count,
        model=used_model,
        pages=[
            OcrPage(page_no=n, markdown=md, char_start=off)
            for n, md, off in zip(numbers, markdowns, offsets)
        ],
    )


#: How pages are joined into ``content_md``. Changing it changes every stored
#: ``content_md`` and every ``attachment_pages.char_start`` — don't.
PAGE_SEPARATOR = "\n\n"


def join_pages(markdowns: list[str]) -> tuple[str, list[int]]:
    """``content_md`` from the page markdowns, plus each page's offset in it.

    The text is byte-identical to what OCR has always stored:
    ``"\\n\\n".join(markdowns).strip()``. The offset of page ``i`` is where its
    markdown starts in that text: its position in the raw join, less the
    whitespace ``.strip()`` removed from the front, clamped to ``[0, len(text)]``
    (a page that lies wholly in the stripped head or tail — an empty first or
    last page — gets ``0`` / ``len(text)``). A first page with leading whitespace
    therefore starts at ``0`` while its markdown begins with that whitespace.
    """
    raw = PAGE_SEPARATOR.join(markdowns)
    text = raw.strip()
    lead = len(raw) - len(raw.lstrip())
    offsets: list[int] = []
    pos = 0
    for md in markdowns:
        offsets.append(min(max(pos - lead, 0), len(text)))
        pos += len(md) + len(PAGE_SEPARATOR)
    return text, offsets


def _page_numbers(pages: list) -> list[int]:
    """1-based page numbers: Mistral's 0-based ``index`` when every page has a
    distinct int one, else the position in the response."""
    idx = [getattr(p, "index", None) for p in pages]
    if idx and all(isinstance(i, int) and not isinstance(i, bool) and i >= 0 for i in idx) \
            and len(set(idx)) == len(idx):
        return [i + 1 for i in idx]
    return list(range(1, len(pages) + 1))
