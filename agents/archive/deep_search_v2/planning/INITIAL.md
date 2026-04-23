# Deep Search V2 (Revised) - Requirements

## What This Agent Does

Deep Search V2 (Revised) replaces the current flat `pydantic_graph` state machine with a **hierarchical supervisor pattern**: a PlanAgent (pydantic_ai Agent with tools) sits above a reusable Search Loop (pydantic_graph containing QueryExpander + SearchNode + Aggregator + ReportNode). The PlanAgent orchestrates 1-3 loop invocations via tools, writes chat responses, and handles report editing. This reduces per-question LLM calls to 3-7 while giving the supervisor full control over strategy, user communication, and multi-turn task state.

**This is a complete fresh build** in `agents/deep_search_v2/`. The current flat-graph implementation is replaced entirely.

## Agent Classification

- **Type**: Hybrid -- Supervisor Agent (pydantic_ai Agent with tools) + Workflow (pydantic_graph inner loop)
- **Complexity**: Complex
- **Domain**: Saudi legal research -- Arabic-first, multi-source (regulations, cases, compliance)

## Architecture Overview

```
                        PlanAgent (supervisor, pydantic_ai Agent)
                       /    |        |        |            \
                      /     |        |        |             \
                ask_user  quick   invoke    update_report   out_of_scope
                (tool)    search  _loop(s)  (tool, edit)    -> router
                          (tool)  (tool)
                          [STUB]    |
                              +-----+------------------------------+
                              |  Search Loop (pydantic_graph)       |
                              |                                     |
                              |  ExpanderNode (1 LLM)               |
                              |       |                             |
                              |  SearchNode (0 LLM)                 |
                              |       |                             |
                              |  AggregateNode (1 LLM)              |
                              |    | weak -> ExpanderNode            |
                              |    | sufficient -> ReportNode        |
                              |  ReportNode (0 LLM, new only)       |
                              +-------------------------------------+
                                    |
                              LoopResult (dynamic instruction)
                                    |
                              PlanAgent writes chat response
                              (or calls update_report if editing)
```

### Three LLM Agents

| Agent | Where | LLM Calls | Output Type | Tools |
|-------|-------|-----------|-------------|-------|
| **PlanAgent** | Above the loop (supervisor) | 1-3 per turn | `PlannerResult` (from `agents/models.py`) | `ask_user`, `quick_search` (stub), `invoke_search_loop`, `update_report` |
| **QueryExpander** | Inside the loop (ExpanderNode) | 1 per loop round | `ExpanderOutput` (structured) | None |
| **Aggregator** | Inside the loop (AggregateNode) | 1 per loop round | `AggregatorOutput` (structured) | None |

### Who Writes What

| Output | Written By | How |
|--------|-----------|-----|
| **New report artifact** | ReportNode (inside loop) | Programmatic: wraps Aggregator's `synthesis_md` + citations, inserts into DB |
| **Updated report artifact** | PlanAgent | `update_report` tool -- PlanAgent has full context (old report + new results) to judge what to merge |
| **Chat response** | PlanAgent | Receives LoopResult as dynamic instruction, writes conversational `answer_ar` |
| **SSE status updates** | Graph nodes directly | Append to loop state `sse_events`, propagated to PlanAgent deps |

## Core Features (MVP)

1. **PlanAgent as supervisor**: Receives user question, decides strategy (search, clarify, out-of-scope), invokes 1-3 search loops, writes final chat response and report
2. **Reusable Search Loop**: pydantic_graph with ExpanderNode -> SearchNode -> AggregateNode -> ReportNode, supporting selective re-search for weak axes
3. **Dual artifact handling**: New reports created by ReportNode inside loop; existing reports edited by PlanAgent's `update_report` tool
4. **Orchestrator-compatible**: Exports `handle_deep_search_turn()` and `build_search_deps()` with identical signatures to current v2/v1

## Technical Setup

### Models

**Model slots and assignments for `agent_models.py`:**

All models are already registered in `agents/model_registry.py`.

| Agent Slot | Primary | Alt 1 | Alt 2 | Fallback |
|-----------|---------|-------|-------|----------|
| `deep_search_v2_plan_agent` | `or-gemini-3.1-pro` | `or-minimax-m2.7` | `or-mimo-v2-pro` | `gemini-2.5-flash` |
| `deep_search_v2_expander` | `or-minimax-m2.7` | `or-mimo-v2-pro` | `or-qwen3.5-397b` | `gemini-2.5-flash` |
| `deep_search_v2_aggregator` | `or-minimax-m2.7` | `or-mimo-v2-pro` | `or-qwen3.5-397b` | `gemini-2.5-flash` |

**Registry keys (all present in `agents/model_registry.py`):**
- `or-gemini-3.1-pro` → `google/gemini-3.1-pro-preview` (OpenRouter)
- `or-minimax-m2.7` → `minimax/minimax-m2.7` (OpenRouter)
- `or-mimo-v2-pro` → `xiaomi/mimo-v2-pro` (OpenRouter)
- `or-qwen3.5-397b` → `qwen/qwen3.5-397b-a17b` (OpenRouter)

### Output Types

#### PlanAgent Output: `PlannerResult` (reuses `agents/models.py`)

```python
class PlannerResult(BaseModel):
    task_done: bool                    # True when task is complete
    end_reason: Literal["completed", "out_of_scope", "pending"]
    answer_ar: str                     # Chat response for user (Arabic)
    search_summary: str                # Internal summary for router context
    artifact_md: str                   # Full report content (from loop or update)
```

**Mapping to orchestrator models (same as v1):**
- `task_done=True` + `end_reason="out_of_scope"` -> `TaskEnd(reason="out_of_scope")`
- `task_done=True` + `end_reason="completed"` -> `TaskEnd(reason="completed")`
- `task_done=False` -> `TaskContinue(response=answer_ar, artifact=artifact_md)`

#### QueryExpander Output: `ExpanderOutput` (new)

```python
class ExpanderOutput(BaseModel):
    queries: list[SearchQuery]   # 2-4 search queries
    status_message: str          # Arabic status update for user (SSE)
```

#### SearchQuery (nested, reused from current v2)

```python
class SearchQuery(BaseModel):
    tool: Literal["regulations", "cases", "compliance"]
    query: str                   # Arabic search query
    rationale: str               # Internal rationale (logs only)
```

#### AggregatorOutput (reused from current v2, no changes)

```python
class AggregatorOutput(BaseModel):
    sufficient: bool
    coverage_assessment: str
    weak_axes: list[WeakAxis]
    strong_results_summary: str
    synthesis_md: str            # Arabic legal analysis markdown
    answer_summary: str          # 1-3 sentence Arabic summary
    citations: list[dict]        # Structured citation dicts
```

#### WeakAxis (reused)

```python
class WeakAxis(BaseModel):
    tool: Literal["regulations", "cases", "compliance"]
    reason: str
    suggested_query: str
```

#### SearchResult (dataclass, reused)

```python
@dataclass
class SearchResult:
    tool: str
    query: str
    raw_markdown: str
    result_count: int
    is_mock: bool
```

#### LoopResult (NEW -- Loop -> PlanAgent)

```python
class LoopResult(BaseModel):
    sub_question: str            # The sub-question that was searched
    report_md: str               # Full report markdown from ReportNode
    artifact_id: str | None      # DB artifact_id if new report was inserted
    answer_summary: str          # From Aggregator
    citations: list[dict]        # From Aggregator
    rounds_used: int             # How many loop rounds executed
```

#### LoopState (dataclass -- mutable graph state)

```python
@dataclass
class LoopState:
    sub_question: str
    context: str                 # Context from PlanAgent
    expander_output: ExpanderOutput | None = None
    all_search_results: list[SearchResult] = field(default_factory=list)
    strong_results: list[SearchResult] = field(default_factory=list)
    aggregator_output: AggregatorOutput | None = None
    weak_axes: list[WeakAxis] = field(default_factory=list)
    round_count: int = 0
    sse_events: list[dict] = field(default_factory=list)
```

#### Citation (BaseModel, for report builder validation -- reused)

```python
class Citation(BaseModel):
    source_type: str
    ref: str
    title: str
    content_snippet: str = ""
    regulation_title: str | None = None
    article_num: str | None = None
    court: str | None = None
    relevance: str = ""
```

### Required Tools (PlanAgent)

1. **`invoke_search_loop(sub_question, context)`**: Runs the full Search Loop pydantic_graph. Returns `LoopResult`. PlanAgent can call this 1-3 times per turn.
2. **`update_report(content_md, citations)`**: Updates existing artifact in DB. Used when editing an existing report (artifact_id exists). Programmatic DB write, 0 LLM cost.
3. **`ask_user(question)`**: Emits SSE `ask_user` event for clarification. Stub -- returns fixed reply. Rare usage.
4. **`quick_search(query)`**: **OUT OF SCOPE** -- write signature/docstring only, raise `NotImplementedError`. Direct lookup without loop for simple queries. Future enhancement.

### Dependencies

#### DeepSearchDeps (top-level, passed to PlanAgent and through to loop)

```python
@dataclass
class DeepSearchDeps:
    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    user_id: str
    conversation_id: str
    case_id: str | None = None
    artifact_id: str | None = None      # Mutable -- updated by ReportNode / update_report
    jina_api_key: str = ""
    http_client: httpx.AsyncClient | None = None
    mock_results: dict | None = None    # For testing
    _sse_events: list[dict] = field(default_factory=list)  # Collected across all loops
```

This is identical in shape to the current `DeepSearchDeps` but adds `_sse_events` at the top level (since PlanAgent is the outer agent, not a graph node).

#### LoopDeps (passed into the search loop graph)

```python
@dataclass
class LoopDeps:
    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    user_id: str
    conversation_id: str
    case_id: str | None = None
    artifact_id: str | None = None      # Mutable
    jina_api_key: str = ""
    http_client: httpx.AsyncClient | None = None
    mock_results: dict | None = None
    is_edit_mode: bool = False          # When True, ReportNode skips DB insert
```

Alternatively, `LoopDeps` could simply be the same `DeepSearchDeps` instance passed through. The implementation agent should decide whether a separate type adds clarity or if reusing `DeepSearchDeps` is cleaner. The key constraint: `is_edit_mode` must be communicated so ReportNode knows to skip DB insert.

### External Services

- **Supabase**: Artifact CRUD (artifacts table), case memory lookup (case_memories table)
- **Jina Reranker API**: Used by regulation search pipeline (`run_retrieval_pipeline`)
- **Embedding API**: `embed_regulation_query` from `agents/utils/embeddings.py`
- **OpenRouter / Google / MiniMax APIs**: LLM calls for PlanAgent, QueryExpander, Aggregator (via `model_registry.py`)

## Prompt Design Requirements

### PlanAgent Prompt

The PlanAgent prompt should focus on:
- **Strategy**: Analyze user question, decide number of sub-questions for `invoke_search_loop`
- **Tool selection**: When to search vs clarify vs reject as out-of-scope
- **User communication**: `answer_ar` should be conversational Arabic, referencing the report
- **Report editing**: When `artifact_id` exists, PlanAgent loads old report (via dynamic instruction), invokes loop for new results, calls `update_report` to merge
- **Multi-turn awareness**: Task history injected as dynamic instruction

Dynamic instructions to inject (Arabic headers):
1. `سياق القضية: ...` -- case memory (when case_id exists)
2. `تقرير سابق: ...` -- previous report content, truncated ~4000 chars (when artifact_id exists)
3. `سجل المحادثة السابقة: ...` -- task history (when multi-turn)
4. `نتائج البحث: ...` -- LoopResult injected after each `invoke_search_loop` completes (before PlanAgent writes final response)

### QueryExpander Prompt

The QueryExpander prompt should focus on Arabic legal query expansion. Critical rules to preserve from v1:

```
قواعد صياغة الاستعلامات:
1. صياغة واضحة مباشرة بمصطلحات قانونية عامة بالعربية
2. ركّز على ما يمكن أن يظهر في نص قانوني
3. لا تضف أسماء أنظمة أو جهات أو أرقام مواد لم يذكرها المستخدم
4. إذا ذكر المستخدم نظاماً أو مادة بعينها، استخدمها كما هي
5. أضف "قانون محتمل" أو "جهة محتملة" كتلميح لنفسك فقط في rationale
6. السبب: ذكر أسماء محددة يُضلل محرك البحث الدلالي
```

Round 2+ behavior: QueryExpander receives `weak_axes` as dynamic instruction and generates queries ONLY for weak tools. Must not re-query tools with strong results.

### Aggregator Prompt

The Aggregator prompt should focus on:
- Independent evaluation of each search tool's results (strong vs weak)
- Coverage threshold: `sufficient=True` when ~80%+ coverage
- Synthesis: Combine ALL accumulated results into Arabic legal analysis markdown
- Citations: Extract structured citation dicts from raw results
- When insufficient: Identify weak tools, provide specific `suggested_query` for each

## Search Loop (pydantic_graph) Specification

### Nodes

#### ExpanderNode (1 LLM call)
- Creates a `QueryExpander` pydantic_ai Agent with `ExpanderOutput` output_type
- Input via user message: `sub_question` + `context` from LoopState
- Round 2+: injects `weak_axes` as dynamic instruction -> generates queries ONLY for weak tools
- Always transitions to SearchNode

#### SearchNode (0 LLM calls)
- Reads `LoopState.expander_output.queries`
- Executes all queries concurrently via `asyncio.gather` using `run_search_pipeline()`
  - `regulations`: wraps `run_retrieval_pipeline()` from `agents/regulation_executor/tools.py`
  - `cases`: mock (returns hardcoded Arabic results)
  - `compliance`: mock (returns hardcoded Arabic results)
- Appends results to `LoopState.all_search_results`
- Always transitions to AggregateNode

#### AggregateNode (1 LLM call)
- Creates an `Aggregator` pydantic_ai Agent with `AggregatorOutput` output_type
- Input via user message: `sub_question` + ALL accumulated results (strong + new)
- Evaluates each tool independently
- Routing logic:
  - `sufficient=False` AND `round_count < MAX_ROUNDS` -> ExpanderNode (loop back)
  - `sufficient=True` OR `round_count >= MAX_ROUNDS` -> ReportNode
- On loop back: moves strong results from `all_search_results` to `strong_results`, clears weak from `all_search_results`

#### ReportNode (0 LLM calls)
- Wraps aggregator's `synthesis_md` + citations into report markdown using `build_report()`
- **New report mode** (no artifact_id or not `is_edit_mode`): inserts artifact into DB, sets `deps.artifact_id`
- **Edit mode** (`is_edit_mode=True`): does NOT insert into DB -- returns `LoopResult` to PlanAgent, who calls `update_report`
- Returns `End(LoopResult(...))`

### Loop Constants
- `MAX_ROUNDS = 3` (max loop iterations before forced completion)
- Queries per round: 2-4

### Loop Entry Point

```python
async def run_search_loop(
    sub_question: str,
    context: str,
    deps: DeepSearchDeps,  # or LoopDeps
    is_edit_mode: bool = False,
) -> LoopResult:
    """Run the complete search loop and return LoopResult."""
```

This is what `invoke_search_loop` tool calls internally.

## PlanAgent Tool Implementations

### invoke_search_loop

```python
async def invoke_search_loop(
    ctx: RunContext[DeepSearchDeps],
    sub_question: str,
    context: str,
) -> str:
    """Run the full Search Loop for a sub-question. Returns summary.

    Can be called 1-3 times per turn. Each invocation runs the full
    ExpanderNode -> SearchNode -> AggregateNode -> ReportNode loop.

    Args:
        sub_question: Focused legal sub-question to research (Arabic).
        context: Additional context from the user question or prior results.
    """
```

Implementation: calls `run_search_loop()`, stores `LoopResult` on deps for dynamic instruction injection, returns serialized summary string.

### update_report

```python
async def update_report(
    ctx: RunContext[DeepSearchDeps],
    content_md: str,
    citations: list[dict],
) -> str:
    """Update an existing report artifact in the database.

    Used when editing mode is active (artifact_id exists on deps).
    Provide FULL updated content, not a diff.

    Args:
        content_md: Complete updated markdown report.
        citations: Complete updated citation list.
    """
```

Implementation: Updates `artifacts` table via Supabase. Emits `artifact_updated` SSE event. Returns confirmation string.

### ask_user

```python
async def ask_user(
    ctx: RunContext[DeepSearchDeps],
    question: str,
) -> str:
    """Ask the user a clarifying question.

    Emits SSE ask_user event. Currently a stub that returns a fixed reply.
    Use only when the question is genuinely ambiguous.

    Args:
        question: Arabic clarifying question for the user.
    """
```

### quick_search (OUT OF SCOPE)

```python
async def quick_search(
    ctx: RunContext[DeepSearchDeps],
    query: str,
) -> str:
    """Direct lookup without running the full search loop.

    OUT OF SCOPE -- stub only. Raises NotImplementedError.

    Args:
        query: Arabic search query for direct lookup.
    """
    raise NotImplementedError("quick_search is not implemented yet")
```

## Entry Points (Orchestrator Contract)

The two public functions must maintain **identical signatures** to the current implementation so the orchestrator can switch without changes:

```python
async def handle_deep_search_turn(
    message: str,
    deps: DeepSearchDeps,
    task_history: list[dict] | None = None,
) -> tuple[TaskContinue | TaskEnd, list[dict]]:
    """Run one turn of deep search. Called by orchestrator."""

async def build_search_deps(
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    supabase: SupabaseClient,
    artifact_id: str | None = None,
) -> DeepSearchDeps:
    """Build deps for a turn. Pre-fetches case memory and previous report."""
```

### handle_deep_search_turn Implementation

1. Build `DeepSearchDeps`, reset `_sse_events`
2. Format task_history for dynamic instruction injection
3. Pre-fetch case memory and previous report content (via `build_search_deps`)
4. Create PlanAgent with dynamic instructions (case memory, previous report, task history)
5. Run PlanAgent via `agent.iter()` with manual `.next()` loop (for SSE event interception)
6. Map `PlannerResult` to `TaskContinue | TaskEnd` (same logic as current v1/v2)
7. Collect SSE events from deps
8. Save run log via `save_run_log()`
9. Return `(mapped_result, sse_events)`

### PlanAgent Flow per Turn

1. PlanAgent receives user message (clean question)
2. Dynamic instructions inject: case memory, previous report, task history
3. PlanAgent decides strategy:
   - **Out of scope** -> returns `PlannerResult(task_done=True, end_reason="out_of_scope")`
   - **Needs clarification** -> calls `ask_user` tool, continues with fixed reply
   - **Search needed** -> calls `invoke_search_loop` 1-3 times
4. After each `invoke_search_loop` returns, its `LoopResult` is injected as dynamic instruction context
5. PlanAgent writes final `answer_ar` (conversational Arabic referencing the report)
6. If editing: PlanAgent calls `update_report` to merge old + new content
7. PlanAgent sets `task_done` and `end_reason`

## File Structure

```
agents/deep_search_v2/
    __init__.py              # Exports: handle_deep_search_turn, build_search_deps, DeepSearchDeps
    models.py                # ALL models: PlannerResult import, ExpanderOutput, SearchQuery,
                             #   AggregatorOutput, WeakAxis, LoopResult, SearchResult,
                             #   Citation, LoopState, DeepSearchDeps
    prompts.py               # PlanAgent prompt, QueryExpander prompt, Aggregator prompt,
                             #   dynamic instruction builders
    search_pipeline.py       # run_search_pipeline() -- wraps regulation executor + mocks
    report_builder.py        # build_report() -- pure Python markdown builder
    plan_agent.py            # PlanAgent definition + tools (invoke_search_loop, update_report,
                             #   ask_user, quick_search stub)
    loop/
        __init__.py          # Exports: run_search_loop
        expander.py          # QueryExpander agent definition
        aggregator.py        # Aggregator agent definition
        nodes.py             # ExpanderNode, SearchNode, AggregateNode, ReportNode
        graph.py             # Loop graph assembly, run_search_loop() function
    graph.py                 # Top-level: handle_deep_search_turn(), build_search_deps()
    logger.py                # save_run_log() -- JSON log writer
    cli.py                   # CLI for testing
```

## Reusable Components from Current Implementation

The following can be carried over with minimal changes:

| Component | Current Location | Changes Needed |
|-----------|-----------------|----------------|
| `SearchResult` dataclass | `models.py` | None |
| `SearchQuery` model | `models.py` | None |
| `WeakAxis` model | `models.py` | None |
| `AggregatorOutput` model | `models.py` | None |
| `Citation` model | `models.py` | None |
| `build_report()` | `report_builder.py` | None |
| `run_search_pipeline()` | `search_pipeline.py` | None -- same regulation/mock dispatch |
| `save_run_log()` | `logger.py` | Update to log PlanAgent usage + loop details |
| Mock results (cases, compliance) | `search_pipeline.py` | None |
| Aggregator system prompt | `prompts.py` | Minor: remove update_report tool reference (Aggregator no longer has it) |
| `build_search_deps()` | `graph.py` | Add `_sse_events` initialization |

### Components That Change Significantly

| Component | What Changes |
|-----------|-------------|
| `PlannerOutput` model | **Removed** -- PlanAgent now uses `PlannerResult` from `agents/models.py` |
| `DeepSearchState` | **Replaced** by `LoopState` (scoped to inner loop) -- PlanAgent manages its own state |
| `PlanNode` | **Replaced** by PlanAgent (full pydantic_ai Agent, not a graph node) |
| `AggregateNode` | Moved inside `loop/nodes.py`, loses `update_report` tool (PlanAgent handles editing now) |
| `graph.py` entry point | Rewired: creates PlanAgent, runs via `agent.iter()`, maps result |

### New Components

| Component | Purpose |
|-----------|---------|
| `ExpanderOutput` model | New structured output for QueryExpander |
| `LoopResult` model | New -- carries loop results back to PlanAgent |
| `LoopState` dataclass | New -- mutable state for inner loop only |
| `plan_agent.py` | New -- PlanAgent definition with 4 tools |
| `loop/expander.py` | New -- QueryExpander agent (extracted from PlanNode logic) |
| `loop/aggregator.py` | New -- Aggregator agent (extracted from AggregateNode agent creation) |
| `loop/graph.py` | New -- inner loop graph assembly + `run_search_loop()` |
| `loop/nodes.py` | New -- ExpanderNode, SearchNode, AggregateNode, ReportNode (inner loop versions) |

## Usage Limits

```python
from pydantic_ai.usage import UsageLimits

# PlanAgent (supervisor) -- generous limits for tool calls
PLAN_AGENT_LIMITS = UsageLimits(
    response_tokens_limit=8_000,
    request_limit=15,        # Allows multiple invoke_search_loop calls
    tool_calls_limit=10,     # 3 loops + update_report + ask_user
)

# QueryExpander -- tight limits (structured output only)
EXPANDER_LIMITS = UsageLimits(
    response_tokens_limit=2_000,
    request_limit=3,
)

# Aggregator -- moderate limits (synthesis can be long)
AGGREGATOR_LIMITS = UsageLimits(
    response_tokens_limit=6_000,
    request_limit=3,
)
```

## SSE Event Flow

Events are collected in `DeepSearchDeps._sse_events` at the top level. Inner loop nodes append to `LoopState.sse_events`, which are transferred to `DeepSearchDeps._sse_events` after each `run_search_loop()` call.

Expected SSE event types:
- `{"type": "status", "text": "..."}` -- progress updates (Arabic)
- `{"type": "ask_user", "question": "..."}` -- clarification request
- `{"type": "artifact_created", "artifact_id": "...", "artifact_type": "report", "title": "..."}`
- `{"type": "artifact_updated", "artifact_id": "..."}`

## Success Criteria

- [ ] PlanAgent can analyze a legal question and invoke 1-3 search loops
- [ ] QueryExpander produces 2-4 SearchQuery items with correct tool assignments
- [ ] SearchNode executes queries concurrently (regulations via real pipeline, cases/compliance via mocks)
- [ ] Aggregator evaluates sufficiency and identifies weak axes for re-search
- [ ] Inner loop correctly loops back to ExpanderNode on weak results (up to MAX_ROUNDS=3)
- [ ] ReportNode creates artifact in DB for new reports
- [ ] PlanAgent's `update_report` tool correctly updates existing artifacts
- [ ] `handle_deep_search_turn()` returns `TaskContinue | TaskEnd` compatible with orchestrator
- [ ] `build_search_deps()` pre-fetches case memory and previous report
- [ ] SSE events propagate correctly from inner loop to outer PlanAgent to orchestrator
- [ ] Out-of-scope questions are rejected with `TaskEnd(reason="out_of_scope")`
- [ ] Error fallback returns `TaskContinue` with Arabic error message, preserving existing artifact
- [ ] Run logs capture PlanAgent usage, loop rounds, search results, and timing
- [ ] All 3 LLM agents work with their assigned models from `agent_models.py`

## agent_models.py Updates Required

The current `agents/utils/agent_models.py` has:
```python
"deep_search_v2_planner":    "or-gemini-3.1-pro",
"deep_search_v2_aggregator": "or-minimax-m2.7",
```

Needs to be updated to:
```python
"deep_search_v2_plan_agent": "or-gemini-3.1-pro",     # PlanAgent (supervisor)
"deep_search_v2_expander":   "or-minimax-m2.7",        # QueryExpander (inner loop)
"deep_search_v2_aggregator": "or-minimax-m2.7",        # Aggregator (inner loop)
```

All model registry keys (`or-mimo-v2-pro`, `or-qwen3.5-397b`) are already present in `agents/model_registry.py`. Alternatives can be swapped by changing the value in `agent_models.py`.

## Assumptions Made

1. **PlanAgent uses `agent.iter()` with manual `.next()` loop** for SSE event interception, same pattern as current v1 runner. This allows mid-run event collection.
2. **LoopResult is injected as a dynamic instruction** after each `invoke_search_loop` call completes, not as a tool return value. The tool returns a serialized summary string; the full LoopResult is stored on deps and read by a dynamic instruction function.
3. **`quick_search` is fully out of scope** -- only a stub function with `NotImplementedError`. No implementation needed.
4. **Cases and compliance search remain mocked** -- same hardcoded Arabic results as current implementation.
5. **Regulation search uses real pipeline** -- `run_retrieval_pipeline()` from `agents/regulation_executor/tools.py`.
6. **The orchestrator does not change** -- `handle_deep_search_turn()` and `build_search_deps()` maintain their current signatures exactly.
7. **No new DB tables or migrations needed** -- uses existing `artifacts` and `case_memories` tables.
8. **`PlannerResult` from `agents/models.py` is reused as-is** for PlanAgent output. No new top-level model needed.
9. **The inner loop uses a separate `pydantic_graph.Graph`** -- it is not nested inside the PlanAgent's own pydantic_ai run. The PlanAgent's `invoke_search_loop` tool is an async function that constructs and runs the graph independently.
10. **Error handling follows v1 pattern**: on any exception, return `TaskContinue(response=ERROR_MSG_AR, artifact=<existing_or_empty>)` to keep the task pinned for retry.
11. **Each `invoke_search_loop` call produces an independent report** -- PlanAgent is responsible for deciding whether to merge multiple loop results or use the last one.

## Testing Requirements

Tests should cover:
1. **PlanAgent tool routing**: Verify correct tool is called for different question types
2. **QueryExpander output**: Verify 2-4 queries with correct tool assignments
3. **Aggregator sufficiency**: Test strong/weak classification and loop-back behavior
4. **ReportNode DB insert**: Test artifact creation and SSE event emission
5. **End-to-end turn**: Full `handle_deep_search_turn()` with mock LLMs
6. **Out-of-scope detection**: Verify non-legal questions return `TaskEnd(reason="out_of_scope")`
7. **Edit mode**: Verify `update_report` tool updates existing artifact
8. **Error recovery**: Verify graceful fallback on LLM/DB errors

Use `pydantic_ai.models.test.TestModel` and `pydantic_ai.models.function.FunctionModel` for deterministic testing.

---

Generated: 2026-04-01
Note: This is a complete rewrite of deep_search_v2. The current flat-graph implementation will be replaced entirely by this hierarchical supervisor pattern.
