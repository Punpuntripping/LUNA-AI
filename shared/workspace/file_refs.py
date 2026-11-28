"""File refs in a report's text — the ``[F1·S7]`` / ``[F1]`` tags («أقسام من مستنداتك»).

Plan: ``.claude/plans/aggregator_file_refs.md`` §4.1 / §4.6 / §4.7 (D10–D12). ONE
grammar for every Python reader of a report body:

* the aggregator's runner reads the tags out of ``synthesis_md`` to build its
  ``file_refs`` (``deep_search_v4/aggregator/file_refs.py``);
* the backend routes resolve a ``{ref_id}`` in the URL;
* the agents that read a report as text (unfold, artifact_analyzer) turn each tag
  into its words, ``(القسم 7 من {doc_title})`` / ``({doc_title})``; a tag with no
  row is removed;
* the chat summarizer, the responder and the public share snapshot drop the tags.

A tag names a file by ``F`` + its **WI number** — the conversation's fixed alias
for it (``workspace_items.wi_seq``: the file headed ``### WI-3 · …`` is ``F3``; D12,
no separate count) — and a section by the segmenter's ``seq`` (the number the
«الخيوط» squares show): ``[F3·S7]`` = section 7 of WI-3; ``[F3]`` = WI-3 shown whole.
The aggregator copies the ready tag from each section's label (``〔F3·S7 · …〕``), so
it never writes ``WI-N``. Accepted spellings: any case, ``F3`` / ``F-3`` / ``F 3``,
any of the angle map's separators before ``S`` (``agents/memory/angle_mapper/refs.py``:
``·`` — what the label shows —, ``.``, ``-``, a space…) plus ``،``, leading zeros,
Arabic-Indic digits, and several refs in one bracket (``[F3·S7، F3·S9]``). Canonical
form: ``"F3.S7"`` / ``"F3"``.

The letters keep it apart from a library citation: ``[4]`` / ``[4,17]`` (digits only,
``aggregator/postvalidator._CITATION_RE``) can never match here, and no tag can
match there. The web mirrors this grammar exactly (``frontend/lib/contracts/file-refs.ts``).

Pure functions except :func:`load_file_ref_rows` (one read, never raises).
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

FILE_REFS_TABLE = "workspace_item_file_refs"

#: The columns a reader needs (the route adds nothing the table lacks).
FILE_REF_COLUMNS = (
    "n, ref_id, item_id, wi_seq, section_seq, char_start, char_end, "
    "page_from, page_to, doc_title, what, owner, brought_by"
)

_DIGITS = "0-9٠-٩۰-۹"
#: Between ``F`` and its number: ``F3`` / ``F-3`` / ``F 3`` / ``F_3``.
_F_SEP = r"[\s\-‐‑–_]*"
#: Before ``S``: the angle map's separators (refs.py ``_SEP``) plus ``،``.
_SEP = r"[\s.·‧•・\-_/:|,،]*"
#: One ref inside a bracket: ``F3·S7`` or ``F3``. Group 1 = the WI number, group 2 = section.
_REF_BODY = rf"[Ff]{_F_SEP}([{_DIGITS}]+)(?:{_SEP}[Ss]\s*([{_DIGITS}]+))?"
_REF_BODY_NC = rf"[Ff]{_F_SEP}[{_DIGITS}]+(?:{_SEP}[Ss]\s*[{_DIGITS}]+)?"
#: Between two refs written in one bracket (``[F3·S7، F3·S9]``): the web's
#: ``LIST_SEP``. The prompt asks for one per bracket.
_LIST_SEP = r"\s*[,،;؛]\s*"
#: A whole bracket of file refs anywhere in a text. No groups: :func:`_refs_in`
#: reads the refs inside it.
FILE_TAG_RE = re.compile(rf"\[\s*{_REF_BODY_NC}(?:{_LIST_SEP}{_REF_BODY_NC})*\s*\]")
_REF_EACH_RE = re.compile(_REF_BODY)
#: A bare ref (no brackets needed) — the route's ``{ref_id}`` path parameter.
_BARE_REF_RE = re.compile(rf"^\s*\[?\s*{_REF_BODY}\s*\]?\s*$")
_TO_WESTERN = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩"
    "۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)

#: When a row has no title.
DEFAULT_DOC_TITLE = "الملف المرفق"


def _num(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw.translate(_TO_WESTERN))
    except ValueError:
        return None


def format_file_ref(wi_seq: int, seq: int | None = None) -> str:
    """``(3, 7)`` → ``"F3.S7"``; ``(2, None)`` → ``"F2"`` (F + the file's WI number)."""
    return f"F{int(wi_seq)}.S{int(seq)}" if seq is not None else f"F{int(wi_seq)}"


def format_file_tag(wi_seq: int, seq: int | None = None) -> str:
    """The tag as the aggregator is shown it and writes it: ``(3, 7)`` → ``"F3·S7"``;
    ``(2, None)`` → ``"F2"`` (no brackets)."""
    return f"F{int(wi_seq)}·S{int(seq)}" if seq is not None else f"F{int(wi_seq)}"


def _canonical(wi_raw: str, seq_raw: str | None) -> str | None:
    wi, seq = _num(wi_raw), _num(seq_raw)
    if wi is None or wi <= 0:
        return None
    if seq_raw is not None and (seq is None or seq <= 0):
        return None
    return format_file_ref(wi, seq)


def parse_file_ref(value: Any) -> str | None:
    """``"[F3·S7]"`` / ``"F3.S7"`` / ``"f-3-s07"`` → ``"F3.S7"``; ``"F2"`` → ``"F2"``;
    anything else → ``None``."""
    m = _BARE_REF_RE.match(str(value if value is not None else ""))
    return _canonical(m.group(1), m.group(2)) if m else None


def split_file_ref(ref: str) -> tuple[int, int | None] | None:
    """``"F3.S7"`` → ``(3, 7)``; ``"F2"`` → ``(2, None)``; any spelling; else ``None``.
    The first number is the file's WI number (``wi_seq``)."""
    canon = parse_file_ref(ref)
    if canon is None:
        return None
    wi, _, seq = canon.partition(".S")
    return int(wi[1:]), (int(seq) if seq else None)


def file_tags(text: str | None) -> list[str]:
    """Canonical refs of every tag in ``text``, distinct, in order of first mention."""
    out: list[str] = []
    for m in FILE_TAG_RE.finditer(text or ""):
        for ref in _refs_in(m.group(0)):
            if ref not in out:
                out.append(ref)
    return out


def _refs_in(bracket: str) -> list[str]:
    """The canonical refs inside one matched bracket, in order; invalid ones skipped."""
    refs: list[str] = []
    for r in _REF_EACH_RE.finditer(bracket):
        ref = _canonical(r.group(1), r.group(2))
        if ref is not None and ref not in refs:
            refs.append(ref)
    return refs


def has_file_tags(text: str | None) -> bool:
    return bool(text) and FILE_TAG_RE.search(text or "") is not None


def file_ref_label(row: Mapping[str, Any]) -> str:
    """The words a tag becomes in plain text: ``(القسم 7 من {doc_title})``, or
    ``({doc_title})`` for a whole document."""
    title = " ".join(str(row.get("doc_title") or "").split()) or DEFAULT_DOC_TITLE
    seq = row.get("section_seq")
    if seq is None:
        return f"({title})"
    return f"(القسم {int(seq)} من {title})"


def _without_tags(text: str, label_of) -> str:
    """Every tag → ``label_of(canonical)``; ``""`` removes the tag together with the
    one space or tab before it, so «… السداد» [F3·S7]، → «… السداد»،."""

    def sub(m: re.Match) -> str:
        labels = [label for label in (label_of(ref) for ref in _refs_in(m.group(2))) if label]
        return f"{m.group(1)}{' '.join(labels)}" if labels else ""

    pattern = re.compile(r"([ \t]?)(" + FILE_TAG_RE.pattern + ")")
    out = pattern.sub(sub, text)
    # Side-by-side tags become side-by-side labels: keep a space between them.
    return out.replace(")(", ") (")


def _row_ref(row: Mapping[str, Any]) -> str | None:
    """A row's canonical ref — ``ref_id`` (the table's column)."""
    return parse_file_ref(row.get("ref_id"))


def replace_file_tags(text: str | None, rows: Iterable[Mapping[str, Any]] | None) -> str:
    """``text`` with each tag turned into its words (:func:`file_ref_label`); a tag
    with no row is removed. ``text`` unchanged (the same string) when it has none."""
    text = text or ""
    if not has_file_tags(text):
        return text
    by_ref = {}
    for row in rows or []:
        ref = _row_ref(row)
        if ref is not None:
            by_ref[ref] = row
    return _without_tags(text, lambda ref: file_ref_label(by_ref[ref]) if ref in by_ref else "")


def strip_file_tags(text: str | None) -> str:
    """``text`` without its tags (unchanged when it has none)."""
    text = text or ""
    if not has_file_tags(text):
        return text
    return _without_tags(text, lambda ref: "")


def load_file_ref_rows(supabase: Any, wi_id: str) -> list[dict]:
    """The report's ``workspace_item_file_refs`` rows in ``n`` order. The client is
    service_role: the caller has already scoped ``wi_id`` to its user.
    Never raises — a failed read (or the table not there yet) is ``[]``."""
    if supabase is None or not wi_id:
        return []
    try:
        resp = (
            supabase.table(FILE_REFS_TABLE)
            .select(FILE_REF_COLUMNS)
            .eq("wi_id", str(wi_id))
            .order("n")
            .execute()
        )
        return [dict(r) for r in (getattr(resp, "data", None) or [])]
    except Exception as exc:  # noqa: BLE001
        logger.warning("file_refs: rows not read for %s (%s)", wi_id, type(exc).__name__)
        return []


def labelled_report_text(supabase: Any, wi_id: str, text: str | None) -> str:
    """A report body as an agent reads it: tags → words from the report's rows.
    No tag → ``text`` as it is, with no read."""
    text = text or ""
    if not has_file_tags(text):
        return text
    return replace_file_tags(text, load_file_ref_rows(supabase, wi_id))


__all__ = [
    "DEFAULT_DOC_TITLE",
    "FILE_REFS_TABLE",
    "FILE_REF_COLUMNS",
    "FILE_TAG_RE",
    "file_ref_label",
    "file_tags",
    "format_file_ref",
    "format_file_tag",
    "has_file_tags",
    "labelled_report_text",
    "load_file_ref_rows",
    "parse_file_ref",
    "replace_file_tags",
    "split_file_ref",
    "strip_file_tags",
]
