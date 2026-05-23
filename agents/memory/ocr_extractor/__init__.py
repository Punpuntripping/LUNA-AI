"""OCR extraction module — Mistral ``mistral-ocr-latest`` over conversation attachments.

A plain module (no LLM reasoning of its own): it calls the Mistral OCR API to
fill an attachment ``workspace_items`` row's ``content_md`` so the router and any
dispatched agent can see the document's text. See
``.claude/plans/ocr_extraction_agent.md``.
"""
from __future__ import annotations

from .models import OcrDocumentResult, OcrExtractionStats
from .runner import run_ocr_extraction

__all__ = ["run_ocr_extraction", "OcrDocumentResult", "OcrExtractionStats"]
