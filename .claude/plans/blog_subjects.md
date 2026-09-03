# Blog Subjects — versioned public wing, browsable taxonomy, library-grade reading surface

Status: **PLANNED** (2026-09-01, rev 2). Supersedes the browse model in `public_blog.md` §8.

> **rev 2 — the public wing moved off `blog_posts`.** rev 1 put `slug`, `is_public`, the
> subject join and the whole browse surface on `blog_posts`. That collided with the snapshot's
> immutability guarantee the moment an SEO agent needed to rewrite a published article
> ([[marketing_agents]] §2). The public wing now lives in its own **versioned** table,
> `public_blogs`, and `blog_posts` reverts to being purely the share-link snapshot backing the
> 99 legacy tokens and every in-app «مشاركة». §4 and §6 are unchanged from rev 1;
> §2, §3, §5 and §7 were rewritten.

Related: [[marketing_agents]] (the SEO + card agents that consume this), [[blog_share_links]]
(the `blog_posts` snapshot), [[blog_post_api]] + [[blog_post_api_protocol]] (the editorial API),
[[seo_public_library]] (sitemap sections), [[compliance_entity_sections]] (the dispatcher
precedent).

---

## 0. Why

The public blog gallery has never had a single post in it. Ground truth, measured 2026-08-31:

```
blog_posts:  live 107 · is_published 99 · is_public 0
GET /api/v1/public/blogs   →  {"posts": []}
GET /sitemaps/blog         →  0 <loc> entries
```

Three causes, all fixed here:

1. **Nothing can publish.** `insert_post` has no `is_public` parameter — by design, editorial
   posts were minted unlisted. The only door in is `POST /blogs/{id}/publish`, gated on
   `users.can_access_blog`, which exactly one account holds and has never used.
2. **Nothing to browse.** A flat reverse-chronological list of one-off answers is not a
   destination. There is no axis a reader can enter on.
3. **Nobody owns it.** Publishing was an in-app curation gesture nobody performs.

This plan gives the blog its own table and a browse axis (**subjects**), promotes the reading
surface to the treatment the library wings get, teaches the aggregator to write an **article
rather than an answer**, and hands publishing to `C:\Programming\marketing`.

---

## 1. Decisions

| # | Decision |
|---|---|
| D1 | The browse axis is **subjects** — a closed, curated vocabulary in its own table. |
| D2 | **Many-to-many**: one blog targets several subjects. |
| D3 | ~~Type is carried by the subject~~ → **rev 2: `type` is carried by the BLOG.** Subjects are plain tags (slug + Arabic label). One owner, no contradiction. |
| D4 | **Subject slugs are English/ASCII. Blog slugs are Arabic.** |
| D5 | Seed vocabulary is 3, designed to reach ~100. |
| D6 | `/blog/{subject}` lists blogs; `/blog/{arabic-title}` is the blog. **Subjects win** the dispatch. |
| D7 | `/blog/{token}` stays alive forever — 99 links are already in the wild. |
| D8 | Reading surface adopts `TocRail`/`TocFloating` as «محتويات المدونة». **المراجع is untouched.** |
| D9 | `mode` + `support` are **optionally pinned** on the job request. Both supplied ⇒ phase 1 is skipped. Either absent ⇒ **the planner is invoked to determine it**, with whatever was supplied overlaid on its output. See §5. |
| D10 | Publishing is **open** to the editorial API, from the marketing repo. |
| D11 | Moderation = **retract to `is_public=false`**, over the internal service-authed API. |
| D12 | `can_access_blog` is **retired as a gate** (§8). |
| D13 | The hub's subject grid is **capped**. |
| D14 | The editorial path gets its own aggregator prompt variant (§6). |
| **D15** | **The public wing is a VERSIONED table.** Every SEO rewrite appends a version; the slug serves the current one. `blog_posts` stays immutable. |
| **D16** | **The editorial job writes DIRECT into `public_blogs`** — it does not flow through `blog_posts`. |
| **D17** | **A public blog is open by default.** `public_blogs.is_public` defaults **true** — inverted from `blog_posts`, where unlisted is the default. There is no token: the slug is the whole address. |
| **D18** | A version copies `references_json` **verbatim**. The citation set of a published blog is **closed** — see [[marketing_agents]] §2.1. |

### Seed vocabulary

| Subject | Slug |
|---|---|
| نظام العمل | `work-law` |
| سند الأمر | `promissory-note` |
| السعودة | `saudization` |

`work-law` is the operator's own slug — keep it verbatim, do not "correct" it to `labor-law`.

### The three types (on the blog, D3)

`laws_explanation` (أنظمة — شروحات وتعديلات) · `judicial_research` (أبحاث قضائية) ·
`compliance` (امتثال). Rendered as a badge, filterable. **Never a URL.**

---

## 2. Data model — migrations 153, 154

Highest applied migration is `152_qwen37_flash_pricing.sql`. ⚠ [[migration_drift]] — verify
live schema before trusting any file in `shared/db/migrations/`.

### `153_public_blogs.sql`

```sql
CREATE TABLE IF NOT EXISTS public.public_blogs (
    blog_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Versioning (D15). root_id == blog_id on v1 and is propagated to every
    -- later version, exactly the way blog_posts.source_post_id root-resolves
    -- through copy chains ([[blog_import]]).
    root_id       uuid NOT NULL REFERENCES public.public_blogs(blog_id),
    version_no    integer NOT NULL DEFAULT 1,
    is_current    boolean NOT NULL DEFAULT true,
    revision_note text,                        -- why this version exists ("seo: <keyword>")

    -- Address + identity
    slug          text NOT NULL,               -- Arabic; PERMANENT across versions
    title         text NOT NULL,
    type          text NOT NULL
                  CHECK (type IN ('laws_explanation','judicial_research','compliance')),

    -- Frozen content (D18)
    question_text   text NOT NULL,             -- ANONYMIZED. Never a raw question.
    content_md      text NOT NULL,
    references_json jsonb NOT NULL DEFAULT '[]',

    -- Provenance
    subtype        text,
    source_item_id uuid,                       -- the agent_search WI it was generated from
    author_user_id uuid NOT NULL,              -- editorial-bot
    confidence     text,                       -- high | medium | low, from the job

    -- Visibility. NOTE THE INVERTED DEFAULT vs blog_posts (D17).
    is_public     boolean NOT NULL DEFAULT true,
    is_published  boolean NOT NULL DEFAULT true,

    view_count    integer NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz
);

-- Exactly one live version per logical blog.
CREATE UNIQUE INDEX IF NOT EXISTS idx_public_blogs_current
    ON public.public_blogs(root_id) WHERE is_current AND deleted_at IS NULL;

-- The slug addresses the CURRENT version only — versions share an address.
CREATE UNIQUE INDEX IF NOT EXISTS idx_public_blogs_slug
    ON public.public_blogs(slug) WHERE is_current AND deleted_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_public_blogs_version
    ON public.public_blogs(root_id, version_no);

-- The gallery / sitemap predicate.
CREATE INDEX IF NOT EXISTS idx_public_blogs_gallery
    ON public.public_blogs(created_at DESC)
    WHERE is_current AND is_public AND is_published AND deleted_at IS NULL;

ALTER TABLE public.public_blogs
    ADD CONSTRAINT public_blogs_slug_nonascii
    CHECK (slug !~ '^[a-z0-9]+(-[a-z0-9]+)*$');   -- an ASCII slug belongs to a SUBJECT (§3)
```

The last constraint is the dispatcher's guarantee expressed as data: a blog slug can never be
shaped like a subject slug, so §3's resolution order can never be ambiguous.

### `154_blog_subjects.sql`

```sql
CREATE TABLE IF NOT EXISTS public.blog_subjects (
    subject_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         text NOT NULL UNIQUE,          -- ASCII kebab-case, permanent
    label_ar     text NOT NULL,                 -- copied from the operator, never retyped
    description_ar text,
    sort_rank    integer NOT NULL DEFAULT 0,
    is_active    boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.blog_subjects
    ADD CONSTRAINT blog_subjects_slug_ascii CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$');

-- Subjects belong to the LOGICAL blog, not to a version — an SEO rewrite must
-- never have to re-file them.
CREATE TABLE IF NOT EXISTS public.public_blog_subjects (
    root_id    uuid NOT NULL REFERENCES public.public_blogs(blog_id) ON DELETE CASCADE,
    subject_id uuid NOT NULL REFERENCES public.blog_subjects(subject_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (root_id, subject_id)
);

CREATE INDEX IF NOT EXISTS idx_public_blog_subjects_subject
    ON public.public_blog_subjects(subject_id);
```

`ON DELETE RESTRICT` on `subject_id` is deliberate: retiring a subject is `is_active=false`,
never a delete that silently unfiles published blogs.

**RLS.** `public_blogs`: anon+authenticated `SELECT` where
`is_current AND is_public AND is_published AND deleted_at IS NULL`; **no** INSERT/UPDATE/DELETE
policy — writes are service-role only, matching `blog_posts`. `blog_subjects`: anon `SELECT`
where `is_active`. `public_blog_subjects`: anon `SELECT`, writes service-role only.

Seed the 3 subjects in the same migration, `ON CONFLICT (slug) DO NOTHING`.

### Versioning mechanics — migration 155

Publishing version N+1 must be one transaction. **The backend cannot do that**: it reaches
Postgres through PostgREST, which has no way to wrap several statements in a transaction. The
best a service layer can manage is insert → demote → promote with a compensating re-flip, which
is safe (the partial unique index makes two-current impossible) but can strand an orphan
non-current row on a crash — on the write path the SEO agent takes on *every* rewrite.

So the flip lives in the database:

```sql
-- 155_public_blogs_append_version.sql
public.append_public_blog_version(
    p_root_id uuid, p_content_md text,
    p_title text = NULL, p_revision_note text = NULL,
    p_type text = NULL, p_confidence text = NULL
) RETURNS public.public_blogs
```

`FOR UPDATE` on the current version (so concurrent appends serialize rather than race the unique
index) → demote → insert N+1 as current → return it. One implicit transaction, no compensation
logic, no debris. It carries `slug`, `references_json`, `question_text`, provenance and both
visibility flags forward; `NULL` for title/type/confidence means "keep the current value". It
raises `no_data_found` when the root has no current version.

⚠ **EXECUTE is revoked from `PUBLIC`/`anon`/`authenticated` and granted to `service_role` only.**
Postgres makes functions world-executable by default, and migration 153 grants no write policy
at all on this table — without the revoke, this function would be a write primitive reachable
with the anon key.

⚠ **A published slug is permanent, across every version.** There is no redirect layer — a
rename 404s ([[corpus_supersession_retirement]] learned this the expensive way). A rewrite may
change `title`; it must never change `slug`.

⚠ **A new version must purge the slug's CDN + ISR cache.** Otherwise the rewrite deploys and
the page silently doesn't change — the failure mode that looks exactly like success.
[[isr_bake_docker_cache_trap]]

### What stays on `blog_posts`

Everything it does today, unchanged: the 99 unlisted share links, in-app «مشاركة», مدوناتي,
blog-import. It gains **no** columns from this plan. Its `is_public` column becomes vestigial —
the public wing no longer reads it — but stays for the same reason `can_access_blog` does (§8).

---

## 3. URL & routing contract

### The surface

| URL | What | Auth |
|---|---|---|
| `/blog` | Hub — capped subject grid + recent blogs | anon |
| `/blog/subjects` | Full subject index (~100) | anon |
| `/blog/work-law` | Every public blog carrying that subject | anon |
| `/blog/{عنوان-عربي}` | The blog — current version | anon |
| `/blog/{token}` | A legacy `blog_posts` share link | anon |
| `/blogs/*` | مدوناتي, owner surface | authed — untouched |

### The dispatcher

`app/blog/[token]/` is **renamed to `app/blog/[slug]/`**. Next cannot host two dynamic segments
at one level, so one route resolves all shapes. `app/blog/subjects/` is a literal static segment
and always wins over `[slug]` — no reserved-word logic needed for it.

Resolution order — **subjects win**, per D6:

```
1. blog_subjects.slug  == ref  (is_active)              → subject listing
2. public_blogs.slug   == ref  (is_current, visible)    → the blog
3. blog_posts.token    == ref  (visible)                → legacy share snapshot
4.                                                      → notFound()
```

Two tables, three lookups. Steps 1 and 2 can never collide: §2's two CHECK constraints put
subject slugs in ASCII and blog slugs outside it, so the vocabulary check is a guarantee rather
than a convention.

**Mint-time refusal.** The publish path rejects a slug that (a) matches any
`blog_subjects.slug`, or (b) equals a reserved literal — currently `{"subjects"}`. This is the
[[compliance_entity_sections]] lesson as code: *reserved slugs must be refused by the
dispatcher's writer, not discovered by its reader.*

**No token on `public_blogs` (D17).** A `blog_posts` token exists because that page is
*unlisted* — the unguessable string is what grants access. A public blog is open, so the slug is
the whole address.

⚠ **This moves the metered source reveal.**
`GET /public/blog/{token}/references/{n}/source` becomes slug-keyed for this wing. Read that
endpoint's docstring before touching it: the token was only ever the *addressing*, never the
entitlement — "entitlement is evaluated against the READER, not the author", via
`resolve_access(..., surface='reference')`, with anon getting a **402** (not 401, so the global
redirect-to-login never fires on a public page). All of that carries over unchanged; only the
key does.

⚠ **Arabic slugs come back percent-encoded.** Next hands a non-ASCII dynamic param to the page
as `%D8%A7%D9%84…`. `lib/library/courts.ts` models the fix — decode exactly once at the entry
point. Do **not** copy `entities.ts`'s "no normalizer needed" comment; that file is ASCII and
this route is not. Same hazard on ISR revalidation, where a mismatch is a 200 that refreshes
nothing ([[isr_revalidate_encoding]]).

---

## 4. Reading surface (D8)

*Unchanged from rev 1.*

### The TOC swap

`components/blog/BlogTableOfContents.tsx` is retired in favour of the library pair, titled
**«محتويات المدونة»**:

- `TocRail` — desktop, sticky, `lg:` and up
- `TocFloating` — phone; the pill/panel/sheet widget
- both consume `TocEntry[]` (`types/library.ts:48`) and share `useTocScrollspy`

`BlogArticleView` already computes `extractHeadings(body)` and gates on `headings.length >= 2`.
That gate and the heading slugs stay; only the projection changes — `Heading[]` → `TocEntry[]`
(`{ id: slug, label: text, href: '#'+slug, level: depth }`).

⚠ **`parseTocLabel` is built for «المادة 80».** Given a prose blog heading it falls through to
`{ chip: null, text: label }` and renders the full heading with no gutter chip — the correct blog
behaviour. Do not "fix" it; the fallback IS the design.

⚠ **The shared scrollspy does not watch blog anchors by default — this was a real regression and
is now fixed.** `useTocScrollspy` filtered to hrefs starting with `#sec-`, the shape a corpus
document page emits; a blog's anchors are bare `slugifyHeading` ids, so the rail rendered and no
row ever lit up. `BlogTableOfContents` — the component this swap *deleted* — had its own working
IntersectionObserver, so the upgrade would have shipped a downgrade on the one surface it exists
to improve. Fix: an optional `spyPrefix` on the hook and on `TocRailProps`/`TocFloatingProps`,
defaulting to `#sec-` so every corpus wing is behaviourally unchanged; the blog passes `"#"`.
The rejected alternative — `sec-`-prefixing blog heading ids — would break every `#slug` link
already copied out of a published article.

`badge` = heading count, e.g. «7 أقسام».

### Reading best-practices to carry over

- **`text-read`** (18/17px) on the article body. Already registered in `lib/utils.ts`'s
  `extendTailwindMerge` classGroups, so `cn("text-read", …)` will not silently drop it.
- ~~**`LegalBlocks`** structure rendering~~ — ⚠ **DROPPED during the build (2026-09-02): it is
  not buildable here, and asking for it was my error.** `LegalBlocks` consumes `LegalBlock[]`
  from `toLegalBlocks` — pre-formatted *corpus* text carrying `TBL_`/`IMG_` tokens, not markdown.
  It renders headings as styled `<p>` with **no ids**, which kills every TOC anchor §4 depends
  on, and it has no `[n]` citation handling, which kills the `focusedN` dance this same section
  calls untouchable. What §4 actually wanted — the long-form reading scale and an editorial
  heading ladder — is already what `MarkdownRenderer` emits in `prose` + `headingAnchors` mode,
  which also emits the `slugifyHeading` ids the TOC links to. That plus `text-read` on the
  `<article>` is the built result.
- The `TocList` inline index above the body: the crawlable copy of the index, and the sentinel
  `TocFloating` measures against.

### المراجع — untouched

`ReferencePanel`, `referenceLabel`, the `[n]` focus dance through `focusedN`/`handleFlashDone`,
and the metered source reveal all stay **exactly** as they are. This is the deliberate centre of
the surface: the references panel is the visible proof-of-work that sells deep_search, and the
one thing on the page a reader cannot get from a chatbot.

🚨 **"Untouched" was not enough — snapshot the PAYLOAD builder, not the models.** Found in
production 2026-09-03, reported by the operator: clicking `[n]` only flashed the card. No
«عرض المصدر», no «افتح في ريحان». The publisher called `fetch_item_references`, which returns
`Reference` **models**, and `model_dump`ed them — and **a `Reference` carries neither
`has_source` nor `library_url`**. Both are added by `fetch_item_references_**payload**`, which is
what a *reader* needs and what the legacy `blog_posts` snapshot has always frozen.
`ReferencePanel` gates the reveal on `has_source === true`, so an absent key meant the affordance
was **never rendered — not hidden**, while the capability was there the whole time (the reveal
route resolves from `ref_id` + `domain` and never reads `source_view`). One wrong function, two
dead affordances, and nothing in a type, a log line or a status code to say so. Fixed by
snapshotting the payload builder verbatim, exactly as `blog.share_artifact` does, plus a read-time
backfill so already-published rows recover without a migration.

⚠ The two keys are asymmetric **on purpose**. A frozen `library_url: null` is KEPT — null is a
real answer ("no published page"), so re-resolving would tax every read forever. A missing
`has_source` is DERIVED — it has no null convention, so absent means never computed.

⚠ The derivation is deliberately **stricter** than `resolve_ref`: a prefix-less `ref_id`, a prefix
disagreeing with `domain`, and a `compliance:` sha1 (not a service handle) all return False.
A `True` the endpoint then refuses would **sell an unlock we cannot deliver**. The one axis it
cannot cover is EXISTENCE — a re-chunked source still looks resolvable — and that is safe because
`resolve_ref` runs *before* `resolve_access`, so the reader gets a refusal card and is never
charged. A test pins that ordering; if anyone moves the charge earlier, it fails.

Measured on the first two published articles (30 references): 28 reveal · 17 library link ·
13 outbound · **0 with no affordance at all**.

### Subject chips + type badge

Under the byline: the blog's `type` badge plus a row of subject chips, each linking to
`/blog/{subject}`. This is the internal-linking spine of the wing — how a reader who landed on
one article from Google discovers the subject, and how link equity reaches the subject pages.

---

## 5. Publishing — the marketing contract (D9, D10, D11, D16)

### Ownership

`C:\Programming\marketing` owns publishing end-to-end, shaped like the pipeline already there:
`social_media/telegram_api/` — `extract_by_date.py` → `filter_questions.py` →
`generate_blogs.py`, POSTing to `/internal/blog-post-jobs` with `EDITORIAL_SERVICE_KEY` and an
idempotency key.

The full request body, the SEO block, and the card block are specified in
**[[marketing_agents]] §3** — that document owns the API contract. The fields this plan cares
about are `title`, `type`, `subjects`, `slug`, `mode`, `support`, `editorial_voice` and
`publish_public`.

An unknown subject slug is a **400, not a silent drop** — a blog that publishes with no subject
is invisible in the browse tree and nobody notices until the traffic doesn't arrive.

### The write (D16)

`generate_answer_headless` produces the `agent_search` workspace item as it does today; the
publisher then writes **v1 directly into `public_blogs`** with `is_public` defaulting true.
It does **not** create a `blog_posts` row. `source_item_id` keeps the link back to the WI.

**The two visibility flags mean different things — an earlier draft of this plan conflated
them.** It said `publish_public=false` produced "a draft nobody can reach" while retraction —
the same column, the same value — left "the URL resolving for anyone holding the link". Both
cannot be true. Corrected during the build (2026-09-02):

| State | Gallery + sitemap | Reachable by slug | Set by |
|---|---|---|---|
| `is_public = true` | yes | yes | the default |
| `is_public = false` | **no** | **yes — unlisted, not hidden** | `publish_public=false`, or retraction |
| `is_published = false` | no | **no — 404** | the confidence gate |

So `is_public=false` is exactly the posture a `blog_posts` share link has always had: readable by
anyone holding the address, absent from every index. Genuinely unreachable is `is_published=false`,
which `publish_policy`/`min_confidence` still owns — a `low`-confidence article is written
unpublished regardless of what the request asked for.

⚠ This is why `GET /public/blogs/{slug}` cannot be backed by the RLS SELECT policy from
migration 153: that policy requires `is_public`, so a retracted blog would be invisible to it.
The by-slug read goes through the service-role client and re-states each predicate — those
filters *are* the access contract for this route.

### Pinning the planner (D9)

The seam already exists and needs no new architecture:

```
agents/deep_search_v4/planner/models.py:54   Mode = Literal["case_led","reg_compliance_led","full"]
agents/deep_search_v4/planner/models.py:90   support: bool
agents/deep_search_v4/planner/runner.py      handle_planner_turn(..., decision=None)
                                             "when supplied (resume path) phase 1 is skipped"
agents/orchestrator.py:2981                  _run_deep_search(input, supabase, decision=None, ...)
```

Threading: `generate_answer_headless(mode, support, editorial_voice)` →
`handle_message(pinned_plan=…)` → `_dispatch` → `_run_deep_search(decision=…)`.

**`pinned_plan` always forces the family; it only sometimes carries a decision.** These are two
separate things and conflating them is the easy mistake here. An editorial job always wants
`deep_search` — that is not a judgement call, it is what the wing is for — so `pinned_plan`
bypasses the router's family choice unconditionally. Whether it also carries a `PlannerDecision`
depends on what marketing sent:

| `mode` | `support` | What happens |
|---|---|---|
| set | set | **Fully pinned.** `PlannerDecision(mode, support, rationale="editorial_pin: …")` is passed as `decision`, phase 1 is skipped entirely. |
| set | `null` | **Partially pinned → phase 1 runs**, then `mode` is overlaid on its output. |
| `null` | set | same — phase 1 runs, `support` overlaid. |
| `null` | `null` | **Unpinned.** Phase 1 runs and decides both. |

⚠ **`support` must therefore be `Optional[bool] = None`, not `bool = False`.** A plain boolean
cannot express "not pinned" — an absent `support` would be indistinguishable from a deliberate
`support=false`, and every partially-pinned job would silently lose its support executor.

### ⚠ A pause has no user to answer it

The moment phase 1 can run on this path, the `ask_user` deferral becomes reachable — and it is
registered unconditionally (`@agent.tool_plain` inside `create_planner_decider`; the decider's
*only* tool). The planner is instructed to use it liberally: for a vague query, for an
unattributed company or body ([[planner_entity_disambiguation]]), for an article number with no
named نظام. In a headless editorial job there is nobody to reply, so the run would sit paused
until `catchup_stuck_jobs` reaps it.

**Rule: in the headless path, a phase-1 pause is converted to the safe default and the run
continues.** `_default_decision(reason)` already exists for exactly this shape — it is what
phase 1 falls back to when it raises (`reg_compliance_led`, `support=False`, §9 of the runner) —
so reuse it with a distinct reason (`editorial_pause_no_user`) and log it. A blog generated from
the default plan is a worse blog; a job stranded for an hour is no blog at all, and the
`confidence` gate still catches a genuinely under-served article before it publishes.

Deliberately **not** removing `ask_user` from the toolset for headless runs. It would be cleaner
semantically, but `prompts.py` instructs the decider to use `ask_user` in at least five separate
places — stripping the tool while leaving those instructions is a prompt/tool mismatch that
degrades the very decision we are asking it to make.

**Fully pinned remains the recommended path**, and marketing should send both fields whenever the
type implies them (the table below). It is cheaper — one fewer LLM call — deterministic, and the
only variant that cannot pause at all.

⚠ **`query_restatement` is empty on the fully-pinned path only.** It defaults to `""`, and an
empty restatement means the raw query flows downstream verbatim — correct here, since marketing's
questions are already curated and self-contained. When phase 1 *does* run, a restatement is
produced and used normally. Both are fine; just do not assume one behaviour in code that sees
both.

Section → mode is marketing's rule; Luna only honours what arrives:

| Type | mode | support |
|---|---|---|
| `judicial_research` | `case_led` | `true` |
| `laws_explanation` | `reg_compliance_led` | operator's call |
| `compliance` | `reg_compliance_led` | operator's call |

### Retraction (D11)

```
POST /internal/public-blogs/{root_id}/retract   →  is_public = false on the current version
```

Service-key auth (`_verify_service_key`, fail-closed when `EDITORIAL_SERVICE_KEY` is unset),
same rate-limit prefix skip as the job routes. **Not** owner-scoped — that is the point: the
in-app publish/unpublish routes filter by `user_id`, so XL0RCH hitting editorial-bot's row gets
a **404, not a 403**, and no user-facing flag can fix that. The service key is the authority.

Retract **delists only** — it does not set `deleted_at` and does not set `is_published=false`.
The blog drops out of the gallery and the sitemap; the URL keeps resolving for anyone holding
the link.

⚠ Because delisting leaves a live 200, it does **not** deindex. §7 handles that.

---

## 6. The editorial aggregator prompt (D14)

*Unchanged from rev 1.*

### Why a variant and not a tweak

The in-app aggregator writes for a lawyer who **asked** and is waiting. The editorial path
writes for a stranger who arrived from Google with no question in the frame. Same evidence, same
citations, different rhetoric. Serving both from one prompt degrades the in-app answer, which is
the product.

### The seam — already first-class

```
aggregator/prompts.py:692   AGGREGATOR_PROMPTS: dict[str, str]     the registry
aggregator/prompts.py:712   get_aggregator_prompt(key)             raises KeyError on unknown
aggregator/agent.py:59      create_aggregator_agent(prompt_key=…)
planner/apply.py:62-70      MODE_PROFILES[mode]["aggregator_prompt_key"]
```

Adding a variant is three registry entries plus a selection flag.

### Three keys, not one

`prompt_editorial_case` · `prompt_editorial_reg_compliance` · `prompt_editorial_full`.

One prompt for all modes would throw away exactly the mode-specific guidance that pinning the
mode exists to select. Compose them the way every existing variant is composed:

```python
PROMPT_EDITORIAL_CASE = f"""{_SHARED_ROLE_AR}
{_MODE_CASE_BODY_AR}
{_EDITORIAL_FORM_AR}
{_COT_TEMPLATE_AR}

{_CITATION_RULES_AR}
"""
```

This requires extracting the three mode bodies out of their current f-strings into
`_MODE_CASE_BODY_AR` / `_MODE_REG_BODY_AR` / `_MODE_FULL_BODY_AR` and rebuilding `PROMPT_MODE_*`
from them. Mechanical — **no wording change**, so the in-app path stays byte-identical. Assert
that in a test.

⚠ **`_CITATION_RULES_AR` goes LAST in every variant.** The editorial block is inserted *before*
it, never appended after.

⚠ Edit `agents/deep_search_v4/aggregator/prompts.py`. `agents/prompts/*.md` is a read-only
catalog — editing it changes nothing. [[prompts_md_reference_only]]

### What `_EDITORIAL_FORM_AR` instructs

Derived from the operator's worked example (the insurance-committee article). Two jobs.

**(a) Mask the identity of the question — rhetorical de-identification.**

- Never address a questioner. No «سؤالك», no «حالتك», no second person, no «السائل».
- Open by generalising to the class of people who live the situation — «يواجه كثير من المؤمَّن
  لهم في السعودية معضلة عملية بعد وقوع حادث مروري…».
- Carry no detail that exists only because one person asked: no first-person possessives, no
  specific dates, amounts or parties unless the number *is* the legal point.
- The article is about the rule, illustrated by the situation — not about the situation,
  answered by the rule.

⚠ **A third layer, not a duplicate of two that exist.** `masking_service` / `PrivacyCodec`
(وضع السرية) redacts *identifiers* and already runs on this path — `handle_message` builds a
codec internally when the blog API calls it with `codec=None`. Marketing anonymizes the question
upstream: the protocol contracts `question` as "anonymized, self-contained", keeping
`question_raw` in `metadata` where it "must not leak". Neither changes grammatical person or
narrative framing. All three are needed.

**(b) Re-shape the answer into an article.**

- A headline as the **first line** (see the H1 contract below).
- A two-paragraph lede: the practical dilemma, then the short answer up front.
- `##` sections with ordinal Arabic headings (أولاً، ثانياً…). These become the §4 TOC entries,
  so they must be self-describing — «أولاً: النظام لا يمنع الإصلاح المسبق», not «أولاً».
- Bold lead-ins for enumerated points inside a section.
- Close on `## الخلاصة`.
- Continuous prose. No memo skeleton, no «الوقائع / التكييف / المطلوب» headers, no bullet dump.

**(c) A vague question — assume, and say what you assumed.** *(added 2026-09-02)*

This closes a loop §5 opens. On this path the planner's `ask_user` deferral is converted to the
default decision because there is nobody to answer it — so vagueness survives all the way to the
aggregator, and something has to deal with it there. Hedging across every reading produces an
article that serves no one; declining produces no article at all. An editorial draft is reviewed
and adjustable, which is exactly what makes an openly stated assumption safe here and a silent
one unacceptable.

- **Take the most probable reading and answer it properly** — unstated contract type, a party
  whose capacity is not given, a procedure that differs by forum, an unclear stage of a dispute.
- **Let the harvest decide.** The references are themselves evidence of what the question most
  likely meant; prefer the reading they actually cover.
- **State it in the lede**, one sentence in the second paragraph, in the reader's own language —
  «هذا المقال يفترض أن العقد محدّد المدة…». Never a footnote, never buried in الخلاصة, never a
  silent narrowing. A stated assumption is something an editor can correct; an unstated one is a
  trap the reader walks into.
- **Name a materially different reading in one line**, not a second article.
- ⚠ **Assume about the SITUATION, never about the LAW.** Facts, posture, forum and stage are
  fair game. A نظام, an article number, a rule, a court or a body that is not in `<references>`
  is not — that prohibition is absolute and this section does not touch it. An assumed fact is
  editorial judgement; an assumed rule is fabrication.
- **An assumption is not a gap.** `gaps` reports what the references failed to cover. Narrowing
  an ambiguous question so it can be answered well is not a failure of the harvest — unless a
  reading you *rejected* is one the references genuinely could not have answered either.

**(d) Inherited and binding — does not change.**

- `[n]` discipline, Western digits, `[n]` reserved for references only.
- **No «المراجع» section inside `synthesis_md`.** Appended programmatically; both
  `_SHARED_ROLE_AR` and `_CITATION_RULES_AR` already forbid it. The operator's example ends with
  a المراجع block — that block is the **rendered** output.
- **Non-contiguous citation numbers are correct.** `[2] [4] [5] [7] [10]` has gaps because
  numbers are pre-assigned to the whole reference set by the pre-processor and unused ones are
  absent. Never renumber — the numbers are the join key to `references_json` and
  `ReferencePanel`.
- No legal disclaimer inside the body.
- `used_refs` / `gaps` / `confidence` semantics unchanged; `confidence` still drives
  `publish_policy` + `min_confidence`.

### The H1 / title contract ⚠

`BlogArticleView` renders `title` as a centred hero **and** the body below it. An H1 left inside
the body double-renders the headline and adds a stray level-1 TOC entry.

Resolution: the prompt writes the headline as the **first line** of `synthesis_md`; the publish
path **extracts it into `public_blogs.title` and strips it from the body**. A `title` supplied on
the request wins, and the extracted line is still stripped.

Deliberately **not** adding a `title` field to `AggregatorLLMOutput`: that schema is shared with
every in-app search, and the salvager's retry message names its four keys verbatim
(`agent.py:33`).

### `display_mode`

`public_blogs` has no `display_mode` column — this wing is always the article template. The
`BlogPostJobRequest.display_mode` field stays for the `blog_posts` path only.

⚠ `public_blogs.question_text` is `NOT NULL` and ships in the public JSON even though the
article template never displays it. Store the anonymized question — never a raw one.

### Selection

`build_retrieval_config(decision, …, editorial=False)`. When true, `aggregator_prompt_key`
resolves to the editorial twin of the mode's key. Threaded from `handle_message(pinned_plan=…)`
alongside the pinned decision.

### Validation

> ⚠ **Corrected during the build (2026-09-02).** This section originally claimed a shape check
> would "burn a retry on every editorial job". **That was wrong.** `validate_llm_output` was
> narrowed in 2026-06 to `passed = bool(citation_ok and gap_honesty_ok)`
> (`postvalidator.py:620`); `structure_ok` and `arabic_only_ok` are surfaced but **non-blocking**,
> and `correction.py` only builds correction blocks for those same two gates. So the article
> shape could never have triggered a retry. The real cost was a false *note* on every editorial
> report — worth fixing, but not the failure the plan described.

Two checks are branched for the editorial keys; **the citation checks are untouched and stay
strict**:

- **`check_structure`** — the in-app skeletons reject the article on sight: it opens on an H1
  headline rather than `## الخلاصة`, its headings are ordinal and question-specific rather than
  drawn from a fixed vocabulary, and it *closes* on the summary. The editorial branch checks only
  what the prompt actually pins — first-line H1, exactly one H1, ≥1 `##`, closing `## الخلاصة`.
- **`check_query_anchoring`** — asks "did the opening restate the user's question", which
  §6(a) **forbids** on this path. Editorial keys short-circuit to `True`.

`prompt_mode_*` deliberately stays in the existing unknown-key fallthrough — zero change to the
in-app path.

---

## 7. SEO

### `noindex` when not public

`app/blog/[slug]/page.tsx` sets **no `robots` key at all** today, so every post is indexable the
moment it is linked. Add:

```ts
robots: blog.is_public ? undefined : { index: false, follow: true }
```

Two things fall out, both correct: a retracted blog stops being a search result while its direct
link keeps working, and the legacy `blog_posts` share links — served on the same route — become
`noindex` retroactively. They were minted unlisted and were never meant to be indexed.

### Sitemap

`SITEMAP_SECTIONS` (`lib/seo/sitemap.ts:90`) gains **`blog-subjects`**. The existing `blog`
section switches from `blog_posts` tokens to `public_blogs` slugs — current versions only.

Backend `sitemap_blog_urls` (`services/library_service.py:328`) reads `public_blogs` and
projects `slug`, with `lastmod` = the current version's `updated_at`. A new version therefore
bumps `lastmod`, which is a genuine freshness signal rather than churn. New
`sitemap_blog_subject_urls` lists subjects **with ≥1 public blog**.

⚠ **Empty sections must not be listed.** The rule the `courts` removal wrote into
`SITEMAP_SECTIONS`' own comment — *"a listed section with an empty urlset is a file Google
refetches hourly to learn nothing."* With ~100 subjects seeded ahead of content, most will be
empty for months. The `≥1` filter is the contract, not an optimization. Same threshold governs
the hub grid (D13) and `/blog/subjects`.

`/blog` and `/blog/subjects` are hardcoded → `getStaticUrls()` in the `static` section.

---

## 8. Retiring `can_access_blog` (D12)

### What it is

A `users` column from migration 084, repurposed once. **084**: gated *viewing* the members-only
directory. **085**: `/blog` went anon, so it was flipped to gate *curation* — who may set
`is_public=true`.

### Where it is read — two places

| Site | Behaviour |
|---|---|
| `api/blog.py:687` — `POST /blogs/{post_id}/publish` | 403 «غير مصرح لك بالنشر في المدونة العامة» |
| `api/blog.py:565` — `GET /blogs/mine` | mirrored as `can_publish_public` so the UI renders a toggle |

### Why it goes

The public wing is a different table now, written by the service key. The gate guards a door
that no longer leads anywhere. And it never granted the power actually wanted — moderating
someone else's row is blocked by **ownership**, not curation. Measured: exactly one account
(`xl0rch@gmail.com`, `c5f4cff0-…`) holds it; 0 posts were ever published through it.

### The retirement

1. Drop the gate from `POST /blogs/{post_id}/publish` — it stays owner-scoped.
2. Drop `can_publish_public` from `MyBlogsResponse`, `types/index.ts`, `lib/api.ts`, and the
   مدوناتي publish toggle.
3. Delete `blog_service.user_can_access_blog`.
4. **Leave the column in the DB, dormant.** Dropping it is a migration with no upside and a
   [[migration_drift]] footgun; `case_id` is the precedent for a column the app stopped writing.

Moderation lives at `POST /internal/public-blogs/{root_id}/retract` (§5).

---

## 9. File manifest

### New

```
shared/db/migrations/153_public_blogs.sql
shared/db/migrations/154_blog_subjects.sql
shared/db/migrations/155_public_blogs_append_version.sql   the atomic version flip (§2)
shared/db/migrations/156_public_blogs_job_idempotency.sql  one job → one blog (§11)

backend/app/services/public_blog_service.py       versioned reads/writes, subject joins
backend/app/api/public_blogs.py                   the anon read surface
frontend/app/blog/subjects/page.tsx               full subject index
frontend/lib/blog/slug.ts                         Arabic slugify + decode-once normalizer
frontend/components/blog/SubjectChips.tsx         chips + type badge
frontend/components/blog/SubjectGrid.tsx          capped hub grid
```

### Renamed

```
frontend/app/blog/[token]/  →  frontend/app/blog/[slug]/     (the §3 dispatcher)
```

### Modified

```
backend/app/api/blog.py                 source-reveal → slug-keyed for public_blogs;
                                        −can_access_blog gate
backend/app/services/blog_service.py    −user_can_access_blog; −list_public_blogs
backend/app/services/library_service.py sitemap_blog_urls → public_blogs slugs;
                                        +sitemap_blog_subject_urls   ⚠ BOTH live here, not in
                                        blog_service.py as an earlier draft of this manifest said
backend/app/models/responses.py         PublicBlogResponse (+is_public,+slug,+type,+subjects);
                                        −can_publish_public on MyBlogsResponse
backend/app/api/deepsearch_api/*        request fields + direct write + retract ([[marketing_agents]] §3)
agents/deep_search_v4/aggregator/prompts.py      extract _MODE_*_BODY_AR; +_EDITORIAL_FORM_AR
agents/deep_search_v4/aggregator/postvalidator.py  branch/loosen shape checks
agents/deep_search_v4/planner/apply.py  build_retrieval_config(+editorial)
agents/orchestrator.py                  handle_message(+pinned_plan) → _run_deep_search
frontend/components/blog/BlogArticleView.tsx  TocRail/TocFloating, text-read, LegalBlocks,
                                              SubjectChips; drop BlogTableOfContents
frontend/app/blog/page.tsx              hub: capped SubjectGrid + recent blogs
frontend/lib/seo/sitemap.ts             +"blog-subjects"
frontend/app/sitemaps/[section]/route.ts    + the blog-subjects case
frontend/lib/api.ts · frontend/types/index.ts   new shapes; drop can_publish_public
frontend/components/blogs/MyBlogsGrid.tsx   drop the publish toggle
```

### Deleted

```
frontend/components/blog/BlogTableOfContents.tsx
backend/app/services/blog_service.py :: user_can_access_blog
```

---

## 10. Sequencing

| # | Step | Gate before moving on |
|---|---|---|
| 1 | ✅ **DONE 2026-09-02.** Migrations 153 + 154 written and **applied to prod**; 3 subjects seeded; RLS on with 1 SELECT policy each. Gate probed transactionally (rolled back): `blog-rejects-ascii` · `blog-accepts-arabic` · `subject-rejects-arabic` · `subject-rejects-reserved` — all pass, so the dispatcher's resolution order is enforced by the DB, not by convention | — |
| 2 | ✅ **DONE 2026-09-02.** `public_blog_service.py` + `api/public_blogs.py` — 4 anon routes (gallery · subject vocabulary · subject feed · blog by slug) + 7 response models | 49 tests pass; `import backend.app.main` clean. **The legacy `GET /public/blogs` on `blog.py` was DELETED**, not shadowed — it declared the same path, and FastAPI keys OpenAPI by path, so shadowing left `/docs` advertising the wrong model for a live endpoint |
| 3 | ✅ **DONE 2026-09-02.** Versioned write via the **migration-155 RPC**, mint-time slug refusal, headline extract/strip | Tests assert exactly one RPC call and **zero** manual `update`s on `public_blogs`, so reintroducing hand-rolled flipping fails the suite. Slug refusal covers the whole ASCII shape (not just currently-held subject slugs), ordered reserved → vocabulary → shape → uniqueness(409) |
| 4 | ✅ **DONE 2026-09-02.** `[token]` → `[slug]` via `git mv`, `lib/blog/slug.ts` (one decode + three shape predicates), hub, subject listing, `/blog/subjects` | **All 8 baseline tokens verified 200** against a real backend + `next start` — correct titles, `noindex, follow`, self-canonical, no redirect. The chain is **fall-through, not exclusive**: a shape test only changes how many round trips a ref costs, never which page it lands on. A 32-hex token is *also* ASCII-kebab-shaped, so token-shaped refs try `blog_posts` first and fall back to the subject lookup — precedence survives for the never-minted 32-hex-subject case without spending a wasted lookup on all 99 live links |
| 5 | ✅ **DONE 2026-09-02.** TOC pair as «محتويات المدونة», `text-read`, `SubjectChips` + type badge; `BlogTableOfContents.tsx` deleted | ⚠ **`LegalBlocks` dropped — see §4.** ⚠ **Scrollspy was inert and is now fixed** (`spyPrefix`) |
| 6 | ✅ **DONE 2026-09-02.** `noindex` when `!is_public`, both sitemap sections, slug-keyed reveal | ⚠ The slug reveal route is **plural** (`/public/blogs/{slug}/…`) beside the singular token route; the client routes on key shape. Both sections currently serve empty urlsets — §12.5 |
| 7 | ✅ **DONE 2026-09-02.** Mode bodies extracted, `_EDITORIAL_FORM_AR` + 3 editorial keys added, `build_retrieval_config(editorial=)`, 2 postvalidator checks branched | **Byte identity PASS** — SHA-256 pinned for all three `PROMPT_MODE_*` (`test_prompts_mode_body_identity.py`, 26 tests); `agents/deep_search_v4` 607 passed. ⚠ Inert until step 8 threads `editorial=True` — `planner/runner.py:401` still calls `build_retrieval_config(decision)`. **Still owed: a CLI run** on the operator's insurance question to see the article shape with intact `[n]` |
| 8 | ✅ **DONE 2026-09-02.** Request fields, planner pinning, direct write, retract, job-idempotency (156) | **Verified by a real pipeline run against prod** (`case_led`+`support`, `promissory-note`): one row · `blog_id == root_id` · `job_id` set · 15 refs cited, 0 dangling · identity masked («صدر أمر تنفيذ…» → «يواجه كثير من المدينين في السعودية») · H1 extracted to `title` · sitemap 0→1 with the two empty subjects absent · **`process_job` re-driven on the completed job published nothing new** · dispatcher verified on all 7 shapes incl. a real legacy token · **retraction verified**: delisted from gallery + both sitemaps, page still 200, `noindex` appears. ⚠ **Still owed**: an unpinned job (phase 1 running, and the `ask_user`→`_default_decision` path), and a re-run confirming `##` headings now that the §6 example carries the literal marker |
| 9 | ✅ **DONE 2026-09-02.** Gate dropped from `POST /blogs/{id}/publish`, `can_publish_public` gone from the response + types + UI, `user_can_access_blog` deleted, column left dormant | ⚠ The toggle was in `app/blogs/[token]/page.tsx`, **not** `MyBlogsGrid.tsx` as this manifest said. `MyBlogsGrid` lost the two things step 9 *does* invalidate: the عام/خاص dot (it reads a now-unwritable flag, so it could only ever say «خاص») and the empty-state promise «ثم يمكنك نشرها في المدونة العامة» |

Steps 1–7 are inert without content: the wing is empty today, so nothing user-visible changes
until step 8 publishes the first blog. The surface goes up cold, then marketing fills it.

⚠ Step 4 is the only irreversible-feeling one. The route rename plus ISR/CDN means a mistake
404s live share links. Verify token resolution against real prod tokens on a preview first.

[[marketing_agents]] picks up after step 8.

---

## 11. Traps

- **Railway deploys your dirty tree, and frontend root is `/frontend`** — a backend-only commit
  does not rebuild the frontend. Steps 2/3/7/8 are backend-only; 4/5/6 touch both.
  [[railway_master_pull_trap]]
- ⚠ **CORRECTED 2026-09-02 — this wing has no ISR, so neither of the two cache traps below
  applies as built.** All three blog routes are `force-dynamic`, inherited from the `[token]`
  route, which needs per-render execution for the `view_count` bump on the 99 legacy links.
  There is therefore no ISR entry for a blog slug: nothing to purge after a deploy, and nothing
  to purge after an SEO version either. The two traps are kept below because they become live
  the moment anyone moves this wing to ISR for performance — and the legacy view counter is what
  that move costs. See §12.9.
  - **ISR bake + CDN purge would be mandatory** or the page serves stale content that looks like
    a successful deploy. [[isr_bake_docker_cache_trap]]
  - **Percent-encoding on Arabic slugs** would break revalidation silently — a 200 that refreshes
    nothing. Encoding is already centralised in `lib/blog/slug.ts` for this reason.
    [[isr_revalidate_encoding]]
- **`cn()` strips custom `text-*` tiers** unless registered — `text-read` already is.
- **`response_model` strips undeclared keys.** Adding `is_public`/`type`/`subjects` to the
  service return without declaring them on the response model yields a silent `undefined` — and
  `robots` would then never go `noindex`. [[regulation_appendix_surface]]
- **Editing `agents/prompts/*.md` changes nothing** — it is a catalog.
  [[prompts_md_reference_only]]
- 🚨 **Never dedupe a publish on an LLM-derived value.** Found by the first live run
  (2026-09-02): one POST published **two** articles, 60s apart, because the only guard was
  `assert_slug_available` and the slug comes from the aggregator's headline. A re-drive
  regenerates a *different* headline → different slug → the uniqueness check passes → a second
  article. Dedupe belongs on the **job**, in the database: migration 156's
  `idx_public_blogs_job`. And the job check must be asked **before** the slug check — the
  same-headline re-drive otherwise 409s a job against the article it published itself, reporting
  failure over a live URL. `test_the_job_check_is_asked_BEFORE_the_slug_check` pins the order,
  because swapping the two leaves almost every other test passing.
- **Do not renumber citations to close gaps.** [[writer_reference_numbering]]
- **`support` must be nullable end to end.** `bool = False` anywhere on the path — the Pydantic
  request model, the job row, the service signature — collapses "planner decides" into "pinned
  off" with no error and no log line. The bug is invisible: every unpinned job just quietly runs
  without its support executor and returns a thinner article. §5.
- **An unpinned headless run can reach `ask_user`.** There is no user. Convert the pause to
  `_default_decision` rather than letting it strand until `catchup_stuck_jobs` reaps it. §5.
- **Cloudflare 403s script user-agents** — a 403 when probing new routes with curl is the edge,
  not a broken deploy. Probe the Railway origin. [[cloudflare_blocks_script_user_agents]]
- **`LIBRARY_SITEMAP_INTERNAL_ONLY`** makes `/api/v1/public/library/sitemap/*` answer 404
  «القسم غير موجود» to external callers, indistinguishable from an unknown section by design.
  Verifying step 6 from a laptop, that 404 is expected — check through the frontend route.
- **Latin-numerals policy** — app chrome renders Latin digits only, ESLint-enforced. Subject
  counts and TOC badges are chrome, **not** carve-outs. [[latin_numerals_policy]]
- **Arabic labels are copied, never retyped.** [[arabic_predicate_retype]]

---

## 12. Open (defaults chosen — flag if wrong)

1. **Hub cap** — 12 subject cards, ranked by public-blog count then `sort_rank`, with
   «كل المواضيع» → `/blog/subjects`.
2. **`/blog/subjects` grouping** — with `type` off subjects (D3), there is no grouping key left;
   the index sorts by blog count. Called out because rev 1 grouped by type.
3. **Subject page ordering** — newest first. No usage-rank equivalent exists for blogs and this
   plan does not build one.
4. **The 99 `blog_posts`** — untouched: no slug, no subjects, now `noindex`, still reachable at
   their tokens. Backfilling them into `public_blogs` is a separate content decision.
5. ⚠ **`blog` AND `blog-subjects` both serve empty urlsets until step 8 publishes**, which §7's
   own "empty sections must not be listed" rule indicts. Measured 2026-09-02: `blog_posts`
   qualifying = 0, `public_blogs` = 0. Accepted deliberately — `blog` has *already* been listed
   and empty for months (that is finding #1 of this whole plan), so the table switch loses
   nothing, and steps 1–7 are inert by design. **Step 8's gate now includes: after the first
   publish, confirm both sections return a non-empty urlset.** If step 8 slips a long way,
   de-register `blog-subjects` rather than let a second empty file accrue.
6. **Subject-page `lastmod` is `None`.** `blog_subjects.updated_at` tracks label edits, not the
   listing's contents, and the honest value (newest blog on the page) costs a round-trip per
   subject. `sitemap_static_urls` has the same posture.
7. **The `blog_posts` publish/unpublish routes still exist and now flip a vestigial column.**
   Only the `can_access_blog` gate was removed (§8); the routes themselves are untouched, and
   the gallery no longer reads `blog_posts.is_public`. With the مدوناتي toggle gone they are
   unreachable from the UI, owner-scoped, and harmless — but they answer `success: true` while
   doing nothing observable. Left in place as out of scope; delete them the next time `blog.py`
   is opened.
12. ⚠ **Testing this wing locally on Windows: two encoding traps, neither a product bug.**
    Both cost real time on 2026-09-02 and both look like application failures.
    - **Do not send the Arabic question through `curl -d` in Git Bash.** The payload is
      transcoded to `?????` before curl sees it, and the pipeline then answers a question made
      of question marks — producing a plausible article on an unrelated topic, with no error
      anywhere. Submit with Python's `urllib` and explicit UTF-8 bytes instead
      (`scratchpad/submit_job.py`).
    - **Start the backend with `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`.** Otherwise Logfire's
      console span exporter dies on the first Arabic span (`UnicodeEncodeError: 'charmap'`) and
      **the log goes silent while the process keeps running** — which is how a duplicate publish
      went unrecorded and initially looked like it came from another machine.
10. **An empty `subjects` list is ALLOWED — decided 2026-09-02, revisit later.** §5 says an
    unknown subject slug is a 400 "never a silent drop", and reasons that an unfiled blog is
    invisible in the browse tree. Both hold, but requiring at least one subject today would block
    legitimate content: the vocabulary is **3** subjects against ~100 planned, so plenty of good
    articles simply have no shelf yet. Make it a 400 once the vocabulary is broad enough that an
    unfiled blog is an oversight rather than a gap in the taxonomy.
11. 🚨 **This plan was built in a working tree shared with other live Claude sessions.** Verified
    2026-09-02: `agents/tool_repository/fetch_article.py` (792 lines), `agents/simple_search/
    manual_search.py` and an additive field in `agents/deep_search_v4/planner/deps.py` were being
    rewritten concurrently under `.claude/plans/fetch_article_bm25_resolution.md` — nothing to do
    with this wing. Consequences for whoever commits: **`git add -A` would sweep up someone
    else's half-finished work**, and the residual `test_fetch_article.py` /
    `test_manual_search.py` failures belong to that effort, not to a stale baseline. Stage this
    wing's files explicitly. [[feedback_git_add_dirty_tree]]
9. ⚠ **The wing is `force-dynamic`, not ISR — a deliberate inheritance, but revisit before
   step 8 scales.** The `[token]` route needs per-render execution for the `view_count` bump on
   the 99 legacy links, and the new `[slug]` route inherited it. Consequences, both ways:
   *no* cache purge is needed on deploy or on an SEO version (§11), and *no* ISR performance or
   edge-caching benefit exists either — every blog read is a server render. Moving to ISR is the
   obvious optimization once the wing has traffic; the price is the legacy view counter, which
   would need to move to a separate beacon. Do not move it without also wiring
   revalidate-on-publish, or an SEO rewrite will ship to a page nobody re-renders.
8. ✅ **`view_count` carries forward across versions** — settled during the build. Migration 155
   originally inserted `0`, which would have let an SEO rewrite silently discard the readership
   the rewrite existed to grow, with nothing in the wing aggregating views across a root to
   recover it. A reader is reading the *blog*, not version 3 of it, so the count belongs to the
   logical article the way `slug` does. Probed: survives two consecutive rewrites.
