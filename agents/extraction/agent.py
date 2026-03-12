"""Extraction agent — analyzes documents, creates summary artifact."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

from shared.types import AgentFamily, ArtifactType, AgentContext
from agents.base.agent import MockAgentBase
from agents.base.artifact import create_agent_artifact

logger = logging.getLogger(__name__)

MOCK_SUMMARY = (
    "# ملخص المستند\n\n"
    "## معلومات المستند\n"
    "- **النوع**: عقد تجاري\n"
    "- **التاريخ**: 1445/06/15هـ\n"
    "- **الأطراف**: شركة الأولى للتجارة / مؤسسة الثانية للمقاولات\n\n"
    "## البنود الرئيسية\n"
    "1. مدة العقد: سنتان من تاريخ التوقيع\n"
    "2. القيمة الإجمالية: 500,000 ريال سعودي\n"
    "3. شرط جزائي: 10% من قيمة العقد\n\n"
    "## المخاطر المحتملة\n"
    "- بند التحكيم غير محدد الجهة\n"
    "- غياب آلية واضحة لتسوية النزاعات\n"
    "- عدم تحديد آلية تعديل الأسعار\n\n"
    "## التوصيات\n"
    "- تحديد جهة التحكيم (المركز السعودي للتحكيم التجاري)\n"
    "- إضافة بند لتعديل الأسعار وفقاً لمؤشر أسعار المستهلك\n"
)

MOCK_STREAM = (
    "قمت بتحليل المستند المرفق واستخراج المعلومات الأساسية. "
    "يتضمن الملخص البنود الرئيسية والمخاطر المحتملة والتوصيات. "
    "يمكنكم مراجعة الملخص الكامل في لوحة المستندات."
)


class ExtractionAgent(MockAgentBase):
    """Document analysis — creates summary artifact."""

    agent_family = AgentFamily.EXTRACTION

    async def execute(self, ctx: AgentContext) -> AsyncGenerator[dict, None]:
        await asyncio.sleep(0.6)  # Simulate document processing

        # Stream response text
        words = MOCK_STREAM.split(" ")
        for i, word in enumerate(words):
            token = word if i == 0 else f" {word}"
            yield {"type": "token", "text": token}
            await asyncio.sleep(0.04)

        # Create summary artifact in DB
        supabase = getattr(ctx, "_supabase", None)
        if supabase:
            title = "ملخص مستند: " + ctx.question[:40]
            try:
                artifact = await create_agent_artifact(
                    supabase,
                    ctx.user_id,
                    ctx.conversation_id,
                    ctx.case_id,
                    agent_family=self.agent_family.value,
                    artifact_type=ArtifactType.SUMMARY.value,
                    title=title,
                    content_md=MOCK_SUMMARY,
                    is_editable=False,
                )
                yield {
                    "type": "artifact_created",
                    "artifact_id": artifact["artifact_id"],
                    "artifact_type": ArtifactType.SUMMARY.value,
                    "title": title,
                }
            except Exception as e:
                logger.warning("Error creating extraction artifact: %s", e)

        yield {
            "type": "done",
            "usage": {
                "prompt_tokens": 2000,
                "completion_tokens": len(MOCK_STREAM) + len(MOCK_SUMMARY),
                "model": "mock-extraction",
            },
        }
