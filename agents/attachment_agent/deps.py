"""Per-run state of one attachment-agent call.

Everything here is loaded and encoded before the model runs (``context.py``); the
tools only slice it. ``FileDoc.seen`` records every masked range a tool returned
(or the whole text of a short file), which is what a quote may come from.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Any

from .text import FixMap, MaskedText, covers, uncovered_is_blank

#: A file without page rows is read in parts of about this many characters.
PART_CHARS = 6_000
#: A part ends at a line break found in its last this many characters.
_PART_SNAP = 600

_LABEL_RE = re.compile(r"^\s*\[?\s*F\s*0*(\d+)\s*\]?\s*$", re.IGNORECASE)
_WI_RE = re.compile(r"^\s*\[?\s*WI\s*[-_ ]?\s*0*(\d+)\s*\]?\s*$", re.IGNORECASE)
_NUM_RE = re.compile(r"^\s*0*(\d+)\s*$")


@dataclass(eq=False)
class SectionRow:
    """One stored section (``attachment_segments``), its words already masked."""

    seq: int
    label: str
    value: str
    what: str
    #: Whose words these are: the document's own role words, else the owner key.
    owner: str
    brought_by: str
    #: Offsets in the ORIGINAL text (``content_md``).
    char_start: int
    char_end: int
    page_from: int | None = None
    page_to: int | None = None
    #: The stored text, masked, when it is not the plain slice of the file (a
    #: section joined across page letterheads). "" = read the slice.
    joined_text: str = ""

    @property
    def ref(self) -> str:
        return f"S{self.seq}"


@dataclass(eq=False)
class FileDoc:
    """One attachment as the agent sees it. Text fields are masked."""

    label: str                      # "F1"
    item_id: str
    wi_seq: int | None
    title: str
    doc_type: str
    summary: str
    view: MaskedText
    sections: list[SectionRow] = field(default_factory=list)
    #: ``(page_no, char_start in the ORIGINAL text)``, page order. Empty = no page rows.
    pages: list[tuple[int, int]] = field(default_factory=list)
    has_segment_map: bool = False
    has_angle_map: bool = False
    #: The whole text was put in the turn message (a short file).
    given_whole: bool = False
    #: Masked ranges the model has seen.
    seen: list[tuple[int, int]] = field(default_factory=list)
    #: The id the file's overlays hang on (``user_files.file_id``; the item id
    #: for a file with no ``user_files`` row).
    file_id: str = ""
    #: The file's active text corrections (already applied to ``view``).
    corrections: list[Any] = field(default_factory=list)
    #: ``(wrong, right)`` REAL fixes staged in this run, in order — a second fix
    #: of the same file is checked against the text with the first applied.
    pending_fixes: list[tuple[str, str]] = field(default_factory=list)
    _parts: list[tuple[int, int]] | None = field(default=None, repr=False)

    # -- identity --------------------------------------------------------------

    @property
    def wi(self) -> str | None:
        return f"WI-{self.wi_seq}" if self.wi_seq is not None else None

    @property
    def chars(self) -> int:
        return len(self.view.original)

    def ref(self, section: SectionRow) -> str:
        return f"{self.label}·S{section.seq}"

    # -- where an offset sits (ORIGINAL offsets) -------------------------------

    def section_at(self, pos: int) -> SectionRow | None:
        starts = [s.char_start for s in self.sections]
        k = bisect.bisect_right(starts, pos) - 1
        if k < 0:
            return None
        section = self.sections[k]
        return section if pos < section.char_end else None

    def page_at(self, pos: int) -> int | None:
        if not self.pages:
            return None
        starts = [start for _no, start in self.pages]
        k = bisect.bisect_right(starts, pos) - 1
        return self.pages[max(k, 0)][0]

    def section(self, seq: int) -> SectionRow | None:
        return next((s for s in self.sections if s.seq == seq), None)

    # -- pages / parts (MASKED ranges) -----------------------------------------

    def page_range(self, page_no: int) -> tuple[int, int] | None:
        for i, (no, start) in enumerate(self.pages):
            if no == page_no:
                end = self.pages[i + 1][1] if i + 1 < len(self.pages) else self.chars
                return self.view.to_masked(start), self.view.to_masked(max(end, start))
        return None

    @property
    def parts(self) -> list[tuple[int, int]]:
        """Fixed-size parts of the masked text, cut at a line break when one is near."""
        if self._parts is None:
            text = self.view.text
            parts: list[tuple[int, int]] = []
            start = 0
            while start < len(text):
                end = min(start + PART_CHARS, len(text))
                if end < len(text):
                    cut = text.rfind("\n", end - _PART_SNAP, end)
                    if cut > start:
                        end = cut + 1
                parts.append((start, end))
                start = end
            self._parts = parts
        return self._parts

    # -- what the model has seen -------------------------------------------------

    def mark_seen(self, start: int, end: int) -> None:
        if end > start:
            self.seen.append((start, end))

    def was_seen(self, start: int, end: int) -> bool:
        return covers(self.seen, start, end)

    def read_whole(self) -> bool:
        return bool(self.view.text.strip()) and uncovered_is_blank(self.view.text, self.seen)

    # -- the REAL text a fix is checked against ------------------------------------

    def _real_map(self) -> FixMap:
        """The original text with the stored fixes, then the ones staged in this run."""
        pending = [
            {"wrong": w, "right": r, "created_at": f"~{i:06d}"}   # "~" sorts after every timestamp
            for i, (w, r) in enumerate(self.pending_fixes)
        ]
        return FixMap(self.view.original, [*self.corrections, *pending])

    def real_text(self) -> str:
        """The REAL text with the stored fixes and the ones staged in this run."""
        return self._real_map().text

    def real_section_bounds(self) -> list[tuple[int, int]] | None:
        """Each section's ``(start, end)`` in :meth:`real_text` (stored offsets are in
        the original text); ``None`` without a map."""
        if not self.sections:
            return None
        fm = self._real_map()
        return [(fm.to_corrected(s.char_start), fm.to_corrected(s.char_end)) for s in self.sections]


@dataclass
class ThreadMap:
    """One stored angle map «الخيوط» as the edit tools resolve it. ``stored`` is
    ``load_angle_map_for_item``'s shape, REAL text. ``label``: ``M1``… when more
    than one map is shown (then threads are written ``M1·A2``), ``""`` otherwise."""

    label: str
    map_id: str
    stored: dict
    #: member item_id → its document number (``D{n}`` in the map's refs). For a
    #: carried map, the members of files here are THIS conversation's copies
    #: (``library.localize_angle_map``); the others stay the map's own items.
    doc_of_item: dict[str, int] = field(default_factory=dict)
    #: Carried from another conversation with a «الملفات» file (owner 2026-11-27):
    #: an edit is made IN PLACE on that map, so it shows wherever the map is read
    #: (plan §7 option a).
    carried: bool = False

    @property
    def angles(self) -> list[dict]:
        return list(self.stored.get("angles") or [])

    @property
    def gaps(self) -> list[dict]:
        return [g for g in ((self.stored.get("map") or {}).get("gaps") or []) if isinstance(g, dict)]

    @property
    def client(self) -> dict:
        return dict((self.stored.get("map") or {}).get("client") or {})

    def angle(self, seq: int) -> dict | None:
        return next((a for a in self.angles if int(a.get("seq") or 0) == int(seq)), None)

    def thread_label(self, seq: int) -> str:
        return f"{self.label}·A{seq}" if self.label else f"A{seq}"

    def gap_label(self, index: int) -> str:
        return f"{self.label}·G{index}" if self.label else f"G{index}"


@dataclass
class StagedEdit:
    """One change an edit tool accepted. Nothing is written until the run's
    output passes validation (``apply.apply_staged``)."""

    #: promote · fix_text · edit_thread · add_thread · remove_thread · add_gap ·
    #: edit_gap · remove_gap · edit_client · reanalysis · undo
    kind: str
    #: The user's words that asked for it (masked, as checked).
    evidence: str
    args: dict = field(default_factory=dict)


@dataclass
class RecentEdit:
    """One of this conversation's changes, as ``list_recent_edits`` shows it."""

    label: str        # "E1"
    source: str       # "text" | "promote" | "map"
    id: str
    line: str         # REAL Arabic description (encoded when shown)
    created_at: str = ""
    #: An angle-map edit's log kind (``edit_gap``, ``snapshot``…); "" otherwise.
    kind: str = ""


@dataclass
class AttachmentDeps:
    supabase: Any
    user_id: str
    conversation_id: str
    message_id: str | None = None
    #: label → file, in upload order.
    files: dict[str, FileDoc] = field(default_factory=dict)
    #: Every phrase given to ``find`` (masked), in order.
    searched: list[str] = field(default_factory=list)
    #: Characters of file text returned by tools.
    chars_read: int = 0
    #: The one chance to fix quotes has been used (``agent._check_output``).
    quote_retry_used: bool = False
    #: Edit tools stage changes here (:class:`StagedEdit`); code applies them only
    #: after the output passes validation, so a failed run changes nothing.
    staged_edits: list[Any] = field(default_factory=list)
    #: The current user message as the model read it (masked) — the evidence guard.
    user_message: str = ""
    #: The turn message the model read (masked) — ``source`` for decoding.
    source_text: str = ""
    #: The angle maps «الخيوط» of these files (empty: free plan / no analysis).
    maps: list[ThreadMap] = field(default_factory=list)
    #: ``E1`` → this conversation's change, filled by ``list_recent_edits``.
    recent_edits: dict[str, RecentEdit] = field(default_factory=dict)
    _read_source: str | None = field(default=None, repr=False)

    def read_source(self) -> str:
        """Every masked text the model could have read (the turn message + the
        files) — what a number in its edit may have been copied from."""
        if self._read_source is None:
            self._read_source = "\n".join([self.source_text, *(f.view.text for f in self.files.values())])
        return self._read_source

    def file(self, ref: Any) -> FileDoc | None:
        """A file by its label (``F1``), its ``WI-3`` alias, or its number."""
        text = str(ref if ref is not None else "").strip()
        if not text:
            return next(iter(self.files.values())) if len(self.files) == 1 else None
        m = _LABEL_RE.match(text) or _NUM_RE.match(text)
        if m:
            return self.files.get(f"F{int(m.group(1))}")
        m = _WI_RE.match(text)
        if m:
            return next((f for f in self.files.values() if f.wi_seq == int(m.group(1))), None)
        return None


__all__ = [
    "PART_CHARS", "AttachmentDeps", "FileDoc", "RecentEdit", "SectionRow", "StagedEdit", "ThreadMap",
]
