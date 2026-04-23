# Router Agent — Pydantic AI Implementation Plan

## What This Agent Does

The router agent is the default conversational agent the user talks to. It owns the main conversation thread, answers general questions directly (greetings, clarifications, simple legal facts, artifact queries), and dispatches specialist tasks (deep_search, end_services, extraction) with synthesized briefings when the user needs research, document drafting, or file processing. It is NOT a task agent — it has no task_state, produces no artifacts, and never gets pinned.

## Agent Definition

| Setting | Value | Justification |
|---------|-------|---------------|
| Model | `gemini-3-flash` via `get_agent_model("router")` | Fast routing decisions at $0.50/$3.00 per 1M tokens, 218 tps. Router responses are short — speed and cost matter more than depth. Upgrade to `gemini-3.1-pro` if classification quality is insufficient. |
| output_type | `ChatResponse \| OpenTask` (union) | The core decision mechanism — either respond directly or dispatch a task. Pydantic AI validates the discriminator automatically. |
| deps_type | `RouterDeps` | Supabase client, case memory, case metadata, user preferences, conversation/user/case IDs |
| instructions | static + 2 dynamic (`inject_case_context`, `inject_user_preferences`) | Static: role, decision rules, briefing guidelines. Dynamic: case memory + user preferences when available. |
| run method | `agent.run()` | No streaming needed — routing decisions are fast, responses are short. The orchestrator fake-streams the ChatResponse word-by-word for UX. |
| end_strategy | `early` (default) | Router decides via structured output, not framework |
| retries | 1 | One retry if the model produces a malformed union output |
| usage_limits | `UsageLimits(response_tokens_limit=2000, request_limit=5, tool_calls_limit=3)` | Router is lightweight — one tool (get_artifact), short responses. Safety net for unexpected loops. |

## Architecture

```
                         User message
                              |
                              v
                     +----------------+
                     |  Orchestrator   |
                     | handle_message()|
                     +--------+-------+
                              |
              active_task? ---|--- no active task
              |                        |
              v                        v
        Task Agent              +-------------+
        (pinned)                |   Router    |  <-- agent.run()
                                | (this agent)|
                                +------+------+
                                       |
                        +--------------+--------------+
                        |              |              |
                   ChatResponse    OpenTask     get_artifact
                        |              |          (tool)
                        v              v
                   SSE tokens    Orchestrator
                   to user       creates task,
                                 pins agent,
                                 runs first turn
```

### Data Flow

1. **Orchestrator** checks for active task on the conversation — if none, routes to router
2. **Orchestrator** loads conversation history, case context, user preferences, builds `RouterDeps`
3. **Router** receives user message + full conversation history (including injected task summaries)
4. **Router** decides: ChatResponse (answer directly) or OpenTask (dispatch specialist task)
5. If **ChatResponse**: orchestrator streams tokens to user via SSE
6. If **OpenTask**: orchestrator creates `task_state` row, builds task deps, runs task agent first turn

## Scope Boundaries

### In Scope
- Classifying user intent (chat vs deep_search vs end_services vs extraction)
- Generating high-quality briefings for task agents (100-500 words, synthesized context)
- Answering greetings, clarifications, meta-questions about Luna directly
- Answering questions about previous artifacts via `get_artifact` tool (read-only)
- Asking for clarification when the user's intent is ambiguous
- Maintaining conversational continuity across task completions (via injected summaries)
- Detecting when a user wants to edit an artifact (dispatch new task with artifact_id)
- Answering simple, well-known legal facts where confidence is very high

### Out of Scope
- Legal research / RAG / vector search / citation extraction (deep_search's job)
- Document drafting — contracts, memos, legal opinions (end_services' job)
- File processing — PDF extraction, image OCR (extraction's job)
- Task lifecycle management (orchestrator's job)
- Artifact creation or editing (task agents create artifacts; router only reads them)
- Embedding generation
- SSE event management (orchestrator handles)

## Integration Points

### Orchestrator -> Router

The orchestrator calls `run_router()` which runs `router_agent.run()`. This is already wired in `orchestrator.py` at `_route()`. The current import is `from agents.router.router import run_router` — the new implementation replaces the mock at that path.

### Router -> Orchestrator (via return value)

| Return type | Orchestrator action |
|-------------|-------------------|
| `ChatResponse` | Yield `agent_selected(router)`, fake-stream tokens, yield `done` |
| `OpenTask` | Call `_open_task(task_type, briefing, artifact_id, ...)` which creates task_state, yields `task_started`, runs first turn |

### SSE Events

| Event | Source | When |
|-------|--------|------|
| `agent_selected` | Orchestrator | `{"agent_family": "router"}` when router answers directly |
| `token` | Orchestrator (fake-stream) | Word-by-word from ChatResponse.message |
| `done` | Orchestrator | Usage stats after response complete |
| `agent_selected` | Orchestrator | `{"agent_family": task_type}` when task opens |
| `task_started` | Orchestrator | `{"task_id": ..., "task_type": ...}` after task creation |

### DB Tables Read

- `messages` — conversation history (loaded by orchestrator, passed as message_history)
- `lawyer_cases` — case metadata (loaded by orchestrator into RouterDeps)
- `case_memories` — case memory context (loaded by orchestrator into RouterDeps)
- `user_preferences` — user preferences (loaded by orchestrator into RouterDeps)
- `artifacts` — read by `get_artifact` tool for answering questions about previous artifacts

### DB Tables Written

- None. The router is read-only. Artifact creation and task lifecycle are managed by the orchestrator and task agents.

### Message History

The router's conversation history is the main conversation thread from the `messages` table. It includes:
1. User messages sent while router was active
2. Router's own ChatResponse replies (saved as assistant messages by the orchestrator)
3. Task summaries injected as assistant messages when tasks complete (format: `[TASK COMPLETED -- {type}]\n{summary}\nArtifact: {artifact_id}`)

The router NEVER sees internal task message history — only summaries.

## Migration Notes

### What Changes

- **New file**: `agents/router/router.py` — real Pydantic AI agent definition + `run_router()` function
- **New file**: `agents/router/__init__.py` — exports

### What Stays the Same

- `agents/orchestrator.py` — `_route()` already imports `from agents.router.router import run_router` and calls it with the correct signature. The return type handling for `ChatResponse` and `OpenTask` is already implemented. No changes needed.
- `agents/models.py` — `ChatResponse` and `OpenTask` models already exist with correct fields.
- `agents/state.py` — no changes needed.
- `agents/utils/history.py` — already converts DB rows to Pydantic AI `ModelMessage` list.
- `agents/utils/agent_models.py` — already has `"router": "gemini-3-flash"` entry.

### What Was Deleted (Legacy)

The git status shows `agents/router/__init__.py`, `agents/router/classifier.py`, and `agents/router/router.py` as deleted. These were the old mock router from Wave 6C. The new implementation replaces them at the same path.
