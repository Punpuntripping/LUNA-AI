# Marketing Agents — a Layer-3 category that runs after publication

Status: **PLANNED** (2026-09-01). Scope deliberately narrow: this document specifies the
**category**, the **two agents**, and the **API contract**. Card-selection logic, the SEO
rewrite's editorial bounds, and the X / LinkedIn workflows are **explicitly deferred** by the
operator — §6 names their seam without designing them.

Depends on: [[blog_subjects]] (the `public_blogs` versioned table, the editorial aggregator
prompt, the publishing path). This plan picks up at that plan's step 8.

---

## 0. What this is

A published blog is not the end of the pipeline. Two things happen to it afterwards:

1. It may need to **earn search traffic** — rewritten or nudged to match keywords it was not
   written against.
2. It needs **cards** — the social artefacts that carry it to X and LinkedIn.

Both are transformations over an already-published article, both need the article's full body
*and* its resolved references, and neither is a conversation. That is a coherent new category.

---

## 1. The category

**Layer 3 Task agents** in the project's own vocabulary (`.claude/plans/wave_9_agent_runs.md`
§ "Agent Hierarchy"): transformers that never talk to a user and never write to chat. They are
**not router-dispatched** — no user utterance ever reaches them. Marketing invokes them over the
service-authed `/internal/*` surface, exactly as it invokes blog generation.

They live in **`agents/marketing/`** (operator's call), siblings of `agents/artifact_editor/`
and `agents/memory/`.

### The capability they share

The operator's phrasing was "the same capabilities as the artifact analyzer — they can see the
full references and re-reference them." The pattern to copy is `artifact_editor`'s, documented
in its own module header:

> the runner fetches the artifact fresh, injects its FULL `content_md` into this agent's prompt
> **deterministically (unfold philosophy — no read tools)**, and the agent emits ONE batched
> call … The batch is all-or-nothing (one guarded write, `prev_content_md` snapshot), so the
> document can never land in a half-edited state.

Extended here to references: body **and** the fully-resolved `Reference[]` handed in up front.

⚠ **This is a sibling, not a reuse.** `artifact_editor` declares
`ALLOWED_KINDS = frozenset({"agent_writing", "note"})` and refuses `agent_search` outright,
because "reports are regenerable" — and every blog descends from exactly that kind. It also
writes to `workspace_items` via `edit_supabase_md`, not to `public_blogs`. Same philosophy,
different guard, different write target.

---

## 2. The two agents

### 2.1 `blog_seo_editor` — the SEO modification step (optional)

**Input** (all injected, no read tools):

| | |
|---|---|
| `title`, `content_md` | the current version, verbatim |
| `references_json` | the frozen array — numbers as published |
| `type` + `subjects` | so it knows the article's shelf |
| `seo` block | primary keyword, secondary keywords, target questions (§3) |

**Output**: a new version of the article. The runner writes it as `version_no + 1` in
`public_blogs`, flips the previous row's `is_current`, and stamps `revision_note`.

#### The closed citation set — the invariant that bounds this agent

`public_blogs` copies `references_json` **verbatim** into every version
([[blog_subjects]] D18). That makes the citation set **closed**, and the rewrite checkable:

```
∀ n cited in the new content_md  →  n exists in references_json
used_refs(new) ⊆ used_refs(old)          -- may drop, may never add
no [n] is renumbered
```

**Enforce these as post-conditions on the runner, not as instructions in the prompt.** A version
that violates any of them is rejected and the current version stands. This is what makes an
agent safe to point at a live, indexed page: it may rewrite *around* the evidence, never *beyond*
it. A model that wants to cite something new is a model hallucinating a source.

⚠ Dropping a citation is permitted but consequential: an `[n]` that disappears from the body
leaves an orphan in the rendered المراجع list. Either keep `used_refs` intact, or have the
publish path recompute the rendered list from the new body. Decide at build time; do not leave
it to chance.

#### Hard invariants beyond citations

- **`slug` never changes.** No redirect layer exists; a rename 404s. `title` may change.
- **The `##` heading structure is the TOC.** Restructuring headings changes every in-page anchor.
  Existing deep links break silently.
- ⚠ **CORRECTED 2026-09-02: there is nothing to purge — the wing is `force-dynamic`.** All three
  blog routes render on demand (inherited from the legacy `[token]` route, which needs per-render
  execution for its `view_count` bump), so a new version is live the instant it is written. If
  anyone later moves this wing to ISR for performance, **revalidate-on-publish becomes mandatory
  in the same change** — otherwise an SEO rewrite ships to a page nobody re-renders, which is the
  failure that looks exactly like success. See [[blog_subjects]] §12.9.

> **DEFERRED (operator, out of scope for now):** how much the agent may rewrite — a surgical
> touch on headline/lede/headings versus a full re-draft on target keywords. The invariants above
> hold under either. Decide before writing `_SEO_EDITOR_PROMPT`.

#### ⚠ The keyword dependency is not satisfied yet

`marketing/seo/README.md` defines the target band — **1,000–30,000 monthly SA searches,
KD ≤ 29** — and `seo/seeds/seed_pool.csv` holds **325 candidates** (167 taxonomy · 111 corpus ·
47 Saudi entities). But its `volume_sa`, `kd` and `verdict` columns are **empty in every row**:
the two-stage Semrush run has not happened.

So this agent has no scored keyword list to draw from. The API therefore takes keywords
**explicitly per job** (§3) rather than reading the pool — which also keeps the decision of
*what to target* on the marketing side, where that README says it belongs.

### 2.2 `blog_card_composer` — the card step

**The catalog** lives at
`marketing/social_media/_assets/templates/rayhan-legal-cards.html` — a Claude Design canvas
(«بطاقات ريحان القانونية») whose entire editable state is the `appifact-doc` script block: ten
`.dc.html` artboards plus `canvas.json`.

| Artboard | Title | Size | Fills |
|---|---|---|---|
| `Main` | غلاف المقال | 1200×630 | headline (OG size) |
| `Question` | سؤال من الميدان | 1080×1350 | the question as asked |
| `Judgment` | ملخّص حكم — مختصر | 1080×1350 | محكمة · رقم · تاريخ · واقعة · منطوق |
| `JudgmentFull` | ملخّص حكم — موسّع | 1080×1620 | + دائرة · المبدأ · حالة الحكم |
| `Takeaways` | أبرز النقاط | 1080×1350 | 4 numbered points |
| `TableCompare` | جدول مقارنة | 1080×1080 | 3 columns × 4 rows |
| `TableElements` | جدول عناصر المقال | 1080×1350 | 5 label/value rows |
| `Timeline` | المسار النظامي | 1080×1350 | 5 steps + venue each |
| `Quote` | قاعدة | 1080×1080 | a maxim + attribution |
| `Term` | مصطلح قانوني | 1080×1080 | term · تعريف · متى يسقط |

Shared by all ten: the ريحان lockup + «مساعدك القانوني الذكي», a category badge
(قضائي / عمّالي / قواعد / مصطلحات), a value-prop strip, and `rayhanai.com`. Two props each —
`theme` (فاتح/داكن) and `accent` (four brand colours) — plus a `$preview` size.

**The templates are the operator's design and are fixed. The agent selects and fills; it never
designs.** Cap: **7 cards per blog**, from ten templates.

Several templates are content-conditional by nature — `Judgment*` only where the article rests
on a ruling, `Timeline` only where there is a procedure, `TableCompare` only where a real
comparison exists, `Quote` only where a maxim is actually quoted. So selection precedes filling.

⚠ **Cards use Arabic-Indic numerals** (١٢٣٤٥) — `Takeaways` and `Timeline` both number their
rows that way. That is a deliberate carve-out from the app's Latin-digits policy
([[latin_numerals_policy]]): these are marketing images, not app chrome. Do not "fix" them, and
do not let an ESLint rule reach these files.

⚠ **`Judgment` and `JudgmentFull` need facts that are not in the prose.** محكمة, رقم الحكم,
التاريخ, الدائرة live in the *reference records*, not the article body. The shipped templates
carry `[رقم الحكم]` and `[التاريخ]` placeholders precisely because they were unfilled. An agent
given only the body will either leave the placeholders or invent a judgment number — and
inventing a judgment number is the worst failure this product can produce. Hence §1's capability:
the resolved `Reference[]` is handed in, and an unfillable slot must leave the placeholder rather
than guess.

> **DEFERRED (operator, out of scope for now):** the selection rubric, what decides the 7 when
> more than 7 could be justified, and exactly what the agent is fed. The storage contract, the
> cap, and the no-invention rule above are settled.

---

## 3. API surface

One declaration, three addressable steps. Generation costs a full deep_search run; SEO and cards
do not. Folding all three into one atomic call means a failed card render either strands the blog
or forces a regeneration already paid for — so each stage is independently re-runnable against a
published blog.

### `POST /internal/blog-post-jobs`

```jsonc
{
  // ── identity / dedup (existing) ────────────────────────────────
  "idempotency_key": "tg:asklawy:48211",
  "question": "…",                     // ANONYMIZED, self-contained

  // ── the public_blogs row ───────────────────────────────────────
  "title": "إصلاح المركبة قبل صدور حكم لجنة المنازعات التأمينية",
  "type": "judicial_research",         // laws_explanation | judicial_research | compliance
  "subjects": ["work-law", "saudization"],
  "slug": null,                        // Arabic; minted from title when null

  // ── retrieval pinning — BOTH OPTIONAL ([[blog_subjects]] §5) ───
  // Both set   → phase 1 skipped, plan is exactly this.
  // Either null → the PLANNER is invoked to determine it; whatever was
  //               supplied is overlaid on its output.
  // `support` MUST be nullable — `false` and "unset" are different requests.
  "mode": "case_led",                  // case_led | reg_compliance_led | full | null
  "support": true,                     // true | false | null
  "editorial_voice": true,             // the §6 aggregator prompt

  // ── publication ────────────────────────────────────────────────
  "publish_policy": "auto",
  "min_confidence": "medium",
  "publish_public": true,              // is_public on the new row

  // ── OPTIONAL — omit the key entirely to skip the step ──────────
  "seo": {
    "primary_keyword": "إصلاح المركبة قبل الحكم",
    "secondary_keywords": ["تعويض حادث مروري", "لجنة المنازعات التأمينية"],
    "target_questions": [
      "هل يجوز إصلاح السيارة قبل صدور الحكم؟",
      "كم مدة دعوى التأمين؟"
    ]
  },
  "cards": { "max": 7 },               // omit to skip; > 7 rejected server-side

  // ── provenance (existing) ──────────────────────────────────────
  "subtype": "marketing_telegram",
  "metadata": {},
  "callback_url": null
}
```

**Response** adds `root_id` alongside the existing `post_id` / `url` / `confidence`, because
every later call addresses the logical blog, not a version.

### The re-runnable steps

```
POST /internal/public-blogs/{root_id}/seo      { seo: {…} }   → new version, is_current flips
POST /internal/public-blogs/{root_id}/cards    { max: 7 }     → renders into Storage
POST /internal/public-blogs/{root_id}/retract                  → is_public = false
GET  /internal/blog-post-jobs/{job_id}                         → status (existing)
```

All service-key authed (`_verify_service_key`, fail-closed when `EDITORIAL_SERVICE_KEY` is
unset), all prefix-skipped in `middleware/rate_limit.py`, none owner-scoped.

**Validation that must be server-side, not trusted from the caller:**

- unknown `subjects` slug → **400**, never a silent drop (a blog with no subject is invisible in
  the browse tree and nobody notices until the traffic doesn't arrive)
- `type` outside the three → 400
- `slug` colliding with a subject slug or the reserved `subjects` → 400
- `cards.max > 7` → 400
- `seo` present but empty of keywords → 400, rather than an agent run with nothing to aim at
- `mode` outside the three literals → 400. **`null` is valid and means "planner decides"** — it
  must not be coerced to a default, and `support: null` must not be coerced to `false`
  ([[blog_subjects]] §5). A Pydantic `bool = False` here would silently turn every
  planner-decides job into a pinned `support=false` one.

---

## 4. Model slots

Two new entries in `AGENT_MODELS` (`agents/utils/agent_models.py`), the single editable slot
registry:

```python
"blog_seo_editor":     ModelPolicy("tier_2", primary="deepseek", reasoning="medium"),
"blog_card_composer":  ModelPolicy("tier_2", primary="deepseek"),
```

Rationale: both are transformers over supplied text with no retrieval, which is the same shape
`artifact_editor` runs at (deepseek-v4-flash, reasoning=medium). Neither justifies tier_1.

⚠ The deepseek-flash "structured-output-as-text" trap applies to both — use the shared
`make_json_salvager` TextOutput member, as `artifact_editor` and the aggregator both do.
[[structured_output_salvage]]

⚠ Cost lands in the `llm_calls` ledger like everything else. These runs bill to the **editorial
bot**, not to a customer, so they will show up in per-model consumption reports as bot spend —
expected, not a leak. [[llm_ledger]]

---

## 5. Card storage

A new Supabase Storage bucket, following the convention the existing three set
(`documents` private; `regulation-images` and `service-guide-images` public, mime-restricted,
50 MB):

```
bucket:  blog-cards
public:  true
limit:   52428800
mime:    image/png, image/webp
path:    {root_id}/v{version_no}/{artboard}-{n}.png
```

Keying the path by `version_no` matters: an SEO rewrite can invalidate the cards that quoted the
old prose, and a version-scoped path lets a fresh set exist without clobbering the old one or
guessing which is current.

> **DEFERRED:** whether card rendering happens in-process (headless browser over the `.dc.html`
> artboard) or by handing filled artboards back to marketing to render. This plan fixes the
> *destination and the path*, not the renderer.

---

## 6. The workflow seam — named, not designed

Two workflows take the finished product downstream. **The operator has explicitly deferred
their design**; this section exists only so the seam is deliberate rather than accidental.

```
                    ┌──────────────────────────────────────┐
  blog published →  │  seo (optional)  →  cards (≤7)       │  →  ┌─ X workflow
  (blog_subjects)   │  new version        blog-cards/…     │      └─ LinkedIn workflow
                    └──────────────────────────────────────┘
```

What the seam must therefore expose, whatever those workflows turn out to be:

- the **current version** of a blog, addressable by `root_id` — not a snapshot a workflow
  captured earlier and might replay stale
- its **public URL** (`/blog/{slug}`), stable across versions
- its **card set**, listable per version
- the blog's **`type` and `subjects`**, since those are the natural targeting axes

The three public targets the operator named — **X**, **the blog itself**, and **LinkedIn** — are
not three content variants in this plan. The blog is the artefact; X and LinkedIn are
distribution. If per-target text variants turn out to be wanted, that is a change to this seam
and should be planned then, not assumed now.

---

## 7. File manifest

### New

```
agents/marketing/__init__.py
agents/marketing/seo_editor/{agent,deps,models,prompts,runner}.py
agents/marketing/card_composer/{agent,deps,models,prompts,runner}.py
agents/marketing/templates.py                  the ten artboards as a typed catalog

backend/app/api/deepsearch_api/marketing.py    the /seo and /cards routes
backend/app/services/card_storage_service.py   bucket writes, version-scoped paths

shared/db/migrations/156_blog_cards.sql        card inventory (artboard, version, storage path)
                                               ⚠ was 155 in the first draft — 155 was taken by
                                               the append-version RPC during the blog_subjects
                                               build. Verify the next free number before writing.
```

### Modified

```
agents/utils/agent_models.py                   +2 slots (§4)
backend/app/api/deepsearch_api/models.py       +seo, +cards on the job request; +root_id on the response
backend/app/api/deepsearch_api/router.py       mount the marketing routes
backend/app/api/deepsearch_api/service.py      thread seo/cards through the job row
backend/app/middleware/rate_limit.py           prefix-skip the two new routes
backend/app/services/public_blog_service.py    append-version write ([[blog_subjects]] §2)
```

### Marketing repo (contract only — not this repo's work)

```
docs/blog_post_api_protocol.md                 the §3 body + the two step endpoints
social_media/telegram_api/generate_blogs.py    send title/type/subjects/seo/cards
```

---

## 8. Sequencing

Starts after [[blog_subjects]] step 8 — there is nothing to transform until a blog is published.

| # | Step | Gate |
|---|---|---|
| 1 | Migration 155 + the `blog-cards` bucket | Bucket public, mime-restricted; RLS on the inventory table |
| 2 | `agents/marketing/` skeleton + 2 model slots + the templates catalog | Ten artboards parse; slot resolution returns a FallbackModel |
| 3 | `blog_seo_editor` + its post-condition checks | **Invariant tests first**: a rewrite that adds a citation, renumbers one, or changes the slug is REJECTED and the current version stands |
| 4 | The `/seo` route + versioned write + cache purge | A live rewrite produces v2, serves at the same slug, and the page actually changes |
| 5 | `blog_card_composer` + `/cards` + Storage | ≤7 enforced; an unfillable judgment slot leaves its placeholder rather than inventing a number |

Step 3's tests come **before** step 4's route. An agent that can rewrite a live indexed page is
the highest-blast-radius thing in either plan; the invariants are the safety rail and they should
exist before the door opens.

---

## 9. Traps

- **`artifact_editor` cannot be reused** — `ALLOWED_KINDS` refuses `agent_search`, and it writes
  to `workspace_items`. Copy the philosophy, not the module.
- **The citation set is closed.** Enforce in the runner, never in the prompt. A prompt cannot be
  a guarantee.
- **No cache purge is needed today** — the wing is `force-dynamic`. That stops being true the
  moment it moves to ISR, and the move must carry revalidate-on-publish with it.
  [[isr_bake_docker_cache_trap]]
- **The slug is permanent.** No redirect layer exists. [[corpus_supersession_retirement]]
- **Cards keep Arabic-Indic numerals** — a carve-out, not a bug. [[latin_numerals_policy]]
- **Never invent a judgment number, date, court or circuit.** Leave the template placeholder.
- **The seed pool is unscored** — `volume_sa`/`kd`/`verdict` are empty in all 325 rows. Any
  design that reads the pool instead of taking explicit keywords is blocked on the Semrush run.
- **These agents bill to editorial-bot**, so consumption reports will show bot spend. Expected.
  [[llm_ledger]]
- **deepseek-flash returns structured output as text** — use `make_json_salvager`.
  [[structured_output_salvage]]

---

## 10. Explicitly out of scope

Carried here verbatim so a later reader does not mistake absence for oversight. The operator
deferred each of these:

1. The card **selection rubric** and what decides the 7 when more could be justified.
2. Exactly **what the card agent is fed** beyond §1's capability.
3. The SEO rewrite's **editorial bounds** — surgical touch versus full re-draft.
4. The **X workflow** and the **LinkedIn workflow** (§6 names the seam only).
5. The card **renderer** — in-process versus handed back to marketing (§5).
