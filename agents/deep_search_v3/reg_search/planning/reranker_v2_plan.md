# Reranker v2 — Two-Phase Refactor Plan

## Context

The v1 reranker (currently implemented) uses a **tool-calling agent** pattern. Testing on 16 queries revealed two problems:

1. **Growing context window** — The LLM receives 30 results (~25K chars), then each tool call adds ~15-20K chars to the conversation. By the 3rd tool call, input is 95-130K tokens. The dropped/unrelated results stay in context forever.

2. **LLM copies content into output** — `RerankedArticle` forces the LLM to reproduce article content, context, references in its structured output. Wasteful output tokens for data the system already has.

3. **Output too rigid** — Only articles allowed. User wants up to 2 sections in output too.

### Solution: Two-Phase Pure Structured Output

Replace the tool-calling agent with a **classification-only agent** (no tools). The RerankerNode handles unfolding programmatically in a loop of up to 3 runs:

```
Run 1: LLM classifies 30 results → keep(position) / drop / unfold(position, mode)
         ↓ code drops unrelated, unfolds requested items programmatically
Run 2: LLM classifies kept + newly unfolded → keep / drop / unfold deeper
         ↓ code unfolds again if needed
Run 3: LLM final classification → structured decisions only
         ↓ code assembles final results from search data + unfold data
```

Each run sees **only relevant results** — dropped items are stripped. No growing context.

---

## Files to Modify (5)

### 1. `models.py` — Replace reranker models

**Remove:** `RerankedArticle`, `RerankerOutput`

**Add:**
```python
class RerankerDecision(BaseModel):
    """LLM's decision about one search result."""
    position: int                    # 1-based position in the input results
    action: Literal["keep", "drop", "unfold"]
    unfold_mode: Literal["article_precise", "section_detailed", "regulation_detailed"] | None
    relevance: Literal["high", "medium"] | None   # only for "keep"
    reasoning: str                   # short Arabic note

class RerankerClassification(BaseModel):
    """Output of each reranker LLM run (classification only, no content)."""
    sufficient: bool                 # 80% rule — collectively enough?
    decisions: list[RerankerDecision]
    summary_note: str                # Arabic sufficiency note

class RerankedResult(BaseModel):
    """A single result kept by the reranker (assembled by code, not LLM)."""
    source_type: Literal["article", "section"]
    title: str
    content: str                     # article content or section summary
    article_num: str | None = None
    article_context: str = ""
    references_content: str = ""
    regulation_title: str = ""
    section_title: str = ""
    section_summary: str = ""
    relevance: Literal["high", "medium"]
    reasoning: str
    # NO article_id — aggregator doesn't need it
```

**Update `RerankerQueryResult`:**
```python
@dataclass
class RerankerQueryResult:
    query: str
    rationale: str
    sufficient: bool
    results: list  # list[RerankedResult] — articles + up to 2 sections
    dropped_count: int
    summary_note: str
    unfold_rounds: int = 0       # how many LLM runs were needed (1-3)
    total_unfolds: int = 0       # how many DB unfold calls were made
```

**Update `RerankerOutput` (rename):** The LLM output type becomes `RerankerClassification`.

### 2. `reranker.py` — Remove tools, add multi-run loop

**Complete rewrite.** The agent becomes tool-free:

```python
def create_reranker_agent(prompt_key, model_override) -> Agent[None, RerankerClassification]:
    """Pure structured-output agent. No tools, no deps."""
    # No deps_type — unfold is handled by RerankerNode
    # No tools registered
```

**New: `run_reranker_for_query()` — the multi-run loop:**
```python
async def run_reranker_for_query(
    query: str,
    rationale: str,
    raw_markdown: str,
    supabase: SupabaseClient,
    model_override: str | None = None,
) -> RerankerQueryResult:
    """Run up to 3 classification rounds with programmatic unfolding between.

    Round 1: classify raw search results
    Round 2: classify kept + unfolded (if any unfold requested)
    Round 3: classify deeper unfolds (if needed)

    Each round only sees relevant results — dropped items stripped.
    80% rule: stop early if sufficient.
    """
```

The loop logic:
1. Parse raw_markdown into individual result blocks (by `### [N]` headers)
2. Run 1: Send all results → get `RerankerClassification`
3. If `sufficient=True` or no unfold decisions → done
4. Strip dropped results, programmatically unfold requested items
5. Build new markdown from kept results + unfolded content (formatted)
6. Run 2: Send trimmed results → get new classification
7. Repeat once more if needed (max 3 runs)
8. Assemble final `RerankerQueryResult` from kept decisions + search/unfold data

**Keep:** `_fetch_section_row()`, `_fetch_regulation_row()`, formatting helpers — still needed for programmatic unfolding.

**Remove:** `_register_unfold_tool()`, tool registration.

### 3. `reranker_prompts.py` — Update prompt for classification-only

**Rewrite prompt_1** to reflect new behavior:
- Input: numbered results with `[id:UUID]` tags
- Output: per-result decisions (position, action, unfold_mode, relevance, reasoning)
- No tool instructions — the LLM just classifies
- Keep: 80% rule, architecture context, Arabic instructions
- Add: "up to 2 sections allowed in keep decisions" rule
- Add: clear instruction that `position` matches the `[N]` number in headers

**Update `build_reranker_user_message()`:** Same format, but add a round indicator:
```python
def build_reranker_user_message(query, rationale, results_markdown, round_num=1):
    # Round 1: "صنّف كل نتيجة"
    # Round 2+: "النتائج بعد التوسع — أعد التصنيف"
```

### 4. `loop.py` — Simplify RerankerNode

**Replace** the current per-query `reranker.run()` call with `run_reranker_for_query()`:

```python
# Old (tool-calling):
result = await reranker.run(user_msg, deps=deps, usage_limits=RERANKER_LIMITS)

# New (multi-run loop):
from .reranker import run_reranker_for_query
query_result = await run_reranker_for_query(
    query=query, rationale=rationale, raw_markdown=raw_markdown,
    supabase=deps.supabase, model_override=state.model_override,
)
state.reranker_results.append(query_result)
```

The RerankerNode becomes thinner — the complexity moves to `run_reranker_for_query()`.

**Update usage tracking:** Accumulate usage across all runs in the loop (run_reranker_for_query returns usage).

### 5. `aggregator_prompts.py` — Update field references

Replace `rr.articles` → `rr.results` and `art.article_id` references. The format stays the same — articles and sections are both rendered with title + content + context.

---

## Files NOT Changed

- `regulation_unfold.py` — `unfold_article_with_siblings()`, format functions, `[id:UUID]` tags all stay as-is. Used by the programmatic unfold step.
- `logger.py` — `save_reranker_md()` stays, but the output model changes from `RerankerOutput` to log the classification + assembled results.
- `cli.py` — `--skip-reranker`, `--reranker-only` flags stay as-is.
- `agent_models.py` — `"reg_search_reranker": "qwen3.5-flash"` stays.

---

## Key Algorithm: `run_reranker_for_query()`

```
Input: raw_markdown (30 results with [id:UUID] tags), query, rationale

# Parse results into blocks
result_blocks = parse_markdown_into_blocks(raw_markdown)
# result_blocks[i] = { position: i+1, markdown: "### [i+1] ...", id: "uuid", source_type: "article"|"section"|"regulation" }

active_blocks = result_blocks  # starts with all
all_kept = []                  # accumulates across rounds
total_dropped = 0
total_unfolds = 0

for round_num in range(1, 4):  # max 3 rounds
    # Build markdown from active blocks only
    trimmed_md = assemble_markdown(active_blocks)

    # LLM classification
    classification = await agent.run(
        build_reranker_user_message(query, rationale, trimmed_md, round_num)
    )

    # Process decisions
    new_kept = []
    to_unfold = []
    for decision in classification.decisions:
        block = active_blocks[decision.position - 1]
        if decision.action == "keep":
            new_kept.append((block, decision))
        elif decision.action == "unfold" and decision.unfold_mode:
            to_unfold.append((block, decision))
        else:  # drop
            total_dropped += 1

    all_kept.extend(new_kept)

    # 80% rule or nothing to unfold → done
    if classification.sufficient or not to_unfold:
        break

    # Programmatic unfold
    unfolded_blocks = []
    for block, decision in to_unfold:
        unfolded_md = await programmatic_unfold(
            supabase, block["id"], decision.unfold_mode
        )
        # Parse unfolded_md into new result blocks
        new_blocks = parse_markdown_into_blocks(unfolded_md)
        unfolded_blocks.extend(new_blocks)
        total_unfolds += 1

    # Next round sees only the unfolded blocks (kept are already saved)
    active_blocks = unfolded_blocks

# Assemble RerankedResult objects from all_kept
results = assemble_results(all_kept)  # extracts content from search data
```

---

## Implementation Sequence

1. **models.py** — Replace `RerankedArticle` + `RerankerOutput` with `RerankerDecision` + `RerankerClassification` + `RerankedResult`. Update `RerankerQueryResult`.
2. **reranker_prompts.py** — Rewrite prompt for classification-only. Update user message builder.
3. **reranker.py** — Remove tools. Add `run_reranker_for_query()` with multi-run loop + markdown parser + programmatic unfold + result assembler.
4. **loop.py** — Simplify RerankerNode to call `run_reranker_for_query()`.
5. **aggregator_prompts.py** — Update `articles` → `results` references.
6. **logger.py** — Update `save_reranker_md()` for new output types.

---

## Verification

1. `python -m agents.deep_search_v3.reg_search.cli --query-id 9 --reranker-only` — verify reranker logs show classification decisions, programmatic unfolds, assembled results
2. `python -m agents.deep_search_v3.reg_search.cli --query-id 5 --reranker-only` — verify edge case where most results are irrelevant
3. Compare token usage vs v1: should see significantly lower input tokens (no growing context)
4. `--skip-reranker` regression — identical to pre-reranker pipeline
