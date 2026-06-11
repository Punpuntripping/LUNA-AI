"""edit_artifact — router tool for surgical edits on one workspace artifact.

The router's context is summaries only; it must never load full artifacts to
do edit work itself. For a clearly-scoped surgical edit (substitute a term,
delete a specific clause, fix a name/number/date) this tool resolves the
``WI-{seq}`` alias and hands off to the Layer-3 ``artifact_editor`` agent
(``agents/artifact_editor``), which gets the whole document injected, reasons
about EVERY location the request touches — including Arabic
grammatical-agreement ripples — and applies one atomic ``edit_supabase_md``
batch in-place (``prev_content_md`` snapshot = one-level undo). The router
pays only the tool-return summary, which it uses to brief the user.

Design + rationale: ``.claude/plans/artifact_editor.md``. Pattern mirrors
``save_memo``: protocol deps contract, deps sinks, lazy heavy import inside
the tool body, ``register_*`` entry point, Arabic LLM-facing docstring.

Parallel fan-out is free: the router model emits 2–3 ``edit_artifact`` calls
in one response (max 3); pydantic-ai runs them concurrently and each editor
owns one ``item_id``, so the optimistic locks never collide.

Failure contract: EVERY failure — unknown/malformed alias, missing item,
wrong kind, LLM error — is returned as a plain Arabic string, never a
``ModelRetry``. House rule for router tools (cf. ``unfold_workspace_item``
returning ``""`` on a miss, ``save_memo`` returning a no-op string): the
router should brief the user or pick a better alias on its NEXT model turn,
not burn tool-retry budget; it also keeps TestModel smoke runs completing.

Registration::

    from agents.tool_repository.edit_artifact import register_edit_artifact
    register_edit_artifact(agent)   # deps must satisfy HasEditorContext
"""
from __future__ import annotations

import logging
import re
from typing import Protocol, runtime_checkable

from pydantic_ai import Agent, RunContext

logger = logging.getLogger(__name__)


@runtime_checkable
class HasEditorContext(Protocol):
    """Structural deps contract for the ``edit_artifact`` tool.

    ``RouterDeps`` satisfies this. Kept loose (``object`` for the client) to
    avoid a hard supabase import here. ``pending_sse_events`` is a mutable
    sink: the tool appends ``workspace_item_updated`` events that
    ``run_router`` returns and ``_route`` drains (panel refresh).
    """

    supabase: object
    user_id: str
    user_message: str
    wi_alias_map: dict
    pending_sse_events: list


# ── Alias resolution ──────────────────────────────────────────────────────────
# Duplicated from ``agents/router/router.py::_resolve_wi_alias`` ON PURPOSE:
# router.py will import ``register_edit_artifact`` from THIS module in the
# wiring wave, so importing the helper back from agents.router.router would
# create a circular import. The helper is small and stable (migration 052);
# keep the two copies in sync if the alias scheme ever changes.

_WI_ALIAS_RE = re.compile(r"^WI-(\d+)$", re.IGNORECASE)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _resolve_wi_alias(alias: str, alias_map: dict[int, str]) -> str | None:
    """Resolve ``"WI-{seq}"`` → workspace_items.item_id UUID.

    Returns the UUID on success, ``None`` if the alias is malformed or its
    seq is not in the conversation's alias map. Accepts a raw UUID verbatim
    (defence-in-depth — mirrors the router's resolver).
    """
    if not alias:
        return None
    s = alias.strip()
    m = _WI_ALIAS_RE.match(s)
    if m:
        try:
            seq = int(m.group(1))
        except ValueError:
            return None
        return alias_map.get(seq)
    if _UUID_RE.match(s):
        return s
    return None


# ── Pydantic AI tool ──────────────────────────────────────────────────────────


def register_edit_artifact(agent: Agent) -> None:
    """Register the ``edit_artifact`` tool on a Pydantic AI agent.

    The agent's deps must structurally satisfy :class:`HasEditorContext`.
    The tool appends ``workspace_item_updated`` events to
    ``deps.pending_sse_events``; ``run_router`` returns them to ``_route``
    which forwards them down the SSE stream (panel refresh).
    """

    @agent.tool
    async def edit_artifact(
        ctx: RunContext[HasEditorContext],
        target_wi: str,
        task: str,
    ) -> str:
        """نفّذ تعديلاً جراحياً محدد النطاق على عنصر موجود في مساحة العمل.

        استخدم هذه الأداة للتعديلات الجراحية الواضحة على عنصر موجود: استبدال
        لفظ، حذف بند محدد، تصحيح اسم أو رقم أو تاريخ، إعادة صياغة جملة
        بعينها. المحرّر يستلم المستند كاملاً ويعالج كل المواضع المتأثرة بما
        فيها التطابق النحوي حولها. لا تستخدمها للتغييرات الهيكلية (إضافة قسم،
        إعادة هيكلة، تفصيل أوسع) ولا لما يحتاج مصادر قانونية جديدة — تلك توجّه
        إلى متخصص الكتابة.

        عند تعديل أكثر من عنصر، استدعِ الأداة مرة لكل عنصر في نفس الرد
        (بحد أقصى 3 عناصر).

        Args:
            target_wi: رمز العنصر المستهدف من الملخصات (مثل «WI-3»).
            task: اقتبس كلمات المستخدم المتعلقة بهذا العنصر **حرفياً** ولا
                تُعِد صياغتها — اقتطع فقط الجزء الخاص بهذا العنصر إن كان
                الطلب يشمل عدة عناصر.

        Returns:
            ملخص عربي للتغيير (ماذا تغيّر وأين وكم موضعاً) لتُبلغ به
            المستخدم، أو شرح لعدم التغيير / سبب الفشل، أو تنبيه بأن رمز
            العنصر غير صالح (استخدم رمزاً من الملخصات وأعد الاستدعاء).
        """
        item_id = _resolve_wi_alias(target_wi, ctx.deps.wi_alias_map or {})
        if not item_id:
            # Plain string, NOT ModelRetry — router-tool house rule (see module
            # docstring). The router fixes the alias on its next turn.
            return (
                f"العنصر {target_wi} غير موجود في هذه المحادثة. "
                f"استخدم رمزاً من الملخصات (WI-1, WI-2, ...) وأعد استدعاء الأداة."
            )

        # Lazy import: keeps this module import-light (the editor package pulls
        # the model registry + tracking) and lets tests monkeypatch
        # ``agents.artifact_editor.run_artifact_editor``.
        from agents.artifact_editor import run_artifact_editor

        result = await run_artifact_editor(
            ctx.deps.supabase,
            item_id=item_id,
            user_message=(getattr(ctx.deps, "user_message", "") or ""),
            task=task,
        )

        if result.status == "edited":
            # Sink drained by run_router → _route → SSE: refresh the panel.
            ctx.deps.pending_sse_events.append(
                {"type": "workspace_item_updated", "item_id": item_id}
            )
            summary = result.change_summary
            if result.assumptions:
                summary = f"{summary}\n(افتراضات المحرّر: {result.assumptions})"
            # Prefix with the alias so the router can attribute multi-artifact
            # edits when briefing the user.
            return f"[{target_wi}] {summary}"

        # no_change / failed: return the explanation plainly — the router
        # briefs the user; retrying would not change the outcome.
        return result.change_summary


__all__ = [
    "register_edit_artifact",
    "HasEditorContext",
]
