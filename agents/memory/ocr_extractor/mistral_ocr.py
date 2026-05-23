"""Mistral OCR API wrapper.

A thin async wrapper over the ``mistralai`` SDK's ``client.ocr.process_async``.
The file is passed **by URL** (a Supabase Storage signed URL) — Mistral fetches
it server-side, so there's no download/upload round-trip.

This is a direct SDK call (like the project's embeddings / rerankers): OCR is
not a chat model, so it bypasses the tier / FallbackModel system entirely.
"""
from __future__ import annotations

import logging

from mistralai import Mistral

from shared.config import get_settings

logger = logging.getLogger(__name__)

# Page cap handed to Mistral. The first ``OCR_MAX_PAGES`` pages are extracted;
# the rest of an over-long document are ignored (never rejected).
OCR_MAX_PAGES = 30

# MIME types Mistral OCR accepts in this module.
_PDF_MIME = "application/pdf"
_IMAGE_MIMES = {"image/png", "image/jpeg"}

from .models import OcrDocumentResult


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
        response = await client.ocr.process_async(
            model=model,
            document=document,
            pages=list(range(OCR_MAX_PAGES)),
        )
    finally:
        # The SDK holds an httpx client; close it so we don't leak sockets.
        try:
            await client.sdk_configuration.async_client.aclose()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    pages = list(getattr(response, "pages", None) or [])
    text = "\n\n".join((getattr(p, "markdown", None) or "") for p in pages).strip()
    page_count = len(pages)
    used_model = getattr(response, "model", None) or model

    logger.info(
        "Mistral OCR done: %d pages extracted, %d chars (model=%s)",
        page_count,
        len(text),
        used_model,
    )
    return OcrDocumentResult(text=text, page_count=page_count, model=used_model)
