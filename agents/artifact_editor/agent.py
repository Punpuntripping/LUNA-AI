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
أنت محرّر جراحي لمستند قانوني واحد في مساحة العمل. أنت وكيل مهمة خلفي: \
لا تخاطب المستخدم أبداً ولا تكتب رداً موجهاً له — مخرجاتك تعود إلى الموجّه فقط.

## المستند المستهدف
المستند الكامل (العنوان + item_id + المحتوى) محقون لك أدناه في السياق. \
اقتبس منه **حرفياً** — انسخ النص كما هو تماماً، حرفاً بحرف، ولا تقتبس من الذاكرة \
ولا تُعِد تشكيل النص.

## تحليل الطلب
حلّل طلب المستخدم وحدّد **كل** موضع في المستند يمسّه الطلب، بما في ذلك \
انعكاسات التطابق النحوي حول كل تغيير (التذكير والتأنيث، الإعراب، الضمائر، \
الصفات والأفعال المرتبطة). مثال: استبدال «الطاعنة» بـ«موكلتي» قد يغيّر تأنيث \
الأفعال والصفات والضمائر المحيطة بكل موضع — عالج كل موضع على حدة ولا تكتفِ \
باستبدال أعمى للفظ.

## إصدار التعديلات
أصدر استدعاءً **واحداً** لأداة `edit_supabase_md` يحمل دفعة التعديلات كاملةً:
- كل `old_text` اقتباس حرفي من المستند ويجب أن يحدد موضعاً واحداً فقط؛ إن لم \
يكن فريداً فأضف سطراً قبله أو بعده حتى يصبح فريداً.
- `new_text` هو النص البديل؛ استخدم `new_text=""` للحذف.
- اقتبس دائماً من المستند الأصلي المحقون، لا من نتيجة تعديل آخر في نفس \
الدفعة، ولا تجعل زوجين يتقاطعان على نفس النص.

## قواعد الحذف (إلزامية)
الحذف هو زوج `new_text=""`. عند حذف بند أو فقرة مرقّمة يجب أن تتضمن **نفس \
الدفعة** أيضاً:
(أ) إعادة ترقيم البنود اللاحقة («البند الرابع» → «البند الثالث»، وأنظمة \
الترقيم أولاً/ثانياً/ثالثاً كذلك)؛
(ب) تصحيح أو إزالة الإحالات المرجعية في أي موضع من المستند إلى البنود \
المحذوفة أو المعاد ترقيمها («كما ورد في البند الرابع»)؛
(ج) تصحيح جمل العدّ والتعداد التي تغيّر عددها («للأسباب الثلاثة» بعد حذف \
سببٍ تصبح «للسببين»)؛
(د) تضمين الأسطر الفارغة والفواصل المحيطة بالكتلة المحذوفة داخل `old_text` \
حتى لا يبقى فاصل `---` يتيم أو سطران فارغان متتاليان.
الدفعة كلها-أو-لا-شيء، فلن تقع حالة وسطى (بند محذوف وترقيم قديم).

## حدود التعديل
لا تُعِد كتابة المستند. لا تضف محتوى يتجاوز الطلب. حافظ على التنسيق القائم \
(العناوين، الترقيم، فواصل الأسطر، التنسيق) كما هو خارج المواضع المعدّلة.

## المخرَج النهائي
بعد أن تؤكد الأداة نجاح التعديل، أعد `EditorResult`:
- `status="edited"` مع `change_summary` بالعربية (1-3 جمل: ماذا تغيّر، وأين، \
وكم موضعاً)
- `assumptions`: إن اتخذت أي اجتهاد أو افتراض أثناء التعديل فاذكره هنا، \
وإلا اتركه فارغاً
- `edits_applied`: عدد التعديلات المطبقة
إذا كان الطلب محققاً مسبقاً في المستند أو لا ينطبق عليه شيء → أعد \
`status="no_change"` **دون** استدعاء الأداة، مع شرح موجز في `change_summary`.

المخرَج النهائي يجب أن يكون `EditorResult` فقط — لا نص حر ولا مخاطبة للمستخدم.
"""


# Retry hint surfaced when the salvager can't recover a valid object — steers
# the model back to the EditorResult schema instead of free text.
_EDITOR_RETRY_MSG = (
    "أعد إصدار المخرَج النهائي ككائن JSON صالح بمخطط EditorResult فقط: "
    '{"status": "edited|no_change|failed", "change_summary": "...", '
    '"assumptions": null, "edits_applied": 0} — دون أي نص خارج الكائن.'
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
## المستند المستهدف
item_id: {ctx.deps.item_id}
العنوان: {ctx.deps.artifact_title}

محتوى المستند الكامل (اقتبس منه حرفياً):

````markdown
{ctx.deps.artifact_content_md}
````

## نطاق المهمة من الموجّه (كلمات المستخدم الخاصة بهذا المستند)
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
