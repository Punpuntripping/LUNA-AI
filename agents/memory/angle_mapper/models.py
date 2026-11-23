"""Contracts of the angle mapper (خريطة الأوجه).

Two groups:

- what the LLM returns (``AngleMap``) — the tested **angles-v2** schema (same
  classes, field names, order and defaults, no class docstrings — pydantic would
  put them in the schema the model sees) with the **angles-v4** changes for one
  map per MESSAGE (plan D17):

  * every section reference is a document-qualified REF string (``refs.py``:
    ``"D1.S7"``) — ``Angle.sections``, ``AngleMap.context``, ``Gap.sections``,
    ``NoAngle.section``;
  * ``Client.by_document`` (owner keys are relative to each document's own
    proceeding: the client's key / name in a document where it differs);
  * ``AngleMap.unrelated_documents`` (a document of the message that belongs to a
    different matter);

  and **angles-v5**'s ``Angle.ocr_suspect`` (the angle rests on a reading the OCR
  could have got wrong — mirrors ``Gap.ocr_suspect``). New fields come last and
  default to ``[]`` / ``False``. The ``mode="before"`` validators
  only make parsing lenient («D1·S7» → ``"D1.S7"``, an unknown strength →
  ``medium``); they do not change the schema;
- what the runner returns (``AngleMapRunResult``).

Whether an angle spans documents ("cross-document") is computed by code from
its refs (:meth:`AngleMap.cross_doc_angles`), never asked of the model.

Plan: ``.claude/plans/relationship_map.md`` §4.3, D16–D17.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .refs import canonical_doc, canonical_ref, doc_of

#: The owner keys a client (or an angle) can be on — the segmenter's party keys
#: (``attachment_segmenter.models.OWNER_CLASSES``) plus ``unknown``.
CLIENT_SIDES: tuple[str, ...] = (
    "claimant", "respondent", "prosecution", "victim", "intervenor", "contract_party", "unknown",
)
UNKNOWN_SIDE = "unknown"
KINDS: tuple[str, ...] = ("strengthen", "attack", "answer")
STRENGTHS: tuple[str, ...] = ("strong", "medium", "weak")
CONFIDENCES: tuple[str, ...] = ("clear", "likely", "unclear")
GAP_KINDS: tuple[str, ...] = ("missing", "contradiction", "unanswered")

# Lenient spellings only — never a change of meaning.
_SIDE_ALIASES = {
    "plaintiff": "claimant",
    "defendant": "respondent",
    "accused": "respondent",
    "public_prosecution": "prosecution",
    "prosecutor": "prosecution",
}
_KIND_ALIASES = {
    "strengthening": "strengthen",
    "attacking": "attack",
    "answering": "answer",
}


def _key(v: Any) -> str:
    return str(v or "").strip().lower().replace("-", "_").replace(" ", "_")


def _as_ref(v: Any) -> Any:
    """Any accepted spelling → canonical ref (``"D1.S7"``, or ``"S7"`` when bare).
    ``None`` / containers are passed through so pydantic reports them."""
    if v is None or isinstance(v, (list, tuple, dict)):
        return v
    return canonical_ref(v)


def _as_ref_list(v: Any) -> Any:
    if v is None:
        return []
    if isinstance(v, (int, str)):
        v = [v]
    if isinstance(v, (list, tuple)):
        return [_as_ref(x) for x in v]
    return v


def _side(v: Any) -> str:
    s = _key(v)
    s = _SIDE_ALIASES.get(s, s)
    return s if s in CLIENT_SIDES else UNKNOWN_SIDE


def _text(v: Any) -> str:
    return str(v or "").strip()


# ---------------------------------------------------------------------------
# LLM output — angles-v2 schema + the v4 changes (no class docstrings, on purpose)
# ---------------------------------------------------------------------------


class ClientInDocument(BaseModel):
    document: str
    side: str = "unknown"
    party: str = ""

    @field_validator("document", mode="before")
    @classmethod
    def _v_document(cls, v: Any) -> str:
        return canonical_doc(v)

    @field_validator("side", mode="before")
    @classmethod
    def _v_side(cls, v: Any) -> str:
        return _side(v)

    @field_validator("party", mode="before")
    @classmethod
    def _v_text(cls, v: Any) -> str:
        return _text(v)


class Client(BaseModel):
    side: str = "unknown"
    party: str
    basis: str = ""
    confidence: str = "unclear"
    by_document: list[ClientInDocument] = Field(default_factory=list)

    @field_validator("side", mode="before")
    @classmethod
    def _v_side(cls, v: Any) -> str:
        return _side(v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _v_confidence(cls, v: Any) -> str:
        s = _key(v)
        return s if s in CONFIDENCES else "unclear"

    @field_validator("party", "basis", mode="before")
    @classmethod
    def _v_text(cls, v: Any) -> str:
        return _text(v)

    @field_validator("by_document", mode="before")
    @classmethod
    def _v_by_document(cls, v: Any) -> Any:
        return [] if v is None else v


class Angle(BaseModel):
    for_side: str = "unknown"
    for_party: str
    kind: str
    title: str
    argument: str
    sections: list[str] = Field(default_factory=list)
    legal_question: str
    strength: str = "medium"
    weakness: str = ""
    ocr_suspect: bool = False

    @field_validator("for_side", mode="before")
    @classmethod
    def _v_side(cls, v: Any) -> str:
        return _side(v)

    @field_validator("ocr_suspect", mode="before")
    @classmethod
    def _v_ocr_suspect(cls, v: Any) -> Any:
        # Absent / null = what the file shows clearly; anything else must be a bool
        # (pydantic's: true / false, "true" / "false", 1 / 0 …) or it is sent back.
        return False if v is None else v

    @field_validator("kind", mode="before")
    @classmethod
    def _v_kind(cls, v: Any) -> str:
        # Strict: the kind is what a consumer plans by, so an unknown one is sent
        # back to the model (a validation retry) rather than guessed.
        s = _key(v)
        s = _KIND_ALIASES.get(s, s)
        if s not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        return s

    @field_validator("strength", mode="before")
    @classmethod
    def _v_strength(cls, v: Any) -> str:
        s = _key(v)
        return s if s in STRENGTHS else "medium"

    @field_validator("sections", mode="before")
    @classmethod
    def _v_sections(cls, v: Any) -> Any:
        return _as_ref_list(v)

    @field_validator("for_party", "title", "argument", "legal_question", "weakness", mode="before")
    @classmethod
    def _v_text(cls, v: Any) -> str:
        return _text(v)

    def documents(self) -> list[int]:
        """The document numbers its refs name, in first-mention order."""
        return list(dict.fromkeys(d for d in (doc_of(r) for r in self.sections) if d is not None))


class Gap(BaseModel):
    kind: str
    sections: list[str] = Field(default_factory=list)
    issue: str
    ocr_suspect: bool = False

    @field_validator("kind", mode="before")
    @classmethod
    def _v_kind(cls, v: Any) -> str:
        return _key(v)

    @field_validator("sections", mode="before")
    @classmethod
    def _v_sections(cls, v: Any) -> Any:
        return _as_ref_list(v)


class NoAngle(BaseModel):
    section: str
    why: str = ""

    @field_validator("section", mode="before")
    @classmethod
    def _v_section(cls, v: Any) -> Any:
        return _as_ref(v)


class UnrelatedDocument(BaseModel):
    document: str
    why: str = ""

    @field_validator("document", mode="before")
    @classmethod
    def _v_document(cls, v: Any) -> str:
        return canonical_doc(v)

    @field_validator("why", mode="before")
    @classmethod
    def _v_text(cls, v: Any) -> str:
        return _text(v)


class AngleMap(BaseModel):
    client: Client
    angles: list[Angle]
    context: list[str] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    no_angle: list[NoAngle] = Field(default_factory=list)
    unrelated_documents: list[UnrelatedDocument] = Field(default_factory=list)

    @field_validator("context", mode="before")
    @classmethod
    def _v_context(cls, v: Any) -> Any:
        return _as_ref_list(v)

    @field_validator("unrelated_documents", mode="before")
    @classmethod
    def _v_unrelated(cls, v: Any) -> Any:
        return [] if v is None else v

    def kind_mix(self) -> dict[str, int]:
        """``{"strengthen": n, "attack": n, "answer": n}`` (every kind, zeros kept)."""
        mix = {k: 0 for k in KINDS}
        for a in self.angles:
            mix[a.kind] = mix.get(a.kind, 0) + 1
        return mix

    def cross_doc_angles(self) -> int:
        """Angles whose sections name two or more documents (computed from the refs)."""
        return sum(1 for a in self.angles if len(a.documents()) > 1)

    def tracking_output(self) -> dict[str, Any]:
        """Counts only — the span never carries the file's words or the angles' text."""
        return {
            "angles": len(self.angles),
            "kind_mix": self.kind_mix(),
            "cross_doc_angles": self.cross_doc_angles(),
            "context": len(self.context),
            "gaps": len(self.gaps),
            "no_angle": len(self.no_angle),
            "unrelated_documents": len(self.unrelated_documents),
            "client_side": self.client.side,
            "client_confidence": self.client.confidence,
        }


# ---------------------------------------------------------------------------
# Runner result
# ---------------------------------------------------------------------------


@dataclass
class AngleMapRunResult:
    """What ``run_angle_mapper`` / ``run_message_angle_map`` return. Never raised —
    read ``outcome``.

    ``outcome``: ``stored`` | ``preview`` | ``joined_group`` | ``skipped_<reason>``
    | ``failed_<reason>`` | ``error`` (the list: ``runner.py``'s docstring).
    ``item_id``: the item the run was called for (the frame's trigger; for a
    message run, the first given item that is a live attachment — the group's user
    comes from it — or the first given item when none is). ``message_id`` /
    ``item_ids``: the group — its
    message (``None`` = a file with no message link, a group of one) and its
    members in D-order (``item_ids[0]`` is D1). ``excluded``: the message's other
    attachments left out of the group, with why (``skipped_no_map``,
    ``skipped_old_segments``…). ``map_id``: the stored map (``stored`` only).
    ``output``: ``AngleMap.model_dump()`` — validated, decoded (real text, never
    the masking fakes), refs checked, unplaced core sections appended to
    ``no_angle``. ``unplaced``: the core section refs still in no angle, not in
    ``context`` and not in ``no_angle`` after the repair call. ``stats``: see
    :func:`_stats_defaults` (+ diagnostics).
    """

    outcome: str            # see runner.py
    item_id: str
    output: dict | None     # AngleMap.model_dump() — validated, decoded (unmasked), refs checked
    unplaced: list[str]     # core section refs still unplaced after the repair call
    stats: dict
    error: str = ""
    message_id: str | None = None
    item_ids: list[str] = field(default_factory=list)       # the group's members, D-order
    excluded: dict[str, str] = field(default_factory=dict)  # item_id → why it is not in the group
    map_id: str | None = None

    @property
    def ok(self) -> bool:
        """A map came out of THIS call (stored, or returned by a preview)."""
        return self.outcome in ("stored", "preview")


def new_result(item_id: str) -> AngleMapRunResult:
    """An ``error`` result with every stats key present (zeros)."""
    return AngleMapRunResult(
        outcome="error",
        item_id=str(item_id or ""),
        output=None,
        unplaced=[],
        stats=_stats_defaults(),
    )


def _stats_defaults() -> dict[str, Any]:
    return {
        "secs": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "model": "",
        "prompt_version": "",
        "attempts": 0,
        "repair_used": False,
        "angle_count": 0,
        "kind_mix": {k: 0 for k in KINDS},
        "client_confidence": "",
        "docs": 0,
        "total_chars": 0,
        "cross_doc_angles": 0,
        "ocr_suspect_angles": 0,
    }


__all__ = [
    "Angle",
    "AngleMap",
    "AngleMapRunResult",
    "CLIENT_SIDES",
    "CONFIDENCES",
    "Client",
    "ClientInDocument",
    "GAP_KINDS",
    "Gap",
    "KINDS",
    "NoAngle",
    "STRENGTHS",
    "UNKNOWN_SIDE",
    "UnrelatedDocument",
    "new_result",
]
