# Deep Search Planner -- Runner Specification

## Overview

The runner is the bridge between the orchestrator and the Pydantic AI agent. It owns the `handle_deep_search_turn()` async function and the `build_search_deps()` factory. The orchestrator calls the runner; the runner drives the agent via `agent.iter()`, intercepts tool calls for SSE events, and returns `TaskContinue | TaskEnd` to the orchestrator.

This document specifies the runner's function signatures, field mapping logic, history formatting, error handling, and integration points. It is the implementation blueprint for `agents/deep_search/agent.py`.

---

## Module Layout

```
agents/deep_search/
    __init__.py          # re-exports handle_deep_search_turn, build_search_deps
    agent.py             # Agent definition, tools, runner function (this spec)
```

All public symbols are defined in `agent.py` and re-exported from `__init__.py`:

```python
# agents/deep_search/__init__.py
from agents.deep_search.agent import (
    handle_deep_search_turn,
    build_search_deps,
    planner_agent,
    SearchDeps,
    PlannerResult,
)
```

---

## Dependency Factory

### `build_search_deps()`

```python
async def build_search_deps(
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    supabase: SupabaseClient,
    artifact_id: str | None = None,
) -> SearchDeps:
```

**Purpose**: Constructs the `SearchDeps` dataclass that the agent receives via `ctx.deps`. Called by the orchestrator's `_run_pydantic_ai_task()` before invoking the runner.

**Steps**:

1. If `case_id` is set, query `case_memories` table for up to 20 non-deleted rows ordered by `created_at DESC`. Format each row as `- [{memory_type}] {content_ar}` and join with newlines. Set as `case_memory`. If no rows or no `case_id`, set `case_memory = None`.
2. Import `embed_text` from `agents.utils.embeddings` as the embedding function.
3. Return `SearchDeps(supabase, embed_text, user_id, conversation_id, case_id, case_memory, artifact_id)`.

**Note on `artifact_id`**: The `SearchDeps` dataclass includes an `artifact_id` field (mutable). The orchestrator passes the task's current `artifact_id` (from `TaskInfo.artifact_id`). Tools like `create_report` update `deps.artifact_id` when they create a new artifact. After the runner returns, the orchestrator checks if `deps.artifact_id` changed and updates the task state accordingly. This is already wired in the orchestrator at lines 399-401 of `orchestrator.py`.

**SearchDeps dataclass** (from deps.md, extended with `artifact_id`):

```python
@dataclass
class SearchDeps:
    supabase: SupabaseClient
    embedding_fn: Callable          # async (str) -> list[float]
    user_id: str
    conversation_id: str
    case_id: str | None
    case_memory: str | None
    artifact_id: str | None = None  # Mutable -- updated by create_report tool
```

---

## Runner Function

### `handle_deep_search_turn()`

```python
async def handle_deep_search_turn(
    message: str,
    deps: SearchDeps,
    task_history: list[dict] | None = None,
) -> tuple[TaskContinue | TaskEnd, list[dict]]:
```

**Parameters**:

| Parameter | Type | Source | Description |
|-----------|------|--------|-------------|
| `message` | `str` | Orchestrator | The user's message text. On first turn this is the router's briefing. On follow-ups this is the raw user message. |
| `deps` | `SearchDeps` | `build_search_deps()` | Pre-built dependencies including supabase client, embedding fn, IDs, and case memory. |
| `task_history` | `list[dict] \| None` | `TaskInfo.history` | Serialized task-scoped history from previous turns. `None` or empty list on first turn. Each entry is `{"role": "user"|"assistant", "content": "..."}`. |

**Return type**: `tuple[TaskContinue | TaskEnd, list[dict]]`

- First element: The task result, either `TaskContinue` (task stays pinned) or `TaskEnd` (task completes).
- Second element: A list of SSE event dicts collected during the run. These are mid-run events (status updates, artifact events) that the orchestrator yields to the client.

**Why a tuple?** The orchestrator's `_run_pydantic_ai_task()` (line 388-396 of `orchestrator.py`) expects this exact shape: it destructures `result, events = await handle_deep_search_turn(...)`, yields the collected events, then handles the `TaskContinue | TaskEnd` result through the shared post-processing path (streaming response tokens, updating task history, handling task completion).

---

## Agent Execution Flow

The runner drives the agent via `agent.iter()` with manual `.next()` control. This is necessary to intercept tool calls for SSE event emission and to implement the `ask_user` pause/resume pattern.

### Step-by-Step

```
1. Format task_history into Pydantic AI ModelMessage list
2. Open agent.iter() context manager
3. Manual .next() loop:
   a. ModelRequestNode  -> collect (no streaming in v1; streaming added later)
   b. CallToolsNode     -> intercept tool calls, collect SSE events
   c. End               -> extract PlannerResult
4. Map PlannerResult fields to TaskContinue or TaskEnd
5. Return (result, collected_events)
```

### Pseudocode

```python
async def handle_deep_search_turn(
    message: str,
    deps: SearchDeps,
    task_history: list[dict] | None = None,
) -> tuple[TaskContinue | TaskEnd, list[dict]]:
    from pydantic_ai.agent import End, CallToolsNode, ModelRequestNode
    from pydantic_ai.usage import UsageLimits

    # 1. Format history
    history = _format_task_history(task_history) if task_history else None

    # 2. Collected SSE events (yielded by orchestrator after runner returns)
    collected_events: list[dict] = []

    # 3. Run agent
    try:
        async with planner_agent.iter(
            message,
            deps=deps,
            message_history=history,
            usage_limits=PLANNER_LIMITS,
        ) as run:
            node = run.next_node

            while not isinstance(node, End):
                node = await run.next(node)

                # Intercept CallToolsNode results for SSE events
                if isinstance(node, CallToolsNode):
                    # Tool interception happens inside the tool functions themselves
                    # (they append to collected_events via deps or a shared list)
                    pass

        # 4. Extract result
        planner_result: PlannerResult = run.result.output

        # 5. Log usage
        usage = run.usage()
        logger.info(
            "Deep search turn — requests=%s, output_tokens=%s, tool_calls=%s, task_done=%s",
            usage.requests, usage.output_tokens, usage.tool_calls, planner_result.task_done,
        )

        # 6. Map to TaskContinue or TaskEnd
        return _map_result(planner_result), collected_events

    except Exception as e:
        logger.error("Deep search planner error: %s", e, exc_info=True)
        return _error_fallback(deps), collected_events
```

---

## History Formatting

### `_format_task_history()`

```python
def _format_task_history(
    task_history: list[dict],
) -> list[ModelMessage]:
```

**Purpose**: Converts the serialized task-scoped history (stored in `task_state.history_json`) into Pydantic AI `ModelMessage` objects that the agent receives as `message_history`.

**Input format** (from `TaskInfo.history`):

```python
[
    {"role": "user", "content": "ابحث عن أحكام الفصل التعسفي"},
    {"role": "assistant", "content": "تم إعداد التقرير. يتضمن 3 مواد و2 حكم قضائي."},
    {"role": "user", "content": "أضف المادة 83 للتقرير"},
    {"role": "assistant", "content": "تم تحديث التقرير بإضافة المادة 83."},
]
```

**Output format**: Uses the same `messages_to_history()` utility from `agents/utils/history.py` that the router uses. This ensures consistency across all agents.

```python
from agents.utils.history import messages_to_history

def _format_task_history(task_history: list[dict]) -> list[ModelMessage]:
    return messages_to_history(task_history)
```

**Behavior**:
- `role="user"` rows become `ModelRequest` with `UserPromptPart`
- `role="assistant"` rows become `ModelResponse` with `TextPart`
- Empty content rows are skipped
- Rows are expected in chronological order (oldest first)

**Why not include tool call history?** The task history stored in `task_state.history_json` only contains the user/assistant exchange summaries, not the raw Pydantic AI message objects with tool calls. This is intentional: the task history is a compact representation that survives serialization to JSON. The agent does not need to see its own previous tool calls because it re-evaluates from scratch each turn based on the current conversation context and any existing artifact.

---

## Output Model

### `PlannerResult` (already defined in `agents/models.py`)

```python
class PlannerResult(BaseModel):
    task_done: bool
    end_reason: Literal["completed", "out_of_scope", ""] = ""
    answer_ar: str
    search_summary: str = ""
    artifact_md: str = ""
```

The runner does NOT define its own output model. It uses the `PlannerResult` from `agents/models.py` which is also the agent's `output_type`.

---

## Field Mapping

### `_map_result()`

```python
def _map_result(result: PlannerResult) -> TaskContinue | TaskEnd:
```

Maps the agent's structured output to the orchestrator's task models.

**When `result.task_done` is `False`** -- return `TaskContinue`:

| PlannerResult field | TaskContinue field | Notes |
|--------------------|--------------------|-------|
| `answer_ar` | `response` | Short Arabic summary shown in chat bubble |
| `artifact_md` | `artifact` | Full markdown report (complete, not diff) |

```python
TaskContinue(
    response=result.answer_ar,
    artifact=result.artifact_md,
)
```

**When `result.task_done` is `True`** -- return `TaskEnd`:

| PlannerResult field | TaskEnd field | Notes |
|--------------------|---------------|-------|
| `end_reason` | `reason` | `"completed"` or `"out_of_scope"`. Maps directly (both models use the same literal values). |
| `search_summary` | `summary` | Internal recap for router context. Persisted in `task_state.summary` and injected into conversation messages. |
| `artifact_md` | `artifact` | Final artifact state |
| `answer_ar` | `last_response` | Final message shown in chat bubble |

```python
TaskEnd(
    reason=result.end_reason or "completed",
    summary=result.search_summary,
    artifact=result.artifact_md,
    last_response=result.answer_ar,
)
```

**Edge case -- `end_reason` is empty string when `task_done` is `True`**: Default to `"completed"`. The `PlannerResult.end_reason` field defaults to `""` for the `task_done=False` case. If the model sets `task_done=True` but forgets `end_reason`, we treat it as `"completed"` rather than crashing.

---

## SSE Event Collection

Tools emit SSE events during agent execution. Rather than yielding events directly (which would require the runner to be an async generator), events are collected into a list and returned alongside the result. The orchestrator yields them to the SSE stream.

### Event Collection Mechanism

The tools that emit SSE events (`respond_to_user`, `create_report`, `ask_user`) receive a `collected_events` list via closure or via a mutable field on `SearchDeps`. The recommended approach is to add a `_sse_events` list on `SearchDeps`:

```python
@dataclass
class SearchDeps:
    # ... existing fields ...
    _sse_events: list[dict] = field(default_factory=list)
```

Tools append to `deps._sse_events`:

| Tool | Event appended |
|------|----------------|
| `respond_to_user` | `{"type": "status", "text": message}` |
| `create_report` (new) | `{"type": "artifact_created", "artifact_id": ..., "artifact_type": "report", "title": ...}` |
| `create_report` (update) | `{"type": "artifact_updated", "artifact_id": ...}` |
| `ask_user` | `{"type": "ask_user", "question": question}` |

The runner returns `deps._sse_events` as the second element of the tuple.

### `ask_user` -- Deferred Implementation

The `ask_user` tool requires pausing agent execution until the user responds. For the initial implementation (Wave 8), `ask_user` is implemented as a **stub that returns a fixed clarification-not-available message**:

```python
@planner_agent.tool
async def ask_user(ctx: RunContext[SearchDeps], question: str) -> str:
    ctx.deps._sse_events.append({"type": "ask_user", "question": question})
    # TODO: Implement real pause/resume via Redis pub/sub or WebSocket
    return "المستخدم لم يقدم توضيحاً إضافياً. تابع بناءً على المعلومات المتاحة."
```

The planner's system prompt already instructs it to proceed with best-effort search if the user does not clarify, so this stub is safe. Real implementation requires a Redis pub/sub listener or WebSocket bridge, which is a Wave 9+ concern.

---

## Error Handling

### `_error_fallback()`

```python
def _error_fallback(deps: SearchDeps) -> TaskContinue:
```

Called when the agent raises an unhandled exception (model timeout, UsageLimits exceeded, malformed output after retries, network error, etc.).

**Strategy**: Return `TaskContinue` (not `TaskEnd`) with an Arabic error message. This keeps the task pinned so the user can retry or the orchestrator can re-attempt on the next message.

```python
def _error_fallback(deps: SearchDeps) -> TaskContinue:
    return TaskContinue(
        response="عذراً، حدث خطأ أثناء البحث القانوني. يرجى المحاولة مرة أخرى.",
        artifact=_get_current_artifact(deps) or "",
    )
```

**Why `TaskContinue`, not `TaskEnd`?** Ending the task on error would unpin the agent and discard all accumulated state (artifact, history). A `TaskContinue` with an error message preserves state and gives the user the opportunity to send another message that triggers a retry. The existing artifact (if any) is preserved.

**`_get_current_artifact()`**: Reads the current artifact content from `deps.artifact_id` if set. Returns the existing report so it is not lost on error.

```python
def _get_current_artifact(deps: SearchDeps) -> str | None:
    if not deps.artifact_id:
        return None
    try:
        result = (
            deps.supabase.table("artifacts")
            .select("content_md")
            .eq("artifact_id", deps.artifact_id)
            .maybe_single()
            .execute()
        )
        if result and result.data:
            return result.data.get("content_md", "")
    except Exception:
        pass
    return None
```

### Specific Error Cases

| Error | Source | Handling |
|-------|--------|----------|
| `UsageLimitExceeded` | `agent.iter()` | Caught in outer `except`. Returns `_error_fallback()` with current artifact preserved. |
| `UnexpectedModelBehavior` | Pydantic AI retries exhausted | Caught in outer `except`. Same fallback. |
| `ValidationError` | `PlannerResult` output fails validation | Pydantic AI retries up to 2 times (configured on agent). If all retries fail, caught in outer `except`. |
| Network / timeout | Model provider | Caught in outer `except`. Same fallback. |
| Tool exception | Individual tool error | Handled inside each tool (returns error string to agent, does NOT raise). Agent decides next step. |

---

## Usage Limits

Defined as a module-level constant:

```python
PLANNER_LIMITS = UsageLimits(
    response_tokens_limit=10_000,
    request_limit=20,
    tool_calls_limit=25,
)
```

These are hard limits enforced by Pydantic AI. Soft limits (3 search rounds, 5 tool calls per round) are behavioral, enforced by the system prompt.

---

## Integration with Orchestrator

### Call Site

The runner is called from `_run_pydantic_ai_task()` in `agents/orchestrator.py` (lines 366-406). The integration is already wired:

```python
# orchestrator.py lines 375-396 (existing code)
if task.task_type == "deep_search":
    from agents.deep_search.agent import handle_deep_search_turn, build_search_deps

    deps = await build_search_deps(
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        supabase=supabase,
        artifact_id=task.artifact_id,
    )

    result, events = await handle_deep_search_turn(
        message=question,
        deps=deps,
        task_history=task.history if task.history else None,
    )

    for event in events:
        yield event

    if deps.artifact_id and deps.artifact_id != task.artifact_id:
        task.artifact_id = deps.artifact_id
        update_task_artifact(supabase, task.task_id, task.artifact_id)
```

### Post-Processing (handled by orchestrator, not runner)

After the runner returns, the orchestrator handles:

1. **Yielding collected SSE events** (lines 394-396)
2. **Updating artifact_id on task** if the agent created a new artifact (lines 399-401)
3. **Streaming response tokens** word-by-word via SSE `token` events (lines 411-416 for `TaskContinue`, lines 430-435 for `TaskEnd`)
4. **Updating task history** with the user/assistant exchange (lines 420-422)
5. **Completing the task** on `TaskEnd` -- marking task as completed, injecting summary, emitting `task_ended` (lines 438-452)
6. **Re-routing on out-of-scope** -- if `TaskEnd.reason == "out_of_scope"`, re-feeds the message to the router (lines 458-460)

The runner does NOT handle any of these. It only produces the result and collected events.

### Orchestrator Data Flow Diagram

```
User message
    |
    v
orchestrator.handle_message()
    |
    v
get_active_task()  -->  TaskInfo { task_type: "deep_search", history, artifact_id }
    |
    v
_run_task()
    |
    v
_run_pydantic_ai_task()
    |
    +-- build_search_deps(user_id, conv_id, case_id, supabase, artifact_id)
    |       |
    |       v
    |   SearchDeps { supabase, embed_fn, ids, case_memory, artifact_id, _sse_events }
    |
    +-- handle_deep_search_turn(message, deps, task_history)
    |       |
    |       v
    |   _format_task_history(task_history) --> list[ModelMessage]
    |       |
    |       v
    |   planner_agent.iter(message, deps, message_history, usage_limits)
    |       |
    |       +-- [ModelRequestNode] --> model thinks, picks tools
    |       +-- [CallToolsNode]   --> tools run, append to deps._sse_events
    |       +-- [End]             --> PlannerResult extracted
    |       |
    |       v
    |   _map_result(PlannerResult) --> TaskContinue | TaskEnd
    |       |
    |       v
    |   return (TaskContinue|TaskEnd, deps._sse_events)
    |
    v
orchestrator post-processing:
    +-- yield collected SSE events
    +-- update artifact_id if changed
    +-- stream response tokens via SSE
    +-- update task_history in DB
    +-- if TaskEnd: complete_task(), inject_summary(), yield task_ended
    +-- if TaskEnd + out_of_scope: re-route to router
```

---

## Module Dependencies

### Imports

```python
# Standard library
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Callable

# Third-party
from pydantic_ai import Agent, RunContext
from pydantic_ai.agent import End, CallToolsNode, ModelRequestNode
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits
from supabase import Client as SupabaseClient

# Luna internal
from agents.models import PlannerResult, TaskContinue, TaskEnd
from agents.utils.agent_models import get_agent_model
from agents.utils.history import messages_to_history
from agents.base.artifact import create_agent_artifact
```

---

## Constants

```python
PLANNER_LIMITS = UsageLimits(
    response_tokens_limit=10_000,
    request_limit=20,
    tool_calls_limit=25,
)

ERROR_MSG_AR = "عذراً، حدث خطأ أثناء البحث القانوني. يرجى المحاولة مرة أخرى."
```

---

## Checklist

- [x] Runner function signature matches orchestrator call site (lines 388-392 of `orchestrator.py`)
- [x] Return type is `tuple[TaskContinue | TaskEnd, list[dict]]` as orchestrator expects
- [x] `build_search_deps()` signature matches orchestrator call site (lines 379-385)
- [x] History formatting reuses `messages_to_history()` from `agents/utils/history.py`
- [x] `PlannerResult` from `agents/models.py` is the agent's output_type (no duplication)
- [x] Field mapping covers all `PlannerResult` -> `TaskContinue` and `PlannerResult` -> `TaskEnd` paths
- [x] `end_reason` empty-string edge case handled (defaults to `"completed"`)
- [x] Error fallback returns `TaskContinue` to preserve task state
- [x] SSE events collected via `deps._sse_events` and returned in tuple
- [x] `ask_user` deferred with safe stub
- [x] Usage limits defined as module constant, passed to `agent.iter()`
- [x] No code that duplicates orchestrator responsibilities (token streaming, task completion, history persistence)
