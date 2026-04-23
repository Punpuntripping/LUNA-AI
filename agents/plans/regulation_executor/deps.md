# Regulation Executor -- Dependencies

## Dataclass Definition

```python
@dataclass
class RegulationSearchDeps:
    """Dependencies injected into the regulation executor agent."""

    supabase: SupabaseClient          # Supabase client for DB queries
    embedding_fn: Callable            # async (str) -> list[float], 768-dim Gemini embeddings
    jina_api_key: str                 # Jina Reranker API key
    http_client: httpx.AsyncClient    # Shared HTTP client for Jina API calls

    # Mutable state -- tools write here, other tools read
    _candidate_cache: dict[str, dict] = field(default_factory=dict)
    _reranked_results: list[dict] = field(default_factory=list)
```

## Field Descriptions

| Field | Type | Required | Source | Purpose |
|-------|------|----------|--------|---------|
| supabase | SupabaseClient | yes | Forwarded from `SearchDeps.supabase` | Vector search and text search queries against regulations, sections, articles tables |
| embedding_fn | Callable[[str], Awaitable[list[float]]] | yes | `agents.utils.embeddings.embed_regulation_query` | Generate 768-dim Gemini embeddings for query-time vector search. NOT the same as the planner's `embed_text` (which is 1536-dim OpenAI). |
| jina_api_key | str | yes | `get_settings().JINA_RERANKER_API_KEY` | Authentication for Jina Reranker API |
| http_client | httpx.AsyncClient | yes | Created in factory, shared across executor calls | Persistent HTTP client for Jina API calls. Reused to avoid connection overhead on repeated calls within a planner turn. |
| _candidate_cache | dict[str, dict] | no (auto) | Written by `embed_and_search` and `text_search_fallback` tools | In-memory cache of search candidates keyed by ID. Avoids re-querying DB for reranking. |
| _reranked_results | list[dict] | no (auto) | Written by `rerank_results` tool | Ordered list of reranked candidates for `unfold_context` to read. |

## Why Separate from SearchDeps

See PLAN.md "SearchDeps vs RegulationSearchDeps" for full tradeoff analysis. Summary:

- `SearchDeps` is the planner's deps -- contains `user_id`, `conversation_id`, `case_id`, `case_memory`, `artifact_id`, `_sse_events`. None of these are relevant to the executor.
- `RegulationSearchDeps` is the executor's deps -- contains `jina_api_key`, `http_client`, `_candidate_cache`, `_reranked_results`. None of these are relevant to the planner.
- The only shared field is `supabase`, which is forwarded from the planner's deps.
- The `embedding_fn` is different: planner uses 1536-dim OpenAI, executor uses 768-dim Gemini.

## Embedding Function Details

The executor uses a NEW embedding function that produces 768-dim vectors to match the existing database embeddings:

```python
# agents/utils/embeddings.py (new function alongside existing embed_text)

import google.genai as genai
from shared.config import get_settings

_gemini_client: genai.Client | None = None

GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"
GEMINI_EMBEDDING_DIMS = 768


def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=get_settings().GOOGLE_API_KEY)
    return _gemini_client


async def embed_regulation_query(text: str) -> list[float]:
    """Generate 768-dim embedding using Gemini for regulation search.

    Uses gemini-embedding-001 to match existing DB embeddings (768-dim).
    This is separate from embed_text() which uses OpenAI (1536-dim).
    """
    client = _get_gemini_client()
    response = client.models.embed_content(
        model=GEMINI_EMBEDDING_MODEL,
        contents=text,
    )
    return response.embeddings[0].values
```

**Important**: The `google.genai` package (Google AI SDK) must be installed. This is the same package used elsewhere via `GOOGLE_API_KEY` for Gemini model access. The embedding call is synchronous in the `genai` library but fast enough (<100ms typically) that wrapping in `asyncio.to_thread()` may be warranted if blocking is observed.

## How the Planner's Tool Constructs Executor Deps

The `search_regulations` tool on the planner agent constructs `RegulationSearchDeps` from the planner's `SearchDeps` plus config:

```python
# In agents/deep_search/agent.py, inside search_regulations tool:

import httpx
from agents.deep_search.executors.regulation_executor import (
    run_regulation_search,
    RegulationSearchDeps,
)
from agents.utils.embeddings import embed_regulation_query
from shared.config import get_settings

# Module-level shared HTTP client (created once, reused across calls)
_jina_http_client: httpx.AsyncClient | None = None

def _get_jina_client() -> httpx.AsyncClient:
    global _jina_http_client
    if _jina_http_client is None:
        _jina_http_client = httpx.AsyncClient(timeout=10.0)
    return _jina_http_client


@planner_agent.tool(retries=1, timeout=30)
async def search_regulations(ctx: RunContext[SearchDeps], query: str) -> str:
    settings = get_settings()
    reg_deps = RegulationSearchDeps(
        supabase=ctx.deps.supabase,
        embedding_fn=embed_regulation_query,
        jina_api_key=settings.JINA_RERANKER_API_KEY or "",
        http_client=_get_jina_client(),
    )
    result = await run_regulation_search(query, reg_deps)
    ctx.deps._sse_events.append({
        "type": "status",
        "text": f"تم البحث في الأنظمة: {query[:80]}...",
    })
    return result
```

## HTTP Client Lifecycle

The `httpx.AsyncClient` is created once at module level (lazy singleton) and reused across all `search_regulations` calls within the process lifetime. This avoids:
- Connection setup overhead on each call (TLS handshake to Jina API)
- Resource leaks from creating new clients per call

The client uses a 10-second timeout for individual HTTP requests. The tool-level timeout (8s for `rerank_results`) provides an additional safety net.

**Shutdown**: The HTTP client is not explicitly closed. In a long-running FastAPI process, this is acceptable -- the client will be garbage collected when the process exits. If explicit cleanup is needed later, a shutdown hook can be added to the FastAPI app.

## Testing Override

```python
from dataclasses import dataclass, field
from unittest.mock import MagicMock, AsyncMock
import httpx

async def mock_embed_768(text: str) -> list[float]:
    """Mock 768-dim embedding for testing."""
    return [0.0] * 768

def make_test_reg_deps(
    supabase=None,
    jina_key: str = "test-jina-key",
) -> RegulationSearchDeps:
    """Create test RegulationSearchDeps with mocked externals."""
    if supabase is None:
        supabase = MagicMock()
    return RegulationSearchDeps(
        supabase=supabase,
        embedding_fn=mock_embed_768,
        jina_api_key=jina_key,
        http_client=httpx.AsyncClient(),  # Real client for integration tests, mock for unit
    )
```

## Config Changes Required

### shared/config.py

Add to the `Settings` class:

```python
# Jina Reranker
JINA_RERANKER_API_KEY: Optional[str] = None
```

### .env

The key already exists as `JINA_RERANKER_API=jina_7d7f...`. It needs to be renamed (or aliased) to match the config field:

```
JINA_RERANKER_API_KEY=jina_7d7fcf1cb0b947dd8e4160ab8bec9fa8qHa5RoFicFdM9f0keV-lV5nRUlOv
```

If renaming is undesirable, add a field validator or alias in `Settings`:

```python
JINA_RERANKER_API_KEY: Optional[str] = Field(
    default=None,
    validation_alias="JINA_RERANKER_API",
)
```

### agents/utils/agent_models.py

No changes needed. The `"search_regulations"` key already exists pointing to `"gemini-3-flash"`. The executor uses `get_agent_model("search_regulations")` to obtain its model instance.
