# Deep Search V3 - Migration Plan: Monolithic Executors to Domain Search Loops

## Summary

This is a **migration plan**, not a new agent plan. The deep_search_v3 PlanAgent (supervisor) already exists and works. We are replacing its monolithic executor system -- where a single LLM agent per domain handles query expansion, search, and synthesis all in one -- with 3 domain-specific search loop packages. Each domain package uses a `pydantic_graph` loop (QueryExpander -> SearchNode -> Aggregator) with retry-on-weak-results (max 2 retries, 3 total rounds).

The PlanAgent itself is NOT being replaced. Its dispatch logic, report building, and runner remain intact. Only the `invoke_executors` tool and supporting infrastructure change.

---

## 1. Current Architecture (What Exists Today)

### Overview

```
PlanAgent (supervisor, pydantic_ai Agent with tools)
    |
    invoke_executors(dispatches: list[ExecutorDispatch])
    |
    create_executor(domain, focus_instruction, user_context)   # executors/base.py
        -> Agent[ExecutorDeps, ExecutorResult]  (monolithic LLM agent)
        -> Registers domain-specific search tool (search_regulations / search_cases / search_compliance)
    |
    run_executor(agent, message, deps)  # executors/base.py
        -> agent.iter() with manual .next() loop
        -> Returns (ExecutorResult, model_messages_json)
    |
    Results collected -> build_aggregated_report() -> insert/update artifact -> return summary to PlanAgent
```

### Current File Inventory

| File | Purpose | Lines |
|------|---------|-------|
| `plan_agent.py` | PlanAgent definition + `invoke_executors` tool + `update_report` tool + `ask_user` tool | 408 |
| `models.py` | DeepSearchV3Deps, ExecutorDeps, ExecutorDispatch, ExecutorResult, AggregatedResults, Citation, SearchResult | 178 |
| `prompts.py` | PLAN_AGENT_SYSTEM_PROMPT + 3 executor prompts + dynamic instruction builders + formatters | 528 |
| `executors/__init__.py` | Exports create_executor, run_executor | 14 |
| `executors/base.py` | Executor factory (create_executor) + runner (run_executor) + domain prompt/model mapping | 164 |
| `executors/regulations.py` | register_search_regulations tool registration | 69 |
| `executors/cases.py` | register_search_cases tool registration | 68 |
| `executors/compliance.py` | register_search_compliance tool registration | 68 |
| `executors/search_pipeline.py` | 3 search pipelines + shared helpers (_hybrid_rpc_search, _rerank, _score_fallback, formatters) | 667 |
| `executors/regulation_unfold.py` | Regulation unfold logic (article, section, regulation) + reference collection + formatting | 651 |
| `report_builder.py` | Aggregated report builder (pure Python, no LLM) | 175 |
| `runner.py` | handle_deep_search_v3_turn entry point + build_search_deps + result mapping | 260 |
| `logger.py` | Run log writer (JSON + trace MD + search results MD) | 538 |
| `cli.py` | CLI test runner | 162 |
| `__init__.py` | Exports: handle_deep_search_v3_turn, build_search_deps, DeepSearchV3Deps | 15 |

### Current Data Flow

1. **runner.py**: `handle_deep_search_v3_turn(message, deps)` runs PlanAgent via `plan_agent.iter()`
2. **PlanAgent LLM**: Analyzes user question, decides executor dispatches, calls `invoke_executors` tool
3. **invoke_executors**: For each `ExecutorDispatch`:
   - `create_executor(domain, focus, user_ctx)` builds a monolithic Agent with domain prompt + dynamic instructions + search tool
   - `run_executor(agent, message, deps)` runs the agent (LLM handles everything: query expansion, tool calls, quality evaluation, synthesis)
4. **Executor LLM**: Calls `search_regulations`/`search_cases`/`search_compliance` tool 1-4 times, evaluates results, returns `ExecutorResult`
5. **invoke_executors**: Collects all ExecutorResults, calls `build_aggregated_report()`, inserts artifact, returns summary
6. **PlanAgent LLM**: Reads summary, writes conversational Arabic answer, returns `PlannerResult`

### Current Model Slots (agent_models.py)

```python
"deep_search_v3_plan_agent":           "or-gemini-3.1-pro-tools",  # PlanAgent supervisor
"deep_search_v3_regulations_executor": "or-minimax-m2.7",          # Regulations executor (WILL BE REMOVED)
"deep_search_v3_cases_executor":       "or-minimax-m2.7",          # Cases executor (WILL BE REMOVED)
"deep_search_v3_compliance_executor":  "or-minimax-m2.7",          # Compliance executor (WILL BE REMOVED)
```

### Current Models Used

| Model | Purpose | What Changes |
|-------|---------|-------------|
| `DeepSearchV3Deps` | Top-level deps passed to PlanAgent tools | KEEP -- unchanged |
| `ExecutorDeps` | Lean deps for executor agents | REMOVE -- replaced by domain-specific deps |
| `ExecutorDispatch` | Instructions from PlanAgent to an executor | KEEP -- PlanAgent still emits these |
| `ExecutorResult` | Output from a single executor | KEEP -- domain packages return compatible schemas |
| `AggregatedResults` | Combined results (internal) | KEEP -- used by report_builder |
| `Citation` | Structured citation | KEEP -- shared across all domain packages |
| `SearchResult` | Result from a search pipeline execution | REMOVE -- each domain package defines its own |

---

## 2. Target Architecture

### Overview

```
PlanAgent (supervisor, pydantic_ai Agent with tools) -- UNCHANGED
    |
    invoke_executors(dispatches: list[ExecutorDispatch])  -- REWRITTEN
    |
    For each dispatch, calls the appropriate domain package:
    |
    +-- dispatch.domain == "regulations":
    |     from .reg_search import run_reg_search, RegSearchDeps
    |     RegSearchDeps(supabase, embedding_fn, ...)
    |     result = await run_reg_search(focus, user_ctx, deps)
    |
    +-- dispatch.domain == "cases":
    |     from .case_search import run_case_search, CaseSearchDeps
    |     CaseSearchDeps(supabase, embedding_fn, ...)
    |     result = await run_case_search(focus, user_ctx, deps)
    |
    +-- dispatch.domain == "compliance":
          from .compliance_search import run_compliance_search, ComplianceSearchDeps
          ComplianceSearchDeps(supabase, embedding_fn, ...)
          result = await run_compliance_search(focus, user_ctx, deps)
    |
    Results mapped to ExecutorResult -> build_aggregated_report() -> insert/update artifact -> return summary
```

### Domain Package Architecture (Each Package)

```
ExpanderNode (LLM: or-deepseek-v3.2)
    |  generates 2-4 Arabic search queries
    v
SearchNode (programmatic: embed + hybrid RPC + rerank + format)
    |  runs queries concurrently
    v
AggregatorNode (LLM: or-qwen3.5-397b)
    |  evaluates quality, produces synthesis + citations
    |
    +-- sufficient=True OR max_rounds reached -> End(DomainSearchResult)
    +-- sufficient=False AND retries remain -> ExpanderNode (with weak_axes as dynamic instructions)
```

### Key Improvements Over Current Architecture

1. **Separated concerns**: Query expansion is now a dedicated LLM agent (cheaper model: deepseek-v3.2) instead of bundled into a single expensive executor agent
2. **Explicit retry logic**: Weak results trigger targeted re-expansion via `weak_axes` dynamic instructions, rather than hoping the monolithic LLM decides to retry
3. **Deterministic search**: SearchNode is programmatic -- no LLM needed for executing searches
4. **Better synthesis**: Aggregator is a dedicated LLM agent (qwen3.5-397b) that receives ALL search results at once, rather than incrementally within a tool-call loop
5. **Multi-prompt A/B testing**: Each domain package supports multiple prompt variants selectable via CLI flags
6. **Independent testability**: Each domain package has its own CLI and test suite

---

## 3. File-by-File Migration Map

### Files to MODIFY

| File | What Changes | Why |
|------|-------------|-----|
| `plan_agent.py` | Rewrite `invoke_executors` tool body. Remove imports from `executors/`. Add imports from domain packages. Change executor creation/running logic to call `run_reg_search`, `run_case_search`, `run_compliance_search`. Map domain results to `ExecutorResult`. Keep artifact creation/update logic, SSE event collection, and summary building. | Core of the migration -- routing from monolithic executors to domain packages |
| `prompts.py` | Remove: `REGULATIONS_EXECUTOR_PROMPT`, `CASES_EXECUTOR_PROMPT`, `COMPLIANCE_EXECUTOR_PROMPT`, `build_executor_dynamic_instructions()`, `_build_regulations_expansion_guidance()`, `_build_cases_expansion_guidance()`, `_build_compliance_expansion_guidance()`. Keep: `PLAN_AGENT_SYSTEM_PROMPT`, `build_dynamic_instructions()`, `format_executor_results()`, `format_task_history()`. | Executor prompts moved into their domain packages. PlanAgent prompt stays. |
| `models.py` | Remove: `ExecutorDeps` dataclass, `SearchResult` dataclass. Keep: `DeepSearchV3Deps`, `ExecutorDispatch`, `ExecutorResult`, `AggregatedResults`, `Citation`. | ExecutorDeps replaced by domain-specific deps (RegSearchDeps, CaseSearchDeps, ComplianceSearchDeps). SearchResult replaced by domain-specific dataclasses. |
| `logger.py` | Minor update: executor trace extraction may need to adapt to the new domain package event format. The `_executor_trace` SSE event type may carry slightly different metadata (e.g., `search_log` format from domain packages vs old executors). Keep the overall structure. | Domain packages collect events in their own `deps._events`; the transfer mechanism changes slightly. |

### Files to DELETE

| File | Reason |
|------|--------|
| `executors/__init__.py` | Entire executors directory is replaced by domain packages |
| `executors/base.py` | `create_executor()` and `run_executor()` replaced by domain package `run_*_search()` calls |
| `executors/regulations.py` | `register_search_regulations()` moved into `reg_search/search_pipeline.py` |
| `executors/cases.py` | `register_search_cases()` moved into `case_search/search_pipeline.py` |
| `executors/compliance.py` | `register_search_compliance()` moved into `compliance_search/search_pipeline.py` |
| `executors/search_pipeline.py` | Split into 3 domain-specific pipelines: `reg_search/search_pipeline.py`, `case_search/search_pipeline.py`, `compliance_search/search_pipeline.py`. Shared helpers (`_hybrid_rpc_search`, `_rerank`, `_score_fallback`) are copied into each package. |
| `executors/regulation_unfold.py` | Moved to `reg_search/regulation_unfold.py` (full copy, only used by regulations) |

### Files UNCHANGED

| File | Why |
|------|-----|
| `report_builder.py` | Consumes `list[ExecutorResult]` and builds unified markdown report. Interface does not change -- domain packages return ExecutorResult-compatible schemas. |
| `runner.py` | Entry point `handle_deep_search_v3_turn()` calls PlanAgent and returns `TaskContinue`/`TaskEnd`. PlanAgent handles the rest internally. `build_search_deps()` constructs `DeepSearchV3Deps` as before. |
| `cli.py` | Calls `runner.py` -- top-level interface does not change. |
| `__init__.py` | Exports `handle_deep_search_v3_turn`, `build_search_deps`, `DeepSearchV3Deps` -- all unchanged. |

### New Sub-Packages (already planned separately)

| Package | Location | INITIAL.md |
|---------|----------|-----------|
| reg_search | `agents/deep_search_v3/reg_search/` | `agents/deep_search_v3/reg_search/planning/INITIAL.md` |
| case_search | `agents/deep_search_v3/case_search/` | `agents/deep_search_v3/case_search/planning/INITIAL.md` |
| compliance_search | `agents/deep_search_v3/compliance_search/` | `agents/deep_search_v3/compliance_search/planning/INITIAL.md` |

### Target Directory Structure After Migration

```
agents/deep_search_v3/
    __init__.py              # UNCHANGED -- exports handle_deep_search_v3_turn, build_search_deps
    plan_agent.py            # MODIFIED -- invoke_executors calls domain packages
    models.py                # MODIFIED -- removed ExecutorDeps, SearchResult
    prompts.py               # MODIFIED -- removed executor prompts, kept PlanAgent prompt
    report_builder.py        # UNCHANGED
    runner.py                # UNCHANGED
    logger.py                # MINOR UPDATE -- executor trace format adaptation
    cli.py                   # UNCHANGED
    planning/
        INITIAL.md           # This file (migration plan)
        prompts.md           # Existing prompt design doc
    logs/                    # Existing log directory
    reg_search/              # NEW -- regulations domain search loop
        __init__.py
        models.py
        expander_prompts.py
        aggregator_prompts.py
        search_pipeline.py
        regulation_unfold.py
        expander.py
        aggregator.py
        loop.py
        logger.py
        cli.py
        planning/
            INITIAL.md
        logs/
        tests/
    case_search/             # NEW -- cases domain search loop
        __init__.py
        models.py
        prompts.py
        search_pipeline.py
        expander.py
        aggregator.py
        loop.py
        logger.py
        cli.py
        planning/
            INITIAL.md
        logs/
        tests/
    compliance_search/       # NEW -- compliance domain search loop
        __init__.py
        models.py
        prompts.py
        search_pipeline.py
        expander.py
        aggregator.py
        loop.py
        logger.py
        cli.py
        planning/
            INITIAL.md
        tests/
    # DELETED: executors/ (entire directory)
```

---

## 4. Updated invoke_executors Tool (Detailed Pseudocode)

This is the core change. The current `invoke_executors` in `plan_agent.py` (lines 72-344) will be rewritten. The overall structure stays the same -- validation, parallel execution, result collection, artifact creation, summary building -- but the executor creation and running logic changes completely.

### Current Logic (to be replaced)

```python
from .executors import create_executor, run_executor

# For each dispatch:
executor_agent = create_executor(domain=domain, focus_instruction=focus, user_context=user_ctx)
executor_deps = ExecutorDeps(supabase=..., embedding_fn=..., ...)
message = f"{focus}\n\n{user_ctx}" if user_ctx else focus
# ... run via _run_one(domain, executor_agent, message, executor_deps)
result, messages_json = await run_executor(agent, msg, deps)
```

### New Logic

```python
@plan_agent.tool
async def invoke_executors(
    ctx: RunContext[DeepSearchV3Deps],
    dispatches: list[ExecutorDispatch],
) -> str:
    """Dispatch 1-5 domain search loops in parallel. Returns combined summary.

    Each dispatch specifies:
    - domain: "regulations", "cases", or "compliance"
    - focus_instruction: What to search for in this domain (Arabic)
    - user_context: User's personal situation (Arabic)

    Multiple dispatches with the same domain are allowed (each gets its own
    search loop instance with a different focus_instruction).
    """
    from .report_builder import build_aggregated_report

    # -- Validation (same as current) --
    if not dispatches:
        return "...error in Arabic..."
    if len(dispatches) > MAX_EXECUTOR_INSTANCES:
        dispatches = dispatches[:MAX_EXECUTOR_INSTANCES]

    valid_domains = {"regulations", "cases", "compliance"}
    domain_labels = {
        "regulations": "الأنظمة",
        "cases": "الأحكام القضائية",
        "compliance": "الخدمات الحكومية",
    }

    # Emit status: starting
    ctx.deps._sse_events.append({
        "type": "status",
        "text": f"جاري تشغيل {len(dispatches)} منفذ بحث بالتوازي...",
    })

    # -- Build domain-specific tasks --
    async def _run_domain_search(
        dispatch: ExecutorDispatch,
        index: int,
    ) -> ExecutorResult:
        domain = dispatch.domain
        focus = dispatch.focus_instruction
        user_ctx = dispatch.user_context

        label = domain_labels.get(domain, domain)
        ctx.deps._sse_events.append({
            "type": "status",
            "text": f"جاري تشغيل منفذ {label} ({index + 1}/{len(dispatches)})...",
        })

        try:
            if domain == "regulations":
                from .reg_search import RegSearchDeps, run_reg_search

                deps = RegSearchDeps(
                    supabase=ctx.deps.supabase,
                    embedding_fn=ctx.deps.embedding_fn,
                    jina_api_key=ctx.deps.jina_api_key,
                    http_client=ctx.deps.http_client,
                    use_reranker=ctx.deps.use_reranker,
                    mock_results=ctx.deps.mock_results,
                    _events=[],
                    _search_log=[],
                )
                domain_result = await run_reg_search(
                    focus_instruction=focus,
                    user_context=user_ctx,
                    deps=deps,
                )

            elif domain == "cases":
                from .case_search import CaseSearchDeps, run_case_search

                deps = CaseSearchDeps(
                    supabase=ctx.deps.supabase,
                    embedding_fn=ctx.deps.embedding_fn,
                    jina_api_key=ctx.deps.jina_api_key,
                    http_client=ctx.deps.http_client,
                    use_reranker=ctx.deps.use_reranker,
                    mock_results=ctx.deps.mock_results,
                    _events=[],
                    _search_log=[],
                )
                domain_result = await run_case_search(
                    focus_instruction=focus,
                    user_context=user_ctx,
                    deps=deps,
                )

            elif domain == "compliance":
                from .compliance_search import (
                    ComplianceSearchDeps,
                    run_compliance_search,
                )

                deps = ComplianceSearchDeps(
                    supabase=ctx.deps.supabase,
                    embedding_fn=ctx.deps.embedding_fn,
                    jina_api_key=ctx.deps.jina_api_key,
                    http_client=ctx.deps.http_client,
                    use_reranker=ctx.deps.use_reranker,
                    mock_results=ctx.deps.mock_results,
                    _events=[],
                    _search_log=[],
                )
                domain_result = await run_compliance_search(
                    focus_instruction=focus,
                    user_context=user_ctx,
                    deps=deps,
                )
            else:
                # Should not happen due to validation, but defensive
                return ExecutorResult(
                    quality="weak",
                    summary_md=f"مجال غير معروف: {domain}",
                    citations=[],
                    domain=domain,
                    queries_used=[],
                    rounds_used=0,
                )

            # Transfer domain package SSE events to plan-level
            ctx.deps._sse_events.extend(deps._events)

            # Transfer search logs for the trace logger
            ctx.deps._sse_events.append({
                "type": "_executor_trace",
                "domain": domain,
                "focus_instruction": focus,
                "search_log": list(deps._search_log),
                "model_messages_json": None,  # Domain packages don't expose raw model messages
            })

            # Map domain result to ExecutorResult (compatible schema)
            return ExecutorResult(
                quality=domain_result.quality,
                summary_md=domain_result.summary_md,
                citations=domain_result.citations,  # Citation schema is identical
                domain=domain_result.domain,
                queries_used=domain_result.queries_used,
                rounds_used=domain_result.rounds_used,
                inner_usage=[],  # Usage tracked separately in domain package logs
            )

        except Exception as e:
            logger.error("Domain search %s failed: %s", domain, e, exc_info=True)
            return ExecutorResult(
                quality="weak",
                summary_md=f"حدث خطأ أثناء البحث في مجال {domain}: {e}",
                citations=[],
                domain=domain,
                queries_used=[],
                rounds_used=0,
            )

    # -- Filter invalid domains --
    valid_dispatches = [d for d in dispatches if d.domain in valid_domains]
    if not valid_dispatches:
        return "خطأ: لم يتم تحديد أي منفذين صالحين."

    # -- Run all domain searches in parallel --
    all_results: list[ExecutorResult] = await asyncio.gather(
        *[_run_domain_search(d, i) for i, d in enumerate(valid_dispatches)],
    )

    # -- Store results on deps for dynamic instruction injection --
    ctx.deps._executor_results.extend(all_results)

    # -- Emit completion statuses --
    quality_labels = {"strong": "قوية", "moderate": "متوسطة", "weak": "ضعيفة"}
    for result in all_results:
        label = domain_labels.get(result.domain, result.domain)
        q_label = quality_labels.get(result.quality, result.quality)
        ctx.deps._sse_events.append({
            "type": "status",
            "text": f"اكتمل منفذ {label} -- جودة: {q_label}",
        })

    # -- Build aggregated report (same as current) --
    ctx.deps._sse_events.append({
        "type": "status",
        "text": "جاري تجميع النتائج وكتابة التقرير...",
    })

    report_md = build_aggregated_report(
        executor_results=all_results,
        question=valid_dispatches[0].focus_instruction if valid_dispatches else "",
    )

    # -- Insert/update artifact (same as current -- no changes) --
    # ... (artifact creation/update logic stays identical)

    # -- Build summary string for PlanAgent LLM (same as current) --
    # ... (summary building logic stays identical)

    return "\n".join(summary_parts)
```

### Key Differences from Current Implementation

| Aspect | Current | New |
|--------|---------|-----|
| Executor creation | `create_executor(domain, focus, ctx)` returns an Agent | `run_*_search(focus, ctx, deps)` returns a result directly |
| Executor running | `run_executor(agent, msg, deps)` via agent.iter() | Domain package handles its own graph loop internally |
| Deps type | `ExecutorDeps` (shared across all domains) | Domain-specific: `RegSearchDeps`, `CaseSearchDeps`, `ComplianceSearchDeps` |
| Model messages | Captured from executor agent run | Not captured at plan level (domain packages log internally) |
| Search loop | LLM decides when to retry (implicit) | Explicit graph loop with max_rounds + weak_axes feedback |
| Query expansion | LLM's first action in its tool-call loop | Dedicated QueryExpander agent (cheaper model) |
| Synthesis | LLM's final output before returning ExecutorResult | Dedicated Aggregator agent (specialized model) |
| SSE events | Collected in `executor_deps._events`, transferred | Collected in `domain_deps._events`, transferred (same pattern) |
| Error handling | try/except returns weak ExecutorResult | Same pattern -- domain packages also handle errors internally |

### Result Schema Compatibility

Domain package results (`RegSearchResult`, `CaseSearchResult`, `ComplianceSearchResult`) are mapped to `ExecutorResult` in `invoke_executors`. The field mapping is direct:

| Domain Result Field | ExecutorResult Field | Type | Notes |
|--------------------|---------------------|------|-------|
| `quality` | `quality` | `Literal["strong", "moderate", "weak"]` | Identical |
| `summary_md` | `summary_md` | `str` | Identical |
| `citations` | `citations` | `list[Citation]` | Citation model is shared/identical |
| `domain` | `domain` | `Literal["regulations", "cases", "compliance"]` | Identical |
| `queries_used` | `queries_used` | `list[str]` | Identical |
| `rounds_used` | `rounds_used` | `int` | Identical |
| (not present) | `inner_usage` | `list[dict]` | Set to `[]` -- usage tracked in domain package logs |

---

## 5. Detailed Changes per Modified File

### 5.1 plan_agent.py Changes

**Remove:**
- Line 90: `from .executors import create_executor, run_executor` (inside invoke_executors)
- Lines 117-156: Executor creation loop (`create_executor` + `ExecutorDeps` construction + task building)
- Lines 162-183: `_run_one` function (wraps `run_executor`)
- Lines 192-213: Executor trace event building (model_messages_json capture)
- Import: `ExecutorDeps` from `.models`

**Replace with:**
- Domain package imports (inside invoke_executors to avoid circular imports)
- Domain-specific deps construction
- Direct `run_*_search()` calls via asyncio.gather
- Simplified result mapping (domain results -> ExecutorResult)
- Simplified trace events (no model_messages_json from domain packages)

**Keep unchanged:**
- Lines 34-57: Agent definition (plan_agent)
- Lines 60-66: Dynamic instructions
- Lines 72-89: invoke_executors signature and docstring
- Lines 93-109: Validation and domain_labels
- Lines 110-114: SSE status: starting executors
- Lines 229-344: Everything after results collection (artifact insert/update, summary building)
- Lines 347-408: `update_report` and `ask_user` tools (completely unchanged)

**New import at top of file:**
```python
# No new top-level imports needed -- domain packages are imported lazily inside invoke_executors
```

### 5.2 prompts.py Changes

**Remove (lines 119-469):**
- `REGULATIONS_EXECUTOR_PROMPT` (lines 119-171) -- moved to `reg_search/expander_prompts.py` + `reg_search/aggregator_prompts.py`
- `CASES_EXECUTOR_PROMPT` (lines 174-224) -- moved to `case_search/prompts.py`
- `COMPLIANCE_EXECUTOR_PROMPT` (lines 227-277) -- moved to `compliance_search/prompts.py`
- `build_executor_dynamic_instructions()` (lines 339-397) -- moved to each domain package's prompts
- `_build_regulations_expansion_guidance()` (lines 400-430) -- moved to `reg_search/expander_prompts.py`
- `_build_cases_expansion_guidance()` (lines 433-450) -- moved to `case_search/prompts.py`
- `_build_compliance_expansion_guidance()` (lines 453-469) -- moved to `compliance_search/prompts.py`

**Keep (lines 1-117, 280-528):**
- `PLAN_AGENT_SYSTEM_PROMPT` (lines 21-114) -- PlanAgent prompt stays completely unchanged. It still thinks in terms of dispatching by domain.
- `build_dynamic_instructions(deps)` (lines 283-336) -- PlanAgent dynamic instructions (case memory, previous report, task history, executor results, artifact_id)
- `format_executor_results(results)` (lines 472-508) -- Formats executor results for PlanAgent context injection
- `format_task_history(task_history)` (lines 511-528) -- Formats task history for PlanAgent context

### 5.3 models.py Changes

**Remove:**
- `SearchResult` dataclass (lines 120-128) -- replaced by domain-specific dataclasses in each package
- `ExecutorDeps` dataclass (lines 134-147) -- replaced by `RegSearchDeps`, `CaseSearchDeps`, `ComplianceSearchDeps`

**Keep:**
- `Citation` (lines 27-58) -- shared across all domain packages (imported by them)
- `ExecutorResult` (lines 61-90) -- PlanAgent still consumes this; domain results are mapped to it
- `ExecutorDispatch` (lines 93-104) -- PlanAgent still emits these
- `AggregatedResults` (lines 107-115) -- used by report_builder
- `DeepSearchV3Deps` (lines 152-178) -- top-level deps for PlanAgent

### 5.4 logger.py Changes

**Minor updates:**
- `_extract_executor_traces()`: The `_executor_trace` events will no longer have `model_messages_json` (domain packages don't expose raw LLM messages at the plan level). The trace extraction should handle `None` gracefully (it already does via the null check in `_parse_executor_messages`).
- `_save_trace_md()`: The executor trace section may show fewer details since raw model messages are not available. The search_log will still be present.
- `_save_search_results_md()`: Unchanged -- still reads `search_log` from executor trace events.

**No structural changes needed.** The existing null-safety in `_parse_executor_messages` and the fact that `model_messages_json` is already optional means the logger will work with the new format without breaking.

---

## 6. Model Slot Additions (agent_models.py)

Add 6 new model slots to `agents/utils/agent_models.py`:

```python
AGENT_MODELS = {
    # ... existing entries ...

    # Deep Search V3 — domain-specific search loops
    # Regulations search loop
    "reg_search_expander":   "or-deepseek-v3.2",       # Query expansion (cheap, fast)
    "reg_search_aggregator": "or-qwen3.5-397b",        # Synthesis + quality evaluation

    # Cases search loop
    "case_search_expander":   "or-deepseek-v3.2",      # Query expansion (cheap, fast)
    "case_search_aggregator": "or-qwen3.5-397b",       # Synthesis + quality evaluation

    # Compliance search loop
    "compliance_search_expander":   "or-deepseek-v3.2", # Query expansion (cheap, fast)
    "compliance_search_aggregator": "or-qwen3.5-397b",  # Synthesis + quality evaluation
}
```

**Fallback chain for all 6 slots:** `or-gemini-2.5-flash` then `or-mimo-v2-pro`.

**Model slots to REMOVE after migration (Phase 3):**

```python
# These become dead code after executors/ is deleted:
"deep_search_v3_regulations_executor": "or-minimax-m2.7",  # REMOVE
"deep_search_v3_cases_executor":       "or-minimax-m2.7",  # REMOVE
"deep_search_v3_compliance_executor":  "or-minimax-m2.7",  # REMOVE
```

**Model slot that stays:**

```python
"deep_search_v3_plan_agent": "or-gemini-3.1-pro-tools",    # KEEP -- PlanAgent unchanged
```

---

## 7. Migration Phases

### Phase 1: Build Domain Packages (Independent, Parallel)

**Goal:** Build all 3 domain packages as standalone, testable units.

**Actions:**
1. Build `reg_search/` per its INITIAL.md (expander, aggregator, search pipeline, regulation_unfold, loop, CLI, tests)
2. Build `case_search/` per its INITIAL.md (expander, aggregator, search pipeline, loop, CLI, tests)
3. Build `compliance_search/` per its INITIAL.md (expander, aggregator, search pipeline, loop, CLI, tests)

**Verification:**
- Each package's CLI works end-to-end: `python -m agents.deep_search_v3.reg_search.cli "query"`
- Each package's tests pass: `pytest agents/deep_search_v3/reg_search/tests/`
- Each package returns a result compatible with ExecutorResult schema

**Dependencies:** None -- packages are self-contained.

**Note:** All 3 packages can be built in parallel by different builders. They share no code between them (each copies the search pipeline helpers it needs).

### Phase 2: Wire Domain Packages into deep_search_v3

**Goal:** Modify `plan_agent.py`, `prompts.py`, and `models.py` to use domain packages instead of executors.

**Actions:**
1. **plan_agent.py**: Rewrite `invoke_executors` tool body per the pseudocode in Section 4
2. **prompts.py**: Remove executor prompts and expansion guidance functions
3. **models.py**: Remove `ExecutorDeps` and `SearchResult`
4. **agent_models.py**: Add 6 new model slots (reg_search_*, case_search_*, compliance_search_*)

**Verification:**
- PlanAgent can still dispatch to all 3 domains
- End-to-end CLI works: `python -m agents.deep_search_v3.cli "query"`
- Report builder produces correct output with new results
- SSE events flow correctly from domain packages through PlanAgent to runner

**Dependencies:** Phase 1 must be complete (all 3 domain packages work standalone).

### Phase 3: Delete Old Executors

**Goal:** Remove the now-unused `executors/` directory.

**Actions:**
1. Delete `executors/__init__.py`
2. Delete `executors/base.py`
3. Delete `executors/regulations.py`
4. Delete `executors/cases.py`
5. Delete `executors/compliance.py`
6. Delete `executors/search_pipeline.py`
7. Delete `executors/regulation_unfold.py`
8. Delete the `executors/` directory itself
9. Remove dead model slots from `agent_models.py`: `deep_search_v3_regulations_executor`, `deep_search_v3_cases_executor`, `deep_search_v3_compliance_executor`

**Verification:**
- `python -m agents.deep_search_v3.cli "query"` still works end-to-end
- No import errors -- nothing references `executors/` anymore
- `grep -r "from .executors" agents/deep_search_v3/` returns nothing
- `grep -r "executors.base\|executors.regulations\|executors.cases\|executors.compliance" agents/` returns nothing

**Dependencies:** Phase 2 must be complete and verified.

### Phase 4: Update logger.py (if needed)

**Goal:** Ensure trace logging works correctly with the new domain package event format.

**Actions:**
1. Verify `_extract_executor_traces()` handles traces without `model_messages_json`
2. Verify `_save_trace_md()` renders correctly with new format
3. Verify `_save_search_results_md()` still captures raw search results
4. Update if any format issues are found

**Verification:**
- Run CLI, check `logs/trace/*.md` and `logs/search_results/*.md` for correct output
- Verify JSON log in `logs/*.json` has expected structure

**Dependencies:** Phase 2 must be complete.

### Phase 5: End-to-End Testing

**Goal:** Validate the complete flow works correctly with all 3 domains.

**Actions:**
1. Run multi-domain queries via CLI: `python -m agents.deep_search_v3.cli "ما هي حقوق العامل في حالة الفصل التعسفي؟"`
2. Verify PlanAgent dispatches to multiple domains (regulations + cases)
3. Verify retry loops trigger when results are weak
4. Verify artifact creation and report formatting
5. Verify SSE event flow is complete and correct
6. Test domain-specific CLIs independently
7. Run all domain package test suites

**Verification:**
- All domain CLIs produce valid results
- V3 CLI produces a full report with citations from multiple domains
- Logs show correct trace and search results
- No regressions in PlanAgent behavior (out-of-scope detection, update_report, ask_user)

---

## 8. Success Criteria

### Functional Criteria

- [ ] `invoke_executors` calls domain packages instead of creating monolithic executor agents
- [ ] PlanAgent still dispatches 1-5 executors by domain (unchanged behavior from LLM perspective)
- [ ] Multiple dispatches of the same domain work (e.g., 2 regulations searches with different focus)
- [ ] Results from all 3 domain packages map correctly to `ExecutorResult`
- [ ] `report_builder.py` produces correct unified reports from domain package results
- [ ] Artifact creation and update work (DB insert/update unchanged)
- [ ] SSE events flow from domain packages -> PlanAgent -> runner -> frontend
- [ ] Retry loops in domain packages trigger on weak results (max 3 rounds per domain)
- [ ] Weak axes are fed back to expanders as dynamic instructions
- [ ] PlanAgent system prompt works unchanged (still dispatches by domain)
- [ ] `update_report` and `ask_user` tools work unchanged
- [ ] CLI works end-to-end: `python -m agents.deep_search_v3.cli "query"`
- [ ] Each domain CLI works independently for testing

### Non-Functional Criteria

- [ ] No import errors after `executors/` deletion
- [ ] No circular imports between deep_search_v3 and domain packages
- [ ] Logger produces correct trace and search results markdown
- [ ] All domain package test suites pass
- [ ] Model slots in agent_models.py are correct (6 new, 3 removed)
- [ ] No dead code remains (no references to deleted files)

### Performance Criteria

- [ ] Domain searches run in parallel (same as current -- asyncio.gather)
- [ ] QueryExpander uses cheaper model (deepseek-v3.2) than current monolithic executor (minimax-m2.7)
- [ ] Aggregator uses specialized model (qwen3.5-397b) for better synthesis quality
- [ ] Total latency is comparable or better than current architecture

---

## 9. Risks and Mitigations

### Risk 1: Domain result schema mismatch

**What could go wrong:** Domain package result models (RegSearchResult, CaseSearchResult, ComplianceSearchResult) may have subtle differences from ExecutorResult that cause failures in report_builder or PlanAgent context injection.

**Mitigation:** The mapping in `invoke_executors` is explicit (field-by-field assignment to ExecutorResult constructor). Citation model is shared/identical. Test the mapping with real domain results before deleting executors/.

### Risk 2: SSE event format changes break frontend

**What could go wrong:** Domain packages may emit SSE events with slightly different text or missing event types that the frontend expects.

**Mitigation:** Domain packages should emit the same SSE event types and Arabic text as the current executors. Review the event flow in the current implementation and match it in domain packages.

### Risk 3: Circular imports

**What could go wrong:** Domain packages import from `agents.deep_search_v3.models` (for Citation). If `plan_agent.py` also imports from domain packages, circular imports could occur.

**Mitigation:** Domain package imports in `invoke_executors` are lazy (inside the function body, not at module level). Domain packages import Citation from `agents.deep_search_v3.models` at module level -- this is fine because `models.py` does not import from domain packages. The dependency graph is acyclic: `models.py` <- `reg_search/` <- `plan_agent.py` (lazy).

### Risk 4: Logger breaks with missing model_messages_json

**What could go wrong:** The logger expects `_executor_trace` events with `model_messages_json` field. Domain packages don't expose raw LLM messages at the plan level.

**Mitigation:** The logger already handles `None` model_messages_json gracefully (`_parse_executor_messages` returns `[]` on None). The executor trace section in trace markdown will show fewer details but will not crash. Verify with a test run.

### Risk 5: Regression in PlanAgent behavior

**What could go wrong:** Changing the invoke_executors tool return format could confuse the PlanAgent LLM, leading to worse dispatch decisions or answer quality.

**Mitigation:** The summary string returned by invoke_executors to the PlanAgent LLM has the same format as before (executor count, domain, quality, citations, artifact_id). The PlanAgent system prompt is unchanged. The LLM should not notice any difference.

### Risk 6: Phase overlap causes conflicts

**What could go wrong:** If Phase 2 (wiring) starts before all 3 domain packages are complete, partial wiring could leave the system in a broken state.

**Mitigation:** Phase 2 should only start after all 3 domain packages pass their standalone tests. During Phase 2, the old executors still exist as fallback. Only Phase 3 (deletion) is destructive and should be done only after Phase 2 is fully verified.

### Risk 7: Test infrastructure differences

**What could go wrong:** Domain packages use `pydantic_graph` testing patterns (mock state, deps) while existing V3 tests use Agent-level testing. Test patterns may not align.

**Mitigation:** Each domain package has its own test suite with its own conftest.py. They test the graph loop, not the PlanAgent integration. PlanAgent integration testing is done via the CLI in Phase 5.

---

## 10. What Does NOT Change

This section explicitly documents what remains untouched to prevent scope creep.

1. **PlanAgent system prompt** (`PLAN_AGENT_SYSTEM_PROMPT`): The LLM still thinks in terms of dispatching executors by domain. It does not know or care whether executors are monolithic agents or graph loops.

2. **ExecutorDispatch model**: PlanAgent still emits `list[ExecutorDispatch]` with domain, focus_instruction, user_context. No schema change.

3. **ExecutorResult model**: The unified result type that PlanAgent consumes. Domain results are mapped to it.

4. **report_builder.py**: Consumes `list[ExecutorResult]` and builds unified markdown. Interface unchanged.

5. **runner.py**: Entry point `handle_deep_search_v3_turn()` calls PlanAgent. PlanAgent handles everything internally.

6. **cli.py**: Top-level CLI calls runner. No changes.

7. **__init__.py**: Exports stay the same.

8. **update_report tool**: Unchanged.

9. **ask_user tool**: Unchanged.

10. **build_search_deps()**: Constructs DeepSearchV3Deps. Unchanged.

11. **DeepSearchV3Deps**: Top-level deps. Unchanged (domain-specific deps are constructed from it inside invoke_executors).

12. **PlannerResult** (from `agents/models.py`): PlanAgent output type. Unchanged.

13. **TaskContinue / TaskEnd** (from `agents/models.py`): Runner result types. Unchanged.

---

Generated: 2026-04-04
Note: This is a migration plan for existing infrastructure. The 3 domain packages (reg_search, case_search, compliance_search) are planned separately in their own INITIAL.md files. This document covers the changes needed in deep_search_v3 itself to use those packages.
