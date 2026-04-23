# RerankerNode Implementation Plan — reg_search Pipeline

## Context

The reg_search pipeline currently flows: **Expander → Search → Aggregator**. The aggregator receives raw search results (up to 30 per query, including noise) and must waste tokens processing irrelevant results while lacking depth on promising ones.

Adding a **RerankerNode** between Search and Aggregator solves this by:
- **Filtering noise** — drops unrelated results before they reach the expensive synthesizer
- **Enriching depth** — progressively unfolds promising results using DB tools
- **Normalizing to articles** — the aggregator always receives article-level structured data

New flow: **Expander → Search → Reranker (per query) → Aggregator**

---

## Files to Create (2)

### `reranker.py` — Agent factory + tool
- `create_reranker_agent(prompt_key, model_override)` → `Agent[RegSearchDeps, RerankerOutput]`
- Single tool `unfold` with 3 modes via `Literal["article_precise", "section_detailed", "regulation_detailed"]`
- `RERANKER_LIMITS = UsageLimits(response_tokens_limit=8_000, request_limit=8)`
- Tool formatting helpers: `_format_article_precise_result()`, `_format_section_detailed_result()`, `_format_regulation_detailed_result()`
- For `section_detailed` and `regulation_detailed`: pre-fetch row from DB first (existing `unfold_section`/`unfold_regulation` need full row dicts, not just `{"id": ...}`)

### `reranker_prompts.py` — System prompt
- Arabic prompt instructing: classify each result (sufficient/unrelated/requires_unfolding), progressive unfolding (max 3 tool calls), 80% collective sufficiency rule, article-centric output
- `RERANKER_PROMPTS` dict + `get_reranker_prompt()` function

---

## Files to Modify (7)

### `models.py` — New Pydantic models + LoopState update

**Add models:**
```python
class RerankedArticle(BaseModel):
    article_id: str           # DB id
    title: str                # Arabic title
    article_num: str | None
    content: str
    article_context: str
    references_content: str
    regulation_title: str
    section_title: str
    section_summary: str
    relevance: Literal["high", "medium"]
    reasoning: str            # Arabic — why it's relevant

class RerankerOutput(BaseModel):  # LLM structured output
    sufficient: bool          # >=80% collectively sufficient?
    articles: list[RerankedArticle]
    dropped_count: int
    summary_note: str         # Arabic sufficiency note

@dataclass
class RerankerQueryResult:    # programmatic container (not LLM output)
    query: str
    rationale: str
    sufficient: bool
    articles: list[RerankedArticle]
    dropped_count: int
    summary_note: str
    tool_calls_count: int = 0
```

**LoopState additions:**
- `reranker_results: list[RerankerQueryResult] = field(default_factory=list)`
- `skip_reranker: bool = False`

### `regulation_unfold.py` — New sibling unfold function

**Add `unfold_article_with_siblings(supabase, article_id)`:**
1. Fetch target article (content + context + references + section_id)
2. Fetch parent section (summary + context)
3. Fetch regulation title
4. Fetch ALL sibling articles in same section (content + references), excluding target
5. Cap siblings: `MAX_SIBLING_CONTENT_CHARS = 2_000` per sibling, `MAX_SIBLINGS_TOTAL_CHARS = 8_000` cumulative

**Add IDs to markdown format functions** (critical for reranker tool calling):
- `_format_article_precise()` line 714: add `[id:{id}]` to header
- `_format_section_precise()` line 749: add `[id:{id}]` to header
- `_format_regulation_precise()` line 778: add `[id:{id}]` to header
- Same for detailed variants: `_format_article()`, `_format_section()`, `_format_regulation()`

### `loop.py` — New RerankerNode + graph rewiring

**Add `RerankerNode(BaseNode[LoopState, RegSearchDeps, RegSearchResult])`:**
- If `state.skip_reranker`: pass through directly to AggregatorNode
- Iterates over current round's search results (from `state.search_results_log`)
- Per query: builds user message (sub-query + rationale + raw_markdown), runs reranker agent with deps
- Accumulates `RerankerQueryResult` in `state.reranker_results`
- Captures usage, logs per-query markdown, handles errors gracefully (fallback: mark as insufficient)
- Returns AggregatorNode

**Graph changes:**
- `SearchNode.run()` returns `RerankerNode()` instead of `AggregatorNode()`
- `reg_search_graph = Graph(nodes=[ExpanderNode, SearchNode, RerankerNode, AggregatorNode])`
- `AggregatorNode.run()`: if `state.reranker_results` exists, use `build_aggregator_user_message_reranked()` else use existing builder
- `run_reg_search()`: add `skip_reranker: bool = False` param, pass to LoopState

### `aggregator_prompts.py` — New reranked message builder

**Add `build_aggregator_user_message_reranked(focus_instruction, user_context, reranker_results)`:**
- Formats structured `RerankerQueryResult` list into aggregator user message
- Each query section: sub-query + rationale + sufficiency flag + dropped count
- Each article: title + regulation + section + relevance tag + reasoning + content + context + references
- Replaces raw markdown with clean structured data

### `logger.py` — Reranker logging

**Add `save_reranker_md(log_id, round_num, query_index, query, rationale, output, usage, messages_json, tool_calls_count)`:**
- Saves to `logs/{log_id}/reranker/round_N_qM_{slug}.md`
- Content: query, rationale, sufficient flag, articles kept, dropped, tool calls, usage, articles detail, raw messages JSON

### `cli.py` — Skip-reranker flag

- Add `--skip-reranker` argument (action="store_true")
- Pass to `run_reg_search(skip_reranker=args.skip_reranker)`
- Print reranker status in terminal output

### `agents/utils/agent_models.py` — Model registration

Add: `"reg_search_reranker": "qwen3.5-flash"` after `reg_search_aggregator`

---

## Implementation Sequence

### Phase 1 — Data layer (no behavioral change)
1. `models.py`: Add RerankedArticle, RerankerOutput, RerankerQueryResult, update LoopState
2. `regulation_unfold.py`: Add `unfold_article_with_siblings()` + add IDs to format functions
3. `agent_models.py`: Register reg_search_reranker

### Phase 2 — Reranker agent (standalone)
4. `reranker_prompts.py`: Create with Arabic system prompt
5. `reranker.py`: Create agent factory + register unfold tool

### Phase 3 — Pipeline wiring
6. `loop.py`: Add RerankerNode, update SearchNode routing, update AggregatorNode, update run_reg_search()
7. `aggregator_prompts.py`: Add build_aggregator_user_message_reranked()
8. `logger.py`: Add save_reranker_md()
9. `cli.py`: Add --skip-reranker flag

---

## Key Design Decisions

1. **One tool, three modes** — single `unfold` tool with `mode: Literal[...]` parameter. Cleaner than 3 separate tools for Qwen 3.5 Flash.

2. **IDs in markdown** — format functions must include `[id:{uuid}]` so the reranker LLM can extract IDs for tool calls. Currently missing from all format functions.

3. **Pre-fetch rows for unfold_section/unfold_regulation** — existing functions expect full row dicts from RPC results. The reranker tool must fetch the row from DB first, then pass it to the unfold function.

4. **Sequential per-query processing** — reranker runs one query at a time (not parallel) to avoid Alibaba API rate limits and keep tool-call context clean.

5. **Accumulation across rounds** — `state.reranker_results` accumulates across retry rounds. Aggregator sees ALL reranked results from ALL rounds.

6. **Graceful degradation** — on reranker error, fall back to marking that query as insufficient. On `--skip-reranker`, pipeline works identically to current behavior.

7. **Tool calls capped at 3** — progressive hierarchy: regulation → section → article. 80% rule stops early.

---

## Architecture Diagram

```
                    ┌──────────────────────────────────────┐
                    │           ExpanderNode               │
                    │  LLM: or-gemma-4-31b                 │
                    │  Output: 2-10 Arabic sub-queries     │
                    └───────────────┬──────────────────────┘
                                    │
                    ┌───────────────▼──────────────────────┐
                    │            SearchNode                 │
                    │  No LLM — programmatic                │
                    │  Embed → 3 parallel RPCs → unfold     │
                    │  Output: raw markdown per query       │
                    └───────────────┬──────────────────────┘
                                    │
               ┌────────────────────▼─────────────────────┐
               │          RerankerNode (NEW)               │
               │  LLM: qwen3.5-flash (per query)          │
               │  Tool: unfold(target_id, mode)            │
               │    ├ article_precise (+ siblings)         │
               │    ├ section_detailed (all child articles)│
               │    └ regulation_detailed (child sections) │
               │  Output: RerankerQueryResult[]            │
               │    - Sufficient articles (high/medium)    │
               │    - Dropped count                        │
               │    - Collective sufficiency flag           │
               └────────────────────┬─────────────────────┘
                                    │
                    ┌───────────────▼──────────────────────┐
                    │          AggregatorNode               │
                    │  LLM: or-gemma-4-31b                 │
                    │  Input: structured articles (not md)  │
                    │  Output: synthesis + citations        │
                    │  If weak → loop back to Expander      │
                    └──────────────────────────────────────┘
```

---

## Verification Plan

1. **Regression**: `python -m agents.deep_search_v3.reg_search.cli --query-id 9 --skip-reranker` → identical to current pipeline
2. **Reranker on**: `python -m agents.deep_search_v3.reg_search.cli --query-id 9` → verify:
   - `logs/query_9/{ts}/reranker/` contains per-query markdown files
   - Reranker drops unrelated results, keeps relevant ones with high/medium tags
   - Tool calls appear in messages JSON (progressive unfolding)
   - Aggregator receives structured data (not raw markdown)
3. **Edge cases**: query with 0 results, all-weak results (sufficient=False passthrough)
