# Chunk tables on the reading surface — the grid instead of the flattening

**Status:** **BUILT 2026-08-25 — §2/§3/§5 done, validated against the full live
corpus, NOT committed and NOT deployed.** §4 (مراجع) not started. The corpus side
was already DONE and live. Validation report:
`agents_reports/chunk_tables_validation_2026-08-25.md`. Three decisions were
REVISED by contact with real data — **D8** (gate charge), **§2.2** (the allowlist
was destroying law), **§3.1** (blank previews) — each marked ⚠ in place. Read
those before trusting an older copy of this file.
**Written:** 2026-08-24 · every number below MEASURED against prod
(`dwgghvxogtwyaxmbgjod`) on that date or on 2026-08-25
**Corpus-side source of truth (other repo, `agentic_for_ministry`):**
`ingestion/regulation_v2/CHUNK_TABLES_REFERENCE.md` (the consumer contract — read
it first) and `reclassification/chunking_v3/CHUNK_TABLES_PLAN.md` (why it looks
like this)
**Related:** `regulation_appendix_surface.md` (D10 is the hole this fills),
`gate_exposure_budget.md`, `access_tiers_gating_DECISIONS.md`,
`chunk_appendix_position_unification.md`, `references_window_fixes.md`

---

## §0 The finding

Every table in the regulation corpus was OCR'd and then **converted to prose**
before ingestion — a `<!-- converted table -->` marker, a numbered list of
sentences, `<!-- end table -->`. That was a deliberate retrieval decision: it is
what BM25 indexes and what the model reads, and rewriting it would change recall.

The original `<table>` HTML survived on disk and, as of 2026-08-24, is **in our
database**. A reader on `/regulations/…` or in the مراجع popup still sees the
flattened list where a grid belongs, because nothing in this repo reads the two
columns that would fix it:

```
$ grep -rn "content_display\|chunk_tables" --include=*.py --include=*.ts --include=*.tsx .
(no output)
```

### What is live and waiting

| | |
|---|---|
| `public.chunk_tables_v2` rows | **24,511** |
| chunks carrying `content_display` | **8,855** of 48,390 (18.3%) |
| tokens in `content_display` | 24,511 — **0 unresolvable** |
| regulations touched | 1,130 |
| HTML payload | 29.0 MB |

The mechanism, in one line: `chunks_v2.content` keeps the prose (the agent view);
`chunks_v2.content_display` is the same text with each confidently-resolved table
collapsed to a whole-line `TBL_…` token (the user view); `chunk_tables_v2` holds
one row per token with `table_html`, `table_md` (the prose it replaced) and
provenance. Render = walk `content_display`, swap each token line for its table.
`content_display` is NULL when there is nothing to swap — coalesce to `content`.

### The reader-facing prize

Published أنظمة means `seo_item_meta.content_type='regulation' AND slug IS NOT
NULL` — **1,686** of the 3,513 meta rows. Of those:

| | |
|---|---|
| published أنظمة carrying ≥1 table | **532 (31.6%)** |
| tables on those pages | **9,512** |

Roughly one published نظام in three is currently rendering at least one grid as a
bulleted list.

---

## §1 Decisions

**D1 — `content` is never read for display again, and never written at all.**
The display body is `content_display or content`. `content` remains what the
aggregator prompt, BM25, `search_topics` and every reranker see. Nothing in this
plan writes to the corpus.

**D2 — Never embed, index, or prompt on `content_display`.** It has table content
*removed*. Indexing it silently drops tables from search. This is the one way this
change could do real damage, and it is a one-line mistake, so it gets its own
decision and its own test.

**D3 — An unresolved token emits NOTHING.** Never a raw `TBL_17261_reg_501_chunk_003_1`
on a page. Corpus-wide this is currently unreachable (0 unresolvable tokens across
all 24,511), but the renderer must still be safe by construction, because the
local corpus runs ahead of the DB and re-ingests will recur.

**D4 — Resolve by `table_ref`, never by position and never by deriving the token.**
`position` is document order, not render order, and a chunk can hold tables that
were skipped as unsure. The token stem is a *sanitized* `chunk_ref` with a hash
appended whenever sanitizing changed anything — four regulations carry Arabic (one
a space) in their ref. Deriving the token is right 99.96% of the time, which is
the worst possible hit rate.

**D5 — Real HTML, not a markdown table.** Measured: **8,363 of 24,511 (34.1%)**
carry `rowspan` or `colspan` (`colspan` 7,032 · `rowspan` 4,066). GFM markdown
tables cannot express a merged cell, so converting would mangle a third of the
corpus *while trying to restore its structure*. `remark-gfm` is not the answer
here.

**D6 — Sanitize server-side, by re-serializing through an allowlist.** Not a regex
strip — parse and **rebuild**, so anything not on the allowlist cannot survive
malformed input. Python stdlib `html.parser`; no new dependency in either repo.
The corpus is provably clean today (§9.3: 0 `<script>`, 0 `<iframe>`, 0
`javascript:`, 0 real event handlers), which means the allowlist costs us nothing
in fidelity and buys the guarantee that the frontend's `dangerouslySetInnerHTML`
is trusted-by-construction. Today that prop appears in this codebase only around
`JSON.stringify` for JSON-LD; this plan is what makes a second use defensible.

**D7 — Never add `rehype-raw` to `MarkdownRenderer`.** It has none today
(`remarkGfm` + `rehypeHighlight` only), so react-markdown strips raw HTML — a
`<table>` handed to it disappears silently. The fix is *not* to enable raw HTML:
that same component renders **model output**, and enabling it would open an HTML
injection path on generated text to solve a corpus problem. The مراجع popup
segments its body instead (§4.3).

**D8 — A token is charged for what it RENDERS, and is atomic under the gate.**
The load-bearing decision of the whole plan; see §3.1. A token spends
`max(len(table_md), len(visible_text(table_html)))` of the free-char budget. A
**grid** is either rendered whole or not at all — never cut through, because half
a table misrepresents the data. A grid that does not fit **degrades to the prose
it replaced** rather than withholding the section (§3.1's three-case rule); it is
that prose, not the grid, that absorbs the truncation.

⚠ **Revised 2026-08-25 after validating against the real corpus.** The original
rule charged `len(table_md)` alone, reasoning that `md` is byte-identical to the
prose the token replaced, so the budget would buy exactly the law it bought
before. That is right for the corpus as a whole — mean `md` 878 chars vs mean
rendered text 786, so `md` usually charges MORE than the grid shows — and wrong
at the tail, which is the half that leaks:

| | |
|---|---|
| tables rendering >500 chars more than `md` charges | **548 (2.2%)** |
| rendering >2,000 chars more | 33 |
| worst single undercharge | **3,382 chars** |
| tables whose `md` is an ingestion ERROR placeholder | **244** |

Those 244 never got a prose conversion: their `table_md` is
«[خطأ في التحويل - انتهت المهلة]», 31 characters. `17405_reg_603_chunk_019` is
the shape — two 3.2 KB violation-fine grids (م / المخالفة / حد قيمة الغرامة,
`rowspan="2"` headers over `colspan="3"` size bands) whose entire prose form is
that sentence, twice. Charging `len(md)` would serve a complete penalty schedule
to an anonymous crawler for 31 chars of a 600-char budget. `max()` costs nothing
on the 97.8% where `md` already dominates and closes the hole exactly.

The invariant, restated honestly: **a gated preview never exposes more legal
content than it did before this change, and on the 2.2% it exposes less** —
because the prose baseline was leaking those grids for free. Assert the
direction, not equality.

**D8a — Those 244 rows are an AGENT-side defect too, and this plan does not fix
it.** `content` holds the same error string, so the model has been reading
«conversion failed» where a fines table belongs. Restoring the grid for the
reader does not repair retrieval. Logged in §8 as a corpus-side follow-up for
the ingestion repo.

**D9 — v1 renders the chunk-shaped surfaces only; the article surface is named,
not attempted.** See §3.4. That is 84.3% of the tables on published pages for a
fraction of the work, and the remainder is a genuinely different (and mostly
pre-existing) problem.

**D10 — مراجع fetches tables on the REVEAL only, never in the live search turn.**
`ura/enrich._enrich_regulations` is shared by the deep_search pipeline and by
`references_service._build_reg_shells`. Adding table HTML to the live path would
put up to 29 MB of markup near persisted retrieval artifacts and would cost every
turn for a body the user opens on 7.7% of citations. A `with_tables: bool = False`
kwarg — default off — mirrors the existing `with_summary` flag on
`_build_case_shells`, which exists for exactly this reason.

**D11 — «نسخ المحتوى» copies text, not markup.** `SourceCopyButton` in the مراجع
dialog receives `extractSourceContent(view)`. A user pasting a source into a
memo must get `table_md`, not `<table>`. The copy string and the render string
diverge here, deliberately.

**D12 — No migration, no new table, no corpus write, no new route.** Everything
this plan needs already exists in the database.

---

## §2 The shared renderer — `shared/library/chunk_tables.py`

New module, sitting beside `reg_status.py` / `case_sources.py` in the layer both
`backend/` and `agents/` already import from. **One** implementation; the library
and مراجع both call it, so the two surfaces cannot drift.

```python
TABLE_PLACEHOLDER = re.compile(r"^[ \t]*(TBL_[A-Za-z0-9_]+)[ \t]*$", re.M)
```

Whole line, nothing else on it. `[A-Za-z0-9_]+` and not something looser is what
makes both token shapes (plain and hash-suffixed) match — do not relax it.

### 2.1 Public surface

```python
@dataclass(frozen=True)
class ChunkTable:
    table_ref: str
    html: str          # ALREADY sanitized — see sanitize_table_html
    md: str            # the prose it replaced; alt text, copy text, gate weight

def display_body(chunk: dict) -> str:
    """`content_display` or `content`. THE body-choosing rule, in one place."""

def sanitize_table_html(raw: str) -> str:
    """Allowlist re-serializer. Returns '' if the result holds no <td>/<th>."""

def split_body(body: str, tables: Mapping[str, ChunkTable]) -> list[Segment]:
    """Body -> alternating prose / table segments. Unresolved tokens DROPPED."""

def render_text_only(body: str, tables: Mapping[str, ChunkTable]) -> str:
    """Tokens -> `table_md`. The copy string, and every text-only channel."""
```

`Segment` is `{"kind": "text", "text": str}` or `{"kind": "table", "ref": str,
"html": str, "md": str}`. Both consumers build their payload from segments; nobody
re-implements the regex.

### 2.2 The allowlist

Elements: `table thead tbody tfoot tr th td caption br b strong i em sup sub`
plus `ul ol li dl dt dd` — a bulleted list inside a `<td>` is real structure, and
226 corpus tables carry one.
Attributes: `rowspan` and `colspan` **only**, and only when the value parses as an
int in `1..100`. Everything else — `style` (55 rows), `class`, `id`, `width`,
`align` — is dropped; the app's own styling owns presentation.

**Unwrapped** — tag dropped, text **kept**: `a` (42 rows), `p`, `span`, `div`,
`u`, `font`, `center`, `small`, `big`, `h1`–`h6`, `section`, `article`, `label`,
`abbr`, `code`, `pre`, `blockquote`, `figure`, `figcaption`.

⚠ **Revised 2026-08-25 — the first version of this list destroyed law.** Every
presentational wrapper the corpus uses must be named in the *unwrapped* set, not
merely left off the element list: an unlisted element is dropped **with its
content**. That is correct for `<img>`/`<script>` and catastrophic for a layout
wrapper. Full-corpus validation caught it:

| tag opening the dropped scope | tables | chars eaten |
|---|---|---|
| `ul` | 218 | 149,116 |
| `div` | 43 | 13,725 |
| `ol` | 17 | 7,568 |
| `h3` / `h2` / `u` / `dl` | 39 | 2,604 |
| | **325** | **177,060** |

24 of those rendered as a **visible blank grid** — cells intact, text gone — and
the proof-of-life test passed on the corpse, so D3's "emit nothing" never fired.
After the fix: text loss **399 chars across 73 tables** (max 30 each), and a
tag-level audit of all 24,511 outputs finds **0** non-allowlisted tags and **0**
non-allowlisted attributes.

Two consequences baked into the module:

1. **Proof of life is TEXT, not tags.** `sanitize_table_html` returns `""` when
   no *visible text* survived, not merely when no `<td>` survived. 79 corpus
   tables are genuinely empty in the source (`<table><tr><td></td></tr></table>`)
   and now correctly resolve to nothing — verified: **0** of the 79 held any
   visible text to begin with, so no law is lost.
2. **A dropped-but-unclosed scope ends at the next structural tag.** 7 tables
   carry a wrapper that is never closed, which otherwise swallows every
   remaining cell — the hazard the void-element guard covers for `<img>`, met
   again through a non-void element that simply isn't closed.
Dropped elements, content and all: `img` (252 rows), `form`/`input`/`button`
(4 rows), and anything else unrecognised. `<img>` goes because the CSP
(`img-src 'self' https://*.supabase.co https://img.youtube.com data:`) would block
these external hosts anyway and render a broken-image icon inside a statute.

An empty result — no `<td>` and no `<th>` survived — returns `""`, which D3 turns
into "emit nothing".

### 2.3 Why re-serialize instead of filter

A regex that strips `<script>` can be defeated by markup a regex cannot parse. A
re-serializer emits only tags it decided to emit, so the output is a function of
the allowlist and not of the input's shape. The corpus is clean today; the point
is that it stays safe when it isn't.

---

## §3 Backend — the library

### 3.1 The gate, which is the hard part

Today: `body = _strip_html_comments(content)` then
`truncate_for_gate(body, gate, free_chars=N)` — a raw character cut at the last
whitespace inside the window. Feed it a body containing token lines and it breaks
in two directions at once. Measured over all 8,855 chunks carrying
`content_display`:

| | |
|---|---|
| chunks with a token inside the first 600 chars | **6,920 (78.1%)** |
| chunks where a 600-char cut lands **mid-token** | **191** |
| mean `content_display` length | 971 chars (vs. a 600-char budget) |

- **Mid-token cut → a raw `TBL_17630_reg_5…` fragment ships to an anonymous
  crawler.** Worse than the raw-token case D3 guards, because the fragment is no
  longer a whole line and the renderer's regex will not even recognise it.
- **A whole token costs ~30 chars of budget and renders ~880 chars of law**
  (mean `table_md` = 878, p95 = 2,483). A 600-char preview holding two tables
  quietly triples its own exposure — precisely what `gate_exposure_budget.md`
  exists to stop.

**The fix (D8).** A new pure function beside `truncate_for_gate`:

```python
def truncate_segments_for_gate(
    segments: list[Segment], gate: str, *, free_chars: int
) -> dict[str, Any]:
    """Same contract as truncate_for_gate, over segments instead of a string.

    Walks segments in order against a remaining budget:
      - a TEXT segment is charged len(text); if it does not fit, it is cut at the
        last whitespace inside the remaining window (today's rule, unchanged) and
        the walk STOPS.
      - a TABLE segment, against remaining budget R:
          1. weight <= R  -> render the GRID, charge weight, CONTINUE.
          2. len(md) <= R -> render its PROSE (md), charge len(md), CONTINUE.
          3. otherwise    -> render md CUT to R (last-whitespace rule), STOP.
        A GRID is never cut through — half a table misrepresents the data, and
        that is what atomicity protects. What case 3 cuts is prose, which cuts
        fine and is exactly what today's gate cuts in that same region.
    Returns {"visible_segments", "is_truncated", "hidden_placeholder_lines"},
    with hidden_placeholder_lines sized off the WEIGHTED remainder so the
    placeholder bars keep meaning what they mean today.
    """
```

Charging a table at `len(table_md)` alone is not a heuristic — `table_md` is
*literally the prose that used to occupy those bytes* — but it undercharges 548
real tables and badly undercharges the 244 whose prose conversion failed
outright (D8). `weight` takes the max against what the grid actually renders, so
the budget can only ever buy LESS law than it bought yesterday, never more.
That direction — not equality — is what §6 tests.

`truncate_for_gate` itself is untouched: every non-regulation caller (judgments,
circulars, forms, guides, the شرح teaser) keeps calling it with a string and gets
today's behaviour exactly.

⚠ **Revised 2026-08-25 — the first version withheld the whole section.** The
original rule withheld an over-budget table and STOPPED, which punished a section
for one oversized grid. Measured over all 8,855 chunks at `free_chars=600`, that
produced **2,640 blank previews (29.8%) across 363 أنظمة**, and after a
section-level fallback keyed on *zero* it still left **2,077 chunks across 182
published أنظمة** with a median surviving lead of **83 characters** where today
serves ~600 — thin content on indexed pages, the exact failure `GateBudget.floor`
exists to prevent.

The granularity was the bug. Degrading the **table** rather than the **section**
dissolves both: case 3 always fills the budget, so nothing is blank or thin, and
— unlike a section-level fallback — a grid that DOES fit still renders beside one
that does not. Cases 2 and 3 show `md` and charge `md`, which is byte-for-byte
what `truncate_for_gate(content, …)` ships for that region, so exposure stays
≤ today step for step.

⚠ `spend_budget_across_sections` / `gate_decision` — the document-wide exposure
budget — is currently wired into **judgments only** (`JUDGMENT_BUDGET`,
`library_service.py:5841`). Regulations still run the absolute per-section budget
(`free_chars=600` at :3188, `ARTICLE_FREE_CHARS` at :4084). This plan does not
migrate regulations onto that layer; it makes the segment walk take a
`remaining` budget so that when regulations do move, the two compose without a
rewrite.

### 3.2 Where the body is chosen

Four call sites read chunk bodies for display. All four move to
`display_body()` + `split_body()`:

| site | what it feeds | corpus reached |
|---|---|---|
| `library_service.py:3134` `_ordered_chunk_query(..., "id, title, position, content")` — the chunk-fallback doc | `visible_sections` on `/regulations/[slug]` | `without_articles` body |
| `_appendix_chunks_for_regulation` :2729 (`"id, title, content"`) → `_appendix_sections` | the ملاحق sections (new, undeployed) | `appendix` |
| `_chunk_row_map` :2796 (`"id, title, content"`) → `_article_sections` fallback bodies | non-extracted مواد on the article surface | a sliver (§3.4) |
| `get_full_regulation` :6712 — the paid reveal, chunk branch | `FullContentGate` | all of the above, untruncated |

Each grows `content_display` in its select list and one batched
`chunk_tables_v2` read per regulation (`table_ref, chunk_id, table_html,
table_md`, filtered on `regulation_id` — `idx_chunk_tables_reg` covers it, one
round trip per document, not per chunk).

Fail-soft direction, and it differs per site by the same logic already documented
there: a `chunk_tables_v2` read that fails returns `{}` → every token resolves to
nothing → the section renders as prose-minus-its-tables. That is a **degradation,
not a leak**, and it must never raise. But note it is *not* the same as today's
output, so a persistent failure silently deletes content — §6.7 asserts the
fallback is to `content` (prose intact) rather than to `content_display` with
empty tables. Concretely: **if the tables read fails, fall back to `content`.**

### 3.3 The wire

`RegulationVisibleSection` (`frontend/lib/library/api.ts:323`) grows one optional
field, following the additive-and-optional rule these ISR-baked payloads already
live by (`kind`, `also_ids`):

```ts
/**
 * Rendered tables for this section, keyed by the `TBL_…` token that stands in
 * for each one inside `text`. Sanitized server-side (allowlist re-serialize);
 * the client renders it as HTML and never parses it.
 *
 * Optional on the wire: a page baked before this shipped carries no `tables`,
 * and `text` for such a page is the prose body — so absent ⇒ exactly today's
 * rendering, with no token to leave dangling.
 */
tables?: Record<string, { html: string; md: string }>;
```

⚠ **The ISR interaction is a real trap.** `text` and `tables` must be baked
together. A payload whose `text` came from `content_display` but whose `tables`
went missing renders naked tokens — which is why the frontend drops any token it
cannot resolve (§5.2) rather than trusting the pair to always arrive intact.

### 3.4 What v1 does NOT reach, stated plainly

On the article surface the document is built from `seo_articles.article_text`,
which is a slice extracted from **`content`** — the token is not in it, and the
chunk's `content_display` cannot be applied to a fragment of a different string.

Of the 9,512 tables on published pages:

| bucket | tables | v1 |
|---|---|---|
| `without_articles` body, chunk-surface regs | 5,156 | ✅ |
| `appendix`, chunk-surface regs | 1,396 | ✅ |
| `appendix`, article-surface regs | 1,465 | ✅ (needs `regulation_appendix_surface.md` deployed) |
| body chunks reachable via the article surface's chunk fallback | ~6 | ✅ free |
| **v1 total** | **8,017 — 84.3%** | |
| body chunks with **no `seo_articles` row at all** | 982 | ❌ — those chunks are absent from the page entirely today, tables or not. A coverage gap, not a table gap; belongs to `regulation_article_coverage_fallback.md`. |
| body chunks whose text ships as extracted `article_text` | 507 | ❌ — needs `table_md`-substring matching inside `article_text`. §8. |

So: 84.3% of the prize for the chunk-shaped work, and the 15.7% that remains is
two-thirds a pre-existing article-surface coverage hole that this plan would not
have fixed anyway.

---

## §4 Backend + agents — مراجع

Measured demand: of **3,706** regulation citations written to
`workspace_item_references` to date, **284 (7.7%)** point at a chunk that carries
a table. Real, and smaller than the library's — which is why it ships second.

### 4.1 The fork that must not close

`ura/enrich._fetch_chunks` selects `content` into `RegURAResult.chunk_content`,
and `chunk_content` is projected by `for_aggregator()` (`ura/schema.py:335`)
straight into the synthesis prompt. It stays prose (D1/D2). The display body
travels beside it, in fields excluded from `for_aggregator()` — the same
"stored only" treatment `chunk_context`, `pdf_url` and `owns` already have:

```python
chunk_display: str = ""                 # stored only — content_display or ""
chunk_tables: list[dict] = Field(default_factory=list)   # stored only
```

Leaving both out of `for_aggregator()` keeps the prompt surface byte-identical,
which also keeps the prompt cache prefix intact — the same argument `doc_type`
is already excluded under.

### 4.2 Reveal-only fetch (D10)

```python
async def _enrich_regulations(reg_results, supabase, *, with_tables: bool = False)
```

`enrich_ura` (live turn) leaves it False → not one extra byte on the hot path.
`references_service._build_reg_shells` (:694) passes `with_tables=True`, because
that function runs on the **click**, rebuilding one shell from the DB.

Then `source_viewer._build_reg_view` gains the display fields on
`ChunkSourceView`:

```python
content: str          # UNCHANGED — the prose. Copy text, text-only fallback.
display_segments: list[dict] = []   # [] ⇒ render `content`, exactly as today
```

Emitting `[]` whenever the chunk has no tables means every persisted pre-2026-08
artifact keeps rendering through the existing path with no compat branch.

### 4.3 Frontend — segmented, not raw markdown (D7)

`ReferencePanel`'s `source_type === "chunk"` arm (:1234) today is
`<MarkdownRenderer content={sourceContent} />`. It becomes: map over
`display_segments`, rendering text runs through `MarkdownRenderer` (unchanged —
still no raw HTML) and table segments through the shared `<ChunkTable>` component
from §5.1. When `display_segments` is empty it renders exactly the line it renders
now.

`extractSourceContent` (:1117) is **not** touched — it keeps returning
`view.content`, i.e. the prose, which is what «نسخ المحتوى» should paste (D11).

---

## §5 Frontend — the library

### 5.1 One table component

`frontend/components/library/blocks/ChunkTable.tsx` — server component, used by
both the library and the مراجع popup.

```tsx
<figure className="my-4 -mx-1 overflow-x-auto" dir="rtl">
  <div
    className="text-sm [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_th]:border …"
    dangerouslySetInnerHTML={{ __html: html }}   // sanitized server-side (D6)
  />
</figure>
```

⚠ **The inner element is a `<div>`, never a `<table>`.** `table_html` is the bare
`<table>…</table>` fragment — all 24,511 rows start `<table` and end `</table>` —
so feeding it to a `<table>`'s `dangerouslySetInnerHTML` nests a table inside a
table, which the HTML parser resolves by foster-parenting the inner one out,
silently and inconsistently across browsers. The fragment brings its own root;
the wrapper only styles it from the outside, which is why every cell selector is
a descendant (`[&_td]`) rather than a child.

Three things the wrapper has to get right:

- **`overflow-x-auto` on the figure, never on the page.** Some of these are wide
  (max `table_html` 12,653 chars). The recent `cf8dafd` "870px of cards behind a
  0px scrollbar" bug is the same failure mode arriving through a different door —
  the scroll container must be the figure, and the page body must never scroll
  horizontally because of one annex.
- **`dir="rtl"`** — the content is Arabic and column order is meaningful.

Digits inside a table are **corpus body text**, which is an explicit carve-out
from the Latin-numerals policy: whatever the statute printed is what renders. Do
not normalize digits inside `table_html`.

### 5.2 The block seam

`toLegalBlocks` (`lib/library/legal-text.tsx`) already lifts pre-formatted legal
text into typed blocks, and the brand-new `LegalBlocks.tsx` renders them with
per-type vertical rhythm. That is exactly the right seam:

```ts
| { type: "table"; ref: string; html: string }
```

`toLegalBlocks` gains a `tables?: Record<string, {html: string}>` parameter. A
line matching `TABLE_PLACEHOLDER` becomes a `table` block when the ref resolves,
**and is dropped entirely when it does not** (D3, enforced on the client too — the
server should never send an unresolvable token, and the client must not print one
if it does). `LegalBlocks` gains one `case "table"` returning `<ChunkTable>` with
a `mt-4` gap. Roughly 25 lines across the two files.

`ArticleBody` passes `tables` through when `plain` is set. Its other five
consumers (circulars, forms, judgments, guides, `GateBanner`) pass nothing and are
unaffected.

---

## §6 Tests

`shared/library/tests/test_chunk_tables.py`:

1. `test_a_chunk_with_no_display_renders_its_content` — `content_display=None` ⇒
   body is `content`, zero segments of kind table. The 82% case.
2. `test_an_unresolved_token_emits_nothing` — a token with no row leaves no trace
   in text or segments. Assert the literal string `TBL_` is absent from output.
3. `test_a_derived_token_is_never_used` — a fixture whose `chunk_ref` holds Arabic
   and whose `table_ref` is the hashed form resolves correctly; the naive
   `TBL_{chunk_ref}_{n}` form does not appear.
4. `test_the_sanitizer_keeps_merged_cells` — `rowspan`/`colspan` survive with
   their values; `style`, `class`, `width` do not.
5. `test_the_sanitizer_drops_active_markup` — `<script>`, `<img>`, `on*=`,
   `javascript:` href, `<iframe>` all gone; a table that is *only* those returns
   `""`.
6. `test_render_text_only_reproduces_the_prose` — `render_text_only(display,
   tables)` normalizes equal to `content`. This is the strongest available proof
   that the two views carry the same law, and it can run over a live sample.

`backend/tests/test_library_gating.py` (extend):

7. `test_a_tables_read_failure_falls_back_to_prose` — put `chunk_tables_v2` in
   `fail_tables`; the section renders `content` (prose tables intact), not
   `content_display` minus its tables. §3.2's named hazard.
8. **`test_the_gate_is_neutral_to_tables`** — the headline assertion. For a
   fixture chunk, the visible *legal* character count (text + `table_md` of every
   rendered table) at `free_chars=600` equals the visible character count of
   truncating `content` at 600. This is D8, made checkable.
9. `test_a_table_is_never_cut_through` — a budget that lands mid-table withholds
   it whole; `visible_segments` ends on a text segment; no partial `<table>`.
10. `test_no_token_survives_truncation` — over a fixture set including the
    mid-token-cut shape, `TBL_` never appears in `visible_text` at any
    `free_chars` from 0 to `len(body)`. Property-style, cheap, and it is the 191-chunk
    bug.
11. `test_a_gated_preview_still_withholds` — `MIN_WITHHELD_*` still hold once
    tables are weighted in.

`backend/tests/test_reference_source.py` (extend):

12. `test_the_live_search_turn_fetches_no_tables` — `enrich_ura` leaves
    `chunk_tables` empty and issues no `chunk_tables_v2` query (D10).
13. `test_for_aggregator_is_byte_identical` — the projected prompt block for a
    table-bearing chunk is unchanged from before this plan (D2, and the prompt
    cache).
14. `test_the_reveal_carries_segments` — `with_tables=True` fills
    `display_segments`; `content` still holds the prose for the copy button (D11).

Existing tests that must stay green untouched:
`test_chunk_stream_order_puts_body_before_appendix`,
`test_a_healthy_regulation_still_renders_article_sections`,
`test_html_comments_never_reach_the_reader`,
`test_the_public_page_and_the_reveal_agree`.

---

## §7 Rollout

Order is **shared → frontend → backend → purge**, and it is not interchangeable.

0. **`regulation_appendix_surface.md` ships first.** It is built and undeployed,
   and 1,465 of v1's 8,017 tables ride its `_appendix_sections`. Flip its **D10**
   from "render `content`" to "render `content_display`" as part of *this* plan's
   backend step, not its own — D10 is written as a deferral waiting on exactly
   this renderer.
1. **Shared module + tests.** No consumer yet; merges harmlessly.
2. **Frontend.** A client reading `tables` from a payload that has none is a
   no-op, so it ships early at zero cost. The reverse is not free.
3. **Backend deploy.**
4. **Purge.** ISR pages call the backend at bake time, so nothing changes on the
   live site until a page re-bakes. Purge the **532** affected regulation pages
   through `POST /api/revalidate` (`x-revalidate-secret`, body `{"path":
   "/regulations/<slug>"}`). The slugs are Arabic: **percent-encode them**, or the
   route returns a cheerful `{"revalidated": true}` for a path that was never
   purged. Purging is mandatory, not optional — a Docker-cached bake will
   otherwise serve the old payload for a full day.
5. **Eyeball, in this order:** a `without_articles` نظام (the 5,156-table bulk),
   then `اللائحة-الفنية-الخليجية-للعب-الأطفال` (89 ملاحق — CMR tables and
   migration limits are the whole point of that document), then one wide table on
   a phone to confirm the figure scrolls and the page does not.
6. **مراجع last**, after the library has been live long enough to trust the
   renderer, since it is 7.7% of citations and shares every component.

---

## §8 Out of scope — named so nobody assumes they came along

- **The article surface** (507 tables inside extracted `article_text`). Needs
  `table_md`-substring matching within the article slice. Worth its own small
  plan once v1 is live and the renderer is proven.
- **The 982 tables in body chunks with no `seo_articles` row.** Those chunks do
  not render *at all* today. That is `regulation_article_coverage_fallback.md`'s
  problem and fixing it would surface their tables for free.
- **The ~3% unsure blocks.** 706 blocks corpus-wide could not be matched to a
  source with confidence and deliberately keep their prose in both views. They
  degrade to exactly today's rendering, invisibly. Correct, and not a bug report.
- **The 244 failed prose conversions (D8a) — a corpus-side bug for the ingestion
  repo, found by this plan's validation and not fixable from here.** Their
  `table_md` AND the corresponding block in `chunks_v2.content` both read
  «[خطأ في التحويل - انتهت المهلة]», so the *agent* has been retrieving an error
  string where a fines table belongs — `17405_reg_603_chunk_019` is 125 chars of
  `content`, of which 62 are that message twice, standing in for two 3.2 KB
  penalty grids. The user view is repaired by this plan (the grid renders); the
  retrieval view is not, and re-running the conversion means rewriting `content`,
  which recomputes `word_count` and BM25 and therefore belongs upstream. Worth
  filing against `agentic_for_ministry` with the 244 `table_ref`s attached.
- **Images.** `chunks_v2.images` / `has_images` / `image_count` are empty
  corpus-wide and the machinery would be identical. Tables only.
- **1,796 appendix chunks leak raw markers into `content`.** An upstream ingestion
  artifact. `content_display` sidesteps it for the 8,855 table-bearing chunks, and
  `_strip_html_comments` already covers the display path for the rest; cleaning
  `content` itself is the other repo's follow-up (it mutates ingested text and
  recomputes `word_count`).
- **`ask_service._ground_regulation`** grounds the AskRayhan widget on chunk
  `content`. It is an agent surface — D1/D2 apply, it keeps the prose.
- **Blog snapshots / `PublicAnswerView`.** They render persisted source views; they
  inherit whatever §4 produces, and nothing here targets them.
- **Moving regulations onto `spend_budget_across_sections`.** Named in §3.1 as the
  thing this composes with, deliberately not done here.

---

## §9 Measurements — 2026-08-24, prod

### 9.1 Corpus

| | |
|---|---|
| `chunk_tables_v2` rows | 24,511 |
| chunks with `content_display` | 8,855 / 48,390 |
| tokens found in `content_display` | 24,511 |
| **unresolvable tokens** | **0** |
| `table_md` empty | 0 (min length 27) |
| `table_html` empty | 0 |
| fragments starting `<table` / ending `</table>` | 24,511 / 24,511 |

### 9.2 Shape

| | |
|---|---|
| mean / p50 / p95 / max `table_html` | 1,182 / 892 / 2,988 / 12,653 chars |
| mean / p95 `table_md` | 878 / 2,483 chars |
| `colspan` | 7,032 (28.7%) |
| `rowspan` | 4,066 (16.6%) |
| **either** | **8,363 (34.1%)** |
| `<th>` | 16,370 · `<thead>` 0 |
| `<br>` | 8,641 |

### 9.3 Safety

| | |
|---|---|
| `<script>` / `<iframe>` / `javascript:` | 0 / 0 / 0 |
| real `on*=` handlers | **0** (2 regex hits are the string `Ammonia =`) |
| `<img>` | 252 · `<a>` 42 · form controls 4 · `style=` 55 |

### 9.4 Distribution and reach

Tables by corpus, whole corpus: `without_articles` 15,511 (63%) · `appendix`
6,735 (27%) · `with_articles` 2,265 (9%).

Published (slugged) أنظمة: 1,686 total, **532 carry tables**, **9,512 tables**;
270 of those regs are on the article surface, 262 on the chunk surface, 205 carry
appendix tables. v1 reach 8,017 / 9,512 = **84.3%** (§3.4).

### 9.5 The gate hazard

| | |
|---|---|
| table-bearing chunks with a token in the first 600 chars | 6,920 / 8,855 (78.1%) |
| chunks where a 600-char cut lands mid-token | **191** |
| mean `content_display` length | 971 chars |
| budget a token spends today vs. law it renders | ~30 chars vs. ~880 chars |

### 9.6 مراجع

| | |
|---|---|
| regulation citations written to date | 3,706 |
| citations pointing at a table-bearing chunk | **284 (7.7%)** |
| distinct chunks cited | 1,869 |

---

## §10 Reproducing these numbers

```sql
-- 9.1 the data contract, end to end
with toks as (
  select c.id as chunk_id,
         (regexp_matches(c.content_display,'^[ \t]*(TBL_[A-Za-z0-9_]+)[ \t]*$','gn'))[1] as tok
  from public.chunks_v2 c where c.content_display is not null)
select count(*) as tokens,
       count(*) filter (where not exists (
         select 1 from public.chunk_tables_v2 ct
         where ct.table_ref=t.tok and ct.chunk_id=t.chunk_id)) as unresolvable
from toks t;

-- 9.5 the gate hazard
select count(*) filter (where left(content_display,600) ~ 'TBL_[A-Za-z0-9_]+') as head_has_token,
       count(*) filter (where left(content_display,600) ~ 'TBL_[A-Za-z0-9_]*$') as cut_mid_token
from public.chunks_v2 where content_display is not null;
```

`§3.4`'s coverage table and `§9.6` are longer; both are reconstructible from
`seo_item_meta` (`content_type='regulation' AND slug IS NOT NULL`),
`seo_articles`, and `workspace_item_references` (`domain='regulations'`,
`ref_id LIKE 'reg:%'`).
