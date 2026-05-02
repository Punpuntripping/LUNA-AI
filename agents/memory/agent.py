"""Memory agent — per-item workspace summarization + conversation compaction.

Wave 9 — mock implementation.
Wave 10+ will replace the mock summarization calls with real LLM invocations;
the call shapes, DB writes, and compaction logic stay identical.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from supabase import Client as SupabaseClient

from agents.runs import AgentRunRecord, record_agent_run

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

DEFAULT_COMPACT_MAX_TOKENS = 10_000
DEFAULT_COMPACT_FRACTION   = 0.60
DRIFT_THRESHOLD            = 0.25   # 25 % change in content length triggers re-summary

# ---------------------------------------------------------------------------
# Token-counting helper (tiktoken is not in requirements.txt — see note below)
# ---------------------------------------------------------------------------
# NOTE FOR MAINTAINER: `tiktoken` is NOT currently listed in
# backend/requirements.txt.  To enable accurate token counting add:
#
#     tiktoken>=0.7.0
#
# to backend/requirements.txt and re-deploy.  Until then every call falls
# back to the len(text)//4 heuristic, which is ≈ accurate enough for the
# compaction threshold check.

try:
    import tiktoken as _tiktoken
    _enc = _tiktoken.get_encoding("cl100k_base")

    def _count_tokens(text: str) -> int:
        return len(_enc.encode(text))

except ImportError:
    _tiktoken = None   # type: ignore[assignment]
    _enc = None        # type: ignore[assignment]
    logger.warning(
        "tiktoken not installed — falling back to len(text)//4 for token counting. "
        "Add tiktoken>=0.7.0 to backend/requirements.txt for accurate counts."
    )

    def _count_tokens(text: str) -> int:  # type: ignore[misc]
        return len(text) // 4


# ---------------------------------------------------------------------------
# Tool-pair boundary helper
# ---------------------------------------------------------------------------

# TODO (Wave 10) — tool-pair boundary rule from Pydantic AI docs
# (10_message_history.md):
#
#   The compaction cutoff MUST NOT split a ToolCallPart from its matching
#   ToolReturnPart.  After computing the naive fraction-based boundary, walk
#   forward through the message list until the current message is NOT a
#   tool-return (i.e. is not ModelResponse immediately following a
#   ToolCallPart).  That position becomes the actual cutoff index.
#   Splitting a ToolCallPart / ToolReturnPart pair causes the model to error
#   on resume because the context becomes structurally invalid.
#
# For Wave 9 the messages table stores plain text content (no tool parts),
# so this is a no-op.  Wave 10 will refine this when real message_history
# with ToolCallPart / ToolReturnPart lands.

def _walk_to_safe_boundary(messages: list[dict[str, Any]], idx: int) -> int:
    """Return the first safe compaction cutoff at or after `idx`.

    Wave 9 placeholder — always returns `idx` unchanged.
    Wave 10: scan forward while messages[idx] is a ToolReturnPart response
    to ensure we never split a ToolCallPart/ToolReturnPart pair.
    """
    return idx


# ---------------------------------------------------------------------------
# Mock summary generation
# ---------------------------------------------------------------------------

def _mock_item_summary(title: str | None, content_md: str) -> str:
    """Generate a deterministic 1–3 sentence Arabic mock summary for an item.

    Wave 10+ replaces this with a real LLM call.
    """
    display_title = title or "عنصر"
    excerpt = content_md[:200].strip()
    if len(content_md) > 200:
        excerpt += "..."
    return f"ملخص للعنصر: {display_title} - {excerpt}"


def _mock_compaction_summary(n_messages: int, conversation_id: str) -> str:
    """Generate a deterministic Arabic mock summary for a compacted conversation segment.

    Wave 10+ replaces this with a real LLM call.
    """
    return (
        f"ملخص للمحادثة السابقة: {n_messages} رسالة بين المستخدم والمساعد "
        f"في المحادثة {conversation_id}. "
        "تمت معالجة هذه الرسائل وتلخيصها للحفاظ على السياق مع تقليل استهلاك الرموز."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def summarize_workspace_item(
    supabase: SupabaseClient,
    item_id: str,
) -> str:
    """Generate a 1–3 sentence Arabic summary of a workspace item's content_md.

    Writes ``summary``, ``summary_source_length``, and ``summary_updated_at``
    back to the row.  Returns the summary text.

    Idempotent: if the row already has a non-null summary whose
    ``summary_source_length`` matches the current ``len(content_md)`` the
    function returns immediately without touching the DB.
    """
    # Fetch the item
    result = (
        supabase.table("workspace_items")
        .select("item_id, title, content_md, summary, summary_source_length, deleted_at")
        .eq("item_id", item_id)
        .single()
        .execute()
    )
    item: dict[str, Any] = result.data

    if item.get("deleted_at") is not None:
        raise ValueError(f"workspace_item {item_id} is soft-deleted")

    content_md: str = item.get("content_md") or ""
    current_len = len(content_md)

    # Idempotency check — skip if summary is fresh
    existing_summary: str | None = item.get("summary")
    existing_source_len: int | None = item.get("summary_source_length")
    if existing_summary and existing_source_len is not None and existing_source_len == current_len:
        logger.debug("summarize_workspace_item: item %s is already fresh, skipping", item_id)
        return existing_summary

    # Generate mock summary
    summary = _mock_item_summary(item.get("title"), content_md)

    # Persist
    (
        supabase.table("workspace_items")
        .update(
            {
                "summary": summary,
                "summary_source_length": current_len,
                "summary_updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        .eq("item_id", item_id)
        .execute()
    )

    logger.debug("summarize_workspace_item: wrote summary for item %s (%d chars)", item_id, current_len)
    return summary


async def resummarize_dirty_items(
    supabase: SupabaseClient,
    conversation_id: str,
) -> list[str]:
    """Find all non-convo_context, non-deleted workspace items in the
    conversation where the summary is NULL or has drifted >= 25 % and
    re-summarize each one.

    Returns the list of item_ids that were updated.

    Drift formula: abs(current_len - summary_source_length) / summary_source_length
    """
    # Fetch all eligible items — exclude convo_context (they are already summaries)
    # and soft-deleted rows.
    result = (
        supabase.table("workspace_items")
        .select("item_id, content_md, summary, summary_source_length, kind")
        .eq("conversation_id", conversation_id)
        .neq("kind", "convo_context")
        .is_("deleted_at", "null")
        .execute()
    )
    items: list[dict[str, Any]] = result.data or []

    updated_ids: list[str] = []

    for item in items:
        content_md: str = item.get("content_md") or ""
        current_len = len(content_md)
        existing_summary: str | None = item.get("summary")
        source_len: int | None = item.get("summary_source_length")
        item_id: str = item["item_id"]

        # Determine whether this item is dirty
        if existing_summary is None or source_len is None:
            # summary is NULL — dirty
            is_dirty = True
        else:
            drift = abs(current_len - source_len) / source_len if source_len > 0 else 1.0
            is_dirty = drift >= DRIFT_THRESHOLD

        if is_dirty:
            try:
                await summarize_workspace_item(supabase, item_id)
                updated_ids.append(item_id)
            except Exception as exc:
                logger.warning(
                    "resummarize_dirty_items: failed to summarize item %s: %s",
                    item_id,
                    exc,
                )

    logger.debug(
        "resummarize_dirty_items: conversation %s — %d/%d items updated",
        conversation_id,
        len(updated_ids),
        len(items),
    )
    return updated_ids


async def compact_conversation(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
    max_tokens: int = DEFAULT_COMPACT_MAX_TOKENS,
    fraction: float = DEFAULT_COMPACT_FRACTION,
) -> str | None:
    """Compact a conversation when its post-cutoff messages exceed max_tokens.

    Algorithm:
    1. Load ``conversations.compacted_through_message_id`` (cutoff pointer).
    2. Fetch all messages with ``created_at`` strictly after the cutoff
       message's ``created_at`` (or all messages if no cutoff exists yet).
    3. Count tokens across all post-cutoff messages.
    4. If total <= max_tokens → return None (no compaction needed).
    5. Take the oldest ``fraction`` of those messages as the batch to compact.
    6. Apply tool-pair boundary safety (see ``_walk_to_safe_boundary``).
    7. Generate a mock Arabic summary of the batch.
    8. Insert a ``convo_context`` workspace_item with the summary.
    9. Update ``conversations.compacted_through_message_id`` to the last
       summarized message's id.
    10. Record an agent_runs row via ``record_agent_run``.
    11. Return the new ``convo_context`` item_id.

    Returns None if compaction was not triggered.
    Fixed-window: one compaction per threshold breach (does not loop).
    """
    # ------------------------------------------------------------------
    # 1. Load conversation to get current compaction pointer
    # ------------------------------------------------------------------
    conv_result = (
        supabase.table("conversations")
        .select("conversation_id, compacted_through_message_id")
        .eq("conversation_id", conversation_id)
        .is_("deleted_at", "null")
        .single()
        .execute()
    )
    conversation: dict[str, Any] = conv_result.data
    cutoff_message_id: str | None = conversation.get("compacted_through_message_id")

    # ------------------------------------------------------------------
    # 2. Resolve cutoff created_at (needed for strict-after filter)
    # ------------------------------------------------------------------
    cutoff_created_at: str | None = None
    if cutoff_message_id is not None:
        cutoff_msg_result = (
            supabase.table("messages")
            .select("created_at")
            .eq("message_id", cutoff_message_id)
            .single()
            .execute()
        )
        cutoff_created_at = cutoff_msg_result.data.get("created_at")

    # ------------------------------------------------------------------
    # 3. Fetch post-cutoff messages ordered oldest-first
    # ------------------------------------------------------------------
    msg_query = (
        supabase.table("messages")
        .select("message_id, role, content, created_at")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=False)
    )
    if cutoff_created_at is not None:
        msg_query = msg_query.gt("created_at", cutoff_created_at)

    msg_result = msg_query.execute()
    messages: list[dict[str, Any]] = msg_result.data or []

    if not messages:
        return None

    # ------------------------------------------------------------------
    # 4. Count total tokens
    # ------------------------------------------------------------------
    full_text = " ".join(m.get("content") or "" for m in messages)
    total_tokens = _count_tokens(full_text)

    if total_tokens <= max_tokens:
        logger.debug(
            "compact_conversation: %s has %d tokens (<= %d threshold), skipping",
            conversation_id,
            total_tokens,
            max_tokens,
        )
        return None

    # ------------------------------------------------------------------
    # 5. Identify the batch to compact (oldest `fraction`)
    # ------------------------------------------------------------------
    naive_boundary = max(1, int(len(messages) * fraction))

    # ------------------------------------------------------------------
    # 6. Tool-pair boundary safety
    # ------------------------------------------------------------------
    safe_boundary = _walk_to_safe_boundary(messages, naive_boundary)
    batch = messages[:safe_boundary]

    if not batch:
        logger.warning(
            "compact_conversation: %s — empty batch after boundary walk, aborting",
            conversation_id,
        )
        return None

    last_summarized_message = batch[-1]
    last_summarized_id: str = last_summarized_message["message_id"]

    # ------------------------------------------------------------------
    # 7. Generate mock summary
    # ------------------------------------------------------------------
    summary_text = _mock_compaction_summary(len(batch), conversation_id)

    # ------------------------------------------------------------------
    # 8. Insert convo_context workspace_item
    # ------------------------------------------------------------------
    new_item_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    (
        supabase.table("workspace_items")
        .insert(
            {
                "item_id": new_item_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "kind": "convo_context",
                "created_by": "agent",
                "title": f"ملخص المحادثة — {now_iso[:10]}",
                "content_md": summary_text,
                "is_visible": False,  # convo_context is internal; hidden from chip bar
                "summary": summary_text,
                "summary_source_length": len(summary_text),
                "summary_updated_at": now_iso,
                "created_at": now_iso,
                "updated_at": now_iso,
            }
        )
        .execute()
    )

    # ------------------------------------------------------------------
    # 9. Update compacted_through_message_id
    # ------------------------------------------------------------------
    (
        supabase.table("conversations")
        .update({"compacted_through_message_id": last_summarized_id})
        .eq("conversation_id", conversation_id)
        .execute()
    )

    logger.info(
        "compact_conversation: %s compacted %d messages into item %s",
        conversation_id,
        len(batch),
        new_item_id,
    )

    # ------------------------------------------------------------------
    # 10. Record agent_runs row (fire-and-forget; never raises)
    # ------------------------------------------------------------------
    record_agent_run(
        supabase,
        AgentRunRecord(
            user_id=user_id,
            conversation_id=conversation_id,
            agent_family="memory",
            subtype="compact",
            output_item_id=new_item_id,
            input_summary=(
                f"Compacted {len(batch)}/{len(messages)} messages "
                f"({total_tokens} tokens total)"
            ),
            tokens_in=total_tokens,
            status="ok",
        ),
    )

    # ------------------------------------------------------------------
    # 11. Return new item id
    # ------------------------------------------------------------------
    return new_item_id
