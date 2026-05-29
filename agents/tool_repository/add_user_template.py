"""add_user_template — save a reusable markdown template to the user's library.

Inserts a new row into the ``user_templates`` table (migration 055) scoped to
the CURRENT user. This is the personal "قوالبي" library — distinct from the
system-wide ``system_templates`` corpus that ``search_templates`` reads.

Why a dedicated tool (rather than letting the agent free-write the table):
  - The insert is scoped to ``ctx.deps.user_id`` so the row is owned by the
    right user — the agent never supplies the user_id itself.
  - ``created_by`` is pinned to ``'agent'`` so the provenance of agent-saved
    templates is auditable and distinguishable from user-authored ones.
  - Failures surface as ``ModelRetry`` so the model self-corrects (e.g. retries
    with a non-empty title) rather than silently dropping the save.

Registration::

    from agents.tool_repository.add_user_template import register_add_user_template
    register_add_user_template(agent)   # agent.deps must expose `.supabase` + `.user_id`

The deps object is typed structurally via :class:`HasUserContext` so this module
stays decoupled from any per-agent deps dataclass.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic_ai import Agent, ModelRetry, RunContext


# --- Schema config: kept here so a table/column rename is a one-line change. ---
TABLE = "user_templates"
USER_COL = "user_id"
TITLE_COL = "title"
CONTENT_COL = "content_md"
CREATED_BY_COL = "created_by"
# All agent-saved templates carry this provenance marker so they are
# distinguishable from user-authored rows (created_by='user').
CREATED_BY_AGENT = "agent"


@runtime_checkable
class HasUserContext(Protocol):
    """Structural type for the agent deps — we need a supabase client + user_id.

    Any concrete deps object (e.g. ``WriterPlannerDeps``) satisfies this as long
    as it has a ``.supabase`` attribute and a ``.user_id`` string, so this module
    stays decoupled from the per-agent deps classes.
    """

    supabase: object  # supabase.Client — kept loose to avoid a hard import here
    user_id: str


# --------------------------------------------------------------------------- #
# Pydantic AI tool.
# --------------------------------------------------------------------------- #
# NOTE: the insert uses the synchronous supabase client (matching the rest of
# the agents/ codebase). The client is service-role and BYPASSES RLS, so the
# user_id scoping below is the only thing keeping the row owned by the right
# user — never let the model supply it.


def register_add_user_template(agent: Agent) -> None:
    """Register the ``add_user_template`` tool on a Pydantic AI agent.

    The agent's deps must structurally satisfy :class:`HasUserContext` (i.e.
    expose a ``.supabase`` client and a ``.user_id`` string).
    """

    @agent.tool
    async def add_user_template(  # noqa: RUF029 — supabase client is sync by design
        ctx: RunContext[HasUserContext],
        title: str,
        content_md: str,
    ) -> str:
        """Save a reusable markdown template to the user's personal "قوالبي" library.

        Use this ONLY when the user EXPLICITLY asks to save something as a
        template (e.g. «احفظ هذا كقالب» / «أضِفه إلى قوالبي»). Do NOT call it
        proactively — drafting a document is not the same as saving a template.

        The template is stored under the CURRENT user's account so it can be
        reused in future turns. You supply only the title and the markdown body;
        ownership (user_id) and provenance (created_by='agent') are set for you.

        Args:
            title: A short Arabic title for the template (e.g. «قالب عقد إيجار»).
                Must be non-empty.
            content_md: The template body as markdown. May contain placeholders
                the user fills in later (e.g. «[اسم الطرف الأول]»).

        Returns:
            A short Arabic confirmation including the new template_id.

        Raises:
            ModelRetry: when the title is empty, or the insert returns no row /
                fails — fix the input and retry.
        """
        if not title.strip():
            raise ModelRetry(
                "عنوان القالب فارغ — أرسل عنواناً قصيراً وواضحاً للقالب ثم أعد المحاولة."
            )

        supabase = ctx.deps.supabase
        try:
            res = (
                supabase.table(TABLE)
                .insert(
                    {
                        USER_COL: ctx.deps.user_id,
                        TITLE_COL: title,
                        CONTENT_COL: content_md,
                        CREATED_BY_COL: CREATED_BY_AGENT,
                    }
                )
                .execute()
            )
        except Exception as exc:  # noqa: BLE001 — surface any DB error as a retry hint
            raise ModelRetry(
                "تعذّر حفظ القالب بسبب خطأ في قاعدة البيانات. تأكد من أن العنوان "
                "والمحتوى صالحان ثم أعد المحاولة."
            ) from exc

        if not res.data:
            raise ModelRetry(
                "لم يُحفظ القالب (لم تُرجع قاعدة البيانات أي صف). أعد المحاولة "
                "بعنوان ومحتوى صالحين."
            )

        template_id = res.data[0].get("template_id", "?")
        return f"تم حفظ القالب «{title}» في مكتبة قوالبك (template_id={template_id})."


__all__ = [
    "register_add_user_template",
    "HasUserContext",
    "TABLE",
    "USER_COL",
    "TITLE_COL",
    "CONTENT_COL",
    "CREATED_BY_COL",
    "CREATED_BY_AGENT",
]
