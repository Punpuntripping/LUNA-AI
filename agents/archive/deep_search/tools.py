"""Tool functions for deep_search planner agent.

Eight tools:
  1. search_regulations   -- delegates to regulation executor (real)
  2. search_cases_courts  -- judicial precedent search (mock)
  3. search_compliance    -- government services search (mock)
  4. think                -- reasoning scratchpad (no side effects)
  5. ask_user             -- clarifying question stub
  6. respond_to_user      -- mid-search status update
  7. create_report        -- artifact CRUD (real DB)
  8. get_previous_report  -- artifact read (real DB)

Tools are plain async functions.  register_tools(agent) binds them
to any Agent instance — called by __init__.py for the default agent
and by create_planner_agent() for prompt-variant agents.
"""
from __future__ import annotations

import json
import logging
import time as _time
from datetime import datetime, timezone

import httpx
from pydantic import ValidationError
from pydantic_ai import Agent, RunContext

from shared.config import get_settings

from .agent import Citation
from .deps import SearchDeps

logger = logging.getLogger(__name__)


# -- Shared httpx client for Jina reranker (lazy init) ------------------------

_jina_client: httpx.AsyncClient | None = None


def _get_jina_client() -> httpx.AsyncClient:
    """Return a module-level httpx.AsyncClient, creating it on first call."""
    global _jina_client
    if _jina_client is None:
        _jina_client = httpx.AsyncClient(timeout=30.0)
    return _jina_client


# -- Mock constants for unimplemented executors --------------------------------

MOCK_CASES_RESULT = """\
## نتائج البحث في السوابق القضائية
**الجودة: متوسطة**

### 1. حكم محكمة العمل - القضية رقم 1445/3/2001
**المحكمة:** المحكمة العمالية بالرياض
**التاريخ:** 1445/03/15 هـ
**الملخص:** قضت المحكمة بأحقية العامل في تعويض عن الفصل التعسفي وفقاً للمادة 77 من نظام العمل، \
حيث لم يثبت صاحب العمل وجود سبب مشروع لإنهاء العقد.
**المبدأ القانوني:** يقع عبء إثبات المسوّغ المشروع للفصل على صاحب العمل.

### 2. حكم محكمة الاستئناف - القضية رقم 1444/5/3050
**المحكمة:** محكمة الاستئناف العمالية بجدة
**التاريخ:** 1444/08/20 هـ
**الملخص:** أيدت محكمة الاستئناف حكم الدرجة الأولى بإلزام صاحب العمل بدفع مكافأة نهاية الخدمة \
كاملة للعامل الذي أمضى أكثر من عشر سنوات في الخدمة.
**المبدأ القانوني:** تحتسب مكافأة نهاية الخدمة على أساس آخر أجر فعلي تقاضاه العامل.

---
**المصادر:**
- CASE-1445-3-2001 | حكم الفصل التعسفي | المحكمة العمالية بالرياض
- CASE-1444-5-3050 | حكم مكافأة نهاية الخدمة | محكمة الاستئناف العمالية بجدة
"""

MOCK_COMPLIANCE_RESULT = """\
## نتائج البحث في الخدمات الحكومية والامتثال
**الجودة: متوسطة**

### 1. خدمة تسجيل عقود العمل - منصة قوى
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**المنصة:** قوى (qiwa.sa)
**الوصف:** تتيح هذه الخدمة لأصحاب العمل تسجيل وتوثيق عقود العمل إلكترونياً، \
مع إمكانية تحديد نوع العقد ومدته وشروطه الأساسية.
**المتطلبات:** سجل تجاري ساري، تسجيل في التأمينات الاجتماعية، حساب مفعّل في قوى.

### 2. خدمة تسوية الخلافات العمالية - ودّي
**الجهة:** وزارة الموارد البشرية والتنمية الاجتماعية
**المنصة:** ودّي (mol.gov.sa)
**الوصف:** خدمة التسوية الودية للخلافات العمالية، حيث يتم محاولة حل النزاع \
بين العامل وصاحب العمل قبل رفعه للمحكمة العمالية.
**المدة:** 21 يوم عمل كحد أقصى لإتمام التسوية.

---
**المصادر:**
- SVC-QIWA-001 | تسجيل عقود العمل | منصة قوى
- SVC-WADDI-001 | تسوية الخلافات العمالية | منصة ودّي
"""


# -- Tool implementations (plain functions) -----------------------------------


async def search_regulations(
    ctx: RunContext[SearchDeps], query: str
) -> str:
    """Search Saudi statutory and regulatory law via the regulation executor agent.

    Use this tool when the legal question involves regulations, royal decrees,
    ministerial decisions, or specific articles of Saudi law. Pass a focused
    Arabic search query targeting the relevant legal domain.
    """
    call_start = _time.time()
    ts = datetime.now(timezone.utc).isoformat()
    logger.info("search_regulations called: %s", query[:120])
    ctx.deps._sse_events.append(
        {"type": "status", "text": f"جاري البحث في الأنظمة: {query[:80]}..."}
    )

    # Use mock results if injected (cross-feed / integration tests)
    if ctx.deps.mock_results and "regulations" in ctx.deps.mock_results:
        ctx.deps._sse_events.append({"type": "status", "text": "اكتمل البحث في الأنظمة"})
        result = ctx.deps.mock_results["regulations"]
        ctx.deps._tool_logs.append({
            "tool": "search_regulations",
            "query": query,
            "output_preview": result[:500],
            "output_length": len(result),
            "duration_s": round(_time.time() - call_start, 2),
            "timestamp": ts,
            "similarity_logs": [],
            "mock": True,
        })
        return result

    try:
        from agents.deep_search.executors import (
            RegulationSearchDeps,
            run_regulation_search,
        )
        from agents.utils.embeddings import embed_regulation_query

        settings = get_settings()
        jina_key = settings.JINA_RERANKER_API_KEY or ""

        reg_deps = RegulationSearchDeps(
            supabase=ctx.deps.supabase,
            embedding_fn=embed_regulation_query,
            jina_api_key=jina_key,
            http_client=_get_jina_client(),
        )

        result = await run_regulation_search(query, reg_deps)

        ctx.deps._sse_events.append(
            {"type": "status", "text": "اكتمل البحث في الأنظمة"}
        )

        # Capture tool log with similarity search data from executor
        ctx.deps._tool_logs.append({
            "tool": "search_regulations",
            "query": query,
            "output_preview": result[:500],
            "output_length": len(result),
            "duration_s": round(_time.time() - call_start, 2),
            "timestamp": ts,
            "similarity_logs": list(reg_deps._retrieval_logs),
            "mock": False,
        })

        return result

    except Exception as e:
        logger.warning("search_regulations error: %s", e, exc_info=True)
        ctx.deps._tool_logs.append({
            "tool": "search_regulations",
            "query": query,
            "output_preview": f"ERROR: {e}",
            "output_length": 0,
            "duration_s": round(_time.time() - call_start, 2),
            "timestamp": ts,
            "similarity_logs": [],
            "error": str(e),
        })
        return f"خطأ أثناء البحث في الأنظمة: {e}"


async def search_cases_courts(
    ctx: RunContext[SearchDeps], query: str
) -> str:
    """Search Saudi judicial precedents and court rulings.

    Use this tool when the legal question involves case law, judicial
    precedents, or court interpretations of Saudi regulations. Pass a
    focused Arabic search query.
    """
    call_start = _time.time()
    ts = datetime.now(timezone.utc).isoformat()
    logger.info("search_cases_courts called: %s", query[:120])
    ctx.deps._sse_events.append(
        {"type": "status", "text": f"جاري البحث في السوابق القضائية: {query[:80]}..."}
    )

    if ctx.deps.mock_results and "cases" in ctx.deps.mock_results:
        result = ctx.deps.mock_results["cases"]
    else:
        # TODO: replace with real cases executor when built
        result = MOCK_CASES_RESULT

    ctx.deps._tool_logs.append({
        "tool": "search_cases_courts",
        "query": query,
        "output_preview": result[:500],
        "output_length": len(result),
        "duration_s": round(_time.time() - call_start, 2),
        "timestamp": ts,
        "similarity_logs": [],
        "mock": True,
    })
    return result


async def search_compliance(
    ctx: RunContext[SearchDeps], query: str
) -> str:
    """Search government services and compliance procedures.

    Use this tool when the legal question involves government service
    requirements, compliance procedures, entity registration, licensing,
    or administrative processes. Pass a focused Arabic search query.
    """
    call_start = _time.time()
    ts = datetime.now(timezone.utc).isoformat()
    logger.info("search_compliance called: %s", query[:120])
    ctx.deps._sse_events.append(
        {"type": "status", "text": f"جاري البحث في خدمات الامتثال: {query[:80]}..."}
    )

    if ctx.deps.mock_results and "compliance" in ctx.deps.mock_results:
        result = ctx.deps.mock_results["compliance"]
    else:
        # TODO: replace with real compliance executor when built
        result = MOCK_COMPLIANCE_RESULT

    ctx.deps._tool_logs.append({
        "tool": "search_compliance",
        "query": query,
        "output_preview": result[:500],
        "output_length": len(result),
        "duration_s": round(_time.time() - call_start, 2),
        "timestamp": ts,
        "similarity_logs": [],
        "mock": True,
    })
    return result


async def think(
    ctx: RunContext[SearchDeps], thought: str
) -> str:
    """مساحة تفكير وتحليل — لا تنتج أي إجراء خارجي.

    استخدم هذه الأداة بعد استلام نتائج البحث لتحليل:
    - ما الذي وجدته وما الذي ينقص؟
    - هل النتائج تغطي السؤال أم تحتاج جولة بحث إضافية؟
    - هل هناك فجوة يمكن سدها بالتغذية المتبادلة من وكيل آخر؟
    - ما المحاور التي لم تُغطَّ بعد؟
    """
    logger.info("think: %s", thought[:200])
    ctx.deps._tool_logs.append({
        "tool": "think",
        "thought": thought,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return "تم. تابع بناءً على استنتاجاتك."


async def ask_user(
    ctx: RunContext[SearchDeps], question: str
) -> str:
    """Ask the user a clarifying question before or during search.

    Use this tool when the query is ambiguous and could refer to multiple
    legal domains -- choosing wrong wastes search tokens. The question
    should be in Arabic and specific enough for the user to answer concisely.

    Note: In v1 this is a stub that returns a fixed reply. Real pause/resume
    via Redis pub/sub is a future enhancement.
    """
    logger.info("ask_user called: %s", question[:120])
    ctx.deps._sse_events.append(
        {"type": "ask_user", "question": question}
    )
    return "لم يقدم المستخدم توضيحات إضافية. تابع بناءً على المعلومات المتاحة."


async def respond_to_user(
    ctx: RunContext[SearchDeps], message: str
) -> str:
    """Send a mid-search status update to the user. Fire-and-forget.

    Use this tool at two moments: (1) when starting search to inform the
    user what you are about to do, and (2) when re-searching or if search
    is taking long. The message should be in Arabic.
    """
    logger.info("respond_to_user called: %s", message[:120])
    ctx.deps._sse_events.append(
        {"type": "status", "text": message}
    )
    return "Done"


async def create_report(
    ctx: RunContext[SearchDeps],
    title: str,
    content_md: str,
    citations: list[dict],
) -> str:
    """Create or update a markdown research report artifact in the database.

    Call this tool after synthesizing search results into a structured report.
    Pass the full report title, complete markdown content, and a list of
    citation dicts. If a report was already created this session, it will be
    updated in place. Every call must include the FULL content -- never diffs.

    Each citation dict should have: source_type, ref, title, and optionally
    content_snippet, regulation_title, article_num, court, relevance.
    """
    logger.info(
        "create_report called: title=%s, citations=%d",
        title[:80],
        len(citations),
    )

    # Validate citations best-effort via Citation model
    validated_citations: list[dict] = []
    for raw in citations:
        try:
            c = Citation.model_validate(raw)
            validated_citations.append(c.model_dump(exclude_none=True))
        except (ValidationError, Exception):
            # Fall back to raw dict if validation fails
            validated_citations.append(raw)

    now_iso = datetime.now(timezone.utc).isoformat()
    metadata = json.dumps(
        {"citations": validated_citations}, ensure_ascii=False
    )

    try:
        if ctx.deps.artifact_id:
            # UPDATE existing artifact
            result = (
                ctx.deps.supabase.table("artifacts")
                .update({
                    "title": title,
                    "content_md": content_md,
                    "metadata": metadata,
                    "updated_at": now_iso,
                })
                .eq("artifact_id", ctx.deps.artifact_id)
                .execute()
            )

            ctx.deps._sse_events.append({
                "type": "artifact_updated",
                "artifact_id": ctx.deps.artifact_id,
            })

            logger.info("Artifact updated: %s", ctx.deps.artifact_id)
            return ctx.deps.artifact_id

        else:
            # INSERT new artifact
            insert_data = {
                "user_id": ctx.deps.user_id,
                "conversation_id": ctx.deps.conversation_id,
                "agent_family": "deep_search",
                "artifact_type": "report",
                "title": title,
                "content_md": content_md,
                "is_editable": True,
                "metadata": metadata,
            }
            if ctx.deps.case_id:
                insert_data["case_id"] = ctx.deps.case_id

            result = (
                ctx.deps.supabase.table("artifacts")
                .insert(insert_data)
                .execute()
            )

            new_id = result.data[0]["artifact_id"]
            ctx.deps.artifact_id = new_id  # Mutable -- propagates to orchestrator

            ctx.deps._sse_events.append({
                "type": "artifact_created",
                "artifact_id": new_id,
                "artifact_type": "report",
                "title": title,
            })

            logger.info("Artifact created: %s", new_id)
            return new_id

    except Exception as e:
        logger.warning("create_report DB error: %s", e, exc_info=True)
        return f"خطأ أثناء حفظ التقرير: {e}"


async def get_previous_report(
    ctx: RunContext[SearchDeps], artifact_id: str
) -> str:
    """Load a previously created research report by artifact_id for editing.

    Use this tool when the briefing includes an artifact_id -- load the
    existing report first, then decide what to change, extend, or refine.
    Returns the full markdown content of the report.
    """
    logger.info("get_previous_report called: %s", artifact_id)

    try:
        result = (
            ctx.deps.supabase.table("artifacts")
            .select("title, content_md, metadata")
            .eq("artifact_id", artifact_id)
            .eq("user_id", ctx.deps.user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )

        if not result or not result.data:
            return f"لم يتم العثور على التقرير: {artifact_id}"

        row = result.data
        title = row.get("title", "")
        content = row.get("content_md", "")

        ctx.deps._sse_events.append({
            "type": "status",
            "text": f"تم تحميل التقرير السابق: {title[:60]}",
        })

        logger.info("Loaded previous report: %s (%s)", artifact_id, title[:40])
        return f"# {title}\n\n{content}"

    except Exception as e:
        logger.warning("get_previous_report error: %s", e, exc_info=True)
        return f"خطأ أثناء تحميل التقرير: {e}"


# -- Registration function ----------------------------------------------------


def register_tools(agent: Agent) -> None:
    """Register all deep_search tools on the given Agent instance."""
    agent.tool(retries=1)(search_regulations)
    agent.tool(retries=1)(search_cases_courts)
    agent.tool(retries=1)(search_compliance)
    agent.tool(retries=0)(think)
    agent.tool(retries=0)(ask_user)
    agent.tool(retries=0)(respond_to_user)
    agent.tool(retries=1)(create_report)
    agent.tool(retries=1)(get_previous_report)
