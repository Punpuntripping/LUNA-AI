"""save_memo — pin a pivotal user message as a durable workspace item.

The router's context is filtered + truncated every turn: it only loads
messages AFTER ``conversations.compacted_through_message_id`` and the memory
pre-hook compacts the conversation on each turn. So a large, pivotal user
message (a full request + template, e.g. a pasted legal draft) can fall out of
the live window after a few turns and be lost to every downstream agent.

Workspace items, by contrast, are ALWAYS re-injected as summaries and can be
unfolded on demand. ``save_memo`` promotes such a core message to a
``kind='note'`` workspace item (``metadata.subtype='memo'``) so it becomes
permanent and always-visible — the anchor of the conversation.

Design notes:
  * **Verbatim** — the stored body is the user's raw message copied exactly
    (``ctx.deps.user_message``), prefixed with a short marker line. The LLM
    supplies only the ``title``; it never re-types the body, so nothing is
    paraphrased away.
  * **Append-only** — multiple memos per conversation are allowed (each
    pivotal message may carry distinct details). The only guard is a
    same-content skip so an identical block isn't pinned twice by accident.
  * **wi_seq alias injection** — a memo created mid-run gets a fresh ``wi_seq``
    that is NOT in the router's start-of-run ``wi_alias_map``. The tool injects
    it so the router output validator can resolve the memo's ``WI-{seq}`` if the
    LLM attaches it, and appends a summary so the next model turn sees it.
  * **Deps sinks** — the tool can't yield SSE or guarantee attachment from
    inside the agent loop, so it stashes the ``workspace_item_created`` event on
    ``deps.pending_sse_events`` and the new item_id on
    ``deps.force_attach_item_ids``; ``run_router`` returns both to ``_route``
    which drains them (emit chip + force-attach to the dispatch).

The DB logic lives in module-level functions (``save_memo_core``,
``memo_exists_with_content``, ``build_memo_content``) so it unit-tests without
an agent or a live DB — mirroring ``unfold_workspace_item``. The ``create``
call is wrapped in ``_insert_workspace_item`` (lazy backend import) so importing
this module stays light and tests can monkeypatch the insert.

Registration::

    from agents.tool_repository.save_memo import register_save_memo
    register_save_memo(agent)   # deps must satisfy HasMemoContext
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic_ai import Agent, ModelRetry, RunContext

logger = logging.getLogger(__name__)


# --- Schema config: a table/column rename is a one-line change here. ----------
_TABLE = "workspace_items"
NOTE_KIND = "note"
MEMO_SUBTYPE = "memo"
CREATED_BY_AGENT = "agent"

# Generic Arabic marker prepended to every memo body. Rendered as a blockquote
# callout so the pinned core message reads distinctly in the workspace pane. Not
# tied to "first message" — a conversation may pin several core messages.
_MARKER = "> 📌 رسالة أساسية من المستخدم — تتضمن تفاصيل جوهرية للطلب"

_FALLBACK_TITLE = "الطلب الأساسي"
# Short summary stored on the injected in-run summary dict (the eager-context
# renderer falls back to this when summary is empty).
_MEMO_SUMMARY = "رسالة أساسية من المستخدم (مثبّتة)."


@runtime_checkable
class HasMemoContext(Protocol):
    """Structural deps contract for the ``save_memo`` tool.

    ``RouterDeps`` satisfies this. Kept loose (``object`` for the client) to
    avoid a hard supabase import here. The four mutable sinks
    (``wi_alias_map``, ``workspace_item_summaries``, ``pending_sse_events``,
    ``force_attach_item_ids``) are mutated in place by the tool and drained by
    ``run_router`` / ``_route``.
    """

    supabase: object
    user_id: str
    conversation_id: str
    user_message: str
    wi_alias_map: dict
    workspace_item_summaries: list
    pending_sse_events: list
    force_attach_item_ids: list


# --------------------------------------------------------------------------- #
# Pure surface — unit-testable in isolation.
# --------------------------------------------------------------------------- #


def build_memo_content(raw_message: str) -> str:
    """Return the memo body: the marker line + the verbatim user message.

    The raw message is copied exactly (only outer whitespace stripped) so no
    detail is paraphrased away.
    """
    body = (raw_message or "").strip()
    return f"{_MARKER}\n\n{body}"


def _normalize(s: str) -> str:
    """Collapse all whitespace runs to single spaces + strip, for dedup compares."""
    return " ".join((s or "").split())


def memo_exists_with_content(supabase, conversation_id: str, content_md: str) -> bool:
    """True if a non-deleted ``subtype='memo'`` note with identical (normalized)
    content already exists in the conversation.

    Filters ``kind='note'`` in PostgREST then checks ``metadata.subtype`` and the
    content match in Python — JSON-path equality is done client-side to stay
    robust against NULL metadata. Never raises: a lookup hiccup returns False
    (better to risk a rare duplicate than to drop the user's core message).
    """
    target = _normalize(content_md)
    try:
        resp = (
            supabase.table(_TABLE)
            .select("content_md, metadata, kind")
            .eq("conversation_id", conversation_id)
            .eq("kind", NOTE_KIND)
            .is_("deleted_at", "null")
            .execute()
        )
        rows = getattr(resp, "data", None) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("memo_exists_with_content lookup failed: %s", exc)
        return False
    for r in rows:
        md = r.get("metadata") or {}
        if isinstance(md, dict) and md.get("subtype") == MEMO_SUBTYPE:
            if _normalize(r.get("content_md") or "") == target:
                return True
    return False


def _insert_workspace_item(
    supabase, *, user_id: str, conversation_id: str, title: str,
    content_md: str, metadata: dict,
) -> dict:
    """Insert the memo row and return the created row (incl. trigger-assigned
    ``wi_seq``). Lazy backend import keeps this module light; tests monkeypatch
    this function rather than the heavy service layer.
    """
    from backend.app.services.workspace_service import create_workspace_item

    return create_workspace_item(
        supabase,
        user_id,
        kind=NOTE_KIND,
        created_by=CREATED_BY_AGENT,
        title=title,
        conversation_id=conversation_id,
        content_md=content_md,
        metadata=metadata,
    )


@dataclass
class MemoSaveResult:
    """Outcome of :func:`save_memo_core`.

    ``skipped=True`` → an identical memo already existed; nothing was inserted
    and the other fields are unset. Otherwise the new item's identifiers + the
    ``workspace_item_created`` SSE event are populated.
    """

    skipped: bool
    item_id: str = ""
    wi_seq: int | None = None
    title: str = ""
    content_md: str = ""
    sse_event: dict | None = None


def save_memo_core(
    supabase, *, user_id: str, conversation_id: str, raw_message: str, title: str,
) -> MemoSaveResult:
    """Persist the verbatim user message as a pinned ``note`` (subtype=memo).

    Builds the marker+verbatim body, skips when an identical memo already
    exists, otherwise inserts the row and returns its identifiers plus the
    ``workspace_item_created`` SSE event the orchestrator should forward.
    """
    content_md = build_memo_content(raw_message)
    clean_title = (title or "").strip() or _FALLBACK_TITLE

    if memo_exists_with_content(supabase, conversation_id, content_md):
        return MemoSaveResult(skipped=True, content_md=content_md)

    row = _insert_workspace_item(
        supabase,
        user_id=user_id,
        conversation_id=conversation_id,
        title=clean_title,
        content_md=content_md,
        metadata={"subtype": MEMO_SUBTYPE},
    )
    item_id = row.get("item_id") or row.get("artifact_id") or ""
    wi_seq = row.get("wi_seq")
    title_out = row.get("title", clean_title)
    sse_event = {
        "type": "workspace_item_created",
        "item_id": item_id,
        "kind": NOTE_KIND,
        "title": title_out,
        "subtype": MEMO_SUBTYPE,
        "created_by": CREATED_BY_AGENT,
    }
    return MemoSaveResult(
        skipped=False,
        item_id=item_id,
        wi_seq=wi_seq,
        title=title_out,
        content_md=content_md,
        sse_event=sse_event,
    )


# --------------------------------------------------------------------------- #
# Pydantic AI tool.
# --------------------------------------------------------------------------- #


def register_save_memo(agent: Agent) -> None:
    """Register the ``save_memo`` tool on a Pydantic AI agent.

    The agent's deps must structurally satisfy :class:`HasMemoContext`. The tool
    mutates the deps' four sinks in place; ``run_router`` returns them to
    ``_route`` for SSE emission + force-attach.
    """

    @agent.tool
    async def save_memo(  # noqa: RUF029 — supabase client is sync by design
        ctx: RunContext[HasMemoContext],
        title: str,
    ) -> str:
        """Save the user's core message (a substantive request or a long template) as a pinned item.

        Use this tool **only** when the user explicitly shares a core request or a
        template containing details that must not be lost — such as pasting a draft
        or a full form. Its purpose is to protect this content from being lost when
        the conversation is compacted later.

        The tool saves the user's message text **verbatim** (do not re-type it), and
        the item is automatically attached to any task in this reply. Do not call it
        for ordinary short messages or simple questions. Afterward you may briefly
        mention to the user that you pinned their request.

        Args:
            title: A short Arabic title derived from the message content (describe the
                topic, not the action), used as the title of the item's card in the
                workspace.

        Returns:
            A short Arabic confirmation including the new item's alias (WI-N), or a
            notice that the message is already saved.

        Raises:
            ModelRetry: when there is no message to save or the save fails — fix and retry.
        """
        raw = (getattr(ctx.deps, "user_message", "") or "").strip()
        if not raw:
            # Nothing to pin (no current user message). Return plainly rather
            # than ModelRetry: the model can't "fix" an absent message by
            # retrying, so a retry loop would only stall the turn.
            return (
                "لا توجد رسالة أساسية لحفظها في هذه اللحظة. استدعِ هذه الأداة فقط "
                "حين يشارك المستخدم طلباً جوهرياً أو قالباً."
            )

        try:
            result = save_memo_core(
                ctx.deps.supabase,
                user_id=ctx.deps.user_id,
                conversation_id=ctx.deps.conversation_id,
                raw_message=raw,
                title=title,
            )
        except Exception as exc:  # noqa: BLE001
            raise ModelRetry(
                "Failed to save the core message due to a database error. Retry."
            ) from exc

        if result.skipped:
            return "هذه الرسالة محفوظة مسبقاً كعنصر أساسي مثبّت — لا حاجة لحفظها مجدداً."

        # ── Inject the new alias so the output validator can resolve the memo's
        #    WI-{seq} if the LLM attaches it, and surface it in the next model
        #    turn's eager summaries.
        if result.wi_seq is not None and result.item_id:
            try:
                ctx.deps.wi_alias_map[int(result.wi_seq)] = result.item_id
                ctx.deps.workspace_item_summaries.append({
                    "item_id": result.item_id,
                    "wi_seq": result.wi_seq,
                    "kind": NOTE_KIND,
                    "title": result.title,
                    "summary": _MEMO_SUMMARY,
                })
            except Exception:  # noqa: BLE001 — sinks are best-effort
                logger.debug("save_memo: alias/summary injection failed", exc_info=True)

        # ── Sinks drained by run_router → _route: emit the chip + force-attach.
        if result.sse_event is not None:
            ctx.deps.pending_sse_events.append(result.sse_event)
        if result.item_id:
            ctx.deps.force_attach_item_ids.append(result.item_id)

        alias = f"WI-{result.wi_seq}" if result.wi_seq is not None else "العنصر"
        return (
            f"تم حفظ رسالة المستخدم الأساسية كعنصر مثبّت «{alias}» "
            f"وسيُرفق تلقائياً بأي مهمة في هذا الرد."
        )


__all__ = [
    "register_save_memo",
    "save_memo_core",
    "memo_exists_with_content",
    "build_memo_content",
    "MemoSaveResult",
    "HasMemoContext",
    "NOTE_KIND",
    "MEMO_SUBTYPE",
    "CREATED_BY_AGENT",
]
