# Router Agent — Dependencies

## Dataclass Definition

```python
@dataclass
class RouterDeps:
    """Dependencies injected into the router agent."""
    supabase: SupabaseClient        # For artifact read access (get_artifact tool)
    user_id: str                    # Current user's user_id (UUID)
    conversation_id: str            # Current conversation_id (UUID)
    case_id: str | None             # Case context (None for general Q&A)
    case_memory_md: str | None      # Pre-built case memory markdown (None if no case or no memories)
    case_metadata: dict | None      # Case name, type, parties, etc. (None if no case)
    user_preferences: dict | None   # Response tone, language, detail level (None if not configured)
```

## Field Descriptions

| Field | Type | Required | Source | Purpose |
|-------|------|----------|--------|---------|
| supabase | SupabaseClient | yes | `get_supabase_client()` from shared/db | Used by `get_artifact` tool to read artifact content |
| user_id | str | yes | Auth middleware -> route handler | Needed for artifact ownership check in get_artifact |
| conversation_id | str | yes | Route handler param | Context identifier (not directly used by router, but useful for logging) |
| case_id | str \| None | no | Route handler param (None for general mode) | Included in OpenTask context if task needs case scope |
| case_memory_md | str \| None | no | Orchestrator builds from `lawyer_cases` + `case_memories` tables | Injected into router instructions via dynamic instruction function |
| case_metadata | dict \| None | no | Orchestrator loads from `lawyer_cases` table | Contains case_name, case_type, status, parties, description |
| user_preferences | dict \| None | no | Orchestrator loads from `user_preferences` table | Contains tone, detail_level, and other response style settings |

## How the Orchestrator Creates Deps

The orchestrator already builds all of these in `_route()` (see `agents/orchestrator.py` lines 105-178). The implementation creates `RouterDeps` and passes it to `run_router()`.

```python
# This is already implemented in orchestrator.py _route() — shown here for reference

router_deps = RouterDeps(
    supabase=supabase,
    user_id=user_id,
    conversation_id=conversation_id,
    case_id=case_id,
    case_memory_md=case_memory_md,      # Built from case_memories + lawyer_cases
    case_metadata=case_metadata,         # From lawyer_cases table
    user_preferences=user_preferences,   # From user_preferences table
)

result = await run_router(
    question=question,
    supabase=supabase,          # Also passed separately for backward compat
    user_id=user_id,
    conversation_id=conversation_id,
    case_id=case_id,
    case_memory_md=case_memory_md,
    case_metadata=case_metadata,
    user_preferences=user_preferences,
    message_history=message_history,
)
```

**Note on orchestrator signature**: The current `run_router()` function in the orchestrator receives individual parameters (not a RouterDeps object). The runner function should construct RouterDeps internally from these parameters to keep the orchestrator interface unchanged.

## Orchestrator Data Loading (Already Implemented)

The orchestrator already loads all the data the router needs. Here is the existing flow:

1. **Conversation history**: Loaded from `messages` table, converted via `messages_to_history()`
2. **Case metadata**: Loaded from `lawyer_cases` table if `case_id` is present
3. **Case memories**: Loaded from `case_memories` table if `case_id` is present, formatted as markdown
4. **User preferences**: Loaded from `user_preferences` table by user_id

No additional data loading is needed in the router agent itself.

## Testing Override

```python
from dataclasses import dataclass
from unittest.mock import MagicMock

# Mock Supabase for artifact reads
mock_supabase = MagicMock()
mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.is_.return_value.maybe_single.return_value.execute.return_value.data = {
    "title": "تقرير الفصل التعسفي",
    "content_md": "# تقرير\n\nمحتوى التقرير..."
}

def make_test_deps(
    case_memory_md: str | None = None,
    case_metadata: dict | None = None,
    case_id: str | None = None,
    user_preferences: dict | None = None,
) -> RouterDeps:
    return RouterDeps(
        supabase=mock_supabase,
        user_id="user-001",
        conversation_id="conv-001",
        case_id=case_id,
        case_memory_md=case_memory_md,
        case_metadata=case_metadata,
        user_preferences=user_preferences,
    )

# Use with TestModel or FunctionModel
from pydantic_ai.models.test import TestModel

with router_agent.override(model=TestModel()):
    result = await router_agent.run("مرحبا", deps=make_test_deps())
```
