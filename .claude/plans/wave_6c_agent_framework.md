# Wave 6C — Agent Framework + Backend Wiring

> **Parent:** `wave_6_integration_overview.md`
> **Dependencies:** Wave 6B (artifact_service must exist for agents to create artifacts)
> **Build Agents:** @sse-streaming (agent framework), @fastapi-backend (wiring)
> **Quality Agents:** Backend import tests, mock execution tests
> **MCP:** None specific (Python-only work)

---

## Pre-Flight Checks

```
1. Verify Gate 6B passed (artifact APIs working, preferences APIs working)
2. Verify: from backend.app.services.artifact_service import create_artifact
3. Verify: from shared.types import AgentFamily, ArtifactType, AgentContext
4. Read existing agents/rag/pipeline.py to understand current mock pattern
5. Read existing message_service.py to understand SSE event flow
```

---

## Stage 1: Agent Base Layer (3 new files)

**Agent:** @sse-streaming

### 1.1 `agents/base/__init__.py` (NEW)

Package init — exports BaseAgent, AgentContext re-export.

### 1.2 `agents/base/agent.py` (NEW)

BaseAgent protocol defining the interface all agents must implement.

```python
from typing import AsyncGenerator, Protocol, runtime_checkable
from shared.types import AgentFamily, AgentContext

@runtime_checkable
class BaseAgent(Protocol):
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
```

**Default plan/reflect implementations** (mixin or base class):

```python
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
```

### 1.3 `agents/base/context.py` (NEW)

Context builder — assembles `AgentContext` from raw data.

```python
from supabase import Client as SupabaseClient
from shared.types import AgentContext, AgentFamily, ChatMessage

async def build_agent_context(
    supabase: SupabaseClient,
    question: str,
    user_id: str,
    conversation_id: str,
    case_id: str | None = None,
    agent_family: AgentFamily | None = None,
    modifiers: list[str] | None = None,
) -> AgentContext:
    """
    Build AgentContext from database state.

    Loads:
    - memory_md from artifacts table (if case-linked)
    - user_preferences from user_preferences table
    - user_templates if agent is end_services
    - conversation_history from messages table
    - case_metadata from lawyer_cases table
    - document_summaries from case_documents table
    """
```

**Key logic:**
- `memory_md`: Query `artifacts WHERE user_id=X AND case_id=Y AND artifact_type='memory_file'` → return `content_md`
- `user_preferences`: Query `user_preferences WHERE user_id=X` → return `preferences` JSONB
- `user_templates`: Only load if `agent_family == END_SERVICES`
- `conversation_history`: Last N messages from `messages` table
- `case_metadata`: From `lawyer_cases` if `case_id` provided
- `document_summaries`: From `case_documents` if `case_id` provided

### 1.4 `agents/base/artifact.py` (NEW)

Thin helper for agents to create artifacts without importing backend directly.

```python
from backend.app.services.artifact_service import create_artifact as _create

async def create_agent_artifact(
    supabase, user_id, conversation_id, case_id,
    agent_family, artifact_type, title, content_md,
    is_editable=False, metadata=None,
) -> dict:
    """Convenience wrapper for agents to create artifacts."""
    return _create(
        supabase, user_id, conversation_id, case_id,
        agent_family, artifact_type, title, content_md,
        is_editable, metadata,
    )
```

---

## Stage 2: Router + Classifier (3 new files)

**Agent:** @sse-streaming

### 2.1 `agents/router/__init__.py` (NEW)

Package init.

### 2.2 `agents/router/classifier.py` (NEW)

Mock intent classifier — keyword-based routing (will be replaced by LLM in Wave 7).

```python
from shared.types import AgentFamily, AgentContext

KEYWORD_MAP = {
    AgentFamily.END_SERVICES: ["عقد", "contract", "مسودة", "نموذج", "خطاب", "مذكرة"],
    AgentFamily.EXTRACTION: ["استخراج", "ملف", "PDF", "مستند", "وثيقة"],
    AgentFamily.MEMORY: ["ذاكرة", "memory", "أضف", "تذكر", "سجل"],
    AgentFamily.DEEP_SEARCH: ["تحليل", "مقارنة", "تفصيل", "شرح مفصل"],
}

async def classify(question: str, context: AgentContext) -> AgentFamily:
    """Mock classifier — keyword matching. Returns SIMPLE_SEARCH as default."""
    question_lower = question.lower()
    for family, keywords in KEYWORD_MAP.items():
        if any(kw in question_lower for kw in keywords):
            return family
    # Long questions default to deep search
    if len(question) > 100:
        return AgentFamily.DEEP_SEARCH
    return AgentFamily.SIMPLE_SEARCH
```

### 2.3 `agents/router/router.py` (NEW)

Main entry point — orchestrates classification → context building → agent execution.

```python
from typing import AsyncGenerator
from shared.types import AgentFamily, AgentContext

# Agent registry
from agents.simple_search.agent import SimpleSearchAgent
from agents.deep_search.agent import DeepSearchAgent
from agents.end_services.agent import EndServicesAgent
from agents.extraction.agent import ExtractionAgent
from agents.memory.agent import MemoryAgent
from agents.router.classifier import classify
from agents.base.context import build_agent_context

AGENT_REGISTRY = {
    AgentFamily.SIMPLE_SEARCH: SimpleSearchAgent(),
    AgentFamily.DEEP_SEARCH: DeepSearchAgent(),
    AgentFamily.END_SERVICES: EndServicesAgent(),
    AgentFamily.EXTRACTION: ExtractionAgent(),
    AgentFamily.MEMORY: MemoryAgent(),
}

async def route_and_execute(
    question: str,
    context: dict,
    user_id: str,
    conversation_id: str,
    supabase=None,
    case_id: str | None = None,
    explicit_agent: str | None = None,
    modifiers: list[str] | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Main pipeline entry point.

    1. explicit_agent set → use that family (skip classifier)
    2. Else → classify() → pick family
    3. Build AgentContext
    4. If 'reflect' in modifiers → yield from agent.reflect()
    5. If 'plan' in modifiers → yield from agent.plan()
    6. Yield from agent.execute()
    """
    modifiers = modifiers or []

    # 1. Determine agent family
    if explicit_agent:
        family = AgentFamily(explicit_agent)
    else:
        # Build minimal context for classification
        ctx_for_classify = AgentContext(
            question=question, conversation_id=conversation_id, user_id=user_id
        )
        family = await classify(question, ctx_for_classify)

    # 2. Yield routing event (tells frontend which agent was selected)
    yield {"type": "agent_selected", "agent_family": family.value}

    # 3. Build full AgentContext
    agent_ctx = await build_agent_context(
        supabase=supabase,
        question=question,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        agent_family=family,
        modifiers=modifiers,
    )

    # 4. Get agent instance
    agent = AGENT_REGISTRY[family]

    # 5. Run modifiers first
    if "reflect" in modifiers:
        async for event in agent.reflect(agent_ctx):
            yield event

    if "plan" in modifiers:
        async for event in agent.plan(agent_ctx):
            yield event

    # 6. Run main execution
    async for event in agent.execute(agent_ctx):
        yield event
```

---

## Stage 3: Five Mock Agents (5 new files + 5 __init__.py)

**Agent:** @sse-streaming

Each agent follows the same mock pattern: accept `AgentContext`, yield Arabic tokens, optionally create artifacts.

### 3.1 `agents/simple_search/__init__.py` + `agents/simple_search/agent.py`

- **Input:** question + memory_md
- **Output:** streaming mock Arabic tokens → citations → done
- **No artifact created**
- Mock response: "وفقاً لنظام العمل السعودي، المادة 74..."

### 3.2 `agents/deep_search/__init__.py` + `agents/deep_search/agent.py`

- **Input:** question + memory_md
- **Output:** streaming tokens → **creates report artifact** → citations → done
- **Artifact:** `agent_family=deep_search, artifact_type=report, is_editable=False`
- Mock title: "تقرير بحث: " + question[:50]

### 3.3 `agents/end_services/__init__.py` + `agents/end_services/agent.py`

- **Input:** question + memory_md + user_templates
- **Output:** streaming tokens → **creates contract/memo artifact** → done
- **Artifact:** `agent_family=end_services, artifact_type=contract, is_editable=True`
- Mock content: Arabic contract template text

### 3.4 `agents/extraction/__init__.py` + `agents/extraction/agent.py`

- **Input:** question (file reference) + memory_md
- **Output:** streaming tokens → **creates summary artifact** → done
- **Artifact:** `agent_family=extraction, artifact_type=summary, is_editable=False`
- Mock content: Arabic document summary

### 3.5 `agents/memory/__init__.py` + `agents/memory/agent.py`

- **Input:** question + memory_md + case_id
- **Output:** streaming tokens → **creates/updates memory.md artifact** → done
- **Artifact:** `agent_family=memory, artifact_type=memory_file, is_editable=True`
- **Also:** Writes mock entry to `case_memories` table (only agent with App DB write access)
- Mock content: Appends "- [mock] " + question to memory_md

**IMPORTANT:** Each agent must create REAL artifacts in the database via `create_agent_artifact()`. The mock is only in the text content and LLM logic — the database operations are real.

---

## Stage 4: Wire Router into Message Service (3 modified files)

**Agent:** @fastapi-backend
**Dependencies:** Stage 3 (router + agents must exist)

### 4.1 `backend/app/services/message_service.py` (MODIFY)

**Replace:**
```python
from agents.rag.pipeline import query as rag_query
```
**With:**
```python
from agents.router.router import route_and_execute
```

**Modify `send_message()` signature:**
```python
async def send_message(
    supabase: SupabaseClient,
    auth_id: str,
    conversation_id: str,
    *,
    content: str,
    agent_family: str | None = None,   # NEW
    modifiers: list[str] | None = None, # NEW
) -> AsyncGenerator[str, None]:
```

**Replace RAG call block** with:
```python
async for event in route_and_execute(
    question=content,
    context=context,
    user_id=user_id,
    conversation_id=conversation_id,
    supabase=supabase,
    case_id=conv.get("case_id"),
    explicit_agent=agent_family,
    modifiers=modifiers,
):
    event_type = event.get("type")

    if event_type == "token":
        # ... existing token handling ...

    elif event_type == "citations":
        # ... existing citations handling ...

    elif event_type == "artifact_created":
        # NEW: forward artifact_created event to frontend
        yield _sse_event("artifact_created", {
            "artifact_id": event["artifact_id"],
            "artifact_type": event["artifact_type"],
            "title": event["title"],
        })

    elif event_type == "agent_selected":
        # NEW: tell frontend which agent was selected
        yield _sse_event("agent_selected", {
            "agent_family": event["agent_family"],
        })

    elif event_type == "done":
        # ... existing done handling ...
```

### 4.2 `backend/app/services/context_service.py` (MODIFY)

Add to `build_context()`:
- Load `memory_md` from artifacts table (for case-linked conversations)
- Load `user_preferences` from user_preferences table

```python
# In build_context():
# After loading case metadata...
if case_id:
    memory_artifact = supabase.table("artifacts") \
        .select("content_md") \
        .eq("user_id", user_id) \
        .eq("case_id", case_id) \
        .eq("artifact_type", "memory_file") \
        .is_("deleted_at", "null") \
        .maybe_single() \
        .execute()
    context["memory_md"] = memory_artifact.data["content_md"] if memory_artifact and memory_artifact.data else None

# Load user preferences
prefs = supabase.table("user_preferences") \
    .select("preferences") \
    .eq("user_id", user_id) \
    .maybe_single() \
    .execute()
context["user_preferences"] = prefs.data["preferences"] if prefs and prefs.data else {}
```

### 4.3 `backend/app/api/messages.py` (MODIFY)

Pass new fields through to `send_message()`:

```python
# Current: sends only content
# Modified: also passes agent_family and modifiers from request body

# Update SendMessageRequest in requests.py:
class SendMessageRequest(BaseModel):
    content: str
    agent_family: Optional[str] = None   # NEW
    modifiers: Optional[list[str]] = None # NEW

# In the route handler:
event_generator = send_message(
    supabase, auth_user.auth_id, conversation_id,
    content=body.content,
    agent_family=body.agent_family,    # NEW
    modifiers=body.modifiers,          # NEW
)
```

---

## Validation Gate 6C

**All must pass before proceeding to Wave 6D:**

| # | Check | Method | Pass Criteria |
|---|-------|--------|---------------|
| 1 | Agent imports | `python -c "from agents.router.router import route_and_execute"` | No errors |
| 2 | Base imports | `python -c "from agents.base.agent import BaseAgent, MockAgentBase"` | No errors |
| 3 | All families | `python -c "from agents.simple_search.agent import ..."` (×5) | All 5 import |
| 4 | Backend intact | `python -c "from backend.app.main import app"` | No errors |
| 5 | Simple search test | POST /messages with plain text | token + citations + done events (no artifact) |
| 6 | Deep search test | POST /messages with `agent_family=deep_search` | token + artifact_created + citations + done |
| 7 | End services test | POST /messages with `agent_family=end_services` | token + artifact_created + done |
| 8 | Memory test | POST /messages with `agent_family=memory` (case conversation) | token + artifact_created + done |
| 9 | Agent selected | POST /messages with no agent_family | `agent_selected` event shows auto-classified family |
| 10 | Artifact in DB | After deep_search test: GET /artifacts/{id} | Returns created artifact with content |
| 11 | Old mock removed | `from agents.rag.pipeline import query` still works (backward compat) | Import succeeds |

### Test Script

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@luna.ai","password":"TestLuna@2025"}' | jq -r .access_token)

# Test 1: Plain text → auto-routed
curl -N http://localhost:8000/api/v1/conversations/{conv_id}/messages \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"ما هي حقوق العامل؟"}'
# Expect: event: agent_selected, event: token (×N), event: citations, event: done

# Test 2: Explicit deep search
curl -N http://localhost:8000/api/v1/conversations/{conv_id}/messages \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"ما هي شروط فسخ العقد؟","agent_family":"deep_search"}'
# Expect: event: agent_selected, event: token (×N), event: artifact_created, event: citations, event: done
```

---

## File Manifest

| File | Action | Agent |
|------|--------|-------|
| `agents/base/__init__.py` | NEW | @sse-streaming |
| `agents/base/agent.py` | NEW | @sse-streaming |
| `agents/base/context.py` | NEW | @sse-streaming |
| `agents/base/artifact.py` | NEW | @sse-streaming |
| `agents/router/__init__.py` | NEW | @sse-streaming |
| `agents/router/classifier.py` | NEW | @sse-streaming |
| `agents/router/router.py` | NEW | @sse-streaming |
| `agents/simple_search/__init__.py` | NEW | @sse-streaming |
| `agents/simple_search/agent.py` | NEW | @sse-streaming |
| `agents/deep_search/__init__.py` | NEW | @sse-streaming |
| `agents/deep_search/agent.py` | NEW | @sse-streaming |
| `agents/end_services/__init__.py` | NEW | @sse-streaming |
| `agents/end_services/agent.py` | NEW | @sse-streaming |
| `agents/extraction/__init__.py` | NEW | @sse-streaming |
| `agents/extraction/agent.py` | NEW | @sse-streaming |
| `agents/memory/__init__.py` | NEW | @sse-streaming |
| `agents/memory/agent.py` | NEW | @sse-streaming |
| `backend/app/services/message_service.py` | MODIFY | @fastapi-backend |
| `backend/app/services/context_service.py` | MODIFY | @fastapi-backend |
| `backend/app/api/messages.py` | MODIFY | @fastapi-backend |
| `backend/app/models/requests.py` | MODIFY | @fastapi-backend |

**Total: 17 new + 4 modified = 21 files**

---

## Backward Compatibility Note

The existing `agents/rag/pipeline.py` is NOT deleted — it remains as a fallback reference.
The new router replaces it as the entry point in `message_service.py`.
The old mock can be removed in a later cleanup wave once all 6 acceptance tests pass.
