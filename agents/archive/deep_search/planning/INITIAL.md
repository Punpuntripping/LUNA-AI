# Deep Search Planner - Requirements

## What This Agent Does

The deep search planner orchestrates multi-source Saudi legal research within Luna Legal AI. It receives a briefing from the router (or a follow-up message while the task is pinned), expands the legal query into targeted Arabic search queries, delegates to executor sub-agents (regulations, court cases, compliance), evaluates result quality, re-searches if results are weak, and produces a structured research report artifact. It is a **task agent** -- pinned to the conversation until the task completes or the user's request is detected as out-of-scope.

## Agent Classification

- **Type**: Tool-Enabled (planner with delegation tools + interaction tools + CRUD tools)
- **Complexity**: Complex
- **Domain**: Saudi legal research -- statutory law, judicial precedents, government compliance
- **Task Pattern**: Returns `TaskContinue` (keep searching / task stays pinned) or `TaskEnd` (done / out-of-scope / task unpins)

## Core Features

1. **Query expansion and executor delegation** -- Analyze the legal question, expand into 2-5 Arabic search queries, delegate to the appropriate executor sub-agents (`search_regulations`, `search_cases_courts`, `search_compliance`), evaluate results, and re-search with different queries if results are weak (max 3 rounds).
2. **Research report artifact creation** -- Synthesize executor results into a structured markdown report with cumulative citations, stored in the `artifacts` database table. Reports are returned in FULL every turn (never diffs). Existing reports can be loaded and edited.
3. **User interaction** -- Send mid-search status updates via `respond_to_user`, ask clarifying questions via `ask_user` (stub in v1), and return short chat summaries alongside full artifact content.
4. **Task lifecycle management** -- Return `PlannerResult` structured output that the orchestrator wraps into `TaskContinue` or `TaskEnd`. Handle out-of-scope detection, follow-up turns while pinned, and graceful error fallback.

## Technical Setup

### Model

TBD -- user will specify.

**Current assignment**: `get_agent_model("deep_search_planner")` which resolves via `agents/utils/agent_models.py` and `agents/model_registry.py`. The model key in `AGENT_MODELS` is `"deep_search_planner"` and currently maps to `"gemini-3.1-pro"`.

### Agent Configuration

| Setting | Value | Justification |
|---------|-------|---------------|
| `output_type` | `PlannerResult` | Structured result -- orchestrator wraps into `TaskContinue` or `TaskEnd` |
| `deps_type` | `SearchDeps` | Supabase client, embedding fn, case memory, user/conversation/case IDs, artifact tracking, SSE event collection |
| `instructions` | Static system prompt + 1 dynamic instruction (`inject_case_memory`) | Arabic opening line + English technical body |
| `retries` | `2` | Retry on malformed `PlannerResult` output |
| `end_strategy` | `"early"` | Planner decides when to stop via structured output, not framework |
| `run method` | `agent.iter()` with manual `.next()` loop | Required for SSE mid-run event interception and future `ask_user` pause/resume |

### Output Type

- **Type**: `PlannerResult` (Pydantic BaseModel, already defined in `agents/models.py`)
- **Fields**:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `task_done` | `bool` | (required) | `True` = wrap as `TaskEnd`, `False` = wrap as `TaskContinue` |
| `end_reason` | `Literal["completed", "out_of_scope", ""]` | `""` | Why the task ended. Empty string when `task_done=False`. |
| `answer_ar` | `str` | (required) | Short Arabic summary for chat display. Full report goes in artifact. |
| `search_summary` | `str` | `""` | Internal recap for router context -- what was searched and found. Injected into conversation history on `TaskEnd`. |
| `artifact_md` | `str` | `""` | Full markdown report content. Must be complete every turn, never a diff. |

**Orchestrator wrapping logic** (in runner's `_map_result`):
- `task_done=False` --> `TaskContinue(response=answer_ar, artifact=artifact_md)`
- `task_done=True` --> `TaskEnd(reason=end_reason or "completed", summary=search_summary, artifact=artifact_md, last_response=answer_ar)`

### Supporting Models

#### Citation (internal, defined in agent.py)

Used by the `create_report` tool for structured citation tracking.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `source_type` | `str` | (required) | `"regulation"`, `"article"`, `"section"`, `"case"`, or `"service"` |
| `ref` | `str` | (required) | Unique identifier -- `chunk_ref` or `case_ref` |
| `title` | `str` | (required) | Arabic title of the source |
| `content_snippet` | `str` | `""` | Relevant excerpt |
| `regulation_title` | `str \| None` | `None` | Parent regulation name (if article/section) |
| `article_num` | `str \| None` | `None` | Article number (if applicable) |
| `court` | `str \| None` | `None` | Court name (if case) |
| `relevance` | `str` | `""` | Why this source supports the answer |

### Required Tools

Seven tools total, registered directly on `planner_agent`. No shared toolsets -- all are specific to this agent.

#### 1. search_regulations

- **Purpose**: Search Saudi statutory and regulatory law via the regulation executor agent.
- **Decorator**: `@planner_agent.tool(retries=1, timeout=30)`
- **Parameters**: `ctx: RunContext[SearchDeps]`, `query: str` (Arabic search query)
- **Returns**: `str` (markdown summary with quality self-assessment and source references)
- **Behavior**:
  - Appends SSE status event: "Searching regulations: {query}"
  - Imports and calls `run_regulation_search(query, reg_deps)` from `agents.deep_search.executors`
  - Constructs `RegulationSearchDeps` from `ctx.deps.supabase`, `embed_regulation_query`, Jina API key, shared httpx client
  - Appends SSE status event on completion
  - On error: returns Arabic error string, does NOT raise (planner decides next step)

#### 2. search_cases_courts

- **Purpose**: Search Saudi judicial precedents and court rulings.
- **Decorator**: `@planner_agent.tool(retries=1)`
- **Parameters**: `ctx: RunContext[SearchDeps]`, `query: str` (Arabic search query)
- **Returns**: `str` (markdown summary of cases found)
- **Behavior**: Currently returns mock `MOCK_CASES_RESULT`. When real executor is built, will call the cases executor agent. Appends SSE status events before and after search.
- **Error handling**: Returns error string, does not raise.

#### 3. search_compliance

- **Purpose**: Search government services and compliance procedures.
- **Decorator**: `@planner_agent.tool(retries=1)`
- **Parameters**: `ctx: RunContext[SearchDeps]`, `query: str` (Arabic search query)
- **Returns**: `str` (markdown summary of services found)
- **Behavior**: Currently returns mock `MOCK_COMPLIANCE_RESULT`. Appends SSE status events.
- **Error handling**: Returns error string, does not raise.

#### 4. ask_user

- **Purpose**: Ask the user a clarifying question before or during search. Pauses agent execution until user responds.
- **Decorator**: `@planner_agent.tool(retries=0)`
- **Parameters**: `ctx: RunContext[SearchDeps]`, `question: str` (Arabic clarifying question)
- **Returns**: `str` (user's reply text)
- **Behavior (v1 stub)**: Appends `{"type": "ask_user", "question": question}` to `deps._sse_events`. Returns fixed Arabic message: "The user did not provide additional clarification. Continue based on available information." Real pause/resume via Redis pub/sub is a future enhancement.

#### 5. respond_to_user

- **Purpose**: Send a mid-search status update to the user. Fire-and-forget.
- **Decorator**: `@planner_agent.tool(retries=0)`
- **Parameters**: `ctx: RunContext[SearchDeps]`, `message: str` (Arabic status message)
- **Returns**: `str` (returns `"Done"`)
- **Behavior**: Appends `{"type": "status", "text": message}` to `deps._sse_events`. The orchestrator yields these to the SSE stream.

#### 6. create_report

- **Purpose**: Create or update a markdown research report artifact in the database.
- **Decorator**: `@planner_agent.tool(retries=1)`
- **Parameters**: `ctx: RunContext[SearchDeps]`, `title: str`, `content_md: str`, `citations: list[dict]`
- **Returns**: `str` (artifact_id UUID string, or Arabic error message)
- **Behavior**:
  - Validates citations through the `Citation` model (best-effort, falls back to raw dict)
  - **If `deps.artifact_id` is set**: Updates existing artifact row (title, content_md, metadata with citations, updated_at). Appends `artifact_updated` SSE event.
  - **If `deps.artifact_id` is None**: Inserts new artifact row with `user_id`, `conversation_id`, `case_id`, `agent_family="deep_search"`, `artifact_type="report"`, `is_editable=True`. Sets `deps.artifact_id` to the new ID (mutable). Appends `artifact_created` SSE event.
  - On DB error: returns Arabic error string, does not raise.

#### 7. get_previous_report

- **Purpose**: Load a previously created research report by artifact_id for editing.
- **Decorator**: `@planner_agent.tool(retries=1)`
- **Parameters**: `ctx: RunContext[SearchDeps]`, `artifact_id: str` (UUID)
- **Returns**: `str` (full markdown content, or Arabic not-found/error message)
- **Behavior**: Queries `artifacts` table filtered by `artifact_id`, `user_id`, and `deleted_at IS NULL`. Returns formatted `# {title}\n\n{content}`. Appends SSE status event on successful load.

### Dependencies (SearchDeps dataclass fields)

```python
@dataclass
class SearchDeps:
    supabase: SupabaseClient           # Supabase client for DB operations (artifacts, case_memories)
    embedding_fn: Callable[[str], Awaitable[list[float]]]  # async (str) -> list[float], from agents.utils.embeddings.embed_text
    user_id: str                       # Current user's user_id (UUID), needed for artifact creation
    conversation_id: str               # Current conversation_id (UUID), needed for artifact creation
    case_id: str | None = None         # Case context (None for general Q&A mode)
    case_memory: str | None = None     # Pre-built case memory text from case_memories table
    artifact_id: str | None = None     # Mutable -- updated by create_report tool when new artifact created
    _sse_events: list[dict] = field(default_factory=list)  # Collected SSE events, returned to orchestrator
```

**Dependency factory**: `build_search_deps()` async function constructs `SearchDeps`. Called by the orchestrator before invoking the runner.

```python
async def build_search_deps(
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    supabase: SupabaseClient,
    artifact_id: str | None = None,
) -> SearchDeps:
```

Steps:
1. If `case_id` is set, query `case_memories` table for up to 20 non-deleted rows ordered by `created_at DESC`. Format as `- [{memory_type}] {content_ar}` per row and join with newlines.
2. Import `embed_text` from `agents.utils.embeddings`.
3. Return `SearchDeps(supabase, embed_text, user_id, conversation_id, case_id, case_memory, artifact_id)`.

### External Services

- **Supabase PostgreSQL**: Artifact CRUD (`artifacts` table), case memory reads (`case_memories` table)
- **Jina Reranker API**: Used by the regulation executor (not the planner directly) for search result reranking. Accessed via `shared.config.get_settings().JINA_RERANKER_API_KEY` and a shared `httpx.AsyncClient`.
- **LLM Provider**: Via `get_agent_model("deep_search_planner")` through the model registry. Currently Google Gemini 3.1 Pro.

## System Prompt Design

### Static Baseline

Arabic opening line followed by English technical body:

```
Opening: "You are the Deep Search planner for Luna Legal AI -- a Saudi legal research platform."
```

Key sections in the static prompt:
1. **Context**: Invoked via the TASK system, receives briefing from router, pinned until done
2. **Workflow** (9 steps): Analyze -> clarify if ambiguous -> expand queries -> status update -> choose executors -> call in parallel -> evaluate results -> build report -> return result
3. **Query expansion guidelines**: Extract legal references, generate semantic variants, consider related domains
4. **User interaction guidelines**: When to use `ask_user` vs `respond_to_user`, short chat responses with full reports in artifact
5. **Budget guidelines**: Max 3 search rounds, max 5 tool calls per round, return partial results if still weak after 3 rounds
6. **Editing existing reports**: Load previous report first, merge citations, maintain structure
7. **Citation tracking**: Cumulative across all turns, complete References section
8. **Prohibitions**: No direct DB access, no fabricated content, no uncited articles, no skipping artifacts, no diffs, no exceeding 3 rounds

### Dynamic Instructions

#### inject_case_memory

- **Trigger**: `ctx.deps.case_memory` is not None
- **Content**: Injects case-specific memory context with instructions to use it for query expansion
- **When absent**: Returns empty string (no injection)

```python
@planner_agent.instructions
def inject_case_memory(ctx: RunContext[SearchDeps]) -> str:
    if ctx.deps.case_memory:
        return f"""
Case Context (from memory.md):
{ctx.deps.case_memory}

Use this context to inform your query expansion. If the case involves
specific regulations or legal domains, prioritize those in your searches.
"""
    return ""
```

### Prompt Assembly Order

1. Static baseline (always present)
2. `inject_case_memory` (conditional on case_id + memories)
3. Message history (previous turns within this task, via Pydantic AI `message_history`)
4. Current user message or briefing

## Runner Function

### Signature

```python
async def handle_deep_search_turn(
    message: str,
    deps: SearchDeps,
    task_history: list[dict] | None = None,
) -> tuple[TaskContinue | TaskEnd, list[dict]]:
```

### Parameters

| Parameter | Type | Source | Description |
|-----------|------|--------|-------------|
| `message` | `str` | Orchestrator | User's message text. On first turn: router's briefing. On follow-ups: raw user message. |
| `deps` | `SearchDeps` | `build_search_deps()` | Pre-built dependencies |
| `task_history` | `list[dict] \| None` | `TaskInfo.history` | Serialized task-scoped history `[{"role": "user"|"assistant", "content": "..."}]`. None on first turn. |

### Return Type

`tuple[TaskContinue | TaskEnd, list[dict]]`

- First element: Task result (`TaskContinue` or `TaskEnd`)
- Second element: List of SSE event dicts collected during the run

### Execution Flow

```
1. Reset deps._sse_events to []
2. Format task_history into Pydantic AI ModelMessage list (via messages_to_history)
3. Open planner_agent.iter(message, deps, message_history, usage_limits=PLANNER_LIMITS)
4. Manual .next() loop:
   a. ModelRequestNode --> model thinks, picks tools
   b. CallToolsNode   --> tools execute, append SSE events to deps._sse_events
   c. End             --> extract PlannerResult from run.result.output
5. Log usage (requests, output_tokens, tool_calls, task_done, duration)
6. Map PlannerResult to TaskContinue or TaskEnd via _map_result()
7. Save comprehensive JSON run log (input, output, model messages, usage, events)
8. Return (result, deps._sse_events)
```

### History Formatting

```python
def _format_task_history(task_history: list[dict]) -> list[ModelMessage]:
    return messages_to_history(task_history)
```

Uses `agents/utils/history.py` which converts:
- `role="user"` --> `ModelRequest` with `UserPromptPart`
- `role="assistant"` --> `ModelResponse` with `TextPart`
- Empty content rows are skipped

Task history only stores user/assistant exchange summaries, not raw tool calls. The agent re-evaluates from scratch each turn.

## SSE Event Patterns

### Event Collection Mechanism

Tools append to `deps._sse_events` (mutable list on the dataclass). The runner returns this list alongside the result. The orchestrator yields each event to the SSE stream.

### Events Emitted by Tools

| Tool | Event Type | Payload | Behavior |
|------|-----------|---------|----------|
| `respond_to_user` | `status` | `{"type": "status", "text": message}` | Fire-and-forget status update |
| `ask_user` | `ask_user` | `{"type": "ask_user", "question": question}` | Pauses flow (stub returns fixed reply in v1) |
| `create_report` (new) | `artifact_created` | `{"type": "artifact_created", "artifact_id": ..., "artifact_type": "report", "title": ...}` | New artifact created |
| `create_report` (update) | `artifact_updated` | `{"type": "artifact_updated", "artifact_id": ...}` | Existing artifact updated |
| `get_previous_report` | `status` | `{"type": "status", "text": "Loaded previous report: {title}"}` | Status update on successful load |
| `search_regulations` | `status` | Two events: searching + completed | Before and after executor call |
| `search_cases_courts` | `status` | One event: searching | Before executor call |
| `search_compliance` | `status` | One event: searching | Before executor call |

### Events Emitted by Orchestrator (not the agent)

| Event | Source | Payload |
|-------|--------|---------|
| `agent_selected` | Orchestrator | `{"agent_family": "deep_search"}` |
| `task_started` | Orchestrator | `{"task_id": ..., "task_type": "deep_search"}` |
| `token` | Orchestrator (word-by-word streaming of `response`/`last_response`) | `{"type": "token", "text": word}` |
| `task_ended` | Orchestrator on TaskEnd | `{"task_id": ..., "summary": ...}` |
| `done` | Orchestrator | Usage stats |

### Typical SSE Event Order

```
1. task_started (orchestrator)
2. status (respond_to_user -- "starting search")
3. status (search_regulations -- "searching...")
4. status (search_regulations -- "search complete")
5. status (search_cases_courts -- "searching...")
6. status (respond_to_user -- "found results, preparing report")
7. artifact_created (create_report)
8. token (orchestrator streams answer_ar word-by-word)
9. task_ended (orchestrator) OR more status events if TaskContinue
10. done (orchestrator)
```

## Integration Points

### Orchestrator Call Site

Located in `_run_pydantic_ai_task()` in `agents/orchestrator.py` (lines 366-406):

```python
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

    # Update artifact_id if agent created a new one
    if deps.artifact_id and deps.artifact_id != task.artifact_id:
        task.artifact_id = deps.artifact_id
        update_task_artifact(supabase, task.task_id, task.artifact_id)
```

### Post-Processing (handled by orchestrator, NOT the runner)

After the runner returns, the orchestrator handles:
1. Yielding collected SSE events
2. Updating `artifact_id` on task if the agent created one
3. Streaming response tokens word-by-word via SSE `token` events
4. Updating task history in DB (appends user/assistant entries)
5. If `TaskEnd`: `complete_task()`, `_inject_task_summary()`, yields `task_ended`
6. If `TaskEnd` with `reason="out_of_scope"`: re-feeds the message to the router

### Exports (__init__.py)

```python
from agents.deep_search.agent import (
    SearchDeps,
    planner_agent,
    handle_deep_search_turn,
    build_search_deps,
)

__all__ = [
    "SearchDeps",
    "planner_agent",
    "handle_deep_search_turn",
    "build_search_deps",
]
```

### Executor Sub-Agent Bridge

The planner's `search_regulations` tool imports from `agents.deep_search.executors`, which is a bridge module re-exporting from `agents.regulation_executor`:

```python
# agents/deep_search/executors/__init__.py
from agents.regulation_executor import (
    ExecutorResult,
    RegulationSearchDeps,
    run_regulation_search,
)
```

The regulation executor is a separate standalone agent at `agents/regulation_executor/` with its own deps, agent, runner, and tools. The planner constructs `RegulationSearchDeps` from its own `SearchDeps` fields + Jina config.

### DB Tables Touched

| Table | Operation | By Whom |
|-------|-----------|---------|
| `artifacts` | INSERT, UPDATE, SELECT | `create_report` tool, `get_previous_report` tool, `_get_current_artifact` helper |
| `case_memories` | SELECT | `build_search_deps()` factory |
| `task_state` | READ/WRITE | Orchestrator only (not the planner) |
| `messages` | INSERT | Orchestrator only (`_inject_task_summary`) |

### Imports Required

```python
# Standard library
from __future__ import annotations
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Awaitable, Optional

# Third-party
import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits
from pydantic_graph import End
from supabase import Client as SupabaseClient

# Luna internal
from agents.models import PlannerResult, TaskContinue, TaskEnd
from agents.utils.agent_models import get_agent_model
from agents.utils.history import messages_to_history
```

## Error Handling Strategy

### Error Fallback

On any unhandled exception during agent execution, return `TaskContinue` (NOT `TaskEnd`) with an Arabic error message. This preserves the pinned task state so the user can retry.

```python
def _error_fallback(deps: SearchDeps) -> TaskContinue:
    return TaskContinue(
        response="Sorry, an error occurred during legal research. Please try again.",  # Arabic
        artifact=_get_current_artifact(deps) or "",
    )
```

The constant: `ERROR_MSG_AR = "Sorry, an error occurred during legal research. Please try again."` (in Arabic).

### Why TaskContinue on Error

Ending the task on error would unpin the agent and discard accumulated state (artifact, history). A `TaskContinue` preserves state and lets the user retry.

### Artifact Preservation on Error

`_get_current_artifact(deps)` reads the current artifact content from the DB (via `deps.artifact_id`) so the existing report is not lost.

### Specific Error Cases

| Error | Source | Handling |
|-------|--------|----------|
| `UsageLimitExceeded` | `agent.iter()` | Caught in outer `except`. Returns `_error_fallback()`. |
| `UnexpectedModelBehavior` | Pydantic AI retries exhausted | Caught in outer `except`. Same fallback. |
| `ValidationError` | `PlannerResult` output fails validation | Pydantic AI retries up to 2 times. If all fail, caught in outer `except`. |
| Network / timeout | Model provider | Caught in outer `except`. Same fallback. |
| Tool exception | Individual tool error | Handled inside each tool (returns error string to agent, does NOT raise). Agent decides next step. |

### Edge Case: end_reason Empty on task_done=True

If the model sets `task_done=True` but forgets `end_reason`, the mapping defaults to `"completed"` rather than crashing.

## Token Budget and Usage Limits

### Hard Limits (enforced by Pydantic AI)

```python
PLANNER_LIMITS = UsageLimits(
    response_tokens_limit=10_000,
    request_limit=20,
    tool_calls_limit=25,
)
```

### Soft Limits (behavioral, enforced by system prompt)

- Maximum 3 search rounds (initial + 2 re-searches)
- Maximum 5 tool calls per round
- If after 3 rounds results are still weak, return partial results with a note

### Typical Token Budget

```
Single round (3 parallel executor calls):
  Router:     ~700 tokens (500 in / 200 out)
  Planner:    ~8,300 tokens (3 requests: read+plan, evaluate+report, synthesize)
  Executors:  ~5,400 tokens (regulation x2 + cases x1)
  TOTAL:      ~14,400 tokens

With re-search (2 rounds): ~22,000 tokens
With re-search (3 rounds, max): ~30,000 tokens
```

## Run Logging

Each turn saves a comprehensive JSON log to `agents/logs/deep_search/{log_id}.json` containing:

- `log_id`, `timestamp`, `status` (success/error)
- `input.message`, `input.task_history`
- `planner_result` (raw agent output fields)
- `result` (mapped TaskContinue/TaskEnd)
- `events` (SSE events collected during run)
- `model_messages` (full model conversation -- reasoning, tool calls, tool results)
- `usage` (requests, input_tokens, output_tokens, total_tokens, tool_calls)
- `duration_seconds`
- `error` (if applicable)

## Module Layout

```
agents/deep_search/
    __init__.py          # Re-exports: handle_deep_search_turn, build_search_deps, planner_agent, SearchDeps
    agent.py             # Agent definition, SearchDeps, Citation, tools, runner, logging (monolith)
    planning/
        INITIAL.md       # This file
    executors/
        __init__.py      # Bridge re-exports from agents.regulation_executor
```

**Note on monolith structure**: The current implementation keeps everything in `agent.py` (781 lines). The agent builder pipeline will produce separate files (`deps.py`, `agent.py`, `tools.py`, `runner.py`, `__init__.py`). The prompt-engineer, dependency-manager, tool-integrator, and validator agents should coordinate to split this correctly.

**Expected output from build pipeline**:

| File | Content |
|------|---------|
| `deps.py` | `SearchDeps` dataclass, `build_search_deps()` factory |
| `agent.py` | `planner_agent` definition, `Citation` model, `inject_case_memory` dynamic instruction, system prompt constant |
| `tools.py` | All 7 tool functions |
| `runner.py` | `handle_deep_search_turn()`, `_format_task_history()`, `_map_result()`, `_error_fallback()`, `_get_current_artifact()`, `_save_run_log()`, constants |
| `__init__.py` | Re-exports of public symbols |

## Success Criteria

- [ ] Agent produces `PlannerResult` structured output with all 5 fields
- [ ] At least one delegation tool is called for search queries
- [ ] `create_report` creates/updates artifacts in the database correctly
- [ ] `respond_to_user` appends SSE status events to `deps._sse_events`
- [ ] `ask_user` returns stub reply and appends SSE event
- [ ] `get_previous_report` loads existing artifact content
- [ ] Out-of-scope detection returns `TaskEnd(reason="out_of_scope")` without calling search tools
- [ ] Error fallback returns `TaskContinue` preserving existing artifact
- [ ] Runner returns `tuple[TaskContinue | TaskEnd, list[dict]]` matching orchestrator expectations
- [ ] `build_search_deps()` loads case memory when `case_id` is provided
- [ ] Usage limits enforce hard caps (10K response tokens, 20 requests, 25 tool calls)
- [ ] History formatting uses `messages_to_history()` from `agents/utils/history.py`
- [ ] `_map_result` handles `end_reason=""` edge case by defaulting to `"completed"`
- [ ] Run logs saved to `agents/logs/deep_search/` with complete details
- [ ] Dynamic instruction injects case memory when available, returns empty string otherwise
- [ ] The orchestrator integration remains unchanged (same call site, same return shape)

## Assumptions Made

- **ask_user is a stub**: Returns a fixed Arabic message. Real pause/resume via Redis pub/sub or WebSocket is deferred to a future wave.
- **search_cases_courts and search_compliance remain mocked**: Only `search_regulations` delegates to a real executor agent (`regulation_executor`). The other two tools return mock markdown constants.
- **Monolith split is expected**: The build pipeline will split the current 781-line `agent.py` into separate files (`deps.py`, `agent.py`, `tools.py`, `runner.py`). The public API and orchestrator integration remain identical.
- **No streaming in v1**: The manual `.next()` loop does not stream thinking tokens. Token streaming is handled by the orchestrator after the runner returns (word-by-word on the response text).
- **Shared httpx client for Jina**: A module-level `httpx.AsyncClient` is reused across calls for the Jina reranker API (used by `search_regulations`).
- **All error messages in Arabic**: Consistent with Luna's absolute rules.
- **PlannerResult is shared**: Defined in `agents/models.py`, not duplicated in this agent's code.
- **TaskContinue and TaskEnd are shared**: Defined in `agents/models.py`, used by all task agents.
- **Citations are validated best-effort**: The `create_report` tool validates citation dicts through the `Citation` model but falls back to raw dicts on validation failure.
- **Run logging is synchronous and non-blocking**: Log writes use `Path.write_text()` (sync I/O). Failures are caught and logged as warnings, never propagated.

---
Generated: 2026-03-30
Note: This is a rebuild of the existing deep_search planner agent. The agent already has a working 781-line monolith implementation. The build pipeline will restructure it into the standard multi-file layout while preserving all functionality and the orchestrator integration contract.
