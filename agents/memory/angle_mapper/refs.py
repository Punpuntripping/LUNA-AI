"""Section refs of an angle map (angles-v4) — ONE representation everywhere.

A section ref is a string, canonical form ``"D{doc}.S{seq}"`` (ASCII, both
1-based): ``"D2.S7"`` = section 7 (``attachment_segments.seq``) of document 2 of
the message group (``attachment_angle_map_items.doc_index``). Used as is in
``angles[].sections``, ``context``, ``gaps[].sections``, ``no_angle[].section``,
in storage (jsonb) and in the result. A document ref is ``"D2"``.

The prompt SHOWS refs as ``D1·S7`` (:func:`display_ref`); the parser accepts any
separator and case (``D1·S7``, ``d1.s7``, ``D1-S07``, ``D1 S7``, ``[D1S7]``). A
bare section number (``7`` / ``"S7"``) is kept as ``"S7"`` — not a ref: it
names no document. :func:`qualify` turns it into ``"D1.S7"`` when the group has
ONE document (the only case where it is unambiguous); otherwise it stays
invalid and goes back to the model once, then is dropped (``validate.py``).
"""
from __future__ import annotations

import re
from typing import Any

_SEP = r"[\s.·‧•・\-_/:|,]*"
_REF_RE = re.compile(rf"^\s*\[?\s*D\s*0*(\d+){_SEP}S\s*0*(\d+)\s*\]?\s*$", re.IGNORECASE)
_BARE_RE = re.compile(r"^\s*\[?\s*(?:S\s*)?0*(\d+)\s*\]?\s*$", re.IGNORECASE)
_DOC_RE = re.compile(r"^\s*\[?\s*(?:D|doc(?:ument)?)?\s*0*(\d+)\s*\]?\s*$", re.IGNORECASE)
_CANON_RE = re.compile(r"^D(\d+)\.S(\d+)$")

#: The separator the prompt shows between the document and the section.
DISPLAY_SEP = "·"


def format_ref(doc: int, seq: int) -> str:
    """``(2, 7)`` → ``"D2.S7"``."""
    return f"D{int(doc)}.S{int(seq)}"


def format_doc(doc: int) -> str:
    """``2`` → ``"D2"``."""
    return f"D{int(doc)}"


def canonical_ref(value: Any) -> str:
    """Lenient → canonical: a document-qualified ref → ``"D2.S7"``; a bare section
    number (``7`` / ``"S7"`` / ``"S07"``) → ``"S7"``; anything else → its stripped
    text (so the validator can name it)."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"S{value}"
    if isinstance(value, float) and value.is_integer():
        return f"S{int(value)}"
    text = str(value if value is not None else "").strip()
    m = _REF_RE.match(text)
    if m:
        return format_ref(int(m.group(1)), int(m.group(2)))
    m = _BARE_RE.match(text)
    if m:
        return f"S{int(m.group(1))}"
    return text


def canonical_doc(value: Any) -> str:
    """``"D2"`` / ``"d2"`` / ``2`` / ``"Document 2"`` → ``"D2"``; anything else → its
    stripped text."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return format_doc(value)
    text = str(value if value is not None else "").strip()
    m = _DOC_RE.match(text)
    return format_doc(int(m.group(1))) if m else text


def parse_ref(value: Any) -> tuple[int, int] | None:
    """``(doc, seq)`` of a document-qualified ref (any accepted spelling), else ``None``."""
    m = _CANON_RE.match(canonical_ref(value))
    return (int(m.group(1)), int(m.group(2))) if m else None


def doc_of(value: Any) -> int | None:
    """The document number of a ref, or ``None`` (bare / invalid)."""
    parsed = parse_ref(value)
    return parsed[0] if parsed else None


def is_bare(value: Any) -> bool:
    """A section number without its document (``"S7"``)."""
    return bool(re.fullmatch(r"S\d+", canonical_ref(value)))


def qualify(value: Any, single_doc: bool) -> str:
    """Canonical ref; a bare ``"S7"`` becomes ``"D1.S7"`` when the group has one
    document."""
    ref = canonical_ref(value)
    if single_doc and re.fullmatch(r"S\d+", ref):
        return f"D1.{ref}"
    return ref


def display_ref(value: Any) -> str:
    """How the prompt writes a ref: ``"D2.S7"`` → ``"D2·S7"`` (other text unchanged)."""
    parsed = parse_ref(value)
    if parsed is None:
        return canonical_ref(value)
    return f"D{parsed[0]}{DISPLAY_SEP}S{parsed[1]}"


__all__ = [
    "DISPLAY_SEP",
    "canonical_doc",
    "canonical_ref",
    "display_ref",
    "doc_of",
    "format_doc",
    "format_ref",
    "is_bare",
    "parse_ref",
    "qualify",
]
