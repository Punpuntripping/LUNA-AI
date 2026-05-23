"""Dataclasses for the OCR extraction module."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OcrDocumentResult:
    """The outcome of a single Mistral OCR call.

    Attributes:
        text: The joined markdown text of every extracted page.
        page_count: The number of pages Mistral *actually* extracted
            (``len(response.pages)``, capped at ``OCR_MAX_PAGES``).
        model: The Mistral OCR model id used (e.g. ``mistral-ocr-latest``).
    """

    text: str
    page_count: int
    model: str


@dataclass
class OcrExtractionStats:
    """Per-run tally of how ``run_ocr_extraction`` handled the candidates.

    Purely diagnostic — the runner returns the list of filled ``item_id``s; this
    is logged so a turn's OCR step is legible in the logs.
    """

    candidates: int = 0
    extracted: int = 0
    skipped_unsupported: int = 0
    skipped_quota: int = 0
    skipped_too_large: int = 0
    failed: int = 0
    empty: int = 0
    filled_item_ids: list[str] = field(default_factory=list)
