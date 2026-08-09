# Appendix chunks — one continuous position sequence per document

**Status:** planned 2026-08-08, not built. Read-time half is written but uncommitted.
**Owner:** @sql-migration (migration 121) + @fastapi-backend (read paths)

`chunks_v2.position` is scoped **per stream**, not per document. Every regulation's
appendix chunks restart at position 1 alongside its body chunks, so ordering by `position`
interleaves the ملاحق into the operative text. This plan makes `position` document-global,
joins the two prev/next chains, and keeps the read-time ordering as a permanent guard.

---

## 1. The damage

| | |
|---|---|
| regulations with both streams | **1,184** |
| …with at least one appendix inside the body's position range | **1,184 — every one** |
| appendix chunks landing mid-body | **3,814 of 5,388 (70.8%)** |
| **published pages rendering chunks with appendices** | **49** |
| …showing an appendix mid-body | **49 — all** |
| **misplaced sections on live pages right now** | **166** |
| published, has appendices, still article-rendered (breaks if flipped) | 37 |

100% is structural, not coincidence: the appendix stream always restarts at 1, so the
first ملحق always collides with the first body chunk.

Worst live cases — اللائحة التنفيذية لنظام العمل ج2 (31 body / 29 appendix, **all 29**
sandwiched — the page alternates body, appendix, body the whole way down), وثيقة الضمان
الصحي الأساسية (18), الدليل الإرشادي لتمريض الأسرة (13), الإطار التنظيمي للأمن السيبراني
(10), الضوابط الأساسية للأمن السيبراني (8).

46 of the 49 have rendered this way since publication. The article-coverage fallback
(`f962acc`, live 2026-08-07) added the other 3 — the labour لائحة among them.

## 2. Decisions (settled 2026-08-08)

| question | decision |
|---|---|
| What changes | **Renumber `position` only. `corpus='appendix'` STAYS.** |
| Fix layer | **Both** — migration now, keep read-time ordering as a guard |
| prev/next chains | **Join them** — one walkable document |
| `chunk_titles_v2` backfill for appendices | **No** — legacy table, see §6 |

⚠ **`corpus` is not cosmetic — do not "finish the job" by rewriting it.** It drives the
«(ملحق)» tag in `unfold_reranker.py:273` and `ura/reg_adapter.py:106` (decision D13): the
agents are deliberately told when they are reading an annex rather than operative text.
Renumbering makes appendices order like body content; the label keeps them *readable as*
what they are. Both properties are wanted.

## 3. The data is regular — this is a safe migration

Verified across all 1,184 affected regulations:

- body positions contiguous `1..N` — **1,184 of 1,184**
- appendix positions contiguous `1..M` — **1,184 of 1,184**
- irregular (gaps, non-1 start, either stream) — **0**
- appendix chunk ids are **uuid5** → renumbering touches no id, no foreign reference
- `articles_v2` rows owned by an appendix chunk — **0**
- `seo_articles` rows pointing at an appendix chunk — **0**
- appendix↔body prev/next links — **0** (the chains are entirely disjoint)

So the article layer, the SEO layer and every id-based reference are untouched. Only
`position` and the two chain endpoints move.

## 4. Migration 121

(Renumbered from 120 on 2026-08-08 — `120_subscription_cancellation.sql` landed
untracked while this plan was being written and took that slot.)

`shared/db/migrations/121_chunk_appendix_position_unification.sql`, against
`regulation_v2.chunks` (`chunks_v2` is a view over it).

### 4.1 Archive first

```sql
CREATE TABLE IF NOT EXISTS regulation_v2.chunk_position_archive_20260808 (
  id uuid PRIMARY KEY, regulation_id uuid, chunk_ref text, corpus text,
  old_position int, old_prev_chunk_id uuid, old_next_chunk_id uuid,
  archived_at timestamptz DEFAULT now()
);
```
Populate for every row this migration touches — the 5,388 appendix chunks plus the 1,184
last-body chunks whose `next_chunk_id` changes.

### 4.2 Renumber — idempotent by construction

```sql
WITH body_max AS (
  SELECT regulation_id, max(position) AS bmax
  FROM regulation_v2.chunks WHERE corpus <> 'appendix' GROUP BY 1
), ranked AS (
  SELECT c.id, b.bmax + row_number() OVER (
           PARTITION BY c.regulation_id ORDER BY c.position, c.chunk_ref
         ) AS new_position
  FROM regulation_v2.chunks c JOIN body_max b USING (regulation_id)
  WHERE c.corpus = 'appendix'
)
UPDATE regulation_v2.chunks c SET position = r.new_position
FROM ranked r WHERE c.id = r.id AND c.position <> r.new_position;
```

**Idempotent:** `bmax` is computed from body rows only, so it never moves. The target is
`bmax + rank-within-appendix-stream`, and rank is order-preserving — re-running lands every
row on the position it already holds and the `<>` guard makes it a no-op. Safe to re-run
after a partial re-ingest.

Order key is `(position, chunk_ref)`, not `(position)` — same stable tiebreaker the read
path uses, so data order and read order can never disagree.

### 4.3 Join the chains

```sql
-- last body chunk → first appendix chunk
UPDATE regulation_v2.chunks lb SET next_chunk_id = fa.id ...
-- first appendix chunk → last body chunk
UPDATE regulation_v2.chunks fa SET prev_chunk_id = lb.id ...
```
Identify `lb` as the body chunk with the regulation's max body position, `fa` as the
appendix chunk with the (post-renumber) minimum appendix position. Appendix-internal links
are already correct and stay untouched.

### 4.4 Post-conditions — assert, don't hope

| check | expected | severity |
|---|---|---|
| duplicate `(regulation_id, position)` groups | **0** (from 3,814) | **abort** |
| appendix chunks with `position <= max(body position)` | **0** (from 3,814) | **abort** |
| row count vs the baseline captured at the top of the run | unchanged | **abort** |
| positions contiguous `1..(nbody+napx)` | 0 violations | warn only |
| one NULL `prev` / one NULL `next` per regulation | — | warn only |

⚠ **The last two are WARNINGs, not aborts** (corrected 2026-08-08). Contiguity can only
hold if the *body* stream is already contiguous, and this migration never writes a body
position — a pre-existing hole there is not its damage to own. The NULL-chain check is
worse: it assumes every affected regulation currently has exactly two clean disjoint
chains, and **38 of the 1,184 do not** (measured). Asserting either would abort a correct
migration on pre-existing damage. Only "damage this migration could have caused" aborts.

Do not hardcode `48,390` as the expected row count — capture a baseline at the start of
the run and assert against that, or the migration breaks the first time the corpus grows.

### 4.5 Verified against live data — 2026-08-08

Migration written as `121_…sql` (958 lines, `BEGIN`/`COMMIT` bounded, dry-run block D1–D7
and rollback block both commented out, temp tables `ON COMMIT DROP`). **Not applied.**

Preconditions — all clean:

| check | result |
|---|---|
| chunks with `corpus IS NULL` | 0 |
| chunks with `position IS NULL` | 0 |
| non-unique `(regulation_id, position, chunk_ref)` | **0** — the idempotency claim depends on this |
| `regulation_v2.chunks` vs `chunks_v2` row count | 48,390 = 48,390 (unfiltered view ✓) |
| appendix-only regulations (would be skipped by the join) | 0 |

Dry-run of the renumber:

| | |
|---|---|
| appendix rows in scope | 5,388 |
| would move | **5,388 — all of them** |
| already on their target | 0 |
| **second pass over the post-state** | **0 moves — idempotency proven empirically** |
| labour لائحة `17900_reg_128_p2` | appendix `1..29` → **`32..60`** (31 body chunks) ✓ |

⚠ "5,388 move" does not contradict "3,814 mis-ordered". 3,814 currently *render* mid-body;
the other 1,574 already sorted after the body but still land on a different number once the
whole stream is shifted as a block. Relative order is preserved for all of them.

### 4.6 SQL defects found while implementing — do not reintroduce

The §4.2/§4.3 sketches above are pseudocode and three of them are wrong as written:

1. **`<>` in the chain join is a silent no-op.** Before the migration the last body chunk's
   `next_chunk_id` **is NULL**, and `NULL <> uuid` evaluates to NULL — so a `<>` guard skips
   every row, the chains never join, and every other post-condition still passes. Use
   `IS DISTINCT FROM`. This is the most likely way to ship a broken step 3 unnoticed.
2. **`<>` in the renumber is NULL-blind twice over** — a NULL `corpus` drags `bmax` down and
   lands an appendix chunk on a live body position; a NULL `position` consumes a
   `row_number()` slot but never updates. Both are 0 today (§4.5), so the shipped file keeps
   §4.2 verbatim and asserts the absence as a hard precondition instead.
3. **Idempotency requires `(regulation_id, position, chunk_ref)` to be unique** — `row_number()`
   over a non-unique ORDER BY is not stable between runs, so tied rows could swap forever and
   the no-op guard would never settle. Asserted as a precondition; verified 0 duplicates.

Also: the archive needs `ON CONFLICT (id) DO NOTHING` — a re-run must **keep** the original
pre-image, not overwrite it with post-migration values, or the second run destroys the only
copy of the pre-migration state.

**Rejected:** a unique index on `(regulation_id, position)`. It would catch a re-ingest
regression, but ingestion lives in the external `agentic_for_ministry` project and a
constraint added from here that aborts someone else's pipeline is a worse failure than the
one it prevents. The read-path guard plus re-runnability is the mitigation (§5.1, §8).

## 5. Read paths

### 5.1 Keep the guard (already written, uncommitted)

`_ordered_chunk_query` — `corpus DESC, position, chunk_ref` — stays, and stays THE single
definition of chunk reading order. After the migration `position` alone would suffice; the
guard exists because ingestion lives in the external `agentic_for_ministry` project and a
re-ingest can reintroduce per-stream numbering with nothing else to catch it.

Already wired: `get_regulation_doc` (TOC + preview), `get_full_regulation`. Three tests,
verified to fail when the ordering is reverted.

### 5.2 Two consumers still on raw `position` — fix in this pass

| site | effect today |
|---|---|
| `ask_service.py:289` `_ground_regulation` | anon «اسأل ريحان» grounding gets an interleaved body/appendix mix, then `.limit(REGULATION_CHUNKS)` truncates it — the model answers about a نظام from text that jumps between the لائحة and its annexes |
| `build_seo_article_index.py:502` | walks documents interleaved. It already carries `.order("position").order("id")` with the comment *"deterministic tiebreak when chunks share a position"* — someone hit the collision and papered over it with a uuid tiebreak instead of separating the streams. Plausibly upstream of some of the article-index damage |

Also `build_seo_article_index.py:553` paginates all chunks by `(regulation_id, position)`
with no unique tiebreaker — rows can duplicate or drop across page boundaries. Add
`chunk_ref`.

## 6. Explicitly NOT doing

**No `chunk_titles_v2` backfill.** Appendix chunks have 0 rows there against ~3.5 per body
chunk — but that table feeds only the legacy `search_chunk_titles` RPC, superseded by
unified `search_topics`. In `search_topics` appendices are fully covered: **11,571 topics,
100% embedded**. They are already first-class in retrieval; backfilling would mean an LLM
pass plus ~19k embeddings into an already-2.3 GB table for a path nothing queries.

**No `corpus` rewrite.** See §2.

**No re-chunking or re-ingestion.** Positions and chain links only.

## 7. Order of operations

1. Commit + deploy the read-time ordering fix **first** — it corrects all 166 live
   misplaced sections immediately and is independent of the migration
2. `/api/revalidate` purge — `DOC_REVALIDATE = 86400`, so without it the pages keep
   serving the interleaved bake for a day
3. Spot-check the labour لائحة and وثيقة الضمان الصحي render body-then-ملاحق
4. Apply migration 121 with archive + post-condition asserts
5. Re-run the §4.4 checks; confirm the read path is now a no-op guard rather than the
   thing doing the work (payload byte-identical before vs after the migration)
6. Fix the two §5.2 consumers

Step 1 before step 4 deliberately: the user-visible damage is fixed within a deploy, and
the migration lands under a read path that is already correct either way.

## 8. Risks

- **Re-ingest reverts the data.** Owned externally; mitigated by keeping the read-time
  guard permanently and by the migration being idempotent (§4.2) so it can be re-applied.
- **`position` may be a natural key upstream.** Nothing in THIS repo upserts chunks by
  `(regulation_id, position)` — `chunk_ref` is the key and ids are uuid5 of it — but the
  ingestion project was not inspected. Confirm before applying if that project is
  reachable.
- **The 2 mid-document appendices** (`17405_reg_645`, `17636_reg_091` — أدلة إرشادية whose
  body genuinely continues past an annex) get their annex moved to the end by this
  renumbering. Both are unpublished and article-less, so nothing renders them today. See
  `regulation_article_coverage_fallback.md` §8b for the detection query.
- **Coverage limit on that check:** 585 of 1,184 (49%) have no page signal on one side, so
  "597 of 599 have appendices genuinely at the end" is 597 of everything checkable.
