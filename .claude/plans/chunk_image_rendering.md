# Chunk images on the reading surface — the figure instead of the filename

**Status:** **DEPLOYED 2026-08-30** — commit `5478194`, both services green,
**175/175 ISR pages purged**, verified live: real `<figure>` + «الصورة N: {title}»
on the reading surface and **0 raw filename leaks / 0 leaked `IMG_` tokens** on
every page sampled. 2,355 backend+shared tests pass. Corpus side was already live
(`public.chunk_images`, 5,347 rows, ingested 2026-08-29).
⚠ Two consequences worth knowing before touching this again: the caption number
is minted BEFORE the gate (stable across entitlement — a gated page can open on
«الصورة 12»; do not "fix" it into contiguity), and figures are usually WITHHELD
on gated previews because D10 charges the transcription against a ~600-char
budget — that is the anti-scraping rule working, and it means the reader-facing
payoff lands on open-tier pages and the paid reveal.
**Corpus-side source of truth (other repo, `agentic_for_ministry`):**
`ingestion/chunk_images/REFERENCE.md` (the consumer contract — read it first)
and `ingestion/chunk_images/PLAN.md` (why it looks like this).
**Related:** `chunk_table_rendering.md` (the plan this one is the sibling of —
same seams, same module shape, and its §8 named this feature as "identical
machinery, images excluded"), `compliance_service_guides.md` (§4/§5 — the guide
screenshots are the same problem solved once already, on a wing with no gate),
`regulation_appendix_surface.md`, `gate_exposure_budget.md`,
`access_tiers_gating_DECISIONS.md`.

---

## §0 The finding — this one is a LIVE BUG, not a missed improvement

The tables plan opened on a *degradation*: a grid rendered as a bulleted list,
correct but ugly. This one opens on a *defect*. A regulation chunk is Arabic
markdown, and 1,839 chunks carry image markup that points at a file no app can
reach:

```
![img-1.jpeg](images/page_005_img_001.jpeg)
```

Nothing in this repository has ever looked at it:

```
$ grep -rn "images/\|chunk_images" --include=*.py --include=*.ts --include=*.tsx .
(no output — the only hits are blog tokens and the guides bucket)
```

So all three consumers ship it, each breaking in its own way:

| surface | what a user or a model gets today | reach |
|---|---|---|
| `/regulations/{slug}` | the **literal string** `![img-1.jpeg](images/page_005_img_001.jpeg)` printed as body text. The library body runs the `plain` path (`toLegalBlocks` → `LegalBlocks`), which parses no markdown — the line falls into the paragraph buffer and `renderInline` prints it verbatim. | **168 published أنظمة · 1,956 spans** |
| مراجع popup | `MarkdownRenderer` (react-markdown + remark-gfm, no `img` override) emits `<img src="images/page_005_img_001.jpeg">` — a **relative** URL against the app origin. 404, broken-image icon inside a cited statute. | **56 citations** already written |
| aggregator / synthesis | `RegURAResult.chunk_content` is `chunks_v2.content` verbatim, so the model reads **filenames** where a diagram belongs, and reads nothing at all where the figure carries the answer. | 61 of 3,800 reg citations (1.6%) |

There is no prose fallback baked into `content` the way there is for tables:
where a table was flattened into sentences before ingestion, a figure was
flattened into **nothing but its own path**. That single asymmetry is what makes
this plan bigger than its sibling: it has to repair the **agent view** too, not
only the user view.

### What is live and waiting

| | |
|---|---|
| `public.chunk_images` rows | **5,347** |
| `uploaded_at IS NULL` (bytes missing) | **0** |
| `cited` (markup exists) / `orphan` (recovered, no markup) | 3,677 / 1,670 |
| chunks carrying a figure (`chunks_v2.has_images`) | **1,598** of 48,429 (3.3%) |
| regulations touched | **418** of 3,951 |
| bucket `regulation-images` | **public** (verified: `storage.buckets.public = true`) |
| bytes in Storage | 339.7 MB · mean 63.5 KB · max **4.67 MB** |
| `mime_type` | jpeg 4,772 · **png 575** |
| `title` / `description` / `transcribed_text` | never-empty short label (4–77 chars) · 98–2,008 chars · p50 31, p95 755, **max 4,854** |

### The reader-facing prize

Published أنظمة means `seo_item_meta.content_type='regulation' AND slug IS NOT
NULL` — **1,689** rows. Of those:

| | |
|---|---|
| published أنظمة carrying ≥1 figure | **175 (10.4%)** |
| figures on those pages | **1,976** |
| published أنظمة printing raw markup today | **168** |
| raw spans on published pages today | **1,956** |

Ten percent of the published wing, not a third — but where the tables plan
*improved* a page, this one *stops it printing a filename*.

---

## §1 Decisions

**D1 — This plan repairs BOTH views, and that is the whole difference from
tables.** `chunk_table_rendering.md` D1/D2 could leave the agent alone because
`content` already held the table as prose. Here `content` holds
`![img-1.jpeg](images/…)` — dead weight in every direction. So there are **three**
strings after this ships, and they must be named apart or they will be confused:

| | what it is | who reads it |
|---|---|---|
| `chunks_v2.content` | the corpus, byte-identical, **never written** | BM25, `search_topics`, embeddings, the reranker |
| the **agent body** | `content` with every span replaced by the figure's *words* | `for_aggregator()` → synthesis |
| the **display body** | `content_display or content` with every span replaced by the figure's *pixels* | `/regulations/{slug}`, مراجع |

Both replacements happen at READ time. Nothing in this plan writes to the
corpus, and no migration is needed — the table, the bucket and the CSP
(`img-src … https://*.supabase.co …`) are all already in place.

**D2 — `content` is still what is indexed, embedded and reranked, unchanged.**
Substituting into the retrieval path would change recall and is a different
project. Measured, so the cost of *not* doing it is on the record: image markup
is **6.1% of `content` on average**, over half on only **16** chunks, and only
**23** chunks have under 200 characters of real prose once the spans are
removed. §8 carries it as a follow-up with those numbers attached.

**D3 — An unresolved span emits NOTHING, and here that rule actually fires.**
For tables it was defensive: 0 of 24,511 tokens were unresolvable. For images
**656 chunks carry markup with no row at all** — 154 of them on published pages,
**298 spans** — because the vision pass judged those figures decorative, or they
sit in regulation front matter, or they could not be attached to a chunk. Those
spans have no image and never will. Deleting them *is* the fix, not a fallback.

**D4 — Resolve by `source_basename`, and replace the SPAN, never the line.**
`source_basename` is the basename inside `![…](images/NAME)`, and
`(regulation_ref, source_basename)` is unique. **47 of the 3,677 cited spans sit
inline inside a prose sentence** (measured against live `content`: 3,630 of
3,677 are whole-line). A whole-line rule silently drops every one of those and
leaves the sentence looking finished. A span replace gives the identical result
on the other 3,630, so it is strictly safer and never worse. Never key on
`meta->>'n'` — see D8.

**D5 — Orphan figures render as a block after the chunk's last line, ordered by
`meta->>'n'`.** 1,670 figures across 484 chunks have no markup — recovered from
the source PDF and placed by line-provenance (`position_source='predicted'`).
Median 2 per chunk, p90 8, **max 31**. The two populations can never
double-show: the corpus invariant is that every orphan row matches nothing in
its chunk's `content`, and §6 re-asserts it against our own DB.

**D6 — `uploaded_at IS NULL` ⇒ treat as unresolved.** 0 rows today. A URL for
absent bytes is a 404 inside a statute, which is the exact thing this plan
exists to stop, so it is checked in code and not asserted in a comment.

**D7 — Build every URL from `storage_path`, never from `image_ref` + `.jpeg`.**
**575 of 5,347 are PNG.** `storage_path` already carries the right extension.
The bucket is public, so the URL is plain and unsigned:
`{SUPABASE_URL}/storage/v1/object/public/regulation-images/{storage_path}` —
built through `get_settings().SUPABASE_URL` exactly as `_guide_image_base()`
already does, so a restore into another project finds its own images.

**D8 — «الصورة {N}» is a RENDER-ORDER counter minted by the renderer. It is
never `meta->>'n'` and never `meta->>'n_in_chunk'.** This is the decision most
likely to be "simplified" back into a bug, so here is the measurement that
settles it:

| candidate | why it fails |
|---|---|
| `meta->>'n'` — index within the **regulation** | **120 of 418 regulations have gaps** (the 9,310 decorative figures were counted and then not ingested). Worst case a نظام whose figures are numbered 1, 47, …, 414 for **31** actual images — a gap of **383**. A reader sees «الصورة 402» and concludes 401 figures are missing. |
| `meta->>'n_in_chunk'` | Unique per chunk (0 duplicates) but **only 1,443 of 1,598 chunks are contiguous 1..k** — 9.7% show a hole. And it restarts every chunk, so a document page with twelve one-figure sections prints «الصورة 1» twelve times. |
| **render order, minted per render scope** | Always contiguous, always matches what the reader can actually count on the page. |

The scope is the **document** on `/regulations/{slug}` (the counter is threaded
across sections in reading order) and the **chunk** in مراجع, which is the same
thing when only one chunk is shown. A repeated `image_ref` re-uses the number it
got the first time — **26 basenames appear more than once in a single chunk**,
genuinely the same figure cited twice, and it must not become «الصورة 3» and
«الصورة 7».

The caption is `الصورة {N}: {title}` — **Latin digits** (the number is app
chrome, so `project_latin_numerals_policy` applies to it) and the **title
verbatim** (corpus text, the carve-out).

**D9 — `title` is the caption; `description` is the `alt`; `transcribed_text` is
NOT printed to the reader in v1.** This is the one place the guides precedent is
deliberately *not* followed to the letter, and `GuideBody.tsx` already made and
documented the same call: `description` is 98–2,008 chars of machine-facing
Arabic analysis, and printed under every figure it reads as a wall of generated
prose restating what the picture shows. What `chunk_images` has that
`service_guide_images` did not is **`title`** — a 4–77 char label (mean 31),
which is exactly what a caption is. So the reader gets the caption, the screen
reader and the crawler get the description, and the model gets everything (§4).
A «نص الصورة» disclosure carrying `transcribed_text` is named in §8, not built —
it is a gate-exposure question and it deserves its own decision.

**D10 — The gate charges a figure for what it puts in front of a reader, and
every removed span is charged whether or not it renders.** The load-bearing
decision, same shape as tables' D8 but arrived at differently; see §3.1. In one
line: `charge = max(len(span), len(caption) + len(transcribed_text))` when the
figure renders, and `len(span)` when it does not — so the budget arithmetic can
never come out looser than the string cut it replaces.

**D11 — A figure that does not fit is SKIPPED; the walk CONTINUES; and the first
skip closes the figure channel for the rest of the section.** Unlike a table, a
figure has no prose it replaced to degrade into — the space it occupied was 41
characters of dead markup — so "degrade the figure, not the section" means
degrade it to *nothing* and let the prose keep filling the budget. A section can
therefore never blank on account of a figure, which is the failure the tables
plan spent a revision fixing. The monotone rule (first skip stops all later
figures in that section) is what keeps «الصورة 1، 2، 3» honest: figures fill
until the budget runs out and then stop, rather than appearing around the holes.

**D12 — The agent body is built on the LIVE path, but the read is gated on a
column we already have.** Tables are reveal-only (D10 there) because
`table_html` is 29 MB corpus-wide and the aggregator does not need it. Images
are the opposite: the aggregator is the consumer that is *currently broken*. The
cost is bounded by `chunks_v2.has_images`, a boolean already on the chunk row —
**1.6% of reg citations land on a figure-bearing chunk**, so on 98.4% of turns
the extra query is not issued at all.

**D13 — The agent body is CAPPED per chunk, and the cap is not optional.**
Measured over all 1,598 figure-bearing chunks: substituting
`title + description + transcribed_text` adds a **mean of 2,114 chars to a chunk
whose mean `content` is 3,076** — a 69% inflation — and on **452 chunks (28%)
the figure text is longer than the statute text**. Worst single chunk:
**+28,318 chars**. A per-image trim (description 400, transcription 1,200) saves
only 10%; a **per-chunk ceiling of 4,000 chars** does the work, touching 177
chunks (11%) and bringing the mean add to 1,584. Past the ceiling the remaining
figures collapse to their captions and a «(+N صورة أخرى)» line, so the model is
told what it is not being shown rather than silently missing it.

**D14 — The projected token is `IMG_{N}`, minted by the server, never derived
from `image_ref`.** `image_ref` is `{reg_ref}_img_{n}` and **four regulations
carry Arabic in their ref** (`17645_reg_الانظمة_002_chunk_001`), so a token built
from it cannot use the `[A-Za-z0-9_]` anchor that makes the `TBL_` regex safe.
`IMG_{N}` is ASCII by construction, is unique within the payload the server just
built, and *is* the caption number, so the token and the label cannot disagree.
Verified against the whole corpus: **0 chunks contain a whole-line `IMG_\d+`**
(8 contain one inline, which the line anchor never matches).

**D15 — No migration, no new table, no corpus write, no new route, no CSP
change.** `img-src 'self' https://*.supabase.co https://img.youtube.com data:`
already covers the bucket.

---

## §2 The shared renderer — `shared/library/chunk_images.py`

New module beside `chunk_tables.py`, in the layer both `backend/` and `agents/`
import from. **One** implementation; the library, مراجع and the aggregator all
call it, so the three surfaces cannot drift.

```python
IMAGE_SPAN  = re.compile(r"!\[[^\]]*\]\(images/([^)]+)\)")
IMAGE_TOKEN = re.compile(r"^[ \t]*(IMG_\d+)[ \t]*$", re.M)
```

`IMAGE_SPAN` is the **corpus** contract (REFERENCE.md §3.1) and matches a span
anywhere on a line. `IMAGE_TOKEN` is **ours** — the whole-line stand-in the
server projects onto the wire (D14). They are different regexes for different
jobs and must not be merged.

### 2.1 Public surface

```python
@dataclass(frozen=True)
class ChunkImage:
    image_ref: str          # canonical id, and the React key
    source_basename: str    # the substitution key for a cited figure; "" for orphans
    title: str              # the caption
    description: str        # the alt text, and half the agent view
    transcribed_text: str   # the figure's own text — the other half
    url: str                # public Storage URL, built from storage_path (D7)
    width: int
    height: int
    origin: str             # "cited" | "orphan" — decides the render path
    n: int                  # meta->>'n', ORDERING ONLY for orphans. Never a label.

def images_by_chunk(rows, *, base_url) -> dict[str, list[ChunkImage]]:
    """Raw `chunk_images` rows -> {chunk_id: [ChunkImage]}. Drops uploaded_at IS NULL."""

def place_images(segments, images, *, start_index=1) -> tuple[list[Segment], int]:
    """Splice figures into `chunk_tables.split_body` output. Returns (segments, next_index)."""

def render_for_agent(content, images, *, max_chars=AGENT_FIGURE_BUDGET) -> str:
    """`content` with every span replaced by the figure's WORDS. The agent body."""

def image_weight(image: ChunkImage) -> int:
    """What one figure costs a free-char budget. See D10."""
```

### 2.2 `place_images` — the composition, and why it is a second pass

`split_body` already walks the display body line by line and turns whole-line
`TBL_…` tokens into table segments. Images are not lines — 47 of them live
inside a sentence — so they cannot join that walk without teaching it a second,
inline grammar. Instead this runs **after** it, over the text segments only:

1. For each `{"kind": "text"}` segment, `IMAGE_SPAN.sub` over its text, splitting
   it into text runs and `{"kind": "image", …}` segments in place.
2. Unresolved span ⇒ **removed, nothing emitted** (D3), and the whitespace it
   orphaned goes with it, exactly as `_walk` already does for a dropped `TBL_`.
3. Orphan figures (`origin='orphan'`), ordered by `n`, are appended after the
   last segment.
4. Each emitted figure takes the next number from the counter, unless its
   `image_ref` was already numbered in this scope, in which case it re-uses it.

The two populations are disjoint by corpus invariant, so step 1 can never
consume a figure step 3 will also append — §6 re-checks that against our DB
rather than trusting the other repo's checker.

**The tables/images composition is safe and was measured, not assumed.** Of the
799 cited rows on chunks that also carry `content_display`, **all 799 keep their
`source_basename` in `content_display`** — no image span was ever swallowed into
a `TBL_` token. 433 chunks carry both tables and figures.

### 2.3 `render_for_agent` — the half tables never needed

```
> 🖼 **الصورة 3: مخطط تدفق إجراءات الترخيص**
> {description}
> **نص الصورة:** {transcribed_text}
```

A labelled blockquote, whitespace-collapsed, exactly the shape
`agents/simple_search/unfold.render_service_guide` already uses for guide
screenshots (`> 🖼 **صورة من الدليل:** …`) — labelled so the model never mistakes
it for the statute's own prose, blockquoted so a multi-line description cannot
break the markdown around it. Three rules:

- **`transcribed_text` is the answer to "what does the diagram say".** It is
  where a spec table's numbers live. It is emitted only when
  `contains_text` — 4,156 of 5,347 rows — and never fabricated for the rest.
- **Nothing about the image itself travels.** No URL, no bucket path, no bytes.
  The model cannot open it, so shipping it is pure cost.
- **The per-chunk ceiling is enforced here** (D13), and when it bites the
  remaining figures degrade to their caption line plus a final
  «(+{k} صورة أخرى لم تُدرج)». The model is told, never silently shorted.

Orphan figures append at the end for the agent too, under one
«صور مرفقة بهذا المقطع:» line, because their position in the prose is
`predicted` and presenting a guessed position as a certain one is a claim we
cannot make to a model that will cite it.

---

## §3 Backend — the library

### 3.1 The gate

`truncate_segments_for_gate` already walks segments against a remaining budget
and already knows two kinds. It learns a third, and the rule is D10/D11:

```python
if seg["kind"] == "image":
    # (1) it fits: render it, charge it, carry on.
    if sep_cost + seg["weight"] <= remaining and figures_open:
        visible.append(seg); remaining -= sep_cost + seg["weight"]; continue
    # (2) it does not: charge only the markup it removed, show nothing,
    #     close the figure channel for this section, and KEEP WALKING.
    remaining -= min(remaining, seg["span_len"])
    figures_open = False
    is_truncated = True
    continue
```

**What a figure costs, and why it is not zero.** Today the gate charges
`len("![img-1.jpeg](images/page_005_img_001.jpeg)")` — mean **41** chars,
p50 44, max 68 — and renders a broken image. Tomorrow it renders a diagram that
may carry a full specification table in pixels. So:

```
weight   = max(len(span), len(caption) + len(transcribed_text or ""))
span_len = len(span)                     # charged even when nothing renders
```

- `len(caption)` is the only text that reaches the DOM, and it is small
  («الصورة 12: » + a 31-char mean title).
- `len(transcribed_text)` is the law the reader's *eye* gets: p50 31 (a photo
  with a word on it), p95 755, max 4,854 (a fines table photographed whole).
  Charging it is what stops an anonymous crawler collecting a نظام's entire spec
  schedule as JPEGs against a 600-char budget.
- `max(len(span), …)` and not `len(caption)+len(transcribed)` alone, so the
  charge is never *below* what the string cut charges today. Combined with
  charging `span_len` on a skipped or unresolved span, the invariant is exact by
  construction rather than by measurement: **every character `content` spent on
  a span is still spent, plus whatever the figure adds on top.**

`is_truncated` becomes true when a figure is withheld even if no prose was cut —
otherwise the page claims it showed everything while hiding a diagram.

### 3.2 Where the body is chosen

`_chunk_section_body` (`library_service.py:3127`) is the ONE seam — all four
chunk-shaped surfaces already funnel through it:

| site | what it feeds |
|---|---|
| `:3394` | `_article_sections` — the **chunk-fallback مادة** on the ARTICLE surface (§3.5) |
| `:3465` | `_appendix_sections` — the ملاحق stream, live since 2026-08-24, on BOTH surfaces |
| `:3752` | `visible_sections` on `/regulations/[slug]` (chunk-fallback doc) |
| `:7315` | `get_full_regulation` — the paid reveal of a chunk-surface نظام, untruncated |

Each grows one batched read per document, mirroring
`_chunk_tables_for_regulation` exactly: `_chunk_images_for_regulation(supabase,
regulation_id)` selecting
`chunk_id, image_ref, source_basename, title, description, transcribed_text,
contains_text, storage_path, mime_type, uploaded_at, meta` filtered on
`regulation_id`. **Paged the same way** — PostgREST clamps at 1,000 and the
heaviest نظام carries 414 figures, so one page covers today with room, and the
paging exists because a re-ingest past the clamp would not error, it would
silently resolve every span to nothing and D3 would turn each into a *deleted*
figure.

`_chunk_section_body` grows `start_index: int` and returns `next_index`
alongside its four keys, so the caller threads the document-wide counter (D8).
**Precondition:** sections must be built in reading order — they are today, and
§6 asserts it, because a counter threaded through an unordered loop numbers
figures in whatever order the rows arrived.

**Fail-soft, and the safe direction is the opposite of tables'.** When the
`chunk_tables_v2` read fails, `_chunk_display_body` falls back to `content` so
the flattened tables survive. There is no equivalent here: a failed
`chunk_images` read leaves the spans unresolved, and D3 removes them. That is
correct and is the **only** safe direction — a failed read must never leave
`![img-1.jpeg](images/…)` on the page, which is precisely today's bug. So an
image read failure degrades to *the prose without its figures*, which is
strictly better than what ships now. Named explicitly because it inverts the
neighbouring rule and someone will "fix" it to match.

### 3.3 The wire

`RegulationVisibleSection` (`frontend/lib/library/api.ts:323`) grows one optional
field beside `tables`, under the same additive-and-optional rule:

```ts
/**
 * Rendered figures for this section, keyed by the `IMG_{n}` token that stands
 * in for each one inside `text`. `n` is the document-wide render number and is
 * also what the caption prints — «الصورة {n}: {title}».
 *
 * Optional on the wire: a page baked before this shipped carries no `images`,
 * and its `text` carries the raw corpus markup instead — which the client now
 * strips, so an old bake degrades to prose-without-figures rather than to
 * today's printed filename.
 */
images?: Record<string, {
  image_ref: string; n: number; title: string; description: string;
  url: string; width: number; height: number;
}>;
```

`project_segments` grows the image arm and keeps its invariant: **every
`IMG_{n}` token in `text` has a key in `images`, and every key appears as a
token line.** Same test as tables (`test_the_token_set_and_the_table_map_agree`),
same reasoning — a token with no entry renders raw, an entry with no token is
dead weight on a 24h ISR payload.

⚠ **The ISR trap, restated because it bites harder here.** `text` and `images`
bake together. A payload whose `text` came from the new projector but whose
`images` went missing renders naked `IMG_3` lines; a payload baked *before* this
ships carries raw `![…](images/…)`. The client therefore drops **both** shapes
unconditionally (§5.2) rather than trusting the pair.

### 3.4 What v1 does NOT reach

The tables plan stopped at the article surface (its D9). This one crosses it —
see §3.5 for why that turned out to be cheap — so the boundary here is not
"article vs chunk" but **cited vs orphan**. It costs little either way: figures
cluster in `without_articles`, the لوائح فنية that are mostly diagrams, rather
than in article-shaped أنظمة:

| bucket | chunks | figures | v1 |
|---|---|---|---|
| `without_articles` body, chunk-surface regs | 459 | **1,562** | ✅ |
| `appendix`, chunk-surface regs | 71 | **308** | ✅ |
| `appendix`, article-surface regs | 21 | **54** | ✅ (rides `_appendix_sections`, already deployed) |
| `with_articles` body, chunk-**fallback** مادة | 2 | **2** | ✅ free — a non-extracted مادة renders its whole chunk through `_chunk_section_body` (`:3394`) |
| `with_articles` body, **extracted** `article_text` — **cited** | 8 | **11** | ✅ via **§3.5** — the span rides inside the slice |
| **v1 total** | | **1,937 — 98.0%** | |
| `with_articles` body, **extracted** `article_text` — **orphan** | 25 | 26 | ❌ — no markup to ride, and its position is predicted against the chunk, not the مادة. §3.5 D16, §8. |
| `with_articles` body chunks with NO `seo_articles` row | 7 | 13 | ❌ — those chunks are absent from the page entirely today. `regulation_article_coverage_fallback.md`'s problem. |

Separately, **52 `seo_articles` rows across 10 regulations carry 217 raw spans
in `article_text`** and print them as literal text today — but only **11 of
those spans, in 8 مواد across 7 published أنظمة**, are reachable by a
reader (`دليل المهن والمنشآت البيطرية` alone is 128 spans and has no slug). §3.5
resolves them.

### 3.5 The article surface — the span survives the slice, so it resolves

The row above was written as a hard bucket, and re-measuring it says otherwise.
`article_text` is cut out of `content` **and the span is cut out with it** —
that is precisely *why* 52 rows print filenames. So the substitution key is
already sitting in the article body, and `seo_articles.chunk_id` names the chunk
whose figures they are. `(chunk_id, source_basename)` is the same lookup §2
already performs; there is no "span matching inside the slice" to invent.

Measured against live `article_text`:

| | |
|---|---|
| spans inside `article_text` | 217 (52 rows · 10 regs) |
| resolve against their own chunk's `chunk_images` rows | **131** |
| chunk carries no rows at all ⇒ D3 deletes them | 74 |
| basename absent from a chunk that does have rows ⇒ D3 | 12 |
| **on published pages** | **11 spans · 8 مواد · 7 أنظمة — 10 of the 11 resolve** (as `art-N` sections; none of the 8 has a published مادة page — §7 step 5) |

**D16 — the extracted `article_text` body renders CITED figures, and only cited
ones.** Orphans (`origin='orphan'`) never reach the article surface. Their
position is `predicted` against the **chunk**, and a مادة is a fragment of that
chunk — appending a figure to a مادة because it was predicted somewhere in the
chunk that contains it is a placement claim the data does not support. The cost
is on the record and it is the larger half: **26 published orphan figures on 25
extracted-article chunks stay invisible**, exactly as they are today. §8 keeps
them.

**D17 — the counter's scope is the PAGE, not the document.** `/regulations/{slug}`
threads one document-wide number through its sections (D8); the مادة page numbers
its own figures from 1. The same figure is therefore «الصورة 7» on the document
and «الصورة 1» on its own page, and that is correct — D8's rule is *render
order within the render scope*, and a reader counting figures on a مادة page can
only count the ones on it.

**D18 — the extracted branch moves from `truncate_for_gate` to
`truncate_segments_for_gate`.** This is the one real code change and it is a
gate change, not a rendering one. Today `_article_sections`
(`library_service.py:3372-3375`) cuts a **plain string** and emits `tables={}`,
so a figure spliced into that body would ride the gate **free** — D10's whole
argument (a photographed penalty schedule against a 500-char budget) applies
here unchanged. The extracted branch therefore goes through `split_body` →
`place_images` → `truncate_segments_for_gate` like every other body, and emits
`tables={}` still (§3.4's table limit is untouched — this plan does not reach
tables inside `article_text`).

### 3.5.1 Four payloads carry an article body, and all four need the map

Miss one and the figure vanishes on exactly the surface the reader paid for:

| payload | builder | today | needs |
|---|---|---|---|
| doc-page مادة section | `_article_sections` (`:3319`) | plain-string cut, `tables={}` | `images` beside `tables`, D18's segment walk |
| مادة page | `get_regulation_article` (`:4529`) | selects `title, content` from the chunk; no map | one `chunk_images` read for that chunk, fired only when `has_images` |
| reveal — sections | `get_full_regulation` → `_article_sections` at `gate='open'` | rides whatever `_article_sections` emits | `FullSection.images` on the wire **and** `renderFull` passing it — see the ⚠ below |
| reveal — one مادة | `get_full_article` | returns a bare `text` string | `FullArticle` grows `images`, or the paying reader gets prose without the figure |

⚠ **The reveal repeats the `TBL_` trap verbatim.** `FullSection`
(`frontend/lib/library/full-content.ts:70`) declares `tables?` and nothing else,
and `renderFull` (`FullContentGate.tsx:433`) passes only `tables`. An
article-surface reveal builds its ملاحق through `_appendix_sections`, so its
`text` **will** carry `IMG_{n}` token lines the moment §3 ships — with no map,
§5.2's strip eats every one and the reader who just unlocked the document sees
*fewer* figures than the anonymous preview of the same page.
`test_the_public_page_and_the_reveal_agree_on_the_appendix`
(`test_library_gating.py:1738`) compares section **ids** only and stays green
through it. This is not article-surface-specific — it bites every reveal — and
it is the highest-severity item in §3.

### 3.5.2 What does NOT change, measured so nobody re-derives it nervously

- **The price.** `unlock_cost` (`:1334`) is structural — `article_no` count plus
  annex chunks, never characters — so figures cannot reprice a reveal.
- **No partial span can survive a cut.** `truncate_for_gate` (`:586`) cuts at
  the last whitespace at or before the budget, and **0 of 6,016 live spans
  contain whitespace**, so the gated preview of an un-migrated article body can
  never emit a fragment like `![img-1.jpeg](images/page_00`. §5.2's strip alone
  is therefore sufficient on any path this section has not reached yet.
- **Fail-soft inverts the same way as §3.2.** A failed `chunk_images` read
  leaves the spans unresolved and D3 removes them — prose without its figures,
  never the filename.
- **The counter's precondition extends.** `_article_sections` becomes a
  `start_index` carrier: the extracted branch consumes numbers now, the fallback
  branch already will (`:3394`), and both must hand `next_index` on to
  `_appendix_sections` or an article-surface نظام numbers its annex figures over
  the top of its مواد.

---

## §4 Agents — the aggregator, then مراجع

### 4.1 The live path (D12)

`ura/enrich._fetch_chunks` grows `has_images` in `_CHUNK_COLUMNS` — one boolean,
already indexed on the row. A new `_fetch_chunk_images(supabase, chunk_ids)`
fires **only when at least one fetched chunk has it set**, batched at `_ID_BATCH`
(150) like every other fetch in that module, paged, and fail-soft to `{}`.

`RegURAResult` grows one field, and unlike the tables pair it **is** projected:

```python
#: `content` with every image span replaced by the figure's WORDS (D1/D13).
#: Empty when the chunk has nothing to fix — 96.7% of the corpus.
chunk_agent_content: str = ""
```

⚠ **The condition is "has something to fix", NOT "has a figure".** 656 chunks
carry markup with **no row behind it** — 298 of those spans on published pages —
so `has_images` is false there and nothing is resolved. Gating the substitution
on a resolved figure leaves the aggregator reading `page_005_img_001.jpeg` on
exactly those chunks, which is §0's bug rather than a smaller version of it. Fill
it whenever the content carries a span **or** a figure resolved; `render_for_agent`
deletes an unresolved span (D3) and returns `content` byte-identical when there
is neither, so the 96.7% case still pays nothing and no extra query is issued —
the span is in a string already in hand.

```python
def for_aggregator(self, n=0) -> AggregatorItem:
    return AggregatorItem(
        ...,
        chunk_content=self.chunk_agent_content or self.chunk_content,
        ...
    )
```

**The prompt-surface question, answered honestly.** The tables plan kept
`chunk_display` out of `for_aggregator()` partly to keep the prompt block
byte-identical for the cache prefix. This change *does* alter `chunk_content`'s
value — but only for the 3.3% of chunks that carry a figure, and only in the
per-item region of the prompt, which varies by query anyway. The **structure**
of `AggregatorItem` is unchanged, so the cached prefix (system prompt +
scaffolding) is untouched. Same reasoning `reg_status` shipped under: the
prompt-surface cost is real, small, and buys a model that stops reading
filenames.

⚠ **`chunk_content` keeps the prose.** `chunk_agent_content` rides beside it,
never over it, so مراجع's «نسخ المحتوى», the forensic dumps and any consumer
that ignores the new field all keep exactly today's string.

### 4.2 مراجع

`_enrich_regulations(..., with_tables=True)` already exists for the reveal.
Images ride the **live** fetch (D12), so `_build_reg_view` has them on both
paths and `ChunkSourceView.display_segments` gains image segments with no new
flag. The `source_type === "chunk"` arm of `ReferencePanel` (:1234) already maps
over `display_segments` after the tables work — it gains one
`case "image"` returning `<ChunkFigure>`.

`extractSourceContent` is **not** touched: «نسخ المحتوى» keeps pasting
`view.content`, the prose. A user pasting a source into a memo must not get a
CDN URL. (Tables' D11, unchanged.)

⚠ **Persisted artifacts.** 56 already-written citations point at chunks with raw
markup. They rebuild from the DB on the click (`references_service._build_reg_shells`),
so they repair themselves; nothing is backfilled.

---

## §5 Frontend

### 5.1 One figure component

`frontend/components/library/blocks/ChunkFigure.tsx` — server component, used by
both the library and the مراجع popup, and modelled directly on `GuideBody.tsx`'s
`<figure>`, which already solved this on a wing carrying 3,180 screenshots.

```tsx
<figure className="my-5" dir="rtl">
  {/* eslint-disable-next-line @next/next/no-img-element */}
  <img src={url} width={width} height={height} alt={description}
       loading={eager ? "eager" : "lazy"} decoding="async"
       className="mx-auto h-auto max-w-full rounded-lg border border-border bg-muted/30" />
  <figcaption className="mt-2 text-sm text-text-secondary">
    الصورة {n}: {title}
  </figcaption>
</figure>
```

Five things it has to get right, four of them already paid for by the guides:

- **`width`/`height` are required, not decorative.** They reserve the box before
  the bytes land. One chunk carries **31** figures and the widest is 12,250px;
  without intrinsic dimensions the section reflows once per image.
- **A plain `<img>`, not `next/image`.** The bucket is public, the payload
  already carries dimensions, and the optimizer would add a remote pattern, a
  transform per image and a bill against ISR-cached anonymous pages —
  `GuideBody` made this call for 3,180 screenshots and it holds for 5,347.
- **`alt` is the `description`**, never the filename. It is what a screen reader
  and a crawler get, and this wing is published for SEO.
- **A `<figcaption>`, unlike `GuideBody`** — and the difference is `title`.
  `GuideBody` has no caption because `service_guide_images` has no short label
  and printing the 400-char description under every screenshot read as
  generated noise. `chunk_images.title` is 4–77 chars. That is a caption.
- **Digits inside `title` are corpus text.** Only the `{n}` is ours and Latin.

The 4.67 MB worst-case object is real: everything below the fold is `lazy`, and
only the first resolved figure of the first section is `eager`. §8 names an
upstream re-encode.

### 5.2 The block seam, and the unconditional strip

`toLegalBlocks` gains an `images?: LegalImageMap` parameter and a
`{ type: "image"; ref: string; … }` block, exactly as it gained `table`.

⚠ **Two lines must be matched UNCONDITIONALLY, not gated on the map** — the same
trap that shipped `TBL_` ids to readers when `FullContentGate` rendered without
passing the table map:

1. a whole-line `IMG_\d+` token, and
2. **a raw `![…](images/…)` span, anywhere on a line.**

Rule 2 is what makes every stale ISR bake, every un-migrated caller and every
`article_text` slice stop printing filenames the moment the frontend ships —
before the backend does anything. It is why §7 ships the frontend **first**, and
it is the single highest-value line in this plan.

`LegalBlocks` gains `case "image"` with an `mt-5` gap. `ArticleBody` passes
`images` through when `plain` is set; its five other consumers (circulars,
forms, judgments, guides, `GateBanner`) pass nothing and are unaffected.

---

## §6 Tests

`shared/library/tests/test_chunk_images.py`:

1. `test_a_chunk_with_no_images_is_untouched` — no rows ⇒ segments identical to
   `split_body`'s output, `render_for_agent` returns `content` unchanged. The
   96.7% case.
2. `test_an_unresolved_span_emits_nothing` — a span with no row leaves no trace
   in text, segments, or agent body. Assert the literal `](images/` is absent.
   **This one fires on real data** — 298 published spans.
3. `test_an_inline_span_keeps_its_sentence` — a span mid-sentence is lifted out
   and the surrounding prose survives intact, in order. The 47-span case.
4. `test_uploaded_at_null_is_unresolved` — a row with no bytes behaves exactly
   like a row that does not exist.
5. `test_the_url_uses_storage_path` — a PNG row yields a `.png` URL; assert no
   code path appends `.jpeg` to `image_ref`.
6. `test_the_number_is_render_order` — a fixture whose `n` is 47 and whose
   `n_in_chunk` is 3 renders as «الصورة 1»; a repeated `image_ref` re-uses its
   number; the counter threads across two sections.
7. `test_orphans_append_in_n_order_after_the_last_line`.
8. `test_cited_and_orphan_never_double_show` — over a live sample: every cited
   row's `source_basename` appears in its chunk's `content`, every orphan's does
   not. Re-asserts the corpus invariant against **our** DB.
9. `test_the_agent_body_carries_the_transcription` — description and
   `transcribed_text` both present for a `contains_text` row; no URL, no bucket
   path, no `image_ref` in the output.
10. `test_the_agent_body_is_capped` — a 28k-char fixture stops at
    `AGENT_FIGURE_BUDGET` and ends with the «(+N صورة أخرى)» line.

`backend/tests/test_library_gating.py` (extend):

11. **`test_the_gate_never_serves_more_than_today`** — the headline assertion.
    For a fixture chunk at `free_chars=600`, the visible non-whitespace
    character count is ≤ `truncate_for_gate(content, …)`'s, and every span's
    length is still charged. Property-style over `free_chars` 0..len(body).
12. `test_no_raw_markup_survives_truncation` — `](images/` never appears in
    `visible_text` at any `free_chars`. The mid-span-cut analogue of tables'
    191-chunk bug.
13. `test_a_withheld_figure_marks_truncated` — a figure skipped for budget sets
    `is_truncated` even when no prose was cut.
14. `test_the_first_skip_closes_the_channel` — figures 1,2 render, 3 does not
    fit, 4 (which would fit) is skipped too; numbering stays 1,2.
15. `test_an_images_read_failure_degrades_to_prose` — `fail_images` ⇒ section
    renders prose with every span removed, never raw markup. §3.2's inversion.
16. `test_sections_are_built_in_reading_order` — the counter's precondition.

`backend/tests/test_reference_source.py` (extend):

17. `test_the_aggregator_reads_words_not_filenames` — `for_aggregator()` for a
    figure-bearing chunk contains the description and no `](images/`.
18. `test_the_images_read_is_skipped_when_no_chunk_has_images` — D12's cost
    bound: no `chunk_images` query is issued (98.4% of turns).
19. `test_chunk_content_still_holds_the_prose` — the copy string is unchanged.

Frontend (`frontend/lib/library/__tests__/legal-text.test.ts`):

20. `test_a_raw_image_span_is_stripped_without_a_map` — §5.2 rule 2, the stale-ISR
    guard.

§3.5, the article surface (`backend/tests/test_library_gating.py`):

21. `test_an_extracted_article_resolves_its_own_span` — a مادة whose
    `article_text` carries `![…](images/x.jpeg)` renders the figure, keyed by
    the row's `chunk_id`. The 11-published-span case.
22. `test_an_article_never_shows_an_orphan` — a chunk with orphan rows renders
    none of them on the مادة surface, on either the doc page or the مادة page.
    D16.
23. `test_the_article_gate_charges_the_figure` — D18: the extracted body walks
    `truncate_segments_for_gate`, and at `free_chars=500` the figure is charged
    D10's weight rather than riding free. Fails today by construction — the
    branch cuts a plain string.
24. **`test_the_reveal_shows_every_figure_the_preview_showed`** — the ⚠ in
    §3.5.1, and the one this plan is most likely to ship broken. Assert over
    `IMG_` tokens and map keys, not section ids;
    `test_the_public_page_and_the_reveal_agree_on_the_appendix` compares ids and
    passes through the bug.
25. `test_the_article_counter_is_page_scoped` — D17: the same figure is
    «الصورة 7» in the document and «الصورة 1» on its own مادة page, and the
    document's ملاحق keep counting from where the مواد stopped.

Existing tests that must stay green untouched:
`test_chunk_stream_order_puts_body_before_appendix`,
`test_the_token_set_and_the_table_map_agree`,
`test_html_comments_never_reach_the_reader`,
`test_the_public_page_and_the_reveal_agree`,
`test_for_aggregator_is_byte_identical` — ⚠ **this one changes**, deliberately
and only for figure-bearing chunks (D12/§4.1). Update it to assert
byte-identity for a chunk with no figures and the substituted form for one with.

---

## §7 Rollout

Order is **frontend → shared → backend → agents → purge**, and unlike the tables
plan the frontend goes first *because it fixes something on its own*.

1. **Frontend.** `ChunkFigure` + the two unconditional strips (§5.2). Ships with
   no backend change and immediately stops 168 published أنظمة and 52
   `seo_articles` rows printing filenames. A client reading an `images` key that
   no payload carries yet is a no-op.
2. **Shared module + tests.** No consumer yet; merges harmlessly.
3. **Backend deploy** — `_chunk_images_for_regulation`, the gate arm, the
   counter, the wire field. **And §3.5 in the same deploy**, not after it: the
   extracted-article segment walk (D18), the مادة page's own read, and — the
   sharp one — `FullSection.images` / `FullArticle.images` on the wire *plus*
   `renderFull` passing them. Shipping §3 without §3.5.1 puts `IMG_{n}` tokens
   into a reveal that has no map, and the reader who paid sees fewer figures
   than the anonymous preview.
4. **Agents deploy** — `has_images` in the select list, the conditional fetch,
   `chunk_agent_content`, the `for_aggregator` swap.
5. **Purge.** ISR pages call the backend at bake time, so nothing changes on the
   live site until a page re-bakes. Purge the **175** affected regulation pages
   through `POST /api/revalidate` (`x-revalidate-secret`, body
   `{"path": "/regulations/<slug>"}`). **The مادة pages need nothing today** —
   measured on the revision date: all 52 span-carrying مواد have a
   `seo_articles.slug`, but **0 have the `seo_item_meta` article sidecar** that
   makes a مادة page published, so the articles sitemap is empty and those 11
   spans reach readers only as `art-N` sections on the document page. The day an
   operator publishes مواد, `/regulations/<slug>/<article_slug>` becomes its own
   ISR route and purging the document will NOT purge it. The slugs are Arabic —
   **percent-encode
   them**, or the route returns a cheerful `{"revalidated": true}` for a path
   that was never purged. Mandatory, not optional: a Docker-cached bake serves
   the old payload for a full day.
6. **Eyeball, in this order:** a `without_articles` لائحة فنية (the 1,562-figure
   bulk — diagrams and spec plates are the whole document); then the 31-figure
   orphan chunk, to confirm the block collapses instead of dumping 31 images
   into one section; then a wide figure on a phone; then a gated نظام as an
   anonymous user, checking that a withheld figure shows the gate banner and not
   a gap. Then **§3.5's surface**: `لائحة-الإنذار-في-حالات-الطوارئ` or
   `اشتراطات-مواقف-السيارات-المدفوعة` (3 spans each) — read the مادة on the
   document page, then on its own page, then **unlock it** and confirm the
   revealed body still carries the figure. The reveal is where this ships broken.
7. **Then one real agent turn** on a figure-bearing chunk, read in Logfire, to
   confirm the aggregator sees Arabic sentences where it used to see
   `page_005_img_001.jpeg`.

---

## §8 Out of scope — named so nobody assumes they came along

- **The retrieval path (D2).** BM25, `search_topics`, embeddings and the
  reranker all keep reading `content` with its markup. Measured cost: markup is
  6.1% of `content` on average, >50% on 16 chunks, and 23 chunks have <200 chars
  of prose without it. Repairing it means rewriting `content`, which recomputes
  `word_count` and BM25 and therefore belongs upstream, in `agentic_for_ministry`.
- ~~**The article surface**~~ — **moved IN, as §3.5.** The premise was wrong: the
  span survives the slice, so `(chunk_id, source_basename)` resolves it with the
  machinery §2 already has. What stays out is the **orphan** half — **26
  published figures on 25 extracted-article chunks** — which carries no markup to
  resolve and no position anyone can defend at مادة granularity (§3.5 D16). It
  needs a placement rule, not a matcher: append to the owning chunk's last مادة
  section, or hand it to the ملاحق. That is a decision, so it is its own plan.
- **Tables inside `article_text`** (507 of them) are still out — §3.5 changes the
  extracted branch's gate walk but resolves no `TBL_` token. The tables plan's §8
  item stands on its own now.
- **The 13 figures in body chunks with no `seo_articles` row.** Those chunks do
  not render at all today. `regulation_article_coverage_fallback.md`.
- **A «نص الصورة» disclosure** carrying `transcribed_text` for the reader (D9).
  It would help a reader on a slow connection and it would hand a crawler 4,854
  characters of spec table as selectable text. That is a gate decision, not a
  rendering one.
- **`meta->>'topics'`** — 1–5 Arabic search phrases on 5,218 rows, stored ready
  and **not embedded**. Reaching figures as first-class search results needs an
  `image` source type in the retrieval RPC, which does not exist. REFERENCE.md §8.
- **`chunk_images.fts`** — the Arabic FTS index over title + description +
  transcription is live and unused by us. It is the cheapest possible path to
  «أرني مخطط إجراءات الترخيص» and it is a separate feature.
- **The 9,310 decorative figures, 42 front-matter figures and 122 unattachable
  ones.** Never ingested, by design. The 298 published spans that point at them
  are removed by D3, not restored.
- **Re-encoding the heavy objects.** 339.7 MB total, one object at 4.67 MB and a
  widest edge of 12,250px. Lazy loading covers the page; a WebP/max-dimension
  pass belongs to the ingestion repo.
- **Anonymous enumeration of the bucket.** `regulation-images` is **public** and
  `storage_path` is `{regulation_ref}/{reg_ref}_img_{n}.{ext}` — guessable given
  a `regulation_ref`. So a gated نظام's figures are reachable by URL even when
  the page withholds them. This is the same class as
  `project_anon_postgrest_corpus_exposure` and it is **not fixed here**; it is
  named with a concrete probe: check whether anon holds `SELECT` on
  `storage.objects` for that bucket (can a stranger *list*, or only *fetch a
  known path*?), then decide between signed URLs on gated documents and
  accepting it as the guides wing already does.
- **Blog snapshots / `PublicAnswerView`.** They render persisted source views and
  inherit whatever §4 produces.
- **`ask_service._ground_regulation`.** An agent surface — it should get
  `chunk_agent_content` too, and it is one line, but it is a different consumer
  with its own tests. Named, not bundled.

---

## §9 Measurements — 2026-08-29, prod

### 9.1 Corpus

| | |
|---|---|
| `chunk_images` rows | 5,347 |
| `uploaded_at IS NULL` | **0** |
| cited / orphan | 3,677 / 1,670 |
| chunks with a row (`has_images`) | 1,598 / 48,429 |
| regulations | 418 / 3,951 |
| `image_ref` / `storage_path` distinct | 5,347 / 5,347 |
| jpeg / png | 4,772 / **575** |

### 9.2 The markup, in our DB

| | |
|---|---|
| chunks whose `content` carries a span | **1,839** |
| total spans | **5,799** |
| chunks with a span **and** a row | 1,183 |
| **chunks with a span and NO row at all** | **656** |
| cited rows whose basename is present in live `content` | **3,677 / 3,677** |
| cited spans that are whole-line / **inline** | 3,630 / **47** |
| span length mean / p50 / min / max | 41 / 44 / 21 / 68 |
| markup share of `content`, mean | **6.1%** |
| chunks where markup is >50% of `content` | 16 |

### 9.3 Composition with tables

| | |
|---|---|
| chunks with both figures and tables | 433 |
| cited rows on chunks carrying `content_display` | 799 |
| of those, basename **still present** in `content_display` | **799 (100%)** |
| basenames lost into a `TBL_` token | **0** |

### 9.4 Per-chunk shape

| | |
|---|---|
| chunks with cited / orphan / both | 1,182 / 484 / 68 |
| cited per chunk p90 / max | 8 / **39** |
| orphan per chunk p50 / p90 / **max** | 2 / 8 / **31** |
| `n_in_chunk` contiguous 1..k | **1,443 / 1,598** |
| `meta->>'n'` contiguous per regulation | **298 / 418** — worst gap **383** |

### 9.5 Text volumes

| | |
|---|---|
| `title` min / mean / max | 4 / 31 / 77 |
| `description` min / p50 / p95 / max | 98 / 394 / 657 / 2,008 |
| `transcribed_text` p50 / p95 / **max** | 31 / 755 / **4,854** |
| `contains_text` | 4,156 / 5,347 |
| by type (n · mean transcription): photo 1,785·15 · diagram 1,781·103 · specification 531·99 · **table 392·673** · flowchart 309·484 · form 95·643 · screenshot 82·527 · chart 75·127 · map 71·159 · icon 64·6 · org chart 36·405 · logo 7·79 · other 119·95 | |

### 9.6 The agent-body cost (D13)

| | |
|---|---|
| mean chars added per chunk | **2,114** (mean `content` 3,076 — **+69%**) |
| p50 / p95 / **max** added | 1,312 / 6,393 / **28,318** |
| chunks where figure text > statute text | **452 (28%)** |
| corpus total, uncapped | 3.38 M chars |
| per-image trims alone (400/1,200) | 3.04 M — only −10% |
| **+ per-chunk 4,000 ceiling** | mean **1,584**, 177 chunks (11%) trimmed |

### 9.7 Published reach

| | |
|---|---|
| published أنظمة | 1,689 |
| carrying ≥1 figure | **175 (10.4%)** · **1,976 figures** |
| **printing raw markup today** | **168 أنظمة · 1,956 spans** |
| spans on published pages with **no row ever** | **298** (154 chunks) |
| v1 reach | **1,937 / 1,976 = 98.0%** (§3.4, including §3.5's 11) |
| `seo_articles` rows carrying raw spans | 52 rows · 10 regs · **217 spans** — of which **published: 11 spans · 8 مادة pages · 7 أنظمة**, 10 resolvable (§3.5) |
| published figures left unreached | **39** — 26 orphans on extracted مواد + 13 on chunks with no `seo_articles` row |

### 9.8 مراجع

| | |
|---|---|
| regulation citations written to date | 3,800 (1,919 distinct chunks) |
| citations on a figure-bearing chunk | **61 (1.6%)**, 40 distinct chunks |
| citations on a chunk with raw markup | **56** |
| citations on a table-bearing chunk (for scale) | 291 |

### 9.9 Safety

| | |
|---|---|
| chunks containing a whole-line `IMG_\d+` | **0** (8 inline, never matched) |
| bucket `regulation-images` public | **true** |
| CSP `img-src` already allows `https://*.supabase.co` | yes — `next.config.mjs:53` |

---

## §10 Reproducing these numbers

```sql
-- 9.2 the markup, and the 656 chunks nothing will ever resolve
with spans as (
  select c.id,
         (select count(*) from regexp_matches(c.content,'!\[[^\]]*\]\(images/([^)]+)\)','g')) n
  from public.chunks_v2 c where c.content ~ '!\[[^\]]*\]\(images/')
select count(*) chunks, sum(n) spans,
       count(*) filter (where id in (select chunk_id from public.chunk_images)) with_rows,
       count(*) filter (where id not in (select chunk_id from public.chunk_images)) no_rows
from spans;

-- 9.3 no image span was swallowed into a TBL_ token
select count(*) rows_,
       count(*) filter (where position(ci.source_basename in c.content_display) = 0) lost
from public.chunk_images ci join public.chunks_v2 c on c.id = ci.chunk_id
where ci.meta->>'origin'='cited' and c.content_display is not null;

-- 9.4 why «الصورة {N}» cannot be meta->>'n'
with per as (
  select regulation_ref, count(*) k, max((meta->>'n')::int) mx
  from public.chunk_images group by 1)
select count(*) regs, count(*) filter (where mx > k) with_gaps, max(mx-k) worst_gap from per;

-- 9.6 the agent-body inflation
with per as (
  select ci.chunk_id,
         sum(length(ci.title)+length(ci.description)
             +length(coalesce(ci.transcribed_text,''))+30) add_
  from public.chunk_images ci group by 1)
select avg(add_)::int mean_add, max(add_) max_add,
       count(*) filter (where add_ > length(c.content)) exceeds_content
from per join public.chunks_v2 c on c.id = per.chunk_id;
```

§9.7's reach table joins `seo_item_meta` (`content_type='regulation' AND slug IS
NOT NULL`) to `chunks_v2` on `regulation_id`, splitting on `corpus` and on
whether the regulation has any `seo_articles` row; §9.8 uses
`workspace_item_references` with `domain='regulations'` and
`ref_id ~ '^reg:[0-9a-f-]{36}$'` — note the ref is a **chunk uuid**, not a
`chunk_ref`, and joining on the wrong one silently returns zero.
