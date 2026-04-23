# Compliance Agent — Design & Role in the Loop

## Position in the Pipeline

```
reg_search graph
    └─ reranker_results
           │
           ▼
    merge_partial_ura()  →  PartialURA
                                │
                                ▼
           run_compliance_from_partial_ura()   ◄──  [this agent]
                                │
                                ▼
                        ComplianceURASlice
                                │
                                ▼
                         merge_to_ura()  →  UnifiedRetrievalArtifact
```

The compliance agent runs **between** reg_search and the final URA merge. It is gated: if `PartialURA.results` is empty, it is skipped entirely.

---

## Input: PartialURA

| Field | What it carries |
|---|---|
| `original_query` | The raw user query (Arabic) |
| `results` | Regulation articles kept by the reranker (high/medium/low relevance) |
| `sector_filter` | Canonical sector names extracted from the expander (e.g. `["العمل والتوظيف"]`) — present only when `ExpanderOutput.sectors` was non-null |
| `sub_queries` | The sub-queries used in reg_search (for context, not directly used here) |

---

## Context Block Passed to the Expander

`_build_context_from_ura()` assembles an Arabic context string appended to the user's original query before the compliance loop starts:

```
[original_query]

**القطاعات القانونية ذات الصلة:** العمل والتوظيف        ← if sector_filter present

**الأنظمة واللوائح المستخرجة من البحث التنظيمي:**
- نظام العمل، المادة 84: يستحق العامل عند انتهاء عقده...  ← top 12 reg hits, high-first
- نظام العمل، المادة 109: للعامل الحق في إجازة سنوية...

ابحث عن الخدمات الإلكترونية والنماذج الحكومية التي تُنفِّذ هذه الأنظمة...
```

This makes the compliance expander regulation-aware without knowing about the URA schema.

---

## The Loop: ExpanderNode → SearchNode → AggregatorNode

```
focus_instruction  (query + regulation context + sector header)
        │
        ▼
  ExpanderNode  [round 1..N]
    - generates 2–4 service-search queries in Arabic
    - on round 2+: injects weak_axes from previous aggregator feedback
    - queries target: e-government services, ministry forms, platforms (Qiwa/Absher/Musaned/Masaar…)
        │
        ▼
  SearchNode
    - runs all queries concurrently via hybrid_search_services()
    - embed query → RPC: hybrid_search_services(query_text, embedding, rrf_k=60)
    - optional Jina reranker; score threshold filter
    - formats results as Arabic markdown
        │
        ▼
  AggregatorNode
    - evaluates sufficiency across axes: service coverage, form/procedure clarity, platform guidance
    - if sufficient=True → exit loop, return citations
    - if sufficient=False → report weak_axes, loop back to ExpanderNode
    - max 3 rounds
```

Max rounds = 3. If still insufficient after round 3, returns whatever citations were found (quality logged as `weak`).

---

## Output: ComplianceURASlice

```python
@dataclass
class ComplianceURASlice:
    results: list[dict]      # URA-shaped service result dicts
    queries_used: list[str]  # all queries fired across rounds
```

Each result dict:

| Key | Value |
|---|---|
| `ref_id` | `compliance:{sha1[:16]}` — deterministic hash of the citation ref string |
| `domain` | `"compliance"` |
| `source_type` | `"gov_service"` or `"form"` etc. |
| `title` | Service name (Arabic) |
| `content` | Short snippet |
| `triggered_by_ref_ids` | List of `reg:` ref_ids this service implements — populated by substring matching regulation titles in citation text |
| `relevance` | `"high"` if relevance note present, else `"medium"` |

---

## Integration: merge_to_ura()

`merge_to_ura(partial, compliance_slice)` concatenates `partial.results + compliance_slice.results` into `UnifiedRetrievalArtifact.results`. The `triggered_by_ref_ids` cross-references on compliance results point back to regulation `ref_id`s in the same URA, enabling the aggregator to build a regulation→service graph.

---

## Mock / Offline Mode

Set `ComplianceSearchDeps.mock_results = {"compliance": "...markdown..."}` to bypass the DB search phase. The expander LLM still runs (it generates real queries). Use for:
- Prompt design iteration
- Testing trigger matching logic
- Offline unit tests with known compliance markdown

Test entry: `python -m agents.deep_search_v3.test_compliance_ura --query-id 10`

---

## Design Rationale

The compliance agent is a **downstream enricher**, not a standalone search pipeline. Its authority comes from the regulation results: it uses them to focus on the services that *implement* those regulations rather than doing a general government-services search from scratch. This is why the loop input is a `PartialURA` rather than a raw query.

The sector_filter from the expander acts as a precision filter — when available, it tells the compliance expander which ministry/domain to prioritize, reducing hallucinated service suggestions outside the relevant sector.
