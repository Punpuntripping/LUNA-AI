"""Base executor factory and runner for deep_search_v3.

Creates executor agents dynamically with domain-specific prompts and
per-instance dynamic instructions from the planner's focus_instruction.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModelSettings
from pydantic_ai.usage import UsageLimits
from pydantic_graph import End

from agents.utils.agent_models import get_agent_model

from ..models import ExecutorDeps, ExecutorResult
from ..prompts import (
    CASES_EXECUTOR_PROMPT,
    COMPLIANCE_EXECUTOR_PROMPT,
    REGULATIONS_EXECUTOR_PROMPT,
    build_executor_dynamic_instructions,
)

logger = logging.getLogger(__name__)

# Per executor instance limits
EXECUTOR_LIMITS = UsageLimits(
    response_tokens_limit=70_000,
    request_limit=10,
)

# Model slot mapping per domain
DOMAIN_MODEL_SLOTS = {
    "regulations": "deep_search_v3_regulations_executor",
    "cases": "deep_search_v3_cases_executor",
    "compliance": "deep_search_v3_compliance_executor",
}

# Static system prompts per domain
DOMAIN_PROMPTS = {
    "regulations": REGULATIONS_EXECUTOR_PROMPT,
    "cases": CASES_EXECUTOR_PROMPT,
    "compliance": COMPLIANCE_EXECUTOR_PROMPT,
}


def create_executor(
    domain: str,
    focus_instruction: str,
    user_context: str,
) -> Agent[ExecutorDeps, ExecutorResult]:
    """Create an executor agent for a specific domain with dynamic instructions.

    Each executor instance gets:
    1. A domain-specific static system prompt (role, scope, quality rules)
    2. Per-instance dynamic instructions from the planner's focus_instruction

    This is the key V3 pattern: the same executor type can be instantiated
    multiple times with different focus instructions.

    Args:
        domain: "regulations", "cases", or "compliance"
        focus_instruction: Arabic -- what to search for (from planner dispatch)
        user_context: Arabic -- user's personal situation

    Returns:
        Configured Agent ready to run.
    """
    if domain not in DOMAIN_MODEL_SLOTS:
        raise ValueError(f"Unknown executor domain: {domain}")

    model_slot = DOMAIN_MODEL_SLOTS[domain]
    static_prompt = DOMAIN_PROMPTS[domain]

    # Build the dynamic instruction strings for this specific instance
    dynamic_parts = build_executor_dynamic_instructions(
        focus_instruction=focus_instruction,
        user_context=user_context,
        domain=domain,
    )

    # Combine static prompt + dynamic instructions into full instructions
    full_prompt = static_prompt + "\n\n" + "\n\n".join(dynamic_parts)

    executor = Agent(
        get_agent_model(model_slot),
        output_type=ExecutorResult,
        deps_type=ExecutorDeps,
        instructions=full_prompt,
        model_settings=OpenRouterModelSettings(max_tokens=56_000),
        retries=2,
        end_strategy="early",
    )

    # Register domain-specific search tool
    _register_search_tool(executor, domain)

    return executor


def _register_search_tool(
    executor: Agent[ExecutorDeps, ExecutorResult],
    domain: str,
) -> None:
    """Register the appropriate search tool on the executor agent."""
    if domain == "regulations":
        from .regulations import register_search_regulations
        register_search_regulations(executor)
    elif domain == "cases":
        from .cases import register_search_cases
        register_search_cases(executor)
    elif domain == "compliance":
        from .compliance import register_search_compliance
        register_search_compliance(executor)


async def run_executor(
    agent: Agent[ExecutorDeps, ExecutorResult],
    message: str,
    deps: ExecutorDeps,
) -> tuple[ExecutorResult, bytes | None]:
    """Run one executor instance and return its result + model messages.

    Uses agent.iter() with manual .next() loop for SSE event interception.

    Args:
        agent: Pre-configured executor Agent from create_executor().
        message: The focus_instruction + user_context combined message.
        deps: ExecutorDeps with search infrastructure.

    Returns:
        (ExecutorResult, model_messages_json) tuple.
    """
    async with agent.iter(
        message,
        deps=deps,
        usage_limits=EXECUTOR_LIMITS,
    ) as run:
        node = run.next_node
        while not isinstance(node, End):
            node = await run.next(node)

    result: ExecutorResult = run.result.output

    # Capture usage info
    usage = run.usage()
    result.inner_usage.append({
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "tool_calls": usage.tool_calls,
    })

    # Capture model messages for logging
    model_messages_json: bytes | None = None
    try:
        model_messages_json = run.result.all_messages_json()
    except Exception:
        pass

    return result, model_messages_json
