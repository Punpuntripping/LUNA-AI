# Plan — إصلاحات نافذة المراجع (references window) + محرر العنصر

**Goal:** Five defects the user found in the المراجع panel and the workspace-item editor. Four are user-visible in chat; one is a corpus leak that also reaches the LLM prompt. Nothing here touches the PDPL noindex gate on `/judgments` — that stays shut.

Five items, three of them independent, two that MUST ship together.

| # | Item | Layers | Ships with |
|---|---|---|---|
| 1 | «فتح الحكم / النظام / التعميم في ريحان» on the reference **card**, not only in the reveal dialog | backend + frontend | **#2** |
| 2 | A judgment reference is labelled «قضية» instead of what the ruling is about | backend only | **#1** |
| 3 | `## classification_error` / `JSONDecodeError` text leaking out of `cases.summary` | agents (one regex) | alone |
| 4 | «من WI-9» is dead text — must open WI-9 at its own مرجع | frontend only | alone |
| 5 | Dead space after «تحرير», and the المراجع panel disappears in edit mode | frontend only | alone |

## Decisions (locked with user)

- **The label is the SUBJECT ALONE.** `judgment_subject()`, NOT `judgment_display_title()` — no « — العمالية 1445هـ» tail on the card. This is also the judgment page's own H1 (`library_service.py:4790`), so the card and the page its button opens say the identical sentence.
- **Item 4 of the original list folded into item 1.** "Link judgments to SEO" meant *link chat references to the judgment pages*, which needs no gate — `noindex` only tells Googlebot to stay away, it never hid the page from signed-in users. **Flipping the PDPL gate is NOT in this plan.**
- **No DB write for `classification_error`.** `cases` is pipeline-owned; a re-ingest would restore the block. Fixed at render, exactly like the resolver appendix before it.
- **Rank + ISR purge for the 10,000 published judgments is OUT OF SCOPE** — tracked separately (see "Adjacent, not in this plan").

## Live corpus numbers (verified 2026-08-10, prod)

| fact | value |
|---|---|
| `cases` rows | 30,531 |
| `## classification_error` in `cases.summary` | **252** (194 + 15 `ConnectError: getaddrinfo failed`, 43 `JSONDecodeError`) |
| …in `short_summary` / `content` / `facts` / `ruling` | **0** — the leak is `summary`-only |
| Latin-headed sections in all 30,531 summaries | **`classification_error` and nothing else** — the fix is closed, not open-ended |
| `short_summary` present | 29,567 |
| `short_summary` empty but `summary` present | +946 → **30,513 / 30,531 = 99.94%** get a real subject |
| no usable text at all (falls back to «حكم {court}») | 18 |
| `seo_item_meta` slugs: judgment / regulation / circular | **10,000 / 3,446 / 1,843** |
| `seo_item_meta.rank` on judgments | **0 of 10,000** ← the ramp ran, the rank step did not |

---

# Item 1 — «فتح … في ريحان» on the reference card

## Why it's missing

The button exists, but only inside the reveal dialog (`ReferencePanel.tsx:664-677`) — i.e. after the reader spends an unlock. The card offers only «عرض المصدر» + «فتح المصدر الرسمي» (`ReferencePanel.tsx:409-433`).

The cause is plumbing, not design: `library_url` is computed **only** in the reveal endpoint (`workspace.py:451` → `library_items_service.public_page_url`). The list payload (`references_service.fetch_item_references_payload:141`) never resolves a slug, so the card has nothing to link to.

**This is navigation, not content — it must stay free and unmetered.** The library page enforces its own gate; a link to it is not a reveal.

## Backend

### `backend/app/services/library_items_service.py` — new batched resolver

```python
async def public_page_urls_for_reference_rows(
    supabase, rows: list[dict]
) -> dict[int, str]:   # {n: url}
```

Rows are `workspace_item_references` rows, which already carry `item_id` (source-row PK) and `ref_id`. That makes most of the work free:

| domain | how content_id is obtained | round-trips |
|---|---|---|
| `cases` | `item_id` **IS** `cases.id` (`persist_item_references` resolved it at write time; `_resolve_case:255` short-circuits on it) | 0 |
| `circulars` | `item_id` **IS** `circulars.id` | 0 |
| `regulations` | needs `chunks_v2.regulation_id` — **one batched select** `(id, regulation_id)`. `owns` is NOT needed: `_public_page_url:843-847` maps an `article` to its parent نظام anyway | 1 |
| `compliance` | **no wing** — `_URL_PREFIX` (`library_items_service.py:125-130`) has no `service` key. Always `None`, never a button | 0 |

Then at most three `ls._slug_map(supabase, ct, ids)` calls (`judgment`, `circular`, `regulation`).

**Total: ≤ 4 round-trips per panel load, independent of reference count.**

Fallbacks for rows with a NULL `item_id` (legacy / failed write-time resolution):
- `reg:` → the `ref_id` tail is the `chunks_v2` uuid
- `circular:` → the `ref_id` tail is the uuid
- `case:` → `case_ref` → `cases.id` via `references_service._fetch_case_ids`, the batch helper that already exists (`references_service.py:1033`)

Fail-soft throughout: any error yields **no URL for that reference**, never a 500 and never a guessed URL. A missing button is correct; a button into a 404 is not.

### `backend/app/services/references_service.py`

`fetch_item_references_payload` calls the resolver once after `_load_references` and stamps `entry["library_url"] = urls.get(ref.n)` (absent → `None`).

Do **not** touch `fetch_item_references` (the typed `list[Reference]` path) — `Reference` is the aggregator's model and belongs to the agents package. `library_url` is a presentation concern of the JSON payload only.

### `backend/app/models/responses.py`

Add `library_url: Optional[str] = None` to the references list response entry model if one is declared; otherwise the payload is already a free-form dict and needs nothing.

## Frontend

**`frontend/types/index.ts` — `Reference`** (after `has_source`, ~line 725):

```ts
/**
 * The cited item's page in OUR library («فتح الحكم في ريحان»). `null` when
 * the item has no published page — the card then renders the external link
 * alone, never a hub fallback. Absent on pre-existing blog snapshots.
 */
library_url?: string | null;
```

**`frontend/components/workspace/ReferencePanel.tsx` — `ReferenceCard` actions (`:405-438`)**

Add, between «عرض المصدر» and «فتح المصدر الرسمي»:

```tsx
{reference.library_url && (
  <Link
    href={reference.library_url}
    target="_blank"
    rel="noopener"
    className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "h-6 gap-1 px-2 text-[11px]")}
  >
    <BookOpen className="h-3 w-3" />
    فتح {referenceDefiniteType(reference)} في ريحان
  </Link>
)}
```

`referenceDefiniteType()` (`:1077`) already produces النظام / الحكم / التعميم / الخدمة with correct Arabic definite articles for all 21 corpus doc types. `BookOpen` is already imported. **New tab on purpose** — the panel can be open over a streaming chat, and navigating the tab away kills the stream (same reasoning as the dialog's link, `:663`).

The dialog's own «فتح … في ريحان» stays as-is. Both point at the same URL; the dialog's comes from the reveal response and remains authoritative post-reveal.

## Free side-effect worth knowing

`blog.py:449` snapshots `fetch_item_references_payload` into `blog_posts.references_json`. **New blog posts inherit the button with no extra work.** Posts published before this ships have no `library_url` key and degrade to no button — which is why the type is optional, not required.

---

# Item 2 — a judgment reference reads «قضية»

## Root cause

One line — `agents/deep_search_v4/aggregator/preprocessor.py:505`:

```python
title=view.case_number or view.judgment_number or "قضية",
```

A case number is not a title, and most rows do not have one.

## The rule that governs the fix

`shared/seo/judgment_naming.py` is the **single source of truth** for judgment naming, shared with `scripts/build_judgment_slugs.py`, which cut the permanent slugs from `judgment_subject()`. Its module header forbids forking it: a surface that derived a *different* subject would label a card one way and open a page whose H1 says another. **Import it. Never re-derive inline.**

Since item 1 puts a link to that exact page on that exact card, this stops being stylistic and becomes a correctness constraint.

## Changes

**`agents/deep_search_v4/ura/schema.py`**
- `CaseURAResult`: add `short_summary: str = ""`.
- `ReferenceView`: add `short_summary: str = ""`, `summary: str = ""`. `court`, `case_number`, `judgment_number` already exist (`:250-255`).
- `CaseURAResult.for_reference()` (`:489`): pass `short_summary=self.short_summary`, `summary=self.case_content`.

`case_content` **is** `cases.summary` (clipped to 6k by `case_search/unfold_ura.py`, already appendix-stripped). `judgment_subject` reads only the *first meaningful line*, so the clip is irrelevant.

**`agents/deep_search_v4/ura/enrich.py`**
- `_fetch_cases` (`:161-178`) — currently selects 4 columns. Add `short_summary, court` **always** (~200 chars, negligible), and `summary` **only when asked**:

```python
def _fetch_cases(supabase, case_refs, *, with_summary: bool = False) -> ...:
```

- `_enrich_cases(case_results, supabase, *, with_summary: bool = False)`:
  - always set `res.short_summary` and `res.court` (today `court` is filled by the adapter on the live path and left **empty** on the panel-rebuild path — that gap is why the «حكم {court}» fallback currently produces «حكم»)
  - when `with_summary` **and** `res.case_content` is empty, set `res.case_content = strip_pipeline_sections(summary)`

**Why the flag:** `_enrich_cases` is shared. On the **live search path** `case_content` already arrives from the adapter, so `summary` (~3 KB/row) would be pure waste on every turn. On the **panel-rebuild path** (`references_service._build_case_shells:474`) the shell has nothing but a `ref_id`, so it needs the column. `references_service` passes `with_summary=True`; the live path leaves it `False`.

**`agents/deep_search_v4/aggregator/preprocessor.py:501-513`**

```python
from shared.seo.judgment_naming import judgment_subject
...
title=judgment_subject({
    "short_summary": view.short_summary,
    "summary": view.summary,
    "court": view.court,
    "case_number": view.case_number,
    "judgment_number": view.judgment_number,
}) or "قضية",
```

`judgment_subject` never returns empty (it ends at «حكم قضائي»), so the `or "قضية"` is belt-and-braces only.

`regulation_title` stays `view.entity_name or view.court` — untouched.

## What the user sees

Before: «قضية» · After: «نزاع عمالي حول مطالبة موظفة سابقة بمكافأة أعمال التفتيش الميداني بقيمة 150,000 ريال»

The «قضية» type chip on the card is unchanged — that's the *kind* of source, and it still belongs there. 30,513 of 30,531 rows get a real subject; 18 fall back to «حكم {court}».

Improves for free: `AgentSearchViewer`'s `copyContent` (`:96`) builds its «المراجع» list from `referenceLabel()`, so copied output stops saying «1-قضية».

---

# Item 3 — `classification_error` in the source popup

## What it is

The judgment pipeline appended a trailing section to 252 `cases.summary` rows:

```
## منطوق الاستئناف
تأييد حكم المحكمة التجارية بالرياض …

## classification_error
ConnectError: [Errno 11001] getaddrinfo failed
```

Same class as the `## المراجع النظامية المحلولة` resolver appendix on 16,505 rows, already handled. Verified `summary`-only: **0** rows in `short_summary`, `content`, `facts`, `ruling` — so **public `/judgments` pages are unaffected** (their body is `cases.content`, their lead is `short_summary`, `library_service.py:3980-4022`).

Where it *does* land: the source-reveal popup, and — worse — the **aggregator synthesis payload**. The model has been reading `ConnectError: getaddrinfo failed` inside the evidence for those 252 rulings.

## Fix — `agents/deep_search_v4/shared/case_summary.py`

Generalise the existing stripper. Add a second heading pattern:

```python
_ERROR_HEADING_RE = re.compile(
    r"^[ \t]{0,3}#{1,6}[ \t]*classification_error[ \t]*:?[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)
```

Same drop semantics as the existing one: heading → next markdown heading of any level, or EOF. Same cheap substring guard before the regex.

**Rename** `strip_resolved_refs_section` → `strip_pipeline_sections`. A function that strips two unrelated pipeline artefacts must not be named after one of them — and the memory rule "any NEW consumer of `cases.summary` must run the strip" depends on the name reading as general. Four mechanical import updates:

- `agents/deep_search_v4/aggregator/preprocessor.py:30,270`
- `agents/deep_search_v4/case_search/unfold_ura.py:64,188`
- `agents/deep_search_v4/source_viewer.py:67,474`
- `agents/tool_repository/unfold_workspace_item.py:45,310`
- plus `agents/deep_search_v4/tests_ura/test_case_aggregator_payload.py`

**No new call sites are needed** — those four are already every surface that serves `cases.summary`.

Update the module docstring with the live 2026-08-10 numbers and the finding that `classification_error` is the **only** Latin-headed section across all 30,531 summaries, so a future reader knows the pattern list is closed rather than a guess.

**No DB write.** The table is pipeline-owned and a re-ingest restores the block.

---

# Item 4 — «من WI-9» opens WI-9

## Today

A plain `<span>` (`ReferencePanel.tsx:380-391`). It carries a `title` tooltip naming «المصدر: WI-9 (مرجع 3)» — so the panel already knows both the item AND the exact citation inside it, and does nothing with either.

Every piece needed already exists:
- `openWorkspaceItemAtReference(conversationId, itemId, n)` — `stores/chat-store.ts:624`
- `reference.source_n` — the citation number inside the source WI
- `useConversationWorkspace(conversationId)` — already cached; every item carries `wi_seq`

## The constraint that shapes the design

**`ReferencePanel` also renders anonymously on `/blog/{token}`** (`PublicAnswerView`), where there is no chat store, no conversation and no workspace. So the panel must stay store-agnostic: it takes a **callback**, and the badge is only interactive when a caller supplies one.

## Changes

**`ReferencePanel.tsx`**
- New optional prop:
  ```ts
  /** Supplied by in-chat hosts only. Absent on the public blog panel, where
   *  the badge stays plain text. */
  onOpenSourceWi?: (seq: number, sourceN: number | null) => void;
  ```
- Parse the alias with `/^WI-(\d+)$/` (the publisher writes exactly this form — `agents/writer/publisher.py:280`).
- Render a `<button>` instead of a `<span>` **only when** the callback exists AND the alias parses. Otherwise the current span, unchanged.
- Keep the existing tooltip text; add the underline/hover affordance so it reads as clickable.

**`NoteEditor.tsx`** (the only host that renders refs with `source_wi` — the writer-publisher's attribution exists on `agent_writing` items only):
- `const { data: ws } = useConversationWorkspace(item.conversation_id ?? undefined)`
- Build `wi_seq → item_id` once, memoized.
- Pass `onOpenSourceWi={(seq, n) => { const id = map.get(seq); if (!id) return; n == null ? openWorkspaceItem(cid, id) : openWorkspaceItemAtReference(cid, id, n); }}`

**Never a dead button:** when the target WI isn't in the map (deleted, other conversation, item not yet loaded), the badge falls back to the plain span. Resolve the map before deciding what to render.

Opening at `source_n` — not just at the item — is the whole point: it lands the reader on the exact citation the writer pulled, which `openWorkspaceItemAtReference` already knows how to flash/reveal.

Out of scope: the chat-bubble `WiBadge` (`MessageBubble.tsx:505,798`) deserves the same treatment, but it's a different surface with a different lookup. Separate change.

---

# Item 5 — dead space on «تحرير», and the vanishing المراجع

## Root cause (mechanism confirmed in the installed package)

`MarkdownDocEditor.tsx:207-217` puts the textarea inside `<ScrollArea className="flex-1">`. Radix's Viewport renders an inner wrapper:

```js
// node_modules/@radix-ui/react-scroll-area/dist/index.mjs:130
jsx("div", { style: { minWidth: "100%", display: "table" }, children })
```

That wrapper's height is `auto`, so `height: 100%` on the textarea resolves to `auto` (a percentage height against an auto-height containing block computes to auto). The textarea therefore falls to its `min-h-[400px]` — **a fixed 400 px box regardless of pane height or document length**.

Consequences, both visible in the user's screenshot:
1. In a pane taller than 400 px, everything below the textarea is dead space.
2. A long draft scrolls inside a nested 400 px window instead of the pane.

Nothing in `globals.css` patches that wrapper (checked) — this is live.

## Second defect, same component

`footerSlot` is passed **only** to `ArtifactPreview` (`:224`). Switching to «تحرير» therefore deletes the entire المراجع panel and the AI disclaimer from the view. Not reported by the user, found while reading — and it's the same three lines.

## Fix

Replace the edit branch with a single native scroll column holding **both** the textarea and the footer:

```tsx
{mode === "edit" ? (
  <div className="flex-1 min-h-0 overflow-y-auto">
    <textarea
      ref={bodyRef}
      value={content}
      onChange={(e) => setContent(e.target.value)}
      readOnly={readOnly}
      dir="rtl"
      rows={1}
      className="block w-full resize-none overflow-hidden border-0 bg-transparent p-4 text-sm leading-relaxed focus:outline-none read-only:cursor-default"
      placeholder={bodyPlaceholder}
    />
    {footerSlot}
  </div>
) : (
  <ArtifactPreview … footer={footerSlot} />
)}
```

with an auto-grow effect (~8 lines, no dependency):

```tsx
const bodyRef = useRef<HTMLTextAreaElement | null>(null);
useLayoutEffect(() => {
  const el = bodyRef.current;
  if (!el || mode !== "edit") return;
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight}px`;
}, [content, mode, docId]);
```

Why auto-grow rather than `h-full`: `h-full` alone fixes the dead space but keeps the textarea as its own scroller, which means the references below it could never be reached in edit mode. One column, one scrollbar, everything reachable.

- Delete `min-h-[400px]` — it is the bug, not a safeguard. The empty-document case is covered by the pane's own height plus `p-4`.
- `ScrollArea` import drops from this file if nothing else uses it.
- `useLayoutEffect`, not `useEffect` — measuring after paint flashes the collapsed height on open.

---

# Verification

**Automated**
- `cd frontend && npx tsc --noEmit` — the `Reference.library_url` addition and the new `ReferencePanel` prop are the only type-surface changes.
- `cd frontend && npm run lint`
- `pytest agents/deep_search_v4/tests_ura/test_case_aggregator_payload.py` — covers the stripper rename; add a case asserting `## classification_error` + its `ConnectError` body are dropped and that the preceding «منطوق الاستئناف» section survives.
- New backend test: `library_url` is `None` for a `compliance` reference and for an unpublished judgment; present for a judgment with a slug. Follow `backend/tests/test_reference_source.py:981-1042`, which already pins the dialog's equivalent.
- `.gitignore:19` ignores `backend/tests/*` with per-file `!` exceptions — **add an exception for any new test file** or CI runs green over a file it cannot see (this exact trap swallowed 8 broken tests during the court-sections build).

**Manual, in one conversation with a deep_search answer citing at least one ruling**
1. A judgment card shows a real subject line, not «قضية», and no « — المحكمة … 1445هـ» tail.
2. That card carries «فتح الحكم في ريحان»; it opens `/judgments/{slug}` in a new tab and the page **H1 is the same sentence as the card label**. ← the invariant item 2 exists to protect.
3. A regulation card shows «فتح النظام في ريحان» (or its real doc type); a compliance card shows **no** in-app button.
4. The panel's «فتح المصادر» balance chip does **not** move when the new button is clicked — it's navigation, not a reveal.
5. Open a ruling whose summary carried `classification_error` — the popup ends at «منطوق الاستئناف» with no English text. Cross-check the aggregator prompt in Logfire for the same turn.
6. On an `agent_writing` item, «من WI-9» is clickable and lands on WI-9 with مرجع N flashed.
7. Click «تحرير» on a long agent draft: no dead space, one scrollbar, and المراجع still visible below the text.

**No migration.** No schema change anywhere in this plan.

---

# Traps

- **Never fork `judgment_naming`.** The 10,000 published slugs were cut from `judgment_subject()`. A second derivation puts a card label and a page H1 out of sync and there is no way to fix it after the fact — URLs are permanent.
- **`_URL_PREFIX` has no `service`.** `/compliance` was retired; a government service must never render an in-app button. (Consistent with `746cbc5`: a service card is its name and its جهة, nothing else.)
- **`ReferencePanel` renders anonymously on `/blog/{token}`.** It cannot import the chat store or any authed hook. Everything new must arrive as a prop.
- **`library_url` must never be gated.** It is a link to a page that enforces its own access tier. Metering it would double-charge and break the D15.1 «name what you unlocked» line.
- **Blog snapshots are frozen.** `references_json` is written at publish time; posts published before this ships have no `library_url`. Optional field, absent → no button.
- **`_enrich_cases` is shared by the live search path.** Fetching `summary` unconditionally adds ~3 KB × refs to every turn for a value the live path already holds. The `with_summary` flag is the point, not decoration.
- **Radix `ScrollArea` cannot host a `h-full` child.** Its viewport wrapper is `display: table` with auto height. Any future "make it fill" inside a ScrollArea will fail the same way.
- **`.gitignore:19` ignores `backend/tests/*`.** Per-file `!` exceptions only.

---

# Adjacent, not in this plan

- **The judgment rank step never ran.** 10,000 judgments published with `rank` NULL on all of them; regulations show 1,188/1,188. Documented order is publish → rank → **manual** ISR purge (`build_judgment_slugs.py` does not call `/api/revalidate`). Two steps outstanding.
- **The PDPL noindex gate stays shut.** Flipping it is ~3 lines across `app/judgments/[slug]/page.tsx`, `app/judgments/page.tsx` + `page/[n]`, and `lib/seo/sitemap.ts` (the recipe is written in those files' comments) — gated on an anonymization pass over 10,000 bodies. Corpus scan: 609 bodies carry a 10-digit ID-shaped number, 4,018 mention هوية/السجل المدني.
- **The chat-bubble `WiBadge`** deserves item 4's treatment on its own surface.
