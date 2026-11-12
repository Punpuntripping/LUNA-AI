"""Dataclasses for the OCR extraction module."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OcrPage:
    """One extracted page, kept beside the joined text (attachment_pages, 215).

    Attributes:
        page_no: 1-based page number.
        markdown: The page's markdown exactly as Mistral returned it.
        char_start: The page's offset inside the final joined ``text`` (which is
            what lands in ``workspace_items.content_md``). Pages lying wholly in
            the stripped leading / trailing whitespace clamp to ``0`` /
            ``len(text)``.
    """

    page_no: int
    markdown: str
    char_start: int


@dataclass
class OcrDocumentResult:
    """The outcome of a single Mistral OCR call.

    Attributes:
        text: The joined markdown text of every extracted page.
        page_count: The number of pages Mistral *actually* extracted
            (``len(response.pages)``, capped at ``OCR_MAX_PAGES``).
        model: The Mistral OCR model id used (e.g. ``mistral-ocr-latest``).
        pages: Every extracted page with its offset inside ``text``. ``text`` is
            built exactly as before; the pages are extra.
    """

    text: str
    page_count: int
    model: str
    pages: list[OcrPage] = field(default_factory=list)


@dataclass
class OcrExtractionStats:
    """Per-run tally of how ``run_ocr_extraction`` handled the candidates.

    Purely diagnostic — the runner returns the list of filled ``item_id``s; this
    is logged so a turn's OCR step is legible in the logs.
    """

    candidates: int = 0
    extracted: int = 0
    skipped_unsupported: int = 0
    skipped_too_large: int = 0
    failed: int = 0
    empty: int = 0
    filled_item_ids: list[str] = field(default_factory=list)
    #: attachment_pages rows written (segmenter flag on only; best-effort).
    pages_stored: int = 0
