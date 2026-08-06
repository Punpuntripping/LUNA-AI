# Regulation article-coverage fallback + chunk-based unlock pricing

**Status: BUILT 2026-08-07 — not deployed.** Backend + tests + audit script are in the
working tree; 602 library tests pass. Deploy still needs §8, and the ISR purge in §8.4 is
not optional. · **Date:** 2026-08-06 · **Owner:** @fastapi-backend

> Line numbers throughout §4 were written against a pre-build read of the file and were
> all stale by 20–90 lines. They are corrected below to the as-built positions.

Two changes to `backend/app/services/library_service.py`:

1. A regulation whose article index has **>10% gaps and >3 missing مواد** stops rendering
   from `seo_articles` and renders from `chunks_v2` instead — on the anon doc page, the
   authed full-document reveal, and the unlock price alike.
2. Chunk-priced regulations stop being weighted by character count and are weighted by
   **chunk count, where 1 chunk = 3 مواد**. This applies to **every** chunk-priced
   regulation, not only the ones the new rule flips.

No frontend change. No migration. No data repair.

---

## 1. Why

`get_regulation_doc` (line 2240) commits the whole reading surface to the article view on
the mere existence of one `seo_articles` row:

```python
if articles:
    # ARTICLES-FIRST
else:
    # CHUNK FALLBACK
```

That is true even when the article index covers a fraction of the document.

**Trigger case — `17900_reg_128_p2`, اللائحة التنفيذية لنظام العمل وملحقاتها الجزء 2**
(`/regulations/اللائحة-التنفيذية-لنظام-العمل-وملحقاتها-الجزء-2`):

| measure | value |
|---|---|
| `article_no` range | 1 → 232 |
| `seo_articles` rows | 68 |
| missing مواد | **164 (70.7%)** |
| missing runs | 59–105, 121–140, 190–229, … |
| chunks available | 60 |
| `extraction_status <> 'extracted'` | **0** |
| `article_text IS NULL` | **0** |

The last two rows are the load-bearing detail: **every row this document has is
healthy.** The damage is in the rows it does *not* have. Any completeness test written
against `extraction_status` or `article_text` scores this page 100% and leaves it broken.
The test must be against the numbering.

The page currently advertises «68 مادة» and silently omits 164 of them.

## 2. Decisions (settled 2026-08-06)

| question | decision |
|---|---|
| Threshold | **10%** of the document AND **>3** مواد missing — both must hold |
| "Missing" | **gaps in `article_no`** = `max(article_no) − count(rows)`. Not text-based |
| Unlock pricing | **Price by chunks at 1 chunk = 3 مواد**, applied to **all** chunk-priced regulations |
| 8-point cap on the labour لائحة | **Accepted as-is** — `UNLOCK_COST_MAX` and `CHUNKS_PER_UNLOCK` both stay put |
| Ingestion repair | **Out of scope** — see §7 |

## 3. The rule

```
missing      = max(article_no) - count(rows)
missing_pct  = missing / max(article_no)
trustworthy  = NOT (missing > 3 AND missing_pct > 0.10)
```

Evaluated on the **full** article list, before the gate slices it to the 3-مادة preview,
so anon, open and authed readers all reach the same verdict.

**Hard guard:** a regulation with zero chunks keeps the article view regardless of how bad
the gaps are. A partial document beats a blank one. No published regulation hits this
today (all 15 candidates have 4–60 chunks) — the guard is for the corpus we don't have yet.

## 4. Implementation

### 4.1 Constants — AS BUILT, lines 626–650

```python
ARTICLES_PER_CHUNK = 3
CHUNKS_PER_UNLOCK = ARTICLES_PER_UNLOCK / ARTICLES_PER_CHUNK   # 25/3 ≈ 8.33
ARTICLE_GAP_MIN_MISSING = 3       # absolute floor: ignore small documents
ARTICLE_GAP_MAX_RATIO = 0.10      # >10% of the document missing → distrust the index
```

`CHARS_PER_UNLOCK` is **deleted** along with the character-scan loop it fed. (It was never
in `__all__` — only the module constant existed.) The new names ARE exported, because the
tests import them.

⚠ **`CHUNKS_PER_UNLOCK` is documentation, not arithmetic.** The price is computed with
integer ceiling division — `-(-n_chunks * ARTICLES_PER_CHUNK // ARTICLES_PER_UNLOCK)` —
not `ceil(n / CHUNKS_PER_UNLOCK)`. The float form is correct today (`25/3` rounds UP to
`8.333333333333334`, so `n / that` lands just below the integer at n = 25, 50, 75 and
ceils the right way; verified over 1..10000, zero divergence). But a money path should not
be correct *because a rounding error happens to point the safe way*, and during the build
one reviewer read the boundary the other way round and reported a phantom off-by-one. The
integer form removes the question.

### 4.2 Helper — AS BUILT at line 682, beside the cost constants

(The plan originally placed this next to `_seo_articles_for_regulation`. It has to sit
above `unlock_cost`, which is the first caller.)

```python
def article_coverage_is_trustworthy(articles: list[dict[str, Any]]) -> bool:
    """Does this article index actually cover its document?

    `seo_articles` rows are keyed by `article_no`, so a document whose highest
    `article_no` far exceeds its row count has HOLES — مواد that exist in the
    نظام and have no row. Rendering that index drops them silently: the page
    shows «68 مادة» for a 232-مادة لائحة and says nothing about the other 164.

    Gaps are counted from the NUMBERING, not from `extraction_status` — on the
    case this rule was written for (17900_reg_128_p2) every present row is
    healthy and 164 are simply absent, so a text-based test scores it perfect.

    False → the caller must render from chunks instead. An empty list is
    trustworthy=False by vacuous truth but callers already branch on falsiness
    first, so it never reaches here.
    """
```

Public (no leading underscore) — `unlock_cost` and the tests both need it.

### 4.3 Call sites

All three take the same shape, `if articles:` → `if articles and _use_articles(...)`:

| function | line (as built) | effect when it flips |
|---|---|---|
| `get_regulation_doc` | **2266** | anon/ISR doc page: chunk TOC (id = chunk uuid), chunk sections, `hidden_section_count` from chunk count |
| `get_full_regulation` | **4612** | authed `/library/full/regulation/{slug}` reveal: every chunk in reading order |
| `unlock_cost` | **799** | prices as a chunk document (§4.4) |

Helper is public — `use_article_surface`, not the `_use_article_surface` sketched below —
because `__all__` exports it and the tests call it directly.

**`get_regulation_doc` and `get_full_regulation` must never disagree.** If the anon page
flips and the paid reveal doesn't, a reader who spent an unlock gets a structurally
different document than the crawler saw. One shared helper, both call sites, same input.

A small wrapper keeps the three sites honest:

```python
def _use_article_surface(supabase, content_id, articles) -> bool:
    if not articles:
        return False
    if article_coverage_is_trustworthy(articles):
        return True
    if _regulation_chunk_count(supabase, content_id) == 0:
        return True          # nothing better to fall back to
    logger.info(
        "article coverage rejected: reg=%s rows=%d max_no=%d missing=%d (%.1f%%) → chunks",
        content_id, len(articles), max_no, missing, pct * 100,
    )
    return False
```

The INFO log is the only observability added. Nothing new on the public payload.

### 4.4 Unlock pricing

`unlock_cost` (line 680) becomes:

```
regulation with a TRUSTED article index → clamp(ceil(n_articles / 25), 1, 8)     [unchanged]
every other regulation                  → clamp(ceil(n_chunks * 3 / 25), 1, 8)   [new]
```

The second branch replaces both the old chunk-only char weighting *and* the price for
newly-flipped documents. Docstring must be rewritten — the current one documents
`CHARS_PER_UNLOCK` explicitly.

Side benefit: the character-scan loop paged through **every chunk body** of a regulation
just to sum lengths. A `count="exact"` on `chunks_v2` replaces it — no body text crossing
the wire to be discarded. Note the *reject* path now costs **two** counts, not one: once
inside `use_article_surface` to confirm a fallback exists, once to price it. Accepted —
agreeing with the render decision beats saving a round trip on a rare, uncached money
path, and threading the count out of the helper would give `unlock_cost` a private door
into a decision that exists precisely so all three callers go through one.

`unlock_cost` selects **`article_no` only** — it deliberately does not reuse
`_seo_articles_for_regulation`, which pulls `article_text`. Fetching article bodies to
compute a price is the same waste this change removes.

## 5. Impact

### 5.1 Documents that flip — 15 of 330 published regulations with articles

| reg_ref | title | rows | max_no | gaps | % | chunks | cost now → new |
|---|---|---|---|---|---|---|---|
| 17900_reg_128_p2 | اللائحة التنفيذية لنظام العمل ج2 | 68 | 232 | 164 | 70.7 | 60 | 3 → **8** |
| 17903_reg_014 | نظام نزع ملكية العقارات | 28 | 37 | 9 | 24.3 | 9 | 2 → 2 |
| 18269_reg_036_p2 | اللائحة التنظيمية لسلوكيات سوق التأمين | 49 | 61 | 12 | 19.7 | 8 | 2 → 1 |
| 17642_reg_022 | اللوائح التنفيذية لنظام المرافعات الشرعية | 198 | 242 | 44 | 18.2 | 28 | 8 → 4 |
| 5000_regulation_1686 | القانون الموحد لمكافحة جرائم تقنية المعلومات | 32 | 39 | 7 | 17.9 | 6 | 2 → 1 |
| 17606_reg_003 | نظام الشركات | 263 | 319 | 56 | 17.6 | 48 | 8 → 6 |
| 17639_reg_059 | إجراءات وضوابط المواد المخدرة | 19 | 23 | 4 | 17.4 | 35 | 1 → **5** |
| 5000_regulation_0004 | نظام الإقامة | 54 | 65 | 11 | 16.9 | 16 | 3 → 2 |
| 5000_regulation_0889 | اللائحة التنفيذية لمراقبة شركات التأمين | 71 | 84 | 13 | 15.5 | 23 | 3 → 3 |
| 5000_regulation_2146 | قواعد الاستعانة بالخبراء | 36 | 41 | 5 | 12.2 | 4 | 2 → 1 |
| 5000_regulation_0576 | نظام شركة الاتصالات السعودية | 45 | 51 | 6 | 11.8 | 7 | 2 → 1 |
| 5000_regulation_1206 | النظام الأساسي للاتصالات المتكاملة | 46 | 52 | 6 | 11.5 | 13 | 2 → 2 |
| 17396_reg_066 | قانون العلامات التجارية الخليجي | 40 | 45 | 5 | 11.1 | 18 | 2 → 3 |
| 5000_regulation_2108 | قواعد لجنة الفصل في المخالفات التمويلية | 33 | 37 | 4 | 10.8 | 5 | 2 → 1 |
| 5000_regulation_2107 | قواعد لجنة المنازعات المصرفية | 33 | 37 | 4 | 10.8 | 5 | 2 → 1 |

None of the 15 has any published مادة page (`seo_item_meta` article rows = 0 across all of
them), so no `article_index` link map is left pointing at a chunk TOC.

### 5.2 Pricing movement — 187 chunk-priced regulations (15 flipped + 172 chunk-only)

| | count |
|---|---|
| unchanged (article-priced, trusted index) | 315 |
| price same | 125 |
| price up | 38 |
| price down | 24 |
| mean cost | 2.06 → 2.17 |

Mean across **all 502** published regulations (not just chunk-priced): **2.29 → 2.33**.

Re-run any time with `python scripts/check_article_coverage.py` — read-only, and
`--threshold` / `--min-missing` re-model the rule without touching code. Its constants are
deliberately **mirrored, not imported**: the "now" column has to price the pre-change world
using the deleted `CHARS_PER_UNLOCK`, so an import would silently turn it into the "new"
column and the audit would report zero movement while looking like it passed.

Three outliers: **اللائحة التنفيذية لنظام العمل ج2 3 → 8** (`UNLOCK_COST_MAX`, the cap),
**إجراءات وضوابط المواد المخدرة 1 → 5** (19 مواد but 35 chunks), and — surfaced by the
audit script, not in the original modelling — **دليل امتثال أصحاب العمل
(`17900_reg_081`) 5 → 8**, which is chunk-only so it never appears in the flip table above
but joins the labour لائحة at the cap. All three are documents whose chunk count runs high
against their article count, and the 1-chunk-=-3-مواد rate is what produces the jump.

**Decided 2026-08-06: leave both.** The oddity is real — the labour لائحة nearly triples in
price at the same moment its reading surface gets coarser (68 مواد → 60 chunk sections, no
per-مادة anchors), which for a free user is 30% → **80%** of a 10-unlock period. It is
accepted because exposure is negligible: 33 unlocks exist in the whole ledger (21 of them
cost 1), and **4 of 187 chunk-priced regulations sit at the cap either way — the same 4
before and after.**

Note for whoever revisits this: the chunk rate moves the cap much closer. Article pricing
reaches 8 at 176+ مواد; chunk pricing reaches it at **59 chunks**. If the cap starts
binding on documents it shouldn't, the lever is `CHUNKS_PER_UNLOCK` (1 chunk = 2 مواد puts
the labour لائحة at 5), not `UNLOCK_COST_MAX` and not the fallback threshold.

Prices are read live by `resolve_access` — no stored costs to backfill. Rows already in
`library_unlocks` keep the cost they were charged.

## 6. Tests — `backend/tests/test_library_gating.py`

The existing `unlock_cost` tests live here; add alongside.

**Helper (pure, no DB):**
- 232 max / 68 rows → False (the trigger case)
- 100 max / 100 rows → True
- 100 max / 95 rows → True (5 missing, but 5% ≤ 10%)
- 20 max / 16 rows → True (20% missing but only 4 > 3 — wait: 4 > 3 AND 20% > 10% → **False**). Use 20 max / 17 rows → True (3 missing, floor not cleared)
- 40 max / 35 rows → False (5 missing, 12.5%)
- empty list → False

**Fallback wiring:**
- flipping doc → `visible_sections[*].id` are chunk uuids, not `art-*`
- flipping doc with **zero chunks** → article surface retained
- `get_regulation_doc` and `get_full_regulation` agree on source for the same slug
- non-flipping doc → payload byte-identical to today (regression guard)

**Pricing:**
- trusted index → `ceil(n/25)` unchanged
- flipped and chunk-only → `ceil(chunks*3/25)`, clamped [1, 8]
- 0 chunks and 0 articles → `UNLOCK_COST_MIN`

## 7. Out of scope

**The underlying ingestion defect.** `17900_reg_128_p2` is broken twice over. Beyond the
gaps, its مواد interleave two different documents — المادة 6, 12, 13, 17, 20, 23–26 carry
**نظام العمل** text, not the لائحة — and «المادة 4 مكرر», «15 مكرر», «22 مكرر» are stored
under the plain integers 4, 15, 22, shadowing whatever the real مواد were. Same bleed class
as the السجل التجاري / الأسماء التجارية case fixed on 2026-08-06.

This plan renders around that. It does not fix it, and a flipped document's chunks may
still carry the interleaved text.

Everything root-cause is catalogued in
[`seo_layer_storage_redesign.md`](seo_layer_storage_redesign.md) — the `seo_articles` →
view redesign (Part A), the 637 مادة pages serving a whole chunk instead of the مادة
(Part B), the 9,231 empty `seo_item_meta` shells (Part C), and the ingestion bleed itself
(Part D: the السجل التجاري instance fixed 2026-08-06, the corpus-wide audit declined).

Also out: per-مادة detail pages, and any frontend change
(`app/regulations/[slug]/page.tsx:123` already detects the surface via
`s.id.startsWith("art-")`).

## 8. Rollout

1. Build + `pytest backend/tests/test_library_gating.py`
2. Dump before/after payloads for all 15 flipping slugs — section count, TOC length, first
   section body — and eyeball the labour لائحة and نظام الشركات specifically
3. Deploy backend
4. **Purge ISR — mandatory.** These pages are `DOC_REVALIDATE = 86400` (24h) on top of the
   backend hour cache; without `/api/revalidate` the change is invisible for a day. This
   trap has recurred twice (see `project_isr_bake_docker_cache_trap`)
5. Verify `/regulations/اللائحة-التنفيذية-لنظام-العمل-وملحقاتها-الجزء-2` renders chunk
   sections, and that a signed-in reveal on the same slug matches
6. Confirm the INFO flip logs list exactly the 15 expected reg_refs — more than 15 means
   the threshold or the gap maths is off

## 9. Risks

- **A flipped document loses its per-مادة anchors.** نظام الشركات goes from a 263-row TOC
  to 48 chunk rows. That is the intended trade (a truthful coarse TOC over a lying fine
  one) but it is a visible downgrade on documents whose index is 82% complete.
- **Chunk titles are ingestion artefacts.** The chunk TOC shows titles like «الفصل الأول:
  أحكام عامة» — fine — but also «فهرس مرافقات المعاملة». Less curated than مادة labels.
- **Threshold is global.** No per-regulation override exists (`seo_item_meta.gate_override`
  is for gating, not source selection). A wrong call on one document can only be fixed by
  moving the threshold for all.
