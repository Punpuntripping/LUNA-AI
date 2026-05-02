# Wave 9: Router-Centric Dispatch + Memory Agent + `agent_runs` Audit

> **Supersedes:** task lifecycle from `archive/task_orchestration_layer.md` (active/completed pinning, TaskContinue/TaskEnd loop, task-scoped history)
> **Dependencies:** Wave 6B (artifacts/workspace_items), Wave 8 (workspace_items rename), deep_search_v4 + agent_writer in `agents/orchestrator.py`
> **Pydantic AI references:** `Obsidian/Legal_AI_March/agents/pydantic_AI/` — guides 07 (structured output), 08 (deferred tools, evals), 09 (multi-agent delegation, usage rollup), 10 (message history serialization). Patterns cited inline below.
> **Date:** 2026-05-02

---

## Implementation Status (2026-05-02)

| Task | Status | Notes |
|------|--------|-------|
| 1 — `029_agent_runs.sql` | ✅ DONE | RLS + 4 indexes + 2 policies; `ADD VALUE IF NOT EXISTS 'memory'` on `agent_family_enum` (defensive — already present in 018); idempotent wrappers added |
| 2 — `030_memory_infra.sql` | ✅ DONE | `summary*` cols + `compacted_through_message_id`; trigger `workspace_items_invalidate_summary` (BEFORE UPDATE, IS DISTINCT FROM) |
| 3 — `031_artifact_cap.sql` | ✅ DONE | `enforce_artifact_cap` BEFORE INSERT, early-returns for non-counted kinds, errcode 23514 |
| 4 — `agents/runs.py` | ✅ DONE | `AgentRunRecord` + `record_agent_run`; swallows exceptions, never raises |
| 5 — `agents/models.py` slim | ✅ DONE | `OpenTask`→`DispatchAgent` (agent_family/target_item_id/attached_item_ids); `MAX_ATTACHED_ITEMS=7`; added MajorAgentInput/SpecialistResult/snapshots; deleted TaskContinue/TaskEnd |
| 5-sub — Aggregator/Writer chat_summary | ✅ DONE | `chat_summary` + `key_findings` on `AggregatorLLMOutput`/`AggregatorOutput`/`WriterLLMOutput`/`WriterOutput`; ModelRetry validators on both agents (≤500 chars, ≤5 bullets); prompts updated (Arabic) |
| 5b — Writer `@agent.system_prompt` | ✅ DONE | New `agents/agent_writer/context.py::format_writer_context`; WriterDeps gained briefing/attached_items/revising_item_id/detail_level/tone; runner stops embedding context in user message; `_populate_deps_from_input` bridge for legacy callers |
| 6 — `agents/memory/agent.py` | ✅ DONE | Mock summarize_workspace_item / resummarize_dirty_items / compact_conversation; tiktoken cl100k_base with `len//4` fallback; `_walk_to_safe_boundary` placeholder for Wave 10 tool-pair refinement; emits agent_runs row on compaction |
| 7 — `agents/orchestrator.py` rewrite | ✅ DONE | Pre-router memory hook (best-effort); `_route` calls `load_router_context` → `run_router`; `_dispatch` cap pre-flight + try/except/finally with `record_agent_run`; full body never streamed (only chat_summary + bullets); Logfire trace_id/span_id populated; new `user_message_id` param threaded |
| 8 — Delete `agents/state.py` | ✅ DONE | `git rm`; zero live imports outside archive/ |
| 9 — Router refactor | ✅ DONE | `output_type=[ChatResponse, DispatchAgent]` (list syntax); `@output_validator` ModelRetry on >7 attached_item_ids; `read_workspace_item` tool (parallel-call hint, RLS belt-and-suspenders); new `agents/router/context.py::load_router_context` (case+summaries+compaction+post-cutoff messages, filters agent_question/agent_answer kinds); 19/19 router tests pass |
| 10 — `message_service` wiring | ✅ DONE | `task_type`→`agent_family` in SendMessageRequest/api/messages/service; new SSE handlers for `agent_run_started`/`agent_run_finished`; `user_message_id` forwarded; full_content accumulator only on `token` events |
| 11 — Frontend cleanup | ✅ DONE | Types: SSETaskStarted/Ended → SSEAgentRunStarted/Finished; chat-store dropped activeTaskId/Type, added isAgentRunning/runningAgentFamily/Subtype; use-chat handlers swapped; lib/api body uses agent_family. `tsc --noEmit` + `lint` green |
| 12 — Drop `task_state` (`032`) | ⏳ POST-DEPLOY | Run after Wave 9 validates green in prod |
| 13 — `ask_user` pause/resume | ⏳ DEFERRED | Tracked as additive 9B sub-wave |

### Validation Gate (Phase 5)

- **@integration-lead** — 6/6 PASS. SSE event names match, `agent_family` request field aligned, no surviving task_state/TaskInfo/activeTask refs in live code, SpecialistResult parity across deep_search/writer paths, `messages.content` accumulator token-only rule confirmed, DispatchAgent contract matched producer↔consumer.
- **@rls-auditor** — 10/10 PASS, 1 LOW: agent_runs RLS policies call `public.get_current_user_id()` directly; canonical pattern in `016_rls.sql` wraps in `(SELECT …)` for row-evaluation caching. Functionally equivalent (function is STABLE SECURITY DEFINER); cosmetic.

### Cleanup Notes (non-blocking)

- `tiktoken` not pinned in `backend/requirements.txt` (present in venv); compaction falls back to `len//4` heuristic if the package is missing in deploy. Pin before counting on accurate token bands.
- `shared/observability.py:59` allowlist still references `"task_type"` — obsolete after Wave 9, can be deleted.
- Frontend `SSEAgentRunStarted/Finished.agent_family` typed as plain `string`; sibling `SSEAgentSelected.agent_family` uses the `AgentFamily` union — tighten for type-safety consistency.
- Optional: rewrap RLS USING/WITH CHECK in `(SELECT public.get_current_user_id())` to match 016_rls.sql pattern.

---

## Why

The task abstraction was designed for multi-turn `TaskContinue | TaskEnd` agents that never materialized. Both real specialists — `deep_search_v4.run_full_loop` and `agent_writer.handle_writer_turn` — are **single-shot pipelines**. The pinning layer (`task_state`, `get_active_task`, `_run_task` history threading, `out_of_scope` re-routing) is dead weight today and will only get harder to reason about once Wave 10+ wires real LLM costs.

We replace the lifecycle with three primitives:

1. **Router-centric dispatch.** Every turn goes to the router. The router either replies directly or dispatches to a specialist for one bounded run. Control returns to the router on the **next user message** — no pinning.
2. **Memory agent.** A first-class agent family responsible for (a) per-workspace-item summaries that are eagerly fed to the router as compact context, and (b) conversation compaction once the message history exceeds a token threshold.
3. **`agent_runs` audit table.** Pure observability log. One row per specialist invocation. No `status='active'`, no `history_json`, no `summary` column for memory.

---

## Agent Hierarchy

| Tier | Members | Reads | Writes | Talks to user? |
|---|---|---|---|---|
| **1 — Conductor** | Router | Full convo (post-cutoff messages + compaction summary), per-item summaries (eager), case metadata + memories, full artifact `content_md` **on demand via `read_workspace_item` tool** (callable in parallel for multiple items) | Streams to chat (assistant `messages.content`); soft-deletes workspace items via the workspace API; never mutates `content_md` directly | Yes (chat) |
| **2 — Major** | deep_search_v4 **planner**, agent_writer | `briefing` + `attached_items` (full content_md, router-selected) + `recent_messages` (last **N≥3**, configurable per agent) + user_id/conversation_id/case_id | One workspace_item per run (via publishers), one `agent_runs` row | Yes (via `ask_user` channel — pinned while awaiting reply, see Task 13) |
| **3 — Task** | All deep_search_v4 sub-agents (reg/compliance/case expanders, search, rerankers, **aggregator**) | Only what the calling major agent injects via deps (URA, queries, vectors, prompt_key, detail_level) | Internal logs only — `agents/deep_search_v4/monitor/`, Logfire spans | No |
| **4 — Memory** | `summarize_workspace_item`, `resummarize_dirty_items`, `compact_conversation` | Workspace items + messages | Per-item `summary` columns, `convo_context` items, `compacted_through_message_id`, `agent_runs` rows | No |

**Hard rules:**
- **Only Tier 1 + Tier 2 may communicate with the user.** Tier 3 emits structured outputs that the major agent or orchestrator surfaces; Tier 4 is system-side.
- **Only Tier 2 + Tier 4 write to `workspace_items`.** Major agents create/edit artifacts (via publishers); memory agents create `convo_context` items + update summary columns. Router never mutates `content_md`.
- **Tier 3 writes nothing user-visible** — pure transformers (URA → markdown, queries → results).
- **All tiers write `agent_runs`** when they perform an LLM-driven invocation.
- **The aggregator is Tier 3** despite producing the artifact body. It's a transformer (URA → markdown); the planner makes decisions, the publishers persist, the aggregator just writes prose into a structured field.

**Editing is always a writer dispatch.** The router never mutates `content_md` directly. "Fix this paragraph" → router emits `DispatchAgent(agent_family='writing', target_item_id=X, briefing='...')`. The writer's edit-mode path is the lightweight path; one audit trail for all artifact mutations.

**Chat summary producer:** The terminal LLM step inside each major agent's pipeline emits `chat_summary` + `key_findings` as structured output fields alongside the artifact body. For deep_search this is the **aggregator** (`AggregatorOutput.chat_summary`, `AggregatorOutput.key_findings`). For writing this is the **writer** (`WriterOutput.chat_summary`, `WriterOutput.key_findings`). No separate summarizer dispatch — same LLM call, two extra structured fields, ~zero added cost or latency. The orchestrator reads them off `SpecialistResult` and streams them to chat; `messages.content` for the assistant row = `chat_summary + bullets`, never the artifact body.

---

## Architectural Shape

```
User message ──► message_service.send_message_stream
                       │
                       ▼
                 [pre-router hook]
                  ├─ resummarize dirty workspace items (memory agent, cheap, parallel)
                  └─ compact conversation if message tokens > 10_000 (memory agent)
                       │
                       ▼
                 router agent (LLM)
                  context: case metadata + case_memories
                         + concatenation of per-item summaries
                         + latest compaction summary (if any) + post-cutoff messages
                         + current user message
                       │
        ┌──────────────┴──────────────┐
        │                             │
   ChatResponse                  DispatchAgent(family, briefing, target_item_id?, attached_item_ids[])
        │                             │
        │                       [cap pre-flight]: if family creates a new artifact
        │                       AND non-system items >= 15 → reject with Arabic error
        │                             │
        │                             ▼
        │                       specialist runs
        │                       (deep_search_v4 loop  |  agent_writer one-shot)
        │                       creates / edits workspace_item (sets summary=NULL on update)
        │                       streams chat_summary + key_findings (NEVER full body_md)
        │                       writes agent_runs row
        │                             │
        ▼                             ▼
      done                          done
                       (next user turn → router again, no pinning)
```

**Key invariants:**

- The router is the only agent with conversation memory.
- Specialists are stateless w.r.t. conversation. They receive a `briefing` and `attached_item_ids` (capped at **N=7** items) from the router.
- `messages.content` for assistant turns is **never** the full artifact body — only `chat_summary + key_findings`. The artifact lives only in `workspace_items.content_md`.
- A workspace item UPDATE always sets `summary = NULL`. NULL is the dirty signal the memory agent looks for.
- Setting up duplex agent↔user channels (specialists asking questions via tools) is **out of scope** for Wave 9.

---

## Goals

1. Remove `task_state` table and all lifecycle code.
2. Add `agent_runs` table for audit/cost telemetry (immutable, RLS-scoped).
3. Add memory agent infrastructure: per-item summary columns, compaction cutoff column, eager router context loader.
4. Enforce 15-artifact cap per conversation (counting `agent_search + agent_writing + note`).
5. Simplify orchestrator: route → maybe dispatch → done. One pre-router hook for memory.
6. Simplify SSE: drop `task_started`/`task_ended`, add `agent_run_started`/`agent_run_finished`.
7. Simplify frontend chat store — drop `activeTaskId` / `activeTaskType`.
8. Preserve everything that already works: deep_search_v4 pipeline, agent_writer publishing, workspace_items renderer, router classification.

---

## Non-Goals

- No new specialist agents. Wave 9 is plumbing + memory.
- No real LLM swap (Wave 10+).
- No retry/replay UI (data lands in `agent_runs`; UX comes later).
- No specialists-ask-user tool channel (future wave; needs SSE pause/resume).
- No semantic dirtiness for summaries — length drift is enough for v1.
- No background queue for summarization — runs lazily in pre-router hook.

---

## Task 1: Migration `029_agent_runs.sql`

### Agent: @sql-migration

**Depends on:** Nothing (additive)

```sql
CREATE TYPE agent_run_status AS ENUM ('ok', 'error', 'timeout');

CREATE TABLE agent_runs (
    run_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(user_id),
    conversation_id   UUID NOT NULL REFERENCES conversations(conversation_id),
    case_id           UUID REFERENCES lawyer_cases(case_id),
    message_id        UUID REFERENCES messages(message_id),
    agent_family      agent_family_enum NOT NULL,
    subtype           TEXT,
    status            agent_run_status NOT NULL DEFAULT 'ok',
    input_summary     TEXT,
    output_item_id    UUID REFERENCES workspace_items(item_id),
    duration_ms       INTEGER,
    tokens_in         INTEGER,
    tokens_out        INTEGER,
    model_used        TEXT,
    per_phase_stats   JSONB DEFAULT '{}'::jsonb,
    error             JSONB,
    -- Logfire correlation (commit 44002d2 wired Logfire). Cheap to populate
    -- from the active span at run completion; lets the audit table pivot
    -- straight to traces.
    trace_id          TEXT,
    span_id           TEXT,
    created_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_agent_runs_conversation ON agent_runs(conversation_id, created_at DESC);
CREATE INDEX idx_agent_runs_user_family  ON agent_runs(user_id, agent_family, created_at DESC);
CREATE INDEX idx_agent_runs_case         ON agent_runs(case_id, created_at DESC) WHERE case_id IS NOT NULL;
CREATE INDEX idx_agent_runs_item         ON agent_runs(output_item_id) WHERE output_item_id IS NOT NULL;

ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_runs_select ON agent_runs
    FOR SELECT USING (user_id = get_current_user_id());
CREATE POLICY agent_runs_insert ON agent_runs
    FOR INSERT WITH CHECK (user_id = get_current_user_id());
```

No UPDATE / DELETE policy — runs are immutable.

### Validation
- Table exists, RLS on, 2 policies (select + insert), no UPDATE/DELETE policies.
- Cross-user isolation: insert as user A, select as user B → 0 rows.
- `agent_family_enum` already includes `memory` (verify; add value if missing in this same migration).

---

## Task 2: Migration `030_memory_infra.sql`

### Agent: @sql-migration

**Depends on:** Nothing (additive)

```sql
-- Per-item summary columns. summary IS NULL is the "dirty" signal.
ALTER TABLE workspace_items
    ADD COLUMN summary               TEXT,
    ADD COLUMN summary_source_length INTEGER,
    ADD COLUMN summary_updated_at    TIMESTAMPTZ;

-- Trigger: any UPDATE that touches content_md clears the summary.
CREATE OR REPLACE FUNCTION workspace_items_invalidate_summary()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.content_md IS DISTINCT FROM OLD.content_md THEN
        NEW.summary               := NULL;
        NEW.summary_source_length := NULL;
        NEW.summary_updated_at    := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_workspace_items_invalidate_summary
    BEFORE UPDATE ON workspace_items
    FOR EACH ROW
    EXECUTE FUNCTION workspace_items_invalidate_summary();

-- Conversation compaction cutoff. Router loads messages with created_at >
-- the cutoff message's created_at. NULL = no compaction has happened yet.
ALTER TABLE conversations
    ADD COLUMN compacted_through_message_id UUID
        REFERENCES messages(message_id) ON DELETE SET NULL;
```

### Validation
- Columns exist on both tables.
- Trigger fires: `UPDATE workspace_items SET content_md='x'` → `summary` becomes NULL.
- Trigger does NOT fire on metadata-only UPDATEs (e.g., `UPDATE ... SET title='y'` keeps summary intact).
- FK on `compacted_through_message_id` valid; ON DELETE SET NULL prevents orphan blockage.

---

## Task 3: Migration `031_artifact_cap.sql`

### Agent: @sql-migration

**Depends on:** Task 2

```sql
-- 15-artifact cap per conversation, counting only user/agent OUTPUT kinds.
-- attachments and convo_context are exempt (system-managed / inputs).
CREATE OR REPLACE FUNCTION enforce_artifact_cap()
RETURNS TRIGGER AS $$
DECLARE
    cap     INTEGER := 15;
    counted INTEGER;
BEGIN
    IF NEW.kind NOT IN ('agent_search', 'agent_writing', 'note') THEN
        RETURN NEW;
    END IF;
    SELECT COUNT(*) INTO counted
        FROM workspace_items
        WHERE conversation_id = NEW.conversation_id
          AND deleted_at IS NULL
          AND kind IN ('agent_search', 'agent_writing', 'note');
    IF counted >= cap THEN
        RAISE EXCEPTION 'workspace_items_cap_exceeded'
            USING ERRCODE = '23514',
                  HINT = 'Conversation has reached the 15-item limit. Delete an item before creating new ones.';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_workspace_items_cap
    BEFORE INSERT ON workspace_items
    FOR EACH ROW
    EXECUTE FUNCTION enforce_artifact_cap();
```

The DB trigger is the **last line of defense**. Application layer (Task 7's pre-flight in `_dispatch`) catches it first to avoid wasting a full specialist run.

### Validation
- Insert 15 `agent_writing` rows → 16th raises `workspace_items_cap_exceeded`.
- Insert 100 `attachment` rows → no error (exempt).
- Insert mixed kinds: 5 `agent_search` + 5 `agent_writing` + 5 `note` = 15 → 16th of any counted kind fails; another `attachment` succeeds.
- Soft-deleted items don't count: delete one, insert succeeds.

---

## Task 4: `agents/runs.py`

### Agent: @fastapi-backend

**Depends on:** Task 1

```python
"""agent_runs writes — append-only audit log."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from supabase import Client as SupabaseClient

@dataclass
class AgentRunRecord:
    user_id: str
    conversation_id: str
    agent_family: str
    case_id: str | None = None
    message_id: str | None = None
    subtype: str | None = None
    input_summary: str | None = None
    output_item_id: str | None = None
    duration_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    model_used: str | None = None
    per_phase_stats: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    error: dict | None = None
    # Populated from the active Logfire span at write time, e.g.:
    #   span = logfire.current_span(); rec.trace_id = format(span.context.trace_id, '032x')
    trace_id: str | None = None
    span_id: str | None = None

def record_agent_run(supabase: SupabaseClient, rec: AgentRunRecord) -> str | None:
    """Insert a row. Returns run_id, or None on failure (never raises)."""
    ...
```

Errors swallowed and logged — telemetry must never break a user-facing run.

### Validation
- Round-trip: `record_agent_run` → `SELECT` returns the row.
- Bad payload (missing FK) → returns None, no exception.

---

## Task 5: Slim `agents/models.py`

### Agent: @shared-foundation

**Keep:**
- `ChatResponse` — router direct reply
- `PlannerResult` — used by deep_search planner agent

**Rename + extend:**
- `OpenTask` → `DispatchAgent`
  - field `task_type` → `agent_family` (Literal includes `"memory"`)
  - field `artifact_id` → `target_item_id`
  - **NEW:** `attached_item_ids: list[str] = Field(default_factory=list, max_length=MAX_ATTACHED_ITEMS)` — router selects which workspace items the specialist sees as input. `MAX_ATTACHED_ITEMS = 7` (constant, single source of truth — bump as needed). Field constraint enforces hard upper bound; an `@output_validator` raising `ModelRetry("you attached N items; pick the 7 most relevant")` gives the model a guided retry when it overshoots.
  - keep `subtype` as optional passthrough

**Add:**
```python
class ChatMessageSnapshot(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: str

class WorkspaceItemSnapshot(BaseModel):
    item_id: str
    kind: str
    title: str
    content_md: str
    metadata: dict = Field(default_factory=dict)

class MajorAgentInput(BaseModel):
    """Tier 2 input contract. Major agents NEVER read DB messages directly —
    they receive only what the orchestrator passes here."""
    briefing: str
    attached_items: list[WorkspaceItemSnapshot]    # router-selected, full content_md
    recent_messages: list[ChatMessageSnapshot]     # last N (default 3, per-agent overridable)
    target_item_id: str | None = None              # if editing existing
    user_id: str
    conversation_id: str
    case_id: str | None = None

class SpecialistResult(BaseModel):
    """Standardized specialist return shape, consumed by orchestrator._dispatch."""
    output_item_id: str | None = None
    chat_summary: str = Field(description="≤ 500 chars, streamed to chat as the assistant body")
    key_findings: list[str] = Field(default_factory=list, description="≤ 5 bullets, streamed after chat_summary")
    sse_events: list[dict] = Field(default_factory=list)
    model_used: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    per_phase_stats: dict = Field(default_factory=dict)
```

Both `_run_deep_search` and `_run_writer` (Task 7) return `SpecialistResult`. The full `body_md` is no longer streamed to chat.

**Sub-step — extend Tier-3 / specialist output schemas with chat_summary + key_findings:**

- `agents/deep_search_v4/aggregator/models.py::AggregatorOutput` — add `chat_summary: str` and `key_findings: list[str]`. Aggregator prompts updated to emit both alongside `synthesis_md` (same LLM call, marginal token cost).
- `agents/agent_writer/.../models.py::WriterOutput` — add the same two fields. Writer prompt updated similarly.
- `_run_deep_search` and `_run_writer` map these directly into `SpecialistResult.chat_summary` / `key_findings`.

**Length enforcement via `output_validator + ModelRetry`** (Pydantic AI native pattern, see `07_structured_output.md §4`):

```python
@aggregator_agent.output_validator
async def _validate_summary_length(ctx, value: AggregatorOutput) -> AggregatorOutput:
    if len(value.chat_summary) > 500:
        raise ModelRetry(
            f"chat_summary is {len(value.chat_summary)} chars; shorten to ≤ 500."
        )
    if len(value.key_findings) > 5:
        raise ModelRetry(
            f"key_findings has {len(value.key_findings)} items; reduce to ≤ 5."
        )
    return value
```

`ModelRetry` triggers a guided retry (model sees the error, regenerates) — better than a hard `Field(max_length=...)` rejection for prose-quality fields. Same validator pattern on `WriterOutput`.

The aggregator stays **Tier 3** even though it now emits user-visible text — `chat_summary` is structured output, not direct user communication. The orchestrator (plumbing) is the one that streams it.

**Delete:**
- `TaskContinue`, `TaskEnd` (no consumer left after Task 7)

### Validation
- `from agents.models import ChatResponse, DispatchAgent, PlannerResult, SpecialistResult` — clean.
- `from agents.models import TaskContinue` raises ImportError.
- `DispatchAgent(agent_family="memory", briefing="x", attached_item_ids=[])` validates.

---

## Task 5b: Writer-side `@agent.system_prompt` for workspace_context

### Agent: @sse-streaming

**Depends on:** Task 5 (MajorAgentInput / WorkspaceItemSnapshot in `agents/models.py`)

Counterpart to Task 9's router context loader, applied to `agent_writer`. Today `agents/agent_writer/agent.py::create_writer_agent` builds the agent with a **static** `instructions=get_writer_prompt(subtype)` string, and the workspace context (attached items, briefing, prior draft on revision) is concatenated into the **user message** by the runner. That works but has three costs: caller-side string assembly, no per-run RunContext access for the prompt, and a future refactor when message_history (Wave 10+ revision flow) lands.

Refactor to Pydantic AI's dynamic system-prompt pattern (`07_structured_output.md`-adjacent; native `@agent.system_prompt` decorator):

```python
# agents/agent_writer/agent.py
def create_writer_agent(
    deps: WriterDeps | None = None, *, subtype: str = "memo", model_name: str | None = None,
) -> Agent[WriterDeps, WriterLLMOutput]:
    ...
    agent: Agent[WriterDeps, WriterLLMOutput] = Agent(
        model,
        deps_type=WriterDeps,
        output_type=WriterLLMOutput,
        instructions=get_writer_prompt(subtype),   # static base — subtype rules
        retries=2,
    )

    @agent.system_prompt
    async def inject_workspace_context(ctx: RunContext[WriterDeps]) -> str:
        """Append router-selected attached_items + briefing + revision target
        as a second system block. Re-evaluated on every run (including resumes)."""
        return format_writer_context(
            attached_items=ctx.deps.attached_items,
            briefing=ctx.deps.briefing,
            revising_item_id=ctx.deps.revising_item_id,
            detail_level=ctx.deps.detail_level,
            tone=ctx.deps.tone,
        )

    return agent
```

**`WriterDeps` gains the fields the runner used to splice into the user message:**

```python
@dataclass
class WriterDeps:
    supabase: ...
    primary_model: str
    fallback_model: str | None = None
    lock_ttl_seconds: int = 30
    emit_sse: Callable | None = None
    # NEW — populated from MajorAgentInput by the runner before agent construction:
    briefing: str = ""
    attached_items: list[WorkspaceItemSnapshot] = field(default_factory=list)
    revising_item_id: str | None = None
    detail_level: str = "standard"
    tone: str = "neutral"
    _events: list[dict] = field(default_factory=list)
```

`agents/agent_writer/runner.py` stops concatenating workspace context into the user prompt. The user prompt becomes just the **task statement** (e.g. `"اكتب مذكرة قانونية حول..."`). The runner passes `deps=writer_deps` on `agent.run(user_msg, deps=writer_deps)` and the system_prompt callable assembles the rest.

**`format_writer_context`** lives in a new helper `agents/agent_writer/context.py` (or inside `prompts.py`) — pure function, deterministic, easy to unit-test in isolation. Same function is reused by Wave 10+ revision flow when message_history is wired.

**Why now:** doing this in Wave 9 (alongside Task 9's router refactor) means both Tier-1 and Tier-2 agents end the wave on the same context-injection idiom. Wave 10's real-LLM swap and revision-via-message_history flow then become "swap the model + pass message_history" with no plumbing change.

### Validation

- `agent.run("test", deps=writer_deps)` produces a system prompt that includes both the static subtype block AND the rendered workspace_context block (assert via Pydantic AI's `result.all_messages()` inspection of the first system message).
- `format_writer_context` unit test: empty attached_items → returns briefing-only block; with 2 items → both titles + `content_md` appear in order.
- Runner no longer concatenates workspace context into the user message: assert the user-role message in `result.all_messages()` equals the raw task statement (no embedded `<attached>` blocks).
- Revision path: `revising_item_id` set in deps → context block contains the prior draft's title + body for the model to reference (until Wave 10 wires `message_history`).
- Existing writer regression tests still pass — output shape (`WriterLLMOutput`) unchanged; only the prompt-assembly seam moved.

### File Manifest delta (folds into the table at the end of Wave 9)

| # | File | Action | Agent |
|---|------|--------|-------|
| 5b-1 | `agents/agent_writer/agent.py` | MODIFY (`@agent.system_prompt` + `deps_type=WriterDeps`) | @sse-streaming |
| 5b-2 | `agents/agent_writer/deps.py` | MODIFY (add `briefing`, `attached_items`, `revising_item_id`, `detail_level`, `tone`) | @sse-streaming |
| 5b-3 | `agents/agent_writer/context.py` | NEW (`format_writer_context` pure helper) | @sse-streaming |
| 5b-4 | `agents/agent_writer/runner.py` | MODIFY (stop concatenating context into user prompt; pass `deps=` on `.run()`) | @sse-streaming |

---

## Task 6: Memory Agent — `agents/memory/agent.py`

### Agent: @sse-streaming

**Depends on:** Tasks 2, 4, 5

Two entry points. Both are **mock implementations** for Wave 9 (return deterministic Arabic text); Wave 10+ swaps in real LLM calls. Same shape both ways.

```python
"""Memory agent — per-item workspace summarization + conversation compaction."""
from __future__ import annotations
from dataclasses import dataclass

DEFAULT_COMPACT_MAX_TOKENS = 10_000
DEFAULT_COMPACT_FRACTION   = 0.60
DRIFT_THRESHOLD            = 0.25  # 25%

async def summarize_workspace_item(
    supabase, item_id: str
) -> str:
    """Generate a 1–3 sentence summary of an item's content_md.

    Writes summary, summary_source_length, summary_updated_at on the row.
    Returns the summary text. Idempotent.
    """
    ...

async def resummarize_dirty_items(
    supabase, conversation_id: str
) -> list[str]:
    """Find all items in the convo where summary IS NULL OR drift >= 25%
    and resummarize each. Returns list of item_ids that were updated.

    Length drift = abs(current_len - summary_source_length) / summary_source_length
    """
    ...

async def compact_conversation(
    supabase,
    conversation_id: str,
    user_id: str,
    max_tokens: int = DEFAULT_COMPACT_MAX_TOKENS,
    fraction: float = DEFAULT_COMPACT_FRACTION,
) -> str | None:
    """If post-cutoff message tokens > max_tokens, summarize the oldest
    fraction (default 60%), insert a new convo_context workspace_item with
    the summary, and update conversations.compacted_through_message_id.

    Returns the new convo_context item_id, or None if compaction wasn't needed.
    Fixed-window: only runs when threshold is breached; one summary per breach.
    Records an agent_runs row.

    CONSTRAINT — tool-pair boundary respect (see Pydantic AI 10_message_history.md):
    The compaction cutoff MUST NOT split a ToolCallPart from its matching
    ToolReturnPart. After computing the naive 60% boundary, walk forward to
    the next message that is NOT a tool-return; that becomes the actual cutoff.
    Splitting a pair makes the model error on resume.
    """
    ...
```

Token counting uses `tiktoken` with `cl100k_base` as a stable cross-provider proxy.

The `convo_context` items produced by compaction are exempt from the 15-artifact cap (Task 3) and from `resummarize_dirty_items` input (they're already summaries).

### Validation
- Single-item summarize: `WHERE summary IS NULL` count drops to 0 after run.
- Drift detection: artificially set `summary_source_length` to half current length → `resummarize_dirty_items` picks it up.
- Compaction below threshold → returns None, no row written.
- Compaction above threshold → new `convo_context` item exists, `compacted_through_message_id` set, `agent_runs` row exists with `agent_family='memory'`, `subtype='compact'`, `output_item_id` populated.
- Idempotent: running summarize on an already-fresh item is a no-op (or a cheap no-write check).
- **Tool-pair boundary**: construct a synthetic conversation with a ToolCallPart at the 60% mark and its ToolReturnPart at 65%. Verify compaction's cutoff lands at ≥65% (after the return), never between 60% and 65%.

---

## Task 7: Rewrite `agents/orchestrator.py`

### Agent: @sse-streaming

**Depends on:** Tasks 1–6

```python
async def handle_message(
    question: str, user_id: str, conversation_id: str,
    supabase: SupabaseClient,
    case_id: str | None = None,
    explicit_agent_family: str | None = None,
) -> AsyncGenerator[dict, None]:
    # Pre-router memory hook (best-effort, swallow errors).
    try:
        await memory.resummarize_dirty_items(supabase, conversation_id)
        await memory.compact_conversation(supabase, conversation_id, user_id)
    except Exception:
        logger.warning("memory pre-hook failed", exc_info=True)

    if explicit_agent_family:
        async for ev in _dispatch(
            agent_family=explicit_agent_family,
            briefing=question,
            target_item_id=None, attached_item_ids=[], subtype=None,
            supabase=supabase, user_id=user_id,
            conversation_id=conversation_id, case_id=case_id,
        ):
            yield ev
        return

    async for ev in _route(question, supabase, user_id, conversation_id, case_id):
        yield ev


async def _route(...):
    """Run router; on ChatResponse stream tokens; on DispatchAgent call _dispatch."""
    # Loads case metadata + case_memories + per-item summaries (concat from
    # workspace_items.summary) + latest convo_context content_md (if any) +
    # messages WHERE created_at > (compacted_through_message_id timestamp).
    ...

async def _dispatch(
    agent_family, briefing, target_item_id, attached_item_ids, subtype, ...
):
    # 1. Cap pre-flight. Skip for memory family. Skip if target_item_id given
    #    (editing existing — no new item created).
    if agent_family in ("deep_search", "writing") and target_item_id is None:
        if _count_artifact_kinds(supabase, conversation_id) >= 15:
            yield {"type": "token", "text":
                "وصلت للحد الأقصى من المستندات في هذه المحادثة (15). "
                "يرجى حذف مستند قبل إنشاء جديد."}
            yield {"type": "done", "usage": _zero_usage("cap_rejected")}
            return

    t0 = perf_counter()
    yield {"type": "agent_selected", "agent_family": agent_family}
    yield {"type": "agent_run_started", "agent_family": agent_family, "subtype": subtype}

    run_result: SpecialistResult | None = None
    err_payload: dict | None = None
    status = "ok"
    try:
        if agent_family == "deep_search":
            run_result = await _run_deep_search(briefing, target_item_id, attached_item_ids, ...)
        elif agent_family == "writing":
            run_result = await _run_writer(briefing, target_item_id, attached_item_ids, subtype, ...)
        elif agent_family == "memory":
            # Explicit memory invocation (e.g. router asks for forced compaction).
            run_result = await _run_memory(briefing, ...)
        else:
            yield {"type": "token", "text": "حدث خطأ: نوع المهمة غير معروف"}
            yield {"type": "done", "usage": _zero_usage("error")}
            return

        for ev in run_result.sse_events:
            yield ev
        # Stream chat_summary + key_findings only — never the full body.
        yield {"type": "token", "text": run_result.chat_summary}
        if run_result.key_findings:
            yield {"type": "token", "text": "\n\n" + "\n".join(f"• {k}" for k in run_result.key_findings)}
        yield {"type": "done", "usage": {
            "prompt_tokens": run_result.tokens_in or 0,
            "completion_tokens": run_result.tokens_out or 0,
            "model": run_result.model_used or agent_family,
        }}

    except Exception as exc:
        logger.error("specialist %s failed: %s", agent_family, exc, exc_info=True)
        status = "error"
        err_payload = {"type": type(exc).__name__, "message": str(exc)[:500]}
        yield {"type": "token", "text": "عذراً، حدث خطأ أثناء تنفيذ المهمة. يرجى المحاولة مرة أخرى."}
        yield {"type": "done", "usage": _zero_usage("error")}

    finally:
        record_agent_run(supabase, AgentRunRecord(
            user_id=user_id, conversation_id=conversation_id, case_id=case_id,
            agent_family=agent_family, subtype=subtype,
            message_id=user_message_id, input_summary=briefing[:500],
            output_item_id=getattr(run_result, "output_item_id", None),
            duration_ms=int((perf_counter() - t0) * 1000),
            tokens_in=getattr(run_result, "tokens_in", None),
            tokens_out=getattr(run_result, "tokens_out", None),
            model_used=getattr(run_result, "model_used", None),
            per_phase_stats=getattr(run_result, "per_phase_stats", {}) or {},
            status=status, error=err_payload,
        ))

    yield {"type": "agent_run_finished", "agent_family": agent_family}
```

`_run_deep_search` and `_run_writer` are extracted from the current `_run_pydantic_ai_task` and refactored to return `SpecialistResult`. The `chat_summary` and `key_findings` fields are populated from existing structured outputs (`AggregatorOutput.synthesis_md` → trimmed/summarized; writer adds these as new fields).

**Removed from orchestrator.py:**
- `get_active_task`, `create_task`, `update_task_history`, `update_task_artifact`, `complete_task`
- `_open_task`, `_open_task_explicit`, `_run_task` (mock path)
- `TaskContinue` / `TaskEnd` handling, `_inject_task_summary`, `_MOCK_AGENTS`, out-of-scope re-route
- `yield {"type": "token", "text": agg_output.synthesis_md}` and the equivalent in writer (full body NEVER streamed to chat)

### Validation
- `python -c "from agents.orchestrator import handle_message"` — clean
- `grep -r "task_state\|TaskInfo\|TaskContinue\|TaskEnd\|active_task" agents/ backend/` → only matches in archive/, plans/, this file
- `grep -r "synthesis_md\|content_md" agents/orchestrator.py` → no `yield.*token.*synthesis_md` line
- Specialist crash → `agent_runs` row has `status='error'`, error JSON populated; user sees Arabic error
- Cap pre-flight: 15 items → dispatch refuses, no specialist runs, no `agent_runs` row

---

## Task 8: Delete `agents/state.py`

### Agent: @fastapi-backend

**Depends on:** Task 7

```
git rm agents/state.py
```

`grep -r "from agents.state" .` returns zero hits.

---

## Task 9: Router Output Type + Context Loader + read_workspace_item Tool

### Agent: @fastapi-backend

**Depends on:** Task 5

**File: `agents/router/router.py`**

```python
async def run_router(...) -> ChatResponse | DispatchAgent: ...
```

Pydantic AI router's `output_type` uses **list syntax**, not the `|` union (per `07_structured_output.md §2` — each member becomes its own output tool internally, gives the model better selection signal, no `# type: ignore` noise):

```python
router_agent = Agent(
    model=...,
    output_type=[ChatResponse, DispatchAgent],  # NOT ChatResponse | DispatchAgent
    ...
)
```

Router prompt updated for the four pre-checks:

1. **Necessity** — does this need a specialist?
2. **Scope** — is this within the app's legal scope?
3. **Ambiguity** — only ask clarifying questions when truly ambiguous
4. **Artifact selection** — populate `attached_item_ids` with the workspace items relevant to this dispatch (router has the per-item summaries available; picks IDs by relevance). **Hard cap: 7 items per dispatch** (`MAX_ATTACHED_ITEMS`). Router prompt instructs the model to choose the most relevant when more than 7 candidates exist; `@output_validator` triggers a `ModelRetry` if the model overshoots so the retry is guided rather than a hard rejection.

The router's eager input context (assembled by the loader, not the agent itself):

```
- Case metadata + case_memories (when case_id present)
- Workspace items: list of (item_id, kind, title, summary) — concat of per-item summaries only
- Compaction summary (if any): full content_md of latest convo_context item
- Messages: WHERE created_at > compacted_through_message_id's created_at, OR all if cutoff is NULL
- Current user message
```

**`read_workspace_item` tool — on-demand full-content access:**

The router gets a Pydantic AI tool that resolves an item_id to its full `content_md`. This avoids eager-loading all bodies (which would explode prompt size) while still letting the router answer questions like "what does the report's conclusion say?" or pick attached_item_ids with full evidence rather than summary-only.

```python
@router_agent.tool
async def read_workspace_item(ctx: RunContext[RouterDeps], item_id: str) -> str:
    """Return the full markdown content of a workspace item.
    Use when summaries are insufficient — e.g. answering a direct question
    about an artifact's contents, or picking attached_items for a dispatch."""
    row = ctx.deps.supabase.table("workspace_items") \
        .select("content_md") \
        .eq("item_id", item_id) \
        .eq("user_id", ctx.deps.user_id) \
        .is_("deleted_at", "null") \
        .maybe_single().execute()
    if not row or not row.data:
        return ""  # silent miss — agent decides whether to retry
    return row.data.get("content_md") or ""
```

**Parallel calls:** the model can issue multiple `read_workspace_item` invocations in one turn (Anthropic and the OpenAI-compatible providers both support parallel tool calls; Pydantic AI surfaces them automatically). Router can open 3 artifacts at once if it needs cross-referencing.

**RLS:** the supabase client is the per-user RLS-scoped client (already established pattern). The `eq("user_id", ctx.deps.user_id)` is belt-and-suspenders.

**Filter agent_question / agent_answer messages from router context:** Tier-2 pause/resume Q&A (Task 13) lives in `messages` with metadata flags. The context loader excludes those from what the router sees — they're audit trail for the user, not prompt material for the router.

### Validation
- All router unit tests pass after rename.
- Out-of-scope query (recipe) → `ChatResponse` with polite Arabic decline.
- Ambiguous research query → `ChatResponse` asking ONE clarifying question.
- Clear research query → `DispatchAgent(agent_family='deep_search', attached_item_ids=[...])`.
- Editing intent ("revise the memo") → `DispatchAgent(agent_family='writing', target_item_id=<existing>)`.

---

## Task 10: Wire Orchestrator into `message_service`

### Agent: @fastapi-backend + @sse-streaming

**Depends on:** Task 7

**File: `backend/app/services/message_service.py`**

- Rename request arg: `task_type` → `agent_family`; forward as `explicit_agent_family=`.
- Drop SSE handlers: `task_started`, `task_ended`.
- Add SSE handlers:

```python
elif event_type == "agent_run_started":
    await queue.put(_sse_event("agent_run_started", {
        "agent_family": event["agent_family"],
        "subtype": event.get("subtype"),
    }))
elif event_type == "agent_run_finished":
    await queue.put(_sse_event("agent_run_finished", {
        "agent_family": event["agent_family"],
    }))
```

Keep `workspace_item_created` / `workspace_item_updated` / `agent_selected` exactly as today.

**File: `backend/app/api/messages.py`**
- Body field `task_type` → `agent_family` on `SendMessageRequest`.

**File: `backend/app/models/requests.py`**
```python
class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    agent_family: Optional[str] = Field(default=None,
        description="Force a specialist family, bypassing the router.")
    attachment_ids: Optional[list[str]] = None
```

### Validation
- `python -c "from backend.app.main import app"` — clean
- POST with `{"agent_family": "deep_search", "content": "…"}` → specialist runs, `agent_runs` row written
- POST without `agent_family` → router runs (pre-hook fires first)
- Cap rejection: 15-item convo + research request → SSE: `agent_selected` → cap-Arabic-token → `done`, NO `agent_runs` row

---

## Task 11: Frontend SSE + Store Cleanup

### Agent: @nextjs-frontend

**Depends on:** Task 10

**`frontend/types/index.ts`**
- Drop `task_started` / `task_ended` SSE event types
- Add `agent_run_started` / `agent_run_finished`

**`frontend/hooks/use-chat.ts`**
- Replace `task_started` / `task_ended` cases with `agent_run_started` / `agent_run_finished` (UI affordance for spinner/status only — no pinning state)

**`frontend/stores/chat-store.ts`**
- Remove fields: `activeTaskId`, `activeTaskType`
- Remove actions: `setActiveTask`, `clearActiveTask`
- Clean up `reset()` and initial-state lines

**`frontend/lib/api.ts`**
- Rename `task_type` → `agent_family` on `sendMessage` body

### Validation
- `npx tsc --noEmit` clean
- `npm run lint` clean
- Playwright: send research request → spinner during run, artifact appears in workspace pane, chat shows summary + bullets only, second message goes to router with no lingering "active task" UI

---

## Task 12: Drop `task_state` (post-deploy migration `032_drop_task_state.sql`)

### Agent: @sql-migration

**Depends on:** Tasks 7–11 deployed and validated in production

```sql
DROP TABLE IF EXISTS task_state;
DROP TYPE IF EXISTS task_status_enum;
```

Run **after** Wave 9 validates green in prod — gives a one-deploy rollback window.

---

## Task 13: Tier-2 Pause/Resume — `ask_user` Channel

### Agent: @sse-streaming + @fastapi-backend + @nextjs-frontend

**Depends on:** Tasks 1, 4, 7, 10, 11 (everything else green first)

The narrow form of pinning: a Tier-2 major agent that needs an answer can pause mid-run, surface the question to the user, and resume on the user's next message. Wave 9 wires this for the deep_search v4 planner (which has had `FullLoopDeps.ask_user: Callable | None = None` stubbed since cut-1). Writer doesn't ask in v1.

### 13.1 Schema additions on `agent_runs`

```sql
ALTER TYPE agent_run_status ADD VALUE 'awaiting_user';

ALTER TABLE agent_runs
    -- Pydantic AI message history bytes from result.all_messages_json().
    -- Resume via ModelMessagesTypeAdapter.validate_json(message_history_bytes).
    ADD COLUMN message_history     BYTEA,
    -- Deferred tool requests (pending tool_call_ids + their validated args)
    -- and any partial structured output. Serialized via model_dump_json.
    ADD COLUMN deferred_payload    JSONB,
    ADD COLUMN question_text       TEXT,
    ADD COLUMN asked_at            TIMESTAMPTZ,
    ADD COLUMN expires_at          TIMESTAMPTZ;   -- default asked_at + 24h
```

One row per run, lifecycle: `awaiting_user` → `ok` (resumed and completed) | `error` | `timeout`.

### 13.2 Q&A persistence in `messages`

The agent's question and the user's reply both land in `messages` with metadata flags so they appear in scrollback:

```
{role: 'assistant', content: <question_text>, metadata: {kind: 'agent_question', run_id, agent_family}}
{role: 'user',      content: <user reply>,    metadata: {kind: 'agent_answer',   run_id}}
```

The router's context loader (Task 9) **excludes** these from prompt material — they're for the user's history, not for routing decisions. The major agent on resume reads them via the rehydrated `agent_state`, not by re-querying messages.

### 13.3 SSE protocol additions

```
agent_question:  { run_id, question, suggestions?: string[] }
agent_resumed:   { run_id, agent_family }
```

`agent_run_started` fires on initial dispatch only. `agent_run_finished` fires only on terminal completion (not on pause). On resume, `agent_resumed` fires before work continues.

### 13.4 `ask_user` as a native deferred tool

Use Pydantic AI's first-class deferred-tool pattern (`08_advanced_features.md §2`) instead of a custom exception. The tool is declared with `requires_approval=True` (or raises `CallDeferred` from inside its body); the agent run terminates with `output: DeferredToolRequests` rather than `AgentRunResult`.

```python
# agents/deep_search_v4/planner/agent.py
from pydantic_ai import Agent, CallDeferred

planner_agent = Agent(
    model=...,
    # NOTE: verify against installed pydantic-ai whether DeferredToolRequests
    # must be declared explicitly here as a union member, or whether the run
    # output type is automatically widened when a tool can raise CallDeferred.
    # The guides (08_advanced_features.md §2.2) describe behavior, not the
    # output_type declaration. Confirm before coding.
    output_type=PlannerOutput,
    ...
)

@planner_agent.tool
async def ask_user(ctx, question: str) -> str:
    """Ask the user a clarifying question; pauses the run until they reply."""
    # CallDeferred ends the run; the question + tool_call_id surface in
    # result.output.calls (DeferredToolRequests). No deferred_tools kwarg —
    # the capability is implicit from raising CallDeferred inside a registered tool.
    raise CallDeferred
```

`_dispatch` checks the run output:

```python
result = await planner_agent.run(prompt, message_history=...)

if isinstance(result.output, DeferredToolRequests):
    # One row per pending tool call. We expect exactly one ask_user.
    pending = result.output.calls[0]
    assert pending.tool_name == "ask_user"
    # IMPORTANT: ToolCallPart.args is typed `str | dict[str, Any] | None`
    # in pydantic-ai 1.39+ — JSON string when streamed, dict when local.
    # ALWAYS go through args_as_dict() to normalize.
    pending_args = pending.args_as_dict()
    question = pending_args["question"]

    record_agent_run(supabase, AgentRunRecord(
        ..., status="awaiting_user",
        message_history=result.all_messages_json(),   # bytes; column is BYTEA
        deferred_payload={
            "tool_call_id": pending.tool_call_id,
            "tool_name": pending.tool_name,
            "args": pending.args,
            "partial_output": None,                   # planner has no partial yet at this point
        },
        question_text=question,
        asked_at=now(), expires_at=now() + timedelta(hours=24),
    ))
    supabase.table("messages").insert({
        "conversation_id": conversation_id, "user_id": user_id,
        "role": "assistant", "content": question,
        "metadata": {"kind": "agent_question", "run_id": run_id, "agent_family": agent_family},
    }).execute()
    yield {"type": "agent_question", "run_id": run_id, "question": question}
    yield {"type": "done", "usage": _zero_usage("paused")}
    return  # do NOT yield agent_run_finished — run is still alive
```

**Why this beats the custom `_PauseSignal`:**
- No bespoke exception class, no `_snapshot_agent_state()` helper — Pydantic AI hands us validated `args` and a stable `tool_call_id`.
- `result.all_messages_json()` is the supported serialization; round-trip via `ModelMessagesTypeAdapter.validate_json(bytes)` is tested upstream.
- Multiple deferred calls in one turn (e.g., agent asks two questions at once) work for free — `DeferredToolRequests.calls` is a list.

### 13.5 Pre-route resume check

`handle_message` gets a new check before the memory pre-hook:

```python
async def handle_message(...):
    # 0. Pending major agent? Resume it.
    pending = _find_awaiting_user(supabase, conversation_id)
    if pending:
        if pending.expires_at < now():
            _mark_status(supabase, pending.run_id, "timeout")
            # fall through to normal flow
        else:
            async for ev in _resume_major_agent(pending, user_reply=question, ...):
                yield ev
            return
    # 1. Memory pre-hook
    # 2. Router or explicit dispatch
```

`_resume_major_agent` does:
1. Insert `messages` row for the user's reply with `metadata.kind='agent_answer', run_id=pending.run_id`
2. Yield `agent_resumed` event
3. Rehydrate Pydantic AI `message_history`:
   ```python
   from pydantic_ai.messages import ModelMessagesTypeAdapter
   history = ModelMessagesTypeAdapter.validate_json(pending.message_history)  # BYTEA → list[ModelMessage]
   ```
4. Construct `DeferredToolResults` mapping the pending tool_call_id to the user reply:
   ```python
   from pydantic_ai import DeferredToolResults
   results = DeferredToolResults(calls={
       pending.deferred_payload["tool_call_id"]: user_reply,
   })
   ```
5. Resume the run: `await planner_agent.run(deferred_tool_results=results, message_history=history)`. Pydantic AI threads the result back into the original tool call site; the agent continues from where `CallDeferred` paused it.
6. On completion: stream `chat_summary + key_findings`, persist artifact, update `agent_runs.status='ok'`, yield `agent_run_finished`
7. On another pause: same as initial pause path (status stays `awaiting_user`, new question persisted, message_history bytes refreshed)
8. On agent abort (`PlannerOutput.aborted=True`): mark `status='abandoned'`, fall through to a fresh router dispatch on the original `user_reply` so the user isn't stuck
9. On deserialization failure (Pydantic AI version drift, etc.): mark `status='error'`, log, route the new message through the router fresh — never fail-loop on a corrupt resume

### 13.6 Frontend rendering

- `agent_question` SSE → render an inline assistant message styled differently (e.g. an icon + "السؤال:" prefix) so the user sees a question is open
- `agent_resumed` SSE → optional UI affordance ("استئناف...")
- Standard text input submits the answer; the orchestrator's pre-route check handles resume routing
- Scrollback: `agent_question` / `agent_answer` messages render as part of normal history (with a visual marker so they read as Q&A, not generic assistant prose)

### 13.7 Wire deep_search v4 planner to use the channel

- Replace the existing `FullLoopDeps.ask_user: Callable | None` stub with a Pydantic AI tool on `planner_agent` that raises `CallDeferred` (see 13.4). Drop the callable plumbing.
- Update planner prompts to call `ask_user` when ambiguity blocks plan derivation (e.g. "بحث في القضايا" without a sector → planner asks which sector).
- Add `aborted: bool` to `PlannerOutput` for the off-script abort path (13.5 step 8).

Writer keeps no `ask_user` tool for v1 (carried in `wave_10_stub.md` item #6).

### 13.8 Usage rollup for nested Pydantic AI calls

When a Tier-2 agent calls into a Tier-3 Pydantic AI agent inside the same dispatch, pass `usage=ctx.usage` so token counts roll up automatically (`09_multi_agent_delegation.md`).

This applies surgically — deep_search_v4 currently runs phases as orchestrator-level `asyncio.gather`, NOT as planner-driven sub-agent calls. So the rollup point is **wherever a Pydantic AI agent invokes another Pydantic AI agent under the same logical run**:

- Aggregator called from inside the planner run (if/when the planner directly invokes it — currently it doesn't).
- Any future fan-out where one agent embeds another.

For Wave 9's actual code path the orchestrator-level token totals stay correct via `_per_executor_stats`. This sub-step is a forward-compat constraint: **whenever a `Pydantic AI Agent.run()` happens inside another agent's tool/function, pass `usage=ctx.usage`.** Add a unit test that fails if a nested call drops usage.

### Validation (Task 13)

| # | Test | Pass Criteria |
|---|------|---------------|
| 1 | Planner asks user → SSE pause | SSE: `agent_run_started` → `agent_question` → `done`; `agent_runs` row has `status='awaiting_user'`, `question_text` populated; one new `messages` row with `metadata.kind='agent_question'` |
| 2 | User replies → planner resumes → completes | SSE: `agent_resumed` → tokens → `agent_run_finished`; `agent_runs.status='ok'`; one `messages.kind='agent_answer'` row, then a normal assistant row with `chat_summary + bullets` |
| 3 | User goes off-script | Planner returns `aborted=True`; orchestrator marks `status='abandoned'`, dispatches the off-script reply through router; user sees router's reply, no infinite loop |
| 4 | Expiry | Set `expires_at` in past, send new message → router runs (pending is bypassed); `status='timeout'` recorded |
| 5 | Router never sees Q&A messages | Send a router-bound message after a complete pause/resume cycle — router prompt does not include the agent_question or agent_answer rows |
| 6 | Pydantic AI message_history round-trip | `result.all_messages_json()` → BYTEA → `ModelMessagesTypeAdapter.validate_json` → planner produces a coherent next step. If deserialization fails, run is marked `status='error'` and original message routes to router fresh |
| 7 | Deferred tool result routing | After resume with `DeferredToolResults({tool_call_id: user_reply})`, planner sees the reply as the return value of its `ask_user` call (not as a fresh user turn) |
| 8 | Multi-question resilience | Construct a planner that calls `ask_user` twice in one turn → `DeferredToolRequests.calls` length 2 → orchestrator surfaces both, resume requires both answers in `DeferredToolResults` |

---

## Execution Order

```
Phase 1 (parallel):  Task 1 (migration 029 agent_runs)
                     Task 2 (migration 030 memory infra)
                     Task 3 (migration 031 artifact cap)
                     Task 4 (agents/runs.py)
                     Task 5 (agents/models.py slim + SpecialistResult + Major/Memory inputs)
                     Task 5-sub (AggregatorOutput / WriterOutput add chat_summary + key_findings)
Phase 2 (parallel):  Task 6 (agents/memory/agent.py)
                     Task 9 (router output type + context loader + read_workspace_item tool)
Phase 3:             Task 7 (orchestrator rewrite — depends on 1–6)
Phase 4 (parallel):  Task 8 (delete agents/state.py)
                     Task 10 (message_service wiring)
                     Task 11 (frontend cleanup)
Phase 5:             Validation gate (@integration-lead, @validate, @rls-auditor)
Phase 6 (post-deploy): Task 12 (drop task_state)
Phase 7 (additive):  Task 13 (Tier-2 pause/resume — ask_user channel)
                     Can ship in same wave or split off as 9B if Pydantic AI
                     message_history serialization needs more bake time.
```

---

## File Manifest

| # | File | Action | Agent |
|---|------|--------|-------|
| 1 | `shared/db/migrations/029_agent_runs.sql` | NEW | @sql-migration |
| 2 | `shared/db/migrations/030_memory_infra.sql` | NEW | @sql-migration |
| 3 | `shared/db/migrations/031_artifact_cap.sql` | NEW | @sql-migration |
| 4 | `agents/runs.py` | NEW | @fastapi-backend |
| 5 | `agents/models.py` | MODIFY (rename, drop Task* types, add SpecialistResult) | @shared-foundation |
| 6 | `agents/memory/agent.py` | REWRITE (per-item summarize + compact_conversation) | @sse-streaming |
| 7 | `agents/orchestrator.py` | REWRITE (pre-router hook, dispatch, SpecialistResult, cap pre-flight) | @sse-streaming |
| 8 | `agents/state.py` | DELETE | @fastapi-backend |
| 9 | `agents/router/router.py` | MODIFY (output type, prompts, context loader) | @fastapi-backend |
| 10 | `backend/app/services/message_service.py` | MODIFY | @fastapi-backend + @sse-streaming |
| 11 | `backend/app/api/messages.py` | MODIFY | @fastapi-backend |
| 12 | `backend/app/models/requests.py` | MODIFY | @fastapi-backend |
| 13 | `frontend/types/index.ts` | MODIFY | @nextjs-frontend |
| 14 | `frontend/hooks/use-chat.ts` | MODIFY | @nextjs-frontend |
| 15 | `frontend/stores/chat-store.ts` | MODIFY (remove activeTask*) | @nextjs-frontend |
| 16 | `frontend/lib/api.ts` | MODIFY | @nextjs-frontend |
| 17 | `shared/db/migrations/032_drop_task_state.sql` | NEW (post-deploy) | @sql-migration |
| 18 | `agents/deep_search_v4/aggregator/models.py` | MODIFY (add chat_summary + key_findings to AggregatorOutput) | @sse-streaming |
| 19 | `agents/deep_search_v4/aggregator/prompts.py` | MODIFY (instruct aggregator to emit both fields) | @sse-streaming |
| 20 | `agents/agent_writer/models.py` (or equivalent) | MODIFY (add chat_summary + key_findings to WriterOutput) | @sse-streaming |
| 21 | `agents/agent_writer/prompts.py` (or equivalent) | MODIFY (instruct writer to emit both fields) | @sse-streaming |

**Task 13 (additive — pause/resume):**

| # | File | Action | Agent |
|---|------|--------|-------|
| 22 | `shared/db/migrations/033_agent_runs_pause_columns.sql` | NEW | @sql-migration |
| 23 | `agents/orchestrator.py` | MODIFY (pause exception, pre-route resume check, _make_ask_user) | @sse-streaming |
| 24 | `agents/deep_search_v4/orchestrator.py` | MODIFY (wire FullLoopDeps.ask_user instead of None) | @sse-streaming |
| 25 | `agents/deep_search_v4/planner/prompts.py` | MODIFY (instruct planner to call ask_user when blocked) | @sse-streaming |
| 26 | `agents/deep_search_v4/planner/models.py` | MODIFY (add `aborted: bool` to PlannerOutput) | @sse-streaming |
| 27 | `backend/app/services/message_service.py` | MODIFY (handle agent_question / agent_resumed SSE events) | @sse-streaming |
| 28 | `frontend/types/index.ts` | MODIFY (agent_question / agent_resumed event types) | @nextjs-frontend |
| 29 | `frontend/hooks/use-chat.ts` | MODIFY (render agent_question inline; resume awareness) | @nextjs-frontend |
| 30 | `frontend/components/chat/MessageBubble.tsx` (or equivalent) | MODIFY (special rendering for kind='agent_question'/'agent_answer' messages) | @nextjs-frontend |

**Total: 21 file ops base + 9 file ops Task 13 = 30 file ops if Wave 9 ships with pause/resume.**

---

## Validation Gate (Wave 9)

### @rls-auditor
- `agent_runs` has RLS, 2 policies (select + insert), no UPDATE/DELETE
- Cross-user isolation verified for `agent_runs`
- Trigger `enforce_artifact_cap` fires on INSERT, exempts attachment + convo_context
- Trigger `workspace_items_invalidate_summary` fires on content_md change only

### @integration-lead
- Backend SSE event names match frontend type union exactly
- `SendMessageRequest.agent_family` matches frontend `sendMessage` body
- No surviving references to `task_state`, `TaskInfo`, `task_started`, `task_ended`, `activeTaskId` outside plans/ and archived migrations
- `SpecialistResult.chat_summary` / `key_findings` consumed identically by deep_search and writer paths

### Notes (out of scope for Wave 9, recorded for later)

- **`history_processors` as router context-injection mechanism.** Pydantic AI's `history_processors=[summarize_old]` would let the router consume the compaction summary inline before the model sees history (see `10_message_history.md`). Wave 9 keeps the explicit "concat into context-loader" approach because writing a `convo_context` row gives the user an auditable, diff-able artifact. `history_processors` is a viable refactor target if the auditability requirement weakens.
- **Pydantic Evals as the validation gate.** The 15 hand-rolled `@validate` tests below would translate cleanly into a `Dataset + Evaluators` setup (`08_advanced_features.md §7.3`) so Wave 10+ regressions get caught automatically. Not blocking Wave 9; record as a tooling upgrade.

### @validate

| # | Test | Pass Criteria |
|---|------|---------------|
| 1 | Greeting → router replies | SSE: `token`* → `done`; no `agent_runs` row |
| 2 | Research request → router dispatches | SSE: `agent_selected` → `agent_run_started` → workspace events → summary tokens → bullet tokens → `done` → `agent_run_finished`; one `agent_runs` row, `output_item_id` set |
| 3 | Force `agent_family='writing'` | Skips router; one `agent_runs` row with `agent_family='writing'` |
| 4 | Second message after research turn | Goes to router (no pinning); router references prior artifact via attached_item_ids |
| 5 | Specialist crashes | `agent_runs` row with `status='error'`, error JSON populated; Arabic error token + `done` |
| 6 | Cap pre-flight: 15 items → research request | SSE: `agent_selected` → cap-Arabic-token → `done`; no `agent_runs` row, no specialist invocation |
| 7 | Workspace item UPDATE | `summary` becomes NULL via trigger |
| 8 | Pre-router hook | Run on convo with NULL-summary item → next router turn the summary is populated |
| 9 | Compaction trigger | Crank message tokens > 10k → next pre-router run creates `convo_context` item, sets `compacted_through_message_id`, writes `agent_runs` row with `agent_family='memory', subtype='compact'` |
| 10 | `messages.content` rule | After research dispatch, `messages.content` for the assistant row contains chat_summary + bullets only — NEVER the full artifact body_md |
| 11 | TypeScript build | `npx tsc --noEmit` clean |
| 12 | Backend imports | `python -c "from backend.app.main import app"` clean |
| 13 | Router parallel artifact reads | Send a message that requires referencing 3 artifacts → router issues parallel `read_workspace_item` tool calls in one turn; final reply references all three correctly |
| 14 | Tier-2 input contract | Spy on `_run_writer` / `_run_deep_search`: assert `recent_messages` length ≥ 3 (or matches per-agent override); assert `attached_items` is the router-selected set (not "everything visible") |
| 15 | Aggregator chat_summary path | Run deep_search → assert `messages.content` for assistant row = `AggregatorOutput.chat_summary + bullets`, NOT `synthesis_md` |
| 16 | `attached_item_ids` cap | Router given a workspace with 12 candidate items → `DispatchAgent.attached_item_ids` length ≤ 7. Force-overshoot via prompt manipulation → `ModelRetry` fires, model resubmits with ≤ 7. Hard `Field(max_length=7)` rejects any post-retry overshoot. |

---

## Success Criteria

- [ ] `agent_runs` table created with RLS, 4 indexes, 2 policies (select + insert only)
- [ ] `agents/runs.py` exports `record_agent_run`, never raises
- [ ] `workspace_items.summary*` columns + invalidation trigger
- [ ] `conversations.compacted_through_message_id` column
- [ ] 15-artifact cap trigger enforces on `agent_search + agent_writing + note`; exempts `attachment + convo_context`
- [ ] `agents/memory/agent.py` exports `summarize_workspace_item`, `resummarize_dirty_items`, `compact_conversation` with kwarg-configurable thresholds (default 10k tokens / 60% fold)
- [ ] `agents/models.py` exports `ChatResponse`, `DispatchAgent`, `PlannerResult`, `SpecialistResult` only (no Task* types)
- [ ] `agents/state.py` deleted
- [ ] `agents/orchestrator.py`: pre-router memory hook, cap pre-flight, dispatch path, no `task_state` reads, full body_md NEVER streamed to chat
- [ ] Router returns `ChatResponse | DispatchAgent`; `DispatchAgent` carries `attached_item_ids` (capped at `MAX_ATTACHED_ITEMS = 7`)
- [ ] Router context loader uses per-item summaries + compaction-summary + post-cutoff messages
- [ ] `SendMessageRequest.agent_family` (not `task_type`)
- [ ] SSE: `agent_run_started` / `agent_run_finished` replace `task_started` / `task_ended`
- [ ] Frontend store: `activeTaskId` / `activeTaskType` removed
- [ ] One `agent_runs` row per specialist invocation; never for direct router replies; never for cap-rejected dispatches
- [ ] Memory invocations also write `agent_runs` rows (`agent_family='memory'`)
- [ ] After deploy verification, `task_state` table dropped via migration 032
- [ ] All Arabic error messages preserved (Absolute Rule #5)
- [ ] Agent hierarchy table at top of plan reflects Tier 1–4 split with privilege rules
- [ ] Aggregator + Writer outputs gain `chat_summary` + `key_findings` structured fields
- [ ] Router has `read_workspace_item` tool, callable in parallel for multiple item_ids
- [ ] Tier-2 `recent_messages` floor = 3 (per-agent overridable upward, never lower)
- [ ] Router never mutates `content_md` directly — all artifact edits flow through writer dispatch with `target_item_id`
- [ ] Task 13 (pause/resume) — if shipped: `agent_runs.status='awaiting_user'` works, deep_search planner uses `ask_user`, Q&A persisted in `messages` with metadata flags, router context loader filters those flags out
