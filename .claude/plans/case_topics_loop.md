# Case loop → `case_topics` (retarget + reranker/aggregator payload redesign)

**Status:** **BUILT 2026-07-24 — all 5 waves merged, 497 tests green. NOT committed, NOT deployed.**
End-to-end validation against the 27-query set has NOT been run yet (needs live embedding + LLM calls).
**Date:** 2026-07-24

---

## AS-BUILT — measured results

| Wave 0 metric | Before | After |
|---|---|---|
| top-60 ANN, `basis` (110k topics), warm | 21,619 ms | **144 ms** |
| top-60 ANN, `fact` (108k topics), warm | (timed out) | **11.6 ms** |
| rows returned for `p_match_count=60` | 44 ⚠ | **60** |
| distinct cases per sub-query | — | 54–60 (target ≥ 25) |

Three partial HNSW indexes live as **`idx_ct_vec_{principle,fact,basis}`** (470 / 835 / 840 MB).

**Dark-corpus recovery, verified:** a ZATCA (`40046`) basis probe returns 54 distinct cases, of
which **46 are cases absent from `case_sections`** — i.e. unreachable by the pre-Wave-1 pipeline.
The probe case surfaced via **2 matched topics**, exercising D1 end-to-end.

### Two corrections to this plan, found during the build

1. **§4.2 said "do not set `hnsw.ef_search` unless benchmarks show recall loss."** They do.
   pgvector HNSW returns at most `ef_search` candidates and the default is **40**, so
   `p_match_count = 60` silently returned **44 rows**. This reads at the call site as "the corpus
   had no more matches", not as a knob. The RPC now sets
   `ef_search = LEAST(1000, GREATEST(80, p_match_count * 2))` transaction-locally, matching
   `search_topics`.
2. **§4.2's single `WHERE t.kind = p_kind` would have lost the partial indexes.** Postgres only
   uses a partial index when it can *prove* the predicate; with a parameter that holds only for
   *custom* plans, so the index silently drops out once plan caching promotes the statement to a
   generic plan. Neither this RPC nor `search_case_sections` gets inlined (both plan as
   `Function Scan`), so the risk was live. Rewritten as three literal-predicate branches.
   **Note: `search_case_sections` has the same defect and is presumably degrading today** — it is
   being retired, so it was left alone.

### Findings that changed the build

- **The dark set is four whole jurisdictions, not a scattered 43%.** `17486` ديوان المظالم
  (4,669), `40046` ZATCA (4,966) and `40045` insurance committees (225) are **100% dark**;
  `17642` commercial is 99.99% lit. The retarget does not improve administrative / criminal /
  tax / insurance case law — it **adds** it, from a baseline of zero.
- **Null-payload rows ARE the newly-reachable rows.** All 184 cases missing a summary sit inside
  the 9,861 dark set; zero in the lit set. Under D2 that meant 18 cases would reach the
  aggregator as citable references with an empty body — a path that could not fire before
  Wave 1. `_resolve_summary` now falls back `summary → short_summary → content`, rescuing 17 of
  18 (the 18th has empty content too). Deliberate carve-out from D2: D2 governs which field is
  the *payload*, not whether empty references ship.
- **Every supreme-court principle is `شكلي`** — 135 topics / 121 cases, **zero `موضوعي`**
  (verified). Using `النوع` as a shortcut for the procedural-drop rule would delete the entire
  Supreme Court. It is not a data defect: Saudi supreme-court review is largely about form, so
  its principles are *about* procedure while still binding. `prompt_2` states that `النوع` is the
  principle's subject matter, not a merits test.
- **`موقف المحكمة` is not a closed vocabulary** — ~60 distinct values over 110,477 basis topics.
  Top 8 cover ~99%; the ~300-row tail is diacritic/spelling variants (`رفض`/`رُفِض`/`مرفوض`,
  `لم تُناقش` vs `لم تُناقَش`), plus NULL, `?`, and compound forms
  (`رُفض ابتداءً ثم قُبل استئنافاً`). `prompt_2` reads it semantically by family; unknown or
  missing = *no disposition information*, never a negative signal.
- **The supreme-court level collapse had FOUR instances**, not one (`unfold_ura.py`,
  `reranker.py` regex, `search.py` label, and a new one in `preprocessor.py`). Consolidated into
  `agents/deep_search_v4/shared/court_levels.py`. The `reranker.py` instance was mislabelling
  **live** rows on the legacy path, not just dead code.

### Deviations from plan

- `SECTION_MATCH_COUNT` 30 → **60** (rows are now topics, 2.1–3.7 per case; 30 could starve the
  reranker). `_TOP_N_PER_QUERY` stays **15** per D8.
- `DEFAULT_RERANKER_PROMPT` was decorative (read by nobody); both defaults now point at it, which
  is what makes `prompt_2` live.
- Reranker system prompt grew 8.2k → 16.4k chars (+~2k tokens). It sits in the cached prefix and
  is amortised across the N concurrent calls while the candidate payload drops ~15×, so net input
  per sub-query still falls sharply — but §12's "cached-token ratio does not drop" is now the
  criterion to watch.

### Still open

- `source_viewer.py:476` — `row.get("content") or ura.case_content`. Its own SELECT still fetches
  `cases.content`, so the user-facing popup is unaffected; but on the fallback branch (DB miss /
  `references_service`-rebuilt shell) it will now show the summary instead of the ruling text.
- `case_sections` not dropped — deliberately kept until the new path is verified in prod.
- End-to-end validation not run.
**Owner agents:** `@sql-migration` (Wave 0) → `@fastapi-backend` / manual (Waves 1–5)
**Scope:** `agents/deep_search_v4/case_search/` + the case-side of `orchestrator.py`, `ura/`, `aggregator/`, plus one shared change to `reg_compliance_search`'s reranker.

---

## 1. Why

### 1.1 A third of the corpus is dark today

| Set | Cases |
|---|---|
| `cases` | 30,531 |
| reachable via `case_sections` (what `search_case_sections` joins) | **20,669** |
| present in `case_topics` (new ingestion, 2026-07-21/22) | **29,734** |
| in `case_topics` but **not** in `case_sections` | **9,861** |

`case_sections` was built 2026-05-16/17 and never extended. Every case ingested since is
invisible to `deep_search_v4` case retrieval. Retargeting the loop at `case_topics` recovers
**+43% reachable corpus** on its own, before any prompt work.

### 1.2 The sector filter costs a third of the corpus and buys ~nothing

- The 9,860 cases with empty `legal_domains` are *exactly* the 9,861 missing from
  `case_sections` — same enrichment batch. `legal_domains && p_sectors` is **false** for an
  empty array, so any sector filter drops all of them.
- On the 20,671 that *are* tagged, the distribution is degenerate:
  **المعاملات التجارية = 18,879 / 20,671 (91%)**. Near-zero selectivity.
- `search.py:443` silently retries **unfiltered** when the filter returns 0 rows. So the damage
  is invisible in logs: partial filtering looks like successful filtering.

→ **Decision (confirmed):** the case executor stops consuming `sector_picker`. `p_sectors`
stays in the new RPC signature, always passed `NULL`, so it can be re-enabled after a
`legal_domains` backfill without a schema change. `sector_picker` keeps running for
`reg_compliance_search`.

### 1.3 `case_topics` has no vector index — HARD BLOCKER

Indexes on `case_topics` today: btree on `case_id`, `kind`, `entity_ref`, `topic_ref`. **No HNSW.**

Measured (`EXPLAIN ANALYZE`, prod): one top-60 ANN query against `kind='principle'` alone =
**21,619 ms** (parallel bitmap heap scan over 61k rows, 79k buffer reads). A second probe
against `kind='basis'` timed out entirely. With 3–10 sub-queries per turn this is not
"slow", it is non-functional. **Nothing in Waves 1–5 is testable until Wave 0 lands.**

### 1.4 The reranker is looking at the wrong thing

Today (`unfold_reranker.py`) each candidate is rendered as `### [N]` + up to **10,000 chars of
raw `cases.content`**, deliberately stripped of all metadata. 15 candidates × N sub-queries
is the dominant token cost of the case executor, and raw ruling text does not tell the model
*whether the court actually relied on* the matching argument.

`case_topics.attrs` does tell it, atomically:

| kind | rows | `attrs` |
|---|---|---|
| `basis` | 110,477 | `{الطرف, موقف المحكمة}` → أساس الحكم (34,449) / رُفض (28,360) / قُبل (19,592) / لم تُناقَش — حُسم بغيره (15,008) / لم يُعتد به (4,906) / قُبل جزئياً (3,640) / … |
| `fact` | 107,995 | `{}` |
| `principle` | 61,476 | `{النوع}` → موضوعي (36,701) / شكلي (24,771) |

### 1.5 The aggregator never sees court or court level

`AggregatorItem` for cases carries only `case_number`, `case_content`, `referenced_regulations`
(`ura/schema.py:363`). `court` reaches the prompt only as a fallback in the `<regulation>` slot
(`preprocessor.py:382`); `court_level` is tagged `# stored only` (`ura/schema.py:358`) and is
rendered nowhere. Adding both is a genuine new capability, not a re-plumb.

`cases.summary` is a far better synthesis payload than `cases.content`:

| field | p50 | p90 | p99 | avg | coverage |
|---|---|---|---|---|---|
| `content` | — | — | — | 8,538 | 30,531 / 30,531 |
| `summary` | 2,035 | 3,290 | 4,856 | 2,171 | **30,513 / 30,531** |
| `short_summary` | 230 | 382 | — | 252 | 29,567 / 30,531 |

`summary` is structured markdown: `## الملخص / ## الوقائع / ## المطالبات / ## اسانيد … /
## التسبيب / ## المنطوق`. ~4× cheaper than `content` **and** better organised.

---

## 2. Decisions locked in the reflection pass

| # | Decision |
|---|---|
| D1 | Reranker sees the **matched topic(s)** — plural. A case surfacing via 2+ topics of the same kind in one sub-query is rendered once, with all its matched topics listed. |
| D2 | `summary` is a **hard replacement** for `content` in the aggregator. Accepted consequence: the post-validator grounds against `summary`, i.e. summaries become the citable substrate. |
| D3 | Sectors: keep `p_sectors` in the RPC, always pass `NULL` from the case executor. Do not backfill `legal_domains` now. |
| D4 | `attrs` is a **signal, never a filter**. Its polarity is decided by what the user/planner_brief needs — a `لم يُعتد به` basis is exactly what a user asking *"will this defence fail?"* needs. The prompt must teach both directions. |
| D5 | `court_level` is **purely informational** — no retrieval-time boost, no reranker instruction to prefer appeal/supreme. |
| D6 | `planner_brief` goes to **both** rerankers (`case_search_reranker` + `reg_compliance_reranker`). Compliance was already merged/deleted; there are only two. |
| D7 | One plan, phased into waves. Waves 0–1 are independently shippable and valuable alone. |
| D8 | `_TOP_N_PER_QUERY` stays at **15**. The payload collapse frees plenty of room to raise it, but holding it fixed keeps the payload shape the only variable in the first eval. |

---

## 3. Target architecture

```
BEFORE
  SectionedExpander → search_case_sections(channel, emb, sectors)   [20,669 cases]
                    → enrich_candidates (fetch cases.content)
                    → Fusion (analytics)
                    → Reranker  [10k chars raw content, zero metadata, zero context]
                    → assemble_kept_cases (cases.content 8k)
                    → Aggregator  [case_number + content]

AFTER
  SectionedExpander → search_case_topics(kind, emb, NULL, count)    [29,734 cases]
                    → group topic rows by case_id (keep ALL matched topics)
                    → Fusion (analytics, unchanged)
                    → Reranker  [planner_brief + sub-query + court/level
                                 + matched topics w/ attrs + short_summary]
                    → assemble_kept_cases (cases.summary)
                    → Aggregator  [court + court_level + summary + refs]
```

---

## 4. Wave 0 — Database (BLOCKING)

**File:** `shared/db/migrations/101_case_topics_search.sql`
**Agent:** `@sql-migration`

### 4.1 Three partial HNSW indexes

Mirror the `case_sections` shape exactly (`m=24, ef_construction=256`, partial per discriminator)
so ANN traversal happens *inside* the kind, which is what "filter by basis first, then search"
means physically:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_case_topics_principle_vec
  ON public.case_topics USING hnsw (embedding vector_cosine_ops)
  WITH (m = 24, ef_construction = 256) WHERE (kind = 'principle'::case_topic_kind);
-- + idx_case_topics_fact_vec   WHERE kind = 'fact'
-- + idx_case_topics_basis_vec  WHERE kind = 'basis'
```

⚠ **`CONCURRENTLY` cannot run inside a transaction block.** The Supabase MCP `apply_migration`
wraps statements — build these via `execute_sql` one statement at a time, or drop
`CONCURRENTLY` and accept a write lock (the table is append-only from a batch ingester, so a
plain build is acceptable). Expect several minutes for 110k rows at `m=24`.

**Verify after build:** re-run the §1.3 probe; target < 150 ms for top-60 per kind.

### 4.2 `search_case_topics` RPC

```sql
CREATE OR REPLACE FUNCTION public.search_case_topics(
    p_kind            case_topic_kind,
    p_query_embedding vector(1024),
    p_sectors         text[] DEFAULT NULL,   -- D3: always NULL from the executor
    p_match_count     int    DEFAULT 60
) RETURNS TABLE (
    topic_id     uuid,
    topic_ref    text,
    case_id      uuid,
    case_ref     text,
    entity_ref   text,
    kind         text,
    topic_index  int,
    topic_text   text,
    attrs        jsonb,
    score        real,
    -- case header, joined once so there is no N+1 enrichment hop
    court        text,
    city         text,
    court_level  text,
    case_number  text,
    date_hijri   text,
    short_summary text
) LANGUAGE sql STABLE AS $$
    SELECT t.id, t.topic_ref, t.case_id, t.case_ref, t.entity_ref,
           t.kind::text, t.topic_index, t.text, t.attrs,
           (1 - (t.embedding <=> p_query_embedding))::REAL AS score,
           c.court, c.city, c.court_level, c.case_number, c.date_hijri, c.short_summary
    FROM case_topics t
    JOIN cases c ON c.id = t.case_id
    WHERE t.kind = p_kind
      AND t.embedding IS NOT NULL
      AND (p_sectors IS NULL OR c.legal_domains && p_sectors)
    ORDER BY t.embedding <=> p_query_embedding
    LIMIT p_match_count;
$$;
```

**Notes**
- Returns **flat topic rows, not deduped** — case-grouping happens in Python (D1) so a case
  can carry >1 matched topic. This is the deliberate difference from the regulation
  `search_topics` RPC, which does `DISTINCT ON (doc_id)`.
- `p_sectors` retained per D3, with a comment recording the 9,860-case blind spot.
- `short_summary` is null/empty on 964 cases — the formatter must tolerate that.
- Do **not** set `hnsw.ef_search` here unless benchmarks show recall loss; the partial indexes
  are small (20–37k vectors each).

### 4.3 Collision-rate calibration (post-index)

One probe completed before the unindexed scan timed out: top-60 `basis` topics → **60 distinct
cases (1.00 topics/case)**. Single sample, cold, cross-kind query vector — **re-measure once
the index exists** across ~20 real queries per kind. Size `MATCH_COUNT` so the grouped output
reliably yields ≥ 25 distinct cases. Start at `60`; raise if the measured ratio exceeds ~1.5.

---

## 5. Wave 1 — Search retarget

**Files:** `case_search/search.py`, `case_search/models.py`, `case_search/loop.py`

1. **`search_case_topics_rpc()`** replaces `_case_sections_rpc`. Same async-to-thread shape.

2. **Channel → kind mapping.** `TypedQuery.channel` is `Literal["principle","facts","basis"]`;
   the DB enum is `principle | fact | basis`. **`facts` ≠ `fact`.** Keep the agent-facing
   vocabulary as-is (expansion stays minimal) and map at the RPC boundary only:

   ```python
   CHANNEL_TO_KIND = {"principle": "principle", "facts": "fact", "basis": "basis"}
   ```

   Single point of translation. Do **not** scatter it — a silent `fact`/`facts` mismatch
   returns zero rows with no error, exactly the failure mode `p_channel` typing used to catch.

3. **`ChannelCandidate` grows a `topics` list** (D1):

   ```python
   @dataclass
   class ChannelCandidate:
       case_id: str
       channel: str
       rank: int
       score: float          # best matched-topic score for this case
       row: dict             # case header: court, city, court_level, case_number,
                             # date_hijri, short_summary, case_ref
       topics: list[dict] = field(default_factory=list)
       # each: {topic_ref, topic_index, text, attrs, score} — ALL topics of this
       # case that appear in THIS sub-query's result window, score-desc.
   ```

4. **Grouping (replaces `enrich_candidates`).** After the RPC returns flat rows:
   group by `case_id` → collect topics score-desc → case score = max topic score → rank
   cases by that → 1-based rank. `enrich_candidates` and `fetch_case_headers` are **deleted**;
   the RPC join supplies the header, removing one round trip per sub-query.

5. **Cross-query merge in `SectionedSearchNode`** (`loop.py:605-626`) stays — same
   best-rank-wins logic — but must **union the `topics` lists** when merging duplicate
   `case_id`s within a channel, not overwrite.

6. **`p_sectors` is always `NULL`.** Remove the `sectors_future` await and the
   zero-rows-retry-unfiltered fallback from `_search_case_section_inner`; drop the
   `agents.deep_search_v4.sector_picker.consume` import from `search.py`.

7. **Legacy path untouched.** `search_cases_pipeline` / `hybrid_search_cases` (`prompt_1`,
   `prompt_2`) stays as the CLI fallback.

---

## 6. Wave 2 — Reranker payload

**Files:** `case_search/unfold_reranker.py`, `case_search/prompts.py`, `case_search/reranker.py`

### 6.1 New candidate rendering

Replace `format_candidate_for_reranker` entirely:

```
### [3]
المحكمة: التجارية — الرياض (ابتدائي)
الموضوعات المطابقة:
- [اسانيد · المدعي · أساس الحكم] الاستناد إلى كشف حساب ومصادقة رصيد بالمبلغ المطالب به
- [اسانيد · المدعى عليه · لم يُعتد به] الاستناد إلى أن المخلص هو من اختار الناقل
الملخص: - نزاع على استرداد جزء من عمولة سمسرة عقارية بعد عدول المشتري عن الصفقة.
- قضت المحكمة برفض الدعوى لثبوت إتمام السمسار لعمله وأن العدول كان بإرادة المدعي.
```

- `attrs` rendered inline in the topic tag: `basis` → `الطرف · موقف المحكمة`;
  `principle` → `النوع`; `fact` → no tag (attrs is `{}`).
- Court level labels: `first_instance` → `ابتدائي`, `appeal` → `استئناف`, **`supreme` → `عليا`**.
- Missing `short_summary` (964 cases) → omit the line, do not emit a placeholder.
- Delete `MAX_CONTENT_CHARS = 10_000` and the `cases.content` fetch.

**Budget:** ~600–800 chars/candidate vs ~10,000 today → **~15× reduction**.
`_TOP_N_PER_QUERY` **stays at 15** — deliberately not raised, so the only variable in the
first eval is the payload shape, not the candidate count. The headroom is real (25+ would
still cost a fraction of today's run) but spending it now would make a recall shift
un-attributable. Revisit only after Wave 2 has a clean before/after.

### 6.2 Reranker prompt (`RERANKER_PROMPTS["prompt_2"]` — new key, keep `prompt_1`)

Rewrite the "your input" and "structure of each result" sections. The existing two-gate
`high`/`medium` test, the keep-only contract, `query_axes`, overclaim prevention, the
obiter-vs-ratio and mechanism-naming forcing-functions all **stay** — they are orthogonal to
the payload change and hard-won.

Add:

- **`موقف المحكمة` as a bidirectional signal (D4).** Explicit rule: this field says how the
  court *treated* the argument, not whether it is relevant. Read it against what the
  sub-query and `<planner_brief>` are asking for.
  - Question of the form *"what grounds win?"* → `أساس الحكم` / `قُبل` is the strong keep;
    `لم تُناقَش — حُسم بغيره` is weak (the court never engaged it).
  - Question of the form *"will my defence be rejected / what did courts refuse?"* →
    `رُفض` / `لم يُعتد به` is **the** valuable result and must not be demoted.
  - Never drop on `موقف المحكمة` alone.
- **`النوع` (principle)** interacts with the existing procedural rule: `شكلي` (24,771 of
  61,476) aligns with "purely procedural rulings are `medium` at most, dropped unless the
  sub-query is itself about the procedural issue". Make the link explicit so the model uses
  the tag rather than re-deriving it from prose.
- **Multiple matched topics (D1)** — more matched topics is *not* itself a relevance signal;
  judge the strongest one, and use the others for the court's overall posture.
- **`short_summary` is a summary, not the ruling.** Do not assert reasoning it does not state
  (mirrors the existing "judge gate (B) on the reasoning actually present" rule, which is now
  load-bearing since the full text is gone).
- Court/city/level are **context only (D5)** — appeal/supreme is an authority signal, *not* an
  axis-match signal. This sharpens a rule already present in `prompt_1`.

### 6.3 Drop forensics

`SectionedRerankerNode._process_one`'s dropped-reconstruction (`loop.py:937-1004`) still maps
`position → cands[pos-1]`, unchanged. `_cand_title` now reads the RPC-joined header, so it
gets richer titles for free. Add `topic_ref` of the top matched topic to each dropped
descriptor so `@reranker-run-judge` can see *what* matched.

### 6.4 Models

`CaseRerankerClassification` / `CaseKeep` are unchanged — the output contract does not move,
only the input. Keeps the salvager and the 341-test keep-only calibration intact.

---

## 7. Wave 3 — `planner_brief` → both rerankers (D6)

This intentionally reverses the "reranker receives zero context blocks" invariant recorded at
`orchestrator.py:992` and `case_search/loop.py:355`. Update both comments — a stale invariant
comment is how this gets reverted by a future reader.

- Thread `context_blocks` (filtered to **`planner_brief` only** — *not* `case_brief`, *not*
  `prior_search_lessons`) into `run_reranker_for_query` in **both**
  `case_search/reranker.py` and `reg_compliance_search/reranker.py`.
- Render it as a dedicated `<planner_brief>` block, **first in the user message**, before the
  sub-query. Rationale: all N concurrent reranker calls in a turn share the identical brief, so
  putting it at the head maximises the cached prefix under DeepSeek/Qwen prefix-caching
  (see `project_prompt_caching`). Placing it after the sub-query would defeat that entirely.
- The system prompt (the primary cache prefix) must **not** change per turn — the brief goes
  in the user message only.
- Prompt framing must match the expander's: background that helps *judge*, never a directive
  that overrides the sub-query. Reuse the existing `<context_blocks>` wording.
- `orchestrator.py` `_run_case_phase` and the reg phase pass the filtered block through.

---

## 8. Wave 4 — Aggregator payload

**Files:** `case_search/unfold_ura.py`, `ura/schema.py`, `aggregator/preprocessor.py`

1. **`AGGREGATOR_CASE_FIELDS`: `content` → `summary`** (D2). Keep `court`, `city`,
   `court_level`, `case_number`, `judgment_number`, `date_hijri`, `details_url`,
   `legal_domains`, `referenced_regulations`, `appeal_result`.
   `MAX_AGGREGATOR_CONTENT_CHARS` 8,000 → **6,000** (p99 summary = 4,856, max 21,735).

2. **Fix the `supreme` coercion bug.** `unfold_ura.py:129`:
   ```python
   court_level = "appeal" if court_level_raw == "appeal" else "first_instance"
   ```
   silently relabels all **125 supreme-court rulings as first_instance**. Replace with a
   three-value passthrough and widen `RerankedCaseResult.court_level`'s docstring/type.

3. **`AggregatorItem` gains `court: str = ""` and `court_level: str = ""`**; `CaseURAResult.
   for_aggregator()` populates them (both fields already exist on `CaseURAResult`, currently
   `# stored only`). `case_content` now carries the summary.

4. **`render_aggregator_content`, `domain == "cases"`** — prepend a one-line header:
   ```
   المحكمة: {court} ({court_level_ar})
   ```
   then the summary, then `referenced_regulations` as today.

5. **Grounding follows automatically** — `postvalidator.py:365` calls the same
   `render_aggregator_content`, so the grounding surface tracks the change with no separate
   edit. This is the D2 consequence to state plainly in the wave's verification: the
   validator now grounds against the summary, so a synthesis claim traceable only to
   `content` will be flagged ungrounded. Watch the ungrounded-claim rate in the first eval.

6. `for_reference()` and the citation panel are **untouched** — `db_id` stays `case_ref`,
   `db_uuid` stays `cases.id`.

---

## 9. Wave 5 — Unwire `sector_picker` from the case path

- `orchestrator.py::_run_case_phase` — stop passing `sectors_override` / `sectors_future`.
- `run_case_search` — keep `sectors_override` for CLI experiments; delete the
  `sectors_future` parameter and `LoopState.sectors_future`.
- `_spawn_sector_picker_task` and the reg-side consumption are **unchanged**.
- `UnifiedRetrievalArtifact.sector_filter` keeps reflecting the reg-side filter only; check the
  monitor/URA renderer does not imply it applied to cases.
- Removes the bounded picker-grace await from the case critical path — a small latency win.

---

## 10. Traps

1. **`fact` vs `facts`.** The DB enum is singular. A mismatch returns 0 rows silently. One
   mapping constant, asserted in a test.
2. **No index → nothing is testable.** Wave 0 first, verified, before any other wave.
3. **`CONCURRENTLY` in a transaction** — see §4.1.
4. **The unfiltered-retry mask** (`search.py:443`) must be deleted, not just bypassed; leaving
   it makes a future sector re-enable look harmless again.
5. **Zero-context-blocks invariant comments** in two files must be updated in Wave 3 or a
   reader will "restore" them.
6. **`short_summary` missing on 964 cases**, `summary` missing on 18 — both formatters must
   tolerate null.
7. **`assemble_kept_cases` position mapping.** `_TOP_N_PER_QUERY` truncation and the bucket
   passed to `wrap_as_fused` must stay in lockstep (`loop.py:827`) — the existing comment
   explains why. The cap stays at 15, so this invariant is untouched; if it is ever raised,
   both sides must move together or a keep on position N dereferences a case the reranker
   never saw.
8. **Prompt-cache prefix.** `planner_brief` in the *user* message head, never the system prompt.
9. **`case_sections` stays** until the new path is verified in prod. Drop it in a follow-up
   migration, not in 101.
10. **Migration drift** — verify live schema via Supabase MCP before writing 101; the files in
    `shared/db/migrations/` are not a reliable mirror of prod (`project_migration_drift`).

---

## 11. File manifest

| File | Change |
|---|---|
| `shared/db/migrations/101_case_topics_search.sql` | NEW — 3 partial HNSW indexes + `search_case_topics` RPC |
| `case_search/search.py` | RPC swap, channel→kind map, case grouping, sector removal |
| `case_search/models.py` | `ChannelCandidate.topics`, drop `sectors_future` |
| `case_search/loop.py` | grouping/merge of topics, drop `enrich_candidates`, drop-forensic `topic_ref` (`_TOP_N_PER_QUERY` unchanged at 15) |
| `case_search/unfold_reranker.py` | rewrite formatter; delete `fetch_case_headers` / `enrich_candidates` |
| `case_search/prompts.py` | reranker `prompt_2`; `<planner_brief>` in user message |
| `case_search/reranker.py` | accept + render `planner_brief` |
| `case_search/unfold_ura.py` | `content`→`summary`, `supreme` fix, cap 8k→6k |
| `reg_compliance_search/reranker.py`, `prompts.py` | `planner_brief` (D6) |
| `ura/schema.py` | `AggregatorItem.court` / `.court_level`; `for_aggregator` |
| `aggregator/preprocessor.py` | case header line in `render_aggregator_content` |
| `orchestrator.py` | unwire picker from case phase; pass `planner_brief` to both rerankers; update two invariant comments |
| `case_search/tests/` | new fixtures for the topic-row shape; `fact`/`facts` assertion; formatter golden |

---

## 12. Success criteria

**Wave 0**
- top-60 per kind < 150 ms warm; all three partial HNSW indexes present.
- `search_case_topics` returns rows for all three kinds with `p_sectors := NULL`.
- Collision ratio measured over ≥ 20 real queries per kind; `MATCH_COUNT` sized to yield ≥ 25
  distinct cases.

**Wave 1**
- A query whose best answers live in the 9,861 topics-only cases returns them (pick a probe
  case from `case_topics MINUS case_sections` and confirm it is retrievable).
- Case-level dedup preserves multiple matched topics — assert a case with 2 hits renders once
  with 2 topics.
- No `sector_picker` await on the case path (verify in a Logfire trace).

**Waves 2–3**
- Reranker input tokens per sub-query down ≥ 10× vs the current run (compare `llm_calls`
  for the same query before/after).
- `@reranker-assessor` on 2–3 conversations: no regression in false-drop rate; specifically
  check the D4 bidirectional case — a query asking about a *rejected* defence should keep
  `رُفض` / `لم يُعتد به` topics.
- `planner_brief` present in the user message of both rerankers; cached-token ratio on the
  reranker slot does **not** drop (confirms prefix discipline).

**Wave 4**
- Aggregator prompt shows `المحكمة: … (…)` per case reference; `court_level` correct for a
  supreme-court case.
- Ungrounded-claim rate from the post-validator not materially worse than the `content`
  baseline (this is the D2 risk — measure it, do not assume it).
- Case reference panel / citations unchanged (`db_id`, `db_uuid`, `details_url`).

**End-to-end**: `/test-search` on ~10 `test_queries.json` case-led queries, then
`/convo-monitor` + `/assess-rerankers` on the results.
