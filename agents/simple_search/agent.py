"""Simple search agent — streams mock Arabic response with citations."""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from shared.types import AgentFamily, AgentContext
from agents.base.agent import MockAgentBase

MOCK_RESPONSE = (
    "وفقاً لنظام العمل السعودي، المادة 74، يحق للعامل الحصول على أجره كاملاً "
    "في مواعيد استحقاقه المحددة. كما تنص المادة 80 على أنه لا يجوز لصاحب العمل "
    "فسخ العقد دون مكافأة أو إشعار العامل إلا في حالات محددة. "
    "وبموجب المادة 84، يستحق العامل مكافأة نهاية الخدمة عند انتهاء عقد العمل."
)

MOCK_CITATIONS = [
    {
        "article_id": "labor-74",
        "law_name": "نظام العمل",
        "article_number": 74,
        "relevance_score": 0.92,
    },
    {
        "article_id": "labor-80",
        "law_name": "نظام العمل",
        "article_number": 80,
        "relevance_score": 0.87,
    },
    {
        "article_id": "labor-84",
        "law_name": "نظام العمل",
        "article_number": 84,
        "relevance_score": 0.81,
    },
]


class SimpleSearchAgent(MockAgentBase):
    """Basic legal Q&A — streams answer + citations, no artifacts."""

    agent_family = AgentFamily.SIMPLE_SEARCH

    async def execute(self, ctx: AgentContext) -> AsyncGenerator[dict, None]:
        await asyncio.sleep(0.3)  # Simulate thinking

        words = MOCK_RESPONSE.split(" ")
        for i, word in enumerate(words):
            token = word if i == 0 else f" {word}"
            yield {"type": "token", "text": token}
            await asyncio.sleep(0.04)

        yield {"type": "citations", "articles": MOCK_CITATIONS}

        yield {
            "type": "done",
            "usage": {
                "prompt_tokens": 800,
                "completion_tokens": len(MOCK_RESPONSE),
                "model": "mock-simple-search",
            },
        }
