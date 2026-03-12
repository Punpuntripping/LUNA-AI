"""BaseAgent protocol and MockAgentBase with default plan/reflect."""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Protocol, runtime_checkable

from shared.types import AgentFamily, AgentContext


@runtime_checkable
class BaseAgent(Protocol):
    """Protocol all agents must implement."""
    agent_family: AgentFamily

    async def execute(self, ctx: AgentContext) -> AsyncGenerator[dict, None]:
        """Main execution — yields SSE events (token, citations, artifact_created, done)."""
        ...

    async def plan(self, ctx: AgentContext) -> AsyncGenerator[dict, None]:
        """Plan step — yields planning tokens before main execution."""
        ...

    async def reflect(self, ctx: AgentContext) -> AsyncGenerator[dict, None]:
        """Reflection step — yields analysis tokens."""
        ...


class MockAgentBase:
    """Base class with default mock plan/reflect implementations."""

    async def plan(self, ctx: AgentContext) -> AsyncGenerator[dict, None]:
        text = "خطة العمل:\n1. تحليل السؤال\n2. البحث في الأنظمة\n3. إعداد الإجابة\n"
        for word in text.split():
            yield {"type": "token", "text": word + " "}
            await asyncio.sleep(0.03)
        yield {"type": "done", "usage": {"prompt_tokens": 0, "completion_tokens": 0, "model": "mock"}}

    async def reflect(self, ctx: AgentContext) -> AsyncGenerator[dict, None]:
        text = "تأمل:\n- هل السؤال واضح؟\n- ما هي الافتراضات؟\n- ما هي المصادر المطلوبة؟\n"
        for word in text.split():
            yield {"type": "token", "text": word + " "}
            await asyncio.sleep(0.03)
        yield {"type": "done", "usage": {"prompt_tokens": 0, "completion_tokens": 0, "model": "mock"}}
