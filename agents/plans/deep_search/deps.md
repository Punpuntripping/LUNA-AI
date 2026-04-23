# Deep Search Planner — Dependencies

## Dataclass Definition

```python
@dataclass
class SearchDeps:
    supabase: SupabaseClient       # Supabase client for DB operations
    embedding_fn: Callable         # async (str) -> list[float]
    user_id: str                   # Current user's user_id (UUID)
    conversation_id: str           # Current conversation_id (UUID)
    case_id: str | None            # Case context (None for general Q&A)
    case_memory: str | None        # Pre-built case memory text (None if no case or no memories)
```

## Field Descriptions

| Field | Type | Required | Source | Purpose |
|-------|------|----------|--------|---------|
| supabase | SupabaseClient | yes | `get_supabase_client()` from shared/db | Used by tools to read/write artifacts, and passed to executor agents |
| embedding_fn | Callable[[str], Awaitable[list[float]]] | yes | `agents.utils.embeddings.embed_text` | Passed to executor agents for vector search queries |
| user_id | str | yes | Auth middleware → route handler | Needed for artifact creation (artifacts.user_id FK) |
| conversation_id | str | yes | Route handler param | Needed for artifact creation (artifacts.conversation_id FK) |
| case_id | str \| None | no | Route handler param (None for general mode) | Needed for artifact creation (artifacts.case_id FK) |
| case_memory | str \| None | no | Orchestrator builds from case_memories table | Injected into planner instructions via dynamic instruction function |

## How the Orchestrator Creates Deps

```python
from agents.utils.embeddings import embed_text
from shared.db.client import get_supabase_client

async def _build_search_deps(
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    supabase: SupabaseClient,
) -> SearchDeps:
    # Build case memory if case_id is set
    case_memory = None
    if case_id:
        result = (
            supabase.table("case_memories")
            .select("content_ar, memory_type")
            .eq("case_id", case_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        if result.data:
            lines = []
            for mem in result.data:
                lines.append(f"- [{mem['memory_type']}] {mem['content_ar']}")
            case_memory = "\n".join(lines)

    return SearchDeps(
        supabase=supabase,
        embedding_fn=embed_text,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        case_memory=case_memory,
    )
```

## Testing Override

```python
from dataclasses import dataclass

# Mock deps for testing
async def mock_embed(text: str) -> list[float]:
    return [0.0] * 1536

test_deps = SearchDeps(
    supabase=mock_supabase,  # Mock or test Supabase client
    embedding_fn=mock_embed,
    user_id="test-user-id",
    conversation_id="test-conv-id",
    case_id=None,
    case_memory=None,
)

# Use with TestModel or FunctionModel
from pydantic_ai.models.test import TestModel

with planner_agent.override(model=TestModel()):
    result = await planner_agent.run("test briefing", deps=test_deps)
```
