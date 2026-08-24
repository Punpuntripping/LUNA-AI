# الملاحق on the reading surface — the article path drops them

**Status:** BUILT 2026-08-24 — validated locally against prod data, NOT deployed
(see §8 for what the build found that this plan did not predict)
**Written:** 2026-08-24 · every number below MEASURED against prod
(`dwgghvxogtwyaxmbgjod`) and the live site on that date
**Related:** [[chunk_appendix_position_unification]] (the ordering half — read it
first), `regulation_article_coverage_fallback.md`, `simple_search_family.md` §4,
`access_tiers_gating_DECISIONS.md`, `gate_exposure_budget.md`

---

## §0 The finding

`chunk_appendix_position_unification.md` fixed how ملاحق are ORDERED. It never
asked whether they are RENDERED. On the surface most أنظمة actually use, they
are not.

`get_regulation_doc` and `get_full_regulation` both branch on
`use_article_surface()`. When it returns True — the default for any نظام with a
healthy `seo_articles` index — the document is built **exclusively** from
`seo_articles`, and `seo_articles` contains **zero** rows derived from the
appendix stream:

| source chunk `corpus` | `seo_articles` rows | regs |
|---|---|---|
| `with_articles` | 49,724 | 1,768 |
| `without_articles` | 1,200 | 26 |
| `appendix` | **0** | **0** |

So on the article surface the ملاحق are not misplaced — they are absent. No
section, no TOC row, no count, nothing in the payload that says an appendix
exists at all.

### The corpus

- 5,388 appendix chunks across 1,184 regulations — 27,915,617 chars, avg 5,181,
  max 18,641. Every one of them HAS a `title` (0 null/empty), so TOC labels are
  free.
- 441 of those 1,184 are published (`seo_item_meta.slug is not null`).
- **188** of the 441 also carry a `seo_articles` index → article surface → their
  ملاحق are dropped.
- The other 253 render through the chunk fallback, where the 2026-08-09 read-time
  fix (`fe30c36`) already puts the ملاحق in the right place: at the end.

### Live proof (2026-08-24)

`/regulations/اللائحة-الفنية-الخليجية-للعب-الأطفال` — 109 chunks: 40 body, **89
appendix**. `seo_articles` = 40 rows, max `article_no` 40, zero holes → article
surface. The page returns 200 / 175,199 bytes and contains:

- 41 «المادة (n)» TOC labels, 3 rendered `sec-art-*` sections, 1
  `#library-doc-gate` (it is gated — no `seo_tier`, so the `regulation` default
  `gated` applies);
- **zero** occurrences of «قائمة المنتجات المستثناة», «المتطلبات الخاصة
  بالسلامة», «المواد المسرطنة», «الحدود القصوى لنزوح العناصر» — i.e. none of the
  89 appendix chunks.

The only «الملحق رقم (1)» strings on that page are cross-references *inside*
مادة text, pointing at an annex the page does not carry. For a لائحة فنية the
annexes ARE the operative content (CMR tables, migration limits, conformity
procedures): 82% of the document is missing, and nothing on the page says so.

Same shape on `اللائحة التنفيذية لجودة الهواء لنظام البيئة` — 39 chunks, **38 of
them appendix**, 8 مواد. The air-quality limit tables are the نظام; the page
ships eight مواد.

### The perverse property this creates

`اللائحة التنفيذية لنظام العمل ج2` has 68 `seo_articles` rows for a 232-مادة
لائحة — 70% holes — so `article_coverage_is_trustworthy` REJECTS it, it falls to
chunks, and it therefore renders its 29 ملاحق correctly. **A worse-indexed نظام
renders more completely than a well-indexed one.** That is the tell that this is
a hole in the article surface and not a data problem.

---

## §1 Decisions

**D1 — الملاحق are part of the document.** The article surface gains a trailing
appendix section list, in the same `visible_sections` array, under the same TOC.
Not a separate page, not a tab, not a download. A reader scrolling a نظام reaches
its ملاحق by scrolling.

**D2 — Order is body-then-ملاحق, always.** The appendix rows come from
`_ordered_chunk_query` filtered to `corpus='appendix'` — never a fresh
`.order("position")`. `position` is per-stream (that is the whole point of
[[chunk_appendix_position_unification]]) and `chunk_ref` is the stable tiebreak.
Migration 121 does not change this; the read path stays the definition.

**D3 — Section id is `apx-{n}`**, n = 1-based ordinal in appendix reading order.
NOT the chunk uuid. The frontend detects the article surface with
`s.id.startsWith("art-")` (`app/regulations/[slug]/page.tsx:126`); a uuid-id
section sitting on the article surface would make that test read a mixed
document as a chunk document. `apx-` shares no prefix with `art-`.

**D4 — TOC rows carry `kind: "article" | "appendix"`.** Additive, and OPTIONAL on
the wire — the doc page is ISR-baked for 24h (`DOC_REVALIDATE = 86400`), so a
payload baked before this ships carries no `kind` at all. Frontend reads
`entry.kind ?? "article"`. Absent ⇒ today's behaviour, never a crash.

**D5 — TOC `position` for a ملحق continues past the last مادة**:
`max(article_no) + n`. The client re-sorts the TOC by `position`
(`page.tsx:136` `.sort((a, b) => a.position - b.position)`), so an appendix
numbered 1..89 alongside مواد 1..40 shuffles straight back into the body — the
exact bug the chunk path already fixed by renumbering server-side, arriving a
second time through a different door. **A payload whose own sort key does not
reproduce its own order is the bug.**

**D6 — The gated preview does not change.** It stays the first 3 مواد; a ملحق is
never a preview section. But `hidden_section_count` becomes
`len(articles) + len(appendix) - 3` so the CTA («N قسمًا إضافيًا بانتظارك») stops
under-counting what is actually behind the gate.

**D7 — An open-tier نظام ships its ملاحق whole**, like every other section on an
open document. Measured exposure delta for that decision: of the 188 affected
regs, **187 are gated** (746 appendix chunks / 3,928,880 chars — all of it behind
the reveal) and **1 is open** (1 chunk / 3,085 chars). This is not a scraping
event; see `scraping_assessment` and `gate_exposure_budget` for the surfaces that
are.

**D8 — Price follows the render.** `unlock_cost`'s own docstring already states
the rule ("The price MUST follow the render decision"), so a reveal that now
ships the ملاحق must price them. Appendix chunks are priced at the existing chunk
rate (`ARTICLES_PER_CHUNK = 3`), added to the مادة count before the same
`/ ARTICLES_PER_UNLOCK` division and the same 1..8 clamp. Simulated over the 188:

| price change | regs |
|---|---|
| unchanged | **137** |
| +1 point | 26 |
| +2 / +3 | 22 |
| ≥ +4 | 3 (1→5, 2→8, 3→7) |

The three big movers are exactly the documents that are mostly annex (the toys
لائحة among them) — which is the rule working, not a regression.

**D9 — HTML comments are stripped for display, server-side, before truncation.**
They are an appendix-exclusive ingestion artifact: 4,695 of 5,388 appendix chunks
carry at least one, and **0 of 43,002 body chunks do**. Forms, by occurrence:
`<!-- end table -->` 7,733 · `<!-- converted table -->` 7,103 ·
`<!-- جدول محول من HTML -->` 2,230 · `<!-- نهاية الجدول -->` 2,194 ·
`<!-- Hyperlinks -->` · `<!-- Page N -->`. `ArticleBody plain` does no markdown
parsing and `toLegalBlocks` does not know what a comment is
(`lib/library/legal-text.tsx:15`), so each one renders as a literal paragraph of
HTML on the page. Strip every `<!-- … -->`, not a marker allowlist — an HTML
comment is never legal text. Pure display transform, mirroring
`_clean_article_display_text`; the stored text is never mutated.

⚠ This is ALSO a live defect on the 253 chunk-path regulations: their reveal
(`get_full_regulation`, chunk branch) returns `content` verbatim, untruncated and
uncleaned. The same helper fixes both paths.

**D10 — Render `content`, not `content_display`.** `content_display` is non-null
on exactly the 1,681 table-carrying appendix chunks and replaces each table with a
`TBL_<chunk_ref>_<n>` placeholder that only the `tables` jsonb can resolve.
**Nothing in the repo reads it** (verified: zero hits across `backend/`,
`frontend/`, `agents/`, `shared/`, `scripts/`). Adopting it without a table
renderer would ship `TBL_5000_regulation_1688_apx_032_1` to readers. Revisit when
a renderer exists — a real table surface for 1,681 chunks is worth its own plan.

**D11 — No per-ملحق page.** No `seo_articles` row, no `article_index` entry, no
sitemap URL, no slug. The مواد pages are the SEO/ranking layer and a ملحق is a
doc-level section only. This plan renders the ملاحق; it does not publish them as
items, so the item meter, the gate keys and the enumeration defence are untouched.

---

## §2 Backend — `backend/app/services/library_service.py`

### 2.1 New: `_appendix_chunks_for_regulation`

```python
def _appendix_chunks_for_regulation(
    supabase: SupabaseClient, regulation_id: str
) -> list[dict[str, Any]]:
    """The appendix stream of one regulation, in reading order. Fail-soft → []."""
```

Body: `_ordered_chunk_query(supabase, regulation_id, "id, title, content")
.eq("corpus", _CHUNK_APPENDIX_CORPUS).execute()`.

- The `.eq()` rides on top of the canonical builder — **do not re-`order()`**.
  `_ordered_chunk_query` stays the one place order is defined (D2).
- Fail-soft to `[]` and log a warning: a missing annex must never 500 a نظام.
  This is the opposite of the article path, where an empty index means "render
  chunks"; here empty simply means "no ملاحق".

### 2.2 New: `_appendix_sections`

```python
def _appendix_sections(
    rows: list[dict[str, Any]],
    *,
    gate: str,
    free_chars: int,
    start_position: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(sections, toc_rows) for the appendix stream. Pure (no DB)."""
```

- section: `{"id": f"apx-{n}", "title": row["title"], "text": …, "is_truncated",
  "hidden_placeholder_lines", "also_ids": []}` — the same shape
  `_article_sections` emits, so every downstream consumer (the page map,
  `FullContentGate.renderFull`, the JSON-LD paywall target) works unchanged.
- toc row: `{"id": f"apx-{n}", "title": row["title"],
  "position": start_position + n, "kind": "appendix"}` (D5).
- text = `truncate_for_gate(_strip_html_comments(row["content"]), gate,
  free_chars=free_chars)` — strip BEFORE truncation (D9), or the free-char budget
  gets spent on `<!-- Page 19 -->`.
- `free_chars=600`, matching the article branch. Inert under D6 (a ملحق is never
  in a gated preview) but it must not be the number that surprises someone the
  day the gate policy changes.

### 2.3 New: `_strip_html_comments`

Pure, module-level, `re.sub(r"<!--.*?-->", "", text, flags=re.S)` + collapse the
blank lines it leaves. Applied to appendix text on BOTH paths and to the chunk
fallback's `content` (D9's second half).

### 2.4 `get_regulation_doc` — article branch

After `visible_sections` / `hidden_section_count` are built:

```python
apx_rows = _appendix_chunks_for_regulation(supabase, str(content_id))
if apx_rows:
    max_no = max((int(a.get("article_no") or 0) for a in articles), default=0)
    apx_sections, apx_toc = _appendix_sections(
        apx_rows, gate=gate, free_chars=600, start_position=max_no
    )
    toc.extend(apx_toc)
    if is_open:
        visible_sections.extend(apx_sections)     # open ⇒ end to end (D7)
    hidden_section_count = 0 if is_open else (
        len(articles) + len(apx_rows) - len(visible_sections)
    )
```

- `max_no` from `article_no`, NOT `len(articles)` — a holed-but-trusted index
  (`ARTICLE_GAP_MIN_MISSING`) has `max_no > len(articles)` and the appendix must
  still sort past the LAST مادة, not past the count of them.
- The مادة TOC rows gain `"kind": "article"` in the same edit.
- Chunk branch: TOC rows get `"kind"` too — `"appendix"` for rows whose corpus
  says so, `"article"` otherwise. That branch already renumbers `position`
  sequentially, so ordering is already right; `kind` is only there so the
  frontend has one rule instead of two.

### 2.5 `get_full_regulation` — article branch

The reveal is where 187 of the 188 actually land (D7), so this is the load-bearing
half. After `sections = _article_sections(...)`:

```python
apx_rows = _appendix_chunks_for_regulation(supabase, str(content_id))
if apx_rows:
    apx_sections, _ = _appendix_sections(
        apx_rows, gate="open", free_chars=600, start_position=0
    )
    sections.extend(apx_sections)
```

`gate="open"` — the reveal truncates nothing; identical to how `_article_sections`
is already called here.

⚠ The two branches must agree section-for-section (the docstring's own warning:
"A reader who spent an unlock and lands on a structurally different document than
the crawler saw is a broken purchase"). Same helper, same order, both call sites.

### 2.6 Chunk fallback — both functions

`"text": _strip_html_comments(r.get("content") or "")`. This is the pre-existing
defect from D9; it costs one function call per section.

### 2.7 `unlock_cost`

```python
if use_article_surface(supabase, str(content_id), articles):
    n_apx = _regulation_appendix_chunk_count(supabase, str(content_id))
    return _clamp_cost(
        -(-(len(articles) + n_apx * ARTICLES_PER_CHUNK) // ARTICLES_PER_UNLOCK)
    )
```

- Integer ceiling division, matching the chunk branch's existing form — the
  float-`ceil` note there applies verbatim, do not reintroduce it.
- A `count`-only query (`select id, count='exact'` on `corpus='appendix'`), never
  a body scan — same discipline as `_regulation_chunk_count`. Fail-soft → 0, i.e.
  today's price. **The fail-safe direction here is DOWN**, unlike the rest of the
  function: a lookup blip must not overcharge.

---

## §3 Frontend

### 3.1 `frontend/lib/library/api.ts`

```ts
export interface RegulationTocEntry {
  id: string;
  title: string;
  position: number;
  /** Additive; absent on a payload baked before this shipped ⇒ "article". */
  kind?: "article" | "appendix";
}
```

### 3.2 `frontend/app/regulations/[slug]/page.tsx`

One change, in the `articlesFirst` TOC map (currently lines 135–150). Today every
non-published row anchors to `` `#sec-art-${entry.position}` `` — derived from
`position`, not from `id`. An appendix row at position 41 would target
`#sec-art-41`, which does not exist; its section is `#sec-apx-1`.

```ts
.map((entry) => {
  const isAppendix = (entry.kind ?? "article") === "appendix";
  if (!isAppendix && publishedSlugs.has(entry.id)) {
    return { id: entry.id, label: entry.title,
             href: `/regulations/${doc.slug}/${entry.id}` };
  }
  return {
    id: entry.id,
    label: entry.title,
    href: isAppendix ? `#sec-${entry.id}` : `#sec-art-${entry.position}`,
  };
})
```

`publishedSlugs` can never hold an `apx-*` id (D11), so the `isAppendix` guard in
the first branch is belt-and-braces — keep it anyway; it states the intent that a
ملحق has no page of its own.

### 3.3 TOC badge

`tocBadge` currently reads `${articleIndex.length} مادة`. With ملاحق in the rail
that count no longer describes the list. When the TOC holds appendix rows:
`` `${articleIndex.length} مادة · ${apxCount} ملحقًا` ``. Latin numerals, per the
app-wide policy.

### 3.4 Nothing else

`FullContentGate.renderFull` already emits `` id={`sec-${section.id}`} `` for
every section, so the reveal anchors work with no change. `LibraryUseBeacon`,
`buildPaywallFragment(".gated-body")` and the two related strips are untouched.

---

## §4 Tests — `backend/tests/test_library_gating.py`

Extend `_chunks()` with a `corpus` field (today's rows have none, which is why
`.eq("corpus", …)` returns nothing for them and every existing fixture stays
green — the fake's `eq` treats a missing column as no-match).

New, under a `§7.6 الملاحق on the article surface` heading:

1. `test_the_appendix_lands_after_the_last_article` — ids are
   `art-1..art-N` then `apx-1..apx-M`, in that order, on an open-tier fixture.
2. `test_appendix_toc_positions_continue_past_the_last_article` — TOC positions
   strictly increasing; every appendix position > `max(article_no)`. Use a HOLED
   index (max_no > row count) so the `max_no`-vs-`len` trap in §2.4 is guarded.
3. `test_a_gated_preview_is_still_three_articles` — no `apx-*` in
   `visible_sections`, and `hidden_section_count == n_articles + n_apx - 3`.
4. `test_the_full_reveal_carries_the_appendix` — `get_full_regulation` on the
   same fixture ends with `apx-*` sections, untruncated.
5. `test_the_public_page_and_the_reveal_agree` — the open-tier doc's section ids
   equal the reveal's section ids. This is the broken-purchase guard from §2.5.
6. `test_html_comments_never_reach_the_reader` — a chunk carrying
   `<!-- converted table -->`, `<!-- نهاية الجدول -->` and `<!-- Page 19 -->`
   renders with none of them, on BOTH the article path and the chunk fallback.
7. `test_a_regulation_with_an_appendix_is_priced_for_it` — 40 مواد + 89 appendix
   chunks → 8 (clamped); 40 مواد + 0 → 2 (unchanged).
8. `test_an_appendix_query_failure_still_renders_the_regulation` — put
   `chunks_v2` in `fail_tables` on an article-surface fixture; the نظام renders
   its مواد, no exception, no ملاحق.

Existing tests that MUST stay green untouched:
`test_a_healthy_regulation_still_renders_article_sections`,
`test_a_flipped_regulation_renders_chunk_sections`,
`test_chunk_stream_order_puts_body_before_appendix`,
`test_the_price_of_a_flipped_regulation_matches_its_rendered_surface`.

---

## §5 Rollout

Order is **frontend → backend → purge**, and it is not interchangeable.

1. **Frontend first.** A frontend that reads `kind` from a payload that has none
   is a no-op (D4), so shipping it early costs nothing. The reverse is not free:
   a page baked with appendix TOC rows against the old frontend anchors every
   ملحق at `#sec-art-{position}`, which does not exist, and the rail silently
   scrolls nowhere.
2. Backend deploy. The ISR pages call the backend at bake time, so nothing on the
   site changes until a page re-bakes — which is what step 3 forces.
3. Purge the 188 affected doc pages through `POST /api/revalidate`
   (`x-revalidate-secret: $REVALIDATE_SECRET`, body `{"path": "/regulations/<slug>"}`).
   The slug is Arabic: **percent-encode it** — an unencoded path returns a
   cheerful `{"revalidated": true}` for a route that was never purged
   ([[project_isr_revalidate_percent_encoding]]). Without the purge the live
   pages serve the old bake for a full day.
4. Eyeball, in this order: `اللائحة-الفنية-الخليجية-للعب-الأطفال` (89 ملاحق —
   should now be ~129 TOC rows and a `126 قسمًا` CTA),
   `اللائحة-التنفيذية-لجودة-الهواء-لنظام-البيئة` (38), and one chunk-path نظام
   (`اللائحة-التنفيذية-لنظام-العمل-وملحقاتها-الجزء-2`) to confirm the comment
   stripping did not disturb it.
5. Signed-in reveal on one gated نظام from the list — that is where 187 of the
   188 actually deliver.

---

## §6 Out of scope — named so nobody assumes they came along

- **Migration 121** (data-side `position` unification). Untouched; its three
  preconditions are unchanged. This plan makes step 3 of that checklist easier to
  satisfy, not redundant — the read path still owns order (D2).
- **`_ground_regulation`** (`ask_service.py:285`) grounds the AskRayhan widget on
  the first `REGULATION_CHUNKS = 4` sections in document order, so a ملحق reaches
  it only on a نظام with ≤ 3 body chunks. Deliberate for now; widening it is a
  retrieval decision, not a rendering one.
- **A real table renderer** for the 1,681 table-carrying appendix chunks —
  `content_display` + `tables` jsonb (D10). Until then those sections render as
  the bulletized prose the ingestion left behind.
- **BM25 navigation search** does not index appendix text; adding it changes what
  `bm25_navigation_search` scores across 9 surfaces.
- **`build_seo_article_index.py`** keeps NOT minting مواد rows out of appendix
  chunks. That is correct — a ملحق is not a مادة — and this plan is what makes it
  harmless.
- **`agents/simple_search/unfold.py`** already treats the appendix stream as a
  first-class partition (`partition_body_appendix`, `measure_chars`,
  `body_before_appendix`). The agent read path was never broken; only the library
  render was.

---

## §7 File manifest

| File | Change |
|---|---|
| `backend/app/services/library_service.py` | `_strip_html_comments`, `_appendix_chunks_for_regulation`, `_regulation_appendix_chunk_count`, `_appendix_sections`; wire into `get_regulation_doc` (both branches), `get_full_regulation` (both branches), `unlock_cost`; `kind` on every TOC row |
| `backend/tests/test_library_gating.py` | `corpus` on `_chunks()`; 8 new tests (§4) |
| `frontend/lib/library/api.ts` | `RegulationTocEntry.kind?` |
| `frontend/app/regulations/[slug]/page.tsx` | appendix-aware TOC anchor; badge |

No migration. No new table. No new route. No corpus write.

---

## §8 What the build found that this plan did not predict

Two things. Both were caught by validating against real data rather than by the
unit tests, which is the note worth keeping.

### 8.1 `response_model` ate the field (caught in the browser)

`kind` reached the payload dict and never reached the browser. `TocEntry` in
`backend/app/api/public_library.py` is a Pydantic response model, and FastAPI
**drops every key the model does not declare** — so every ملحق arrived at the
client as a مادة and anchored at `#sec-art-{position}`, a target that does not
exist. The service tests passed the whole time; the rail scrolled nowhere.

Fixed by declaring `kind: str = "article"` on `TocEntry`. The guard is
`test_the_toc_kind_field_survives_the_response_model`
(`backend/tests/test_library_enforcement.py`), which was confirmed to FAIL with
the field removed and pass with it restored — the file already exists precisely
for bugs that are invisible below HTTP.

⚠ Generalise it: **any** new key on a library payload needs a matching model
field, or it silently does not ship.

### 8.2 HTML comments are NOT appendix-exclusive (caught by the prod sweep)

§0 measured the markers in `chunks_v2` and concluded appendix-only (4,695 of
5,388 appendix chunks, 0 of 43,002 body chunks). That was true of the chunk
stream and wrong about the document: **1,245 مواد across 491 regulations carry an
ingestion marker inside `seo_articles.article_text`** — a bigger blast radius
than the appendix leak, live today on the doc page AND on every مادة page.

So the strip belongs in `_clean_article_display_text` (the display cleaner all
three article surfaces already route through), running FIRST — before the
heading-strip transform, which matches the first line only and would be defeated
by a leading `<!-- Page 19 -->`. `_strip_html_comments` is still applied
separately to chunk bodies on both paths.

D9 stands; its scope was understated.

---

## §9 Validation performed (2026-08-24, pre-deploy)

- `pytest backend/tests/test_library_gating.py backend/tests/test_library_enforcement.py`
  → **198 passed** (10 new appendix tests + the response-model guard).
  Full `-k "library or ask"` run: 1,124 passed, 1 pre-existing unrelated failure
  (`test_wave_8b_legacy_removal.py::test_orchestrator_emits_workspace_item_events_for_mock_tasks`,
  confirmed failing on a clean tree).
- **Corpus-wide prod sweep — all 441 published appendix-owning أنظمة**
  (181 article surface · 260 chunk surface): every one carries its ملاحق in the
  TOC, TOC positions reproduce TOC order everywhere, no ملحق in any gated
  preview, `hidden_section_count` exact on every article-surface نظام, and
  **zero marker leaks corpus-wide**. The sweep's single flag
  (`نظام-الاستثمار-1446ه-2024م`) was a bug in the validator, not the code — that
  نظام is open-tier with 0 `seo_articles`, so it renders through the chunk path
  where sections keep chunk uuids rather than `apx-` ids; re-checked by hand:
  4 sections, all rendered, ملحق last, `kind='appendix'`, no markers.
- **Prod-data payload check** over 8 أنظمة spanning every shape (89-annex toys
  لائحة, annex-dominated جودة الهواء, 46-annex قواعد طرح الأوراق المالية, the
  chunk-path labour ج2): every invariant held — body-then-ملاحق, monotonic TOC
  positions, no ملحق in a gated preview, honest `hidden_section_count`, zero
  markers in either payload.
- **Local browser check** (dev servers, prod DB) on four pages:
  - `نظام التقاعد العسكري` (open tier) — 35 مواد then `sec-apx-1`, rail row
    resolves to it.
  - `اللائحة الفنية الخليجية للعب الأطفال` (gated) — rail 129 rows (40 مواد +
    89 named ملاحق, `#sec-apx-1` … `#sec-apx-89`), preview still 3 مواد, CTA now
    reads **«126 قسماً إضافياً»** (was 37).
  - `اللائحة التنفيذية لجودة الهواء` — 8 مواد then 38 ملاحق, no interleave.
  - `اللائحة التنفيذية لنظام العمل ج2` (chunk path) — unchanged, 60 rows.

### Left for the deploy

§5's rollout has not run. Nothing is live.

### Observed, not fixed

- The rail draws a ملحق row like a مادة row minus the number chip. It reads
  fine, but a «الملاحق» divider would read better — deliberately out of D11's
  scope, and a frontend-only follow-up.
- `نظام التقاعد العسكري`'s single ملحق is the source site's feedback FORM, not
  legal content. A corpus-quality artefact, not a render bug — but it is the one
  open-tier نظام with an appendix, so it is also the only one an anonymous
  visitor can see.
