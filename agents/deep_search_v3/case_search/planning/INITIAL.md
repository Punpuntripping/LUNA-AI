# Case Search - Domain-Specific Search Loop Requirements

## What This Agent Does

A cases-domain search loop that replaces the monolithic cases executor in `agents/deep_search_v3/`. It runs a `pydantic_graph` loop -- QueryExpander (1 LLM call) -> SearchNode (0 LLM calls, programmatic search) -> Aggregator (1 LLM call) -- with loop-back to the Expander on weak results (max 2 retries, 3 total rounds). Weak axes from the Aggregator are fed back to the QueryExpander as dynamic instructions on retry so it generates queries only for the deficient aspects. This is one of 3 domain-specific search packages (`reg_search`, `case_search`, `compliance_search`) that deep_search_v3's PlanAgent will invoke instead of the current monolithic executors.

## Agent Classification

- **Type**: Workflow (pydantic_graph loop with 2 embedded pydantic_ai Agents)
- **Complexity**: Medium
- **Domain**: Saudi legal research -- Arabic-first, court rulings and judicial precedents

## Architecture

```
ExpanderNode (1 LLM call -- QueryExpander agent)
     |
SearchNode (0 LLM calls -- programmatic embed + hybrid RPC + optional rerank)
     |
AggregatorNode (1 LLM call -- Aggregator/Synthesizer agent)
   |         |
   | weak    | sufficient (or max retries hit)
   |         |
   v         v
ExpanderNode  End(CaseSearchResult)
(dynamic instruction: weak_axes from Aggregator)
```

### Key Design: Retry via Dynamic Instructions

When the Aggregator returns `sufficient=False` and retries remain (max 2 retries = up to 3 total rounds), the loop goes back to ExpanderNode. The `weak_axes` from the Aggregator are injected as **dynamic instructions** into the QueryExpander on the retry round. The QueryExpander then generates queries ONLY for the weak aspects, not re-querying what was already strong.

### How It Will Be Called

deep_search_v3's PlanAgent currently calls `invoke_executors(dispatches)` which creates monolithic executor agents. After this migration, PlanAgent will instead call domain-specific tools like `invoke_case_search(focus_instruction, user_context)` which internally runs this pydantic_graph loop.

Entry point signature:
```python
async def run_case_search(
    focus_instruction: str,
    user_context: str,
    deps: CaseSearchDeps,
) -> CaseSearchResult
```

## Core Features (MVP)

1. **QueryExpander**: LLM agent that generates 2-4 Arabic search queries targeting court rulings, understanding dispute types and judicial language. On retry rounds, reads `weak_axes` dynamic instructions and generates queries ONLY for weak aspects.
2. **SearchNode**: Programmatic node that embeds queries, calls `hybrid_search_cases` RPC (BM25 + semantic via RRF), optionally reranks via Jina, formats results into markdown blocks.
3. **Aggregator**: LLM agent that evaluates result quality (strong/moderate/weak), produces Arabic judicial analysis markdown, extracts structured citations, and identifies weak axes for retry.
4. **Loop orchestration**: pydantic_graph wiring with ExpanderNode -> SearchNode -> AggregatorNode, loop-back on weak results, max 3 rounds.

## Technical Setup

### Models

**Model slots and assignments for `agent_models.py`:**

| Agent Slot | Primary | Fallback 1 | Fallback 2 |
|-----------|---------|------------|------------|
| `case_search_expander` | `or-deepseek-v3.2` | `or-gemini-2.5-flash` | `or-mimo-v2-pro` |
| `case_search_aggregator` | `or-qwen3.5-397b` | `or-gemini-2.5-flash` | `or-mimo-v2-pro` |

All keys exist in `agents/model_registry.py`. The implementation should use `get_agent_model("case_search_expander")` and `get_agent_model("case_search_aggregator")` from `agents/utils/agent_models.py`.

**Required `agent_models.py` additions:**
```python
# Case Search -- domain-specific search loop
"case_search_expander":   "or-deepseek-v3.2",
"case_search_aggregator": "or-qwen3.5-397b",
```

### Output Types

#### CaseSearchResult (returned to PlanAgent -- the terminal output)

```python
class CaseSearchResult(BaseModel):
    """Final result from the case search loop, returned to the calling PlanAgent."""

    quality: Literal["strong", "moderate", "weak"] = Field(
        description='Overall quality: "strong" = directly relevant rulings with clear principles, '
                    '"moderate" = similar disputes, "weak" = unrelated results',
    )
    summary_md: str = Field(
        description="Arabic judicial analysis markdown -- how courts ruled on the issue",
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="Structured citations for every court ruling referenced",
    )
    domain: Literal["cases"] = Field(
        default="cases",
        description="Always 'cases' for this domain package",
    )
    queries_used: list[str] = Field(
        default_factory=list,
        description="All Arabic search queries executed across all rounds",
    )
    rounds_used: int = Field(
        default=1,
        description="How many rounds (1-3) were executed",
    )
```

This must be compatible with the existing `ExecutorResult` schema from `agents/deep_search_v3/models.py`. The fields are a subset -- `CaseSearchResult` has the same `quality`, `summary_md`, `citations`, `domain`, `queries_used`, `rounds_used` fields. The PlanAgent can consume it interchangeably.

#### ExpanderOutput (QueryExpander LLM output)

```python
class ExpanderOutput(BaseModel):
    """Structured output from the QueryExpander agent."""

    queries: list[str] = Field(
        description="2-4 Arabic search queries targeting court rulings and judicial precedents",
    )
    rationales: list[str] = Field(
        default_factory=list,
        description="Internal rationale per query (for logs/debugging only, not shown to user)",
    )
```

Note: Unlike the deep_search_v2 `ExpanderOutput` which uses `list[SearchQuery]` with a `tool` field, this is cases-only so queries are plain strings. No `tool` discriminator needed.

#### AggregatorOutput (Aggregator LLM output)

```python
class AggregatorOutput(BaseModel):
    """Structured output from the Aggregator/Synthesizer agent."""

    sufficient: bool = Field(
        description="True if results adequately cover the legal question (~80%+ coverage)",
    )
    quality: Literal["strong", "moderate", "weak"] = Field(
        description='Quality assessment of case search results',
    )
    weak_axes: list[WeakAxis] = Field(
        default_factory=list,
        description="Aspects where results are insufficient (empty if sufficient=True)",
    )
    synthesis_md: str = Field(
        description="Arabic judicial analysis markdown -- how courts ruled, principles extracted",
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="Structured citations for every court ruling referenced",
    )
```

#### WeakAxis (retry feedback model)

```python
class WeakAxis(BaseModel):
    """An aspect where case search results were insufficient."""

    reason: str = Field(
        description="What is missing or why the results are weak (Arabic)",
    )
    suggested_query: str = Field(
        description="Suggested Arabic re-search query for the next round",
    )
```

Note: Unlike deep_search_v2's `WeakAxis`, this has no `tool` field since this package only searches cases.

#### Citation (shared with deep_search_v3)

```python
class Citation(BaseModel):
    """Structured citation for a court ruling. Must match deep_search_v3 Citation schema."""

    source_type: str = Field(
        default="case",
        description='Always "case" for this domain package',
    )
    ref: str = Field(
        description="Unique identifier -- case_ref or case_number",
    )
    title: str = Field(
        description="Arabic title -- typically court + case_number + date",
    )
    content_snippet: str = Field(
        default="",
        description="Relevant excerpt from the ruling text",
    )
    regulation_title: str | None = Field(
        default=None,
        description="Referenced regulation name (from case metadata, if applicable)",
    )
    article_num: str | None = Field(
        default=None,
        description="Referenced article number (from case metadata, if applicable)",
    )
    court: str | None = Field(
        default=None,
        description="Court name",
    )
    relevance: str = Field(
        default="",
        description="Why this ruling supports the analysis",
    )
```

This is intentionally identical to the `Citation` model in `agents/deep_search_v3/models.py` and `agents/deep_search_v2/models.py` so PlanAgent can consume citations uniformly across domain packages.

#### LoopState (mutable graph state dataclass)

```python
@dataclass
class LoopState:
    """Mutable state that accumulates across graph nodes within one run_case_search() call."""

    focus_instruction: str           # From PlanAgent -- what to focus on
    user_context: str                # From PlanAgent -- user's situation
    expander_output: ExpanderOutput | None = None
    search_results_md: list[str] = field(default_factory=list)   # Raw markdown per query
    search_result_counts: list[int] = field(default_factory=list) # Result count per query
    aggregator_output: AggregatorOutput | None = None
    weak_axes: list[WeakAxis] = field(default_factory=list)
    all_queries: list[str] = field(default_factory=list)  # Accumulated across all rounds
    round_count: int = 0
    max_rounds: int = 3
```

### Required Tools

This package has NO pydantic_ai tools. The QueryExpander and Aggregator are tool-less structured-output agents. SearchNode is a programmatic graph node that calls the search pipeline directly.

### Dependencies (deps dataclass)

```python
@dataclass
class CaseSearchDeps:
    """Dependencies injected into the case search loop."""

    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    jina_api_key: str = ""
    http_client: httpx.AsyncClient | None = None
    use_reranker: bool = False
    mock_results: dict | None = None  # {"cases": "...mock markdown..."} for testing
    _events: list[dict] = field(default_factory=list)       # SSE events collected
    _search_log: list[dict] = field(default_factory=list)   # Per-query search log
```

This is a lean subset of `DeepSearchV3Deps` -- no `user_id`, `conversation_id`, `case_id`, `artifact_id`. The calling PlanAgent constructs `CaseSearchDeps` from its own `DeepSearchV3Deps` before invoking `run_case_search()`.

### External Services

- **Supabase**: `hybrid_search_cases` RPC (BM25 + semantic via RRF)
- **Jina Reranker API**: Optional reranking via `jina-reranker-v3` at `https://api.jina.ai/v1/rerank`
- **Embedding API**: Query embedding via `deps.embedding_fn` (shared across all agents)
- **OpenRouter**: LLM calls for expander (`or-deepseek-v3.2`) and aggregator (`or-qwen3.5-397b`) via `agents/model_registry.py`

## Search Pipeline Specification

This package contains its **own copy** of the cases search pipeline, adapted from `agents/deep_search_v3/executors/search_pipeline.py` (the `search_cases_pipeline` function and its helpers).

### Pipeline Steps

1. **Embed query**: Call `deps.embedding_fn(query)` to get vector embedding
2. **Hybrid search**: Call `hybrid_search_cases` RPC with `query_text`, `query_embedding`, `match_count=30`, `full_text_weight=0.25`, `semantic_weight=0.75`
3. **Rerank or score fallback**:
   - If `deps.use_reranker` and `deps.jina_api_key`: POST to Jina Reranker v3, take top 10
   - Otherwise: Sort by hybrid RRF `score` descending, take top 10
4. **Format results**: Convert each case row into a markdown block using `_format_case_result()`

### Case Row Schema (from hybrid_search_cases RPC)

Each row returned by the RPC has these fields:
- `court` (str): Court name
- `city` (str): City
- `court_level` (str): "appeal" or "first_instance"
- `case_number` (str): Case number
- `judgment_number` (str): Judgment number
- `date_hijri` (str): Hijri date
- `content` (str): Ruling text -- primary payload for search and analysis
- `legal_domains` (list): List of legal domain strings
- `referenced_regulations` (list[dict]): Each has regulation name + article number
- `appeal_result` (str | None): Appeal outcome
- `appeal_court` (str | None): Appeal court
- `appeal_date_hijri` (str | None): Appeal date
- `details_url` (str): Link to full ruling
- `score` (float): Hybrid RRF score from the RPC
- `case_ref` (str): Unique case reference

### Constants

```python
CASES_TOP_N = 10           # Max cases after rerank/score fallback
MATCH_COUNT = 30           # Candidates from hybrid RPC
MAX_CONTENT_CHARS = 5_000  # Truncation limit for ruling text in formatted output
```

### Functions to Carry Over (adapted from deep_search_v3)

| Function | Source | Changes |
|----------|--------|---------|
| `search_cases_pipeline()` | `deep_search_v3/executors/search_pipeline.py` | Change `deps: ExecutorDeps` to `deps: CaseSearchDeps`; return type unchanged |
| `_hybrid_rpc_search()` | Same file | No changes -- shared helper |
| `_rerank()` | Same file | No changes -- shared helper |
| `_score_fallback()` | Same file | No changes -- shared helper |
| `_format_case_result()` | Same file | No changes |
| `_collect_case_references()` | Same file | No changes |

## Prompt Design Requirements

### QueryExpander System Prompt

Arabic-first prompt. The expander for cases needs to understand:

**Core identity:**
- Part of Luna Legal AI platform -- Saudi legal research
- Specializes in court rulings and judicial precedents
- Receives a focus_instruction from the PlanAgent supervisor

**Query expansion rules:**
1. Describe the **dispute type**, not the court name or law name
2. Use terms from judicial ruling language: "دعوى", "منازعة", "فصل تعسفي", "تعويض", "مطالبة"
3. Each query covers **one angle** of the legal dispute
4. Frame from different perspectives (plaintiff vs defendant)
5. Don't restrict to one legal domain -- cases often cross multiple domains
6. Cases have `referenced_regulations` metadata, so mentioning a regulation in the query CAN help if the user mentioned it
7. 2-4 queries per round

**Round 2+ behavior (dynamic instruction injection):**
When `weak_axes` are provided, the expander MUST:
- Generate queries ONLY for the weak aspects identified
- NOT re-query aspects that were already strong
- Use the `suggested_query` from each `WeakAxis` as a starting point, but can refine/expand
- The number of queries should match the number of weak axes (1-2 typically)

**Dynamic instruction template for retry rounds:**
```
---
جولة إعادة البحث ({round_number} من {max_rounds})
الجوانب الضعيفة التي تحتاج بحثاً إضافياً:
{for each weak_axis:}
- السبب: {reason}
  الاستعلام المقترح: {suggested_query}

ابحث فقط في هذه الجوانب. لا تُعد البحث في الجوانب التي كانت نتائجها قوية.
```

### Aggregator/Synthesizer System Prompt

Arabic-first prompt. The aggregator for cases needs to:

**Quality evaluation criteria:**
- **strong**: Directly relevant rulings with clear judicial principles, same type of dispute
- **moderate**: Similar disputes or applicable principles from related areas
- **weak**: Unrelated rulings or tangential results

**Sufficiency threshold:** `sufficient=True` when approximately 80%+ of the user's legal question is covered by the found rulings.

**When sufficient (or max retries hit):**
- Produce Arabic judicial analysis markdown (`synthesis_md`) showing:
  - How courts ruled on the specific issue
  - Extracted judicial principles (مبادئ قضائية) from ruling texts
  - Appeal outcomes and their significance
  - Comparison of how different courts ruled on similar disputes
- Extract structured `Citation` objects for each referenced ruling

**When insufficient (and retries remain):**
- Set `sufficient=False`
- Identify weak axes with:
  - `reason`: What aspect of the question is not covered (Arabic)
  - `suggested_query`: A specific Arabic search query to try next
- Still populate `synthesis_md` and `citations` with whatever was found so far (carried forward)

**Citation extraction rules:**
- `source_type` = "case"
- `ref` = case_ref or case_number
- `title` = court + case_number + date_hijri formatted as Arabic title
- `content_snippet` = key excerpt from ruling text (max ~200 chars)
- `court` = court name
- `relevance` = why this ruling supports the analysis

## pydantic_graph Loop Specification

### Nodes

#### ExpanderNode (1 LLM call)

- Creates a QueryExpander pydantic_ai Agent with `ExpanderOutput` output_type
- Input via user message: `focus_instruction` + `user_context` from LoopState
- Round 1: Standard expansion from focus_instruction
- Round 2+: Injects `weak_axes` as dynamic instruction, generates queries ONLY for weak aspects
- Stores output in `LoopState.expander_output`
- Appends new queries to `LoopState.all_queries`
- Increments `LoopState.round_count`
- Always transitions to SearchNode

#### SearchNode (0 LLM calls)

- Reads `LoopState.expander_output.queries`
- Executes all queries via `asyncio.gather` using `search_cases_pipeline()`
- Each query: embed -> hybrid RPC -> optional rerank -> format markdown
- Appends result markdown to `LoopState.search_results_md`
- Appends result counts to `LoopState.search_result_counts`
- Emits SSE status events via `deps._events`
- Always transitions to AggregatorNode

#### AggregatorNode (1 LLM call)

- Creates an Aggregator pydantic_ai Agent with `AggregatorOutput` output_type
- Input via user message: `focus_instruction` + ALL accumulated search results markdown
- Evaluates quality and sufficiency
- Routing logic:
  - `sufficient=False` AND `round_count < max_rounds` -> ExpanderNode (loop back)
  - `sufficient=True` OR `round_count >= max_rounds` -> `End(CaseSearchResult)`
- On loop back: stores `weak_axes` in LoopState for the next ExpanderNode round
- On terminal: constructs `CaseSearchResult` from AggregatorOutput fields

### Loop Constants

```python
MAX_ROUNDS = 3           # Max loop iterations before forced completion
QUERIES_PER_ROUND = "2-4" # LLM decides within this range
```

### Loop Entry Point

```python
async def run_case_search(
    focus_instruction: str,
    user_context: str,
    deps: CaseSearchDeps,
) -> CaseSearchResult:
    """Run the complete case search loop.

    Called by deep_search_v3's PlanAgent via the invoke_case_search tool.

    Args:
        focus_instruction: Arabic instruction from PlanAgent -- what to focus on.
        user_context: Arabic context -- user's personal situation/question.
        deps: CaseSearchDeps with supabase, embedding_fn, etc.

    Returns:
        CaseSearchResult with quality assessment, judicial analysis, citations.
    """
```

## Usage Limits

```python
from pydantic_ai.usage import UsageLimits

# QueryExpander -- tight limits (structured output only, 2-4 queries)
EXPANDER_LIMITS = UsageLimits(
    response_tokens_limit=2_000,
    request_limit=3,
)

# Aggregator -- moderate limits (synthesis can be long)
AGGREGATOR_LIMITS = UsageLimits(
    response_tokens_limit=6_000,
    request_limit=3,
)
```

## SSE Event Flow

Events are collected in `CaseSearchDeps._events`. The calling PlanAgent transfers these to its own `_sse_events` list after `run_case_search()` returns.

Expected SSE event types:
- `{"type": "status", "text": "جاري البحث في السوابق القضائية: {query}..."}` -- search started
- `{"type": "status", "text": "جاري البحث في قاعدة بيانات الأحكام القضائية..."}` -- RPC call
- `{"type": "status", "text": "جاري إعادة ترتيب {N} نتيجة قضائية عبر Jina..."}` -- reranking
- `{"type": "status", "text": "تم استرجاع {N} حكم قضائي."}` -- results ready
- `{"type": "status", "text": "جاري إعادة البحث في الجوانب الضعيفة (الجولة {N})..."}` -- retry

## File Structure

```
agents/case_search/
    __init__.py              # Exports: run_case_search, CaseSearchDeps, CaseSearchResult
    models.py                # CaseSearchResult, ExpanderOutput, AggregatorOutput,
                             #   WeakAxis, Citation, LoopState
    prompts.py               # Expander system prompt, Aggregator system prompt,
                             #   build_expander_dynamic_instructions(),
                             #   build_aggregator_user_message()
    search_pipeline.py       # search_cases_pipeline() + helpers: _hybrid_rpc_search,
                             #   _rerank, _score_fallback, _format_case_result,
                             #   _collect_case_references
    expander.py              # QueryExpander pydantic_ai Agent definition
    aggregator.py            # Aggregator pydantic_ai Agent definition
    loop.py                  # pydantic_graph: ExpanderNode, SearchNode, AggregatorNode
                             #   + run_case_search() entry point
    logger.py                # Run log writer (JSON log per run)
    cli.py                   # Standalone CLI for testing
    tests/
        __init__.py
        conftest.py          # Fixtures: mock deps, mock embedding_fn, mock supabase
        test_expander.py     # Test QueryExpander with TestModel/FunctionModel
        test_aggregator.py   # Test Aggregator with TestModel/FunctionModel
        test_loop.py         # Test full loop with mocked search results
        test_search_pipeline.py  # Test search_cases_pipeline with mock Supabase/Jina
```

## agent_models.py Updates Required

Add to `agents/utils/agent_models.py`:
```python
# Case Search -- domain-specific search loop
"case_search_expander":   "or-deepseek-v3.2",
"case_search_aggregator": "or-qwen3.5-397b",
```

## CLI for Testing

```bash
# Basic usage
python -m agents.case_search.cli "سوابق قضائية في الفصل التعسفي وعدم صرف الأجور"

# With reranking
python -m agents.case_search.cli "سوابق قضائية في الفصل التعسفي وعدم صرف الأجور" --rerank

# Verbose (show all rounds, queries, intermediate results)
python -m agents.case_search.cli "سوابق قضائية في الفصل التعسفي وعدم صرف الأجور" --rerank --verbose

# With mock results (for testing without DB)
python -m agents.case_search.cli "test query" --mock
```

The CLI should:
1. Create Supabase client from `shared/db/client.py`
2. Create embedding function from `agents/utils/embeddings.py`
3. Build `CaseSearchDeps`
4. Call `run_case_search(focus_instruction=query, user_context="", deps=deps)`
5. Print result: quality, rounds_used, summary_md, citations count
6. If `--verbose`: also print all queries_used, SSE events, search logs

## Success Criteria

- [ ] QueryExpander produces 2-4 Arabic search queries focused on dispute types using judicial language
- [ ] SearchNode correctly calls `hybrid_search_cases` RPC and formats results
- [ ] Aggregator evaluates quality (strong/moderate/weak) and identifies weak axes with suggested queries
- [ ] Loop correctly loops back to ExpanderNode on weak results (up to MAX_ROUNDS=3)
- [ ] On retry, QueryExpander receives weak_axes as dynamic instruction and generates queries ONLY for weak aspects
- [ ] `run_case_search()` returns `CaseSearchResult` compatible with deep_search_v3's `ExecutorResult` schema
- [ ] SSE events collected in `deps._events` through all rounds
- [ ] Optional Jina reranking works when `use_reranker=True` and `jina_api_key` is set
- [ ] Handles gracefully: empty search results, RPC failures, LLM failures, Jina failures
- [ ] Error fallback returns `CaseSearchResult(quality="weak", summary_md=<error_msg_ar>, ...)`
- [ ] Citation schema matches `agents/deep_search_v3/models.py` Citation model exactly
- [ ] CLI works end-to-end with real Supabase and with mock results
- [ ] All tests pass using `pydantic_ai.models.test.TestModel` and `pydantic_ai.models.function.FunctionModel`

## Testing Requirements

### test_expander.py
1. Test round 1: Given focus_instruction, produces 2-4 queries (use FunctionModel to return deterministic ExpanderOutput)
2. Test round 2 with weak_axes: Verify dynamic instructions are injected and queries target only weak aspects
3. Test empty focus_instruction edge case

### test_aggregator.py
1. Test sufficient results: Given strong search results, returns `sufficient=True`, quality="strong", synthesis_md, citations
2. Test insufficient results: Given weak search results, returns `sufficient=False`, weak_axes with suggested queries
3. Test forced completion: When round_count >= max_rounds, always returns terminal result regardless of quality

### test_loop.py
1. Test single-round success: Mock expander + search + aggregator, verify CaseSearchResult
2. Test multi-round retry: Mock aggregator to return `sufficient=False` first round, `sufficient=True` second round
3. Test max retries: Mock aggregator to always return `sufficient=False`, verify loop terminates at round 3
4. Test SSE events propagation across rounds
5. Test error handling: Mock search pipeline failure, verify graceful fallback

### test_search_pipeline.py
1. Test `search_cases_pipeline` with mocked Supabase RPC
2. Test Jina reranking with mocked HTTP response
3. Test score fallback when Jina is unavailable
4. Test `_format_case_result` output format
5. Test `_collect_case_references` deduplication
6. Test mock_results passthrough

## Reusable Components from deep_search_v3

| Component | Source | Changes |
|-----------|--------|---------|
| `search_cases_pipeline()` | `deep_search_v3/executors/search_pipeline.py` | Deps type: `ExecutorDeps` -> `CaseSearchDeps` |
| `_hybrid_rpc_search()` | Same file | Copy as-is |
| `_rerank()` | Same file | Copy as-is |
| `_score_fallback()` | Same file | Copy as-is |
| `_format_case_result()` | Same file | Copy as-is |
| `_collect_case_references()` | Same file | Copy as-is |
| `Citation` model | `deep_search_v3/models.py` or `deep_search_v2/models.py` | Copy as-is (identical in both) |
| Cases expansion guidance | `deep_search_v3/prompts.py:_build_cases_expansion_guidance()` | Adapt into expander system prompt |
| Cases executor prompt pattern | `deep_search_v3/prompts.py:CASES_EXECUTOR_PROMPT` | Adapt -- split into expander + aggregator prompts |

## Assumptions Made

1. **Cases are flat (no unfolding)**: Unlike regulations which have article->section->regulation hierarchy and require unfold logic, cases are searched directly and formatted as-is. This makes the search pipeline simpler than the regulations pipeline.
2. **No DB writes**: Unlike deep_search_v2's ReportNode which creates artifacts, this package is a pure search+analyze loop. Report creation and artifact management remain in the calling PlanAgent.
3. **Citation schema must be identical**: The Citation model must match deep_search_v3 exactly so PlanAgent can merge citations from different domain packages uniformly.
4. **SSE events are appended, not streamed**: Events are collected in `deps._events` list. The calling PlanAgent propagates them. No direct SSE streaming from this package.
5. **mock_results key is "cases"**: When `deps.mock_results` is set and contains a `"cases"` key, the search pipeline returns that mock markdown instead of calling the RPC.
6. **Embedding function is shared**: The same `embedding_fn` from `agents/utils/embeddings.py` is used. No case-specific embedding model.
7. **Search pipeline is a local copy, not imported**: To avoid coupling between packages, the search pipeline functions are copied into `case_search/search_pipeline.py` rather than importing from `deep_search_v3/executors/search_pipeline.py`. This allows independent evolution.
8. **No task orchestration pattern**: This package does NOT use `TaskContinue`/`TaskEnd`. It returns `CaseSearchResult` directly. The calling PlanAgent handles task lifecycle.
9. **Agent temperature**: The expander and aggregator agents should use default temperature from `model_registry.py` (via `get_model_settings`). No custom temperature override needed.
10. **Jina reranking is optional**: The default is `use_reranker=False`. When the PlanAgent constructs `CaseSearchDeps`, it can opt-in based on the user's subscription tier or CLI flag.

---

Generated: 2026-04-04
Note: This is a domain-specific search package that replaces the monolithic cases executor in deep_search_v3. After this works, the same pattern will be applied to reg_search and compliance_search.
