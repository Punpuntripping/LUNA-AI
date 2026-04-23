# Router Agent — Runner Specification

## Run Method

| Setting | Value | Justification |
|---------|-------|---------------|
| Method | `agent.run()` | Router decisions are fast and short — no streaming needed. The orchestrator fake-streams ChatResponse word-by-word for UX. OpenTask returns are consumed immediately by the orchestrator. |
| UsageLimits | `UsageLimits(response_tokens_limit=2000, request_limit=5, tool_calls_limit=3)` | Router outputs are short (ChatResponse.message or OpenTask.briefing). 5 request limit allows for retries + one get_artifact call. 3 tool call limit prevents runaway artifact reads. |

## Output Model

The router uses a union output type. Both models already exist in `agents/models.py`.

```python
class ChatResponse(BaseModel):
    """Router responds directly to the user."""
    type: Literal["chat"] = "chat"
    message: str = Field(description="Response text to show the user")


class OpenTask(BaseModel):
    """Router opens a specialist task."""
    type: Literal["task"] = "task"
    task_type: Literal["deep_search", "end_services", "extraction"] = Field(
        description="Which task type to open"
    )
    briefing: str = Field(
        description="Context summary for the task agent. Must include: what the user wants, "
        "relevant conversation context, any specific requirements mentioned."
    )
    artifact_id: str | None = Field(
        default=None,
        description="If editing an existing artifact, its UUID. None for new tasks."
    )
```

The `type` field acts as a Literal discriminator, enabling Pydantic AI to validate which branch the model chose.

## Runner Function Signature

```python
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
```

This signature matches the existing call site in `orchestrator.py` `_route()` method. The runner constructs `RouterDeps` internally and passes it to `agent.run()`.

## Runner Function Implementation Spec

```python
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
        logger.error("Router error: %s", e, exc_info=True)
        # Fallback: return a safe ChatResponse so the user sees something
        return ChatResponse(
            message="عذراً، حدث خطأ أثناء معالجة رسالتك. يرجى المحاولة مرة أخرى."
        )
```

## Error Handling

- **Validation error (malformed output)**: Pydantic AI retries once (retries=1). If both attempts fail, exception is caught and fallback ChatResponse returned.
- **UsageLimitExceeded**: Caught by the except block, returns fallback ChatResponse.
- **Tool error (get_artifact fails)**: The tool returns an Arabic error string, which the model sees and can incorporate into its ChatResponse. The run does not fail.
- **Network/model error**: Caught by the except block, returns fallback ChatResponse with Arabic error message.

## History Handling

The router receives the full conversation history as `message_history` from the orchestrator. This history:

1. Is loaded from the `messages` table by the orchestrator
2. Is converted to Pydantic AI `ModelMessage` list via `messages_to_history()`
3. Includes user messages, router responses, and injected task summaries
4. Is passed directly to `agent.run(message_history=...)` — Pydantic AI handles the rest

The router does NOT format or modify the history. The `instructions` approach (not `system_prompt`) means the router's own instructions are NOT included in shared message history, which is correct since the router's instructions should not leak to task agents.

## No SSE Events

The router does not collect or emit SSE events. It returns a simple `ChatResponse | OpenTask` value. The orchestrator handles all SSE event emission:

- For ChatResponse: orchestrator fake-streams tokens
- For OpenTask: orchestrator creates task, emits task_started, runs task agent

This keeps the router agent pure — it makes a decision and returns it.
