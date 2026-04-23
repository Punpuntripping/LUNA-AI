# Compliance Search - Domain-Specific Search Loop Requirements

## What This Agent Does

A compliance/government-services domain search loop package that replaces the monolithic compliance executor in `agents/deep_search_v3/`. It follows a `pydantic_graph` loop pattern -- ExpanderNode (LLM) -> SearchNode (programmatic) -> AggregatorNode (LLM) -- with loop-back on weak results (max 2 retries, 3 total rounds). Weak axes from the Aggregator are fed back to the QueryExpander as dynamic instructions on retry. This is one of 3 domain-specific search packages (reg_search, case_search, compliance_search) that deep_search_v3's PlanAgent will invoke.

## Agent Classification

- **Type**: Workflow (pydantic_graph loop with 2 embedded pydantic_ai Agents)
- **Complexity**: Medium
- **Domain**: Saudi government e-services and official platforms (Qiwa, Absher, Nafith, Najiz, Egar, Muqeem, etc.)

## Architecture Overview

```
run_compliance_search(focus_instruction, user_context, deps)
    |
    v
pydantic_graph: ExpanderNode -> SearchNode -> AggregatorNode
                    ^                              |
                    |  weak (retries remain)        | sufficient OR max retries
                    +------------------------------+
                                                   |
                                                   v
                                            End(ComplianceSearchResult)
```

### Loop Semantics

- **ExpanderNode** (1 LLM call): Runs the QueryExpander pydantic_ai Agent to produce 2-4 Arabic search queries. On retry rounds, receives `weak_axes` from the Aggregator as dynamic instructions to guide re-expansion.
- **SearchNode** (0 LLM calls): Programmatic node. Runs each query through the compliance search pipeline (embed -> hybrid_search_services RPC -> optional Jina rerank -> format). Executes queries concurrently via `asyncio.gather`.
- **AggregatorNode** (1 LLM call): Runs the Aggregator pydantic_ai Agent to evaluate result quality and synthesize. Routes to ExpanderNode (loop back) or End.

### Loop Constants

- `MAX_ROUNDS = 3` (initial round + up to 2 retries)
- Queries per round: 2-4
- Top results per search query: 3 (SERVICES_TOP_N = 3)

## How It Will Be Called

deep_search_v3's PlanAgent will call a tool like `invoke_compliance_search(focus_instruction, user_context)` which internally creates `ComplianceSearchDeps` and calls:

```python
async def run_compliance_search(
    focus_instruction: str,
    user_context: str,
    deps: ComplianceSearchDeps,
) -> ComplianceSearchResult
```

The returned `ComplianceSearchResult` is compatible with deep_search_v3's `ExecutorResult` schema.

## Core Features (MVP)

1. **Query expansion**: LLM-powered expansion of focus instructions into 2-4 precise Arabic search queries targeting government services and platforms
2. **Hybrid search pipeline**: Embed query -> `hybrid_search_services` Supabase RPC (BM25 + semantic via RRF) -> optional Jina rerank -> format top 3 results as markdown
3. **Quality evaluation and synthesis**: LLM-powered aggregation that evaluates result quality (strong/moderate/weak), produces Arabic service listing markdown with citations, and identifies weak axes for retry
4. **Retry loop**: Automatic re-expansion on weak results with dynamic instruction injection of weak axes (max 2 retries)

## Technical Setup

### Models

| Agent Slot (key in `agent_models.py`) | Primary | Fallback 1 | Fallback 2 |
|---------------------------------------|---------|------------|------------|
| `compliance_search_expander` | `or-deepseek-v3.2` | `or-gemini-2.5-flash` | `or-mimo-v2-pro` |
| `compliance_search_aggregator` | `or-qwen3.5-397b` | `or-gemini-2.5-flash` | `or-mimo-v2-pro` |

All keys exist in `agents/model_registry.py`. The `agent_models.py` file needs two new entries added:

```python
"compliance_search_expander":   "or-deepseek-v3.2",
"compliance_search_aggregator": "or-qwen3.5-397b",
```

### Output Types

#### ComplianceSearchResult (returned to caller / PlanAgent)

```python
class ComplianceSearchResult(BaseModel):
    quality: Literal["strong", "moderate", "weak"]
    summary_md: str          # Arabic service listing markdown
    citations: list[Citation]
    domain: Literal["compliance"] = "compliance"
    queries_used: list[str]
    rounds_used: int
```

Must be compatible with deep_search_v3's `ExecutorResult` schema (same field names and types for: quality, summary_md, citations, domain, queries_used, rounds_used).

#### ExpanderOutput

```python
class ExpanderOutput(BaseModel):
    queries: list[str]       # 2-4 Arabic search queries
    rationales: list[str]    # Internal rationale per query (for logging, not sent to search)
```

Note: Unlike deep_search_v2 which uses `SearchQuery(tool, query, rationale)`, this package is single-domain (compliance only), so queries are plain strings. No `tool` field needed.

#### AggregatorOutput

```python
class AggregatorOutput(BaseModel):
    sufficient: bool
    quality: Literal["strong", "moderate", "weak"]
    weak_axes: list[WeakAxis]
    synthesis_md: str         # Arabic service listing markdown
    citations: list[Citation]
```

#### WeakAxis

```python
class WeakAxis(BaseModel):
    reason: str              # Why this axis is weak (Arabic)
    suggested_query: str     # Suggested re-search query (Arabic)
```

#### Citation

Reuse deep_search_v3's Citation model exactly:

```python
class Citation(BaseModel):
    source_type: str         # "service" for this domain
    ref: str                 # service_ref from DB
    title: str               # service_name_ar
    content_snippet: str = ""
    regulation_title: str | None = None   # Not used for services
    article_num: str | None = None        # Not used for services
    court: str | None = None              # Not used for services
    relevance: str = ""
```

#### LoopState (mutable graph state dataclass)

```python
@dataclass
class LoopState:
    focus_instruction: str
    user_context: str
    expander_output: ExpanderOutput | None = None
    all_search_results: list[SearchResult] = field(default_factory=list)
    aggregator_output: AggregatorOutput | None = None
    weak_axes: list[WeakAxis] = field(default_factory=list)
    round_count: int = 0
    queries_used: list[str] = field(default_factory=list)
    sse_events: list[dict] = field(default_factory=list)
    inner_usage: list[dict] = field(default_factory=list)
    search_results_log: list[dict] = field(default_factory=list)
```

#### SearchResult (programmatic dataclass)

```python
@dataclass
class SearchResult:
    query: str
    raw_markdown: str
    result_count: int
```

### Required Agents (pydantic_ai)

1. **QueryExpander** (`expander.py`): `Agent[None, ExpanderOutput]` -- structured output, no tools, no deps. Dynamic instructions inject weak_axes on round 2+.
2. **Aggregator** (`aggregator.py`): `Agent[None, AggregatorOutput]` -- structured output, no tools, no deps. Receives all accumulated results as user message.

Both agents use `Agent[None, ...]` (no deps type parameter) because they are pure LLM agents with no tool calls. All search infrastructure access happens in the programmatic SearchNode.

### Graph Nodes (pydantic_graph)

1. **ExpanderNode**: `BaseNode[LoopState, ComplianceSearchDeps, ComplianceSearchResult]` -- runs QueryExpander agent, stores output in state, transitions to SearchNode
2. **SearchNode**: `BaseNode[LoopState, ComplianceSearchDeps, ComplianceSearchResult]` -- runs search pipeline for each query concurrently, transitions to AggregatorNode
3. **AggregatorNode**: `BaseNode[LoopState, ComplianceSearchDeps, ComplianceSearchResult]` -- runs Aggregator agent, routes to ExpanderNode (loop back) or End(ComplianceSearchResult)

### Dependencies (deps dataclass)

```python
@dataclass
class ComplianceSearchDeps:
    supabase: SupabaseClient
    embedding_fn: Callable[[str], Awaitable[list[float]]]
    jina_api_key: str = ""
    http_client: httpx.AsyncClient | None = None
    use_reranker: bool = False
    mock_results: dict | None = None    # For testing: {"compliance": "...markdown..."}
    _events: list[dict] = field(default_factory=list)
    _search_log: list[dict] = field(default_factory=list)
```

### External Services

- **Supabase**: `hybrid_search_services` RPC (BM25 + semantic via RRF) -- searches the government services vector table
- **Jina Reranker API** (`https://api.jina.ai/v1/rerank`, model: `jina-reranker-v3`): Optional reranking of search results. Falls back to hybrid RRF score sort when disabled or on failure.
- **Embedding API**: Query embedding via `deps.embedding_fn` (injected by caller, typically `agents/utils/embeddings.py`)
- **OpenRouter**: LLM calls for expander (`or-deepseek-v3.2`) and aggregator (`or-qwen3.5-397b`) via `agents/model_registry.py`

### Usage Limits

```python
from pydantic_ai.usage import UsageLimits

EXPANDER_LIMITS = UsageLimits(
    response_tokens_limit=2_000,
    request_limit=3,
)

AGGREGATOR_LIMITS = UsageLimits(
    response_tokens_limit=6_000,
    request_limit=3,
)
```

## Prompt Design Requirements

### QueryExpander Prompt

Arabic-first system prompt. Key characteristics for the compliance domain:

**Opening (Arabic):**
```
انت موسّع استعلامات متخصص في الخدمات الحكومية الإلكترونية السعودية ضمن منصة لونا للذكاء الاصطناعي القانوني.
```

**Core instructions:**
- Receives focus_instruction + user_context, expands into 2-4 precise Arabic search queries
- Only 3 results returned per query -- precision is critical
- Platform names ARE useful search terms here (unlike regulations): قوى، أبشر، نافذ، إيجار، ناجز، مقيم
- Search by service type or procedure name
- Can mention government agency names
- Each query = one specific government service or procedure
- Do NOT search for legal texts -- that is the regulations domain's job

**Query expansion rules (Arabic):**
```
قواعد صياغة الاستعلامات:
1. استخدم اسم المنصة إذا عُرف (قوى، أبشر، نافذ، إيجار، ناجز، مقيم)
2. صِف نوع الخدمة أو الإجراء المطلوب بوضوح
3. يمكنك ذكر اسم الجهة الحكومية المعنية
4. كل استعلام = خدمة أو إجراء حكومي واحد محدد
5. لا تبحث عن نصوص قانونية — اتركها لمنفذ الأنظمة
```

**Round 2+ dynamic instructions:**
When `weak_axes` are provided, inject as dynamic instruction:
```
المحاور الضعيفة من الجولة السابقة:
- [reason]: [suggested_query]
...
وسّع استعلاماتك لتغطية هذه المحاور الضعيفة فقط. لا تكرر استعلامات ناجحة سابقة.
```

### Aggregator Prompt

Arabic-first system prompt. Key characteristics for the compliance domain:

**Opening (Arabic):**
```
أنت مقيّم ومُجمّع نتائج البحث في الخدمات الحكومية الإلكترونية السعودية ضمن منصة لونا للذكاء الاصطناعي القانوني.
```

**Quality evaluation criteria:**
- **strong**: Matching services with full details (platform, URL, requirements, steps)
- **moderate**: Related services but incomplete details or partially matching
- **weak**: Unrelated results or no government services found

**When sufficient (`sufficient=True`):**
- Produce organized Arabic service listing markdown with: service name, platform, provider, requirements, steps, URL
- Extract structured citations (source_type="service", ref=service_ref, title=service_name_ar, content_snippet, relevance)
- Focus on practical information: how to access the service, what documents are needed, steps, platform URL

**When insufficient (`sufficient=False`):**
- Identify weak axes with specific `suggested_query` per axis
- Explain what information is missing in `reason`
- Keep synthesis_md with whatever partial results exist

**Sufficiency threshold:** `sufficient=True` when the results contain at least one directly matching service with actionable details.

### Dynamic Instruction Builders (in prompts.py)

```python
def build_expander_dynamic_instructions(
    weak_axes: list[WeakAxis],
) -> str:
    """Build round-2+ dynamic instructions from weak axes."""

def build_aggregator_user_message(
    focus_instruction: str,
    user_context: str,
    search_results: list[SearchResult],
) -> str:
    """Build the user message for the Aggregator with all search results."""
```

## Search Pipeline Specification

The search pipeline is adapted from `agents/deep_search_v3/executors/search_pipeline.py` (the `search_compliance_pipeline` function and its helpers). This package contains its **own copy** in `search_pipeline.py`.

### Pipeline Steps

1. **Mock check**: If `deps.mock_results` has a "compliance" key, return it directly
2. **Embed query**: `embedding = await deps.embedding_fn(query)`
3. **Hybrid search**: `_hybrid_rpc_search(deps.supabase, "services", query, embedding, MATCH_COUNT=30)` -- calls `hybrid_search_services` Supabase RPC with BM25 + semantic weights
4. **Rerank or fallback**:
   - If `deps.use_reranker` and `deps.jina_api_key`: Jina rerank top `SERVICES_TOP_N=3`
   - Otherwise: `_score_fallback()` sorts by hybrid RRF score, takes top 3
5. **Format results**: `_format_service_result()` for each result, `_collect_service_references()` for refs block
6. **Return**: `(result_markdown, result_count)` tuple

### Service Data Schema (from Supabase)

Each service row from `hybrid_search_services` RPC has:
- `service_name_ar`: Arabic service name
- `provider_name`: Government provider/agency
- `platform_name`: Platform (e.g., قوى, أبشر, نافذ)
- `service_markdown`: Main content (full service description in markdown)
- `service_context`: Fallback content if no markdown
- `service_url`: URL to the service
- `category`: Service category
- `service_ref`: Unique reference identifier
- `score`: Hybrid RRF score from the RPC

### Functions to Port from deep_search_v3

From `agents/deep_search_v3/executors/search_pipeline.py`:
- `search_compliance_pipeline()` -> becomes `search_compliance()` in this package
- `_hybrid_rpc_search()` -> copy as-is (shared helper)
- `_rerank()` -> copy as-is (shared Jina reranker helper)
- `_score_fallback()` -> copy as-is
- `_format_service_result()` -> copy as-is
- `_collect_service_references()` -> copy as-is

The deps parameter type changes from `ExecutorDeps` to `ComplianceSearchDeps` (same shape for the fields used by these functions).

## Node Implementations

### ExpanderNode

```python
class ExpanderNode(BaseNode[LoopState, ComplianceSearchDeps, ComplianceSearchResult]):
    async def run(self, ctx: GraphRunContext[LoopState, ComplianceSearchDeps]) -> SearchNode:
        state = ctx.state
        state.round_count += 1

        # Create expander with weak_axes on round 2+
        weak_axes = state.weak_axes if state.round_count > 1 else None
        expander = create_expander_agent(weak_axes=weak_axes)

        # Build user message from focus_instruction + user_context
        user_message = state.focus_instruction
        if state.user_context:
            user_message += f"\n\nسياق المستخدم:\n{state.user_context}"

        result = await expander.run(user_message, usage_limits=EXPANDER_LIMITS)
        state.expander_output = result.output
        state.queries_used.extend(result.output.queries)

        # Capture usage for logging
        # ... (same pattern as deep_search_v2)

        return SearchNode()
```

**Error handling**: On expander failure, create fallback `ExpanderOutput` with the raw `focus_instruction` as a single query.

### SearchNode

```python
class SearchNode(BaseNode[LoopState, ComplianceSearchDeps, ComplianceSearchResult]):
    async def run(self, ctx: GraphRunContext[LoopState, ComplianceSearchDeps]) -> AggregatorNode:
        state = ctx.state
        deps = ctx.deps
        queries = state.expander_output.queries if state.expander_output else []

        # Execute all queries concurrently
        tasks = [search_compliance(query=q, deps=deps) for q in queries]
        results: list[tuple[str, int]] = await asyncio.gather(*tasks)

        # Convert to SearchResult and append to state
        for query, (markdown, count) in zip(queries, results):
            state.all_search_results.append(SearchResult(
                query=query, raw_markdown=markdown, result_count=count,
            ))

        return AggregatorNode()
```

### AggregatorNode

```python
class AggregatorNode(BaseNode[LoopState, ComplianceSearchDeps, ComplianceSearchResult]):
    async def run(self, ctx: ...) -> Union[ExpanderNode, End[ComplianceSearchResult]]:
        state = ctx.state

        aggregator = create_aggregator_agent()
        user_message = build_aggregator_user_message(
            state.focus_instruction, state.user_context, state.all_search_results,
        )

        result = await aggregator.run(user_message, usage_limits=AGGREGATOR_LIMITS)
        output: AggregatorOutput = result.output
        state.aggregator_output = output

        # Route
        if not output.sufficient and state.round_count < MAX_ROUNDS:
            state.weak_axes = output.weak_axes
            state.sse_events.append({
                "type": "status",
                "text": f"النتائج غير كافية -- جاري إعادة البحث (الجولة {state.round_count + 1})...",
            })
            return ExpanderNode()

        # Sufficient or max rounds -- build final result
        return End(ComplianceSearchResult(
            quality=output.quality,
            summary_md=output.synthesis_md,
            citations=output.citations,
            domain="compliance",
            queries_used=state.queries_used,
            rounds_used=state.round_count,
        ))
```

**Error handling**: On aggregator failure, return `End(ComplianceSearchResult(quality="weak", ...))` with whatever partial results exist.

## SSE Events

Events are collected in `LoopState.sse_events` during the loop, then transferred to `deps._events` after `run_compliance_search()` completes.

Expected event types:
- `{"type": "status", "text": "جاري البحث في الخدمات الحكومية: ..."}` -- search progress
- `{"type": "status", "text": "جاري تنفيذ N استعلامات بحث..."}` -- query execution
- `{"type": "status", "text": "تم استرجاع N خدمة حكومية."}` -- results received
- `{"type": "status", "text": "جاري تقييم جودة النتائج..."}` -- aggregation in progress
- `{"type": "status", "text": "النتائج غير كافية -- جاري إعادة البحث..."}` -- retry

## File Structure

```
agents/compliance_search/
    __init__.py              # Exports: run_compliance_search, ComplianceSearchDeps, ComplianceSearchResult
    models.py                # ComplianceSearchResult, ExpanderOutput, AggregatorOutput,
                             #   WeakAxis, Citation, LoopState, SearchResult
    prompts.py               # EXPANDER_SYSTEM_PROMPT, AGGREGATOR_SYSTEM_PROMPT,
                             #   build_expander_dynamic_instructions(),
                             #   build_aggregator_user_message()
    search_pipeline.py       # search_compliance(), _hybrid_rpc_search(), _rerank(),
                             #   _score_fallback(), _format_service_result(),
                             #   _collect_service_references()
    expander.py              # create_expander_agent(), EXPANDER_LIMITS
    aggregator.py            # create_aggregator_agent(), AGGREGATOR_LIMITS
    loop.py                  # ExpanderNode, SearchNode, AggregatorNode,
                             #   compliance_search_graph, run_compliance_search()
    logger.py                # save_run_log() -- JSON run log writer
    cli.py                   # Standalone CLI for testing
    planning/
        INITIAL.md           # This file
    tests/
        __init__.py
        conftest.py          # Fixtures: mock_deps, mock_embedding_fn, etc.
        test_expander.py     # QueryExpander structured output tests
        test_aggregator.py   # Aggregator quality/routing tests
        test_loop.py         # End-to-end loop tests with mock LLMs
        test_search_pipeline.py  # Search pipeline unit tests
```

## Entry Point

```python
# agents/compliance_search/loop.py

async def run_compliance_search(
    focus_instruction: str,
    user_context: str,
    deps: ComplianceSearchDeps,
) -> ComplianceSearchResult:
    """Run the compliance search loop.

    Creates fresh LoopState, runs the pydantic_graph from ExpanderNode,
    transfers SSE events to deps._events, returns ComplianceSearchResult.

    Args:
        focus_instruction: Arabic -- what to search for (from PlanAgent).
        user_context: Arabic -- user's personal situation/question.
        deps: ComplianceSearchDeps with supabase, embedding_fn, etc.

    Returns:
        ComplianceSearchResult with quality, summary_md, citations, etc.
    """
```

## CLI for Testing

```bash
python -m agents.compliance_search.cli "كيف أنقل كفالة عامل عبر منصة قوى" --verbose
python -m agents.compliance_search.cli "إصدار رخصة عمل لعامل منزلي" --rerank
```

The CLI should:
1. Load settings and create Supabase client
2. Create `ComplianceSearchDeps`
3. Call `run_compliance_search()`
4. Print the result (quality, summary_md, citations, rounds_used)
5. With `--verbose`: also print SSE events, search log, queries used, timing
6. With `--rerank`: enable Jina reranker
7. With `--mock`: use hardcoded mock results for offline testing

Pattern: Follow `agents/deep_search_v2/cli.py` or `agents/deep_search_v3/cli.py`.

## agent_models.py Updates Required

Add two new entries to `agents/utils/agent_models.py`:

```python
# Compliance Search -- domain-specific search loop
"compliance_search_expander":   "or-deepseek-v3.2",
"compliance_search_aggregator": "or-qwen3.5-397b",
```

## Reference Implementations

| Reference | Location | What to Learn |
|-----------|----------|---------------|
| V2 inner loop | `agents/deep_search_v2/loop/` | pydantic_graph pattern: nodes.py, graph.py, expander.py, aggregator.py |
| V2 INITIAL.md | `agents/deep_search_v2/planning/INITIAL.md` | Full requirements doc pattern |
| V3 compliance executor | `agents/deep_search_v3/executors/compliance.py` | Current compliance search tool |
| V3 search pipeline | `agents/deep_search_v3/executors/search_pipeline.py` | `search_compliance_pipeline`, `_format_service_result`, `_collect_service_references`, `_hybrid_rpc_search`, `_rerank`, `_score_fallback` |
| V3 compliance prompt | `agents/deep_search_v3/prompts.py` | `COMPLIANCE_EXECUTOR_PROMPT`, `_build_compliance_expansion_guidance()` |
| V3 models | `agents/deep_search_v3/models.py` | `Citation`, `ExecutorResult`, `ExecutorDeps` schemas |

## Success Criteria

- [ ] `run_compliance_search()` accepts focus_instruction + user_context + deps and returns `ComplianceSearchResult`
- [ ] QueryExpander produces 2-4 Arabic search queries targeting government services
- [ ] Search pipeline correctly calls `hybrid_search_services` RPC and returns formatted markdown
- [ ] Aggregator evaluates quality (strong/moderate/weak) and identifies weak axes
- [ ] Loop correctly retries on weak results (up to MAX_ROUNDS=3 total rounds)
- [ ] Weak axes from Aggregator are injected as dynamic instructions into QueryExpander on retry
- [ ] `ComplianceSearchResult` is compatible with deep_search_v3's `ExecutorResult` schema
- [ ] Citation schema matches deep_search_v3's `Citation` model exactly
- [ ] SSE events are collected and transferred to deps._events
- [ ] Error handling returns graceful fallback `ComplianceSearchResult(quality="weak", ...)` on any failure
- [ ] CLI works for standalone testing with `--verbose`, `--rerank`, and `--mock` flags
- [ ] All tests pass with `TestModel` and `FunctionModel` (no real LLM calls in tests)

## Testing Requirements

### test_expander.py
- Verify ExpanderOutput has 2-4 queries (strings)
- Verify rationales match query count
- Verify dynamic instruction injection for round 2+ (weak_axes)
- Use `FunctionModel` to return deterministic ExpanderOutput

### test_aggregator.py
- Verify sufficient=True produces quality + synthesis_md + citations
- Verify sufficient=False produces weak_axes with suggested_query
- Verify quality classification (strong/moderate/weak)
- Use `FunctionModel` to return deterministic AggregatorOutput

### test_loop.py
- End-to-end loop with mock LLMs: single round (sufficient on first try)
- End-to-end loop with retry: insufficient first round, sufficient second round
- Max rounds reached: verify loop exits after 3 rounds even if insufficient
- Error recovery: expander failure, aggregator failure
- Verify SSE events collected correctly
- Verify queries_used accumulates across rounds

### test_search_pipeline.py
- Mock Supabase RPC: verify `hybrid_search_services` is called with correct params
- Mock results: verify formatting via `_format_service_result`
- Mock reranker: verify Jina API call and fallback behavior
- Empty results: verify graceful handling

### conftest.py Fixtures
- `mock_supabase`: Mock Supabase client with `rpc().execute()` chain
- `mock_embedding_fn`: Returns fixed 1536-dim vector
- `mock_deps`: Pre-configured `ComplianceSearchDeps` with mocks
- `mock_http_client`: Mock httpx.AsyncClient for Jina API

Use `pydantic_ai.models.test.TestModel` and `pydantic_ai.models.function.FunctionModel` for deterministic testing. No real LLM or database calls in tests.

## Assumptions Made

1. **Single-domain queries**: Unlike deep_search_v2 which dispatches to regulations/cases/compliance, this package only searches compliance/services. Queries are plain Arabic strings, not `SearchQuery(tool, query, rationale)`.
2. **No ReportNode**: Unlike deep_search_v2 which has a ReportNode for DB artifact insertion, this package returns `ComplianceSearchResult` to the caller (PlanAgent). Artifact creation/update is the caller's responsibility.
3. **No tool calls in agents**: Both QueryExpander and Aggregator are pure structured output agents (`Agent[None, ...]`). All search infrastructure access is in the programmatic SearchNode.
4. **Search pipeline is self-contained**: This package contains its own copy of the compliance search pipeline functions, adapted from `agents/deep_search_v3/executors/search_pipeline.py`. This avoids cross-package imports and allows independent evolution.
5. **Deps shape mirrors ExecutorDeps**: `ComplianceSearchDeps` has the same fields as deep_search_v3's `ExecutorDeps` (supabase, embedding_fn, jina_api_key, http_client, use_reranker, mock_results, _events, _search_log) without user_id/conversation_id (not needed for search).
6. **Simplest of 3 domains**: Compliance returns only 3 results per query (SERVICES_TOP_N=3). Services are flat (no unfolding chain like regulations). This makes it the simplest domain-specific loop.
7. **OpenRouter model settings**: Expander may use `OpenRouterModelSettings` with reasoning effort configuration, following the deep_search_v2 pattern. Aggregator similarly.
8. **Arabic-first everything**: All prompts open in Arabic. All user-facing text (SSE events, error messages, synthesis) is in Arabic.

---

Generated: 2026-04-04
Note: This is one of 3 domain-specific search packages being extracted from deep_search_v3's monolithic executors. The other two (reg_search, case_search) will follow the same pattern with domain-specific prompts and search pipelines.
