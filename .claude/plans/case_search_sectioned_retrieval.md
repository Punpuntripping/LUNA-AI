# Case Search — Sectioned Retrieval Migration Plan

## Why

Single-vector retrieval per ruling mixes principle-text with narrative-text in one embedding. The expander can't produce a query style that matches both. Test results confirm: query 16 regressed 15→12 vs prompt_1, and query 28 q2 (`امتداد الحجز التنفيذي إلى أموال حُوِّلت للزوجة`) is still 0/10 kept.

Fix: split each ruling into three semantic channels, embed each independently, filter by legal sector pre-retrieval, fuse channels via RRF. Query types (direct / step-back / decomposition) stop being stylistic variants in one space and become structural lookups against purpose-built spaces.

Scope: 8,368 cases in entity 17642 today. Re-embed cost ≈ $0.50 at text-embedding-3-small. Storage grows ~3× (~150 MB — negligible).

## Target architecture

```
User Q → Expander (sectors + typed queries)
            ↓
   [SQL pre-filter: legal_domains ∩ sectors ≠ ∅]
            ↓
   ┌────────┬────────┬────────┐
   │ المبدأ │ الوقائع │ اسانيد │     (each channel: independent vector search)
   └───┬────┴───┬────┴───┬────┘
       └────────┼────────┘
                ↓
      RRF fusion (designed later)
                ↓
   4-bucket output:
     • top of المبدأ channel
     • top of الوقائع channel
     • top of اسانيد channel
     • top of fused list (cross-channel consensus)
                ↓
         Per-query reranker
                ↓
         Aggregator
```

### Channel composition

Three channels. Text is raw (no LLM-extracted principles — that's a later optimization if measurement shows it's needed).

| Channel | Sections included | Rationale |
|---|---|---|
| **principle** (المبدأ) | تسبيب الحكم الابتدائي + تسبيب الاستئناف + منطوق الحكم الابتدائي + منطوق الاستئناف | Reasoning + outcome as one logical unit — "court reasoned X, therefore ruled Y" |
| **facts** (الوقائع) | الملخص + الوقائع + المطالبات | Case story arc — layperson scenario + formal claim |
| **basis** (اسانيد) | اسانيد المطالبة + اسانيد المدعى عليه + أسباب الاعتراض + رد المستأنف ضده + الأنظمة_المستخدمة (serialized) | All statutory/legal-basis text and procedural grounds |

Same section allocation for first_instance cases — just fewer source sections available. For case_variant = "both" (appeal + first_instance), both halves concatenate into the same channels.

## Work breakdown (5 waves)

---

### Wave 1 — Schema migration (Supabase, LUNA_AI repo)

New normalized table `case_sections`. Keep existing `cases.embedding` column during transition so we can A/B.

**Migration file:** `backend/migrations/XXX_case_sections.sql` (or wherever Supabase migrations land in LUNA_AI — TBD)

```sql
-- Enum for channel types
CREATE TYPE case_channel AS ENUM ('principle', 'facts', 'basis');

-- Sectioned embeddings table
CREATE TABLE case_sections (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id    UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    channel    case_channel NOT NULL,
    text       TEXT NOT NULL,
    embedding  VECTOR(1536),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (case_id, channel)
);

-- Metadata indexes
CREATE INDEX idx_case_sections_case ON case_sections(case_id);
CREATE INDEX idx_case_sections_channel ON case_sections(channel);

-- Per-channel HNSW vector indexes (partial indexes keep each channel searchable independently)
CREATE INDEX idx_case_sections_principle_vec
    ON case_sections USING hnsw (embedding vector_cosine_ops)
    WHERE channel = 'principle';
CREATE INDEX idx_case_sections_facts_vec
    ON case_sections USING hnsw (embedding vector_cosine_ops)
    WHERE channel = 'facts';
CREATE INDEX idx_case_sections_basis_vec
    ON case_sections USING hnsw (embedding vector_cosine_ops)
    WHERE channel = 'basis';

-- Keep cases.embedding column (for transition / fallback) — no change.
```

**New RPC:** `search_case_sections` — takes channel, embedding, legal_sector filter, match_count. Returns `(case_id, section_rank, score)`. Caller does the join to `cases` for metadata.

```sql
CREATE OR REPLACE FUNCTION search_case_sections (
    p_channel        case_channel,
    p_query_embedding VECTOR(1536),
    p_sectors        TEXT[] DEFAULT NULL,   -- NULL = no filter
    p_match_count    INT DEFAULT 30
)
RETURNS TABLE (
    case_id    UUID,
    case_ref   TEXT,
    score      REAL,
    section_text TEXT
)
LANGUAGE sql STABLE AS $$
    SELECT
        cs.case_id,
        c.case_ref,
        1 - (cs.embedding <=> p_query_embedding) AS score,
        cs.text
    FROM case_sections cs
    JOIN cases c ON c.id = cs.case_id
    WHERE cs.channel = p_channel
      AND cs.embedding IS NOT NULL
      AND (p_sectors IS NULL
           OR c.legal_domains ?| p_sectors)
    ORDER BY cs.embedding <=> p_query_embedding
    LIMIT p_match_count;
$$;
```

Notes:
- `?|` is the JSONB "any key exists" operator. `legal_domains` is stored as JSONB array, so this picks up rulings where any of the caller's sectors appears in the ruling's tags.
- Sector filter is optional — if the expander's confidence is low it passes NULL.
- No BM25 here initially. We can add a hybrid `search_case_sections_hybrid` RPC later if needed; starting pure-semantic because the channels are already narrower.

**Migration order:**
1. Create enum + table + metadata indexes (fast).
2. Run ingestion backfill (Wave 2) — populate rows but no vector indexes yet.
3. After all embeddings written: create HNSW indexes (creating HNSW on empty or sparse data is wasteful; do it last).
4. Create RPC.

---

### Wave 2 — Ingestion layer (agentic_for_ministry repo)

Three changes in `ingestion/cases/`:

**2a. New constants module** — `ingestion/cases/section_channels.py`

```python
"""Channel composition — which ndjson sections feed which embedding channel."""

PRINCIPLE_SECTIONS = (
    "تسبيب الحكم الابتدائي",
    "تسبيب الاستئناف",
    "منطوق الحكم الابتدائي",
    "منطوق الاستئناف",
    "تسبيب الحكم",      # first-instance-only format
    "منطوق الحكم",      # first-instance-only format
)

FACTS_SECTIONS = (
    "الملخص",
    "الوقائع",
    "المطالبات",
)

BASIS_SECTIONS = (
    "اسانيد المطالبة",
    "اسانيد المدعى عليه",
    "أسباب الاعتراض",
    "رد المستأنف ضده",
    # الأنظمة_المستخدمة handled separately (serialized from JSON array)
)

CHANNELS = {
    "principle": PRINCIPLE_SECTIONS,
    "facts": FACTS_SECTIONS,
    "basis": BASIS_SECTIONS,
}
```

**2b. New script** — `ingestion/cases/split_and_embed_sections.py`

Responsibilities:
- For each case row (from DB or fresh ndjson): build three channel-texts by concatenating the relevant sections with `## header` formatting (same convention `generate_cases_sql.py` uses).
- For channel `basis`, append a serialized form of `الأنظمة_المستخدمة` (e.g., each referenced regulation on its own line).
- Embed all three texts per case. Can batch across cases (e.g., 30 cases × 3 channels = 90 texts per batch).
- Insert into `case_sections` with ON CONFLICT (case_id, channel) DO UPDATE SET embedding, text.

CLI flags (mirror `embed_cases.py`):
- `--dry-run` — preview token count + cost
- `--limit N` — first N cases only
- `--force` — re-embed existing rows
- `--provider {openai,google}` — via `embedding.py`
- `--channels principle,facts,basis` — subset (default all three)

Rough structure:

```python
def build_channel_text(row, channel: str) -> str:
    parts = []
    for header in CHANNELS[channel]:
        val = row.get(header)
        if not val:
            continue
        text = "\n".join(str(x) for x in val) if isinstance(val, list) else str(val)
        if text.strip():
            parts.append(f"## {header}\n{text}")
    if channel == "basis":
        regs = row.get("الأنظمة_المستخدمة", [])
        if regs:
            serialized = "\n".join(json.dumps(r, ensure_ascii=False) for r in regs)
            parts.append(f"## الأنظمة_المستخدمة\n{serialized}")
    return "\n\n".join(parts)
```

This script operates against ndjson — matches how `generate_cases_sql.py` works today. For an existing DB backfill, a sister script reads from the DB `cases.content` (which is section-concatenated) and parses it back. Cleaner to re-read the ndjson.

**2c. Modify `generate_cases_sql.py`** — add a second SQL generator that emits `INSERT INTO case_sections` for each case, one row per channel with text filled and embedding NULL. Then `split_and_embed_sections.py` only handles the embedding PATCH step — symmetric with how cases ingestion works today.

Alternative: skip generate-SQL for case_sections entirely and have `split_and_embed_sections.py` do text-write + embedding in one pass. Simpler, fewer files. Recommend this alternative for the first pass.

**Cost:** 8,368 cases × 3 channels × avg 1.2k tokens/channel = ~30M tokens @ $0.02/1M = **~$0.60**. Wall clock: ~30-60 min at batch size 100.

**Document:** Update `ingestion/cases/PLAN.md` to explain the three-channel architecture. Keep the old "single embedding" section but mark it deprecated.

---

### Wave 3 — Retrieval layer (LUNA_AI/agents/deep_search_v3/case_search)

**3a. Models** — `models.py`

Add:
```python
class QueryChannel(str, Enum):  # or Literal
    PRINCIPLE = "principle"
    FACTS = "facts"
    BASIS = "basis"

class TypedQuery(BaseModel):
    text: str
    channel: Literal["principle", "facts", "basis"]
    rationale: str = ""
```

Modify `ExpanderOutput`:
```python
class ExpanderOutput(BaseModel):
    legal_sectors: list[str] = Field(default_factory=list)  # 1-4 sectors or []
    queries: list[TypedQuery]
```

Keep a compat shim that flattens to old shape during transition (for prompt_1 / prompt_2 users).

**3b. Search pipeline** — `search_pipeline.py`

New function `search_case_section(query: TypedQuery, sectors: list[str], deps) -> ChannelResults`:
- Embeds the query text.
- Calls `search_case_sections` RPC with `p_channel=query.channel`, `p_sectors=sectors or None`.
- Returns ranked list of `(case_id, rank, score)` for this channel.

The existing `search_cases_pipeline` (which calls `hybrid_search_cases`) stays in place as the legacy path — prompt_1 / prompt_2 continue to use it.

**3c. Fusion module** — new file `fusion.py`

```python
def rrf_fuse(channel_results: dict[str, list[ChannelRank]], k: int = 60) -> list[FusedRank]:
    """Reciprocal Rank Fusion across channel result lists.

    User to design final formula. Placeholder: standard RRF.
    Returns ranked list of case_ids with fused score and per-channel breakdown.
    """
    ...
```

The 4-bucket output (top-A, top-B, top-C, top-fused) is assembled here.

**3d. Loop** — `loop.py`

New `run_sectioned_case_search(...)`:
1. Call expander → `ExpanderOutput` with sectors + typed queries.
2. For each typed query: dispatch to `search_case_section` in parallel.
3. Group results by channel.
4. Call `rrf_fuse` → 4 buckets.
5. Pass each bucket to the reranker (or fuse to one list first — TBD in Wave 5 tuning).
6. Return `CaseSearchResult`.

Keep old `run_case_search` for legacy.

**3e. Reranker** — `reranker.py`

Minimal change: accept either the flat legacy list or the 4-bucket structure. Start by running the reranker on just the fused bucket (top-fused). Per-channel bucket reranking can come later if needed.

**3f. CLI** — `cli.py`

Add flags:
- `--sectioned` — use new pipeline (default once stable)
- `--channels principle,facts,basis` — restrict channels
- `--sectors X,Y` — override auto-sector classification for testing

---

### Wave 4 — Expander prompt (prompt_3)

**4a. Sector vocabulary** — `sector_vocab.py`

Mirrors `reg_search/sector_vocab.py`. Case-specific sectors drawn from the existing `legal_domains` tags actually present in the DB.

Starter list (to be validated against real tag distribution):
```
منازعات البيع والشراء
عقود تجارية
عقود المقاولات والإنشاءات
عقود الإيجار
قانون الشركات
الشراكة والاستثمار
الإفلاس والإعسار
التعويضات والأضرار
الإخلال العقدي
الملكية الفكرية
التحكيم
الأوراق التجارية
التنفيذ وإجراءات التنفيذ
القضايا العمالية
...
```

Need a one-shot SQL query against production to enumerate distinct `legal_domains` values before finalizing. Don't invent sector names.

**4b. prompt_3** — new variant in `prompts.py`

Key differences from prompt_2:
- **Sector classification** — mirror reg_search prompt_2. LLM picks 1-4 sectors or null.
- **Typed queries** — each query tagged with its channel at production time.
- **Channel-specific style guidance**:
  - `principle`: short doctrinal clause in تسبيب style (5-10 words). "من المقرر أن..."
  - `facts`: compressed case narrative (8-15 words). "دائن طالب بتنفيذ ضد مدين تصرف في أمواله"
  - `basis`: article/regulation-referencing query (5-12 words). "تطبيق المادة X من نظام Y على حالة Z"
- Drop the rare-details pruning table mostly — the sectioning + sector filter handle most of it structurally. Keep rare-amount-dropping since that's still a user-side noise issue.
- Drop "step-back is mandatory" — replace with "include at least 2 of the 3 channels per run".

Defaults:
- `EXPANDER_PROMPT_THINKING["prompt_3"] = "medium"`
- When we're confident it works, flip `DEFAULT_EXPANDER_PROMPT = "prompt_3"`.

Keep prompt_2 in the file (user requested). Only prompt_3 uses the new output shape.

**4c. Expander agent** — `expander.py`

The agent's `output_type` changes from `ExpanderOutput` (old shape) to `ExpanderOutput` (new shape with sectors + typed queries). Need a dispatch: prompt_1/prompt_2 → old shape, prompt_3 → new shape. Simplest way: introduce `ExpanderOutputV2` and a factory that picks the right output type based on prompt key.

---

### Wave 5 — Test + tune

**5a. Re-run existing test queries** (10, 16, 28) with prompt_3 + sectioned pipeline. Compare kept/sufficient metrics to prompt_1 and prompt_2.

**5b. Expand the test set.** Pick 10-15 additional queries from `test_queries.json` spanning different sectors.

**5c. Tune RRF weights** — user-owned. Start equal; adjust based on observed quality.

**5d. Short-section noise check** — compute median token count per channel across the DB. If `basis` channel median is < 30 tokens, flag as weakest channel and consider always-RRF-with-lower-weight.

**5e. Sector classifier accuracy** — sample 50 queries, hand-tag correct sectors, compare to LLM's classification. Target ≥80% recall (i.e., the correct sector is among the LLM's picks at least 80% of the time). If below, either widen LLM's pick count or add a "no-filter" fallback.

---

## Rollback / transition strategy

- Don't drop `cases.embedding` during migration. Legacy `hybrid_search_cases` RPC keeps working. prompt_1 / prompt_2 unaffected.
- `--sectioned` CLI flag is opt-in initially. Default stays legacy until Wave 5 metrics confirm.
- If sectioned retrieval underperforms, flip default back and keep the work as an available variant.
- Dropping `cases.embedding` happens only in a later wave (Wave 6?) after prompt_3 is proven dominant.

---

## Decisions needed before building

1. **Schema shape**: new `case_sections` table (proposed) vs. add 3 columns to `cases`. Recommend table.
2. **Sector source of truth**: reuse reg_search's SECTORS_PROMPT_LIST or make case-specific? Recommend case-specific — the legal domains in the case DB are different (more commercial-focused) than the regulation domains.
3. **RRF design**: user to specify. Plan has a placeholder `rrf_fuse`.
4. **Output shape per run**: return 4 buckets (top-A, top-B, top-C, top-fused) or just the fused list? Both are feasible; the 4-bucket version gives the reranker more signal but complicates the aggregator downstream.
5. **Ingestion backfill runner**: `split_and_embed_sections.py` reads from ndjson (simple, re-parses sections) or from DB (needs to parse `cases.content` markdown back into sections). Recommend ndjson.
6. **Where migration SQL lives**: is there an existing migrations/ folder in LUNA_AI or agentic_for_ministry? Need pointer.

---

## File-change manifest

### agentic_for_ministry/ (ingestion repo)
```
ingestion/cases/
  section_channels.py              NEW: channel → sections mapping
  split_and_embed_sections.py      NEW: per-channel text builder + batched embedding
  generate_cases_sql.py            (unchanged — or extended if we generate case_sections INSERTs here)
  PLAN.md                          UPDATE: document channel architecture
```

### LUNA_AI/ (app repo)
```
backend/migrations/
  XXX_case_sections.sql            NEW: enum + table + partial HNSW indexes
  XXX_search_case_sections.sql     NEW: RPC

agents/deep_search_v3/case_search/
  models.py                        UPDATE: TypedQuery, ExpanderOutputV2
  search_pipeline.py               UPDATE: search_case_section()
  fusion.py                        NEW: rrf_fuse() placeholder
  loop.py                          UPDATE: run_sectioned_case_search()
  reranker.py                      UPDATE: accept 4-bucket input
  prompts.py                       UPDATE: add prompt_3
  sector_vocab.py                  NEW: case-specific sector list
  expander.py                      UPDATE: dispatch output type by prompt key
  cli.py                           UPDATE: --sectioned, --channels, --sectors
  tests/                           UPDATE: new mocks + channel-aware tests
```

---

## Order of execution (critical path)

```
Wave 1 (schema)   ─────┐
                       ├──→ Wave 2 (ingestion backfill)   ──→  HNSW indexes  ─┐
                       │                                                      │
Wave 3 (models)  ──┐   │                                                      │
                   ├───┴──→ Wave 4 (prompt)  ──→  Wave 5 (test/tune) ────────┘
Wave 3 (search)  ─┘
```

Waves 1 + 2 are the biggest blockers (require DB write access + an evening to run the backfill). Wave 3 + 4 can start in parallel once the schema exists. Wave 5 is iterative, unbounded.

## Open risks

1. **Sector tag quality in the DB.** If `legal_domains` is inconsistent or missing on a meaningful fraction of cases, the sector pre-filter produces false negatives. Mitigation: run a DB audit early, fallback to NULL sectors when LLM is uncertain.
2. **HNSW index memory during rebuild.** 8k × 3 = 24k vectors × 1536 dims × 4 bytes = ~150MB raw, HNSW adds overhead. Should be fine on Supabase's default tier, but monitor during index build.
3. **Text truncation at 30k chars.** Current `embed_cases.py` already truncates content to 30k chars. Per-channel text will mostly be well under that limit, but تسبيب الاستئناف on long rulings might exceed. Sample before running backfill.
4. **Channel imbalance for first-instance-only cases.** Appeal sections are empty → `principle` channel text is shorter. Not a blocker but worth checking what fraction of rulings are first-instance-only.
5. **Sector classifier cold-start.** LLM won't know real distribution of legal_domains until we give it the actual list. Wave 4 depends on a DB audit of `legal_domains` values.
