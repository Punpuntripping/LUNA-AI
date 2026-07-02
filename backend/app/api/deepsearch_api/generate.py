"""Headless answer generation for the editorial bot.

``generate_answer_headless`` drives the SAME pipeline an in-app message uses
(``agents.orchestrator.handle_message``) but WITHOUT the SSE framing, the
per-conversation send-dedup, or the quota gate that
``message_service.send_message_stream`` wraps around it. The editorial bot is
our own cost and must not be capped by user quota windows; cost is still
recorded because ``handle_message`` opens ``collect_llm_calls`` internally and
bills every LLM call to the bot user's ``llm_calls`` ledger.

It replicates ``send_message_stream``'s crash-safe ordering (Absolute Rule #7):

    1. create the throwaway conversation owned by the bot,
    2. save the user message = question  (BEFORE any AI call),
    3. insert the assistant-message placeholder,
    4. consume ``handle_message`` to completion, capturing the
       ``workspace_item_created`` event's ``item_id``.

Returns the throwaway ``conversation_id``, the generated ``workspace_item_id``
(``None`` on a chat-only route — see plan §10), and the ``assistant_message_id``.

The heavy ``handle_message`` orchestrator import is deferred to call time to
keep this module's import cheap and side-effect-free (boot-safety).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from supabase import Client as SupabaseClient

# Reuse the exact message-row insert/update helpers message_service uses, so the
# throwaway conversation is byte-identical in shape to an organic one.
from backend.app.services.message_service import (
    _insert_assistant_placeholder,
    _insert_user_message,
    _update_message_content,
)
from shared.config import get_settings
from shared.db.run import run_db
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()


@dataclass
class HeadlessResult:
    """Outcome of one headless generation run."""

    conversation_id: str
    workspace_item_id: Optional[str]
    assistant_message_id: str
    content_text: str


def _create_bot_conversation(
    supabase: SupabaseClient,
    bot_user_id: str,
    question: str,
) -> str:
    """Insert a throwaway conversation owned by the editorial bot; return its id.

    A general (non-case) conversation — the same lazy-created shape an in-app
    general chat uses. Title derives from the question for readability in any
    admin view.
    """
    title = (question or "").strip()[:60]
    if len(question or "") > 60:
        title += "..."
    if not title:
        title = "منشور مدونة"

    result = (
        supabase.table("conversations")
        .insert({"user_id": bot_user_id, "title_ar": title})
        .execute()
    )
    if not result.data:
        raise RuntimeError("failed to create editorial-bot conversation")
    return result.data[0]["conversation_id"]


async def generate_answer_headless(
    supabase: SupabaseClient,
    *,
    bot_user_id: str,
    question: str,
    metadata: Optional[dict[str, Any]] = None,
) -> HeadlessResult:
    """Run the full generation pipeline headlessly as the editorial bot.

    Raises ``asyncio.TimeoutError`` if generation exceeds
    ``LUNA_PIPELINE_TIMEOUT_S`` (the caller maps that to ``generation_timeout``).
    Any pipeline exception propagates to the caller (mapped to
    ``generation_failed``).
    """
    # Deferred import — keeps module import cheap + avoids any import-order edge.
    from agents.orchestrator import handle_message

    # 1. Throwaway conversation owned by the bot.
    conversation_id = await run_db(
        _create_bot_conversation, supabase, bot_user_id, question
    )

    # 2. Save the user message BEFORE the AI call (Absolute Rule #7). If the
    #    process crashes mid-generation the question is not lost and the job's
    #    catch-up sweep can safely re-drive it.
    user_message_id = str(uuid.uuid4())
    await run_db(_insert_user_message, supabase, user_message_id, conversation_id, question)

    # 3. Assistant placeholder — the row the produced workspace_item links to
    #    via workspace_items.message_id (the publishers thread it through).
    assistant_message_id = str(uuid.uuid4())
    await run_db(
        _insert_assistant_placeholder, supabase, assistant_message_id, conversation_id
    )

    # 4. Consume the pipeline to completion. Capture the workspace_item_created
    #    event's item_id (the artifact we snapshot). No SSE, no quota, no dedup.
    workspace_item_id: Optional[str] = None
    content_text = ""

    with _logfire.span(
        "editorial.generate_headless",
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
    ) as _span:
        async with asyncio.timeout(get_settings().LUNA_PIPELINE_TIMEOUT_S):
            async for event in handle_message(
                question=question,
                user_id=bot_user_id,
                conversation_id=conversation_id,
                supabase=supabase,
                case_id=None,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
            ):
                etype = event.get("type")
                if etype == "token":
                    content_text += event.get("text", "")
                elif etype == "workspace_item_created":
                    iid = event.get("item_id")
                    if iid:
                        workspace_item_id = iid  # last publish wins if >1
                elif etype == "workspace_item_updated":
                    # An in-place edit route (artifact-editor) emits this instead
                    # of _created. Fall back to it if no _created was seen.
                    iid = event.get("item_id")
                    if iid and workspace_item_id is None:
                        workspace_item_id = iid

        try:
            _span.set_attribute("workspace_item_id", workspace_item_id or "")
            _span.set_attribute("content_chars", len(content_text))
        except Exception:  # noqa: BLE001
            pass

    # Persist the streamed chat text onto the assistant placeholder (best-effort
    # — keeps the throwaway conversation coherent; the real answer for a
    # deep_search route lives on the workspace_item, not this row).
    if content_text:
        try:
            await run_db(
                _update_message_content,
                supabase,
                assistant_message_id,
                {"content": content_text},
            )
        except Exception:  # noqa: BLE001
            logger.warning("headless: assistant content update failed", exc_info=True)

    return HeadlessResult(
        conversation_id=conversation_id,
        workspace_item_id=workspace_item_id,
        assistant_message_id=assistant_message_id,
        content_text=content_text,
    )


__all__ = ["generate_answer_headless", "HeadlessResult"]
