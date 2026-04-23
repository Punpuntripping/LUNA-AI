# reg_search - Regulations Domain Search Loop

## What This Agent Does

A domain-specific regulations search package that implements a pydantic_graph loop (QueryExpander -> SearchNode -> Aggregator) with retry-on-weak-results (max 2 retries, up to 3 total rounds). Replaces the monolithic regulations executor in `agents/deep_search_v3/`. Called by deep_search_v3's PlanAgent via `run_reg_search(focus_instruction, user_context, deps)`.

## Agent Classification
- **Type**: Workflow (pydantic_graph loop with 2 LLM agents + 1 programmatic node)
- **Complexity**: Medium-High
- **Domain**: Saudi legal regulations search (articles, sections, regulations via hybrid vector search)

## Core Features (MVP)

1. **Query Expansion** (ExpanderNode): LLM agent that takes a focus_instruction and generates 2-4 precise Arabic search queries, each targeting a single legal concept. On retry rounds, receives weak_axes as dynamic instructions to generate queries ONLY for weak aspects.
2. **Programmatic Search** (SearchNode): No LLM. Runs the regulations search pipeline: embed query -> 3 parallel hybrid RPCs (articles, sections, regulations) -> merge + tag source_type -> optional Jina rerank (or score_fallback) -> unfold results -> format to markdown.
3. **Aggregation/Synthesis** (AggregatorNode): LLM agent that evaluates result quality, produces Arabic legal analysis markdown with structured citations, and decides whether to loop back (insufficient) or end (sufficient / max retries).
4. **Retry Loop**: When Aggregator returns `sufficient=False` and retries remain, weak_axes are fed back to ExpanderNode as dynamic instructions. The expander then generates queries ONLY for the weak aspects, not re-querying what was already strong.

## Technical Setup

### Models

| Agent Slot | Primary | Fallback 1 | Fallback 2 |
|-----------|---------|------------|------------|
| `reg_search_expander` | `or-deepseek-v3.2` | `or-gemini-2.5-flash` | `or-mimo-v2-pro` |
| `reg_search_aggregator` | `or-qwen3.5-397b` | `or-gemini-2.5-flash` | `or-mimo-v2-pro` |

All model keys exist in `agents/model_registry.py`. Use `agents/utils/agent_models.py` pattern: add these slots to `AGENT_MODELS` and use `get_agent_model()` to create model instances.

### Output Type
- **Entry point return type**: `RegSearchResult` (BaseModel)
- **Expander output**: `ExpanderOutput` (BaseModel)
- **Aggregator output**: `AggregatorOutput` (BaseModel)

### Output Models (all fields documented)

#### RegSearchResult (returned to PlanAgent by `run_reg_search()`)
```python
class RegSearchResult(BaseModel):
    quality: Literal["strong", "moderate", "weak"]  # Overall quality assessment
    summary_md: str          # Arabic legal analysis markdown
    citations: list[Citation]  # Structured citations
    domain: Literal["regulations"] = "regulations"
    queries_used: list[str]  # All queries executed across all rounds
    rounds_used: int         # How many rounds (1-3)
    expander_prompt_key: str = "prompt_1"   # Which expander prompt was used (for logging)
    aggregator_prompt_key: str = "prompt_1" # Which aggregator prompt was used (for logging)
```

This must be compatible with deep_search_v3's `ExecutorResult` schema so PlanAgent can handle both old executor results and new domain search results without changes. The field names and types align: `quality`, `summary_md`, `citations`, `domain`, `queries_used`, `rounds_used` are all present in both. `inner_usage` from ExecutorResult is intentionally omitted here (usage is tracked in LoopState and can be logged separately).

#### ExpanderOutput
```python
class ExpanderOutput(BaseModel):
    queries: list[str]       # 2-4 Arabic search queries
    rationales: list[str]    # Internal rationale per query (logs only, not sent to LLM)
```

Note: Unlike deep_search_v2's ExpanderOutput which has `SearchQuery` objects with `tool` field, this expander is regulations-only. No `tool` field needed -- all queries go to the regulations search pipeline.

#### AggregatorOutput
```python
class AggregatorOutput(BaseModel):
    sufficient: bool         # True if results adequately answer the question
    quality: Literal["strong", "moderate", "weak"]  # Quality assessment
    weak_axes: list[WeakAxis]   # What needs re-searching (empty if sufficient)
    synthesis_md: str            # Arabic legal analysis markdown
    citations: list[Citation]    # Structured citations
```

#### WeakAxis
```python
class WeakAxis(BaseModel):
    reason: str              # Why this aspect is weak (Arabic)
    suggested_query: str     # Specific query to try on retry
```

#### Citation (reuse from deep_search_v3)
```python
class Citation(BaseModel):
    source_type: str         # "regulation", "article", "section"
    ref: str                 # chunk_ref identifier
    title: str               # Arabic title
    content_snippet: str     # Relevant excerpt
    regulation_title: str | None  # Parent regulation name
    article_num: str | None  # Article number if applicable
    relevance: str           # Why this source supports the answer
```

This is the SAME Citation model as `agents/deep_search_v3/models.py:Citation`. Import it directly or define a compatible copy. The `court` field from V3 Citation is not needed here (regulations only).

#### LoopState (mutable graph state dataclass, NOT BaseModel)
```python
@dataclass
class LoopState:
    focus_instruction: str
    user_context: str
    expander_prompt_key: str = "prompt_1"   # Which expander prompt variant to use
    aggregator_prompt_key: str = "prompt_1" # Which aggregator prompt variant to use
    round_count: int = 0
    max_rounds: int = 3
    expander_output: ExpanderOutput | None = None
    all_search_results: list[SearchResult] = field(default_factory=list)
    aggregator_output: AggregatorOutput | None = None
    weak_axes: list[WeakAxis] = field(default_factory=list)
    all_queries_used: list[str] = field(default_factory=list)
    sse_events: list[dict] = field(default_factory=list)
    inner_usage: list[dict] = field(default_factory=list)
    search_results_log: list[dict] = field(default_factory=list)
```

#### SearchResult (programmatic dataclass, NOT BaseModel)
```python
@dataclass
class SearchResult:
    query: str
    raw_markdown: str        # Full formatted pipeline output
    result_count: int
```

### Required Agents (2 pydantic_ai Agents)

1. **QueryExpander agent** (`expander.py`):
   - Model slot: `reg_search_expander`
   - Output type: `ExpanderOutput`
   - No deps_type needed (stateless, no tools)
   - **Factory function**: `create_expander_agent(prompt_key: str = "prompt_1") -> Agent[None, ExpanderOutput]`
   - System prompt: Looked up from `expander_prompts.get_expander_prompt(prompt_key)`
   - Dynamic instructions on retry: weak_axes injected via `build_expander_dynamic_instructions()`
   - UsageLimits: `response_tokens_limit=4_000, request_limit=3`

2. **Aggregator agent** (`aggregator.py`):
   - Model slot: `reg_search_aggregator`
   - Output type: `AggregatorOutput`
   - No deps_type needed (stateless, no tools)
   - **Factory function**: `create_aggregator_agent(prompt_key: str = "prompt_1") -> Agent[None, AggregatorOutput]`
   - System prompt: Looked up from `aggregator_prompts.get_aggregator_prompt(prompt_key)`
   - UsageLimits: `response_tokens_limit=16_000, request_limit=3`

### Graph Nodes (3 pydantic_graph nodes in `loop.py`)

1. **ExpanderNode** (`BaseNode[LoopState, RegSearchDeps, RegSearchResult]`):
   - Increments `state.round_count`
   - Creates expander agent via `create_expander_agent(prompt_key=state.expander_prompt_key)`
   - On round 2+, injects weak_axes as dynamic instructions
   - Runs expander with `focus_instruction + user_context` (+ weak_axes context on retry)
   - Stores `state.expander_output`, appends queries to `state.all_queries_used`
   - Always transitions to `SearchNode`
   - On error: creates fallback output using `focus_instruction` as a single query

2. **SearchNode** (`BaseNode[LoopState, RegSearchDeps, RegSearchResult]`):
   - No LLM. Reads `state.expander_output.queries`
   - For EACH query: calls `search_regulations_pipeline(query, deps)` concurrently via `asyncio.gather`
   - Appends results to `state.all_search_results`
   - Logs to `state.search_results_log`
   - Always transitions to `AggregatorNode`

3. **AggregatorNode** (`BaseNode[LoopState, RegSearchDeps, RegSearchResult]`):
   - Creates aggregator agent via `create_aggregator_agent(prompt_key=state.aggregator_prompt_key)`
   - Builds user message from `state.focus_instruction` + all search results markdown
   - Runs aggregator agent
   - Stores `state.aggregator_output`
   - Decision logic:
     - If `sufficient=False` AND `state.round_count < state.max_rounds`: set `state.weak_axes`, return `ExpanderNode`
     - Else: return `End(RegSearchResult(...))` assembled from aggregator output

### Graph Assembly and Entry Point (`loop.py`)

```python
from pydantic_graph import Graph

reg_search_graph = Graph(nodes=[ExpanderNode, SearchNode, AggregatorNode])

async def run_reg_search(
    focus_instruction: str,
    user_context: str,
    deps: RegSearchDeps,
    expander_prompt_key: str = "prompt_1",
    aggregator_prompt_key: str = "prompt_1",
) -> RegSearchResult:
    state = LoopState(
        focus_instruction=focus_instruction,
        user_context=user_context,
        expander_prompt_key=expander_prompt_key,
        aggregator_prompt_key=aggregator_prompt_key,
    )
    graph_result = await reg_search_graph.run(ExpanderNode(), state=state, deps=deps)
    deps._events.extend(state.sse_events)
    return graph_result.output
```

### Dependencies (deps dataclass fields)
```python
@dataclass
class RegSearchDeps:
    supabase: SupabaseClient       # For hybrid search RPCs and unfold DB lookups
    embedding_fn: Callable[[str], Awaitable[list[float]]]  # Query embedding function
    jina_api_key: str = ""         # Jina reranker API key (optional)
    http_client: httpx.AsyncClient | None = None  # For Jina API calls
    use_reranker: bool = False     # Whether to use Jina reranker vs score_fallback
    mock_results: dict | None = None  # For testing: {"regulations": "...markdown..."}
    _events: list[dict] = field(default_factory=list)  # SSE events collected during run
    _search_log: list[dict] = field(default_factory=list)  # Raw search logs for debugging
```

### External Services
- **Supabase**: 3 hybrid search RPCs (`hybrid_search_articles`, `hybrid_search_sections`, `hybrid_search_regulations`) + direct table queries for unfold (articles, sections, regulations, entities)
- **Jina Reranker API** (optional): `https://api.jina.ai/v1/rerank` with `jina-reranker-v3` model
- **Embedding API**: Via `deps.embedding_fn` (caller provides the implementation)
- **OpenRouter**: LLM calls for expander (`or-deepseek-v3.2`) and aggregator (`or-qwen3.5-397b`)

## Search Pipeline Details

### `search_pipeline.py` — Adapted from `agents/deep_search_v3/executors/search_pipeline.py`

Only the regulations pipeline is needed. Copy and adapt `search_regulations_pipeline()` and its helpers:
- `_hybrid_rpc_search()` — Supabase RPC caller (runs sync client in `asyncio.to_thread`)
- `_rerank()` — Jina reranker with fallback
- `_score_fallback()` — Sort by hybrid RRF score

Key parameters:
- `MATCH_COUNT = 30` total candidates across 3 RPCs
- Article ratio: 50%, Section ratio: 33%, Regulation ratio: remaining
- `full_text_weight=0.25, semantic_weight=0.75`
- Top 10 candidates after rerank/fallback
- Batch formatting: 5 top + 5 second batch
- Content truncation: result_md capped at 15,000 chars per query

Signature:
```python
async def search_regulations_pipeline(
    query: str,
    deps: RegSearchDeps,
) -> tuple[str, int]:  # (result_markdown, result_count)
```

### `regulation_unfold.py` — Copied from `agents/deep_search_v3/executors/regulation_unfold.py`

Full copy of the unfold logic. Functions:
- `unfold_article(supabase, row)` — Fetches article_context, references, resolves cross-refs
- `unfold_section(supabase, row)` — Fetches ALL child articles + their cross-refs
- `unfold_regulation(supabase, row)` — Fetches child sections (titles + summaries)
- `collect_references(all_results)` — Deduplicates regulation references
- `format_unfolded_result(result, position)` — Formats article/section/regulation to markdown
- `_resolve_article_references(supabase, refs_json)` — Resolves JSONB references
- `_resolve_external_references(ext_refs)` — Passes through external refs

Truncation limits:
- `MAX_CONTENT_CHARS = 3_000`
- `MAX_CONTEXT_CHARS = 500`
- `MAX_SIBLINGS_CHARS = 1_500`
- `MAX_REGULATION_META_CHARS = 300`

## Multi-Prompt Architecture

Both the expander and aggregator use a **multi-prompt** system. Each has its own prompt file containing a dictionary of named prompt variants. This enables A/B testing different prompt strategies via CLI flags without changing code.

### Prompt Files

1. **`expander_prompts.py`** — Contains:
   - `EXPANDER_PROMPTS: dict[str, str]` — Named prompt variants (e.g., `"prompt_1"`, `"prompt_2"`, ...)
   - `DEFAULT_EXPANDER_PROMPT: str = "prompt_1"` — Default key
   - `get_expander_prompt(key: str) -> str` — Lookup with KeyError on missing
   - `build_expander_dynamic_instructions(weak_axes, round_count) -> str` — Retry-round injection (shared across all prompt variants)
   - `build_expander_user_message(focus_instruction, user_context) -> str` — User message builder

2. **`aggregator_prompts.py`** — Contains:
   - `AGGREGATOR_PROMPTS: dict[str, str]` — Named prompt variants
   - `DEFAULT_AGGREGATOR_PROMPT: str = "prompt_1"` — Default key
   - `get_aggregator_prompt(key: str) -> str` — Lookup with KeyError on missing
   - `build_aggregator_user_message(focus_instruction, user_context, all_search_results) -> str` — User message builder

### How Prompt Selection Flows

```
CLI --expander-prompt prompt_3 --aggregator-prompt prompt_2
  └─> run_reg_search(expander_prompt_key="prompt_3", aggregator_prompt_key="prompt_2")
        └─> LoopState.expander_prompt_key / aggregator_prompt_key
              └─> ExpanderNode calls create_expander_agent(prompt_key=state.expander_prompt_key)
              └─> AggregatorNode calls create_aggregator_agent(prompt_key=state.aggregator_prompt_key)
```

### Prompt Design Guidance (reference for writing prompt variants)

#### Expander Prompts — Key Elements

Arabic-first. Each variant should cover these concerns (with different strategies/emphasis):

1. **Role**: Legal query expansion specialist for Saudi regulations search.
2. **Search infrastructure knowledge** (critical for good queries):
   - Article match -> auto-fetches article_context + parent section/regulation + cross-references
   - Section match -> auto-fetches ALL child articles + their cross-references
   - Regulation match -> auto-fetches ALL child sections (titles + summaries)
   - Precise queries matching one article automatically bring surrounding context
   - Over-broad queries dilute the vector match
3. **Query expansion rules**:
   - Each query = one legal concept that one article or section could answer
   - Describe the behavior/right, not the law name (semantic search)
   - Don't mention law names unless the user did
   - Don't merge two different concepts in one query
   - Cover different angles: definition, procedures, penalties, victim rights
   - Use article-level precision for depth, section-level for breadth
4. **Output**: 2-4 queries with rationales

Source: Adapt from `agents/deep_search_v3/prompts.py:_build_regulations_expansion_guidance()` and `REGULATIONS_EXECUTOR_PROMPT`.

#### Expander Dynamic Instructions (retry rounds — shared across all prompts)

When `weak_axes` are provided (round 2+), inject as dynamic instructions:

```
---
## تعليمات إعادة البحث (الجولة {round_count})

النتائج السابقة كانت ضعيفة في المحاور التالية:

{for axis in weak_axes:}
- **السبب:** {axis.reason}
  **استعلام مقترح:** {axis.suggested_query}
{end for}

وجّه استعلاماتك الجديدة لتغطية هذه المحاور الضعيفة فقط.
لا تكرر استعلامات أنتجت نتائج قوية سابقاً.
```

#### Aggregator Prompts — Key Elements

Arabic-first. Each variant should cover these concerns (with different strategies/emphasis):

1. **Role**: Legal research evaluator and synthesizer for Saudi regulations.
2. **Quality evaluation criteria**:
   - **strong**: Direct legal text (articles, sections) that explicitly answers the question
   - **moderate**: Partial/indirect legal text that is relevant but doesn't fully answer
   - **weak**: Tangential results that don't substantively address the question
3. **When sufficient** (`sufficient=True`):
   - Produce Arabic legal analysis markdown with headers, citations, cross-references
   - Build regulatory argument chains across articles from different regulations
   - Extract structured Citation objects for every source referenced
4. **When insufficient** (`sufficient=False`):
   - Identify weak_axes with specific reason and suggested_query for each
   - Each weak axis should target a specific gap in the current results
   - The suggested_query should be a concrete, actionable search query
5. **Citation extraction rules**:
   - source_type: "article", "section", or "regulation"
   - ref: use chunk_ref from the search results
   - title: Arabic title from the result
   - content_snippet: the most relevant excerpt
   - regulation_title: parent regulation name
   - article_num: if applicable
   - relevance: why this source supports the analysis

Source: Adapt from `agents/deep_search_v3/prompts.py:REGULATIONS_EXECUTOR_PROMPT` quality evaluation section.

#### Aggregator User Message Construction (shared across all prompts)

Build the user message programmatically (not in the system prompt):
```
السؤال / تعليمات التركيز:
{focus_instruction}

سياق المستخدم:
{user_context}

---
نتائج البحث ({len(all_search_results)} استعلام، {total_result_count} نتيجة):

{for i, result in enumerate(all_search_results):}
### استعلام {i+1}: "{result.query}"
{result.raw_markdown}
{end for}
```

### Initial Prompt Variants (to be expanded over time)

Each file starts with a single `prompt_1`. New variants are added as you iterate on prompt quality — the code never changes, only the prompt dicts grow.

- `prompt_1`: Baseline — direct translation of the design guidance above into Arabic system prompts
- Future: `prompt_2`, `prompt_3`, etc. — alternative strategies (more/fewer queries, different emphasis, chain-of-thought vs direct, etc.)

## File Structure

```
agents/deep_search_v3/reg_search/
    __init__.py              # Exports: run_reg_search, RegSearchDeps, RegSearchResult
    models.py                # RegSearchResult, ExpanderOutput, AggregatorOutput, WeakAxis, Citation, LoopState, SearchResult
    expander_prompts.py      # EXPANDER_PROMPTS dict, DEFAULT_EXPANDER_PROMPT, get_expander_prompt(), build_expander_dynamic_instructions(), build_expander_user_message()
    aggregator_prompts.py    # AGGREGATOR_PROMPTS dict, DEFAULT_AGGREGATOR_PROMPT, get_aggregator_prompt(), build_aggregator_user_message()
    search_pipeline.py       # search_regulations_pipeline() + _hybrid_rpc_search() + _rerank() + _score_fallback()
    regulation_unfold.py     # unfold_article(), unfold_section(), unfold_regulation(), collect_references(), format_unfolded_result()
    expander.py              # create_expander_agent(prompt_key) -> Agent[None, ExpanderOutput]
    aggregator.py            # create_aggregator_agent(prompt_key) -> Agent[None, AggregatorOutput]
    loop.py                  # ExpanderNode, SearchNode, AggregatorNode, reg_search_graph, run_reg_search()
    logger.py                # Run log writer (write JSON logs to agents/deep_search_v3/reg_search/logs/)
    cli.py                   # Standalone CLI with --expander-prompt / --aggregator-prompt flags
    tests/
        __init__.py
        conftest.py          # Fixtures: mock_deps, mock_embedding_fn, mock_supabase
        test_expander.py     # Test expander with TestModel/FunctionModel
        test_aggregator.py   # Test aggregator with TestModel/FunctionModel
        test_loop.py         # Test full graph loop with mocked search
        test_search_pipeline.py  # Test pipeline with mocked Supabase RPCs
```

## Integration with deep_search_v3

After this package works standalone, it will be wired into deep_search_v3:

1. **Agent models**: Add `reg_search_expander` and `reg_search_aggregator` to `agents/utils/agent_models.py`
2. **PlanAgent tool**: Replace `register_search_regulations()` with a new tool `invoke_reg_search()` that calls `run_reg_search()`
3. **Result compatibility**: `RegSearchResult` fields align with `ExecutorResult` so PlanAgent's result handling works unchanged
4. **Deps bridging**: `RegSearchDeps` is created from `DeepSearchV3Deps` fields (supabase, embedding_fn, jina_api_key, http_client, use_reranker, mock_results)

This wiring is NOT part of this package -- it will be done separately after the package is tested standalone.

## CLI for Testing

```bash
# Basic search (uses prompt_1 for both)
python -m agents.deep_search_v3.reg_search.cli "أحكام إنهاء عقد العمل والفصل التعسفي" --verbose

# Select specific prompt variants
python -m agents.deep_search_v3.reg_search.cli "أحكام إنهاء عقد العمل" --expander-prompt prompt_2 --aggregator-prompt prompt_3

# Mix: custom expander, default aggregator
python -m agents.deep_search_v3.reg_search.cli "أحكام إنهاء عقد العمل" --expander-prompt prompt_3

# With Jina reranker
python -m agents.deep_search_v3.reg_search.cli "أحكام إنهاء عقد العمل" --rerank --verbose

# With mock results (no real DB/API calls)
python -m agents.deep_search_v3.reg_search.cli "أحكام إنهاء عقد العمل" --mock --verbose

# List available prompts
python -m agents.deep_search_v3.reg_search.cli --list-prompts
```

The CLI should:
- Accept a focus_instruction as positional argument
- Accept optional `--user-context` flag
- Accept `--expander-prompt KEY` and `--aggregator-prompt KEY` (default: `prompt_1`)
- Accept `--list-prompts` to print available prompt keys for both expander and aggregator
- Accept `--rerank`, `--mock`, `--verbose` flags
- Pass prompt keys through to `run_reg_search()`
- Create real `RegSearchDeps` (Supabase client, embedding function from shared config)
- Call `run_reg_search()` and print the result
- Log to `agents/deep_search_v3/reg_search/logs/` with timestamp
- Log should record which prompt keys were used

## Success Criteria

- [ ] `run_reg_search()` returns a valid `RegSearchResult` with quality, summary_md, citations
- [ ] QueryExpander generates 2-4 precise Arabic queries from a focus_instruction
- [ ] SearchNode executes all queries concurrently and returns merged results
- [ ] Aggregator evaluates quality and produces Arabic synthesis with citations
- [ ] Retry loop works: weak results trigger re-expansion with weak_axes as dynamic instructions
- [ ] Max 3 rounds enforced (1 initial + 2 retries)
- [ ] Expander on retry only generates queries for weak aspects, not re-querying strong results
- [ ] SSE events collected in `deps._events` throughout the loop
- [ ] Search pipeline correctly calls 3 parallel hybrid RPCs, merges, reranks, unfolds
- [ ] CLI `--expander-prompt` and `--aggregator-prompt` flags select correct prompt variants
- [ ] CLI `--list-prompts` prints available prompt keys for both agents
- [ ] Prompt key selection flows through: CLI -> run_reg_search -> LoopState -> create_*_agent
- [ ] RegSearchResult records which prompt keys were used
- [ ] Logger records prompt keys in run logs
- [ ] CLI runs end-to-end with real Supabase + embedding API
- [ ] All tests pass with TestModel/FunctionModel (no real LLM calls in tests)
- [ ] `RegSearchResult` is compatible with deep_search_v3's `ExecutorResult` for PlanAgent consumption

## Assumptions Made

- **No ReportNode**: Unlike deep_search_v2 which has a ReportNode that inserts artifacts into DB, this package does NOT handle artifact creation. It returns `RegSearchResult` to the caller (PlanAgent), which handles artifact creation. This keeps the package focused on search + synthesis only.
- **No DB writes**: The package only reads from Supabase (search RPCs + unfold queries). All writes (artifacts, logs) are handled by the caller.
- **Regulations only**: Unlike deep_search_v2 which handles all 3 domains, this package handles ONLY regulations. The `tool` field on SearchQuery and WeakAxis from V2 is not needed.
- **Stateless agents**: Both expander and aggregator agents are stateless (no deps_type). They receive all context via the user message and system prompt. Search infrastructure access is handled by SearchNode programmatically.
- **Copy search infrastructure**: `search_pipeline.py` and `regulation_unfold.py` are copied from deep_search_v3, not imported. This avoids circular dependencies and allows independent evolution.
- **Citation compatibility**: The Citation model matches deep_search_v3's Citation model. The `court` field is omitted since this is regulations-only.
- **Embedding function provided by caller**: The package does not create its own embedding function. It receives `embedding_fn` via deps, which the caller (deep_search_v3 runner or CLI) provides.
- **Sync Supabase client**: Following the established project pattern, the Supabase client is sync and wrapped in `asyncio.to_thread()` for async contexts.
- **Fallback models handled by caller**: The model table lists fallbacks, but the actual fallback logic (try primary, fall back to secondary) is handled by `get_agent_model()` or by the caller. The agents themselves are created with a single model.

---
Generated: 2026-04-04
Note: This is a focused domain search package. After standalone testing, it will be wired into deep_search_v3 as a replacement for the monolithic regulations executor.
