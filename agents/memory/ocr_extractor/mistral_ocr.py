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

from mistralai import Mistral

from shared.config import get_settings

logger = logging.getLogger(__name__)

# Page cap handed to Mistral. The first ``OCR_MAX_PAGES`` pages are extracted;
# the rest of an over-long document are ignored (never rejected).
OCR_MAX_PAGES = 30

# Per-attachment OCR call timeout (seconds). On expiry ``asyncio.wait_for``
# cancels the SDK call → TimeoutError escapes ocr_document → the runner marks
# the row ``failed`` (never retried) and the turn continues without it.
OCR_TIMEOUT_S = 60.0

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


async def ocr_document(file_url: str, mime_type: str) -> OcrDocumentResult:
    """Run Mistral OCR on a single document given its signed URL.

    Args:
        file_url: A publicly fetchable URL (Supabase Storage signed URL).
        mime_type: One of ``application/pdf``, ``image/png``, ``image/jpeg``.

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

    client = Mistral(api_key=settings.MISTRAL_API_KEY)
    try:
        response = await asyncio.wait_for(
            client.ocr.process_async(
                model=model,
                document=document,
                pages=list(range(OCR_MAX_PAGES)),
            ),
            timeout=OCR_TIMEOUT_S,
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
