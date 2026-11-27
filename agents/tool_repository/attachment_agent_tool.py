"""ask_attachment_agent — the router's way into the user's uploaded files.

Plan: ``.claude/plans/attachment_agent.md`` §2.1 (D1, D2, D4, D18). The router
keeps the cards (summary, doc_type) and its «which document?» rule; reading the
user's files — and changing them when the user asks — is the attachment agent's
job (``agents/attachment_agent``). This tool is the seam:

* **Shown only when the flag is on for this user** (``RouterDeps.
  attachment_agent_on``, set by ``orchestrator._route`` from
  ``attachment_agent_enabled``) and withheld on the router's talk-only hand-back
  turn, like the other tools that can write (``handback_guard``). Flag off, the
  model's tool list is exactly today's.
* **No question argument.** The agent gets ``RouterDeps.user_message`` — the
  user's message word for word — so the router cannot reword it (the owner's
  no-restatement rule, enforced by construction). The router only picks the
  files (``WI-n`` aliases; none = every attachment in the conversation).
* **One call per user message.** A second call in the same run (parallel or
  later) runs nothing: one agent run reads and edits for the whole message, and
  a second one could apply the same edit twice.
* **Never raises.** Every failure is a plain system line the router answers
  from (router-tool house rule, cf. ``edit_artifact``).

Registration::

    from agents.tool_repository.attachment_agent_tool import register_ask_attachment_agent
    register_ask_attachment_agent(router_agent)
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.tools import ToolDefinition

from agents.tool_repository.handback_guard import hide_on_planner_handback
from agents.tool_repository.unfold_workspace_item import _resolve_wi_alias

logger = logging.getLogger(__name__)

_ATTACHMENT_KIND = "attachment"

# Router-facing system lines (never shown to the user as such: the router's
# validator strips 〔[نظام] …〕 markers from its replies).
NOT_USER_FILES_AR = (
    "〔[نظام] {aliases} ليست من ملفات المستخدم المرفوعة؛ هذه الأداة لملفاته المرفوعة "
    "فقط. اختر رموز ملفاته من بطاقات مساحة العمل (kind=attachment)، أو اترك القائمة "
    "فارغة لكل ملفاته في المحادثة. مخرجاتنا (التقارير والمذكرات والملاحظات) تُفتح "
    "بـ unfold_workspace_item.〕"
)
NO_USER_FILES_AR = (
    "〔[نظام] لا توجد في هذه المحادثة ملفات رفعها المستخدم. لا تذكر موضعًا أو نصًا "
    "من ملف؛ أخبر المستخدم بذلك واسأله عن الملف الذي يقصده.〕"
)
ALREADY_CALLED_AR = (
    "〔[نظام] سبق استدعاء وكيل المرفقات في هذا الدور برسالة المستخدم كاملة، فلا "
    "يُستدعى مرة ثانية. أجب المستخدم من نتيجته.〕"
)
FAILED_AR = (
    "〔[نظام] تعذّر الاطلاع على ملفات المستخدم الآن. أخبره بإيجاز أنك لم تستطع "
    "قراءة الملف في هذه اللحظة، ولا تذكر موضعًا أو نصًا من الملفات.〕"
)


async def show_when_attachment_agent_on(
    ctx: RunContext[Any], tool_def: ToolDefinition
) -> ToolDefinition | None:
    """``prepare`` hook: the tool exists only for a user the flag is on for, and
    never on the talk-only hand-back turn."""
    if not getattr(ctx.deps, "attachment_agent_on", False):
        return None
    return await hide_on_planner_handback(ctx, tool_def)


def _attachment_ids(deps: Any) -> set[str]:
    """item_ids of this conversation's uploaded files, from the loaded cards."""
    return {
        str(s.get("item_id"))
        for s in (getattr(deps, "workspace_item_summaries", None) or [])
        if s.get("item_id") and s.get("kind") == _ATTACHMENT_KIND
    }


def resolve_files(deps: Any, files: list[str] | None) -> tuple[list[str], list[str]]:
    """``WI-n`` aliases → the item_ids of the user's uploaded files.

    Returns ``(item_ids, rejected_aliases)``. Only items of THIS conversation
    (the loaded cards) of kind ``attachment`` are kept: an unknown alias, a raw
    UUID from anywhere else, or one of our own outputs is rejected.
    """
    alias_map = getattr(deps, "wi_alias_map", None) or {}
    attachments = _attachment_ids(deps)
    item_ids: list[str] = []
    rejected: list[str] = []
    for raw in files or []:
        alias = str(raw or "").strip()
        if not alias:
            continue
        item_id = _resolve_wi_alias(alias, alias_map)
        if item_id and str(item_id) in attachments:
            if str(item_id) not in item_ids:
                item_ids.append(str(item_id))
        else:
            rejected.append(alias)
    return item_ids, rejected


async def ask_attachment_agent_for_deps(deps: Any, files: list[str] | None) -> str:
    """The tool body, outside the registered closure so it tests on its own."""
    try:
        if getattr(deps, "attachment_agent_called", False):
            return ALREADY_CALLED_AR
        if not _attachment_ids(deps):
            return NO_USER_FILES_AR
        item_ids, rejected = resolve_files(deps, files)
        if rejected and not item_ids:
            return NOT_USER_FILES_AR.format(aliases="، ".join(rejected))
        # Set before the first await: a parallel second call sees it at once.
        deps.attachment_agent_called = True

        # Lazy import (like edit_artifact): keeps the router module import-light
        # and lets tests monkeypatch ``agents.attachment_agent.runner``.
        from agents.attachment_agent.models import AttachmentAgentCall
        from agents.attachment_agent.render import render_for_router
        from agents.attachment_agent.runner import run_attachment_agent

        call = AttachmentAgentCall(
            user_id=str(getattr(deps, "user_id", "") or ""),
            conversation_id=str(getattr(deps, "conversation_id", "") or ""),
            message_id=getattr(deps, "message_id", None) or None,
            # Word for word, as the router received it — never reworded.
            user_message=getattr(deps, "user_message", "") or "",
            item_ids=item_ids,
        )
        result = await run_attachment_agent(call, supabase=deps.supabase)
        logger.info(
            "ask_attachment_agent: outcome=%s files=%d rejected=%d points=%d edits=%d",
            result.outcome, len(item_ids), len(rejected), len(result.points),
            len(result.edits),
        )
        if result.error:
            logger.warning("ask_attachment_agent: agent error: %s", result.error)
        return render_for_router(result)
    except Exception as exc:  # noqa: BLE001 - a router tool never raises
        logger.warning("ask_attachment_agent failed: %s", exc, exc_info=True)
        return FAILED_AR


def register_ask_attachment_agent(agent: Agent) -> None:
    """Register ``ask_attachment_agent`` on the router agent.

    The deps must carry ``supabase``, ``user_id``, ``conversation_id``,
    ``user_message``, ``wi_alias_map``, ``workspace_item_summaries`` and the
    flag ``attachment_agent_on`` (``RouterDeps`` does); ``message_id`` and
    ``attachment_agent_called`` are read with ``getattr``.
    """

    @agent.tool(prepare=show_when_attachment_agent_on)
    async def ask_attachment_agent(
        ctx: RunContext[Any],
        files: list[str] | None = None,
    ) -> str:
        """Ask the attachment agent about the files the user uploaded (kind=attachment) — it reads them, and changes how we read them when the user asks.

        This is your only way into the user's uploaded files. Use it when the user's message:
        - asks where something is in their files, or what a file says on a point («وين قال الخبير إن المدة المتبقية 142 يوم؟»، «اش مكتوب في البند السادس من العقد المرفق؟»، «حدد مكان هذه الإفادة من المدعي»);
        - points at a thread in «الخيوط» or asks about one;
        - asks to change something in a file's reading: a name or number misread in its text, a section's importance, a thread, a gap, or the client's side in «الخيوط»;
        - asks to re-analyze a file («ماعجبني التحليل، أعد تحليله»).
        Not for our own outputs (reports, memos, notes: unfold_workspace_item), not for a library source (simple_search), and not for a legal search about what a file says (deep_search).

        The user's message is passed to the agent automatically, word for word: there is no question to write. Call it ONCE per user message, with every file the message may concern.

        Args:
            files: WI-N aliases of the user's uploaded files the message is about (e.g. ["WI-2"]). Empty = every file the user uploaded in this conversation.

        Returns:
            What the agent found (statements with word-for-word quotes and their exact place), the changes it applied, or a question to ask the user. Answer only from it.
        """
        return await ask_attachment_agent_for_deps(ctx.deps, files)


__all__ = [
    "register_ask_attachment_agent",
    "ask_attachment_agent_for_deps",
    "resolve_files",
    "show_when_attachment_agent_on",
]
