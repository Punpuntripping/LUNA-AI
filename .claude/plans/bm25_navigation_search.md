# Shared BM25 Navigation Search — Build Plan

> **Goal:** one search structure — one index, one ranking function, one frontend
> component — serving every navigation surface in the app. Real BM25 with proper
> IDF, Arabic-normalized, replacing the per-hub single-column `ILIKE '%q%'` that
> exists today.
>
> **Surfaces (locked):** `/regulations` · `/judgments` · `/circulars` ·
> `/compliance` · `/library` (new cross-wing) · `/blogs` · `/blogs/mine` ·
> ~~`/templates`~~ · `/templates/mine` · `/library/mine`
>
> **Corrected 2026-08-01: `/templates` is NOT a surface.** It renders a
> «اختر قالبًا» chooser (the master/detail landing beside `/templates/[id]`),
> not `MyTemplatesGrid`. Only `/templates/mine` gets a box. Nine surfaces, not ten.
>
> **Live-verified 2026-08-01** on project `dwgghvxogtwyaxmbgjod` (PostgreSQL 17.6).

---

## 0. Locked decisions (Q&A, 2026-08-01)

| # | Decision |
|---|----------|
| **D1** | **Real BM25, hand-rolled.** One unified `search_index` table + `bm25_terms` (IDF) + `bm25_corpus_stats` (N, avgdl) + one `bm25_search()` RPC with tunable `k1`/`b`. No new extension. `pg_search` (ParadeDB) is **not** in the Supabase catalog — verified. |
| **D2** | ~~Two submit modes~~ → **ONE mode, superseded by D9.** The `mode="form"` GET-`<form>` variant existed so anon `?q=` responses would be crawl-safe and ISR-cacheable. Registered-only search means no anon `?q=` response is ever cached or indexed, so every surface uses the live 250 ms-debounced `/chats` pattern. `<SearchBar>` takes no `mode` prop. |
| **D3** | **Index only what the card already prints.** `search_doc` = title + facets + the **always-free lead** — the text the anon hub card and doc page already publish. No gated body text enters the index, so there is no snippet gate, no `ts_headline`, and no per-hit access-tier resolution: cards keep the static free snippet they render today. A leak stops being a code path to keep correct and becomes structurally impossible. Also the precision win — measured 2026-08-01, the free lead is **245 avg chars/judgment vs 1,166** for the summary-column body, so incidental matches drop ~4.8× on the largest corpus. |
| **D4** | **`/services` means `/compliance`.** No new route. The `services` table (4,717 rows, الإجراءات الحكومية) backs the existing `/compliance` wing. |
| **D5** | **`/library` becomes a real cross-wing search page**, replacing the `ComingSoonHub` placeholder. One box over أنظمة + أحكام + تعاميم + خدمات. |
| **D6** | **مواد are NOT indexed as their own results.** The 51,791 `regulation_v2.articles` rows stay out of the index; only the 3,373 parent أنظمة are searchable on `/regulations`. Revisit after Wave F calibration. |
| **D7** | **`/forms` and `/calculators` are out of scope.** Not wired, not indexed. |
| **D8** | **BM25 replaces `ILIKE` behind the existing `q` param.** The public hub query-string contract (`?q=…`, 3-char floor, 400 on shorter) does not change — only what it does. No frontend URL churn. |
| **D9** | **Search is registered-only. Anon sees the box, and clicking it converts.** Added 2026-08-01, and it makes the plan smaller — see §0.1. Anon callers get the search input rendered and enabled, but focus/click opens a CTA modal instead of searching. Enforcement is server-side: `q` is ignored for anon on the hub endpoints and `/api/v1/public/search` requires auth. An anon visitor arriving on a shared `?q=` URL gets the unfiltered hub page 1 + the CTA, and the URL is `noindex`. |

### 0.1 What D9 removes

Gating search to registered users deletes work rather than adding it:

| Was | Now |
|---|---|
| Trap #8 — ISR cache keys churn on `?q=` URLs, purge after Wave C | **Gone.** No anon `?q=` response is ever baked. |
| D2's two-mode `SearchBar` (`form` + `live`) | **One mode** (`live`). |
| §5.4 — "a crawler that can search is a crawler that can enumerate" | **Needs an account.** Every search hit lands on an item budget tied to a real user; the filter-dimension bypass in `navigation_enumeration_defence.md` now requires an authenticated attacker. |
| §9 — "public hub search URLs stay crawlable" | **Inverted:** `?q=` URLs are `noindex`. Indexed internal-search pages are thin near-duplicates that burn crawl budget; not indexing them is the correct SEO posture, not a concession. |

**Timing argument.** Public search currently ranks over ~100 rows per wing (the §2
slug ceiling, verified live 2026-08-01). Anon search over 100 of 3,373 أنظمة would
look broken — and would look broken to Google. Gating buys the backfill time;
`refresh_search_index()` picks up new slugs nightly with no code change.

**The CTA modal.** Reuses `anon_conversion_popup.md`'s `?next=` carrier (the single
return-to-page mechanism across email login, Google OAuth and email verification)
and `lib/anon-cta/copy.ts`. It does **not** reuse that plan's scroll-and-dwell
trigger — that one is document-only and explicitly excludes hubs, because a
directory grid has no reading depth. A search-box click is an intent gesture and
gets its own click trigger. No interstitial/SEO risk: like the scroll popup, it
fires on a gesture Googlebot never performs — no user-agent branch, no cloaking.
Lead with «اسأل ريحان» (a reader reaching for search has a *question*, and chat is
the product's answer to it), with a second line naming what an account unlocks —
otherwise it reads as a bait-and-switch to someone who wanted to filter a list.

---

## 1. Live-verified starting state

### 1.1 What exists

| Surface | Search today | Backing table | Rows |
|---|---|---|---|
| `/regulations` | `q` → `ilike(clean_title)`, **no UI** | `regulation_v2.regulations` | 3,373 |
| `/judgments` | `q` → `ilike(short_summary)`, **has UI** (`JudgmentsFilterBar`) | `cases` | 30,531 |
| `/circulars` | `q` → `ilike(title)`, **no UI** | `circulars` | 1,843 |
| `/compliance` | `q` → `ilike(service_name_ar)`, **no UI** | `services` | 4,717 |
| `/library` | — (`ComingSoonHub` placeholder) | — | — |
| `/library/mine` | none | `library_items` → corpus rows | per-user |
| `/blogs`, `/blogs/mine` | none | `blog_posts` | 100 live |
| `/templates`, `/templates/mine` | none | `user_templates` | 5 live |
| `/chats` (reference model) | debounced `ILIKE` over `title_ar` + `messages.content` | — | — |

### 1.2 Extension reality

| Extension | Status | Verdict |
|---|---|---|
| `pg_search` / ParadeDB | **absent from catalog** | BM25 must be built |
| `pgroonga` 3.2.5 | available, uninstalled | rejected (D1) |
| `rum` 1.3 | available, uninstalled | optional Wave F perf lever |
| `pg_trgm` 1.6 | **installed** | keep for fuzzy/typo fallback |
| `pg_cron` 1.6.4 | **installed** | stats + static-corpus refresh |

### 1.3 The Arabic text situation

`to_tsvector('arabic', …)` stems usefully — verified:

```
نظام العمل السعودي والمادة الخامسة من اللائحة التنفيذية للأنظمة
  arabic → 'نظام':1 'عمل':2 'سعود':3 'والماد':4 'خامس':5 'من':6 'لايح':7 'تنفيذ':8 'انظم':9
  simple → 'نظام' 'العمل' 'السعودي' 'والمادة' 'الخامسة' 'من' 'اللائحة' 'التنفيذية' 'للأنظمة'
```

Two gaps the snowball stemmer does **not** close:
1. **No Arabic stopwords.** `من` survives. So does the `و` conjunction glued to a word (`والمادة` → `والماد`, not `ماد`).
2. **No orthographic normalization.** `أ/إ/آ/ا`, `ة/ه`, `ى/ي`, tatweel `ـ`, and harakat are all distinct lexemes. A reader typing `الايجار` will not match a document holding `الإيجار`.

Both are fixed in §3.

### 1.4 Existing `fts` columns are NOT reusable

| Column | How built | Problem |
|---|---|---|
| `circulars.fts` | generated, `to_tsvector('simple', title \|\| content)` | **Config mismatch.** `hybrid_search_*` queries it with `websearch_to_tsquery('arabic', …)`. A query stemmed to `عمل` can never match a `simple` vector holding `العمل`. Circular FTS is effectively broken today for any stemmed term. |
| `cases.fts` | populated by ingest, config unrecorded | **Polluted.** Sampled content includes `details_url` paths and bare numeric ids: `'/ar/judicialdecisionslist/1/gofcc6ol…':23`, `'1437016659':152`. Noise that BM25 would happily rank on. |
| `services.fts` | populated by ingest, plain column | unverified provenance |

→ **Build `search_index.search_doc` fresh.** Do not read, copy, or alias the existing `fts` columns. They stay owned by the agent retrieval path (`hybrid_search_*`) and are untouched by this plan.

---

## 2. ⚠ BLOCKING PREREQUISITE — the slug backfill stalled at 100/wing

`seo_item_meta` is the slug sidecar, and `_slug_map()` (`library_service.py:1199`)
documents the rule: **"a hub lists only slugged (published) items."**

Verified counts:

| content_type | sidecar rows | **rows with a slug** | last written |
|---|---|---|---|
| `service` | 4,717 | **100** | 2026-07-23 15:35 |
| `regulation` | 3,373 | **100** (54 of them `seo_tier='open'`) | 2026-07-23 15:35 |
| `circular` | 1,843 | **100** | 2026-07-23 15:35 |
| `judgment` | 100 | **100** | 2026-07-25 18:14 |
| `article` | 5 | 5 | 2026-07-23 16:57 |

The backfill ran on 2026-07-23, wrote exactly 100 rows per wing, and stopped.

**Consequence for this plan:** BM25 over 3,373 أنظمة is pointless when only 100
have a reachable URL. Public-hub search returns *slugged rows only* — so until the
backfill completes, `/regulations` search ranks over 100 documents, not 3,373.

**Two ways forward, and this needs an answer before Wave C ships:**
- **(a)** Finish the slug backfill first, then index. Public search is real on day one.
- **(b)** Ship Waves A–B + D–E now (private surfaces and cross-wing search over
  whatever is slugged), and let public-hub recall grow as the backfill lands.
  `refresh_search_index()` picks up new slugs on its next run with no code change.

Waves A, B, D are unaffected either way — the private corpora (blogs, templates,
shelf) carry no slug dependency. **Recommendation: (b)**, because it unblocks 5 of
the 10 surfaces immediately and the index self-heals.

---

## 3. The Arabic text pipeline

### 3.1 `luna_normalize_ar(text) → text`

`IMMUTABLE PARALLEL SAFE` — it is used in a generated/indexed expression, so it
must never be redefined in place without reindexing.

**Rewritten 2026-08-01 after measuring against the live stemmer. The draft had
two bugs; both are fixed in `111_bm25_search_index.sql`.**

```sql
create or replace function public.luna_normalize_ar(t text)
returns text language sql immutable parallel safe as $$
  select regexp_replace(
    translate(
      -- U+0640 tatweel + U+064B-U+0655 harakat + U+0670 superscript alef.
      -- Stops short of U+0660 (Arabic-Indic zero) -- see bug 1.
      regexp_replace(coalesce(t, ''), '[ـً-ٕ]', '', 'g'),
      'أإآٱ', 'اااا'),   -- hamza-carrying alef only -- see bug 2
    '\s+', ' ', 'g');
$$;
```

**Bug 1 - the draft class ate Arabic-Indic digits.** It spanned U+064B-U+0670,
which contains U+0660-U+0669 (the digits). Measured: a test string lost both its
Hijri year and its percentage. Hijri years and judgment numbers are load-bearing
here, so this would have been quiet, wide data loss.

**Bug 2 - folding taa-marbuta to haa, and alef-maqsura to yaa, makes retrieval
WORSE and both are dropped.** The snowball stemmer already conflates taa-marbuta
and haa on its own, and pre-folding *breaks* its suffix stripping:

| word | stemmer alone | with the draft's fold |
|---|---|---|
| مكتبة / مكتبه | both مكتب | مكتب - no gain |
| اللائحة | لايح | لايحه (worse) |
| الخامسة | خامس | خامسه (worse) |
| الأمومة | اموم | امومه (worse) |
| على | علي | عل (worse) |

Hamza folding alone still satisfies the success criterion: الإيجار and
الايجار both stem to ايجار.

**Residual gap (accepted, Wave F).** The stemmer strips taa-marbuta always, and
a bare word's final haa, but not both together: المحكمة stems to
محكم while the dotless المحكمه stems to محكمه - so the
dotless spelling misses. Every cheap fix is worse: a global fold degrades all
stems, and rewriting a trailing haa on al-prefixed words corrupts real legal
vocabulary (الفقه, الله). Route it through `pg_trgm` (§1.2) as a
no-results fallback instead. **Confirmed end-to-end on the live API 2026-08-01:**
`?q=إجازة` returns نظام العمل, `?q=الاجازه` (dotless ه) returns nothing. A
reader who types the dotless spelling — common informal Arabic — gets a blank
page today. Wave F should treat this as user-facing, not theoretical.

### 3.2 Text search configuration

```sql
create text search configuration public.arabic_luna ( copy = arabic );
```

**TRAP — no stopword file.** The textbook fix is a `simple` dictionary with a
stopwords file in `$SHAREDIR/tsearch_data`. **Managed Supabase gives no
filesystem access**, so that path is closed. Strip stopwords with `ts_delete`
after vector construction instead:

```sql
create or replace function public.luna_tsvector(t text, weight "char")
returns tsvector language sql immutable parallel safe as $$
  select setweight(
    ts_delete(
      to_tsvector('public.arabic_luna', public.luna_normalize_ar(t)),
      ARRAY['الت','التي','الذ','الي','ان','انه','او','بعد','به','بها',
            'بين','ثم','حيث','ذلك','علي','عن','عند','في','فيه','قبل',
            'قد','كان','كل','كما','لا','له','لها','ما','مع','من',
            'هذا','هذه','وه','وهو']
    ), weight);
$$;
```

The stopword array holds **post-stem, post-normalization** lexemes, and was
GENERATED by running the stemmer over a candidate list - per the trap below. The
draft version was hand-written and wrong: it listed the surface forms of the two
relative pronouns, whose real stems are one character shorter, so neither was
ever actually removed.

Deliberately NOT stopworded despite being frequent: the words for "other-than"
and "same/possessing". The first is semantically load-bearing in legal Arabic
(non-compliant, non-statutory), and BM25's IDF already down-weights frequent
terms without discarding them outright.

### 3.3 `search_doc` weighting

| Weight | Field | Why |
|---|---|---|
| `A` | title | the thing the card shows |
| `B` | facets-as-text (entity name, court, provider, doc type, sectors, domains, refs) | a reader searching «وزارة التجارة» means the issuer |
| `D` | **free lead** (§4.5) — never gated body | topical recall: no regulation *title* contains «إجازة الأمومة». Length-normalized by BM25's `b` |

Field boost is applied **inside TF**, not as a post-multiplier — an `A`-weighted
occurrence counts `title_boost` times toward term frequency (§4.3), which is what
makes it interact correctly with saturation (`k1`).

**Why a lead and not the full body.** Search on these hubs is two different jobs.
*Known-item* («نظام العمل» — I want that document) is served by the title, and §4.3's
exact-title bonus makes it deterministic. *Topical* («إجازة الأمومة») needs prose
beyond the title, which is why title-only is not enough — it would be the `ilike`
we already ship, plus IDF. The free lead is the smallest text that does the second
job, and every extra character past it is pure incidental-match surface for a
common term like «نظام العمل».

---

## 4. Database layer (migration `109_bm25_search_index.sql`)

### 4.1 `public.search_index`

```sql
create table public.search_index (
  id            uuid primary key default gen_random_uuid(),
  corpus        text not null
                check (corpus in ('regulation','judgment','circular',
                                  'service','blog','template')),
  content_id    text not null,
  owner_user_id uuid references public.users(user_id) on delete cascade,
  slug          text,                       -- public URL segment; null for private corpora
  title         text not null default '',
  lead          text not null default '',   -- ALWAYS-FREE text only (§4.5). Never a gated column.
  facets        jsonb not null default '{}'::jsonb,
  search_doc    tsvector not null,
  doc_len       integer not null,           -- weighted token count (§4.3)
  updated_at    timestamptz not null default now()
);

-- PG 15+ NULLS NOT DISTINCT: one row per (corpus, content_id) for public
-- corpora (owner_user_id IS NULL) without a sentinel uuid. Verified PG 17.6.
create unique index search_index_identity
  on public.search_index (corpus, content_id, owner_user_id) nulls not distinct;

create index search_index_doc_gin on public.search_index using gin (search_doc);
create index search_index_corpus  on public.search_index (corpus)
  where owner_user_id is null;
create index search_index_owner   on public.search_index (owner_user_id, corpus)
  where owner_user_id is not null;
create index search_index_facets  on public.search_index using gin (facets jsonb_path_ops);
```

**`doc_len` is weighted token count, not `length(search_doc)`.** `length()` counts
*distinct* lexemes and would make a repetitive document look short, inverting the
length normalization BM25 exists to provide:

```sql
select sum(coalesce(array_length(positions, 1), 1))::int
from unnest(search_doc);
```

### 4.2 IDF statistics

```sql
create table public.bm25_terms (
  corpus   text    not null,
  lexeme   text    not null,
  doc_freq integer not null,
  primary key (corpus, lexeme)
);

create table public.bm25_corpus_stats (
  corpus      text primary key,
  doc_count   integer not null,
  avg_doc_len numeric not null,
  computed_at timestamptz not null default now()
);
```

`refresh_bm25_stats(p_corpus text)` rebuilds both from `ts_stat`:

```sql
execute format(
  'insert into public.bm25_terms (corpus, lexeme, doc_freq)
   select %L, word, ndoc
   from ts_stat(%L)
   on conflict (corpus, lexeme) do update set doc_freq = excluded.doc_freq',
  p_corpus,
  'select search_doc from public.search_index where corpus = '
    || quote_literal(p_corpus)
);
```

`ts_stat` takes a **SQL string**, so `quote_literal` on the corpus name is
mandatory — this is the one injection seam in the whole migration.

### 4.3 `public.bm25_search()` — the one ranking function

```sql
create or replace function public.bm25_search(
  p_corpora     text[],
  p_query       text,
  p_owner       uuid    default null,
  p_facets      jsonb   default '{}'::jsonb,
  p_limit       integer default 20,
  p_offset      integer default 0,
  p_candidates  integer default 500,
  p_k1          numeric default 1.2,
  p_b           numeric default 0.75,
  p_title_boost numeric default 3.0,
  p_exact_bonus numeric default 1000.0
) returns table (
  corpus text, content_id text, slug text, title text,
  facets jsonb, score numeric, total_count bigint
)
```

Two-stage, because a common Arabic term can match 20k+ أحكام:

1. **Candidate stage** — GIN `@@` match, narrowed by corpus/owner/facets, ordered
   by `ts_rank_cd` and cut to `p_candidates` (default 500).
2. **Rescore stage** — exact BM25 over those candidates:

```
score = Σ_t  ln(1 + (N - df_t + 0.5) / (df_t + 0.5))
             ·  (tf_t · (k1 + 1)) / (tf_t + k1 · (1 - b + b · dl/avgdl))

tf_t  = (count of 'A'-weighted positions) · title_boost
      + (count of non-'A' positions)
```

TF per term comes straight out of the vector — `unnest(tsvector)` yields
`(lexeme, positions smallint[], weights text[])`, and `weights` is positionally
parallel to `positions`, which is what makes the weighted TF exact rather than
estimated.

**Exact-title bonus.** BM25 ranks «نظام العمل» above the لوائح that merely cite it,
but only *probably* — it depends on `title_boost` beating the `D`-weight mass of
however many summaries mention the term. Known-item lookup should not be
probabilistic, so add a flat bonus when the normalized query equals the normalized
title:

```sql
+ case when public.luna_normalize_ar(p_query) = public.luna_normalize_ar(title)
       then p_exact_bonus else 0 end
```

with a supporting b-tree index:

```sql
create index search_index_title_norm
  on public.search_index (public.luna_normalize_ar(title));
```

`p_exact_bonus` is set far above any achievable BM25 score so the pin is absolute,
not a thumb on the scale. This is the whole answer to «نظام العمل» returning noise:
the named document is rank 1 by construction, and everything BM25 does happens
below it. Same `IMMUTABLE` constraint as trap #4 — this index depends on
`luna_normalize_ar` too.

The `p_candidates` cut is a **recall/latency trade-off and must be logged**, not
silent: a query matching 8,000 judgments is BM25-scored over the top 500 by
`ts_rank_cd`. Wave F calibrates the number against a query set.

### 4.4 RLS

```sql
alter table public.search_index enable row level security;

create policy search_index_public_read on public.search_index
  for select using (owner_user_id is null);

create policy search_index_owner_read on public.search_index
  for select using (
    owner_user_id = (select user_id from public.users where auth_id = auth.uid())
  );
```

Writes are service-role only (no `for insert/update/delete` policy). The backend
additionally scopes every private query by `user_id` in the service layer — RLS is
the backstop, not the only guard.

### 4.5 Corpus registry — what lands in the index

**A0 audit complete — 2026-08-01.** All four public corpora verified against their
anon doc-page payload builders.

| corpus | source | `content_id` | title (A) | facets (B) | **lead (D)** | free? | slug |
|---|---|---|---|---|---|---|---|
| `regulation` | `regulation_v2.regulations` | `id` | `clean_title` ?? `title` | `entity_name`, `doc_type_bucket`, `sectors`, `status_class`, `reg_ref` | `llm_summary` ?? `summary` | ✅ **full** — shipped unconditionally as `summary_md` (`:2205`), outside the gate | `seo_item_meta` |
| `judgment` | `cases` | `id` | `judgment_display_title(row)` | `court`, `court_level`, `city`, `legal_domains`, `case_number` | `short_summary` | ✅ **full** — `summary_md` (`:4266`) | `seo_item_meta` |
| `circular` | `circulars` | `id` | `title` | `entity_ref`, `doc_type`, `sectors`, `circ_ref` | **first 400 chars of `content`, trailing partial word stripped** | ⚠ **partial only** | `seo_item_meta` |
| `service` | `services` | `id` | `service_name_ar` | `provider_name`, `sectors` | `intro_description` + `requirements` + `required_documents` + `steps` | ✅ **full** — services never gate (`:2425`, `:477`) | `seo_item_meta` |
| `blog` | `blog_posts` | `post_id` | `title` ?? `question_text` | `subtype`, `display_mode`, `is_public`, `is_imported` | `content_md` (full) | owner-only corpus | `token` |
| `template` | `user_templates` | `template_id` | `title` | `created_by` | `content_md` (full) | owner-only corpus | `template_id` |

**`circular` is the only partial one.** `content` runs through
`truncate_for_gate(content, effective_circular_gate(gate, len), free_chars=400)`
(`:3127`). `GATE_FREE_CHARS_DEFAULT = 400`; a body `<= CIRCULAR_FREE_LENGTH` (800)
downgrades to open and ships whole. Rather than replicate `resolve_gate` per row in
SQL, index the **safe floor that holds for every circular regardless of gate**:

```sql
regexp_replace(left(coalesce(content,''), 400), '\S*$', '')
```

This is byte-identical to `truncate_for_gate`'s whitespace cut for a gated row, and
a strict subset of what an open row publishes. The trailing-`\S*` strip matters —
plain `left(…, 400)` would pull the first few characters of the next (gated) word
into the index as a matchable fragment.

**`services.service_context` is excluded.** It appeared in the pre-audit draft, but
it is in neither the doc payload (`:2427`) nor the hub payload (`:2361`) — unpublished,
so same rule as `facts`/`reasoning`/`ruling` below.

**The `judgment` title is NOT court + case number.** `judgment_display_title()`
(`shared/seo/judgment_naming.py:204`) walks `short_summary → summary → facts →
ruling` for the first meaningful line, clause-truncates it, and appends court +
Hijri year: «نزاع تجاري حول عقد توريد مستلزمات طبية — المحكمة التجارية 1445هـ».
Present on 29.5k of 30.5k rows. The court+number form is the *slug* discriminator
(`_stable_ref`, `:226`) — a different function. A judgment title is therefore
already a one-line subject summary, which is most of why title+lead is enough here.

**`cases.content` is NOT indexed.** The full ruling averages 8,538 chars and is
gated after `JUDGMENT_FREE_CHARS`. Neither are `facts`/`reasoning`/`ruling`: per
`_JUDGMENT_DOC_SELECT` (`library_service.py:3407`) those are internal summaries
that the doc page **never publishes at all**, so they have no unlock path — text
with no published home must not become a search excerpt.

**Excluded on purpose:** `cases.details_url`, `cases.referenced_regulations`,
`circulars.content_path/content_hash`, every `*_url` column, every numeric id.
That is exactly the junk polluting `cases.fts` (§1.4) and it must not be
re-imported.

Public corpora are inserted **only where a slug exists** — matching `_slug_map`'s
published-item rule (§2).

### 4.6 Sync

| corpus | mechanism |
|---|---|
| `regulation`, `judgment`, `circular`, `service` | `refresh_search_index(corpus)` — full upsert from source. Run after ingest, plus a nightly `pg_cron` job. Picks up new slugs automatically. |
| `blog`, `template` | `AFTER INSERT/UPDATE/DELETE` trigger → single-row upsert/delete. 105 live rows total; trigger cost is noise. Soft deletes (`deleted_at`) must **delete** the index row, not update it. |
| `bm25_terms` / `bm25_corpus_stats` | nightly `pg_cron` per corpus. IDF drift from one new blog post is immaterial; a 30k-row judgment reingest is not. |

---

## 5. Backend layer

### 5.1 New files

| File | Contents |
|---|---|
| `backend/app/services/search_service.py` | `bm25_search()` RPC wrapper, query validation, facet whitelist per corpus. **No gating logic** — nothing gated is indexed (§5.3) |
| `backend/app/api/search.py` | `GET /api/v1/public/search` (cross-wing, anon-cacheable) · `GET /api/v1/search/mine` (authed, private corpora + shelf) |
| `backend/app/models/search.py` | `SearchHit`, `SearchResponse` |

### 5.2 Modified

| File | Change |
|---|---|
| `backend/app/services/library_service.py` | `_apply_reg_filters` / `_apply_*_filters`: when `q` is present, resolve ids via `bm25_search()` and order by score instead of `ilike`. `q`-absent paths untouched. |
| `backend/app/api/public_library.py` | `_search_text()` keeps the 3-char floor. Add `p_candidates` and the item-charge cap (§5.4). **D9: for an anon caller, drop `q` entirely and serve the unfiltered page — do not 400.** A shared search link must degrade to "here is the wing", not to an error. |
| `backend/app/api/library_mine.py` | `q` param on the shelf endpoint → BM25 over the shelf's joined corpus rows |
| `backend/app/api/blog.py`, `templates.py` | `q` param on the `mine` list endpoints |

### 5.3 Snippets — there is no gate

**Search returns no snippet field at all.** Each hub card already renders a static
excerpt from an always-free column, and it keeps doing exactly that:

| Surface | Existing card snippet | Source |
|---|---|---|
| `/judgments` | `snippet`, 160 chars, bullet-stripped | `short_summary` (`library_service.py:3636`) |
| `/regulations` | `summary_snippet`, 160 chars | `summary` (`:1542`) |
| `/circulars` | `body_snippet`, 160 chars | free lead of `content` (`:2683`) |

So `SearchHit` carries **no** `snippet` and **no** `match_in_body`. Deleted with
them: the per-hit `_find_unlock_row` lookup, the `seo_tier` check, `ts_headline`,
the `<mark>` sanitizer path, and «وُجدت مطابقة داخل النص».

Two reasons beyond the saved work:

1. **The gate was mostly unreachable anyway.** D2 makes public hub `?q=` responses
   anon and ISR-cached (trap #8), but §5.3-as-written keyed the snippet on
   `_find_unlock_row(user, …)` — per-user state. A shared cached response cannot
   hold a per-user excerpt. On the public hubs the `unlocked` branch would almost
   never fire; the apparatus would have existed to serve `snippet: null`.
2. **Where it *would* fire, it is unnecessary.** `blog`, `template` and
   `/library/mine` are owner-only, so "unlocked" is unconditional there.

**If highlighting is wanted later, do it client-side** over the free snippet string
the card already has — no backend gate, no leak surface, and it survives ISR
caching because it is a pure function of `?q=` and text already in the payload.

### 5.4 Metering

A search that yields 20 items charges 20 through `_charge_hub_yield` — same as any
hub page. But search adds a *filter dimension* on top of the page-depth cap, which
is exactly the enumeration hole `navigation_enumeration_defence.md` documents.

- `p_limit` for anon callers capped at the existing anon page size (9).
- Search results count toward `enforce_item_budget` identically to browse results.
- **No new exemption.** A crawler that can search is a crawler that can enumerate.

---

## 6. Frontend layer

### 6.1 New shared components

| File | Role |
|---|---|
| `components/search/SearchBar.tsx` | The one search input. **No `mode` prop** (D2 superseded by D9) — always a controlled input + 250 ms debounce, caller owns the state. Takes an optional `gate: { returnTo }`; present ⇒ anon sees a `readOnly` box whose click opens `SearchCtaModal`, absent ⇒ plain box that never subscribes to auth. One prop rather than two so a gated box cannot ship without a `returnTo`. |
| `components/search/SearchCtaModal.tsx` | The D9 anon conversion modal. Opens on `pointerdown`/keystroke, **never on `focus`** — Radix returns focus to the trigger on close, so a focus trigger reopens forever. |
| `components/search/SearchEmptyState.tsx` | «لا توجد نتائج» / «جرّب كلمات بحث أخرى» — shared by all 10 surfaces |
| ~~`components/search/SearchHighlight.tsx`~~ | **Dropped (§5.3).** Result cards reuse the hub card they already render, static free snippet included |
| `lib/search/copy.ts` | Every Arabic string, one file — same convention as `library/mine/copy.ts` |
| `hooks/use-search.ts` | TanStack Query hook for the live surfaces |

**RTL trap:** the two existing search boxes disagree. `ConversationSearch` puts the
icon at `end-2.5` with the clear button at `start-2`; `JudgmentsFilterBar` puts the
icon at `start-3`. `SearchBar` picks **one** — icon leading the text at `start-3`,
clear at `end-2.5` — and both callers adopt it.

### 6.2 Per-surface wiring

| Surface | Calls | Corpus | Notes |
|---|---|---|---|
| `/regulations` | wing list `?q=` | `regulation` | **new** filter bar (none today) |
| `/judgments` | wing list `?q=` | `judgment` | `JudgmentsFilterBar` refactors onto `SearchBar` — its court-level chips stay |
| `/circulars` | wing list `?q=` | `circular` | **new** filter bar |
| `/compliance` | wing list `?q=` | `service` | **new** filter bar |
| `/library` | `/api/v1/search` | all four | **new page** — replaces `ComingSoonHub`, wing chips + result list |
| `/library/mine` | live | shelf join | sits beside the existing `MyLibraryTabs` + `MyLibrarySortMenu` | **`q` REPLACES `sort` server-side** (counts stay whole-shelf), so the UI hides «الترتيب» while a search is live and labels the order «مرتّبة حسب مطابقة البحث» — a menu still reading «الأحدث» would name an order the server is not using.
| `/blogs`, `/blogs/mine` | live | `blog` (owner) | one box in `MyBlogsGrid` — both routes inherit it |
| `/templates/mine` | live | `template` (owner) | one box in `MyTemplatesGrid`. **`/templates` does NOT render that grid** — it is a chooser page, so it gets no box. |

**Which endpoint a surface calls — this was missing and the two candidates
disagree.** The four wings call **their own list endpoint with `?q=` + bearer**,
NOT `/api/v1/search`. Reason is D3: `SearchHit` carries no snippet, but §5.3
requires a result card to keep the static free excerpt it already shows while
browsing — and that excerpt (`summary_snippet` / `snippet` / `body_snippet` /
`intro_snippet`) exists only on the hub envelope. So hub search swaps `ilike` →
`bm25_search()` *inside* the existing endpoint and the payload is unchanged.
`/api/v1/search` exists for the Wave E cross-wing page, which renders its own
card and needs no per-wing excerpt.

`/blogs` and `/blogs/mine` inherit one box for free because both render
`MyBlogsGrid`. **That holds for blogs only** — verified 2026-08-01. The draft
assumed the same of `MyTemplatesGrid` and it is not the case: `/templates` is a
chooser page and never renders that grid, so only `/templates/mine` gets a box.

---

## 7. Waves

| Wave | Scope | Files | Depends on |
|---|---|---|---|
| **A0** | ~~Free-column audit~~ **DONE 2026-08-01** — results in §4.5. | — | — |
| **A** | DB foundation: `luna_normalize_ar`, `arabic_luna` config, `luna_tsvector`, `search_index`, `bm25_terms`, `bm25_corpus_stats`, `refresh_*`, `bm25_search()` incl. exact-title bonus + `search_index_title_norm`, RLS, triggers, `pg_cron` jobs. Backfill all six corpora. | `shared/db/migrations/111_bm25_search_index.sql` | A0 |
| **B** | Backend: `search_service.py`, `api/search.py`, `models/search.py`; swap the four hub `ilike` paths to BM25; `q` on the three private list endpoints. | 3 new, 5 modified | A |
| **C** | Public hubs: `SearchBar` + shared pieces; new filter bars for regulations/circulars/compliance; refactor `JudgmentsFilterBar`. | 5 new, 4 modified | B, §2 |
| **D** | Private surfaces: live search in `MyBlogsGrid`, `MyTemplatesGrid`, `MyLibraryPage`. | 1 new hook, 3 modified | B |
| **E** | `/library` cross-wing search page — replaces `ComingSoonHub`. | 2 new, 1 modified | C |
| **F** | Calibration: `k1`/`b`/`title_boost`/`p_exact_bonus`/`p_candidates` against a real Arabic query set; decide on `rum`; decide D6 (index مواد?); evaluate the recall escape hatch below. | tuning migration | C, D, E |

**Recall escape hatch (Wave F, only if needed).** If the free lead proves too thin
— a topical query that should hit returns nothing — the fix is **rank on more,
quote nothing**: add the summary columns (`facts`, `reasoning`, `ruling` for
judgments; ~1,166 avg chars vs 245) to `search_doc` for *ranking only*, leaving
`lead` and the card untouched. That recovers the original D3 recall without a single
line of gating, because nothing new is ever displayed. It costs precision — ~4.8×
the incidental-match surface on `cases` — so it is a measured response to a
demonstrated recall gap, not a default.

Waves **C** and **D** are independent of each other — D can ship first and is
unblocked by the slug situation entirely.

---

## 8. Traps

1. **Slug ceiling (§2).** Public search ranks over ~100 rows per wing until the
   backfill finishes. Not a code bug; will read as one.
2. **Do not reuse `circulars.fts` / `cases.fts` / `services.fts` (§1.4).** Wrong
   config, polluted content, owned by the agent path.
3. **No stopword file on managed Supabase.** `ts_delete` is the workaround (§3.2).
   Anyone "fixing" this by adding a dictionary file will find it works locally and
   fails on deploy.
4. **`luna_normalize_ar` is used in an indexed expression.** Redefining it silently
   corrupts the index — every change requires a full `refresh_search_index` +
   `REINDEX`. Mark it in the migration header.
5. **Stopword array holds post-stem lexemes.** `إلى` → `الي`. Generate the list by
   running the stemmer, never by hand.
6. **`doc_len` ≠ `length(search_doc)`** (§4.1). Getting this wrong inverts BM25's
   length normalization instead of disabling it, which is worse than not having it.
7. **`ts_stat` takes a SQL string** — `quote_literal` the corpus name (§4.2).
8. **ISR cache keys change.** Hub responses for `?q=…` are cached per filter
   combination; switching `ilike`→BM25 changes the payload for URLs already baked.
   Purge via `/api/revalidate` after Wave C, and **deploy backend before frontend** —
   the `isr_bake_docker_cache_trap` failure mode exactly.
9. **Soft deletes must remove the index row.** `blog_posts.deleted_at` /
   `user_templates.deleted_at` set → `DELETE FROM search_index`, not an upsert.
   Otherwise deleted templates keep surfacing in search.
10. **Search is an enumeration channel** (§5.4). Same item budget, same anon page
    size, no exemption.
11. **`nulls not distinct`** needs PG 15+. Confirmed 17.6 — but it is the one line
    that would break a downgrade or a self-hosted target running older Postgres,
    which matters for the KSA/Alibaba migration.
12. **Only always-free text may enter `lead`** (§4.5). This replaces the old
    `ts_headline` trap and is the single remaining leak path in the design. Verified
    for `judgment` only; `regulation`, `circular` and `service` are unverified and
    must be confirmed in Wave A before their backfill runs. A wrong column here is
    silent — the index ranks fine and publishes nothing visibly wrong, while the
    text is reachable by probing `?q=`.
13. **The exact-title bonus index depends on `luna_normalize_ar`** (§4.3), so it
    falls under trap #4 too: redefining that function invalidates `search_doc`
    *and* `search_index_title_norm`. Both need rebuilding, not just the vector.

---

## 9. Success criteria

> **Measured limitation, 2026-08-01 — read this before trusting a topical query.**
> An earlier draft of the criteria below claimed `?q=إجازة الأمومة` would return
> نظام العمل. **It does not, and that is correct behaviour, not a bug.** Verified
> against the live index:
>
> | | `اجاز` stem | `اموم` stem | both |
> |---|---|---|---|
> | نظام العمل | ✅ | ❌ | ❌ |
> | نظام التأمينات الاجتماعية | ❌ | ✅ | ❌ |
>
> No single indexed نظام carries both, so the AND tsquery correctly yields zero.
> The cause is what the lead *is*: `llm_summary` is a ~1,334-char executive
> summary («## النطاق — يسري هذا النظام على كل عقد عمل…»), not the statute. A
> specific entitlement like إجازة الأمومة lives in a **مادة**, and D6 keeps مواد
> out of the index.
>
> So state the capability honestly: **the lead buys topical recall at SUMMARY
> granularity, not article granularity.** Single salient terms resolve (`إجازة`,
> `تعويض`); a multi-term query naming a specific provision generally will not
> until either D6 is revisited or the Wave F escape hatch ships. This is now the
> strongest argument on the table for indexing مواد — stronger than anything in
> D6's original reasoning, which was about result-count noise rather than recall.

- [ ] One `bm25_search()` RPC serves all 10 surfaces; no second ranking path exists.
- [ ] `<SearchBar>` is the only search input in the app; `ConversationSearch` and
      `JudgmentsFilterBar`'s inline box are both gone or delegating.
- [ ] `الايجار` finds documents containing `الإيجار` (normalization works).
- [ ] `من` alone returns nothing (stopwords work).
- [ ] A rare term outranks a common one at equal frequency (IDF works).
- [ ] `/regulations?q=نظام العمل` puts نظام العمل itself at **rank 1**, above every
      لائحة تنفيذية that merely cites it (exact-title bonus works).
- [x] `/regulations?q=إجازة` returns نظام العمل — a topical term no regulation
      *title* contains, and the reason the lead is indexed at all. **Verified live
      2026-08-01** (`total_count=1`); `تعويض` returns 6 across different أنظمة.
      Title-only search would return nothing for either.
- [ ] **No search response contains any text not already present in the anon card
      or doc-page payload.** Diff the two; this is the leak test, and per-corpus.
- [ ] **Anon cannot search, server-side.** `?q=` on a hub is ignored for an anon
      caller (unfiltered page 1 + CTA, not a 400) and `/api/v1/public/search`
      rejects anon. Verified by calling the API directly, not by clicking the UI.
- [ ] Any `?q=` URL emits `noindex`; the box still renders for anon and its
      click opens the CTA modal carrying `?next=` back to the intended search.
- [ ] `/regulations?q=xx` (2 chars) still 400s in Arabic — contract unchanged.
- [ ] Search results charge the item budget identically to browse results.
- [ ] p95 latency < 300 ms on the 30,531-row judgment corpus.
