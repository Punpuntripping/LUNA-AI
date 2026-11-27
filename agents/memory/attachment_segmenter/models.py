"""Contracts of the attachment segmenter.

Three groups:

- what the LLM returns (``SegmentMap``) — paragraph RANGES as integers
  (``from_p``/``to_p``, P4), no quoted text (``start_quote`` dropped: Flash quotes
  were unreliable, plan §2.2); the Phase 0 v4.4 schema;
- what is stored and read back (``StoredMap`` / ``StoredSegment``, migration 215);
- what the runner returns (``SegmenterResult``).
"""
from __future__ import annotations

import re
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: What the MODEL may rate a section. ``repeat`` is set by code only.
ModelValue = Literal["metadata", "core", "supporting", "boilerplate"]
MODEL_VALUES: tuple[str, ...] = get_args(ModelValue)
#: Everything a stored section can be (the CHECK in migration 215).
StoredValue = Literal["metadata", "core", "supporting", "boilerplate", "repeat"]
STORED_VALUES: tuple[str, ...] = get_args(StoredValue)

#: Who a section belongs to — the person or body whose words, findings or decision it
#: carries (seg-v5.2, owner 2026-11-18). Keys are fixed (code filters and joins on
#: them); ``Segment.owner`` keeps the document's own role words.
OWNER_CLASSES: tuple[str, ...] = (
    # sides of the dispute, relative to the proceeding in THIS document
    "claimant", "respondent", "prosecution", "victim", "intervenor", "both_parties",
    # who decides or runs the process
    "court", "court_officer", "arbitrator", "committee", "conciliator",
    # fact-finders for the state
    "investigator", "inspector",
    # neutral / technical
    "expert", "translator",
    # statements
    "witness",
    # outside the case
    "authority", "third_party",
    # non-litigation documents
    "contract_party",
    # several owners in one section (``owner`` names them) / not said
    "mixed", "unknown",
)
UNKNOWN_OWNER = "unknown"

_INT_RE = re.compile(r"\d+")


def _as_int(v: Any) -> Any:
    """``12`` / ``"12"`` / ``"P012"`` / ``"[P012]"`` → ``12``. Anything else is
    passed through so pydantic reports it."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        m = _INT_RE.search(v)
        if m:
            return int(m.group())
    return v


class _Ranged(BaseModel):
    @field_validator("from_p", "to_p", mode="before", check_fields=False)
    @classmethod
    def _v_int(cls, v: Any) -> Any:
        return _as_int(v)


# ---------------------------------------------------------------------------
# LLM output — the schema the model sees is the one Phase 0 tested
# (experiments/run_flash_segmenter.py v4.4): same field names and order (`what`
# BEFORE `value`, on purpose), same descriptions, every field required, and no
# class docstrings (pydantic would put them in the schema). The ``mode="before"``
# validators only make parsing lenient; they do not change the schema
# (``tests/test_prompts.py`` pins its hash).
# ---------------------------------------------------------------------------


class EmbeddedDoc(_Ranged):
    from_p: int = Field(description="number of the document's first paragraph (P012 -> 12)")
    to_p: int = Field(description="number of its last paragraph, inclusive")
    name: str


def _all_required(schema: dict[str, Any]) -> None:
    # The owner fields have defaults so code (bundles, tests, older stored maps) can
    # build a Segment without them — but the MODEL must always fill them: the
    # schema it sees lists every property as required.
    schema["required"] = list(schema.get("properties", {}))


class Segment(_Ranged):
    model_config = ConfigDict(json_schema_extra=_all_required)

    from_p: int = Field(description="number of the first paragraph (P012 -> 12)")
    to_p: int = Field(description="number of the last paragraph, inclusive")
    label: str
    owner_class: str = Field(default=UNKNOWN_OWNER, description="who this section belongs to: one key from the owner list")
    owner: str = Field(default="", description="that owner in the document's own Arabic role words, with its number")
    brought_by: str = Field(default="", description="the party that filed or relies on it, in the document's role words; empty when it is the owner or not said")
    what: str
    value: ModelValue

    @field_validator("value", mode="before")
    @classmethod
    def _v_value(cls, v: Any) -> str:
        # Lenient: case / blanks normalised; an unknown rating becomes
        # ``supporting`` (a whole-map retry costs a full re-output, and the middle
        # rating neither hides nor floods).
        s = str(v or "").strip().lower()
        return s if s in MODEL_VALUES else "supporting"

    @field_validator("label", mode="before")
    @classmethod
    def _v_label(cls, v: Any) -> str:
        return str(v or "").strip() or "other"

    @field_validator("owner_class", mode="before")
    @classmethod
    def _v_owner_class(cls, v: Any) -> str:
        # Lenient like ``value``: an unknown key becomes ``unknown`` rather than
        # failing the whole map.
        s = str(v or "").strip().lower().replace("-", "_").replace(" ", "_")
        return s if s in OWNER_CLASSES else UNKNOWN_OWNER

    @field_validator("owner", "brought_by", mode="before")
    @classmethod
    def _v_owner_text(cls, v: Any) -> str:
        s = " ".join(str(v or "").split())
        return "" if s.lower() in {"none", "null", "n/a", "-", "—", "same"} else s


class KeyParagraph(BaseModel):
    p: int
    why: str

    @field_validator("p", mode="before")
    @classmethod
    def _v_p(cls, v: Any) -> Any:
        return _as_int(v)


class SegmentMap(BaseModel):
    # The segmenter's output for one document (or one part of it).
    documents_in_file: list[EmbeddedDoc]
    segments: list[Segment]
    key_paragraphs: list[KeyParagraph]

    def tracking_output(self) -> dict[str, Any]:
        """Counts only — the span never carries document text or descriptions."""
        return {
            "segments": len(self.segments),
            "documents_in_file": len(self.documents_in_file),
            "key_paragraphs": len(self.key_paragraphs),
        }


# ---------------------------------------------------------------------------
# Stored (migration 215) — read back by delivery code
# ---------------------------------------------------------------------------


class StoredSegment(BaseModel):
    """One ``attachment_segments`` row. ``content_md`` is the original text of
    the section, ``workspace_items.content_md[char_start:char_end]`` — except a
    section joined across page letterheads (``joins.py``): its text is its core
    parts joined by a newline, without the letterhead between them, while
    ``char_start`` / ``char_end`` / ``from_p`` / ``to_p`` span the whole range."""

    seq: int
    from_p: int
    to_p: int
    page_from: int | None = None
    page_to: int | None = None
    char_start: int
    char_end: int
    label: str
    value: StoredValue
    what: str = ""
    content_md: str = ""
    #: seg-v5.2+ (migration 220); ``None`` / ``""`` on older maps.
    owner_class: str | None = None
    owner: str | None = None
    brought_by: str | None = None
    #: Not a column: ``True`` when ``value`` is ``core`` only because the user
    #: promoted the section (migration 234, ``shared/attachments/segment_overrides``,
    #: applied by ``store.load_segment_maps``).
    promoted_by_user: bool = False


class StoredMap(BaseModel):
    """One ``attachment_segment_maps`` row plus its segments in document order."""

    item_id: str
    user_id: str | None = None
    paragraph_rule_version: str
    prompt_version: str
    model: str | None = None
    split: bool = False
    documents_in_file: list[dict[str, Any]] = Field(default_factory=list)
    key_paragraphs: list[dict[str, Any]] = Field(default_factory=list)
    content_chars: int
    created_at: str | None = None
    segments: list[StoredSegment] = Field(default_factory=list)
    #: Not a column: the id the map is stored under — the item's FILE (or the item
    #: itself when it links none), as ``load_segment_maps`` resolved it. Readers key
    #: the file's text corrections by it without a second lookup.
    file_key: str | None = None

    def by_value(self, *values: str) -> list[StoredSegment]:
        return [s for s in self.segments if s.value in values]


# ---------------------------------------------------------------------------
# Runner result
# ---------------------------------------------------------------------------

Outcome = Literal[
    "ok",                      # map stored
    "not_found",               # no row, or soft-deleted
    "skipped_not_attachment",  # kind != 'attachment'
    "skipped_disabled",        # ATTACHMENT_SEGMENTER_ENABLED off for this user
    "skipped_short",           # len(content_md) <= SEGMENT_MIN_CHARS
    "skipped_fresh",           # a non-stale map exists and force=False
    "failed_llm",              # every attempt raised / timed out
    "failed_invalid",          # every attempt returned an unrepairable map
    "failed_store",            # the map could not be written
    "error",                   # anything else (caught)
]


class SegmenterResult(BaseModel):
    """What ``run_attachment_segmenter`` returns. Never raised — read ``outcome``."""

    item_id: str
    outcome: Outcome
    doc_type: str = ""
    chars: int = 0
    paragraphs: int = 0
    split: bool = False
    #: Parallel calls the document was cut into (1 = whole).
    parts: int = 1
    #: Seam stitching counts summed over every seam (split runs only).
    seams: dict[str, int] = Field(default_factory=dict)
    attempts: int = 0
    segments: int = 0
    repeats: int = 0
    #: Core sections joined back across page letterheads (``joins.py``).
    page_joins: int = 0
    #: Core sections under 100 chars merged into a core neighbour (``joins.py``).
    short_merges: int = 0
    core_chars: int = 0
    repairs: dict[str, int] = Field(default_factory=dict)
    model_used: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_reasoning: int = 0
    tokens_cached: int = 0
    duration_ms: int = 0
    error: str = ""
    #: Characters of "What the user said" the model read (0 = none found).
    user_words_chars: int = 0
    #: Preview runs only (``preview=True``): what would have been stored —
    #: ``paragraphs``, the decoded ``map``, the ``segments`` rows, ``split_info``
    #: (+ ``bundle`` for a bundle member).
    preview: dict[str, Any] | None = None
    #: Bundle members only: the id shared by the bundle's maps and its members in
    #: upload order. A member's token fields are its share of the bundle's run
    #: (by characters) — they add up to the bundle's total across the members;
    #: ``attempts``, ``split``, ``repairs``, ``model_used`` and ``duration_ms`` are
    #: the bundle run's.
    bundle_id: str | None = None
    bundle_members: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"


__all__ = [
    "EmbeddedDoc",
    "KeyParagraph",
    "MODEL_VALUES",
    "ModelValue",
    "OWNER_CLASSES",
    "UNKNOWN_OWNER",
    "Outcome",
    "STORED_VALUES",
    "Segment",
    "SegmentMap",
    "SegmenterResult",
    "StoredMap",
    "StoredSegment",
    "StoredValue",
]
