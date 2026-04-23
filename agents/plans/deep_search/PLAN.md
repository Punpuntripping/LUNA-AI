# Deep Search Planner — Pydantic AI Implementation Plan

## What This Agent Does

The deep search planner orchestrates multi-source Saudi legal research. It receives a briefing from the router, expands it into targeted Arabic search queries, delegates to executor agents (regulations, cases, compliance), evaluates results, re-searches if weak, and produces a structured research report artifact. It is a task agent — pinned to the conversation until done or out-of-scope.

## Agent Definition

| Setting | Value | Justification |
|---------|-------|---------------|
| Model | Configurable via `get_agent_model("deep_search_planner")` — Claude Sonnet recommended | Best cost/speed balance for Arabic legal reasoning and query expansion + synthesis. Model is configurable in `model_registry.py` for easy swapping. |
| output_type | `PlannerResult` | Internal structured result — orchestrator wraps into `TaskContinue` or `TaskEnd` |
| deps_type | `SearchDeps` | Supabase client, embedding fn, case memory, user/conversation/case IDs |
| instructions | static + 1 dynamic (`inject_case_memory`) | Static: role, scope, budget rules. Dynamic: case memory if available. |
| run method | `agent.iter()` with manual `.next()` | Required for `ask_user` pause/resume, SSE mid-run events, streaming |
| end_strategy | `early` (default) | Planner decides when to stop via structured output, not framework |
| retries | 2 | Retry on malformed PlannerResult output |
| usage_limits | `UsageLimits(response_tokens_limit=10000, request_limit=20, tool_calls_limit=25)` | Safety net for runaway loops. Soft budget in instructions (3 rounds, 5 calls/round) handles normal behavior. |

## Architecture

```
                    ┌─────────────┐
                    │ Orchestrator │
                    └──────┬──────┘
                           │ briefing + SearchDeps
                           ▼
                    ┌──────────────┐
                    │   Planner    │  ← agent.iter() with manual .next()
                    │ (this agent) │
                    └──┬───┬───┬──┘
         ┌─────────────┤   │   ├─────────────┐
         ▼             ▼   │   ▼             ▼
   ┌───────────┐ ┌────────┐│┌────────┐ ┌──────────┐
   │ search_   │ │search_ │││search_ │ │ interact │
   │regulations│ │cases_  │││compli- │ │  tools   │
   │ (tool)    │ │courts  │││ance    │ │(4 tools) │
   └───────────┘ └────────┘│└────────┘ └──────────┘
         │             │   │     │
         └─────────────┴───┴─────┘
                       │
              executor agents (future)
              currently: mock responses
```

### Data Flow

1. **Orchestrator** loads `OrchestratorState` from DB, builds `SearchDeps`, calls planner via `agent.iter()`
2. **Planner** reads briefing + case memory, expands into search queries
3. **Delegation tools** (`search_regulations`, `search_cases_courts`, `search_compliance`) run executor agents (mocked initially), return markdown summaries
4. **Planner** evaluates results, optionally re-searches (max 3 rounds)
5. **Interaction tools** (`respond_to_user`, `ask_user`, `create_report`, `get_previous_report`) handle SSE events and artifact CRUD
6. **Planner** returns `PlannerResult` — orchestrator wraps as `TaskContinue` or `TaskEnd`

## Output Types

### PlannerResult (agent output_type)

The agent's internal structured output. The orchestrator reads these fields to build `TaskContinue` or `TaskEnd`.

```
PlannerResult:
    task_done: bool       # true → wrap as TaskEnd, false → wrap as TaskContinue
    end_reason: str | None  # "done" | "user_satisfied" | "out_of_scope" (only when task_done=true)
    answer_ar: str        # chat-level response (SHORT summary for TaskContinue.response or TaskEnd.last_response)
    search_summary: str   # summary injected into router_history when task ends (TaskEnd.summary)
    artifact_md: str      # FULL artifact markdown (TaskContinue.artifact or TaskEnd.artifact)
```

### Task Models (orchestrator wrapping)

```
TaskContinue:
    type = "continue"
    response           # str — from PlannerResult.answer_ar
    artifact           # str — from PlannerResult.artifact_md (FULL every turn, NOT diffs)

TaskEnd:
    type = "end"
    reason             # "done" | "user_satisfied" | "out_of_scope" — from PlannerResult.end_reason
    summary            # str — from PlannerResult.search_summary (injected into router_history)
    artifact           # str — from PlannerResult.artifact_md (FINAL)
    last_response      # str — from PlannerResult.answer_ar
```

### Citation (internal type for structured citation tracking)

```
Citation:
    source_type        # "regulation" | "article" | "section" | "case" | "service"
    ref                # chunk_ref or case_ref — the unique identifier
    title              # Arabic title
    content_snippet    # relevant excerpt
    regulation_title   # parent regulation name (if article/section)
    article_num        # article number (if applicable)
    court              # court name (if case)
    relevance          # why this source supports the answer
```

## End-to-End Workflow

The complete lifecycle of a deep search request flows through three orchestrator phases.

### Phase 1: Routing

1. User sends message via `POST /chat`
2. Orchestrator loads `OrchestratorState` from DB (includes `router_history`, `pinned_task`)
3. If `pinned_task` is None, route to **router agent**
4. Router returns `OpenTask(task_type="deep_search", briefing="...", artifact_id=None|"rpt_001")`
5. Orchestrator updates `router_history` with the routing exchange

### Phase 2: Task Execution (Planner)

1. Orchestrator calls `_open_task()`:
   - Sets `state.pinned_task = "deep_search"`
   - Sets `state.current_artifact = ""`
   - Builds clean `task_history` with briefing as `SystemPromptPart`
   - Builds `SearchDeps` (supabase, embedding_fn, case_memory, IDs)
   - Emits SSE: `{ event: "task_started", task_type: "deep_search" }`

2. Runs planner via `agent.iter()` with manual `.next()` control:
   ```python
   async with planner_agent.iter(
       "Begin the task based on the briefing above.",
       deps=SearchDeps(...),
       message_history=[SystemPromptPart(briefing)],
       usage_limits=UsageLimits(
           response_tokens_limit=10000,
           request_limit=20,
           tool_calls_limit=25,
       ),
   ) as run:
   ```

3. Orchestrator drives the graph node by node:
   - **ModelRequestNode**: Stream thinking tokens via SSE `token` events
   - **CallToolsNode**: Intercept specific tool calls:
     - `respond_to_user` -- emit SSE `status` event (fire-and-forget)
     - `ask_user` -- emit SSE `ask_user` event, PAUSE, `await wait_for_user_reply()`, inject reply as tool result, RESUME
     - `create_report` -- tool writes to DB, orchestrator emits `artifact_created`/`artifact_updated` SSE
     - Delegation tools -- execute normally (run executor agents)
   - **End**: Extract `PlannerResult` from `run.result.output`

4. Orchestrator wraps result:
   - If `result.task_done` is true -- wrap as `TaskEnd`
   - If `result.task_done` is false -- wrap as `TaskContinue`

### Phase 3: Task Completion (on TaskEnd)

1. Persist final artifact to `artifacts` table
2. Inject summary into `router_history`:
   ```
   "[TASK COMPLETED -- deep_search]\n{search_summary}"
   ```
3. Unpin task: `state.pinned_task = None`, `state.task_history = []`, `state.current_artifact = ""`
4. Serialize state back to DB
5. **If `reason="out_of_scope"`**: Re-feed the user's message to the router (back to Phase 1) so it can be routed to the correct agent family

### Follow-Up Turns (while task is pinned)

When `state.pinned_task = "deep_search"`, the orchestrator skips routing and forwards the user message directly to the planner with the existing `task_history`. The planner receives the follow-up as its user message and can search further, edit the report, or end the task.

## Token Budget

```
Typical deep search (1 round, 3 parallel executor calls):
  Router:     ~700 tokens (500 in / 200 out)
  Planner:    ~8,300 tokens (3 requests: read+plan, evaluate+report, synthesize)
  Executors:  ~5,400 tokens (regulation x2 + cases x1)
  TOTAL:      ~14,400 tokens

With re-search (2 rounds): ~22,000 tokens
With re-search (3 rounds, max): ~30,000 tokens

Hard limits (safety net): 10K response tokens, 20 requests, 25 tool calls
Soft limits (behavioral): 3 search rounds, 5 tool calls per round
```

## Scope Boundaries

### In Scope
- Query expansion (Arabic semantic variants, legal domain detection)
- Executor delegation (3 search domains, parallel calls)
- Result evaluation and re-search logic
- Report artifact creation and editing
- User interaction (ask_user for ambiguity, respond_to_user for status)
- Cumulative citation tracking across turns
- Out-of-scope detection and task ending

### Out of Scope
- Direct database access (executors are black boxes)
- Making up legal content not from search results
- Contract drafting, document extraction, memory management (other agent families)
- The executor agents themselves (planned separately)
- Embedding generation (handled by `agents/utils/embeddings.py`)

## Integration Points

### Orchestrator → Planner
- Orchestrator calls `handle_deep_search_turn()` which runs `planner_agent.iter()`
- Passes `SearchDeps` with supabase client, embedding fn, case memory, IDs
- Passes `UsageLimits` to `agent.iter()` as safety net
- Manual `.next()` loop intercepts tool calls for SSE events

### SSE Events (aligned to existing orchestrator names)
| Event | Source | Mapping |
|-------|--------|---------|
| `agent_selected` | Orchestrator | `{"agent_family": "deep_search"}` |
| `task_started` | Orchestrator | `{"task_id": ..., "task_type": "deep_search"}` |
| `token` | Planner streaming | Token-by-token final synthesis |
| `artifact_created` | `create_report` tool | `{"artifact_id": ..., "artifact_type": "report"}` |
| `artifact_updated` | `create_report` on edit | `{"artifact_id": ...}` |
| `task_ended` | Orchestrator on TaskEnd | `{"task_id": ..., "summary": ...}` |
| `done` | Orchestrator | Usage stats |

Custom mid-run events (new, emitted by orchestrator intercepting tool calls):
| Event | Source | Mapping |
|-------|--------|---------|
| `status` | `respond_to_user` tool | `{"text": "..."}` — fire-and-forget |
| `ask_user` | `ask_user` tool | `{"question": "..."}` — pauses flow |

SSE events emitted in order during a typical search:
1. `task_started` -- task type
2. `status` -- planner's respond_to_user messages (0-N)
3. `ask_user` -- clarification question (0-1, pauses flow)
4. `token` -- streaming planner's thinking/synthesis (continuous)
5. `artifact_created` -- artifact_id + content
6. `task_end` -- final response + reason

### DB Tables Touched
- `task_state` -- read/write (managed by orchestrator, not planner)
- `artifacts` -- write via `create_report`, read via `get_previous_report`
- `case_memories` -- read by orchestrator to build `SearchDeps.case_memory`

### Existing Code Changes Required
- `agents/orchestrator.py` -- replace `mock_deep_search` import with real planner runner
- `agents/deep_search/agent.py` -- replace mock with real agent definition + `handle_deep_search_turn()`
- `agents/models.py` -- add `PlannerResult` model, `Citation` model, update `TaskEnd.reason` to `"done" | "user_satisfied" | "out_of_scope"`
- `agents/state.py` -- no changes needed (already handles task lifecycle)

## Migration Notes

Replacing `agents/deep_search/agent.py` which currently contains `mock_deep_search()`. The mock:
- Returns hardcoded `MOCK_REPORT` markdown
- Checks `OUT_OF_SCOPE_KEYWORDS` for task ending
- Takes `(question, current_artifact, is_first_turn)` args

The real agent:
- Takes `(briefing_or_followup, deps, existing_artifact, message_history)` args
- Returns `TaskContinue | TaskEnd` (same models, orchestrator wrapping stays the same)
- The orchestrator's `_run_task()` will need updating to call the async planner runner instead of the sync mock function

### Runner Function Signature

```python
async def handle_deep_search_turn(
    briefing_or_followup: str,
    deps: SearchDeps,
    existing_artifact: str | None = None,
    message_history: list[ModelMessage] | None = None,
) -> TaskContinue | TaskEnd:
```

### ask_user Implementation

Recommended approach (Option A): The `ask_user` tool itself is an async function that awaits user input. The `CallToolsNode` blocks until all tool calls complete, so `ask_user` naturally pauses the entire agent execution. When the user replies (via WebSocket message or Redis pub/sub event), the tool returns the reply text and the agent resumes.
