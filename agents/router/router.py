"""Router agent — conversational front-end for Luna Legal AI.

Classifies user intent and either responds directly (ChatResponse) or
dispatches a specialist task (OpenTask) with a synthesized briefing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits
from supabase import Client as SupabaseClient

from agents.models import ChatResponse, OpenTask
from agents.utils.agent_models import get_agent_model

logger = logging.getLogger(__name__)


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


# ── Usage limits ──────────────────────────────────────────────────────────────


ROUTER_LIMITS = UsageLimits(
    response_tokens_limit=2000,
    request_limit=5,
    tool_calls_limit=3,
)


# ── System prompt ─────────────────────────────────────────────────────────────


SYSTEM_PROMPT = """\
أنت لونا، المساعد القانوني الذكي للمحامين السعوديين.

أنت الواجهة الرئيسية للمحادثة — كل رسالة لا تتعلق بمهمة نشطة تمر من خلالك.

لديك ثلاث وظائف:
1. الإجابة المباشرة — التحيات، التوضيحات، الأسئلة القانونية البسيطة، الأسئلة عن تقارير ومستندات سابقة
2. فتح مهام متخصصة — عندما يحتاج المستخدم بحثاً قانونياً معمقاً أو صياغة مستند أو معالجة ملف
3. الحفاظ على تواصل المحادثة — تعرف ما حدث في المهام السابقة عبر الملخصات المحقونة في سجل المحادثة

## متى تجيب مباشرة (ChatResponse):
- التحيات والمجاملات
- الأسئلة البسيطة التي يمكنك الإجابة عنها بثقة عالية
- أسئلة التوضيح — عندما تحتاج مزيداً من المعلومات من المستخدم
- أسئلة عن لونا ووظائفها
- أسئلة عن محتوى تقرير أو مستند سابق — استخدم أداة get_artifact لقراءة المحتوى والإجابة مباشرة
- الرسائل الغامضة — اسأل المستخدم قبل فتح مهمة

## متى تفتح مهمة deep_search:
- أسئلة قانونية تحتاج بحثاً في الأنظمة أو الأحكام أو السوابق
- طلبات تحليل أو مقارنة أو شرح تفصيلي لمفاهيم قانونية
- كلمات مفتاحية: "ابحث"، "حلل"، "قارن"، "اشرح بالتفصيل"
- أسئلة عن حقوق أو التزامات أو عقوبات أو إجراءات بموجب أنظمة محددة
- أي سؤال يحتاج مصدراً قانونياً أو استشهاداً
- القاعدة: إذا كانت الإجابة تحتاج استشهاداً → افتح مهمة

## متى تفتح مهمة end_services:
- طلب صريح لكتابة مستند: عقد، مذكرة، دفاع، رأي قانوني
- كلمات مفتاحية: "اكتب"، "صياغة"، "مسودة"، "عقد"، "مذكرة"، "خطاب"
- طلب تعديل مستند سابق (artifact) — افتح مهمة مع artifact_id

## متى تفتح مهمة extraction:
- المستخدم رفع ملفاً ويريد معالجته
- كلمات مفتاحية: "استخراج"، "تلخيص"، "ملف"، "وثيقة"

## قواعد التعامل مع المستندات السابقة (artifacts):
- سؤال عن محتوى المستند (قراءة) → استخدم get_artifact وأجب مباشرة
- طلب تعديل أو تحرير المستند → افتح مهمة جديدة مع artifact_id
- عندما يشير المستخدم لمستند دون تحديد → اذكر المستندات المتاحة واسأل أيها يقصد

## قواعد كتابة الملخص (briefing) عند فتح مهمة:
- اكتب ملخصاً شاملاً (100-500 كلمة) يتضمن:
  * ماذا يريد المستخدم بالتحديد
  * السياق المهم من المحادثة السابقة
  * أي متطلبات أو قيود ذكرها المستخدم
  * إشارات لتقارير أو مستندات سابقة مع تحديد artifact_id
- لا تنسخ المحادثة حرفياً — لخّص واستخرج المهم فقط
- لا تفتح مهمة إذا كنت غير متأكد مما يريده المستخدم — اسأله أولاً

## قواعد عامة:
- كن منحازاً نحو فتح المهام بدلاً من إعطاء إجابات قانونية بدون مصادر
- إذا كنت غير متأكد → اسأل المستخدم
- أجب بالعربية إلا إذا كتب المستخدم بالإنجليزية
- لا تذكر كلمة "مهمة" أو "task" أو تفاصيل تقنية — المستخدم لا يعرف عن نظام المهام
"""


# ── Agent definition ──────────────────────────────────────────────────────────


router_agent = Agent(
    get_agent_model("router"),
    output_type=ChatResponse | OpenTask,  # type: ignore[arg-type]
    deps_type=RouterDeps,
    instructions=SYSTEM_PROMPT,
    retries=1,
    end_strategy="early",
)


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


# ── CRUD tools ────────────────────────────────────────────────────────────────


@router_agent.tool
async def get_artifact(ctx: RunContext[RouterDeps], artifact_id: str) -> str:
    """Read a previous artifact (report, contract, summary) by its ID.

    Use this when the user asks about the content of a previous report,
    contract, or other document. Returns the full markdown content.

    Args:
        artifact_id: The UUID of the artifact to retrieve.
    """
    try:
        result = (
            ctx.deps.supabase.table("artifacts")
            .select("title, content_md")
            .eq("artifact_id", artifact_id)
            .eq("user_id", ctx.deps.user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
        if result and result.data:
            title = result.data.get("title", "")
            content = result.data.get("content_md", "")
            logger.info("Loaded artifact %s for user %s", artifact_id, ctx.deps.user_id)
            return f"# {title}\n\n{content}" if title else content
        logger.info("Artifact %s not found for user %s", artifact_id, ctx.deps.user_id)
        return "لم يُعثر على المستند المطلوب."
    except Exception as e:
        logger.warning("Error loading artifact %s: %s", artifact_id, e)
        return "حدث خطأ أثناء تحميل المستند. يرجى المحاولة مرة أخرى."


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
) -> ChatResponse | OpenTask:
    """Run the router agent to classify user intent and respond or dispatch.

    Called by the orchestrator's ``_route()`` method. Constructs RouterDeps
    internally from the individual parameters so the orchestrator interface
    stays unchanged.

    Args:
        question: The user's message text.
        supabase: Supabase client for artifact reads.
        user_id: Current user's user_id.
        conversation_id: Current conversation UUID.
        case_id: Optional case context.
        case_memory_md: Pre-built case memory markdown.
        case_metadata: Case name, type, parties dict.
        user_preferences: Response tone/style preferences dict.
        message_history: Pydantic AI ModelMessage list from conversation.

    Returns:
        ChatResponse if the router answers directly,
        OpenTask if the router dispatches a specialist task.
    """
    deps = RouterDeps(
        supabase=supabase,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        case_memory_md=case_memory_md,
        case_metadata=case_metadata,
        user_preferences=user_preferences,
    )

    try:
        result = await router_agent.run(
            question,
            deps=deps,
            message_history=message_history,
            usage_limits=ROUTER_LIMITS,
        )

        usage = result.usage()
        logger.info(
            "Router decision — type=%s, requests=%s, output_tokens=%s",
            result.output.type,
            usage.requests,
            usage.output_tokens,
        )

        return result.output

    except Exception as e:
        logger.error("خطأ في الموجه: %s", e, exc_info=True)
        # Fallback: return a safe ChatResponse so the user sees something
        return ChatResponse(
            message="عذراً، حدث خطأ أثناء معالجة رسالتك. يرجى المحاولة مرة أخرى."
        )
