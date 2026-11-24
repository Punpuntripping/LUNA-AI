"""The attachment citation run: scan → decide → the agent → store → the card.

Plan: ``.claude/plans/completion_search.md`` §4.6 (owner decisions C14–C20, C26). One
run per attachment, started by the helper frame beside the segmenter.

1. Read the file's text and document type. Contracts are never scanned (C16).
2. Scan — pure code (C14). No citing mention → a scan row with zero counts, no model.
3. Code decides every citation it can PROVE from library facts
   (``agents/memory/citation_pins/resolve.py``).
4. What is left and can be settled by reading goes to the Completion Search agent,
   which fetches the article itself (C15, C26 — ``agent_step.py``). A file whose
   citations code proved calls no model at all.
5. Replace the file's rows (``attachment_citation_scans`` / ``attachment_citations``).
6. Add the pinned articles to the conversation's «النصوص المثبّتة» card.

Never raises: ``run_citation_pins`` returns a ``PinsResult`` whose ``outcome`` says
what happened. The helper-frame wrapper turns a failed outcome into a failed run so
the frame records it and retries.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from agents.memory.attachment_segmenter.paragraphs import PARAGRAPH_RULE_VERSION
from agents.memory.citation_pins import store
from agents.memory.citation_pins.resolve import PINNED, Citation, counts, resolve_citations
from agents.tool_repository.citation_scan import SCAN_RULE_VERSION, scan_citations

logger = logging.getLogger(__name__)

STAGE = "citation_pins.run"
#: Document types that are never scanned. A contract's «المادة 5» is its own clause.
SKIPPED_DOC_TYPES: frozenset[str] = frozenset({"contract"})

WRITTEN = "written"
NO_CITATIONS = "no_citations"
ALREADY_DONE = "already_done"
SKIPPED_DISABLED = "skipped_disabled"
SKIPPED_NOT_APPLICABLE = "skipped_not_applicable"
FAILED_STORE = "failed_store"
ERROR = "error"


@dataclass(frozen=True)
class PinsResult:
    outcome: str
    item_id: str
    counts: Mapping[str, int] = field(default_factory=dict)
    error: str = ""

    @property
    def failed(self) -> bool:
        return self.outcome.startswith("failed") or self.outcome == ERROR


def _doc_type(row: Mapping[str, Any]) -> str:
    meta = row.get("metadata")
    return str((meta or {}).get("doc_type") or "") if isinstance(meta, Mapping) else ""


def citation_pins_applies(row: Mapping[str, Any] | None) -> bool:
    """An attachment that has text and is not a contract. No type yet = not a contract."""
    if not row or row.get("deleted_at"):
        return False
    return (
        str(row.get("kind") or "") == "attachment"
        and bool(str(row.get("content_md") or "").strip())
        and _doc_type(row) not in SKIPPED_DOC_TYPES
    )


def _load_item(supabase: Any, item_id: str) -> dict | None:
    from agents.memory.attachment_segmenter.runner import _load_item as load

    return load(supabase, item_id)


def citation_pins_applies_to_item(supabase: Any, item_id: str) -> bool:
    return citation_pins_applies(_load_item(supabase, item_id))


def load_citation_scan(supabase: Any, item_id: str) -> store.ScanState | None:
    """The helper frame's ``load``: the stored scan when it was made from the text the
    file holds NOW, else ``None`` (changed text redoes the run; a new rule version
    does not)."""
    from agents.memory.attachment_segmenter.store import _content_lengths

    try:
        state = store.scan_state(supabase, item_id)
        if state is None:
            return None
        length = _content_lengths(supabase, [str(item_id)], None).get(str(item_id))
        return state if length is not None and int(length) == state.content_chars else None
    except Exception as exc:  # noqa: BLE001 — unreadable = not ready
        logger.warning("citation_pins: load failed for %s (%s)", item_id, type(exc).__name__)
        return None


def _scan_and_decide(supabase: Any, content: str, doc_type: str) -> tuple[Any, list[Citation]]:
    from agents.completion_search.lookups import load_title_index

    titles = load_title_index(supabase)
    scan = scan_citations(content, doc_type or None, titles)
    if not scan.citing:
        return scan, []
    facts = store.load_facts(supabase, scan, titles)
    return scan, resolve_citations(scan, facts)


async def _agent_pass(
    supabase: Any,
    row: Mapping[str, Any],
    scan: Any,
    citations: list[Citation],
    content: str,
    doc_type: str,
    *,
    agent: Any = None,
    library: Any = None,
) -> tuple[list[Citation], Any]:
    """Step 4. Never raises: on any failure the citations stay as code decided them.

    وضع السرية: the request is encoded with the user's codec and new fakes are stored
    before the model sees them — the segmenter's rule, with its helpers.
    """
    from agents.memory.citation_pins.agent_step import AgentStats, run_agent_step

    try:
        from agents.completion_search.lookups import SupabaseLibrary
        from agents.memory.attachment_segmenter.runner import _codec_for, _enc, _persist_fakes

        user_id = str(row.get("user_id") or "")
        codec = await asyncio.to_thread(_codec_for, supabase, user_id)

        async def persist() -> None:
            await asyncio.to_thread(_persist_fakes, supabase, user_id, codec)

        async def reg_status(ids: Any) -> Mapping[str, str]:
            return await asyncio.to_thread(store.reg_status, supabase, ids)

        return await run_agent_step(
            scan, citations, content,
            library=library if library is not None else SupabaseLibrary(supabase),
            reg_status=reg_status,
            doc_type=doc_type,
            encode=(lambda text: _enc(codec, text)) if codec is not None else None,
            before_call=persist if codec is not None else None,
            agent=agent,
            user_id=user_id or None,
            conversation_id=row.get("conversation_id"),
            case_id=row.get("case_id"),
        )
    except Exception:  # noqa: BLE001
        logger.warning("citation_pins: agent step failed for %s", row.get("item_id"), exc_info=True)
        return list(citations), AgentStats()


def _top_up_card(supabase: Any, row: Mapping[str, Any]) -> int:
    """Add this attachment's pinned articles to its conversation's card. Best effort."""
    conversation_id = row.get("conversation_id")
    user_id = row.get("user_id")
    if not conversation_id or not user_id:
        return 0
    from shared.workspace.pinned_texts import add_attachment_pins

    pins = store.load_pins(supabase, [str(row["item_id"])], user_id=str(user_id)).get(str(row["item_id"])) or []
    if not pins:
        return 0
    return add_attachment_pins(
        supabase,
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        case_id=row.get("case_id"),
        attachment_id=str(row["item_id"]),
        attachment_title=str(row.get("title") or ""),
        content=str(row.get("content_md") or ""),
        pins=pins,
    )


async def run_citation_pins(supabase: Any, item_id: str, *, agent: Any = None, library: Any = None) -> PinsResult:
    """Scan one attachment and store what it cites. Never raises.

    ``agent`` / ``library`` hand in a ready Completion Search agent and library (tests).
    """
    from agents.memory.citation_pins.agent_step import AgentStats
    from agents.utils.citation_pin_flags import citation_agent_enabled, citation_pins_enabled
    from agents.utils.tracking import track_stage

    item_id = str(item_id)
    try:
        row = await asyncio.to_thread(_load_item, supabase, item_id)
        if not citation_pins_applies(row):
            return PinsResult(SKIPPED_NOT_APPLICABLE, item_id)
        user_id = str(row.get("user_id") or "")
        if not citation_pins_enabled(user_id):
            return PinsResult(SKIPPED_DISABLED, item_id)
        content = str(row.get("content_md") or "")
        doc_type = _doc_type(row)

        with track_stage(STAGE, conversation_id=row.get("conversation_id"), case_id=row.get("case_id"),
                         agent_family="memory") as span:
            state = await asyncio.to_thread(store.scan_state, supabase, item_id)
            if state is not None and state.content_chars == len(content):
                pinned = state.pinned
                span.set(outcome=ALREADY_DONE, pinned=pinned)
                if pinned:      # the same file, attached in another conversation: its card is topped up
                    await _card(supabase, row, span)
                return PinsResult(ALREADY_DONE, item_id, {"pinned": pinned})

            scan, citations = await asyncio.to_thread(_scan_and_decide, supabase, content, doc_type)
            stats = AgentStats()
            if citations and citation_agent_enabled():
                citations, stats = await _agent_pass(
                    supabase, row, scan, citations, content, doc_type, agent=agent, library=library,
                )
            tally = {**counts(scan, citations), **stats.row()}
            scan_row = {
                "item_id": item_id, "user_id": user_id,
                "scan_rule_version": SCAN_RULE_VERSION, "paragraph_rule_version": PARAGRAPH_RULE_VERSION,
                "doc_type": doc_type or None, "content_chars": len(content), "model": stats.model, **tally,
            }
            try:
                await asyncio.to_thread(store.write_scan, supabase, scan_row, [c.row(item_id) for c in citations])
            except store.StoreError as exc:
                span.set(outcome=FAILED_STORE)
                logger.warning("citation_pins: store failed for %s (%s)", item_id, exc)
                return PinsResult(FAILED_STORE, item_id, tally, str(exc))

            # Counts only — never a word of the attachment.
            span.set(outcome=WRITTEN if citations else NO_CITATIONS, doc_type=doc_type,
                     content_chars=len(content), laws=len(scan.named_laws),
                     agent_searches=stats.searches, agent_seconds=stats.seconds, **tally)
            if any(c.status == PINNED for c in citations):
                await _card(supabase, row, span)
            return PinsResult(WRITTEN if citations else NO_CITATIONS, item_id, tally)
    except Exception as exc:  # noqa: BLE001 — the run's contract
        logger.warning("citation_pins: run failed for %s", item_id, exc_info=True)
        return PinsResult(ERROR, item_id, error=type(exc).__name__)


async def _card(supabase: Any, row: Mapping[str, Any], span: Any) -> None:
    """The card is the user's view; the rows above are what delivery reads. A card that
    could not be written never fails the run."""
    try:
        added = await asyncio.to_thread(_top_up_card, supabase, row)
        span.set(card_entries=added)
    except Exception:  # noqa: BLE001
        logger.warning("citation_pins: card top-up failed for %s", row.get("item_id"), exc_info=True)


__all__ = [
    "ALREADY_DONE",
    "ERROR",
    "FAILED_STORE",
    "NO_CITATIONS",
    "PinsResult",
    "SKIPPED_DISABLED",
    "SKIPPED_DOC_TYPES",
    "SKIPPED_NOT_APPLICABLE",
    "STAGE",
    "WRITTEN",
    "citation_pins_applies",
    "citation_pins_applies_to_item",
    "load_citation_scan",
    "run_citation_pins",
]
