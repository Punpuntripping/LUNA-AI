"""Internal webhook endpoints invoked by Supabase database triggers.

These routes are NOT for end users. They authenticate via a shared secret
header (``X-Webhook-Secret``) configured both in the app settings and as a
Postgres GUC (``app.webhook_secret``) on the Supabase side.

Currently:

* ``POST /internal/summarize-workspace-item`` — invoked by the
  ``summarize_artifact_on_insert`` trigger after a row is inserted into
  ``workspace_items``. Delegates to
  :func:`agents.memory.summarize.summarize_workspace_item`, which fetches the
  row, runs the artifact_summarizer agent, writes the result back to
  ``workspace_items.summary`` + ``metadata.artifact_summary``, and records a
  tier_2 ``agent_runs`` row. The summarize-and-persist logic lives in that
  module so the same code path can be invoked inline (non-webhook callers).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from agents.memory.summarize import summarize_workspace_item
from backend.app.deps import get_supabase
from backend.app.errors import ErrorCode, LunaHTTPException
from shared.config import get_settings
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()

router = APIRouter()


# ---------------------------------------------------------------------------
# Request payload
# ---------------------------------------------------------------------------


class SummarizeWorkspaceItemPayload(BaseModel):
    """Body the Postgres trigger posts.

    The trigger sends a minimal envelope — just the new row's ``item_id``.
    The summarize core re-fetches the row rather than trusting the trigger
    payload, so a summary write that races with another UPDATE still sees the
    current ``content_md``.
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
# Route
# ---------------------------------------------------------------------------


@router.post(
    "/summarize-workspace-item",
    response_model=SummarizeWorkspaceItemResponse,
    dependencies=[Depends(_verify_webhook_secret)],
)
async def summarize_workspace_item_webhook(
    payload: SummarizeWorkspaceItemPayload,
    supabase: SupabaseClient = Depends(get_supabase),
) -> SummarizeWorkspaceItemResponse:
    """Generate + persist an agent-facing summary for a workspace item.

    Thin wrapper over :func:`agents.memory.summarize.summarize_workspace_item`,
    which owns all guards (already-summarized / empty / below-min-length),
    the agent call, persistence, and cost recording.

    Idempotent and best-effort: the summarize core never raises, so this
    endpoint never raises into the client. The DB trigger is fire-and-forget;
    we don't want exceptions echoing back into the Postgres logs.

    The response ``status`` reflects whether a summary was written:
    ``"ok"`` when the summarize core produced + persisted a summary,
    ``"skipped"`` when a guard short-circuited it or an error was swallowed.
    """
    item_id = payload.item_id

    with _logfire.span("webhook.summarize_artifact", item_id=item_id) as _wb_span:
        summarized = await summarize_workspace_item(supabase, item_id)

        try:
            _wb_span.set_attribute("summarized", summarized)
        except Exception:
            pass

        if summarized:
            return SummarizeWorkspaceItemResponse(status="ok", item_id=item_id)
        return SummarizeWorkspaceItemResponse(
            status="skipped",
            item_id=item_id,
            detail="not_summarized",
        )
