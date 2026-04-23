# Regulation Executor -- Runner Specification

## Overview

The regulation executor is a single-turn agent that runs via `agent.run()` (not `iter()`). It has no multi-turn conversation, no SSE events, no task state. The runner is a simple async function that the planner's `search_regulations` tool calls, and it returns a string (formatted markdown) back to the planner.

This document specifies the runner function, output model, integration with the planner, and error handling.

---

## Module Layout

```
agents/deep_search/executors/
    __init__.py                  # re-exports run_regulation_search, RegulationSearchDeps
    regulation_executor.py       # Agent definition, tools, output model, runner
```

Public API:

```python
# agents/deep_search/executors/__init__.py
from agents.deep_search.executors.regulation_executor import (
    run_regulation_search,
    RegulationSearchDeps,
    RegulationSearchResult,
    regulation_executor,
)
```

---

## Run Method

| Setting | Value | Justification |
|---------|-------|---------------|
| Method | `agent.run()` | Single-turn, no streaming, no user interaction. Simplest run method. |
| UsageLimits | `UsageLimits(response_tokens_limit=4000, request_limit=5, tool_calls_limit=8)` | Executor should complete in 1-3 model requests with 4-6 tool calls. Safety net for edge cases. |

---

## Output Model

```python
class RegulationSearchResult(BaseModel):
    """Structured output from the regulation executor agent."""

    quality: Literal["strong", "moderate", "weak"] = Field(
        description=(
            "Self-assessed quality of search results. "
            "'strong': top reranker score > 0.7 and >= 3 good results. "
            "'moderate': top score > 0.4 or >= 2 results. "
            "'weak': top score < 0.4 and < 2 results."
        ),
    )
    result_count: int = Field(
        description="Number of relevant results found after reranking.",
    )
    results_md: str = Field(
        description=(
            "Formatted Arabic markdown summary of findings. "
            "Grouped by parent regulation, with article titles, numbers, "
            "and content excerpts. The planner uses this for synthesis."
        ),
    )
    citations: list[dict] = Field(
        default_factory=list,
        description=(
            "Structured citation list. Each dict has: "
            "source_type ('regulation'|'article'|'section'), "
            "ref (chunk_ref or regulation_ref), "
            "title (Arabic title), "
            "content_snippet (relevant excerpt, max 200 chars), "
            "regulation_title (parent regulation name), "
            "article_num (if applicable), "
            "relevance (why this source matters)."
        ),
    )
    top_score: float = Field(
        default=0.0,
        description="Highest reranker relevance score among results (0.0-1.0).",
    )
```

### Why Structured Output (not just str)

The planner needs machine-readable quality signals to decide whether to re-search. Returning a plain markdown string forces the planner to parse quality from text (fragile, wastes tokens). With structured output:

- `quality` lets the planner branch on strong/moderate/weak without text parsing
- `result_count` helps the planner decide if more results are needed
- `top_score` provides a numeric threshold for re-search decisions
- `citations` are passed directly to the planner's `create_report` tool without re-extraction
- `results_md` is the human-readable summary that goes into the report

---

## Runner Function

### `run_regulation_search()`

```python
async def run_regulation_search(
    query: str,
    deps: RegulationSearchDeps,
) -> str:
    """Run the regulation executor and return formatted results as a string.

    Called by the planner's search_regulations tool. Returns a markdown
    string that includes quality assessment and formatted results.

    Args:
        query: Arabic search query from the planner.
        deps: RegulationSearchDeps with supabase, embedding_fn, jina key, http client.

    Returns:
        Formatted markdown string with quality header, results, and citations.
        On error, returns an Arabic error message string.
    """
```

**Why return `str` and not `RegulationSearchResult`?**

The planner's `search_regulations` tool must return a `str` (tools return strings to the model). The runner converts the structured `RegulationSearchResult` to a formatted markdown string that includes the quality signal, results, and citation refs. This gives the planner both:
- The quality signal in the first line (parseable by the model: `**quality: strong**`)
- The formatted content for report synthesis
- Citation refs for cumulative tracking

### Implementation Pseudocode

```python
import asyncio
import json
import logging
import time

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model

logger = logging.getLogger(__name__)

EXECUTOR_LIMITS = UsageLimits(
    response_tokens_limit=4_000,
    request_limit=5,
    tool_calls_limit=8,
)

EXECUTOR_TIMEOUT = 25  # seconds

ERROR_MSG_AR = "خطأ في البحث في الأنظمة. لم يتم العثور على نتائج."


async def run_regulation_search(
    query: str,
    deps: RegulationSearchDeps,
) -> str:
    # Reset mutable state for this run
    deps._candidate_cache = {}
    deps._reranked_results = []

    try:
        result = await asyncio.wait_for(
            regulation_executor.run(
                query,
                deps=deps,
                usage_limits=EXECUTOR_LIMITS,
            ),
            timeout=EXECUTOR_TIMEOUT,
        )

        output: RegulationSearchResult = result.output

        # Log usage
        usage = result.usage()
        logger.info(
            "Regulation executor — query=%s, quality=%s, results=%d, "
            "top_score=%.2f, requests=%s, tokens=%s",
            query[:60], output.quality, output.result_count,
            output.top_score, usage.requests, usage.total_tokens,
        )

        # Format structured result as markdown string for the planner
        return _format_result_for_planner(output)

    except asyncio.TimeoutError:
        logger.warning("Regulation executor timed out for query: %s", query[:80])
        return f"خطأ: انتهت مهلة البحث في الأنظمة ({EXECUTOR_TIMEOUT}s). جرب صياغة مختلفة."

    except Exception as e:
        logger.error("Regulation executor error: %s", e, exc_info=True)
        return ERROR_MSG_AR
```

### Result Formatting

```python
def _format_result_for_planner(result: RegulationSearchResult) -> str:
    """Convert structured RegulationSearchResult to markdown string for the planner."""
    quality_ar = {
        "strong": "قوية",
        "moderate": "متوسطة",
        "weak": "ضعيفة",
    }

    lines = [
        f"## نتائج البحث في الأنظمة",
        f"**الجودة: {quality_ar.get(result.quality, result.quality)}** "
        f"({result.result_count} نتائج، أعلى درجة: {result.top_score:.2f})",
        "",
        result.results_md,
    ]

    if result.citations:
        lines.append("")
        lines.append("**مصادر**: " + ", ".join(
            f"{c.get('ref', 'unknown')}" for c in result.citations
        ))

    return "\n".join(lines)
```

---

## History Formatting

Not applicable. The regulation executor is single-turn -- it receives one query and returns one result. There is no conversation history.

---

## Error Handling

### Error Strategy

The executor's errors are contained within the planner's `search_regulations` tool. The tool returns an error string to the planner, which can then:
- Re-search with a different query
- Try a different executor (cases, compliance)
- Report partial results

The executor NEVER raises exceptions to the planner. All errors are caught and returned as Arabic error strings.

### Specific Error Cases

| Error | Source | Handling |
|-------|--------|----------|
| `asyncio.TimeoutError` | `wait_for()` wrapper | Return timeout message with query hint |
| `UsageLimitExceeded` | Pydantic AI | Caught in outer `except`. Return generic error. |
| `ValidationError` | `RegulationSearchResult` output fails | Pydantic AI retries once (retries=1). If still fails, caught in outer `except`. |
| Embedding API failure | `embed_regulation_query()` | `embed_and_search` tool raises `ModelRetry`. Agent retries the tool call once. |
| Jina API failure | `rerank_results` tool | Tool falls back to similarity-only ranking (no raise). |
| DB query failure | Any tool | Tool returns empty results. Agent proceeds with partial data. |
| All tools return empty | No results anywhere | Agent returns `RegulationSearchResult(quality="weak", result_count=0, results_md="...", citations=[])` |

### Graceful Degradation Ladder

The executor degrades gracefully at each stage:

```
Full pipeline:   embed → vector_search → rerank → unfold → structured_result
                     ↓ (embedding fails)
Retry once:      embed → vector_search → rerank → unfold → structured_result
                     ↓ (embedding still fails)
Text fallback:   text_search → rerank → unfold → structured_result
                     ↓ (Jina fails)
No reranking:    text_search → similarity_sort → unfold → structured_result
                     ↓ (unfold fails)
Minimal:         text_search → similarity_sort → preview_only → structured_result
                     ↓ (everything fails)
Empty:           RegulationSearchResult(quality="weak", result_count=0, ...)
```

---

## SSE Events

None. The regulation executor does not emit SSE events. It runs silently inside the planner's `search_regulations` tool. The planner emits its own status event after the executor returns.

---

## Integration with Planner

### Call Site

The regulation executor is called from the planner's `search_regulations` tool in `agents/deep_search/agent.py`. The current mock implementation:

```python
# CURRENT (mock):
@planner_agent.tool(retries=1)
async def search_regulations(ctx: RunContext[SearchDeps], query: str) -> str:
    logger.info("search_regulations called with query: %s", query)
    ctx.deps._sse_events.append({
        "type": "status",
        "text": f"جاري البحث في الأنظمة واللوائح: {query[:80]}...",
    })
    return MOCK_REGULATION_RESULT
```

Will be replaced with:

```python
# NEW (real executor):
@planner_agent.tool(retries=1, timeout=30)
async def search_regulations(ctx: RunContext[SearchDeps], query: str) -> str:
    """Search Saudi statutory and regulatory law via the regulation executor agent.

    Args:
        query: Arabic search query targeting regulations and statutory provisions.
    """
    logger.info("search_regulations called with query: %s", query)
    ctx.deps._sse_events.append({
        "type": "status",
        "text": f"جاري البحث في الأنظمة واللوائح: {query[:80]}...",
    })

    from agents.deep_search.executors.regulation_executor import (
        run_regulation_search,
        RegulationSearchDeps,
    )
    from agents.utils.embeddings import embed_regulation_query
    from shared.config import get_settings

    settings = get_settings()
    reg_deps = RegulationSearchDeps(
        supabase=ctx.deps.supabase,
        embedding_fn=embed_regulation_query,
        jina_api_key=settings.JINA_RERANKER_API_KEY or "",
        http_client=_get_jina_client(),
    )

    return await run_regulation_search(query, reg_deps)
```

### Data Flow Diagram

```
User message
    |
    v
Orchestrator → handle_deep_search_turn()
    |
    v
Planner agent (iter loop)
    |
    v  (tool call)
search_regulations(query)
    |
    +-- Build RegulationSearchDeps from SearchDeps + config
    |
    +-- run_regulation_search(query, reg_deps)
    |       |
    |       v
    |   regulation_executor.run(query, deps=reg_deps, usage_limits=...)
    |       |
    |       +-- [embed_and_search]    → vector search 3 tables
    |       +-- [text_search_fallback] → ILIKE fallback (optional)
    |       +-- [rerank_results]       → Jina cross-encoder reranking
    |       +-- [unfold_context]       → full text + metadata + siblings
    |       |
    |       v
    |   RegulationSearchResult (structured output)
    |       |
    |       v
    |   _format_result_for_planner() → markdown string
    |
    v  (tool return)
Planner receives markdown string, evaluates quality, decides next step
```

---

## Module Dependencies

### Imports for regulation_executor.py

```python
# Standard library
from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Literal

# Third-party
import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.usage import UsageLimits
from supabase import Client as SupabaseClient

# Luna internal
from agents.utils.agent_models import get_agent_model
```

---

## Constants

```python
EXECUTOR_LIMITS = UsageLimits(
    response_tokens_limit=4_000,
    request_limit=5,
    tool_calls_limit=8,
)

EXECUTOR_TIMEOUT = 25  # seconds for asyncio.wait_for

ERROR_MSG_AR = "خطأ في البحث في الأنظمة. لم يتم العثور على نتائج."

# Truncation limits
MAX_CONTENT_CHARS = 3_000        # Per article/section content
MAX_CONTEXT_CHARS = 500          # Per article_context/section_summary
MAX_SIBLINGS_CHARS = 1_500       # Total for sibling/child articles
MAX_REG_METADATA_CHARS = 300     # Regulation metadata fields
MAX_TOTAL_CHARS = 40_000         # Total budget across all results
MAX_RESULTS = 10                 # Max results after reranking

# Jina Reranker
JINA_RERANKER_URL = "https://api.jina.ai/v1/rerank"
JINA_RERANKER_MODEL = "jina-reranker-v2-base-multilingual"
JINA_TOP_N = 10
```

---

## Agent Definition

```python
regulation_executor = Agent(
    get_agent_model("search_regulations"),
    output_type=RegulationSearchResult,
    deps_type=RegulationSearchDeps,
    instructions=SYSTEM_PROMPT,
    retries=1,
    end_strategy="early",
)
```

The agent uses `get_agent_model("search_regulations")` which resolves to `gemini-3-flash` (mapped to `gemini-3-flash-preview` via the model registry). The `"search_regulations"` key already exists in `agents/utils/agent_models.py` -- no changes needed.

---

## Checklist

- [x] Runner function is a simple async function returning `str` (not a generator, not a tuple)
- [x] Output model `RegulationSearchResult` provides quality, results_md, citations, top_score
- [x] The planner receives a formatted markdown string (not a raw Pydantic model)
- [x] Separate `RegulationSearchDeps` -- does not modify `SearchDeps`
- [x] `embed_regulation_query` uses 768-dim Gemini embeddings matching DB vectors
- [x] Jina reranker with graceful fallback to similarity-only on API failure
- [x] Truncation strategy: per-result caps + total budget
- [x] Timeout: 25s executor + 30s tool wrapper
- [x] All errors caught and returned as Arabic strings -- never raises to planner
- [x] No SSE events, no user interaction, no artifact management
- [x] Agent uses `get_agent_model("search_regulations")` -- resolves to `gemini-3-flash`. No new entry in `agent_models.py` needed.
