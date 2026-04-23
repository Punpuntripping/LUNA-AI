# Regulation Search Executor - Simple Requirements

## What This Agent Does

Receives a single Arabic legal query from the deep search planner, runs a 3-stage retrieval pipeline (semantic search, cross-encoder reranking, unfolding), then uses an LLM to formalize results into a structured markdown answer with quality assessment and citations. It is a stateless delegation agent -- called as a tool, returns results, planner continues.

## Position in System

```
Orchestrator
    --> Deep Search Planner (pinned task agent)
            |-- search_regulations(query) --> THIS AGENT (regulation_executor)
            |-- search_cases_courts(query) --> future executor
            +-- search_compliance(query)   --> future executor
```

The planner's `search_regulations` tool in `agents/deep_search/agent.py` (line 263-299) already imports and calls this executor. The wiring code exists -- we just need to create the implementation it imports from.

## Core Features (MVP)

1. **3-stage retrieval pipeline** -- embed query, parallel semantic search across 3 Supabase RPCs, cross-encoder rerank via Jina (with cosine fallback)
2. **Result unfolding** -- expand retrieved articles/sections/regulations into full content with parent context and references
3. **LLM synthesis** -- agent receives two batches of unfolded results, produces structured `ExecutorResult` with quality assessment, markdown summary, and citations

## Technical Setup

### Model (from model_registry.py)

- **Agent Models Key**: `"search_regulations"` (already registered in `agents/utils/agent_models.py` line 8)
- **Registry Key**: `gemini-2.5-flash`
- **Provider**: google
- **Model ID**: `gemini-2.5-flash`
- **Capabilities**: vision, tools, streaming, temperature support
- **Pricing**: $0.30/1M input, $2.50/1M output
- **Speed**: 216 tokens/sec
- **Why**: Cost-effective and fast. The executor processes retrieved content (not reasoning-heavy), so a flash-tier model is appropriate. Matches the other executor slots already assigned.

### Agent Configuration

```python
agent = Agent(
    get_agent_model("search_regulations"),
    output_type=ExecutorResult,
    deps_type=RegulationSearchDeps,
    instructions=EXECUTOR_SYSTEM_PROMPT,   # NOT system_prompt -- stripped from history
    retries=1,
    end_strategy="early",
)
```

Key details:
- Uses `instructions=` (not `system_prompt=`) so the planner never sees the executor's internal prompt in returned message history
- `retries=1` -- one retry on tool failure
- `end_strategy="early"` -- return as soon as the agent produces a structured result
- No `message_history` -- stateless executor, fresh each call

### Output Type: ExecutorResult

```python
class ExecutorResult(BaseModel):
    quality: Literal["strong", "moderate", "weak"]
    summary_md: str          # formatted markdown answer in Arabic
    citations: list[Citation]  # reuse Citation from agents/deep_search/agent.py
```

The `Citation` model already exists at `agents/deep_search/agent.py` line 54-86. Import it rather than redefining.

### Dependencies Type: RegulationSearchDeps

```python
@dataclass
class RegulationSearchDeps:
    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]  # embed_regulation_query
    jina_api_key: str
    http_client: httpx.AsyncClient
```

Leaner than the planner's `SearchDeps` -- no user_id, conversation_id, case_id, etc. This executor does not need user context; it only needs database access, embedding function, and the Jina reranker client.

### Required Tools (2 tools on the agent)

1. **`search_and_retrieve`**: The main pipeline tool. Embeds the query, runs 3 parallel Supabase RPCs, reranks via Jina (cosine fallback), unfolds top 10 results, returns two batches as formatted text blocks.
2. **`fetch_parent_section`**: Contextual lookup tool. When an article lacks sufficient context, fetches the parent section's title/summary/context by section_id.

### External Services

- **Supabase PostgreSQL**: 3 existing RPC functions (`search_articles`, `search_sections`, `search_regulations`) that accept an embedding vector and match_count, return rows with distance scores
- **Gemini Embedding API**: `embed_regulation_query()` already implemented in `agents/utils/embeddings.py` -- generates 768-dim vectors matching the ingestion pipeline
- **Jina Reranker v3 API**: POST to `https://api.jina.ai/v1/rerank` with model `jina-reranker-v3` for cross-encoder reranking. Fallback to cosine distance sort if Jina fails or key is missing.

## Environment Variables

```bash
# Already defined in shared/config.py -- no new env vars needed
GOOGLE_API_KEY=xxx           # For Gemini embedding + Gemini 2.5 Flash LLM
JINA_RERANKER_API_KEY=xxx    # For Jina Reranker v3 (alias: JINA_RERANKER_API)
SUPABASE_URL=xxx             # Database access
SUPABASE_SERVICE_KEY=xxx     # Service role for RPC calls
```

No new environment variables are required. All keys are already in `shared/config.py` Settings.

## File Structure

```
agents/deep_search/executors/
    __init__.py                    # exports: run_regulation_search, RegulationSearchDeps, ExecutorResult
    regulation_executor.py         # Agent definition + instructions + ExecutorResult model + runner
    regulation_tools.py            # Tool 1 (search_and_retrieve) + Tool 2 (fetch_parent_section)
    regulation_unfold.py           # Unfolding logic (article/section/regulation/references)
    planning/
        INITIAL.md                 # This file
```

## Detailed Tool Specifications

### Tool 1: `search_and_retrieve`

**Input**: `query: str` (Arabic legal query)

**Pipeline steps**:

1. **Embed**: Call `deps.embedding_fn(query)` to get 768-dim vector
2. **Parallel search**: `asyncio.gather` across 3 Supabase RPCs:
   - `search_articles(embedding, 15)` -- articles with distances
   - `search_sections(embedding, 10)` -- sections with distances
   - `search_regulations(embedding, 5)` -- regulations with distances
3. **Merge and deduplicate**: Combine all results, deduplicate by primary key
4. **Cross-encoder rerank**: POST to Jina Reranker v3 with the query + document texts. If Jina fails or key is empty, fall back to sorting by cosine distance.
5. **Take top 10**: After reranking, select positions 1-10
6. **Unfold**: For each result, expand based on type (article/section/regulation) using `regulation_unfold.py`
7. **Split into batches**:
   - Batch 1 (positions 1-5, highest relevance)
   - Batch 2 (positions 6-10)
8. **Format and return**: Return both batches as a formatted string block with a deduplicated `<references>` section

**Output**: `str` -- formatted text with two labeled batches + references block

### Tool 2: `fetch_parent_section`

**Input**: `section_id: str`

**Query**: Simple Supabase select on sections table for title, summary, context by section_id

**Output**: `str` -- formatted section context

## Unfolding Logic (regulation_unfold.py)

Each result type unfolds differently:

- **Article**: content + context + identifier_number + references (from JSONB)
- **Section**: title + summary + context + ALL child articles (with their references)
- **Regulation**: summary + entity_name + external_references + ALL child sections (stop at section level, do not recurse into articles)

**References from JSONB** come in two shapes:
1. Article references: have a reference ID, need content fetched from DB
2. Regulation-only references: have a title directly, use as-is

**Keep unfolding simple for MVP**: If a fetch fails for a child item, skip it and continue. Log a warning but do not crash the pipeline.

## System Prompt (Arabic-first)

The executor's system prompt should instruct the LLM to:

1. Receive two batches of search results from the `search_and_retrieve` tool
2. Start with batch 1 (highest relevance). Only use batch 2 if batch 1 is insufficient.
3. For each relevant result, explain the legal context in Arabic
4. Self-assess quality as "strong" (clear answer with direct legal basis), "moderate" (partial answer, some relevant sources), or "weak" (tangential results only)
5. Produce structured citations for every source referenced
6. Output must always be in Arabic
7. Use `fetch_parent_section` if an article needs more context

The full prompt text is defined in the Obsidian spec. The `pydantic-ai-prompt-engineer` agent will formalize it into `prompts.md`.

## Runner Function

The planner imports and calls a single async function:

```python
async def run_regulation_search(query: str, deps: RegulationSearchDeps) -> str:
    """Run the regulation executor agent and return formatted markdown result.

    Returns a string (not ExecutorResult) because the planner receives tool
    results as strings. The function runs the agent, then serializes the
    ExecutorResult into a markdown string the planner can consume.
    """
```

This function:
1. Creates a fresh agent run with the query as the user message
2. Passes `deps` as the agent's dependencies
3. Extracts the `ExecutorResult` from the agent output
4. Formats it as a markdown string: quality header + summary_md + citations block
5. Returns the string to the planner

**Important**: The return type is `str`, not `ExecutorResult`, because the planner's tool receives it as a string return value. The structured `ExecutorResult` is internal to the executor.

## Error Handling (Keep Simple)

- **Embedding failure**: Raise immediately -- cannot search without a vector
- **Supabase RPC returns empty**: Return empty list for that source type, continue with whatever other sources returned
- **All RPCs return empty**: Agent receives empty batches, should assess quality as "weak" and note no results found
- **Jina reranker failure**: Fall back to cosine distance sort (already have distances from Supabase RPCs)
- **Jina API key missing/empty**: Skip reranking entirely, use cosine sort
- **Unfold failure for a single item**: Log warning, skip that item, continue with remaining results
- **Agent LLM failure**: `run_regulation_search` catches the exception, returns a brief Arabic error string so the planner can note the failure and continue

## Existing Code to Reuse

| What | Where | How |
|------|-------|-----|
| `Citation` model | `agents/deep_search/agent.py:54` | Import directly |
| `embed_regulation_query()` | `agents/utils/embeddings.py:58` | Passed via deps.embedding_fn |
| `get_agent_model()` | `agents/utils/agent_models.py:17` | Creates model from registry |
| `_get_jina_client()` | `agents/deep_search/agent.py:253` | Shared httpx client, passed via deps.http_client |
| `JINA_RERANKER_API_KEY` | `shared/config.py:95` | Passed via deps.jina_api_key |
| Supabase client | `shared/db/client.py` | Passed via deps.supabase |

## Wiring (Already Done in Planner)

The planner's `search_regulations` tool at `agents/deep_search/agent.py` lines 279-294 already has the complete import and call structure:

```python
from agents.deep_search.executors import run_regulation_search, RegulationSearchDeps
from agents.utils.embeddings import embed_regulation_query
from shared.config import get_settings

settings = get_settings()
reg_deps = RegulationSearchDeps(
    supabase=ctx.deps.supabase,
    embedding_fn=embed_regulation_query,
    jina_api_key=settings.JINA_RERANKER_API_KEY or "",
    http_client=_get_jina_client(),
)
result = await run_regulation_search(query, reg_deps)
```

The `__init__.py` must export `run_regulation_search` and `RegulationSearchDeps` to match these imports.

## Success Criteria

- [ ] `run_regulation_search(query, deps)` returns a non-empty markdown string for a valid Arabic query
- [ ] 3-stage pipeline executes: embed -> parallel RPC search -> rerank -> unfold
- [ ] Jina reranker failure falls back to cosine sort without crashing
- [ ] Empty RPC results produce a "weak" quality assessment (not a crash)
- [ ] `ExecutorResult` validates with quality, summary_md, and citations fields
- [ ] Citations list contains at least one Citation for non-empty results
- [ ] Agent LLM failure returns an Arabic error string (not an exception)
- [ ] All imports from `agents.deep_search.executors` resolve correctly
- [ ] Tests pass using `FunctionModel` / `TestModel` (no real API calls)

## Assumptions Made

- **Supabase RPCs exist and return expected shapes**: The 3 RPC functions (`search_articles`, `search_sections`, `search_regulations`) are already deployed in the database. We assume they accept `(embedding vector, match_count int)` and return rows with distance scores and content fields.
- **No caching needed for MVP**: Each call is fresh. Caching can be added later if the same queries repeat.
- **Jina Reranker v3 API shape**: Standard `POST /v1/rerank` with `model`, `query`, `documents`, `top_n` fields. Returns `results` array with `index` and `relevance_score`.
- **Unfolding fetches are cheap**: Fetching child articles for a section or child sections for a regulation uses simple Supabase selects. No pagination needed for MVP (regulations typically have < 100 articles per section).
- **768-dim vectors match DB index**: The Gemini embedding function produces 768-dim vectors that match the pgvector index used by the Supabase RPCs.
- **Return type is str**: The planner consumes tool results as strings, so `run_regulation_search` serializes the `ExecutorResult` into markdown before returning.
- **No streaming needed**: The executor runs inside a tool call. The planner handles user-facing streaming.
- **Single language**: All content in the regulation database is Arabic. No language detection or translation needed.

---
Generated: 2026-03-30
Note: This is an MVP. Additional features (caching, parallel unfold batching, streaming progress to planner) can be added after the basic agent works end-to-end.
