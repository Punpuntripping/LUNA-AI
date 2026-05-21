"""Internal webhook endpoints invoked by Supabase database triggers.

These routes are NOT for end users. They authenticate via a shared secret
header (``X-Webhook-Secret``) configured both in the app settings and as a
Postgres GUC (``app.webhook_secret``) on the Supabase side.

Currently:

* ``POST /internal/summarize-workspace-item`` — invoked by the
  ``summarize_artifact_on_insert`` trigger after a row is inserted into
  ``workspace_items``. Fetches the row, looks up ``original_query`` from the
  conversation's most recent user message, runs the artifact_summarizer
  agent, writes the result back to ``workspace_items.summary`` +
  ``metadata.artifact_summary``, and records a tier_2 ``agent_runs`` row.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from agents.memory.artifact_summarizer import (
    ArtifactSummaryInput,
    ArtifactSummaryOutput,
    build_artifact_summary_deps,
    run_artifact_summary,
)
from agents.runs import AgentRunRecord, record_agent_run
from backend.app.deps import get_supabase
from backend.app.errors import ErrorCode, LunaHTTPException
from shared.config import get_settings
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()

router = APIRouter()


# Below this many characters of content_md, we skip the LLM entirely and
# leave summary = NULL. Matches the same gate enforced by the Postgres trigger
# (see migration 041) — kept here as defense in depth in case the webhook is
# called via a path that bypasses the trigger (manual POST, backfill script).
MIN_CONTENT_LENGTH_CHARS = 300


# ---------------------------------------------------------------------------
# Request payload
# ---------------------------------------------------------------------------


class SummarizeWorkspaceItemPayload(BaseModel):
    """Body the Postgres trigger posts.

    The trigger sends a minimal envelope — just the new row's ``item_id``.
    We re-fetch the row here rather than trusting the trigger payload, so a
    summary write that races with another UPDATE still sees the current
    ``content_md``.
    """

    item_id: str = Field(..., description="workspace_items.item_id of the new row")


class SummarizeWorkspaceItemResponse(BaseModel):
    status: str
    item_id: str
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _verify_webhook_secret(x_webhook_secret: Optional[str] = Header(default=None)) -> None:
    """Shared-secret auth. The trigger attaches the secret as
    ``X-Webhook-Secret``; missing/mismatched → 401.

    If ``INTERNAL_WEBHOOK_SECRET`` is unset in settings (typical for local
    dev where the trigger isn't wired) the endpoint is effectively closed:
    ALL calls 401. This is intentional — refusing rather than open-mode
    avoids accidental production deployments without a secret.
    """
    expected = (get_settings().INTERNAL_WEBHOOK_SECRET or "").strip()
    if not expected:
        raise LunaHTTPException(
            status_code=401,
            code=ErrorCode.AUTH_INVALID,
            detail="webhook auth not configured",
        )
    supplied = (x_webhook_secret or "").strip()
    if supplied != expected:
        raise LunaHTTPException(
            status_code=401,
            code=ErrorCode.AUTH_INVALID,
            detail="invalid webhook secret",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _persist_summary(
    supabase: SupabaseClient,
    item_id: str,
    summary: ArtifactSummaryOutput,
    source_length: int,
    existing_metadata: dict,
) -> None:
    """UPDATE workspace_items with the summary + telemetry. Best-effort."""
    metadata = dict(existing_metadata or {})
    metadata["artifact_summary"] = {
        "model_used": summary.model_used,
        "tokens_in": summary.tokens_in,
        "tokens_out": summary.tokens_out,
        "tokens_reasoning": summary.tokens_reasoning,
        "fallback_used": summary.fallback_used,
    }
    try:
        (
            supabase.table("workspace_items")
            .update({
                "summary": summary.summary_md,
                "summary_updated_at": datetime.now(timezone.utc).isoformat(),
                "summary_source_length": source_length,
                "metadata": metadata,
            })
            .eq("item_id", item_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "summarize-workspace-item: UPDATE failed for item_id=%s: %s",
            item_id, exc, exc_info=True,
        )


def _record_cost(
    supabase: SupabaseClient,
    row: dict,
    summary: ArtifactSummaryOutput,
) -> None:
    """Record a tier_2 agent_runs row. Skipped if the LLM never ran."""
    if summary.fallback_used:
        return
    try:
        record_agent_run(
            supabase,
            AgentRunRecord(
                user_id=str(row.get("user_id") or ""),
                conversation_id=str(row.get("conversation_id") or ""),
                case_id=row.get("case_id") and str(row["case_id"]) or None,
                agent_family="memory",
                subtype="summarize_artifact",
                output_item_id=str(row.get("item_id") or ""),
                tokens_in=summary.tokens_in,
                tokens_out=summary.tokens_out,
                tokens_reasoning=summary.tokens_reasoning,
                model_used=summary.model_used or None,
                per_phase_stats={
                    "summarize": {
                        "per_tier": {
                            "tier_2": {
                                "input": summary.tokens_in,
                                "output": summary.tokens_out,
                                "reasoning": summary.tokens_reasoning,
                            },
                        },
                    },
                },
                status="ok",
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "summarize-workspace-item: record_agent_run failed: %s", exc,
        )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post(
    "/summarize-workspace-item",
    response_model=SummarizeWorkspaceItemResponse,
    dependencies=[Depends(_verify_webhook_secret)],
)
async def summarize_workspace_item(
    payload: SummarizeWorkspaceItemPayload,
    supabase: SupabaseClient = Depends(get_supabase),
) -> SummarizeWorkspaceItemResponse:
    """Generate + persist an agent-facing summary for a workspace item.

    Idempotent: if ``summary`` is already set on the row, return early. If
    ``content_md`` is empty (e.g. attachment-kind rows), return early too.

    Never raises into the client — every failure path is logged and turned
    into a structured response. The DB trigger is fire-and-forget; we don't
    want exceptions echoing back into the Postgres logs.
    """
    item_id = payload.item_id

    with _logfire.span(
        "webhook.summarize_artifact",
        item_id=item_id,
    ) as _wb_span:
        # Fetch the row.
        try:
            resp = (
                supabase.table("workspace_items")
                .select("*")
                .eq("item_id", item_id)
                .maybe_single()
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("summarize-workspace-item: fetch failed item_id=%s: %s", item_id, exc)
            try:
                _wb_span.set_attributes({
                    "outcome": "fetch_failed",
                    "error.type": type(exc).__name__,
                    "error": str(exc),
                })
            except Exception:
                pass
            return SummarizeWorkspaceItemResponse(
                status="error", item_id=item_id, detail="fetch_failed"
            )

        row = (resp.data or {}) if resp else {}
        if not row:
            try:
                _wb_span.set_attribute("outcome", "not_found")
            except Exception:
                pass
            return SummarizeWorkspaceItemResponse(
                status="not_found", item_id=item_id, detail="row missing"
            )

        # Tag the span with the row's identity NOW so even early-exit paths
        # (already_summarized / empty / below_min) surface conversation_id.
        # PII note: user_id deliberately not surfaced — recoverable via Supabase
        # join from item_id when needed.
        try:
            _wb_span.set_attributes({
                "conversation_id": str(row.get("conversation_id") or ""),
                "case_id": str(row.get("case_id") or "") or None,
                "kind": str(row.get("kind") or ""),
                "content_md_chars": len((row.get("content_md") or "")),
                "has_describe_query": bool((row.get("describe_query") or "").strip()),
            })
        except Exception:
            pass

        # Idempotency: skip if already summarized (trigger may fire on resummarize
        # updates if expanded later; today it's INSERT-only).
        if (row.get("summary") or "").strip():
            try:
                _wb_span.set_attribute("outcome", "already_summarized")
            except Exception:
                pass
            return SummarizeWorkspaceItemResponse(
                status="skipped", item_id=item_id, detail="already_summarized"
            )

        content_md = (row.get("content_md") or "").strip()
        if not content_md:
            try:
                _wb_span.set_attribute("outcome", "empty_content_md")
            except Exception:
                pass
            # Attachment / empty-body kinds: nothing to summarize.
            return SummarizeWorkspaceItemResponse(
                status="skipped", item_id=item_id, detail="empty_content_md"
            )
        if len(content_md) < MIN_CONTENT_LENGTH_CHARS:
            try:
                _wb_span.set_attribute("outcome", "below_min_length")
            except Exception:
                pass
            # Below the threshold the LLM call is wasteful — short blurbs don't
            # need an agent-facing summary; downstream agents can just read
            # content_md directly. Saves token cost on notes / tiny artifacts.
            return SummarizeWorkspaceItemResponse(
                status="skipped",
                item_id=item_id,
                detail=f"content_md_below_min_length:{len(content_md)}<{MIN_CONTENT_LENGTH_CHARS}",
            )

        # The agent reads exactly three columns off the workspace_items row —
        # title, describe_query (router-written query description, migration 038),
        # and content_md. NULL describe_query is fine: the prompt falls back to
        # describing content without a query anchor.
        summary = await run_artifact_summary(
            ArtifactSummaryInput(
                describe_query=str(row.get("describe_query") or ""),
                content_md=content_md,
                title=str(row.get("title") or ""),
                kind=str(row.get("kind") or "agent_search"),
            ),
            build_artifact_summary_deps(),
        )

        # Persist + record cost (best-effort).
        _persist_summary(
            supabase,
            item_id,
            summary,
            source_length=len(content_md),
            existing_metadata=row.get("metadata") or {},
        )
        _record_cost(supabase, row, summary)

        try:
            _wb_span.set_attributes({
                "outcome": "ok_fallback" if summary.fallback_used else "ok",
                "model_used": summary.model_used,
                "tokens_in": summary.tokens_in,
                "tokens_out": summary.tokens_out,
                "fallback_used": summary.fallback_used,
                "summary_chars": len(summary.summary_md or ""),
            })
        except Exception:
            pass

        return SummarizeWorkspaceItemResponse(
            status="ok" if not summary.fallback_used else "ok_fallback",
            item_id=item_id,
        )
