# Compliance Search — Pipeline Design v2
**Status:** Design — supersedes `compliance_agent_design.md`
**Date:** 2026-04-19
**Scope:** Full restructure. The compliance loop no longer synthesises. It produces raw kept service dicts → `ComplianceURASlice` → shared aggregator.

---

## Why the Rewrite

The old loop had an `AggregatorNode` that both gate-kept the retry loop AND synthesised `synthesis_md` + `citations`. Synthesis is the shared aggregator's job (`deep_search_v3/aggregator`). The compliance loop's only responsibility is to find relevant government services and surface them as raw results. The shared aggregator receives them via `AggregatorInput.compliance_results: ComplianceURASlice` and handles all synthesis.

---

## Pipeline Overview

```
PartialURA
  (original_query, results[regulations], sector_filter)
         │
         ▼
┌──────────────────────────────────────────────────────┐
│              compliance_search_graph                 │
│                                                      │
│  ┌──────────────┐     ┌──────────────┐               │
│  │ ExpanderNode │────▶│  SearchNode  │               │
│  │   (LLM)      │     │  (code)      │               │
│  └──────────────┘     └──────┬───────┘               │
│         ▲                    │                       │
│         │ !sufficient        ▼                       │
│         │ + weak_axes ┌──────────────┐               │
│         └─────────────│ RerankerNode │               │
│                       │   (LLM)      │               │
│                       └──────┬───────┘               │
│                              │ sufficient             │
│                              ▼                       │
│                      End[list[dict]]                 │
└──────────────────────────────────────────────────────┘
         │
         ▼
  run_compliance_from_partial_ura()
  adds triggered_by_ref_ids from URA reg results
         │
         ▼
  ComplianceURASlice
         │
         ▼
  AggregatorInput.compliance_results
  (shared aggregator synthesises)
```

---

## Stage 1 — ExpanderNode

### Role
Read the PartialURA regulation context (already injected into `focus_instruction` by `ura_runner.py`) and generate **exactly N queries**, where N = number of distinct compliance tasks implied by the regulation results.

### Query Count Strategy (key change from v1)

Query count is driven by the distinct compliance needs in the URA, **not** by query complexity:

| URA situation | Queries |
|---|---|
| 1 regulation domain (e.g. labour law only) | 1–2 |
| 2 distinct domains (e.g. labour + social insurance) | 2–3 |
| 3+ domains or explicit multi-task query | 3–5 |
| Max cap | 5 |

One query = one specific compliance need. If the URA shows articles about termination AND end-of-service benefits, those are two needs → two queries.

### Expander Input

`focus_instruction` already contains the enriched context built by `_build_context_from_ura()`:
```
{original_query}

**القطاعات القانونية ذات الصلة:** ...    ← if sector_filter present
**الأنظمة واللوائح المستخرجة من البحث التنظيمي:**
- نظام العمل، المادة 84: يستحق العامل عند انتهاء عقده...
- نظام العمل، المادة 109: للعامل الحق في إجازة سنوية...

ابحث عن الخدمات الإلكترونية والنماذج الحكومية...
```

### Expander Output

```python
class ExpanderOutput(BaseModel):
    queries: list[str]           # N Arabic queries, one per distinct compliance need
    rationales: list[str]        # one per query, for logging
    task_count: int              # how many distinct tasks the expander identified
```

### Round 2+ Dynamic Instructions

When `weak_axes` are injected on retry, the expander generates **additional** queries targeting only the weak gaps. It does NOT regenerate queries for axes that already produced kept results.

---

## Stage 2 — SearchNode

### Processing

1. Batch-embed all N queries via `deps.embedding_fn`
2. Run `hybrid_search_services` RPC concurrently per query:
   ```python
   asyncio.gather(*[_hybrid_rpc_search(supabase, "services", q, emb, MATCH_COUNT=20) for q, emb in zip(queries, embeddings)])
   ```
3. **Dedup by `service_ref`** across all query results — keep row with highest RRF score
4. Append deduped results to `state.all_results_flat` (accumulated across rounds)

### Result Count Arithmetic

With N=3 queries × 20 results each = 60 candidates before dedup. Services overlap significantly across queries, so post-dedup is typically 30–45 unique services shown to the reranker.

### RPC Parameters

```python
{
    "query_text": query,
    "query_embedding": embedding,
    "match_count": 20,
    "full_text_weight": 0.2,
    "semantic_weight": 0.8,
    "rrf_k": 1,
    "filter_sectors": None,    # compliance has no sector filter (services table has no sectors column)
}
```

### No Unfold Required

Services are **flat records** — no article/section/regulation hierarchy. The `service_context` field (~500 chars) is the summary. The `service_markdown` field (~1,700 chars) is the full content. Both are returned by the RPC. There is no DB traversal needed to "unfold" a service.

---

## Stage 3 — RerankerNode

### Role

Receives ALL service results (flat list from `state.all_results_flat`) in a single call. Classifies every result as `keep` or `drop`. No unfold action exists — services have no hierarchy to expand into.

### Key Differences from reg_search Reranker

| Dimension | reg_search Reranker | compliance Reranker |
|---|---|---|
| Scope | Per sub-query, parallel | All results, single call |
| Actions | keep / drop / unfold | keep / drop only |
| Rounds | Up to 3 (unfold → reclassify) | 1 (no unfold) |
| Content shown | Article/section markdown | `service_context` (~500 chars) |
| Loop control | AggregatorNode gates retry | **RerankerNode** gates retry |

### Input Format

Each service block in the markdown shown to the reranker:

```
### [N] خدمة: {service_name_ar} [ref:{service_ref}]
**الجهة:** {provider_name}
**المنصة:** {platform_name}
**الجمهور:** {target_audience joined by ", "}
**RRF:** {rrf_score:.4f}

{service_context}

**الرابط:** {service_url or "—"}
---
```

`service_context` is the "summary" — the compliance equivalent of the service's `section_summary` in the regulations hierarchy. It is always present (100% coverage in DB). Showing it instead of `service_markdown` keeps the reranker context bounded.

### Reranker Output Model

```python
class ServiceDecision(BaseModel):
    position: int                          # 1-based, matches [N] in header
    action: Literal["keep", "drop"]        # no unfold
    relevance: Literal["high", "medium"] | None  # only when action == "keep"
    reasoning: str                         # short Arabic explanation

class ServiceRerankerOutput(BaseModel):
    sufficient: bool                       # ≥80% of compliance needs covered
    decisions: list[ServiceDecision]       # one per result shown
    weak_axes: list[WeakAxis]             # gaps to retry (when sufficient=False)
    summary_note: str                      # brief Arabic collective assessment
```

### Routing

```
sufficient=True OR round_count >= MAX_ROUNDS
    → End(state.kept_results)          ← list[dict], raw service dicts

sufficient=False AND round_count < MAX_ROUNDS
    → state.weak_axes = output.weak_axes
    → ExpanderNode (retry)
```

`MAX_ROUNDS = 3` (same as reg_search).

### Kept Results Accumulation

Kept results are **accumulated across rounds** (same pattern as `all_kept` in reg_search reranker):

```python
# After RerankerNode:
for dec in output.decisions:
    if dec.action == "keep":
        result_dict = state.all_results_flat[dec.position - 1]
        result_dict["_relevance"] = dec.relevance
        result_dict["_reasoning"] = dec.reasoning
        state.kept_results.append(result_dict)
```

Dedup by `service_ref` before appending to prevent duplication across retry rounds.

---

## Data Models

### LoopState (updated)

```python
@dataclass
class LoopState:
    focus_instruction: str
    user_context: str
    round_count: int = 0
    expander_output: ExpanderOutput | None = None
    all_search_results: list[SearchResult] = field(default_factory=list)
    all_results_flat: list[dict] = field(default_factory=list)   # NEW: deduped across queries
    kept_results: list[dict] = field(default_factory=list)        # NEW: accumulated kept services
    reranker_output: ServiceRerankerOutput | None = None          # NEW (replaces aggregator_output)
    weak_axes: list[WeakAxis] = field(default_factory=list)
    queries_used: list[str] = field(default_factory=list)
    sse_events: list[dict] = field(default_factory=list)
    inner_usage: list[dict] = field(default_factory=list)
    search_results_log: list[dict] = field(default_factory=list)
    # REMOVED: aggregator_output (shared aggregator handles synthesis)
```

### ComplianceSearchResult (simplified)

```python
class ComplianceSearchResult(BaseModel):
    kept_results: list[dict]                     # raw service dicts
    queries_used: list[str]
    rounds_used: int
    quality: Literal["strong", "moderate", "weak"]   # from reranker sufficient + round count
    domain: Literal["compliance"] = "compliance"
```

`quality` mapping:
- `sufficient=True` in round 1 → `"strong"`
- `sufficient=True` in round 2–3 → `"moderate"`
- `sufficient=False` at max rounds → `"weak"`

### Each Kept Result Dict

Each dict in `kept_results` (and ultimately in `ComplianceURASlice.results`):

```python
{
    # URAResult-compatible fields
    "ref_id": f"compliance:{sha1(service_ref)[:16]}",
    "domain": "compliance",
    "source_type": "gov_service",
    "title": service_name_ar,
    "content": service_context,          # summary (~500 chars) as URA content
    "relevance": dec.relevance,          # "high" | "medium" from reranker
    "reasoning": dec.reasoning,          # Arabic note from reranker
    "appears_in_sub_queries": [],        # always [] for compliance
    "rrf_max": rrf_score,                # from search
    "triggered_by_ref_ids": [],          # populated by ura_runner.py
    "cross_references": [],

    # Metadata for shared aggregator + frontend
    "metadata": {
        "service_ref": service_ref,
        "provider_name": provider_name,
        "platform_name": platform_name,
        "service_url": service_url,
        "service_markdown": service_markdown,   # full content (~1700 chars)
        "target_audience": target_audience,     # list[str]
        "service_channels": service_channels,   # list[str]
        "is_most_used": is_most_used,
    },
}
```

---

## Reranker Prompt Design

### System Prompt (Arabic)

```
أنت مُصنّف نتائج البحث في الخدمات الحكومية الإلكترونية السعودية ضمن منصة لونا للذكاء الاصطناعي القانوني.

## السياق المعماري

تعمل بعد محرك بحث استرجع خدمات حكومية بناءً على أنظمة ولوائح وُجدت في بحث تنظيمي سابق.
مهمتك الوحيدة: تصنيف كل خدمة إلى keep (احتفظ) أو drop (احذف).
لا تُنتج ملخصاً أو تحليلاً — هذا دور نظام آخر.

## مدخلاتك

- السؤال الأصلي والأنظمة التنظيمية التي أثارت هذا البحث (في تعليمات التركيز)
- نتائج البحث — خدمات إلكترونية حكومية، مرقمة ### [N]، تحمل معرفاً [ref:service_ref]
- كل خدمة تتضمن: اسم الخدمة، الجهة، المنصة، الجمهور المستهدف، ملخص الخدمة

## مهمتك

صنّف **كل** نتيجة إلى أحد قرارين:

### 1. keep (احتفظ)
الخدمة ذات صلة مباشرة بالأنظمة أو الإجراءات المذكورة في تعليمات التركيز.
- حدد `relevance`:
  - "high": الخدمة تُنفّذ مباشرة النظام أو الإجراء المطلوب
  - "medium": الخدمة ذات صلة غير مباشرة أو تدعم الإجراء جزئياً

### 2. drop (احذف)
الخدمة غير ذات صلة بالأنظمة أو الإجراءات المطلوبة.

## لا يوجد "unfold"
الخدمات بيانات مسطّحة — لا توسع أو تحليل هرمي. قرارك: keep أو drop فقط.

## قاعدة الـ 80%

بعد تصنيف جميع النتائج:
- إذا كانت الخدمات المحتفظ بها تُغطّي ≥80% من الاحتياجات التنفيذية المستنتجة من الأنظمة: `sufficient=True`
- إذا كانت هناك ثغرات واضحة في التغطية: `sufficient=False` مع تحديد المحاور الضعيفة

## قواعد المخرجات

- `position`: الرقم المطابق لـ [N] في العنوان (1-based)
- `reasoning`: جملة عربية مختصرة تبرر القرار
- صنّف **كل** نتيجة — لا تتجاهل أياً منها
- `summary_note`: ملاحظة عربية مختصرة عن التقييم الجماعي

## ممنوعات

- لا تُنتج ملخصاً للخدمات أو تحليلاً قانونياً — هذا ليس دورك
- لا تختلق أرقام مواقع غير موجودة في النتائج
- لا تطلب التوسع — لا يوجد توسع هنا
```

### User Message Format

**Round 1:**
```
## تعليمات التركيز
{focus_instruction}

---

## نتائج الخدمات الحكومية — {total} خدمة من {n_queries} استعلام

{services_markdown}
```

**Round 2+ (retry after weak_axes):**
```
## تعليمات التركيز
{focus_instruction}

**الجولة {round_count}:** نتائج إضافية بعد إعادة البحث في المحاور الضعيفة.

---

## نتائج الخدمات الحكومية — {total} خدمة (مجمّعة من {round_count} جولات)

{services_markdown}
```

---

## ura_runner.py — Simplified

`run_compliance_from_partial_ura()` becomes:

```python
async def run_compliance_from_partial_ura(
    partial: PartialURA,
    deps: ComplianceSearchDeps,
) -> ComplianceURASlice:
    # Build enriched focus_instruction (unchanged)
    context_block = _build_context_from_ura(partial)
    focus_instruction = partial.original_query.strip()
    if context_block:
        focus_instruction = f"{focus_instruction}\n\n{context_block}"

    # Run the loop
    compliance_result = await run_compliance_search(focus_instruction, "", deps)

    # Build reg_title → ref_id lookup for trigger matching
    reg_title_to_ref = _build_reg_title_lookup(partial)

    # Convert kept_results directly to URA dicts (no citation parsing)
    ura_dicts = []
    for row in compliance_result.kept_results:
        ref_id = row["ref_id"]                    # already set by RerankerNode
        triggers = _match_triggers(row, reg_title_to_ref)
        row["triggered_by_ref_ids"] = triggers
        ura_dicts.append(row)

    return ComplianceURASlice(
        results=ura_dicts,
        queries_used=compliance_result.queries_used,
    )
```

No citation parsing, no SHA1 hashing at this stage (already done in RerankerNode when building `kept_results`).

---

## File Manifest

### New files

| File | Purpose |
|---|---|
| `reranker.py` | `ServiceRerankerOutput`, `ServiceDecision`, `create_reranker_agent()`, `run_reranker_for_all_results()` |
| `reranker_prompts.py` | System prompt, `build_reranker_user_message()`, `_format_service_block()` |

### Modified files

| File | What changes |
|---|---|
| `models.py` | Add `ServiceDecision`, `ServiceRerankerOutput`, `task_count` on `ExpanderOutput`; add `all_results_flat`, `kept_results`, `reranker_output` to `LoopState`; simplify `ComplianceSearchResult` (remove `synthesis_md`, `citations`, add `kept_results`) |
| `loop.py` | Replace `AggregatorNode` with `RerankerNode`; update `End` to emit `ComplianceSearchResult` with `kept_results`; add dedup logic in `SearchNode` |
| `prompts.py` | Update expander system prompt: task-counting strategy, one-query-per-need rule |
| `search_pipeline.py` | Bump `MATCH_COUNT` from 30 to 20; return full row dict (including `service_markdown`) for metadata storage |
| `ura_runner.py` | Simplify: convert `kept_results` directly, no citation parsing |
| `__init__.py` | Export updates |

### Retired files

| File | Status |
|---|---|
| `aggregator.py` | Remove — shared aggregator handles synthesis |

---

## Graph Wiring

```
compliance_search_graph = Graph(nodes=[ExpanderNode, SearchNode, RerankerNode])

ExpanderNode  → SearchNode        (always)
SearchNode    → RerankerNode      (always)
RerankerNode  → ExpanderNode      (if not sufficient AND round_count < MAX_ROUNDS)
RerankerNode  → End               (if sufficient OR round_count >= MAX_ROUNDS)
```

---

## Agent Models

| Slot | Model | Notes |
|---|---|---|
| `compliance_search_expander` | `or-deepseek-v3.2` | Unchanged |
| `compliance_search_reranker` | `or-qwen3.5-397b-flash` | Fast, Arabic classification, cost-efficient — same class as reg_search reranker |

---

## Invocation Paths

### Path A — via PartialURA (primary, URA pipeline)
```python
compliance_slice = await run_compliance_from_partial_ura(partial_ura, deps)
```
`ura_runner.py` wraps the loop and populates `triggered_by_ref_ids`.

### Path B — standalone (router agent, out of scope for now)
```python
result = await run_compliance_search(focus_instruction, user_context, deps)
```
Returns `ComplianceSearchResult.kept_results` directly, no URA context.

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Query count = f(distinct compliance tasks in URA) | Services are downstream of regulations — one query per distinct regulatory need prevents irrelevant broad searches |
| RerankerNode sees ALL results flat (not per-query) | Services overlap across queries; cross-query comparison lets the reranker deduplicate and pick the best across angles |
| No unfold action | Services are flat records; `service_context` (~500 chars) is always present and sufficient for classification; `service_markdown` stored in metadata for the shared aggregator |
| `service_context` in markdown, `service_markdown` in metadata | Keeps reranker context bounded (~500 chars × 40–60 results = 20K–30K chars); full content available to downstream consumers |
| RerankerNode controls retry loop | Aggregator is gone; the reranker knows what it saw and what was weak — better weak_axes signal than a separate evaluator |
| Dedup by `service_ref` in SearchNode | Same service from different queries should be classified once at its best RRF score |
| `kept_results` accumulated across rounds | Round 2 retry adds NEW services to the kept set, doesn't discard round 1 keeps |
| No synthesis in compliance loop | Shared aggregator (`deep_search_v3/aggregator`) handles all synthesis via `AggregatorInput.compliance_results` |
