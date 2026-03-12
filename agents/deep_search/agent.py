"""Deep search agent — detailed analysis, creates report artifact."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

from shared.types import AgentFamily, ArtifactType, AgentContext
from agents.base.agent import MockAgentBase
from agents.base.artifact import create_agent_artifact

logger = logging.getLogger(__name__)

MOCK_REPORT = (
    "# تقرير بحث قانوني\n\n"
    "## ملخص تنفيذي\n"
    "بناءً على تحليل الأنظمة ذات الصلة، تم التوصل إلى النتائج التالية:\n\n"
    "## التحليل القانوني\n"
    "### أولاً: الإطار النظامي\n"
    "يحكم هذا الموضوع نظام العمل الصادر بالمرسوم الملكي رقم م/51 وتاريخ 1426/8/23هـ، "
    "وتحديداً المواد من 74 إلى 84 التي تنظم أحكام إنهاء عقد العمل.\n\n"
    "### ثانياً: الأحكام التفصيلية\n"
    "1. **المادة 74**: تحدد حالات انتهاء عقد العمل بشكل طبيعي\n"
    "2. **المادة 80**: تنظم حالات الفسخ المشروع من قبل صاحب العمل\n"
    "3. **المادة 81**: تحدد حالات ترك العامل للعمل دون إشعار\n\n"
    "## التوصيات\n"
    "- مراجعة بنود العقد المبرم بين الطرفين\n"
    "- التأكد من استيفاء الإجراءات النظامية للإنهاء\n"
    "- حساب مستحقات نهاية الخدمة وفقاً للمادة 84\n"
)

MOCK_STREAM = (
    "أجري بحثاً معمقاً في الأنظمة السعودية ذات الصلة بسؤالكم. "
    "بعد تحليل المواد القانونية المتعلقة، أعددت لكم تقريراً شاملاً "
    "يتضمن التحليل القانوني والتوصيات. يمكنكم الاطلاع على التقرير الكامل في لوحة المستندات."
)

MOCK_CITATIONS = [
    {
        "article_id": "labor-74",
        "law_name": "نظام العمل",
        "article_number": 74,
        "relevance_score": 0.95,
    },
    {
        "article_id": "labor-80",
        "law_name": "نظام العمل",
        "article_number": 80,
        "relevance_score": 0.91,
    },
    {
        "article_id": "labor-81",
        "law_name": "نظام العمل",
        "article_number": 81,
        "relevance_score": 0.88,
    },
    {
        "article_id": "labor-84",
        "law_name": "نظام العمل",
        "article_number": 84,
        "relevance_score": 0.85,
    },
]


class DeepSearchAgent(MockAgentBase):
    """Detailed legal analysis — creates report artifact."""

    agent_family = AgentFamily.DEEP_SEARCH

    async def execute(self, ctx: AgentContext) -> AsyncGenerator[dict, None]:
        await asyncio.sleep(0.5)  # Simulate deeper thinking

        # Stream response text
        words = MOCK_STREAM.split(" ")
        for i, word in enumerate(words):
            token = word if i == 0 else f" {word}"
            yield {"type": "token", "text": token}
            await asyncio.sleep(0.04)

        # Create report artifact in DB
        supabase = getattr(ctx, "_supabase", None)
        if supabase:
            title = "تقرير بحث: " + ctx.question[:50]
            try:
                artifact = await create_agent_artifact(
                    supabase,
                    ctx.user_id,
                    ctx.conversation_id,
                    ctx.case_id,
                    agent_family=self.agent_family.value,
                    artifact_type=ArtifactType.REPORT.value,
                    title=title,
                    content_md=MOCK_REPORT,
                    is_editable=False,
                )
                yield {
                    "type": "artifact_created",
                    "artifact_id": artifact["artifact_id"],
                    "artifact_type": ArtifactType.REPORT.value,
                    "title": title,
                }
            except Exception as e:
                logger.warning("Error creating deep search artifact: %s", e)

        yield {"type": "citations", "articles": MOCK_CITATIONS}

        yield {
            "type": "done",
            "usage": {
                "prompt_tokens": 1500,
                "completion_tokens": len(MOCK_STREAM) + len(MOCK_REPORT),
                "model": "mock-deep-search",
            },
        }
