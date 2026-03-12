"""End services agent — drafts contracts/memos, creates editable artifacts."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

from shared.types import AgentFamily, ArtifactType, AgentContext
from agents.base.agent import MockAgentBase
from agents.base.artifact import create_agent_artifact

logger = logging.getLogger(__name__)

MOCK_CONTRACT = (
    "# عقد عمل\n\n"
    "## الطرف الأول (صاحب العمل)\n"
    "الاسم: ________________\n"
    "السجل التجاري: ________________\n\n"
    "## الطرف الثاني (العامل)\n"
    "الاسم: ________________\n"
    "رقم الهوية: ________________\n\n"
    "## البنود والشروط\n\n"
    "### البند الأول: مدة العقد\n"
    "مدة هذا العقد سنة واحدة تبدأ من تاريخ مباشرة العمل، ويتجدد تلقائياً "
    "لمدة مماثلة ما لم يخطر أحد الطرفين الآخر بعدم رغبته في التجديد.\n\n"
    "### البند الثاني: الأجر\n"
    "يتقاضى الطرف الثاني أجراً شهرياً قدره ________ ريال سعودي.\n\n"
    "### البند الثالث: ساعات العمل\n"
    "ساعات العمل الرسمية ثمان ساعات يومياً وفقاً لنظام العمل السعودي.\n\n"
    "### البند الرابع: الإجازات\n"
    "يستحق الطرف الثاني إجازة سنوية مدتها 21 يوماً.\n\n"
    "---\n"
    "التوقيع: ________________  التاريخ: ________________\n"
)

MOCK_STREAM = (
    "أعددت لكم مسودة عقد عمل وفقاً لأحكام نظام العمل السعودي. "
    "تتضمن المسودة البنود الأساسية المطلوبة نظاماً. "
    "يمكنكم تعديل المسودة مباشرة في لوحة المستندات لتناسب احتياجاتكم."
)


class EndServicesAgent(MockAgentBase):
    """Contract/memo drafting — creates editable document artifacts."""

    agent_family = AgentFamily.END_SERVICES

    async def execute(self, ctx: AgentContext) -> AsyncGenerator[dict, None]:
        await asyncio.sleep(0.4)

        # Stream response text
        words = MOCK_STREAM.split(" ")
        for i, word in enumerate(words):
            token = word if i == 0 else f" {word}"
            yield {"type": "token", "text": token}
            await asyncio.sleep(0.04)

        # Create contract artifact in DB (editable)
        supabase = getattr(ctx, "_supabase", None)
        if supabase:
            title = "مسودة عقد: " + ctx.question[:40]
            try:
                artifact = await create_agent_artifact(
                    supabase,
                    ctx.user_id,
                    ctx.conversation_id,
                    ctx.case_id,
                    agent_family=self.agent_family.value,
                    artifact_type=ArtifactType.CONTRACT.value,
                    title=title,
                    content_md=MOCK_CONTRACT,
                    is_editable=True,
                )
                yield {
                    "type": "artifact_created",
                    "artifact_id": artifact["artifact_id"],
                    "artifact_type": ArtifactType.CONTRACT.value,
                    "title": title,
                }
            except Exception as e:
                logger.warning("Error creating end services artifact: %s", e)

        yield {
            "type": "done",
            "usage": {
                "prompt_tokens": 1200,
                "completion_tokens": len(MOCK_STREAM) + len(MOCK_CONTRACT),
                "model": "mock-end-services",
            },
        }
