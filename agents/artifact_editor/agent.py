"""artifact_editor — Layer-3 surgical editor for one workspace artifact.

Design + rationale: ``.claude/plans/artifact_editor.md``.

The router stays cheap (its context is summaries); when the user asks for a
clearly-scoped surgical edit (substitute a term, delete a clause, fix a
name/number/date) the router's ``edit_artifact`` tool resolves the WI alias and
calls :func:`run_artifact_editor`. The runner fetches the artifact fresh,
injects its FULL ``content_md`` into this agent's prompt deterministically
(unfold philosophy — no read tools), and the agent emits ONE batched
``edit_supabase_md`` call covering every location the request touches —
including the Arabic grammatical-agreement ripples a find-replace would miss.
The batch is all-or-nothing (one guarded write, ``prev_content_md`` snapshot),
so the document can never land in a half-edited state.

Layer 3 task agent: never talks to the user, no streaming of its own. The
final output is an :class:`EditorResult` whose Arabic ``change_summary`` is
what the router uses to brief the user.

Model: slot ``"artifact_editor"`` (deepseek-v4-flash, reasoning=medium). The
deepseek-flash structured-output-as-text trap is covered by the shared
``make_json_salvager`` TextOutput member (``project_structured_output_salvage``).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.tool_repository.edit_supabase_md import register_edit_supabase_md
from agents.utils.agent_models import get_agent_model
from agents.utils.structured_output import make_json_salvager
from agents.utils.tracking import run_tracked

logger = logging.getLogger(__name__)


# Kinds the editor may touch. ``agent_search`` reports are regenerable — an
# edit request against one is almost certainly a misroute, so the runner
# refuses WITHOUT spending an LLM call. ``convo_context`` / ``attachment`` are
# system-side artifacts and equally off-limits.
ALLOWED_KINDS = frozenset({"agent_writing", "note"})


# ── Dependencies ──────────────────────────────────────────────────────────────


@dataclass
class EditorDeps:
    """Per-run dependencies for one editor instance (one artifact each).

    Structurally satisfies ``HasSupabase`` (the ``edit_supabase_md`` deps
    contract) via the ``supabase`` attribute. The artifact content is fetched
    fresh by the runner and injected whole — the agent has no read tools.
    """

    supabase: object  # supabase.Client — kept loose to avoid a hard import here
    item_id: str
    artifact_title: str
    artifact_content_md: str
    # The user's raw message, verbatim (no-paraphrase rule — like save_memo).
    user_message: str
    # The router's scoped quote: the user's words for the part of the request
    # that applies to THIS artifact (needed to split a multi-artifact request).
    task: str


# ── Output model ──────────────────────────────────────────────────────────────


class EditorResult(BaseModel):
    """What the editor returns to the router's tool loop."""

    status: Literal["edited", "no_change", "failed"]
    # Arabic, 1–3 sentences: what changed, where, how many locations.
    change_summary: str
    # Any judgment call the editor made (Arabic), surfaced to the router.
    assumptions: str | None = None
    edits_applied: int = 0


# ── Usage limits ──────────────────────────────────────────────────────────────

# One edit_supabase_md call (+ a possible retry round) and the final output.
# output_tokens generous because batch new_text can be sizable on renumbering
# deletions.
EDITOR_LIMITS = UsageLimits(
    request_limit=4,
    tool_calls_limit=5,
    output_tokens_limit=8000,
)


# ── System prompt (Arabic) ────────────────────────────────────────────────────


SYSTEM_PROMPT = """\
You are a surgical editor for a single legal document in the workspace. You are \
a back-office task agent: never address the user and never write a reply aimed \
at them — your output goes back to the router only.

## Output language — strict rule
Write `change_summary` and `assumptions` in Arabic. These instructions are in \
English, but the document you edit is Arabic and the result fields you return \
are Arabic.

## The target document
The full document (title + item_id + content) is injected for you below in the \
context. Quote from it **verbatim** — copy the text exactly as it is, letter by \
letter; do not quote from memory and do not re-vowel/re-shape the text.

## Analyzing the request
Analyze the user's request and identify **every** location in the document the \
request touches, including the grammatical-agreement ripples around each change \
(masculine/feminine, case-inflection, pronouns, the associated adjectives and \
verbs). Example: replacing «الطاعنة» with «موكلتي» may change the gender of the \
verbs, adjectives, and pronouns surrounding each location — handle each \
location individually and do not settle for a blind find-replace of the word.

## Issuing the edits
Issue a **single** call to the `edit_supabase_md` tool carrying the complete \
batch of edits:
- Each `old_text` is a verbatim quote from the document and must pinpoint a \
single location only; if it is not unique, add a line before or after it until \
it becomes unique.
- `new_text` is the replacement text; use `new_text=""` for deletion.
- Always quote from the original injected document, not from the result of \
another edit in the same batch, and do not let two pairs overlap on the same \
text.

## Deletion rules (mandatory)
A deletion is a `new_text=""` pair. When deleting a numbered clause or \
paragraph, the **same batch** must also include:
(a) Renumbering the subsequent clauses («البند الرابع» → «البند الثالث», and \
the أولاً/ثانياً/ثالثاً numbering systems too);
(b) Correcting or removing the cross-references anywhere in the document to the \
deleted or renumbered clauses («كما ورد في البند الرابع»);
(c) Correcting the counting/enumeration sentences whose count changes («للأسباب \
الثلاثة» after deleting a reason becomes «للسببين»);
(d) Including the blank lines and separators surrounding the deleted block \
inside `old_text` so no orphaned `---` separator or two consecutive blank lines \
remain.
The batch is all-or-nothing, so no intermediate state occurs (a deleted clause \
with old numbering).

## Edit limits
Do not rewrite the document. Do not add content beyond the request. Preserve \
the existing formatting (the headings, the numbering, the line breaks, the \
formatting) as-is outside the edited locations.

## The final output
After the tool confirms the edit succeeded, return `EditorResult`:
- `status="edited"` with `change_summary` in Arabic (1-3 sentences: what \
changed, where, and how many locations)
- `assumptions`: if you made any judgment call or assumption during the edit, \
state it here (in Arabic); otherwise leave it empty
- `edits_applied`: the number of edits applied
If the request is already satisfied in the document or nothing applies → return \
`status="no_change"` **without** calling the tool, with a brief explanation in \
`change_summary`.

The final output must be `EditorResult` only — no free text and no addressing \
of the user.
"""


# Retry hint surfaced when the salvager can't recover a valid object — steers
# the model back to the EditorResult schema instead of free text.
_EDITOR_RETRY_MSG = (
    "Re-issue the final output as a valid JSON object with the EditorResult "
    "schema only: "
    '{"status": "edited|no_change|failed", "change_summary": "...", '
    '"assumptions": null, "edits_applied": 0} — with no text outside the object.'
)

# Exposed at module level so tests can exercise the salvage path directly.
_salvage_editor_result = make_json_salvager(EditorResult, retry_msg=_EDITOR_RETRY_MSG)


# ── Agent definition ──────────────────────────────────────────────────────────

editor_agent = Agent(
    get_agent_model("artifact_editor"),
    name="artifact_editor",
    deps_type=EditorDeps,
    # TextOutput salvager: deepseek-flash sometimes dumps the structured output
    # as plain text after a thinking pass (project_structured_output_salvage).
    output_type=[EditorResult, TextOutput(_salvage_editor_result)],
    instructions=SYSTEM_PROMPT,
    retries=2,
    output_retries=3,
)

# The only tool: anchored batch replace with uniqueness check + optimistic
# concurrency + prev_content_md snapshot. Deps contract: ``.supabase``.
register_edit_supabase_md(editor_agent)


# ── Dynamic instructions ──────────────────────────────────────────────────────
# Prefix discipline: the static system prompt above is registered first and
# never varies; per-run content (artifact + task) rides AFTER it so the prompt
# prefix stays cache-friendly across runs.


@editor_agent.instructions
def inject_artifact(ctx: RunContext[EditorDeps]) -> str:
    """Inject the target artifact whole + the router's scoped task line."""
    return f"""
## The target document
item_id: {ctx.deps.item_id}
Title: {ctx.deps.artifact_title}

The full document content (quote from it verbatim):

````markdown
{ctx.deps.artifact_content_md}
````

## Task scope from the router (the user's own words for this document)
{ctx.deps.task}
"""


# ── Runner ────────────────────────────────────────────────────────────────────


def _fetch_item(supabase, item_id: str) -> dict | None:
    """Fetch the artifact row (content_md, title, kind) by item_id.

    Returns ``None`` when the row is missing/deleted. Exceptions propagate to
    the caller's defensive wrapper.
    """
    res = (
        supabase.table("workspace_items")
        .select("content_md, title, kind")
        .eq("item_id", item_id)
        .is_("deleted_at", "null")
        .maybe_single()
        .execute()
    )
    return getattr(res, "data", None) if res is not None else None


async def run_artifact_editor(
    supabase,
    *,
    item_id: str,
    user_message: str,
    task: str,
) -> EditorResult:
    """Run one surgical-edit pass over a single workspace artifact.

    Fetches the artifact fresh, refuses non-editable kinds WITHOUT an LLM
    call, then runs the editor agent via ``run_tracked`` (slot
    ``"artifact_editor"``, agent_family ``"editing"`` — the llm_calls ledger
    row lands automatically inside the turn's capture scope).

    Never raises — every failure path returns ``status="failed"`` with an
    Arabic reason so the router's tool loop briefs the user instead of
    retrying forever.
    """
    # ── Fetch + guards (deterministic, pre-LLM) ──────────────────────────────
    try:
        row = _fetch_item(supabase, item_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("artifact_editor: fetch failed for %s: %s", item_id, exc)
        row = None

    if not row:
        return EditorResult(
            status="failed",
            change_summary=(
                "العنصر غير موجود في مساحة العمل أو تم حذفه — لم يتغيّر شيء."
            ),
        )

    kind = (row.get("kind") or "").strip()
    if kind not in ALLOWED_KINDS:
        if kind == "agent_search":
            reason = (
                "هذا العنصر تقرير بحث (agent_search) يُعاد توليده بالبحث ولا "
                "يُحرَّر جراحياً — الأرجح أن الطلب يخص مستنداً مكتوباً آخر أو "
                "يحتاج إعادة بحث. لم يتغيّر شيء."
            )
        else:
            reason = (
                f"نوع العنصر ({kind or 'غير معروف'}) لا يدعم التحرير الجراحي — "
                "يُسمح فقط بالمستندات المكتوبة والملاحظات. لم يتغيّر شيء."
            )
        return EditorResult(status="failed", change_summary=reason)

    deps = EditorDeps(
        supabase=supabase,
        item_id=item_id,
        artifact_title=(row.get("title") or "").strip(),
        artifact_content_md=row.get("content_md") or "",
        user_message=user_message,
        task=task,
    )

    # ── LLM run (defensive: never raise into the router's tool loop) ─────────
    try:
        result = await run_tracked(
            editor_agent,
            user_message,  # raw verbatim message; artifact + task ride in instructions
            deps=deps,
            stage="artifact_editor.run",
            slot="artifact_editor",
            agent_family="editing",
            usage_limits=EDITOR_LIMITS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "artifact_editor: run failed for item %s: %s", item_id, exc, exc_info=True
        )
        return EditorResult(
            status="failed",
            change_summary=(
                "فشل التعديل بسبب خطأ تقني أثناء تنفيذ المحرّر — لم يتغيّر "
                "المستند. يمكن إعادة المحاولة."
            ),
        )

    output = getattr(result, "output", None)
    if not isinstance(output, EditorResult):
        # output_type guarantees EditorResult; belt-and-suspenders only.
        logger.warning(
            "artifact_editor: unexpected output type %s for item %s",
            type(output).__name__, item_id,
        )
        return EditorResult(
            status="failed",
            change_summary="فشل التعديل: مخرَج المحرّر غير صالح — لم يتغيّر المستند.",
        )
    return output


__all__ = [
    "EditorDeps",
    "EditorResult",
    "EDITOR_LIMITS",
    "ALLOWED_KINDS",
    "editor_agent",
    "run_artifact_editor",
]
