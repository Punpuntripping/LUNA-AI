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
            (``len(response.pages)``) — what is billed.
        model: The Mistral OCR model id used (e.g. ``mistral-ocr-latest``).
        pages: Every extracted page with its offset inside ``text``. ``text`` is
            built exactly as before; the pages are extra.
        total_pages: The document's real page count when it was counted (the
            big-file path parses the PDF); None otherwise.
        text_layer_pages: Pages whose text came from the PDF's own text layer
            (text_layer.py) — because Mistral failed on them, or because the
            OCR page budget did not cover them (``overflow_pages``). Never billed.
        lost_pages: Planned pages neither source could read (Mistral failed
            and the page has no text layer — an image page).
        overflow_pages: The part of ``text_layer_pages`` read because the page
            budget did not cover them (owner, 2026-11-23).
        overflow_blank: Pages past the budget whose text layer had nothing (image
            pages) — not extracted.
        unread_pages: Every page of the document not extracted because of the
            budget: past it and blank, or past ``TEXT_LAYER_MAX_PAGES``. None when
            the path that produced the result does not know (the whole-file URL
            path) — the runner then decides from the plan.
    """

    text: str
    page_count: int
    model: str
    pages: list[OcrPage] = field(default_factory=list)
    total_pages: int | None = None
    text_layer_pages: tuple[int, ...] = ()
    lost_pages: tuple[int, ...] = ()
    overflow_pages: tuple[int, ...] = ()
    overflow_blank: tuple[int, ...] = ()
    unread_pages: tuple[int, ...] | None = None

    @property
    def billed_pages(self) -> int:
        """Pages Mistral extracted — what counts against the user's OCR pages.
        Text-layer pages are free (owner, 2026-11-16)."""
        return max(0, self.page_count - len(self.text_layer_pages))


@dataclass(frozen=True)
class PartialRead:
    """A file the page budget let OCR read only in part (plan ocr_page_budget.md).

    Attributes:
        item_id: The attachment's ``workspace_items.item_id``.
        name: Its filename, for the user's notice.
        pages: The 1-based page numbers read, ascending.
        total_pages: The document's page count; None when not known.
    """

    item_id: str
    name: str
    pages: tuple[int, ...]
    total_pages: int | None


@dataclass(frozen=True)
class FallbackRead:
    """A file Mistral failed on, read (in whole or part) from its text layer.

    Attributes:
        item_id: The attachment's ``workspace_items.item_id``.
        name: Its filename, for the user's notice.
        text_layer_pages: Pages taken from the text layer (free).
        lost_pages: Planned pages that could not be read at all.
    """

    item_id: str
    name: str
    text_layer_pages: tuple[int, ...]
    lost_pages: tuple[int, ...] = ()


@dataclass(frozen=True)
class OverflowRead:
    """A PDF whose pages past the OCR page budget were read from its own text
    layer (owner, 2026-11-23) — free, with a warning for the agents.

    Attributes:
        item_id: The attachment's ``workspace_items.item_id``.
        name: Its filename, for the user's notice.
        ocr_pages: Pages read by OCR within the budget (may be empty).
        text_layer_pages: Pages past the budget read from the text layer.
        blank_pages: Pages past the budget with no text layer — not extracted.
        beyond_pages: Pages past ``TEXT_LAYER_MAX_PAGES`` — not extracted.
        total_pages: The document's page count.
        had_budget: The budget chose at least one page of this file (False =
            no pages were left at all).
    """

    item_id: str
    name: str
    ocr_pages: tuple[int, ...]
    text_layer_pages: tuple[int, ...]
    blank_pages: tuple[int, ...] = ()
    beyond_pages: tuple[int, ...] = ()
    total_pages: int | None = None
    had_budget: bool = True


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
    #: Files read in part because of the page budget (one notice each).
    partial: list[PartialRead] = field(default_factory=list)
    #: Files read from their text layer because Mistral failed (one notice each).
    fallback: list[FallbackRead] = field(default_factory=list)
    #: PDFs whose pages past the budget came from their text layer (one notice each).
    overflow: list[OverflowRead] = field(default_factory=list)
    #: Names of files left unread this turn: no OCR pages left.
    no_budget: list[str] = field(default_factory=list)
    #: The quota could not be read; every file was left for the next turn.
    budget_unavailable: bool = False
