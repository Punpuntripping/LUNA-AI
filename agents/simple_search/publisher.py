"""Publish ONE ``simple_search`` synthesis to the workspace (§7.4).

A ~60-line sibling of ``agents/agent_search/publisher.py``, deliberately NOT a
reuse of it: ``publish_search_result`` hard-wires ``agent_family="deep_search"``
(``publisher.py:184``) and ``subtype="legal_synthesis"`` (``:77``), and its
``SearchPublishInput`` carries five deep_search-only fields plus a forensics
block (``retrieval_artifacts`` + ``reranker_runs``) that a lookup has nothing to
put in. What IS reused is the pair that matters: ``create_workspace_item`` and
``persist_item_references``, both already generic.

Three decisions this file encodes:

* **``kind='agent_search'``** (D10). The lookup's card is a search result; a new
  kind would need a DB enum value, a frontend ``Record`` entry and a cap rule
  (``031_artifact_cap.sql:41``) for no gain.
* **No ``metadata.subtype``.** The frontend renders ``SUBTYPE_LABEL[subtype] ??
  subtype`` (``WorkspaceCard.tsx:81-83``), so an unregistered subtype prints its
  own raw English token on the card. With the key absent the card falls back to
  ``KIND_LABEL['agent_search']`` = «بحث», which is both correct and Arabic. A
  dedicated label is a one-line frontend change, not a publisher one.
* **``ref_count`` / ``cited_count``** are kept from ``artifact_builder``'s
  metadata — the frontend reads them (§7.4).

The backend imports are lazy and wrapped, so this module stays import-light and
the test suite monkeypatches the two wrappers rather than the heavy service
layer (the pattern is ``fetch_article._insert_statute_item``).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from agents.deep_search_v4.aggregator.models import Reference

logger = logging.getLogger(__name__)

_KIND = "agent_search"
_CREATED_BY = "agent"
_AGENT_FAMILY = "simple_search"  # present in agent_family_enum (verified live)
_TITLE_MAX = 80
_FALLBACK_TITLE = "نتيجة بحث"


@dataclass
class SimpleSearchPublishResult:
    """What the publisher returns: the new item + the SSE events to forward."""

    item_id: str = ""
    sse_events: list[dict] = field(default_factory=list)


def _decode_for_persist(text: str) -> str:
    """وضع السرية exit decode — restore real identifiers before persisting.

    The synthesis is pipeline text, i.e. encoded when the turn is masked, and
    the store-real invariant says DB rows must never hold fakes. Always-on and
    never gated by the masking flag; a byte-identical passthrough when no codec
    is active. Never raises — a decode hiccup must not lose the card.
    """
    try:
        from backend.app.services.masking_service import decode_for_persist

        return decode_for_persist(text)
    except Exception:  # noqa: BLE001
        logger.debug("simple_search publish: decode skipped", exc_info=True)
        return text


def _create_item(supabase, **kwargs) -> dict:
    """Insert the workspace item. Lazy import; monkeypatched whole in tests."""
    from backend.app.services.workspace_service import create_workspace_item

    return create_workspace_item(supabase, kwargs.pop("user_id"), **kwargs)


def _persist_refs(supabase, wi_id: str, references: list, cited: list[int]) -> int:
    """Persist the per-WI references. Lazy import; monkeypatched whole in tests.

    ``ura_results=None`` degrades cleanly (§7.2) — a lookup has no URA, and the
    only thing the URA supplies is the ``service_ref`` recovery for compliance
    refs, whose id we already carry on the ref row.
    """
    from backend.app.services.references_service import persist_item_references

    return persist_item_references(
        supabase,
        wi_id=wi_id,
        references=references,
        ura_results=None,
        cited_numbers=cited,
        ref_to_sub_queries=None,
    )


async def publish_simple_search_result(
    supabase,
    *,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    message_id: str | None,
    title: str,
    content_md: str,
    references: "list[Reference]",
    cited_numbers: list[int],
    level: str,
    data_type: str = "",
) -> SimpleSearchPublishResult:
    """Persist one lookup answer as a workspace item + its references.

    The item insert is NOT wrapped in try/except — a failure there is a real
    error the caller must see. The reference write IS best-effort, mirroring the
    sibling publisher: a refs hiccup must not lose the user-visible card.

    §6.3 / §9 trap 1 — ``workspace_item_references.domain`` is a hard CHECK.
    Until the migration widening it to ``articles`` / ``regulation_docs`` is
    APPLIED, refs on those two domains are dropped row-by-row by
    ``references_service``'s retry loop, silently (ERROR log only). The count
    this function logs is the ground truth: a card whose ``ref_count`` exceeds
    the persisted count is that trap firing.
    """
    clean_title = (title or "").strip()[:_TITLE_MAX] or _FALLBACK_TITLE
    body = _decode_for_persist(content_md or "")

    row = await asyncio.to_thread(
        _create_item,
        supabase,
        user_id=user_id,
        kind=_KIND,
        created_by=_CREATED_BY,
        title=clean_title,
        conversation_id=conversation_id,
        case_id=case_id,
        message_id=message_id,
        agent_family=_AGENT_FAMILY,
        content_md=body,
        metadata={
            "ref_count": len(references),
            "cited_count": len(cited_numbers),
            "level": level,
            "data_type": data_type,
        },
    )
    item_id = str(row.get("item_id") or row.get("artifact_id") or "")
    if not item_id:
        raise RuntimeError("simple_search: publish returned no item_id")

    if references:
        try:
            written = await asyncio.to_thread(
                _persist_refs, supabase, item_id, list(references), list(cited_numbers)
            )
            if written < len(references):
                logger.warning(
                    "simple_search publish: %d/%d refs persisted for %s "
                    "(domain CHECK not widened yet?)",
                    written, len(references), item_id,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "simple_search publish: refs persist failed for %s: %s",
                item_id, exc, exc_info=True,
            )

    return SimpleSearchPublishResult(
        item_id=item_id,
        sse_events=[{
            "type": "workspace_item_created",
            "item_id": item_id,
            "kind": _KIND,
            "title": row.get("title", clean_title),
            "created_by": _CREATED_BY,
        }],
    )


__all__ = ["SimpleSearchPublishResult", "publish_simple_search_result"]
