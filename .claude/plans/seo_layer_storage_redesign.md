# SEO layer — storage redesign, live defects, ingestion bleed

**Status:** analysed 2026-08-06, none of it built · **Owner:** @fastapi-backend + @sql-migration

Companion to [`regulation_article_coverage_fallback.md`](regulation_article_coverage_fallback.md),
which renders *around* the defects catalogued here. This document is the root-cause side.

Four parts, independent and separately shippable:

| | scope | size | risk |
|---|---|---|---|
| **A** | `seo_articles` → view over `regulation_v2.articles` + a ~1,200-row override table | 61 MB → ~2 MB | medium |
| **B** | 637 live مادة pages rendering a whole chunk instead of the مادة | content fix | low |
| **C** | 9,231 empty `seo_item_meta` shells | 92% of a 10k table | low |
| **D** | Ingestion bleed — one instance FIXED, corpus audit not run | — | — |

---

## 0. Calibration — read this before optimising anything

| | size | share |
|---|---|---|
| Whole SEO layer (5 tables) | **69 MB** | **0.6%** |
| `case_topics` + `search_topics` + `regulation_v2.chunk_titles` | 8,519 MB | **77%** |
| Database total | 11 GB | |

The duplication in `seo_articles` costs ~35 MB. Three embedding tables cost 8.5 GB. **If
the goal is storage, this document is the wrong lever** — vector dimensionality is. The
reason to do Part A is that the copy has already drifted from its source and is serving
the worse version on live pages (Part B is the proof). Storage recovery is a side effect,
not the case.

---

## Part A — `seo_articles` becomes a view

### A.1 What the table actually holds

Every column checked against `regulation_v2.articles` across all 50,924 rows:

| column | verdict |
|---|---|
| `regulation_id`, `article_no` | = `articles.regulation_id` / `article_number::int` |
| `slug` | **100% derivable** — all 50,924 are exactly `المادة-{article_no}`. One shape, zero exceptions |
| `article_label` | **100% derivable** — all 50,924 are `المادة {article_no}`; the code already derives it this way at `library_service.py:1993` |
| `chunk_id` | 50,755 = `articles.chunk_parent_id`. **169 real overrides** — all valid, all inside the same regulation |
| `extraction_status` | 50,092 match the plain rule `length(content) >= 20`. **832 mismatch — those are stale, not overrides** (Part B) |
| `article_text` | 47,962 byte-identical · 1,044 whitespace-only diff · **~1,000 real repairs** |
| `id` (uuid4) | exists only because the table exists |

Breakdown of the 2,054 `extracted`-but-different rows:

- 1,044 identical ignoring all whitespace — formatting churn
- 849 within ±3 characters
- **209 where SEO is genuinely richer** — recovered from the owning chunk when the source
  article row was a bare heading (e.g. `5000_regulation_2051` م14: 373 chars vs 66)
- **774 where the SOURCE is a mega-blob** — one `articles` row swallowing a whole document
  (worst: 177,896 chars) and the SEO slice is the saner one

So ~1,200 rows carry information. They are stored as 50,924 rows and 61 MB.

`seo_articles` is also **not** the publishing registry — that is `seo_item_meta` with
`content_type='article'`, `content_id='{regulation_id}#{article_no}'`
(`_regulation_article_index`, line 1951: *"the `seo_articles` index is NOT consulted
here"*). The sitemap runs off the sidecar too.

### A.2 Target shape

```
seo_article_overrides   (regulation_id, article_no, article_text, chunk_id, reason, updated_at)
                        ~1,200 rows, ~2 MB — ONLY where the build beat the source

seo_articles            VIEW: regulation_v2.articles
                              LEFT JOIN seo_article_overrides USING (regulation_id, article_no)
                        slug              = 'المادة-' || article_no
                        article_label     = 'المادة ' || article_no
                        article_text      = COALESCE(o.article_text, a.content)
                        chunk_id          = COALESCE(o.chunk_id, a.chunk_parent_id)
                        extraction_status = CASE WHEN length(btrim(resolved_text)) >= 20
                                                 THEN 'extracted' ELSE 'chunk_fallback' END
```

Same table name, same column list → the ~10 read sites in `library_service.py` and
`ask_service.py` need **no change**. `build_seo_article_index.py --apply` is rewritten to
write overrides only.

### A.3 Why this is safe

- `regulation_v2.articles` and `chunks` use **uuid5** — deterministic ids derived from
  their refs, so re-ingestion does not churn keys
- `(regulation_id, article_number)` is **unique**: 0 duplicate groups across 51,792 rows.
  The builder's "duplicate `article_no` keeps the longer body" rule has nothing to dedup
- All 50,924 seo rows match a source article row — no orphans
- View must skip non-numeric `article_number` (the current builder already does)

### A.4 The one real counter-argument

`regulation_v2` is pipeline-owned and re-ingested; that is precisely why the sidecar
pattern exists (`library_service.py:24`: *SEO columns on the corpus would be clobbered*).
A copy means a bad re-ingest cannot corrupt public pages. A view means it can, instantly.

That protection is not being collected today. The snapshot is already wrong in 832 places,
and wrong **silently** — it kept serving `chunk_fallback` on 637 مواد whose text has sat in
the source since May. A snapshot nobody rebuilds is not insulation, it is divergence. If
the insulation is wanted for real, the honest form is a rebuild-and-diff gate in the
ingest pipeline, not a permanent 61 MB shadow copy.

### A.5 Steps

1. Extract overrides: rows where resolved text ≠ source content, or `chunk_id` ≠
   `chunk_parent_id` → `seo_article_overrides` (~1,200 rows), with a `reason` column
   (`recovered_from_chunk` | `source_megablob` | `manual`)
2. Rename `seo_articles` → `seo_articles_legacy` (keep one release as a rollback path)
3. Create the view under the original name
4. Diff view vs legacy across all 50,924 rows — expect differences **only** on the 832
   stale-status rows (that is Part B landing for free) and the whitespace-churn rows
5. Rewrite `build_seo_article_index.py` to emit overrides
6. Drop `seo_articles_legacy` after one clean release

---

## Part B — 637 مادة pages serve the wrong body

**Live defect, independent of Part A, shippable today.**

Of the 908 `chunk_fallback` rows (`article_text IS NULL` → the reader renders the whole
owning chunk instead of the مادة):

| source `articles.content` | rows | verdict |
|---|---|---|
| < 20 chars | 81 | correct — the intended tiny-placeholder case |
| 20–200 chars | 190 | questionable |
| **> 200 chars** | **637** | **wrong — a real مادة body exists and is not shown** |

Across **108 regulations**. Examples: `17573_reg_115` م13 (1,668 chars), `18269_reg_507`
م66 (861), `17573_reg_199` م24 (1,039).

Not a race: source rows were ingested 2026-05-15/16, the SEO index was built 2026-07-23.
The text was already there. The builder's documented rule accounts for 81 of the 908 — the
other 827 need a diagnosis before the re-run, not just a re-run.

Part A fixes these as a side effect, because `extraction_status` stops being a value
frozen at build time and becomes a live expression. Shipping Part B alone means re-running
`build_seo_article_index.py` for the 108 regulations after finding the builder gap.

---

## Part C — `seo_item_meta` empty shells

The sidecar is the **right** pattern — key + pointer + policy, no content, exactly the
`workspace_item_references` shape. The problem is population:

| | rows |
|---|---|
| total | 10,038 |
| with a slug | 807 (502 regulation · 100 service · 100 circular · 100 judgment · 5 article) |
| **no slug, no rank, no gate_override, no seo_tier, no usage_score** | **9,231 (92%)** |

Those 9,231 rows carry nothing. Gate resolution falls back to `seo_gate_defaults` by
content_type whether the row is absent or empty, so they change no behaviour.

**Change:** insert only when a row holds state; purge the shells. Small storage win, but
the real gain is that "row exists" and "item is published" stop being different things —
today `_published_ids` / sample-mode logic has to know the difference.

**Verify first:** confirm nothing treats row-presence as "known item" — check
`set_gate.py`, `build_seo_slugs.py`, `build_usage_rank.py` before deleting.

---

## Part D — ingestion bleed

### D.1 FIXED — 2026-08-06, `17606_reg_001_p3`

«قرار مجلس الوزراء والمرسوم الملكي - نظام السجل التجاري» carried the **whole of نظام
الأسماء التجارية** inside it, plus the معاملة attachment index. Deleted positions 7–11:

- 5 `regulation_v2.chunks` + 20 `chunk_titles` + 20 `search_topics` = **45 rows**
- chunk 6 trimmed of its dangling `# نظام الأسماء التجارية` heading (word_count 104 → 101),
  `next_chunk_id` → NULL
- 0 `articles_v2` / `seo_articles` rows affected — the 29 مواد all belong to السجل التجاري
- No FK constraints exist on these tables: nothing cascades, nothing blocks a dangling
  pointer. Prev/next had to be fixed by hand
- Rollback: `regulation_v2.ingestion_bleed_archive_20260806` (46 rows, embeddings included)
- Nothing lost — نظام الأسماء التجارية exists standalone as `17606_reg_002_p2`

**Left unfixed:** the reg-level `summary` / `llm_summary` / `scope` / `summary_embedding`
were generated over the merged text. The prose reads clean; the embedding was computed
from the merged run and needs regeneration if reg-level semantic search matters here.

**Also noticed:** `17606_reg_006` is a standalone «نظام السجل التجاري» with 3 chunks /
1,093 words, while the قرار doc carries the *fuller* copy of the same نظام (6 chunks /
1,810 words). Same نظام twice at different completeness, the better copy filed under the
قرار title.

### D.2 NOT RUN — corpus-wide audit

`17900_reg_128_p2` (اللائحة التنفيذية لنظام العمل ج2) is the same class: its مواد
interleave two documents — المادة 6, 12, 13, 17, 20, 23–26 carry **نظام العمل** text, not
the لائحة — and «المادة 4 مكرر», «15 مكرر», «22 مكرر» are stored under the plain integers
4, 15, 22, shadowing the real مواد.

Both known instances are `_p2` / `_p3` split-page docs in the same ingest batches. An audit
of how widespread that is was **explicitly declined on 2026-08-06** in favour of shipping
the fallback rule. Open when wanted:

- how many `_pN` regs contain a second document's opening heading mid-body
- how many `مكرر` مواد collapsed onto a base `article_no`
- whether `entity_ref` batches 17606 / 17900 share a common ingest run

---

## Cross-references

- [`regulation_article_coverage_fallback.md`](regulation_article_coverage_fallback.md) —
  the >10%-gaps → chunks rule; renders around Part D's damage without repairing it
- [`seo_public_library.md`](seo_public_library.md) — the wing this layer serves
- `project_isr_bake_docker_cache_trap` — any change here needs `/api/revalidate`
