# Wave 8: Task Orchestration Layer — Replace Agent Dispatch with Task-Based Architecture

> **Supersedes:** `wave_6c_agent_framework.md` (agent dispatch replaced by task lifecycle)
> **Dependencies:** Wave 6B services (artifact_service, preferences_service must exist), Wave 7A (SSE heartbeat)
> **Date:** 2026-03-18
> **Design doc:** `Obsidian/Legal_AI_March/agents/Task_Orchestration_Plan.md`

---

## Overview

Replace the current "call agents directly" architecture with a **task-based orchestration system**. Tasks are the only public interface — agent families become internal implementation details.

**Current:** `message_service → route_and_execute() → classifier → agent.execute() → SSE events`
**New:** `message_service → orchestrator.handle_message() → { router | pinned task agent } → SSE events`

### Core Concepts

- **Task**: A unit of work that produces an artifact. Has its own message history, lifecycle (active → completed/abandoned), and a pinned agent that handles all messages until the task ends.
- **Router Agent**: Pydantic AI agent that decides: respond directly (ChatResponse) or open a task (OpenTask). Owns the conversation memory.
- **Task Agent**: Pydantic AI agent pinned to a task. Returns TaskContinue (keep working) or TaskEnd (done/out-of-scope).
- **Orchestrator**: Pure Python traffic controller. No LLM. Routes messages to router or pinned task. Manages state.

### Task Types

| Task Type | Agent Family | Artifact Type | Description |
|-----------|-------------|---------------|-------------|
| `deep_search` | deep_search | report | Legal research, analysis, precedent search |
| `end_services` | end_services | contract/memo/legal_opinion | Document generation |
| `extraction` | extraction | summary | Document processing (details deferred) |

**NOT tasks:** Memory agent (invoked differently, no task lifecycle). Router direct responses (simple chat, clarifications).

### Task Arguments

A task can be opened to:
1. **Create new** — empty artifact, fresh research/drafting
2. **Edit existing** — load previous artifact, refine it (user says "edit that report" or router infers)

---

## Task 1: Database Migration — `task_state` Table

### Agent: @sql-migration

**Depends on:** Nothing (first step, foundation for everything else)

**Migration: `021_create_task_state_table.sql` (NEW — via Supabase MCP `apply_migration`)**

```sql
CREATE TYPE task_status_enum AS ENUM ('active', 'completed', 'abandoned');

CREATE TABLE task_state (
    task_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(conversation_id),
    user_id UUID NOT NULL REFERENCES users(user_id),
    agent_family agent_family_enum NOT NULL,
    status task_status_enum NOT NULL DEFAULT 'active',
    artifact_id UUID REFERENCES artifacts(artifact_id),
    briefing TEXT NOT NULL,
    summary TEXT,
    history_json JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    ended_at TIMESTAMPTZ
);

-- Fast lookup: active task for a conversation
CREATE INDEX idx_task_state_active
    ON task_state(conversation_id)
    WHERE status = 'active';

-- History by conversation
CREATE INDEX idx_task_state_conversation
    ON task_state(conversation_id, created_at DESC);

-- RLS
ALTER TABLE task_state ENABLE ROW LEVEL SECURITY;

CREATE POLICY task_state_select ON task_state
    FOR SELECT USING (user_id = get_current_user_id());

CREATE POLICY task_state_insert ON task_state
    FOR INSERT WITH CHECK (user_id = get_current_user_id());

CREATE POLICY task_state_update ON task_state
    FOR UPDATE USING (user_id = get_current_user_id());
```

**Enum fix in `shared/types.py` (MODIFY):**
Remove `SIMPLE_SEARCH = "simple_search"` from `AgentFamily`. Leave the PostgreSQL enum unchanged (existing rows may reference it).

### Validation (Task 1)

| # | Check | Agent/Tool | Pass Criteria |
|---|-------|------------|---------------|
| 1 | `SELECT * FROM task_state LIMIT 0` | `mcp__supabase__execute_sql` | Table exists, no errors |
| 2 | `SELECT enum_range(NULL::task_status_enum)` | `mcp__supabase__execute_sql` | Returns `{active,completed,abandoned}` |
| 3 | RLS enabled | `mcp__supabase__execute_sql`: `SELECT relname, relrowsecurity FROM pg_class WHERE relname='task_state'` | `relrowsecurity = true` |
| 4 | 3 RLS policies exist | `mcp__supabase__execute_sql`: `SELECT policyname FROM pg_policies WHERE tablename='task_state'` | select, insert, update policies present |
| 5 | Indexes exist | `mcp__supabase__execute_sql`: `SELECT indexname FROM pg_indexes WHERE tablename='task_state'` | `idx_task_state_active`, `idx_task_state_conversation` |
| 6 | Python enum updated | `python -c "from shared.types import AgentFamily; assert not hasattr(AgentFamily, 'SIMPLE_SEARCH')"` | No errors |

---

## Task 2: Data Models — `agents/models.py`

### Agent: @shared-foundation

**Depends on:** Nothing (pure Pydantic models, no DB or imports from other new files)

**File: `agents/models.py` (NEW)**

All Pydantic output models for the router and task agents:

```python
"""Pydantic output models for router and task agents."""
from pydantic import BaseModel, Field
from typing import Literal, Optional


# ── Router outputs ──

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
    artifact_id: Optional[str] = Field(
        default=None,
        description="If editing an existing artifact, its UUID. None for new tasks."
    )


# ── Task agent outputs ──

class TaskContinue(BaseModel):
    """Task agent continues working."""
    type: Literal["continue"] = "continue"
    response: str = Field(description="What to show the user this turn")
    artifact: str = Field(description="Full markdown artifact — complete, not a diff")


class TaskEnd(BaseModel):
    """Task agent is done or detected out-of-scope message."""
    type: Literal["end"] = "end"
    reason: Literal["completed", "out_of_scope"] = Field(
        description="Why the task is ending"
    )
    summary: str = Field(
        description="Recap: key findings, user modifications, references used. "
        "Persisted in conversation memory."
    )
    artifact: str = Field(description="Final state of the markdown artifact")
    last_response: str = Field(
        description="Final message to show the user"
    )
```

### Validation (Task 2)

| # | Check | Agent/Tool | Pass Criteria |
|---|-------|------------|---------------|
| 1 | Import all models | `python -c "from agents.models import ChatResponse, OpenTask, TaskContinue, TaskEnd"` | No errors |
| 2 | ChatResponse instantiation | `python -c "from agents.models import ChatResponse; r = ChatResponse(message='test'); assert r.type == 'chat'"` | Passes |
| 3 | OpenTask validation | `python -c "from agents.models import OpenTask; t = OpenTask(task_type='deep_search', briefing='x'); assert t.artifact_id is None"` | Passes |
| 4 | TaskEnd validation | `python -c "from agents.models import TaskEnd; t = TaskEnd(reason='completed', summary='s', artifact='a', last_response='r'); assert t.type == 'end'"` | Passes |

---

## Task 3: Task State Management — `agents/state.py`

### Agent: @fastapi-backend

**Depends on:** Task 1 (table must exist), Task 2 (imports TaskInfo concept)

**File: `agents/state.py` (NEW)**

DB helpers for `task_state` table + orchestrator state dataclass.

```python
"""Task state management — DB operations and state dataclass."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from supabase import Client as SupabaseClient


@dataclass
class TaskInfo:
    """In-memory representation of an active task."""
    task_id: str
    task_type: str              # "deep_search" | "end_services" | "extraction"
    agent_family: str           # Same as task_type for now
    artifact_id: str | None     # Set after first TaskContinue or loaded from existing
    current_artifact: str       # Latest artifact markdown
    history: list[dict]         # Serialized Pydantic AI messages (task-scoped)


def get_active_task(supabase: SupabaseClient, conversation_id: str) -> TaskInfo | None:
    """Load active task for a conversation from DB. Returns None if no active task."""
    ...

def create_task(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
    agent_family: str,
    briefing: str,
    artifact_id: str | None = None,
) -> TaskInfo:
    """Insert new task_state row. Load existing artifact if artifact_id provided."""
    ...

def update_task_history(supabase: SupabaseClient, task_id: str, history: list[dict]) -> None:
    """Persist task message history to DB."""
    ...

def update_task_artifact(supabase: SupabaseClient, task_id: str, artifact_id: str) -> None:
    """Link artifact to task."""
    ...

def complete_task(
    supabase: SupabaseClient,
    task_id: str,
    summary: str,
    status: str = "completed",
) -> None:
    """Mark task as completed/abandoned, set ended_at, persist summary."""
    ...
```

### Validation (Task 3)

| # | Check | Agent/Tool | Pass Criteria |
|---|-------|------------|---------------|
| 1 | Import all functions | `python -c "from agents.state import get_active_task, create_task, update_task_history, update_task_artifact, complete_task, TaskInfo"` | No errors |
| 2 | TaskInfo dataclass | `python -c "from agents.state import TaskInfo; t = TaskInfo(task_id='x', task_type='deep_search', agent_family='deep_search', artifact_id=None, current_artifact='', history=[])"` | No errors |
| 3 | create_task + get_active_task round-trip | @validate: POST create_task, then get_active_task on same conversation_id | Returns matching TaskInfo |
| 4 | complete_task sets status | @validate: call complete_task, then `SELECT status, ended_at FROM task_state WHERE task_id=X` via `mcp__supabase__execute_sql` | `status='completed'`, `ended_at IS NOT NULL` |
| 5 | get_active_task returns None after completion | @validate: call get_active_task on conversation with completed task | Returns `None` |

---

## Task 4: Mock Router — `agents/router/router.py`

### Agent: @fastapi-backend

**Depends on:** Task 2 (imports ChatResponse, OpenTask from agents.models)

**File: `agents/router/router.py` (REWRITE)**

Phase 1 mock: keyword-based classification that returns `ChatResponse` or `OpenTask` directly (no Pydantic AI yet — that comes when we add real LLM in Wave 9+).

```python
"""Mock router — keyword classification, returns ChatResponse or OpenTask."""
from agents.models import ChatResponse, OpenTask

TASK_KEYWORDS = {
    "deep_search": ["بحث", "تحليل", "مقارنة", "تفصيل", "شرح", "ما حكم", "ما هي", "حقوق",
                     "search", "research", "analyze", "explain"],
    "end_services": ["عقد", "مسودة", "مذكرة", "خطاب", "صياغة", "contract", "draft", "memo"],
    "extraction": ["استخراج", "ملف", "PDF", "مستند", "وثيقة", "extract", "summarize"],
}


def mock_route(question: str, conversation_history: list[dict] | None = None) -> ChatResponse | OpenTask:
    """
    Mock router — keyword match to decide task type or direct response.

    Returns ChatResponse for greetings, simple questions.
    Returns OpenTask for anything matching task keywords.
    """
    q_lower = question.lower()

    # Check for task keywords
    for task_type, keywords in TASK_KEYWORDS.items():
        if any(kw in q_lower for kw in keywords):
            return OpenTask(
                task_type=task_type,
                briefing=f"User request: {question}",
                artifact_id=None,
            )

    # Default: direct chat response
    return ChatResponse(
        message="مرحباً! أنا لونا، مساعدك القانوني. كيف يمكنني مساعدتك اليوم؟"
    )
```

**Delete:** `agents/router/classifier.py` — replaced by mock_route().

### Validation (Task 4)

| # | Check | Agent/Tool | Pass Criteria |
|---|-------|------------|---------------|
| 1 | Import mock_route | `python -c "from agents.router.router import mock_route"` | No errors |
| 2 | Arabic legal keyword → OpenTask | `python -c "from agents.router.router import mock_route; r = mock_route('أريد بحث قانوني'); assert r.type == 'task' and r.task_type == 'deep_search'"` | Passes |
| 3 | Contract keyword → end_services | `python -c "from agents.router.router import mock_route; r = mock_route('أريد صياغة عقد'); assert r.task_type == 'end_services'"` | Passes |
| 4 | Greeting → ChatResponse | `python -c "from agents.router.router import mock_route; r = mock_route('مرحبا'); assert r.type == 'chat'"` | Passes |
| 5 | classifier.py deleted | `python -c "import agents.router.classifier"` | ImportError (file gone) |

---

## Task 5: Mock Task Agents (3 files)

### Agent: @fastapi-backend

**Depends on:** Task 2 (imports TaskContinue, TaskEnd from agents.models)

Each task agent is a mock that:
- Accepts question + current_artifact + is_first_turn
- Returns `TaskContinue` (with updated artifact) or `TaskEnd` (with summary)
- Uses keyword detection for out-of-scope (keywords from other task types)

### 5.1 `agents/deep_search/agent.py` (REWRITE)

```python
"""Mock deep search task agent."""
from agents.models import TaskContinue, TaskEnd

MOCK_REPORT = """# تقرير بحث قانوني

## ملخص تنفيذي
بناءً على تحليل الأنظمة ذات الصلة...

## التحليل القانوني
### أولاً: الإطار النظامي
يحكم هذا الموضوع نظام العمل...

## التوصيات
- مراجعة بنود العقد المبرم بين الطرفين
- التأكد من استيفاء الإجراءات النظامية
"""

MOCK_STREAM_TEXT = "أجري بحثاً معمقاً في الأنظمة السعودية..."

OUT_OF_SCOPE_KEYWORDS = ["عقد", "مسودة", "صياغة", "contract", "draft", "استخراج", "PDF"]


def mock_deep_search(
    question: str,
    current_artifact: str,
    is_first_turn: bool,
) -> TaskContinue | TaskEnd:
    """Mock task agent — returns TaskContinue with report or TaskEnd if out of scope."""
    q_lower = question.lower()

    if not is_first_turn and any(kw in q_lower for kw in OUT_OF_SCOPE_KEYWORDS):
        return TaskEnd(
            reason="out_of_scope",
            summary="تم إعداد تقرير بحثي حول السؤال المطروح.",
            artifact=current_artifact or MOCK_REPORT,
            last_response="يبدو أن طلبك خارج نطاق البحث القانوني. سأحولك للخدمة المناسبة.",
        )

    artifact = current_artifact if current_artifact else MOCK_REPORT
    return TaskContinue(
        response=MOCK_STREAM_TEXT,
        artifact=artifact,
    )
```

### 5.2 `agents/end_services/agent.py` (REWRITE)

Same pattern. Mock contract/memo content. Out-of-scope: research keywords, extraction keywords.

### 5.3 `agents/extraction/agent.py` (REWRITE)

Same pattern. Mock summary content. Out-of-scope: research keywords, drafting keywords.

### Validation (Task 5)

| # | Check | Agent/Tool | Pass Criteria |
|---|-------|------------|---------------|
| 1 | Import deep_search | `python -c "from agents.deep_search.agent import mock_deep_search"` | No errors |
| 2 | Import end_services | `python -c "from agents.end_services.agent import mock_end_services"` | No errors |
| 3 | Import extraction | `python -c "from agents.extraction.agent import mock_extraction"` | No errors |
| 4 | First turn → TaskContinue | `python -c "from agents.deep_search.agent import mock_deep_search; r = mock_deep_search('بحث', '', True); assert r.type == 'continue'"` | Passes |
| 5 | Out-of-scope → TaskEnd | `python -c "from agents.deep_search.agent import mock_deep_search; r = mock_deep_search('أريد صياغة عقد', 'existing', False); assert r.type == 'end' and r.reason == 'out_of_scope'"` | Passes |
| 6 | Artifact preserved on continue | `python -c "from agents.deep_search.agent import mock_deep_search; r = mock_deep_search('تفاصيل', 'my artifact', False); assert r.artifact == 'my artifact'"` | Passes |

---

## Task 6: Orchestrator — `agents/orchestrator.py`

### Agent: @sse-streaming

**Depends on:** Task 2 (models), Task 3 (state), Task 4 (router), Task 5 (task agents)
**This is the critical path — all previous tasks must complete first.**

**File: `agents/orchestrator.py` (NEW)**

The core traffic controller. Pure Python, no LLM. Yields SSE event dicts.

```python
"""Task orchestrator — routes messages to router or pinned task agent."""
from __future__ import annotations
from typing import AsyncGenerator
from supabase import Client as SupabaseClient

from agents.models import ChatResponse, OpenTask, TaskContinue, TaskEnd
from agents.state import (
    TaskInfo, get_active_task, create_task,
    update_task_history, update_task_artifact, complete_task,
)


async def handle_message(
    question: str,
    user_id: str,
    conversation_id: str,
    supabase: SupabaseClient,
    case_id: str | None = None,
    explicit_task_type: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Main entry point — replaces route_and_execute().

    1. Check for active task on this conversation
    2. If active task → send message to pinned task agent
    3. If no active task → send message to router
       a. ChatResponse → yield response tokens
       b. OpenTask → create task, pin agent, run first turn
    """
    ...
```

### Internal Methods

```python
async def _route(question, supabase, user_id, conversation_id, case_id):
    """Run router agent, handle ChatResponse or OpenTask."""
    # 1. Load router conversation history from messages table
    # 2. Run mock router (keyword-based, returns ChatResponse or OpenTask)
    # 3. If ChatResponse → yield token events for the message
    # 4. If OpenTask → call _open_task()

async def _open_task(task_type, briefing, artifact_id, supabase, user_id, conversation_id, case_id):
    """Create task, pin agent, run first turn."""
    # 1. Create task_state row via create_task()
    # 2. If artifact_id → load existing artifact content
    # 3. Yield {"type": "task_started", "task_id": ..., "task_type": ...}
    # 4. Run task agent first turn with briefing as initial prompt
    # 5. Handle TaskContinue or TaskEnd from first turn

async def _open_task_explicit(question, task_type, supabase, user_id, conversation_id, case_id):
    """User explicitly chose a task type — generate briefing from question, then open."""
    # briefing = question itself (no router LLM needed)
    # artifact_id = None (new task)
    # Delegate to _open_task()

async def _run_task(question, task, supabase, user_id, conversation_id, case_id):
    """Send message to pinned task agent."""
    # 1. Load task history from task.history
    # 2. Run task agent with question + history
    # 3. If TaskContinue:
    #    a. Update task.current_artifact
    #    b. Persist artifact to DB (create or update)
    #    c. Update task history in DB
    #    d. Yield token events + artifact_updated event
    # 4. If TaskEnd:
    #    a. Persist final artifact
    #    b. Inject summary into conversation memory
    #    c. Mark task completed via complete_task()
    #    d. Yield {"type": "task_ended", "task_id": ..., "summary": ...}
    #    e. If reason == "out_of_scope" → re-process question through _route()
```

### Validation (Task 6)

| # | Check | Agent/Tool | Pass Criteria |
|---|-------|------------|---------------|
| 1 | Import orchestrator | `python -c "from agents.orchestrator import handle_message"` | No errors |
| 2 | handle_message is async generator | `python -c "import inspect; from agents.orchestrator import handle_message; assert inspect.isasyncgenfunction(handle_message)"` | Passes |
| 3 | Standalone flow test (no server) | @validate: Python script — call `handle_message()` with mock Supabase, collect all yielded events | Events include token events + done |

---

## Task 7: Wire Orchestrator into Message Service

### Agent: @fastapi-backend + @sse-streaming (pair)

**Depends on:** Task 6 (orchestrator must exist and import cleanly)

### 7.1 `backend/app/services/message_service.py` (MODIFY)

**Change import:**
```python
# OLD:
from agents.router.router import route_and_execute
# NEW:
from agents.orchestrator import handle_message
```

**Change call site in `send_message_stream()` → `pipeline_producer()`:**
```python
# OLD:
async for event in route_and_execute(
    question=content, context=context, user_id=user_id,
    conversation_id=conversation_id, supabase=supabase,
    case_id=conv.get("case_id"), explicit_agent=agent_family, modifiers=modifiers,
):

# NEW:
async for event in handle_message(
    question=content, user_id=user_id,
    conversation_id=conversation_id, supabase=supabase,
    case_id=conv.get("case_id"), explicit_task_type=task_type,
):
```

**Add new SSE event handlers:**
```python
elif event_type == "task_started":
    await queue.put(_sse_event("task_started", {
        "task_id": event["task_id"],
        "task_type": event["task_type"],
    }))

elif event_type == "task_ended":
    await queue.put(_sse_event("task_ended", {
        "task_id": event["task_id"],
        "summary": event.get("summary", ""),
    }))

elif event_type == "artifact_updated":
    await queue.put(_sse_event("artifact_updated", {
        "artifact_id": event["artifact_id"],
    }))
```

**Remove `modifiers` parameter** from `send_message_stream()` signature.

### 7.2 `backend/app/api/messages.py` (MODIFY)

Change parameter name passed to service:
```python
# OLD:
agent_family=body.agent_family, modifiers=body.modifiers,
# NEW:
task_type=body.task_type,
```

Remove `modifiers` from the endpoint.

### 7.3 `backend/app/models/requests.py` (MODIFY)

```python
class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    task_type: Optional[str] = Field(
        default=None,
        description="Explicit task type: deep_search, end_services, extraction"
    )
    attachment_ids: Optional[list[str]] = None
    # REMOVED: agent_family, modifiers
```

### 7.4 `shared/types.py` (MODIFY — if not done in Task 1)

Confirm `SIMPLE_SEARCH` removed from `AgentFamily`.

### Validation (Task 7)

| # | Check | Agent/Tool | Pass Criteria |
|---|-------|------------|---------------|
| 1 | Backend imports clean | `python -c "from backend.app.main import app; print('OK')"` | No errors |
| 2 | No reference to route_and_execute | `grep -r "route_and_execute" backend/` | Zero matches |
| 3 | No reference to old modifiers | `grep -r "modifiers" backend/app/` | Zero matches (except comments) |
| 4 | SendMessageRequest has task_type | `python -c "from backend.app.models.requests import SendMessageRequest; r = SendMessageRequest(content='test', task_type='deep_search')"` | No errors |
| 5 | TypeScript build | `cd frontend && npx tsc --noEmit` | Zero errors |

---

## Task 8: Clean Up Dead Code

### Agent: @fastapi-backend

**Depends on:** Task 7 (wiring must be complete before deleting old code)

### 8.1 Delete Files

| File | Reason |
|------|--------|
| `agents/router/classifier.py` | Replaced by mock_route() in router.py |
| `agents/simple_search/agent.py` | Merged into deep_search |
| `agents/simple_search/__init__.py` | Directory removed |
| `agents/rag/pipeline.py` | Dead code, never called |
| `agents/rag/retriever.py` | Dead code |
| `agents/rag/prompts.py` | Dead code |
| `agents/rag/__init__.py` | Directory removed |
| `agents/embeddings/service.py` | Dead code |
| `agents/embeddings/__init__.py` | Directory removed |

### 8.2 Modify `agents/base/agent.py`

Remove `BaseAgent` protocol and `MockAgentBase`. These are replaced by Pydantic AI agents.
Keep the file but replace contents with a note or remove entirely if nothing depends on it.

### 8.3 Remove `plan()` and `reflect()` from all agents

The modifier system is dropped. No agent needs these methods.

### 8.4 Update `agents/router/router.py` registry

Remove `SimpleSearchAgent` from `_AGENT_REGISTRY`. The old `route_and_execute()` function can be deleted entirely.

### Validation (Task 8)

| # | Check | Agent/Tool | Pass Criteria |
|---|-------|------------|---------------|
| 1 | Deleted files gone | `python -c "import agents.router.classifier"` | ImportError |
| 2 | simple_search gone | `python -c "import agents.simple_search"` | ImportError |
| 3 | rag directory gone | `python -c "import agents.rag"` | ImportError |
| 4 | embeddings gone | `python -c "import agents.embeddings"` | ImportError |
| 5 | No dangling imports | `python -c "from backend.app.main import app"` | No errors — proves nothing imports deleted code |
| 6 | No dead references | `grep -r "simple_search\|SimpleSearch\|route_and_execute\|BaseAgent\|MockAgentBase" agents/ backend/` | Zero matches (except comments/docs) |

---

## Task 9: SSE Event Contract Update

### Agent: @integration-lead (verify) + @nextjs-frontend (update frontend types)

**Depends on:** Task 7 (new events must be emitted before frontend can handle them)

### New SSE Events

| Event | When | Data |
|-------|------|------|
| `task_started` | Orchestrator opens a new task | `{task_id, task_type}` |
| `task_ended` | Task completes or abandons | `{task_id, summary}` |
| `artifact_updated` | Task agent returns TaskContinue with updated artifact | `{artifact_id}` |

### Modified SSE Events

| Event | Change |
|-------|--------|
| `agent_selected` | Renamed conceptually — still emitted, value is the task_type |
| `artifact_created` | Still emitted on first artifact creation within a task |

### Frontend Type Updates

**File: `frontend/types/sse.ts` or equivalent (MODIFY)**

Add `task_started`, `task_ended`, `artifact_updated` to the SSE event type union.

**File: `frontend/hooks/use-chat.ts` (MODIFY)**

Handle new event types in the SSE event parser:
- `task_started` → update chat store with active task info
- `task_ended` → clear active task, show summary
- `artifact_updated` → trigger artifact panel refresh

### Validation (Task 9)

| # | Check | Agent/Tool | Pass Criteria |
|---|-------|------------|---------------|
| 1 | Frontend types include new events | @integration-lead: grep for `task_started` in frontend types | Present in type definitions |
| 2 | use-chat handles task_started | @integration-lead: grep for `task_started` in use-chat.ts | Handler exists |
| 3 | SSE contract matches | @integration-lead: compare backend event emission (orchestrator.py) with frontend event parsing (use-chat.ts) | All event types, field names match |
| 4 | TypeScript compiles | `cd frontend && npx tsc --noEmit` | Zero errors |

---

## Parallel vs Sequential Dependencies

```
                    ┌──────────────────┐
                    │   Task 1: DB     │
                    │  @sql-migration  │
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
    ┌─────────▼──────┐ ┌────▼─────────┐   │
    │  Task 2: Models│ │ Task 3: State │   │
    │ @shared-found. │ │ @fastapi-BE   │   │
    └────────┬───────┘ └────┬──────────┘   │
             │              │              │
    ┌────────▼──────────────▼──────────┐   │
    │  PARALLEL: Task 4 + Task 5       │   │
    │  @fastapi-backend                │   │
    │  (router rewrite + agent mocks)  │   │
    └────────────────┬─────────────────┘   │
                     │                     │
           ┌─────────▼──────────┐          │
           │  Task 6: Orchestr. │          │
           │  @sse-streaming    │          │
           └─────────┬──────────┘          │
                     │                     │
           ┌─────────▼──────────┐          │
           │ Task 7: Wiring     │          │
           │ @fastapi-BE + @sse │          │
           └─────────┬──────────┘          │
                     │                     │
        ┌────────────┼───────────────┐     │
        │            │               │     │
  ┌─────▼─────┐ ┌───▼────────┐      │     │
  │ Task 8:   │ │ Task 9:    │      │     │
  │ Cleanup   │ │ SSE Events │      │     │
  │ @fastapi  │ │ @integ +   │      │     │
  │           │ │ @nextjs-FE │      │     │
  └─────┬─────┘ └────┬───────┘      │     │
        │            │               │     │
        └────────────┼───────────────┘     │
                     │                     │
           ┌─────────▼──────────┐          │
           │  VALIDATION GATE   │          │
           │  @validate + @rls  │          │
           └────────────────────┘          │
```

### Recommended Execution Order

```
Phase 1 (parallel):  Task 1 (@sql-migration) + Task 2 (@shared-foundation)
Phase 2 (parallel):  Task 3 (@fastapi-backend) + Task 4 (@fastapi-backend) + Task 5 (@fastapi-backend)
    Note: Task 3 depends on Task 1. Tasks 4+5 depend on Task 2 only.
Phase 3 (sequential): Task 6 (@sse-streaming) — depends on Tasks 2-5
Phase 4 (sequential): Task 7 (@fastapi-backend + @sse-streaming) — depends on Task 6
Phase 5 (parallel):  Task 8 (@fastapi-backend) + Task 9 (@integration-lead + @nextjs-frontend)
Phase 6:             Validation Gate
```

---

## Final File Structure

```
agents/
├── __init__.py
├── orchestrator.py          # NEW — main entry point, replaces route_and_execute
├── models.py                # NEW — ChatResponse, OpenTask, TaskContinue, TaskEnd
├── state.py                 # NEW — TaskInfo, task_state DB helpers
├── router/
│   ├── __init__.py
│   └── router.py            # REWRITTEN — mock_route() returning ChatResponse|OpenTask
├── deep_search/
│   ├── __init__.py
│   └── agent.py             # REWRITTEN — mock_deep_search() returning TaskContinue|TaskEnd
├── end_services/
│   ├── __init__.py
│   └── agent.py             # REWRITTEN — mock_end_services()
├── extraction/
│   ├── __init__.py
│   └── agent.py             # REWRITTEN — mock_extraction()
├── memory/
│   ├── __init__.py
│   └── agent.py             # KEPT — not task-based, future scope
└── base/
    ├── __init__.py
    ├── context.py            # KEPT — build_agent_context() still useful for loading DB state
    └── artifact.py           # KEPT — create_agent_artifact() helper
```

**Deleted:**
```
agents/simple_search/         # Merged into deep_search
agents/router/classifier.py   # Replaced by mock_route
agents/rag/                   # Dead code
agents/embeddings/            # Dead code
agents/base/agent.py          # BaseAgent/MockAgentBase replaced
```

---

## File Manifest

| # | File | Action | Agent | Task |
|---|------|--------|-------|------|
| 1 | Migration: `021_create_task_state_table` | NEW (Supabase MCP) | @sql-migration | 1 |
| 2 | `shared/types.py` | MODIFY (remove SIMPLE_SEARCH) | @sql-migration | 1 |
| 3 | `agents/models.py` | NEW | @shared-foundation | 2 |
| 4 | `agents/state.py` | NEW | @fastapi-backend | 3 |
| 5 | `agents/router/router.py` | REWRITE | @fastapi-backend | 4 |
| 6 | `agents/deep_search/agent.py` | REWRITE | @fastapi-backend | 5 |
| 7 | `agents/end_services/agent.py` | REWRITE | @fastapi-backend | 5 |
| 8 | `agents/extraction/agent.py` | REWRITE | @fastapi-backend | 5 |
| 9 | `agents/orchestrator.py` | NEW | @sse-streaming | 6 |
| 10 | `backend/app/services/message_service.py` | MODIFY | @fastapi-backend + @sse-streaming | 7 |
| 11 | `backend/app/api/messages.py` | MODIFY | @fastapi-backend | 7 |
| 12 | `backend/app/models/requests.py` | MODIFY | @fastapi-backend | 7 |
| 13 | `agents/router/classifier.py` | DELETE | @fastapi-backend | 8 |
| 14 | `agents/simple_search/` | DELETE (dir) | @fastapi-backend | 8 |
| 15 | `agents/rag/` | DELETE (dir) | @fastapi-backend | 8 |
| 16 | `agents/embeddings/` | DELETE (dir) | @fastapi-backend | 8 |
| 17 | `agents/base/agent.py` | DELETE/MODIFY | @fastapi-backend | 8 |
| 18 | `frontend/types/sse.ts` (or equiv) | MODIFY | @nextjs-frontend | 9 |
| 19 | `frontend/hooks/use-chat.ts` | MODIFY | @nextjs-frontend | 9 |

**Total: 4 new + 4 rewrite + 6 modify + 5 delete = 19 file operations**

---

## Validation Gate (Wave 8)

### @rls-auditor

| # | Query | Tool | Pass Criteria |
|---|-------|------|---------------|
| 1 | `SELECT relname, relrowsecurity FROM pg_class WHERE relname='task_state'` | `mcp__supabase__execute_sql` | `relrowsecurity = true` |
| 2 | `SELECT policyname, cmd FROM pg_policies WHERE tablename='task_state'` | `mcp__supabase__execute_sql` | 3 policies: select, insert, update |
| 3 | Cross-user isolation: insert as user A, select as user B | `mcp__supabase__execute_sql` | User B sees 0 rows |

### @integration-lead

| # | Check | Method | Pass Criteria |
|---|-------|--------|---------------|
| 1 | SSE event types match | Compare `orchestrator.py` yield types with `use-chat.ts` parser | All event types handled |
| 2 | SendMessageRequest contract | Compare `requests.py` fields with frontend API call | `task_type` field name matches |
| 3 | task_state FK integrity | Verify `conversation_id`, `user_id`, `artifact_id` FKs match referenced tables | All FKs valid |
| 4 | No orphan imports | grep for deleted module names across entire codebase | Zero matches |

### @validate

**API Tests (via Python requests):**

| # | Test | Method | Pass Criteria |
|---|------|--------|---------------|
| 1 | Send message — no task_type (router path) | POST `/api/conversations/{id}/messages` with plain text | SSE stream: `token` events → `done` |
| 2 | Send message — explicit task_type=deep_search | POST with `task_type: "deep_search"` | SSE: `task_started` → `token` → `artifact_created` → `done` |
| 3 | Follow-up message (task pinned) | POST second message to same conversation | SSE: `token` → `artifact_updated` → `done` (no `task_started`) |
| 4 | Out-of-scope during task | POST message with contract keywords during deep_search task | SSE: `task_ended` → re-routed → new response |
| 5 | Message after task ended | POST to conversation where task is completed | Router handles normally, no pinned task |

**Database Checks (via Supabase MCP):**

| # | Query | Tool | Pass Criteria |
|---|-------|------|---------------|
| 6 | `SELECT * FROM task_state WHERE conversation_id = X` | `mcp__supabase__execute_sql` | Rows with correct status, summary, artifact_id |
| 7 | `SELECT status, ended_at FROM task_state WHERE task_id = Y` | `mcp__supabase__execute_sql` | `completed` tasks have `ended_at IS NOT NULL` |
| 8 | `SELECT COUNT(*) FROM task_state WHERE status='active' AND conversation_id = Z` | `mcp__supabase__execute_sql` | Max 1 active task per conversation |

**Playwright MCP (Browser Tests):**

| # | Test | MCP Tools | Pass Criteria |
|---|------|-----------|---------------|
| 9 | Send message → SSE stream renders | `browser_navigate`, `browser_type`, `browser_click`, `browser_snapshot` | Response text appears in chat |
| 10 | Task creates artifact → panel shows | `browser_snapshot` after task_started | Artifact panel visible with content |
| 11 | Task ended → artifact persists | `browser_snapshot` after task_ended | Artifact still visible, summary shown |

**Import & Build Checks:**

| # | Check | Command | Pass Criteria |
|---|-------|---------|---------------|
| 12 | All new modules import | `python -c "from agents.models import *; from agents.state import *; from agents.orchestrator import handle_message"` | No errors |
| 13 | Backend app imports | `python -c "from backend.app.main import app"` | No errors |
| 14 | Frontend TypeScript | `cd frontend && npx tsc --noEmit` | Zero errors |
| 15 | Frontend lint | `cd frontend && npm run lint` | Zero errors |

---

## Success Criteria (Wave 8)

- [ ] `task_state` table exists with RLS enabled and 3 policies
- [ ] `task_status_enum` has values: active, completed, abandoned
- [ ] `SIMPLE_SEARCH` removed from Python `AgentFamily` enum
- [ ] `agents/models.py` exports: ChatResponse, OpenTask, TaskContinue, TaskEnd
- [ ] `agents/state.py` exports: TaskInfo, get_active_task, create_task, complete_task
- [ ] `agents/orchestrator.py` exports: handle_message (async generator)
- [ ] `agents/router/router.py` exports: mock_route (keyword-based)
- [ ] 3 mock task agents return TaskContinue or TaskEnd correctly
- [ ] `classifier.py` deleted, `simple_search/` deleted, `rag/` deleted, `embeddings/` deleted
- [ ] `message_service.py` calls `handle_message()` instead of `route_and_execute()`
- [ ] `SendMessageRequest` uses `task_type` field (not `agent_family` / `modifiers`)
- [ ] New SSE events emitted: `task_started`, `task_ended`, `artifact_updated`
- [ ] Frontend handles all 3 new SSE event types
- [ ] No dangling imports to deleted modules
- [ ] Active task pinning works: follow-up messages go to pinned agent
- [ ] Out-of-scope detection triggers task_ended + re-routing
- [ ] Max 1 active task per conversation enforced
- [ ] All error messages remain in Arabic (Absolute Rule #5)
- [ ] TypeScript compiles with zero errors
- [ ] Backend imports with zero errors
