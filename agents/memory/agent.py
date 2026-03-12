"""Memory agent — manages case memory, creates memory.md artifact."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

from shared.types import AgentFamily, ArtifactType, AgentContext
from agents.base.agent import MockAgentBase
from agents.base.artifact import create_agent_artifact

logger = logging.getLogger(__name__)

MOCK_STREAM = (
    "تم تسجيل المعلومة في ذاكرة القضية. "
    "يمكنكم مراجعة وتعديل ملف الذاكرة من لوحة المستندات."
)


class MemoryAgent(MockAgentBase):
    """Case memory management — creates/updates memory.md artifact + writes to case_memories."""

    agent_family = AgentFamily.MEMORY

    async def execute(self, ctx: AgentContext) -> AsyncGenerator[dict, None]:
        await asyncio.sleep(0.3)

        # Stream response text
        words = MOCK_STREAM.split(" ")
        for i, word in enumerate(words):
            token = word if i == 0 else f" {word}"
            yield {"type": "token", "text": token}
            await asyncio.sleep(0.04)

        supabase = getattr(ctx, "_supabase", None)
        if supabase:
            # Build updated memory_md content
            existing_memory = ctx.memory_md or "# ذاكرة القضية\n\n"
            new_entry = f"- [mock] {ctx.question}\n"
            updated_memory = existing_memory.rstrip() + "\n" + new_entry

            # Create/update memory.md artifact (editable so lawyers can amend it)
            title = "ذاكرة القضية"
            try:
                artifact = await create_agent_artifact(
                    supabase,
                    ctx.user_id,
                    ctx.conversation_id,
                    ctx.case_id,
                    agent_family=self.agent_family.value,
                    artifact_type=ArtifactType.MEMORY_FILE.value,
                    title=title,
                    content_md=updated_memory,
                    is_editable=True,
                )
                yield {
                    "type": "artifact_created",
                    "artifact_id": artifact["artifact_id"],
                    "artifact_type": ArtifactType.MEMORY_FILE.value,
                    "title": title,
                }
            except Exception as e:
                logger.warning("Error creating memory artifact: %s", e)

            # Also write a structured entry to case_memories (only agent with DB write access)
            if ctx.case_id:
                try:
                    supabase.table("case_memories").insert({
                        "case_id": ctx.case_id,
                        "memory_type": "fact",
                        "content_ar": f"[mock] {ctx.question}",
                        "confidence_score": 0.8,
                    }).execute()
                except Exception as e:
                    logger.warning("Error writing to case_memories: %s", e)

        yield {
            "type": "done",
            "usage": {
                "prompt_tokens": 600,
                "completion_tokens": len(MOCK_STREAM),
                "model": "mock-memory",
            },
        }
