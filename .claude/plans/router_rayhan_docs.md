# Router ↔ Rayhan docs — the router answers questions about the product itself

**Status:** BUILT · migration 126 APPLIED · 15 rows live in Supabase · backend NOT yet deployed
**Date:** 2026-08-10

## The problem

The router already claimed a job it could not do. Its system prompt listed
«Questions about Rayhan and its functions» under *answer directly*, but it had
no grounded product knowledge — so it answered «كيف أستخدم ريحان؟»، «كم تكلفة
الاشتراك؟»، «هل بياناتي آمنة؟» from the model's own priors. For a product whose
whole pitch is *never assert what you did not retrieve*, that was the one
surface still doing exactly that. And the wrong answer here is a **pricing
claim** or a **data-protection claim** — the two kinds a company gets held to.

All of that content already existed and was carefully written: nine public
pages under `/about_us`, `/audiences`, `/vs-chatgpt`, `/pricing`, `/privacy`,
`/terms`, `/masking`, `/learn/*`. The router just couldn't see it.

## Why the router cannot simply read the pages

`backend/Dockerfile` copies `shared/`, `agents/`, `backend/` — **not**
`frontend/`. And the learn pages are not prose: `HowItWorksView`,
`WorkspaceView`, `DataProtectionView`, `UsageLimitsView` carry their copy
inside `const CARDS = [...]` arrays interleaved with Tailwind. Only `/terms`,
`/privacy`, `/masking` had real md (`frontend/content/legal/*.md`).

## Shape

**`public.product_docs` is the whole source of truth** — owner decision:
the content lives in Supabase, not in the repo. There is no seeder script and
no markdown mirror. Rows are edited in the Supabase console and the router
picks the change up within one cache TTL (10 min).

> ⚠ **The rows are the only copy.** Nothing in the repo can regenerate them.
> An accidental `DELETE` is content loss, not a re-run. If that ever bites,
> the fix is a `pg_dump` of this one table into the repo — not a seeder.

Two tools, per the owner's framing:

| Tool | Catalog | Serves |
|---|---|---|
| `open_rayhan_page` | `about` | what Rayhan is, who it's for, vs ChatGPT, plans, privacy, terms, masking, data protection |
| `open_rayhan_guide` | `guide` | agents, workspace, library, usage limits, step-by-step guide, best practices, example questions |

**The doc keys are a code-level `Literal`, the bodies are in the DB.** The enum
lands in each tool's JSON schema, so the model sees the full catalog — every
doc with a one-line description — without a single token being added to the
router's system prompt, which is the cached prefix on every turn of every
conversation. It also makes a hallucinated key structurally impossible.

⚠ Renaming a `doc_key` in the DB without renaming it in
`agents/tool_repository/rayhan_docs.py` removes the doc from the model's reach
*silently*: the tool keeps advertising the key and the lookup finds nothing.
Same for `catalog` — a row catalogued `about` is unreachable from
`open_rayhan_guide` no matter what its key says.

### The 15 live rows

`about` — `about`, `audiences`, `vs_chatgpt`, `pricing`, `privacy`, `terms`,
`masking`, `data_protection`.

`guide` — `how_it_works`, `workspace`, `library`, `usage_limits`, `guide`,
`best_practices`, `examples`.

The last three have **no page behind them** — `/learn/guide`,
`/learn/best-practices`, `/learn/examples` are `enabled: false` in
`site-nav.ts` and were never built. Owner decision: author them anyway. Their
rows carry `canonical_path = NULL`, which is what stops the router offering the
user a 404, and each body opens with a line telling the router to teach the
content rather than link it. When those lessons ship, these rows are the draft
— set the path then.

### Legal docs went in verbatim

`privacy` / `terms` / `masking` are binding text, so they were copied
byte-for-byte out of `frontend/content/legal/*.md` rather than re-typed — the
router must quote the same words the pages serve. Largest is the terms at
8,091 chars, well under the 18,000-char return cap, so nothing is ever
truncated mid-clause. **If a legal page is edited, update the row too** — that
link is now a manual one.

## Numbers: what the docs may and may not state

Owner decision — **the docs explain the model, `/pricing` states the amounts.**
The `pricing` row describes what a نقطة is and how the windows work but carries
**no SAR figures**, and opens with an instruction telling the router to refer
to `/pricing` for prices. Amounts live in `plans.price_sar` (what checkout
charges) and `frontend/lib/pricing.ts` (display); those two are pinned by hand
and have drifted once. A third copy would be the one nobody updates — and the
router would quote a dead price to a paying customer with total confidence.

The per-operation point costs (≈0.1 router · ≈1 writing · 3–5 deep search) and
the per-plan allowances DO appear: they are measured from the `llm_calls`
ledger and stored in `plans`, the same numbers `/learn/usage-limits` publishes.
**If that page is re-measured, update the `usage_limits` row.**

## The prompt contract

`SYSTEM_PROMPT` gained one section — *questions about ريحان itself* — plus
three edits:

- Scope check #2 got an explicit carve-out, or it fires on «كيف أستخدم ريحان؟»
  and declines it as outside the Saudi legal domain.
- The answer-directly bullet now says *open the document first*.
- General rules gained the product-facts twin of the legal-content rule: no
  price, allowance, feature, data-handling claim or corpus number unless it
  came out of a doc opened this turn.

Also stated: never dispatch these to a specialist (deep_search searches Saudi
law, not Rayhan's docs), and the user's own balance is not in any doc.

## Non-goals

- **The user's own balance.** «كم بقي لي من النقاط؟» is per-user state behind
  `get_user_quota_state`. The prompt tells the router to point at the in-app
  usage indicator rather than guess. A quota tool is separate work.
- Frontend changes. The pages are untouched; the docs were distilled *from*
  them. That is a real two-surface cost — a page rewrite needs a row edit.

## Files

| File | Change |
|---|---|
| `shared/db/migrations/126_product_docs.sql` | new — table, RLS, updated_at trigger. **APPLIED** |
| `agents/tool_repository/rayhan_docs.py` | new — both tools, TTL cache, render/fetch |
| `agents/router/router.py` | register tools + prompt section + scope carve-out |
| `agents/tool_repository/tests/test_rayhan_docs.py` | new — 15 tests, all passing |

## Verification done

- All 15 keys resolve against live Supabase (`open_doc` end-to-end, service-role client).
- `best_practices` renders with no `rayhanai.com` link — the NULL-path contract holds.
- `agents/router/tests` + `agents/tool_repository/tests`: 127 passed. The two
  failures in `test_agent.py` (`test_inject_case_context_with_case_memory`,
  `test_inject_user_preferences_with_prefs`) are **pre-existing** — they assert
  Arabic strings against injectors that have been English since before this
  work; confirmed against `HEAD`.

## Still to do

1. Deploy the backend (the table and rows are already live, so deploy order
   doesn't matter here — the docs are waiting for the code).
2. Live-validate in a real conversation: ask «كم سعر الاشتراك؟» and confirm the
   router opens `pricing`, quotes no figure, and links `/pricing`.
3. Fix or delete the two stale router tests.
