"""Case documents — the user's own case papers, forwarded downstream.

Plan: ``.claude/plans/case_documents_forwarding.md`` §3.1.

The router attaches workspace items to a deep_search turn (``attached_items``).
The planner decider reads every one of them in full; until this module the
expanders and the aggregator saw none of them — whatever the planner chose to
retell in ``planner_brief`` was all they got. Uploaded case papers (a judgment,
a statement of claim, a contract…) now travel on their own channel instead: one
``case_document`` :class:`ContextBlock` per document.

Who gets what (built once per turn in ``orchestrator.run_retrieval``):

- case expander — every forwarded document's summary, plus the full text of
  those the planner named in ``PlannerDecision.case_full_text_wis``;
- reg expander — the same, with ``reg_full_text_wis``;
- aggregator — every forwarded document in full (planner-named first, so they
  win the total cap);
- sector_picker, rerankers — nothing.

Which attachments travel: ``kind == "attachment"`` with a ``wi_seq``, whose
``metadata.doc_type`` is one of :data:`CASE_DOCUMENT_TYPES` **or unset**
(pre-``doc_type`` uploads, a summarizer failure, a summary not written yet).
``chat`` / ``official_record`` / ``user_notes`` / ``reference`` / ``other`` and
every non-attachment kind stay planner-only — the planner carries what matters
from them into ``planner_brief``.

The block body is Markdown, not XML: every renderer (expanders, aggregator)
XML-escapes block bodies, so a nested tag would arrive as ``&lt;…&gt;``.

وضع السرية: the snapshots handed in are the planner's ALREADY-ENCODED copies
(``agents/orchestrator.py::_encode_attached_for_planner``), so the blocks are
masked exactly like ``planner_brief`` — nothing here encodes or decodes.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from agents.deep_search_v4.shared.context import ContextBlock

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agents.models import WorkspaceItemSnapshot

logger = logging.getLogger(__name__)


#: The ``doc_type`` labels that travel downstream as case documents. A subset of
#: ``agents.memory.artifact_summarizer.models.DOC_TYPES`` — a test asserts it, so
#: a label renamed there cannot silently stop forwarding here.
CASE_DOCUMENT_TYPES: frozenset[str] = frozenset({
    "judgment", "hearing_minutes", "claim", "memo", "statement",
    "expert_report", "financial", "contract", "letter",
})

#: Full text shown per document; longer documents are cut with a visible marker.
PER_DOC_FULL_TEXT_CAP = 12_000
#: Full text per recipient per turn. A document that would push the running
#: total past it drops to its summary + a note.
TOTAL_FULL_TEXT_CAP = 30_000
#: When a document has no summary yet and gets no full text, this much of its
#: opening stands in for the summary.
NO_SUMMARY_EXCERPT_CHARS = 1_500

CASE_DOCUMENT_LABEL = "case_document"

_SUMMARY_HEADING = "#### الملخص"
_FULL_TEXT_HEADING = "#### النص الكامل"
_NO_SUMMARY_NOTE = "(لم يُكتب ملخص بعد — هذا مطلع النص)"
_OVER_TOTAL_NOTE = "(لم يُرفق النص الكامل لتجاوز حد السياق — اعتمد على الملخص)"

# «WI-3», tolerant of case and of a space/underscore in place of the hyphen.
_WI_ALIAS_RE = re.compile(r"^\s*WI[-_\s]?(\d+)\s*$", re.IGNORECASE)


def _doc_type(item: Any) -> str:
    """The item's ``metadata.doc_type``, or ``""`` when unset."""
    metadata = getattr(item, "metadata", None)
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("doc_type") or "").strip()


def _alias(item: Any) -> str:
    return f"WI-{getattr(item, 'wi_seq', None)}"


def forwardable_documents(
    attached_items: Iterable[WorkspaceItemSnapshot] | None,
) -> list[WorkspaceItemSnapshot]:
    """The attached items that travel downstream as case documents.

    ``kind == "attachment"`` and (``doc_type`` in :data:`CASE_DOCUMENT_TYPES`
    or ``doc_type`` unset), with a ``wi_seq`` (the alias is how the planner
    names a document). Order preserved (router order); a repeated ``item_id``
    is kept once.
    """
    docs: list[WorkspaceItemSnapshot] = []
    seen: set[str] = set()
    for item in attached_items or []:
        if getattr(item, "kind", None) != "attachment":
            continue
        if getattr(item, "wi_seq", None) is None:
            continue
        doc_type = _doc_type(item)
        if doc_type and doc_type not in CASE_DOCUMENT_TYPES:
            continue
        item_id = str(getattr(item, "item_id", "") or "")
        if item_id in seen:
            continue
        seen.add(item_id)
        docs.append(item)
    return docs


def parse_wi_aliases(
    aliases: Sequence[str] | None,
    docs: Sequence[WorkspaceItemSnapshot],
) -> list[str]:
    """Resolve the planner's ``WI-N`` aliases to forwarded documents' item_ids.

    Only aliases of documents in ``docs`` resolve; anything else — a malformed
    string, an alias of a non-forwarded item, an alias from another turn — is
    dropped (debug log). Order kept, duplicates dropped.
    """
    by_seq = {getattr(d, "wi_seq", None): str(d.item_id) for d in docs}
    resolved: list[str] = []
    for raw in aliases or []:
        match = _WI_ALIAS_RE.match(str(raw or ""))
        item_id = by_seq.get(int(match.group(1))) if match else None
        if item_id is None:
            logger.debug("parse_wi_aliases: dropped %r (not a forwarded document)", raw)
            continue
        if item_id not in resolved:
            resolved.append(item_id)
    return resolved


def _heading(item: Any) -> str:
    """``### WI-3 · judgment · title`` — the type segment omitted when unset."""
    title = " ".join(str(getattr(item, "title", "") or "").split())
    segments = [_alias(item), _doc_type(item), title]
    return "### " + " · ".join(s for s in segments if s)


def _render_body(
    item: Any,
    *,
    full_text: str | None,
    original_chars: int,
    over_total: bool,
) -> str:
    """One document's Markdown body (§3.1 body format + variants).

    ``full_text`` — the (possibly cut) text to show, ``None`` when this
    recipient gets no full text for the document. ``over_total`` — full text was
    due but the recipient's total cap was already spent.
    """
    summary = str(getattr(item, "summary", "") or "").strip()
    content = str(getattr(item, "content_md", "") or "").strip()
    lines = [_heading(item)]

    if summary:
        lines += [_SUMMARY_HEADING, summary]
    elif full_text is None and content:
        # No summary yet and no full text: the opening stands in for the summary.
        # (No summary but full text attached → the section is simply omitted.)
        lines += [
            _SUMMARY_HEADING,
            _NO_SUMMARY_NOTE,
            content[:NO_SUMMARY_EXCERPT_CHARS],
        ]

    if full_text is not None:
        lines += [_FULL_TEXT_HEADING, full_text]
        if original_chars > len(full_text):
            lines.append(
                f"[انتهى المعروض: {len(full_text)} حرف من أصل {original_chars}"
                " — بقية الوثيقة لم تُعرض]"
            )
    elif over_total:
        lines += [_FULL_TEXT_HEADING, _OVER_TOTAL_NOTE]
    return "\n".join(lines)


def build_case_document_blocks(
    docs: Sequence[WorkspaceItemSnapshot],
    full_text_ids: Sequence[str],
    *,
    per_doc_cap: int = PER_DOC_FULL_TEXT_CAP,
    total_cap: int = TOTAL_FULL_TEXT_CAP,
    stats: dict[str, Any] | None = None,
) -> list[ContextBlock]:
    """One ``case_document`` block per document, for ONE recipient.

    ``full_text_ids`` — item_ids this recipient reads in full, in priority
    order. Those documents come first, in that order, then the rest in router
    order. Each full text is cut at ``per_doc_cap``; the cut text counts toward
    ``total_cap``, and a document whose text would push the running total past
    it drops to its summary + a note (a later, shorter one may still fit).

    ``stats`` (optional, telemetry) is filled with ``full_text`` (aliases that
    got their text), ``truncated`` (aliases cut at ``per_doc_cap``),
    ``over_total`` (aliases dropped for the total cap) and ``full_text_chars``.
    """
    by_id = {str(d.item_id): d for d in docs}
    wanted = [i for i in dict.fromkeys(str(x) for x in full_text_ids) if i in by_id]
    wanted_set = set(wanted)
    ordered = [by_id[i] for i in wanted] + [
        d for d in docs if str(d.item_id) not in wanted_set
    ]

    shown_full: list[str] = []
    truncated: list[str] = []
    over_total: list[str] = []
    used = 0
    blocks: list[ContextBlock] = []
    for doc in ordered:
        content = str(getattr(doc, "content_md", "") or "").strip()
        full_text: str | None = None
        dropped = False
        if str(doc.item_id) in wanted_set and content:
            cut = content[:per_doc_cap]
            if used + len(cut) <= total_cap:
                full_text = cut
                used += len(cut)
                shown_full.append(_alias(doc))
                if len(content) > len(cut):
                    truncated.append(_alias(doc))
            else:
                dropped = True
                over_total.append(_alias(doc))
        blocks.append(
            ContextBlock(
                label=CASE_DOCUMENT_LABEL,
                body=_render_body(
                    doc,
                    full_text=full_text,
                    original_chars=len(content),
                    over_total=dropped,
                ),
                persistence="turn",
                source_item_id=str(doc.item_id),
            )
        )

    if stats is not None:
        stats.update(
            full_text=shown_full,
            truncated=truncated,
            over_total=over_total,
            full_text_chars=used,
        )
    return blocks


__all__ = [
    "CASE_DOCUMENT_LABEL",
    "CASE_DOCUMENT_TYPES",
    "NO_SUMMARY_EXCERPT_CHARS",
    "PER_DOC_FULL_TEXT_CAP",
    "TOTAL_FULL_TEXT_CAP",
    "build_case_document_blocks",
    "forwardable_documents",
    "parse_wi_aliases",
]
