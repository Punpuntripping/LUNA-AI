# Agent Tracking Protocol — Unified Logfire Telemetry for Every Agent

**Scope.** One reusable contract for how *every* Luna agent stage — existing and future — emits Logfire telemetry: identity, input ("what the agent saw"), output, output schema, resource (tokens/cost/model), timing, and outcome. Replaces the current state where each family hand-rolls its own span name + attribute set and `conversation_id` is missing from ~half the pipeline.

**Status.** ✅ Built 2026-05-28 (P0–P5). First adopter was `writer_planner` + `writing_executor` (they emitted **zero** hand-rolled spans — see the 2026-05-28 telemetry gap analysis); all other families since migrated.

**Motivating defect.** `writer_planner`'s `Agent.run()` produced only an auto-instrumented `agent run` span with **no `conversation_id`**, so any conversation_id-pivoted query (`WHERE attributes->>'conversation_id' = '<id>'`) never saw it. `deep_search_v4` hand-stamps `conversation_id` on every stage span and is fully visible; nothing else did it consistently. This protocol makes the deep_search discipline automatic and uniform.

---

## TL;DR

| Concern | Mechanism | Where it lives |
|---|---|---|
| **Basics** (ids, family, subtype, stage, turn) | Total template — auto-stamped by the helper | span attributes |
| **Resource** (tokens_in/out/reasoning, cost_usd, model_used, cache, requests) | Total template — from pydantic-ai `result.usage()` + `estimate_run_cost` | span attributes |
| **Timing + outcome** (duration_ms, outcome, error, error.type) | Total template — the helper times + classifies | span attributes |
| **Output value** | Total template — guarded `model_dump()`; per-agent `tracking_output()` override | span attributes `output.*` / `output_json` |
| **Output schema** | Dumped **once** at startup; per-span = cheap `output_type` + `output_schema_ref` | startup registry + span attrs |
| **Input — bounded** ("what it saw", queryable) | Per-agent `tracking_input()` + reflective fallback | span attributes `input.*` |
| **Input — full content** ("what it saw", verbatim) | Per-agent `tracking_input_full()` + reflective fallback — **span event, env-gated, off in prod** | log record parented to the span |

**The answer to "total template or inject per agent?"** — Both. ~85% is one global template (helper). The only per-agent injection is up to three small optional methods on the deps/output object (`tracking_input`, `tracking_input_full`, `tracking_output`), and even those have automatic fallbacks so a brand-new agent is tracked with **zero** extra code.

---

## Two storage homes (do not conflate)

| | Logfire (this protocol) | Supabase `agent_runs` (already exists) |
|---|---|---|
| Shape | Hierarchical span tree | Flat, one row per dispatch |
| Lifespan | ~30 days | Permanent |
| Purpose | Forensics, perf, "why/how/in what order" | Audit + billing source of truth |
| Written by | `agents/utils/tracking.py` helper (this doc) | `agents/runs.py::record_agent_run` (unchanged) |

This protocol governs **Logfire only**. The durable `agent_runs` row is unchanged; the helper just makes sure `trace_id`/`span_id` line up so the Supabase↔Logfire join the monitor relies on stays intact (`agents/runs.py::_hydrate_trace_ids_from_span` already does this).

---

## Two stage flavors — the protocol covers both

Not every trackable unit has an `Agent.run()`. The protocol has two entry points writing into the **same attribute namespace**:

### 1. LLM stages — `run_tracked(...)`

Stages that call a pydantic-ai `Agent.run()` (router, deep_search planner/aggregator/sector_picker, writer_planner decider, writing_executor, item_analyzer, artifact_summarizer). The runner swaps `await agent.run(prompt, deps=deps, ...)` for:

```python
result = await run_tracked(
    agent, user_prompt,
    deps=deps,
    stage="writer.plan",       # <family>.<stage>
    subtype=subtype,           # optional
)
```

`run_tracked` does everything automatically: opens the span, stamps basics + identity (pulled from `deps`), captures input (`deps.tracking_input()` / `_full()`), runs the agent, captures output + output-schema-ref, pulls tokens/cost/model from `result.usage()` + the cost registry, classifies outcome (incl. `DeferredToolRequests` → `paused`, `CancelledError` → `cancelled`), records exceptions, closes. Returns the raw `AgentRunResult` unchanged so call sites are untouched downstream.

### 2. Non-LLM stages — `track_stage(...)`

Stages with no `Agent.run()` (publish.workspace_item, ocr_extraction orchestration, memory.summarize DB path, agent_runs.record, retrieval phases that fan out to sub-runs). Context-manager flavor:

```python
with track_stage("memory.summarize", conversation_id=cid, case_id=case_id,
                 agent_family="memory", input_obj=row) as t:
    ...
    t.set(items_written=n, model_used=model, tokens_in=ti, tokens_out=to)
    t.record_output(summary_obj, outcome="ok")
```

Same basics + identity + input/output/outcome semantics; no automatic LLM-usage extraction (caller supplies tokens/cost when relevant via `t.set(...)`).

---

## The per-agent contract — `Trackable`

All three methods are **optional**. Implement only when the automatic fallback is wrong or too noisy.

```python
# agents/utils/tracking.py
@runtime_checkable
class Trackable(Protocol):
    def tracking_input(self) -> dict[str, Any]: ...        # bounded → span attrs input.*
    def tracking_input_full(self) -> dict[str, Any]: ...   # verbatim → span event (gated)
    # tracking_output() lives on the OUTPUT object, not deps:
    # def tracking_output(self) -> dict[str, Any]: ...      # heavy outputs only
```

**Example — `WriterPlannerDeps`** (the deps shape that motivated the split; see `agents/writer_planner/deps.py`):

```python
def tracking_input(self) -> dict:           # bounded, always on
    return {
        "intent_chars": len(self.intent),
        "attached_items": [s.item_id for s in self.attached_items],
        "prior_artifacts": len(self.prior_artifacts),
        "recent_messages": len(self.recent_messages),
        "case_brief_present": self.case_brief is not None,
        "detail_level": self.style.detail_level,
        "present_count": self.present_count,
    }

def tracking_input_full(self) -> dict:      # verbatim, env-gated event
    return {
        "intent": self.intent,
        "recent_messages": [m.text for m in self.recent_messages],
        "attached_items": [
            {"item_id": s.item_id, "kind": s.kind, "title": s.title, "summary": s.summary}
            for s in self.attached_items
        ],
        "prior_artifacts": [a.summary for a in self.prior_artifacts],
        "case_brief": self.case_brief,
    }
```

`tracking_input_full` is a **superset** of `tracking_input` by convention (same field names where they overlap), so a reader diffing the two sees only "counts → contents".

### Reflective fallback (future-proofing — the key to "all future agents")

When a deps object implements **neither** method, the helper auto-snapshots it so a new agent is tracked with no extra code:

- **`tracking_input` fallback:** iterate `dataclasses.fields(deps)`; for each field, emit `<name>` (scalars) or `<name>_count`/`<name>_chars` (collections/strings); **skip the type denylist** below; truncate every value to `MAX_ATTR_CHARS`.
- **`tracking_input_full` fallback:** same iteration, but emit values verbatim (still denylist + `MAX_EVENT_CHARS` cap + scrubber).
- **Type denylist (never serialized):** `supabase.Client`, `httpx.AsyncClient`/`Client`, anything `callable`, `asyncio.*`, names starting with `_`. These are infra/sinks (`supabase`, `http_client`, `emit_sse`, `_events`) — dumping them crashes or leaks nothing useful.

So: implement the methods to **curate**; rely on the fallback to **bootstrap**.

---

## Canonical attribute namespace (the contract every reader depends on)

| Attribute | Type | Source | Notes |
|---|---|---|---|
| `conversation_id` | str | deps / caller | **Mandatory.** The universal pivot. |
| `case_id` | str\|null | deps / caller | |
| `agent_family` | str | caller | `router` \| `deep_search` \| `writing` \| `memory` \| `publish` |
| `subtype` | str\|null | caller | e.g. `contract` |
| `stage` | str | caller | `<family>.<stage>` — also the span name |
| `turn_number` | int\|null | deps | |
| `run_id` | str\|null | set post-`agent_runs.record` when available | |
| `input.<k>` | scalar | `tracking_input()` / fallback | bounded only; truncated |
| `output.<k>` / `output_json` | scalar/str | `tracking_output()` or guarded `model_dump()` | capped `MAX_OUTPUT_JSON_CHARS` |
| `output_type` | str | `type(result.output).__name__` | cheap, every run |
| `output_schema_ref` | str | `f"{stage}@{sha8(schema)}"` | points at the once-dumped schema |
| `tokens_in` / `tokens_out` / `tokens_reasoning` | int | `result.usage()` | reasoning tracked separately (providers bill it) |
| `cache_hit_tokens` | int\|null | `result.usage()` details | provider-dependent |
| `requests` | int | `result.usage()` | model round-trips |
| `cost_usd` | float | `estimate_run_cost(...)` | tier-accurate via registry |
| `model_used` | str | agent model / `get_agent_model` slot | |
| `duration_ms` | int | helper clock | |
| `outcome` | str | helper | `ok` \| `empty` \| `paused` \| `error` \| `cancelled` |
| `degraded` | bool | output/result | optional |
| `error` / `error.type` | str | exception | on failure only |

**PII rules (carried from `shared/observability.py`):**
- `user_id` is **never** stamped on a span (recoverable via Supabase join). Helper drops it even if present on deps.
- All values pass through the configured Logfire scrubber (`_PII_EXTRA_PATTERNS` — Saudi national IDs, phones, emails, iqama).
- Raw query/prompt text is **never** a span *attribute* — only char-counts in `input.*`. Verbatim content lives **only** in the env-gated full-content event (below).

---

## The full-content sink — span event, env-gated, OFF by default

`tracking_input_full()` output is too large and too sensitive for span attributes. It is emitted as a **log record parented to the active span** (a "span event") and is **disabled in production by default**.

### How it's emitted

Inside the open span the helper calls:

```python
if _VERBOSE:
    get_logfire().info("agent.input.full", **_truncate(payload, MAX_EVENT_CHARS))
```

`logfire.info(...)` while a span is active creates a child log record on that span — visible in the trace next to the stage, queryable as its own record, scrubbed by the same scrubber. When the gate is off, **`tracking_input_full()` is never even called** — zero cost, zero PII exposure.

### How it's turned off (and on)

Single env var, read **once** at module import into a module-level bool:

```python
# agents/utils/tracking.py
_VERBOSE = os.getenv("LUNA_TRACK_VERBOSE", "").strip().lower() in {"1", "true", "yes", "on"}
```

| `LUNA_TRACK_VERBOSE` | Behavior |
|---|---|
| unset / `0` / `false` / empty | **OFF (default, production).** No full-content events. `tracking_input_full()` not called. |
| `1` / `true` / `yes` / `on` | ON (local/dev/debug). Full-content events emitted, scrubbed, capped. |

- **Production (Railway):** the variable is left **unset** → off. No code change needed to keep it off; it is off unless someone explicitly sets it.
- **Local debugging:** `LUNA_TRACK_VERBOSE=1` in `.env` or the shell before launching the backend / a CLI.
- **Kill switch:** `LUNA_TRACK_DISABLE=1` no-ops the entire tracking layer (helper becomes a pass-through that still runs the agent) — defense for incident response. Independent of `_VERBOSE`.
- Because `get_logfire()` already degrades to a no-op when Logfire/`LOGFIRE_TOKEN` is absent, the verbose event also silently no-ops in tokenless dev even if the flag is on.

---

## Output schema — dumped once, referenced per span

Schemas are byte-identical every run; re-serializing them per span is waste.

- **Startup:** a registry maps `stage → output_type`. On first sight of each stage (lazy, thread-safe), the helper computes `schema = output_type.model_json_schema()`, writes it to `agents_reports/schemas/<stage>.json`, and memoizes `output_schema_ref = f"{stage}@{sha8(json)}"`.
- **Per span:** stamp only `output_type` (class name) + `output_schema_ref`. A schema change flips the hash → new file → detectable in diffs, without bloating spans.
- Non-pydantic outputs (rare) skip the schema ref; `output_type` still recorded.

---

## Span-name + Agent-name conventions

- **Span name = `stage` = `<family>.<stage>`.** Dotted namespace (deep_search already does this).
- **Every `Agent(...)` MUST set `name=`.** Today some do (`router_agent`, `writer_planner_decider`) and some don't (writing_executor logs as the generic `agent_name='agent'`). The auto `agent run` child span is only identifiable when the Agent is named. Convention: Agent `name=` matches the stage's leaf (`writer_executor`, `aggregator`, …).

---

## Coverage map — every existing stage

| Current span | File | conv_id today | Canonical `stage` | Entry point | Action |
|---|---|---|---|---|---|
| `message.stream` | message_service.py:296 | ✅ | `turn.stream` | `track_stage` | align attrs (keep name as alias) |
| `router.classify` | router/router.py:586 | ✅ | `router.classify` | `run_tracked` | migrate to helper |
| `dispatch.specialist` | orchestrator.py:1008 | ✅ | `dispatch.specialist` | `track_stage` | align attrs |
| `deep_search.run_full_loop` | ds_v4/orchestrator.py:688 | ✅ | `deep_search.run_full_loop` | `track_stage` | align attrs |
| `deep_search.phase.{reg,case,compliance}` | ds_v4/orchestrator.py:255,428,552,658 | ✅ | same | `track_stage` | align attrs |
| `deep_search.planner` | ds_v4/planner/runner.py:182 | ✅ | `deep_search.planner` | `run_tracked` | migrate |
| `deep_search.aggregator` | ds_v4/orchestrator.py:801 | ✅ | `deep_search.aggregator` | `run_tracked` | migrate |
| `deep_search.sector_picker` | ds_v4/sector_picker/runner.py:80 | ✅ | `deep_search.sector_picker` | `run_tracked` | migrate |
| *(none)* | writer_planner | ❌ | **`writer.plan`** | `run_tracked` | **ADD (P1)** |
| *(none, Agent unnamed)* | writer/runner (executor) | ❌ | **`writer.execute`** | `run_tracked` | **ADD (P1) + name Agent** |
| `publish.workspace_item` | writer/publisher.py:311, agent_search/publisher.py:157 | ✅ | `publish.workspace_item` | `track_stage` | align attrs |
| `item_analyzer.{analyze,refs,meta}` | memory/item_analyzer/runner.py | ✅ | `memory.item_analyzer.{...}` | `run_tracked`/`track_stage` | rename + migrate |
| `artifact_summarizer.{run,run_attachment}` | memory/artifact_summarizer/runner.py | ❌ | `memory.artifact_summarizer.{...}` | `run_tracked` | **ADD conv_id + migrate** |
| `summarize_workspace_item` | memory/summarize.py:353 | ❌ | `memory.summarize` | `track_stage` | **ADD conv_id + rename** |
| `ocr_extraction.run` | memory/ocr_extractor/runner.py:130 | ✅ | `memory.ocr` | `track_stage` | rename (optional) |
| `agent_runs.record` / `.update_status` | runs.py:136,267 | ✅ / ❌ | keep | n/a | leave (DB-write spans) |

Renames are cosmetic and **P-low**; the load-bearing changes are (a) add `conversation_id` everywhere, (b) name every Agent, (c) route through the helper for uniform input/output/resource/outcome attrs.

---

## Build plan (phased)

- **P0 — Foundation.** ✅ DONE 2026-05-28. `agents/utils/tracking.py` (in `agents/utils`, NOT `shared/` — `shared` must not import `agents`): `_VERBOSE`/`_DISABLE` gates, `Trackable` protocol, reflective fallback (+ denylist), `run_tracked`, `track_stage`, `AgentSpan` handle, schema registry, truncation helpers, size constants (`MAX_ATTR_CHARS=512`, `MAX_OUTPUT_JSON_CHARS=4000`, `MAX_EVENT_CHARS=20000`). Tests in `agents/utils/tests/test_tracking.py` (19 passing).
- **P1 — Close the motivating gap.** ✅ DONE 2026-05-28. `writer_planner/runner.py` wraps the decider in `track_stage("writer.plan", …)` + `record_run(slot="writer_planner_decider")`; `writer/runner.py::_run_writer` wraps the executor (primary→fallback) in `track_stage("writer.execute", …)` + `record_run(slot="agent_writer")`. Executor `Agent` now `name="writer_executor"`. `WriterDeps` gained `conversation_id`/`case_id` + `tracking_input`/`_full`; `WriterPlannerDeps` gained `tracking_input`/`_full`; `PlannerDecision` + `WriterLLMOutput` gained `tracking_output`.
- **P2 — deep_search.** ✅ DONE 2026-05-28. `orchestrator.py` (`run_full_loop`, `phase.{reg,case,compliance}`, `aggregator`), `planner/runner.py` (`planner`), `sector_picker/runner.py`, `cli.py` migrated to `track_stage`. Manual `__enter__/__exit__` converted to `with`. Names kept. No `record_run` (these wrap `handle_*_turn` helpers, not a single direct `agent.run`); the `_logfire.current_span()` attr writes in `_run_planner_turn` now target the active `track_stage` span.
- **P3 — memory.** ✅ DONE 2026-05-28. `item_analyzer` (+`record_run` slot=item_analyzer on refs/meta), `artifact_summarizer` (+`record_run`, **conv_id added** via `getattr(input,"conversation_id",None)` — resolves to None until the input model carries it), `summarize.py` (**conv_id added** from the fetched row; name kept), `ocr_extractor` migrated. Body-set outcomes routed through `set_outcome(...)` so the finalizer doesn't clobber them.
- **P4 — Edges.** ✅ PARTIAL 2026-05-28. `router.classify` (swallow+fallback preserved; `record_run` slot=router) + `publish.workspace_item` (writer + agent_search) migrated. **Intentionally NOT migrated:** `message.stream` (backend turn root) + `dispatch.specialist` (orchestrator wrapper, carries the `_SkipRunRecord` control-flow exception `track_stage` would misclassify) — both already carry conv_id. `agent_runs.record`/`update_status` left as-is (DB-write audit spans, not agent trackers).
- **Span renames deferred.** Names kept identical (e.g. `summarize_workspace_item`, `item_analyzer.*`) to avoid breaking downstream consumers keyed on the existing span names. The `<family>.<stage>` rename is a separate low-risk follow-up.
- **P5 — Enforcement.** ✅ DONE 2026-05-28. `agents/utils/tests/test_tracking_enforcement.py` (15 tests): (1) AST — every production `Agent(...)` has `name=` (all 18 already do); (2) the 13 migrated tracked-stage modules each import `agents.utils.tracking` + reference `track_stage`/`run_tracked`; (3) tripwire — any module awaiting `*agent.run(` must import the helper OR be in `LAYERED_ALLOWLIST` (4 entries: the helper + aggregator/runner + case_search/reranker + reg_search/reranker, each tracked by an enclosing `deep_search.*` span). Negative-control confirmed the tripwire bites.

---

## Testing

- **Reflective fallback:** a synthetic dataclass with a Supabase client + httpx client + callable + big string → asserts clients/callables dropped, string truncated, scalars kept.
- **Verbose gate:** with `_VERBOSE=False`, assert `tracking_input_full` is **not called** and no `agent.input.full` record emitted; with `True`, assert it is, scrubbed + capped.
- **Outcome classification:** `DeferredToolRequests` → `paused`; raised `CancelledError` → `cancelled` (re-raised); generic exception → `error` + `error.type` (re-raised); normal → `ok`.
- **Resource extraction:** a `FunctionModel`/`TestModel` run → tokens + cost stamped, `cost_usd` matches `estimate_run_cost`.
- **Schema ref stability:** same output_type → same `output_schema_ref` across runs; changed schema → changed hash.
- Use the existing `TestModel`/`FunctionModel` harness (see `agents/.claude/agents/@pydantic-ai-validator` conventions).

---

## Downstream effects

- **Conversation-scoped queries:** once `conversation_id` is on every stage span, a single `WHERE attributes->>'conversation_id' = '<id>'` pull returns the full tree for *all* families, not just deep_search — and `input.*` / `output.*` / `agent.input.full` carry each stage's context inline.
- **Cost rollups:** `cost_usd` + `model_used` uniform on every stage span → cost can be summed straight from Logfire without per-family special-casing.
- **Dashboards:** one attribute schema → group/filter by `agent_family` / `stage` / `outcome` across the whole pipeline.

---

## Open / deferred

- **Permanent "what it saw":** if the env-gated event proves valuable enough to keep beyond 30 days, promote the same `tracking_input_full()` payload to a Supabase side-table — **no change to the per-agent functions**, only a second sink in the helper.
- **Custom metrics** (counters/histograms — cost histogram, cache-hit gauge) are out of scope here; this protocol is spans + events only.
