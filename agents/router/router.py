"""Router agent — conversational front-end for Rayhan Legal AI.

Classifies user intent and either responds directly (ChatResponse) or
dispatches a specialist agent (DispatchAgent) with a content-derived
task_label plus the workspace items the specialist should see as input.
The router no longer paraphrases the query — the specialist receives the
raw user message (orchestrator-filled MajorAgentInput.describe_query) and
recent_messages for context.

Wave 9 changes:
- ``OpenTask`` → ``DispatchAgent`` (renamed fields ``task_type`` →
  ``agent_family``, ``artifact_id`` → ``target_item_id``, plus
  ``attached_item_ids`` capped at ``MAX_ATTACHED_ITEMS``).
- ``output_type`` uses Pydantic AI list syntax for per-member output tools.
- ``read_workspace_item`` tool exposes full ``content_md`` on demand.
- Eager context (workspace items summaries + compaction summary + filtered
  messages) is assembled by ``agents.router.context.load_router_context``
  and rendered as dynamic instructions on the agent.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from pydantic_ai import Agent, ModelRetry, RunContext, TextOutput
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits
from supabase import Client as SupabaseClient

from agents.models import ChatResponse, DispatchAgent, MAX_ATTACHED_ITEMS
from agents.utils.agent_models import get_agent_model
from agents.utils.tracking import track_stage
from shared.observability import get_logfire


# ── Alias resolution (migration 052 / agent communication protocol) ───────────
# The router LLM emits ``WI-{seq}`` aliases (e.g. ``"WI-3"``) instead of raw
# UUIDs. The output validator resolves them against ``RouterDeps.wi_alias_map``
# and fills the orchestrator-facing ``target_item_id`` / ``attached_item_ids``
# fields. The read_workspace_item tool accepts either form for robustness.

_WI_ALIAS_RE = re.compile(r"^WI-(\d+)$", re.IGNORECASE)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _resolve_wi_alias(alias: str, alias_map: dict[int, str]) -> str | None:
    """Resolve ``"WI-{seq}"`` → workspace_items.item_id UUID.

    Returns the UUID on success, ``None`` if the alias is malformed or its
    seq is not in the conversation's alias map. Accepts a raw UUID
    verbatim (defence-in-depth — older orchestrator paths may still pass
    UUIDs directly).
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
    # Verbatim UUID — accept for backward compat.
    if _UUID_RE.match(s):
        return s
    return None


# ── Plain-text fallback ───────────────────────────────────────────────────────
# qwen3.6-plus occasionally emits the final chat answer as plain text after a
# long thinking pass, instead of wrapping it in `ChatResponse(message=...)`.
# Without a fallback, Pydantic AI raises ModelRetry → a full extra LLM round-trip
# costing ~$0.04 per turn (observed twice in convo_1: T05 round 2, T07 round 1).
# Registering TextOutput tells Pydantic AI to accept plain text as a valid
# output, wrapped via `_text_as_chat`. DispatchAgent still requires an explicit
# tool call — only chat responses get this fallback because the failure mode is
# specific to text-shaped answers.

def _text_as_chat(text: str) -> ChatResponse:
    text = (text or "").strip()
    if len(text) < 20:
        # Defensive: model emitted a fragment, not a real answer. Force a
        # retry so we don't ship garbage downstream.
        raise ModelRetry(
            "الرد قصير جداً أو فارغ. أصدر إجابة كاملة عبر استدعاء ChatResponse "
            "أو أعد كتابة النص الكامل."
        )
    return ChatResponse(message=text)

logger = logging.getLogger(__name__)
_logfire = get_logfire()


# ── Dependencies ──────────────────────────────────────────────────────────────


@dataclass
class RouterDeps:
    """Dependencies injected into the router agent by the orchestrator."""

    supabase: SupabaseClient
    user_id: str
    conversation_id: str
    case_id: str | None
    case_memory_md: str | None
    case_metadata: dict | None
    user_preferences: dict | None
    # Eager context assembled by the loader before .run() — rendered into
    # dynamic instructions. Lists hold compact (item_id, wi_seq, kind, title,
    # summary) dicts; full content_md is fetched on demand via
    # read_workspace_item.
    workspace_item_summaries: list[dict] = field(default_factory=list)
    compaction_summary_md: str | None = None
    # Migration 052: ``WI-{seq}`` alias → item_id UUID lookup. Built by
    # ``run_router`` from ``workspace_item_summaries`` so the output
    # validator can resolve LLM-emitted aliases without re-querying.
    wi_alias_map: dict[int, str] = field(default_factory=dict)


# ── Usage limits ──────────────────────────────────────────────────────────────


ROUTER_LIMITS = UsageLimits(
    output_tokens_limit=6000,
    request_limit=5,
    tool_calls_limit=8,
)


# ── System prompt ─────────────────────────────────────────────────────────────


SYSTEM_PROMPT = """\
أنت ريحان، المساعد القانوني الذكي للمحامين السعوديين.

## قاعدة المخرَجات (إلزامية)

كل ردّ يجب أن يكون **استدعاءَ أداةِ مخرَج** واحدةً فقط: إمّا `ChatResponse` للرد المباشر، أو `DispatchAgent` للتوجيه. **لا تكتب نصاً عادياً مطلقاً**؛ لا اعتذار، لا توضيح، لا سؤال — ولو كان السؤال موجَّهاً للمستخدم — خارج حقل `ChatResponse.message`. إن أردت طرح سؤال توضيحي، ضع نصّه داخل `ChatResponse(message=...)`. إن وصلتك رسالة إعادة محاولة من النظام بسبب فشل سابق، **لا تعتذر بنص حرّ**؛ كرّر المحاولة بإصدار `ChatResponse` أو `DispatchAgent` صالح.

أنت الواجهة الرئيسية للمحادثة — كل رسالة من المستخدم تمر من خلالك.

لديك ثلاث وظائف:
1. الإجابة المباشرة — التحيات، التوضيحات، الأسئلة القانونية البسيطة، الأسئلة عن تقارير ومستندات سابقة
2. توجيه المهام إلى متخصص (DispatchAgent) — عندما يحتاج المستخدم بحثاً قانونياً معمقاً أو صياغة مستند أو معالجة ملف
3. الحفاظ على تواصل المحادثة — تستفيد من ملخصات عناصر مساحة العمل وملخص ضغط المحادثة المحقونين في السياق

## القرارات قبل كل رد (أربع فحوصات):
1. **الضرورة** — هل تحتاج هذه الرسالة فعلاً متخصصاً؟ إن أمكن الرد المباشر فردّ مباشرة.
2. **النطاق** — هل الطلب ضمن المجال القانوني السعودي؟ إن لم يكن فاعتذر بأدب عبر ChatResponse.
3. **الغموض** — إن كانت الرسالة غامضة، اطرح سؤالاً توضيحياً واحداً عبر ChatResponse قبل التوجيه.
4. **اختيار العناصر المرفقة** — حدد attached_wis بناءً على ملخصات العناصر المتاحة في مساحة العمل.

## متى تجيب مباشرة (ChatResponse):
- التحيات والمجاملات
- الأسئلة البسيطة التي يمكنك الإجابة عنها بثقة عالية
- أسئلة التوضيح — عندما تحتاج مزيداً من المعلومات من المستخدم
- أسئلة عن ريحان ووظائفه
- أسئلة عن محتوى تقرير أو مستند سابق — استخدم أداة read_workspace_item لقراءة المحتوى والإجابة مباشرة
- الرسائل الغامضة — اسأل المستخدم قبل التوجيه

## متى توجّه إلى deep_search (DispatchAgent):
- أسئلة قانونية تحتاج بحثاً في الأنظمة أو الأحكام أو السوابق
- طلبات تحليل أو مقارنة أو شرح تفصيلي لمفاهيم قانونية
- كلمات مفتاحية: "ابحث"، "حلل"، "قارن"، "اشرح بالتفصيل"
- أسئلة عن حقوق أو التزامات أو عقوبات أو إجراءات بموجب أنظمة محددة
- القاعدة: إذا كانت الإجابة تحتاج استشهاداً → وجّه مهمة deep_search

## متى توجّه إلى writing:
- طلب صريح لصياغة أو إعداد أو كتابة مستند قانوني طويل، حيث يحتاج المستخدم مسوّدة قابلة للتحرير في مساحة العمل
- كلمات مفتاحية: "اكتب"، "صِغ"، "حضّر"، "أعدّ"، "مسوّدة"، "صياغة"
- يجب اختيار قيمة subtype واحدة من ست قيم بناءً على طلب المستخدم:
  * "contract" — عند طلب عقد (عقد عمل، إيجار، بيع، شراكة، خدمات…)
  * "memo" — عند طلب مذكرة قانونية أو مذكرة شارحة
  * "legal_opinion" — عند طلب رأي قانوني أو فتوى قانونية
  * "defense_brief" — عند طلب لائحة دفاع أو لائحة جوابية أمام محكمة
  * "letter" — عند طلب خطاب رسمي (إنذار، مطالبة، إشعار، خطاب موجَّه لجهة)
  * "summary" — عند طلب ملخّص لمستند مرفق أو لمحتوى محادثة
- إذا أشار المستخدم لمستند موجود في مساحة العمل ("حدّث المذكرة السابقة"، "عدّل العقد") — حدّد رمز العنصر المقصود (مثل «WI-3») من ملخصات العناصر، ومرّره عبر `target_wi` لفتح مهمة writing تحرير
- إذا كان المستخدم يبحث عن معلومات قانونية لتدعيم الصياغة — وجّه deep_search أولاً، ثم writing لاحقاً

## متى توجّه إلى memory (هيكل أولي — قيد التطوير):
- طلب صريح لحفظ معلومة أو واقعة في ذاكرة القضية
- طلب استرجاع أو تحديث ذاكرة سابقة مرتبطة بالقضية الحالية
- كلمات مفتاحية: "احفظ"، "تذكّر"، "أضف لذاكرة القضية"، "حدّث الذاكرة"
- ملاحظة: هذا المسار ما زال هيكلاً أولياً؛ استخدمه فقط للطلبات الصريحة المتعلقة بإدارة الذاكرة، لا للأسئلة العامة.

## اختيار attached_wis:
- ملخصات عناصر مساحة العمل تُحقن لك في السياق برموز قصيرة (WI-1, WI-2, ...). كل عنصر يحمل: الرمز، النوع (kind)، العنوان، الملخص.
- اختر العناصر الأكثر صلة بالطلب الحالي فقط، واذكرها بأرقامها («WI-3»، «WI-7») في `attached_wis`.
- الحد الأقصى الصارم: {MAX_ATTACHED_ITEMS} عناصر لكل توجيه. إن وجدت أكثر، اختر الأهم.
- إن لم تكفك الملخصات، استدعِ `read_workspace_item` بالرمز (مثل «WI-3») للحصول على المحتوى الكامل (يمكن استدعاؤها على عدة عناصر بالتوازي).
- إن لم يوجد عنصر مناسب، اترك `attached_wis` قائمة فارغة.
- **لا تكتب معرّفات UUID مطلقاً** — استخدم رموز WI-N الموجودة في السياق فقط، ولا تخترع رموزاً جديدة.

## قواعد التعامل مع العناصر السابقة (workspace items):
- سؤال عن محتوى العنصر (قراءة) → استخدم `read_workspace_item("WI-N")` وأجب مباشرة عبر ChatResponse
- طلب تعديل أو تحرير العنصر → وجّه DispatchAgent مع `target_wi="WI-N"`
- عندما يشير المستخدم لعنصر دون تحديد → اذكر العناصر المتاحة (برموزها وعناوينها من الملخصات) واسأل أيها يقصد

## قواعد task_label:
- عبارة عربية قصيرة (30-60 حرفاً) **مشتقة من محتوى السؤال** لا من سير العمل.
- وصف **الموضوع**، لا الفعل: «بحث عن قوانين التحرش بالسعودية» لا «أبحث عن…».
- ممنوع استخدام أفعال مثل: «أبحث»، «أكتب»، «أحلل»، «أصيغ»، «أعدّ».
- يجب أن يكون مستقراً عبر إعادات الصياغة — نفس السؤال يُنتج نفس العنوان.
- يُستخدم كعنوان لبطاقة العنصر في مساحة العمل وكمعرّف في سجل المهام.

## وصف السؤال — ليس من مهامك:
- **لا تَصِفْ السؤال ولا تُعِد صياغته**. المتخصص يستلم رسالة المستخدم الأصلية وسياق المحادثة مباشرةً.
- مهمتك التوجيه فقط: اختيار `agent_family` و`task_label` والعناصر المرفقة.
- لا توجّه إذا كنت غير متأكد مما يريده المستخدم — اسأله أولاً عبر ChatResponse.

## قواعد عامة:
- كن منحازاً نحو التوجيه بدلاً من إعطاء إجابات قانونية بدون مصادر
- إذا كنت غير متأكد → اسأل المستخدم
- أجب بالعربية إلا إذا كتب المستخدم بالإنجليزية
- لا تذكر كلمة "مهمة" أو "task" أو تفاصيل تقنية — المستخدم لا يعرف عن نظام التوجيه
""".replace("{MAX_ATTACHED_ITEMS}", str(MAX_ATTACHED_ITEMS))


# ── Agent definition ──────────────────────────────────────────────────────────

# Pydantic AI list-syntax for output_type: each member becomes its own output
# tool internally, giving the model a stronger selection signal than the
# `ChatResponse | DispatchAgent` union form.
router_agent = Agent(
    get_agent_model("router"),
    name="router_agent",
    # TextOutput accepts a raw text response as a ChatResponse fallback —
    # see _text_as_chat above for rationale. DispatchAgent stays strict
    # (no text-shaped equivalent) so routing decisions remain structured.
    output_type=[ChatResponse, DispatchAgent, TextOutput(_text_as_chat)],
    deps_type=RouterDeps,
    instructions=SYSTEM_PROMPT,
    retries=2,
    output_retries=4,
    end_strategy="early",
)


# ── Output validator (belt-and-suspenders for attached_item_ids cap) ──────────


@router_agent.output_validator
def _validate_and_resolve_dispatch(
    ctx: RunContext[RouterDeps],
    value: ChatResponse | DispatchAgent,
) -> ChatResponse | DispatchAgent:
    """Validate the dispatch output AND resolve WI-{seq} aliases → UUIDs.

    Migration 052 / agent communication protocol:

    * Resolves ``target_wi`` (e.g. ``"WI-3"``) → ``target_item_id`` (UUID).
      Raises :class:`ModelRetry` if the alias is malformed or not present in
      the conversation's alias map.
    * Resolves each ``attached_wis`` entry → an UUID into
      ``attached_item_ids``. Same error contract on malformed/unknown aliases.
    * Enforces the cap on ``attached_wis`` (the LLM-emitted field) and the
      non-empty ``task_label`` invariant.

    Defence-in-depth: if the LLM mistakenly fills the UUID fields directly
    (legacy schema bleed-through), the aliases-derived values overwrite
    them so the orchestrator always sees the canonical resolved UUIDs.
    """
    if not isinstance(value, DispatchAgent):
        return value

    if not (value.task_label or "").strip():
        raise ModelRetry(
            "task_label فارغ. أصدر عبارة عربية قصيرة (30-60 حرفاً) "
            "مشتقة من محتوى السؤال — وصف الموضوع لا الفعل."
        )

    if len(value.attached_wis) > MAX_ATTACHED_ITEMS:
        raise ModelRetry(
            f"اخترت {len(value.attached_wis)} عناصر، والحد الأقصى "
            f"{MAX_ATTACHED_ITEMS}. أعد الاختيار وأبقِ على الأكثر صلة فقط."
        )

    alias_map = ctx.deps.wi_alias_map or {}

    # Resolve target_wi → target_item_id.
    # Some LLM outputs serialize the absence of a target as the literal
    # strings "None" / "null" / "" instead of an actual JSON null. Coerce
    # those sentinels to Python None BEFORE the truthy check so we don't
    # send the resolver looking for an alias that can never exist (the old
    # behavior burned ~5.5k tokens × 2 retries per dispatch turn before
    # eventually surrendering).
    raw_target = (value.target_wi or "").strip()
    if raw_target and raw_target.lower() not in {"none", "null"}:
        resolved = _resolve_wi_alias(raw_target, alias_map)
        if resolved is None:
            raise ModelRetry(
                f"العنصر {raw_target} غير موجود في هذه المحادثة. "
                f"استخدم رمزاً من الملخصات (WI-1, WI-2, ...)."
            )
        value.target_item_id = resolved
    else:
        value.target_wi = None       # canonicalize the field too
        value.target_item_id = None

    # Resolve each attached_wis → attached_item_ids.
    resolved_attached: list[str] = []
    for alias in value.attached_wis:
        resolved = _resolve_wi_alias(alias, alias_map)
        if resolved is None:
            raise ModelRetry(
                f"العنصر {alias} غير موجود في هذه المحادثة. "
                f"استخدم رموزاً من الملخصات (WI-1, WI-2, ...)."
            )
        resolved_attached.append(resolved)
    value.attached_item_ids = resolved_attached

    return value


# ── Dynamic instructions ──────────────────────────────────────────────────────


@router_agent.instructions
def inject_case_context(ctx: RunContext[RouterDeps]) -> str:
    """Inject case-specific memory and metadata when the conversation is within a lawyer's case."""
    if ctx.deps.case_memory_md:
        return f"""
سياق القضية الحالية:
{ctx.deps.case_memory_md}

استخدم هذا السياق لفهم أسئلة المستخدم وتصنيفها وتوجيهها بدقة.
"""
    return ""


@router_agent.instructions
def inject_user_preferences(ctx: RunContext[RouterDeps]) -> str:
    """Inject user preferences (tone, detail level, language) to guide response style."""
    if ctx.deps.user_preferences:
        prefs = ctx.deps.user_preferences
        parts = []
        if prefs.get("tone"):
            parts.append(f"أسلوب الرد: {prefs['tone']}")
        if prefs.get("detail_level"):
            parts.append(f"مستوى التفصيل: {prefs['detail_level']}")
        if parts:
            return "\nتفضيلات المستخدم:\n" + "\n".join(f"- {p}" for p in parts) + "\n"
    return ""


@router_agent.instructions
def inject_workspace_summaries(ctx: RunContext[RouterDeps]) -> str:
    """Render workspace item summaries with ``WI-{seq}`` aliases.

    Migration 052: each item is rendered as ``WI-{wi_seq}`` instead of as a
    raw UUID. These aliases are the candidate pool for ``attached_wis`` and
    ``target_wi`` and are the **only** form the LLM should emit. The router
    output validator resolves them back to UUIDs after the run.

    Items without a ``wi_seq`` (rare — should never happen for items with a
    conversation_id post-migration 052) are skipped from the alias prompt
    surface to avoid handing the model an unresolvable label.
    """
    items = ctx.deps.workspace_item_summaries or []
    if not items:
        return ""
    lines = [
        "عناصر مساحة العمل المتاحة في هذه المحادثة "
        "(استخدم الرموز التالية فقط في attached_wis و target_wi):"
    ]
    for item in items:
        wi_seq = item.get("wi_seq")
        if wi_seq is None:
            continue
        alias = f"WI-{wi_seq}"
        kind = item.get("kind") or item.get("kind_hint") or "unknown"
        title = item.get("title") or "(بدون عنوان)"
        summary = item.get("summary")
        summary_text = summary if summary else "(لا يوجد ملخص بعد)"
        lines.append(f"- {alias} | kind={kind} | title={title}\n  summary: {summary_text}")
    if len(lines) == 1:
        # All items lacked wi_seq — nothing to render.
        return ""
    lines.append(
        "للاطلاع على المحتوى الكامل لأي عنصر استدعِ `read_workspace_item(\"WI-N\")` "
        "بالرمز نفسه (يمكن استدعاؤها على عدة عناصر بالتوازي). "
        "لا تستخدم معرّفات UUID مطلقاً — استخدم رموز WI-N فقط."
    )
    return "\n" + "\n".join(lines) + "\n"


@router_agent.instructions
def inject_compaction_summary(ctx: RunContext[RouterDeps]) -> str:
    """Inject the latest convo_context compaction summary, when present."""
    md = ctx.deps.compaction_summary_md
    if not md:
        return ""
    return f"\nملخص ضغط المحادثة (ما قبل النافذة الحالية من الرسائل):\n{md}\n"


# ── Tools ─────────────────────────────────────────────────────────────────────


@router_agent.tool
async def read_workspace_item(ctx: RunContext[RouterDeps], wi: str) -> str:
    """Return the full markdown content of a workspace item.

    Use this tool when the per-item ``summary`` provided in context is
    insufficient — for example, answering a direct question about an
    artifact's contents, or picking ``attached_wis`` for a dispatch where
    the summary leaves the item's relevance ambiguous.

    Pass the ``WI-{n}`` alias shown in the workspace summaries (e.g.
    ``"WI-3"``). The tool resolves the alias against the conversation's
    workspace items. The tool can be invoked in parallel for multiple
    items in a single turn — feel free to open several at once.

    Returns the raw ``content_md`` string, or an empty string if the alias
    is unknown / not accessible — in which case silently move on without
    retrying.

    Args:
        wi: The ``WI-{n}`` alias of the workspace item to read. A raw UUID
            is also accepted for backward compatibility but the LLM should
            always emit the alias form.
    """
    item_id = _resolve_wi_alias(wi, ctx.deps.wi_alias_map or {})
    if not item_id:
        logger.info(
            "read_workspace_item: alias %r not resolvable for conversation %s",
            wi, ctx.deps.conversation_id,
        )
        return ""
    try:
        result = (
            ctx.deps.supabase.table("workspace_items")
            .select("content_md")
            .eq("item_id", item_id)
            .eq("user_id", ctx.deps.user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
        if result and getattr(result, "data", None):
            content = result.data.get("content_md") or ""
            logger.info(
                "read_workspace_item: loaded %s (alias %s) for user %s (%d chars)",
                item_id, wi, ctx.deps.user_id, len(content),
            )
            return content
        logger.info(
            "read_workspace_item: %s (alias %s) not found for user %s",
            item_id, wi, ctx.deps.user_id,
        )
        return ""
    except Exception as e:
        logger.warning("read_workspace_item error for %s (alias %s): %s", item_id, wi, e)
        return ""


@router_agent.tool
async def list_workspace_items(ctx: RunContext[RouterDeps]) -> list[dict]:
    """List existing workspace items (artifacts/chips) for the current conversation.

    Most of the time the eager-loaded summaries in the system prompt are
    enough; call this tool only when you suspect items have changed mid-turn
    or you need a fresh listing.

    Returns:
        Compact list of {wi, title, kind_hint, created_at} dicts where ``wi``
        is the ``WI-{seq}`` alias for use in attached_wis / target_wi.
        Empty list on any error.
    """
    try:
        result = (
            ctx.deps.supabase.table("workspace_items")
            .select("item_id, wi_seq, title, kind, metadata, created_at")
            .eq("conversation_id", ctx.deps.conversation_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        rows = (result.data if result and getattr(result, "data", None) else []) or []
        items: list[dict] = []
        for row in rows:
            kind = row.get("kind") or "agent_search"
            metadata = row.get("metadata") or {}
            subtype = metadata.get("subtype") if isinstance(metadata, dict) else None
            wi_seq = row.get("wi_seq")
            items.append({
                # Expose the alias to the LLM; never the raw UUID.
                "wi": f"WI-{wi_seq}" if wi_seq is not None else None,
                "title": row.get("title", ""),
                "kind_hint": "agent_writing" if kind in ("note", "agent_writing") else "agent_search",
                "artifact_type": subtype,
                "created_at": row.get("created_at"),
            })
        logger.info(
            "list_workspace_items: %d items for conversation %s",
            len(items), ctx.deps.conversation_id,
        )
        return items
    except Exception as e:
        logger.warning(
            "list_workspace_items error for conversation %s: %s",
            ctx.deps.conversation_id, e,
        )
        return []


# ── Main runner ──────────────────────────────────────────────────────────────


async def run_router(
    question: str,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    case_memory_md: str | None,
    case_metadata: dict | None,
    user_preferences: dict | None,
    message_history: list[ModelMessage],
    workspace_item_summaries: list[dict] | None = None,
    compaction_summary_md: str | None = None,
) -> ChatResponse | DispatchAgent:
    """Run the router agent to classify user intent and respond or dispatch.

    Called by the orchestrator's ``_route()`` method. Constructs RouterDeps
    internally from the individual parameters so the orchestrator interface
    stays stable.

    Args:
        question: The user's message text.
        supabase: Supabase client for workspace item reads.
        user_id: Current user's user_id.
        conversation_id: Current conversation UUID.
        case_id: Optional case context.
        case_memory_md: Pre-built case memory markdown.
        case_metadata: Case name, type, parties dict.
        user_preferences: Response tone/style preferences dict.
        message_history: Pydantic AI ModelMessage list, already filtered
            by the loader to exclude agent_question / agent_answer kinds
            and to start strictly after compacted_through_message_id.
        workspace_item_summaries: Compact (item_id, kind, title, summary)
            dicts for the conversation's workspace items. Optional —
            empty list when none.
        compaction_summary_md: Full content_md of the latest convo_context
            workspace item, or None if the conversation has not been
            compacted yet.

    Returns:
        ChatResponse if the router answers directly,
        DispatchAgent if the router dispatches a specialist.
    """
    # Migration 052: build the seq → item_id lookup from the loaded summaries
    # so the output validator can resolve WI-{seq} aliases without a DB hit.
    summary_list = list(workspace_item_summaries or [])
    alias_map: dict[int, str] = {}
    for item in summary_list:
        seq = item.get("wi_seq")
        iid = item.get("item_id")
        if seq is not None and iid:
            alias_map[int(seq)] = str(iid)

    deps = RouterDeps(
        supabase=supabase,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        case_memory_md=case_memory_md,
        case_metadata=case_metadata,
        user_preferences=user_preferences,
        workspace_item_summaries=summary_list,
        compaction_summary_md=compaction_summary_md,
        wi_alias_map=alias_map,
    )

    # PII note: user_id intentionally NOT on this span. The monitor recovers
    # user_id via Supabase join on conversation_id (see agent_runs / messages /
    # conversations tables — all carry user_id as a column). Keeping user_id
    # out of Logfire span attributes narrows the PII surface area across the
    # 30-day retention window.
    with track_stage(
        "router.classify",
        conversation_id=conversation_id,
        case_id=case_id,
        agent_family="router",
        question_length=len(question),
        history_turns=len(message_history),
        workspace_item_count=len(deps.workspace_item_summaries),
        has_compaction_summary=bool(compaction_summary_md),
    ) as span:
        try:
            result = await router_agent.run(
                question,
                deps=deps,
                message_history=message_history,
                usage_limits=ROUTER_LIMITS,
            )
            span.record_run(result, slot="router")

            usage = result.usage()
            decision_type = getattr(result.output, "type", None)
            agent_family = (
                getattr(result.output, "agent_family", None)
                if isinstance(result.output, DispatchAgent)
                else None
            )
            attached_count = (
                len(result.output.attached_item_ids)
                if isinstance(result.output, DispatchAgent)
                else 0
            )
            span.set(
                decision=decision_type,
                agent_family=agent_family,
                attached_item_count=attached_count,
            )

            logger.info(
                "Router decision — type=%s, agent_family=%s, attached=%d, requests=%s, output_tokens=%s",
                decision_type,
                agent_family,
                attached_count,
                usage.requests,
                usage.output_tokens,
            )

            return result.output

        except Exception as e:
            logger.error("خطأ في الموجه: %s", e, exc_info=True)
            span.set(decision="error", error=str(e))
            span.set_outcome("error")
            # Fallback: return a safe ChatResponse so the user sees something
            return ChatResponse(
                message="عذراً، حدث خطأ أثناء معالجة رسالتك. يرجى المحاولة مرة أخرى."
            )
