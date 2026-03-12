"""
Mock RAG pipeline for end-to-end testing.
Yields Arabic tokens via AsyncGenerator to simulate real AI streaming.
Replace with real LLM calls in production.
"""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator

MOCK_RESPONSE = (
    "وفقاً لنظام العمل السعودي، المادة 74، يحق للعامل الحصول على أجره كاملاً "
    "في مواعيد استحقاقه المحددة. كما تنص المادة 80 على أنه لا يجوز لصاحب العمل "
    "فسخ العقد دون مكافأة أو إشعار العامل إلا في حالات محددة."
)

MOCK_CITATIONS = [
    {
        "article_id": "mock-1",
        "law_name": "نظام العمل",
        "article_number": 74,
        "relevance_score": 0.92,
    },
    {
        "article_id": "mock-2",
        "law_name": "نظام العمل",
        "article_number": 80,
        "relevance_score": 0.87,
    },
]


async def query(
    question: str,
    context_messages: list | None = None,
    case_context: dict | None = None,
    memories: list | None = None,
    document_summaries: list | None = None,
    model: str = "mock",
    user_id: str | None = None,
    conversation_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Mock RAG pipeline - yields events for SSE streaming."""
    await asyncio.sleep(0.5)  # Simulate thinking delay

    words = MOCK_RESPONSE.split(" ")
    for i, word in enumerate(words):
        token = word if i == 0 else f" {word}"
        yield {"type": "token", "text": token}
        await asyncio.sleep(0.05)  # 50ms between tokens

    yield {"type": "citations", "articles": MOCK_CITATIONS}

    yield {
        "type": "done",
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": len(MOCK_RESPONSE),
            "cost": 0.003,
            "latency_ms": len(words) * 50,
            "finish_reason": "stop",
            "model": "mock-model",
        },
    }
