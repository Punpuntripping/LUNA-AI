"""Summary-NULL sweep job.

A ``workspace_items`` row can be left with ``summary = NULL`` despite having
summarizable ``content_md`` by two failure modes:

  * a dropped ``pg_net`` webhook — the AFTER-INSERT trigger's POST to
    ``/internal/summarize-workspace-item`` never arrived, so the summarizer was
    never attempted; or
  * a persist failure — the summarizer ran (and billed) but the final UPDATE
    that writes ``summary`` failed, leaving the row NULL with a
    ``metadata.summary_attempt`` marker present.

This daily sweep catches both. It selects the oldest summary-NULL debt (with a
real ``content_md`` body, past the min-age guard so it never races a freshly
inserted item's webhook), filters out legitimately-unsummarized short blurbs
and anything attempted within the retry window, then re-summarizes up to
``SWEEP_CAP`` items sequentially. Each ``summarize_workspace_item`` call opens
its own ``collect_llm_calls`` scope (it has no active one here), so the cost
lands in the ledger against the item's owner. Hard spend bound: ``SWEEP_CAP``
LLM re-runs per day.

The constant ``MIN_CONTENT_LENGTH_CHARS`` is imported from
``agents.memory.summarize`` so the sweep's "is this worth summarizing?" gate
stays in lock-step with the summarizer's own threshold.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from supabase import Client as SupabaseClient

from agents.memory.summarize import (
    MIN_CONTENT_LENGTH_CHARS,
    summarize_workspace_item,
)

logger = logging.getLogger(__name__)


# Max LLM re-runs per sweep — a hard spend bound on the daily job.
SWEEP_CAP = 25
# How many candidate rows to pull before filtering down to the cap.
SWEEP_FETCH_LIMIT = 100
# Never race a just-inserted item's trigger webhook.
MIN_ITEM_AGE_MIN = 30
# Only re-attempt items whose last attempt marker is older than this (24h).
RETRY_AFTER_S = 86_400


async def sweep_missing_summaries(supabase: SupabaseClient) -> dict:
    """Re-summarize summary-NULL workspace items that should have a summary.

    Returns a small report dict::

        {"candidates": <rows pulled>,
         "attempted": <rows we ran the summarizer on>,
         "summarized": <rows that produced a summary>}

    Best-effort and bounded: at most ``SWEEP_CAP`` LLM runs, executed
    sequentially (no stampede). ``summarize_workspace_item`` never raises, so a
    single bad item cannot abort the sweep.
    """
    now = datetime.now(timezone.utc)

    def _fetch():
        return (
            supabase.table("workspace_items")
            .select("item_id, content_md, metadata, created_at")
            .is_("summary", "null")
            .is_("deleted_at", "null")
            .not_.is_("content_md", "null")
            .lt(
                "created_at",
                (now - timedelta(minutes=MIN_ITEM_AGE_MIN)).isoformat(),
            )
            .order("created_at", desc=False)        # oldest debt first
            .limit(SWEEP_FETCH_LIMIT)
            .execute()
        )

    try:
        resp = await asyncio.to_thread(_fetch)
        rows = (getattr(resp, "data", None) or []) if resp else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("summary sweep: candidate fetch failed: %s", exc)
        return {"candidates": 0, "attempted": 0, "summarized": 0}

    picked: list[str] = []
    for r in rows:
        # Legitimately unsummarized short blurbs — leave them NULL.
        if len((r.get("content_md") or "").strip()) < MIN_CONTENT_LENGTH_CHARS:
            continue
        # Skip rows attempted within the retry window (in-flight or recent fail).
        at = ((r.get("metadata") or {}).get("summary_attempt") or {}).get("at")
        if at:
            try:
                age = (now - datetime.fromisoformat(str(at))).total_seconds()
                if age < RETRY_AFTER_S:
                    continue
            except Exception:
                pass   # unparseable marker → eligible
        picked.append(r["item_id"])
        if len(picked) >= SWEEP_CAP:
            break

    ok = 0
    for item_id in picked:                            # sequential — no LLM stampede
        try:
            if await summarize_workspace_item(supabase, item_id):
                ok += 1
        except Exception as exc:  # noqa: BLE001 — defensive; the callee never raises
            logger.warning(
                "summary sweep: summarize failed for item_id=%s: %s",
                item_id, exc,
            )

    report = {"candidates": len(rows), "attempted": len(picked), "summarized": ok}
    logger.info("summary sweep complete: %s", report)
    return report


__all__ = ["sweep_missing_summaries", "SWEEP_CAP"]
