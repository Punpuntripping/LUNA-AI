"""Router agent — conversational front-end for Luna Legal AI.

Classifies user intent and either responds directly (ChatResponse) or
dispatches a specialist agent (DispatchAgent) with a synthesized briefing
plus the workspace items the specialist should see as input.

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
from dataclasses import dataclass, field

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits
from supabase import Client as SupabaseClient

from agents.models import ChatResponse, DispatchAgent, MAX_ATTACHED_ITEMS
from agents.utils.agent_models import get_agent_model
from shared.observability import get_logfire

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
    # dynamic instructions. Lists hold compact (item_id, kind, title, summary)
    # dicts; full content_md is fetched on demand via read_workspace_item.
    workspace_item_summaries: list[dict] = field(default_factory=list)
    compaction_summary_md: str | None = None


# ── Usage limits ──────────────────────────────────────────────────────────────


ROUTER_LIMITS = UsageLimits(
    response_tokens_limit=2000,
    request_limit=5,
    tool_calls_limit=5,
)


# ── System prompt ─────────────────────────────────────────────────────────────


SYSTEM_PROMPT = """\
أنت لونا، المساعد القانوني الذكي للمحامين السعوديين.

أنت الواجهة الرئيسية للمحادثة — كل رسالة من المستخدم تمر من خلالك.

لديك ثلاث وظائف:
1. الإجابة المباشرة — التحيات، التوضيحات، الأسئلة القانونية البسيطة، الأسئلة عن تقارير ومستندات سابقة
2. توجيه المهام إلى متخصص (DispatchAgent) — عندما يحتاج المستخدم بحثاً قانونياً معمقاً أو صياغة مستند أو معالجة ملف
3. الحفاظ على تواصل المحادثة — تستفيد من ملخصات عناصر مساحة العمل وملخص ضغط المحادثة المحقونين في السياق

## القرارات قبل كل رد (أربع فحوصات):
1. **الضرورة** — هل تحتاج هذه الرسالة فعلاً متخصصاً؟ إن أمكن الرد المباشر فردّ مباشرة.
2. **النطاق** — هل الطلب ضمن المجال القانوني السعودي؟ إن لم يكن فاعتذر بأدب عبر ChatResponse.
3. **الغموض** — إن كانت الرسالة غامضة، اطرح سؤالاً توضيحياً واحداً عبر ChatResponse قبل التوجيه.
4. **اختيار العناصر المرفقة** — حدد attached_item_ids بناءً على ملخصات العناصر المتاحة في مساحة العمل.

## متى تجيب مباشرة (ChatResponse):
- التحيات والمجاملات
- الأسئلة البسيطة التي يمكنك الإجابة عنها بثقة عالية
- أسئلة التوضيح — عندما تحتاج مزيداً من المعلومات من المستخدم
- أسئلة عن لونا ووظائفها
- أسئلة عن محتوى تقرير أو مستند سابق — استخدم أداة read_workspace_item لقراءة المحتوى والإجابة مباشرة
- الرسائل الغامضة — اسأل المستخدم قبل التوجيه

## متى توجّه إلى deep_search (DispatchAgent):
- أسئلة قانونية تحتاج بحثاً في الأنظمة أو الأحكام أو السوابق
- طلبات تحليل أو مقارنة أو شرح تفصيلي لمفاهيم قانونية
- كلمات مفتاحية: "ابحث"، "حلل"، "قارن"، "اشرح بالتفصيل"
- أسئلة عن حقوق أو التزامات أو عقوبات أو إجراءات بموجب أنظمة محددة
- القاعدة: إذا كانت الإجابة تحتاج استشهاداً → وجّه مهمة deep_search

## متى توجّه إلى end_services:
- طلب صريح لكتابة مستند: عقد، مذكرة، دفاع، رأي قانوني (عبر مسار end_services)
- طلب تعديل عنصر سابق في مساحة العمل — وجّه مع target_item_id

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
- إذا أشار المستخدم لمستند موجود في مساحة العمل ("حدّث المذكرة السابقة"، "عدّل العقد") — حدّد item_id المقصود من ملخصات العناصر، ومرّره عبر target_item_id لفتح مهمة writing تحرير
- إذا كان المستخدم يبحث عن معلومات قانونية لتدعيم الصياغة — وجّه deep_search أولاً، ثم writing لاحقاً

## متى توجّه إلى extraction:
- المستخدم رفع ملفاً ويريد معالجته
- كلمات مفتاحية: "استخراج"، "تلخيص"، "ملف"، "وثيقة"

## اختيار attached_item_ids:
- ملخصات عناصر مساحة العمل تُحقن لك في السياق (item_id, kind, title, summary).
- اختر العناصر الأكثر صلة بالطلب الحالي فقط.
- الحد الأقصى الصارم: {MAX_ATTACHED_ITEMS} عناصر لكل توجيه. إن وجدت أكثر، اختر الأهم.
- إن لم تكفك الملخصات، استدعِ read_workspace_item بمعرف العنصر للحصول على المحتوى الكامل (يمكن استدعاؤها على عدة عناصر بالتوازي).
- إن لم يوجد عنصر مناسب، اترك attached_item_ids قائمة فارغة.

## قواعد التعامل مع العناصر السابقة (workspace items):
- سؤال عن محتوى العنصر (قراءة) → استخدم read_workspace_item وأجب مباشرة عبر ChatResponse
- طلب تعديل أو تحرير العنصر → وجّه DispatchAgent مع target_item_id
- عندما يشير المستخدم لعنصر دون تحديد → اذكر العناصر المتاحة (من الملخصات) واسأل أيها يقصد

## قواعد كتابة الملخص (briefing) عند التوجيه:
- اكتب ملخصاً شاملاً (100-500 كلمة) يتضمن:
  * ماذا يريد المستخدم بالتحديد
  * السياق المهم من المحادثة السابقة (وإن لزم من ملخص ضغط المحادثة)
  * أي متطلبات أو قيود ذكرها المستخدم
  * إشارات لعناصر سابقة مع تحديد target_item_id إن كان التحرير على عنصر بعينه
- لا تنسخ المحادثة حرفياً — لخّص واستخرج المهم فقط
- لا توجّه إذا كنت غير متأكد مما يريده المستخدم — اسأله أولاً

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
    output_type=[ChatResponse, DispatchAgent],
    deps_type=RouterDeps,
    instructions=SYSTEM_PROMPT,
    retries=1,
    end_strategy="early",
)


# ── Output validator (belt-and-suspenders for attached_item_ids cap) ──────────


@router_agent.output_validator
def _validate_attached_items_cap(
    ctx: RunContext[RouterDeps],
    value: ChatResponse | DispatchAgent,
) -> ChatResponse | DispatchAgent:
    """Guard against the model overshooting MAX_ATTACHED_ITEMS.

    ``Field(max_length=MAX_ATTACHED_ITEMS)`` already rejects too many items
    at parse time, but raising ``ModelRetry`` here gives the LLM a guided
    retry with an instructive error rather than a hard validation failure.
    """
    if isinstance(value, DispatchAgent) and len(value.attached_item_ids) > MAX_ATTACHED_ITEMS:
        raise ModelRetry(
            f"اخترت {len(value.attached_item_ids)} عناصر، والحد الأقصى "
            f"{MAX_ATTACHED_ITEMS}. أعد الاختيار وأبقِ على الأكثر صلة فقط."
        )
    return value


# ── Dynamic instructions ──────────────────────────────────────────────────────


@router_agent.instructions
def inject_case_context(ctx: RunContext[RouterDeps]) -> str:
    """Inject case-specific memory and metadata when the conversation is within a lawyer's case."""
    if ctx.deps.case_memory_md:
        return f"""
سياق القضية الحالية:
{ctx.deps.case_memory_md}

استخدم هذا السياق لفهم أسئلة المستخدم. إذا طلب بحثاً أو صياغة، ضمّن المعلومات ذات الصلة في الملخص (briefing).
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
    """Render workspace item summaries (item_id, kind, title, summary).

    These are the candidate pool for ``attached_item_ids`` and the primary
    way the router knows what artifacts exist in this conversation. Full
    ``content_md`` stays out of the prompt — fetched on demand by the
    ``read_workspace_item`` tool.
    """
    items = ctx.deps.workspace_item_summaries or []
    if not items:
        return ""
    lines = ["عناصر مساحة العمل المتاحة (للاختيار في attached_item_ids):"]
    for item in items:
        item_id = item.get("item_id", "")
        kind = item.get("kind") or item.get("kind_hint") or "unknown"
        title = item.get("title") or "(بدون عنوان)"
        summary = item.get("summary")
        summary_text = summary if summary else "(لا يوجد ملخص بعد)"
        lines.append(f"- item_id={item_id} | kind={kind} | title={title}\n  summary: {summary_text}")
    lines.append(
        "للاطلاع على المحتوى الكامل لأي عنصر استدعِ أداة read_workspace_item بمعرّفه "
        "(يمكن استدعاؤها على عدة عناصر بالتوازي)."
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
async def read_workspace_item(ctx: RunContext[RouterDeps], item_id: str) -> str:
    """Return the full markdown content of a workspace item by id.

    Use this tool when the per-item ``summary`` provided in context is
    insufficient — for example, answering a direct question about an
    artifact's contents, or picking ``attached_item_ids`` for a dispatch
    where the summary leaves the item's relevance ambiguous.

    The tool can be invoked in parallel for multiple item_ids in a single
    turn (the Pydantic AI runtime surfaces parallel tool calls automatically),
    so feel free to open several artifacts at once when cross-referencing.

    Returns the raw ``content_md`` string, or an empty string if the item
    is not found / not accessible — in which case you should silently move
    on without retrying.

    Args:
        item_id: The UUID of the workspace item to read.
    """
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
                "read_workspace_item: loaded %s for user %s (%d chars)",
                item_id, ctx.deps.user_id, len(content),
            )
            return content
        logger.info(
            "read_workspace_item: %s not found for user %s",
            item_id, ctx.deps.user_id,
        )
        return ""
    except Exception as e:
        logger.warning("read_workspace_item error for %s: %s", item_id, e)
        return ""


@router_agent.tool
async def list_workspace_items(ctx: RunContext[RouterDeps]) -> list[dict]:
    """List existing workspace items (artifacts/chips) for the current conversation.

    Most of the time the eager-loaded summaries in the system prompt are
    enough; call this tool only when you suspect items have changed mid-turn
    or you need a fresh listing.

    Returns:
        Compact list of {item_id, title, kind_hint, created_at} dicts.
        Empty list on any error.
    """
    try:
        result = (
            ctx.deps.supabase.table("workspace_items")
            .select("item_id, title, kind, metadata, created_at")
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
            items.append({
                "item_id": row.get("item_id"),
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
    deps = RouterDeps(
        supabase=supabase,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        case_memory_md=case_memory_md,
        case_metadata=case_metadata,
        user_preferences=user_preferences,
        workspace_item_summaries=list(workspace_item_summaries or []),
        compaction_summary_md=compaction_summary_md,
    )

    with _logfire.span(
        "router.classify",
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
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
            try:
                span.set_attribute("decision", decision_type)
                span.set_attribute("agent_family", agent_family)
                span.set_attribute("attached_item_count", attached_count)
                span.set_attribute("requests", usage.requests)
                span.set_attribute("output_tokens", usage.output_tokens)
            except Exception:
                pass

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
            try:
                span.set_attribute("decision", "error")
                span.set_attribute("error", str(e))
            except Exception:
                pass
            # Fallback: return a safe ChatResponse so the user sees something
            return ChatResponse(
                message="عذراً، حدث خطأ أثناء معالجة رسالتك. يرجى المحاولة مرة أخرى."
            )
