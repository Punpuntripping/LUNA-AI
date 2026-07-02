# Blog-Post Generation API — LUNA side implementation plan

Status: **PLAN (initial)**. Answers the marketing request in `.claude/plans/LUNA_API_REQUEST.md` and specs LUNA's build.
Scope: one **service-authed** endpoint that turns a legal question into a **private/unlisted `blog_posts` row** (`rayhanai.com/blog/<token>` link + answer + confidence), by driving the **same generation pipeline** an in-app question uses, then doing the **same share snapshot**. **Internal use only** for now (100/hr · 300/day). Each post is a token link sent **1:1 to a particular user** — never in the public `/blog` gallery.

---

## 1. The ask, restated

Marketing wants: `question` in → published `blog_posts` row out (URL + `content_md` + `confidence.label`), reachable from a single service call, grounded in **our** regulations corpus (internal `reg:` citations), idempotent, gated on a confidence label they read to decide whether to do outreach.

Today this only happens when a human sends a message (→ `agent_search`/`agent_writing` workspace_item) and clicks **مشاركة** (→ `POST /workspace/{item_id}/share` → `blog_posts` snapshot). We need that whole chain reachable **headlessly as a fixed editorial bot user**.

---

## 2. How it maps to our system — verified facts (corrects the request where needed)

Traced end-to-end during planning. Concrete anchors:

| Piece | Where | Note |
|---|---|---|
| Message → answer pipeline | `agents/orchestrator.py:1069` `handle_message()` (async generator) | Prereqs: internal `user_id`, **owned** `conversation_id`, a **pre-saved user `messages` row**, and a **non-null plan** to clear `quota.check`. |
| Route that drives it today | `backend/app/api/messages.py:47` → `message_service.send_message_stream()` (`message_service.py:353`) | SSE only; no non-streaming path. Does dedup + quota + save-user-msg + placeholder, then calls `handle_message`. |
| Where the WI is written | both publishers → `create_workspace_item()` `backend/app/services/workspace_service.py:103` | `kind='agent_search'` (deep_search / تحليل قانوني, `agents/agent_search/publisher.py:170`) or `kind='agent_writing'` (writer, `agents/writer/publisher.py:395`). Emits `workspace_item_created` carrying the `item_id`. |
| Router may answer directly | `orchestrator.py:1268` `ChatResponse` branch | **No WI produced** for a plain chat answer. Legal questions normally route to `deep_search`, but this must be handled (see §10). |
| Confidence signal — ALREADY EXISTS | `metadata.confidence` on the WI (`agents/agent_search/publisher.py:77`); per-ref `relevance` + `used` on `workspace_item_references` (`references_service.py:592`) | Aggregator emits `"high"/"medium"/"low"`. **Answer to their open Q#2: yes, we expose a real label.** |
| Share snapshot | `POST /workspace/{item_id}/share` `backend/app/api/blog.py:161` → `blog_service.insert_post()` (`blog_service.py:239`) + `fetch_item_references(used_only=True)` (`references_service.py:78`) | Freezes `content_md` + resolved refs into `blog_posts`; DB mints `token`. `subtype` copied from `metadata->>'subtype'`. |
| `blog_posts` schema | `shared/db/migrations/070_blog_posts.sql`, `084`, `085` | `question_text`/`content_md` NOT NULL; `display_mode ∈ {question,title}`; `is_published` (kill switch); `is_public` (gallery). Writes are **service-role only** (no anon/authenticated INSERT policy). |
| Service-to-service auth precedent | `backend/app/api/internal_webhooks.py:68` `_verify_webhook_secret` + `INTERNAL_WEBHOOK_SECRET` (`shared/config.py:159`) | Shared-secret header `Depends`, fail-closed when unset, mounted under `/internal` (`main.py:526`). **This is the pattern to mirror.** |
| Bot user | **Does not exist.** `public.users` rows are only created by the `handle_new_user()` trigger on `auth.users` insert (`shared/db/migrations/014_triggers.sql:52`). | Must provision one real Supabase Auth identity for the editorial bot (§9). **Answer to their open Q#3: WE provision it.** |
| Public read | `GET /public/blog/{token}` anon (`blog.py:91`); gallery `GET /public/blogs` anon | The `/blog/<token>` page is anonymous regardless of `is_public`; `is_public` only controls listing in the `/blog` gallery. |

**Request's model was essentially right.** The one correction: the share is **already a backend service-role insert** (`POST /workspace/{item_id}/share`), not a client-side insert — so we own it and will reuse `blog_service.insert_post` directly (no HTTP round-trip inside the job).

---

## 3. Answers to the request's §12 open questions

1. **Generation time / concurrency.** A turn is bounded by `LUNA_PIPELINE_TIMEOUT_S = 420s` (7 min hard cap; `shared/config.py:138`); typical deep_search synthesis is ~1–4 min. **Async** (§4). Volume: internal use, capped at **100 submissions/hour + 300/day** (§7). Concurrency: backend is **single-worker** by contract (`main.py:83`, in-process dedup); processing is gated to `EDITORIAL_MAX_CONCURRENT_JOBS=2`, so a burst of submissions is *accepted* fast (cheap insert) then drains 2-at-a-time — a 100-job hour drains in ~100×2min/2 ≈ 100 min, which is fine for an async model.
2. **Confidence.** Yes — native `metadata.confidence` (high/medium/low) + reference relevance/`used` counts. We return `confidence.label` + `reasons`; `score` optional/derived.
3. **Editorial bot user.** WE provision it (one Supabase Auth identity → auto `users` row → configured `EDITORIAL_BOT_USER_ID`). No curator flag — posts are private/unlisted (§11). See §9.
4. **subtype.** Store the request's `subtype` (e.g. `marketing_telegram`) on `blog_posts.subtype` so marketing posts are filterable; the underlying WI keeps its own `legal_synthesis` subtype. Recommend `marketing_telegram`.
5. **Async vs sync.** **Async job** (submit → poll), with an optional `?wait=N` long-poll convenience. Generation is too slow for a reliable synchronous request.
6. **Extra share-flow fields.** For a `question`-mode post we set `question_text` (= the question), `title` (engine or request), `content_md`, `references_json`, `subtype`, `display_mode='question'`, `is_published` (per publish_policy). `is_public` stays **false** — private/unlisted, so **not** in the `/blog` gallery or sitemap. Public URL = `PUBLIC_WEB_URL + /blog/<token>`.

---

## 4. Architecture

Async job model — durable table + in-process worker (mirrors the existing detached-pipeline + reconciler patterns).

```
POST /internal/blog-post-jobs           (service key)                          GET /internal/blog-post-jobs/{id}
  │  validate + idempotency dedup                                                 │  read job row
  │  INSERT blog_post_jobs (status=queued)                                        └─ return status + result
  │  spawn asyncio task → process_job(job_id)   ── tracked in a keepalive set;
  │                                                 generation gated by asyncio.Semaphore(EDITORIAL_MAX_CONCURRENT_JOBS)
  │                                                 so a burst parks in the queue and drains 2-at-a-time
  └─ 202 { job_id, status:"queued", status_url }

process_job(job_id):                                            [backend/app/api/deepsearch_api/service.py]
  status=processing
  1. create throwaway conversation owned by EDITORIAL_BOT_USER_ID
  2. save user message = question           (Rule #7: user msg BEFORE AI call)
  3. insert assistant placeholder
  4. drive generate_answer_headless():  async-consume handle_message(...) to completion,
        capturing workspace_item_created → item_id     (NO SSE, NO quota gate — our own bot)
  5. load the agent_search WI: content_md, metadata.confidence, title, subtype
     load refs via fetch_item_references(used_only=True)
  6. derive confidence {label, reasons, score?}
  7. decide is_published per publish_policy/min_confidence (is_public always false — private/unlisted)
  8. blog_service.insert_post(owner=bot, subtype=req.subtype, question_text=question,
        content_md, references_json, display_mode='question', is_published)
  9. UPDATE job: status=completed, result{post_id, token, url, confidence, ...}
 10. if callback_url: best-effort POST result
  (on exception/timeout → status=failed, error{code,message,retryable})
```

**Why call `handle_message` directly (not `send_message_stream`):** the headless path deliberately skips SSE framing, the per-conversation send-dedup, and `quota.check` — the editorial bot is our own cost and must not be capped by user quota windows. Cost is still recorded: `handle_message` opens `collect_llm_calls` internally, so every LLM call still lands in the `llm_calls` ledger attributed to the bot user (`orchestrator.py:1087`).

---

## 5. Data model — migration `086_blog_post_jobs.sql`

New table (service-role only, no user RLS surface):

```sql
CREATE TABLE public.blog_post_jobs (
    job_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT UNIQUE NOT NULL,            -- retries never duplicate a post
    status          TEXT NOT NULL DEFAULT 'queued',  -- queued|processing|completed|failed
    -- request echo
    question        TEXT NOT NULL,
    title           TEXT,
    display_mode    TEXT NOT NULL DEFAULT 'question',
    subtype         TEXT NOT NULL DEFAULT 'marketing_telegram',
    language        TEXT NOT NULL DEFAULT 'ar',
    publish_policy  TEXT NOT NULL DEFAULT 'auto',     -- auto|always|never
    min_confidence  TEXT NOT NULL DEFAULT 'high',     -- high|medium|low
    metadata        JSONB NOT NULL DEFAULT '{}',
    callback_url    TEXT,
    -- results / provenance
    conversation_id UUID,           -- the throwaway conversation used
    workspace_item_id UUID,         -- the generated WI
    post_id         UUID,           -- FK-ish to blog_posts (no hard FK; provenance)
    result          JSONB,          -- the full result payload we return
    error           JSONB,          -- {code,message,retryable}
    attempts        INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    CHECK (status IN ('queued','processing','completed','failed')),
    CHECK (publish_policy IN ('auto','always','never'))
);
CREATE UNIQUE INDEX idx_blog_post_jobs_idem ON public.blog_post_jobs (idempotency_key);
CREATE INDEX idx_blog_post_jobs_status ON public.blog_post_jobs (status) WHERE status IN ('queued','processing');
ALTER TABLE public.blog_post_jobs ENABLE ROW LEVEL SECURITY;   -- no policies => service-role only
-- updated_at trigger (reuse public.update_updated_at())
```

Idempotency retention ≥30d: a cheap daily prune of `completed`/`failed` rows older than 30d (add to the existing APScheduler jobs in `main.py`) — optional for v1.

**Also extend `blog_service.insert_post`** (`blog_service.py:239`) with an `is_published: bool = True` param so the job can honor `publish_policy` (auto/always/never). `is_public` stays at its column default (`false`) — these posts are never gallery-listed (§11) — so no new param is needed there. Backward-compatible default keeps the in-app share path unchanged.

---

## 6. Config additions — `shared/config.py`

Mirror `INTERNAL_WEBHOOK_SECRET`:

```python
EDITORIAL_SERVICE_KEY: Optional[str] = None   # Bearer key for the blog-post-jobs API
EDITORIAL_BOT_USER_ID: Optional[str] = None   # public.users.user_id of the editorial bot
EDITORIAL_MAX_CONCURRENT_JOBS: int = 2        # in-flight generation cap (protects single-worker backend)
EDITORIAL_RATE_LIMIT_PER_HOUR: int = 100      # submissions / rolling hour
EDITORIAL_RATE_LIMIT_PER_DAY: int = 300       # submissions / rolling day
```

All set as Railway env vars on the backend service. Endpoint is **fail-closed** on auth: if `EDITORIAL_SERVICE_KEY` unset → all calls 401 (same stance as `_verify_webhook_secret`). **No quota/plan gating** — this is our own internal use; the *only* volume limits are the two windows above (§7) + the concurrency cap. (Resolves §14 #4.)

---

## 7. Auth + rate limiting

**Auth — `_verify_service_key`** (`deepsearch_api/auth.py`). A `Depends` guard modeled on `internal_webhooks._verify_webhook_secret` (`internal_webhooks.py:68`) but reading `Authorization: Bearer <key>` (the request's chosen transport) via `HTTPBearer(auto_error=False)`:
- compare **constant-time** (`hmac.compare_digest`) against `EDITORIAL_SERVICE_KEY`;
- unset secret → 401 "editorial auth not configured" (fail-closed);
- mismatch/missing → 401.

**Rate limit — dedicated two-window limiter** (`deepsearch_api/ratelimit.py`), a `Depends` on the **POST submit route only** (GET polling is never capped). Mirrors the existing Redis **sliding-window ZSET** idiom (`middleware/rate_limit.py:101-118`) but with **two windows at once**:
- `EDITORIAL_RATE_LIMIT_PER_HOUR = 100` over a rolling **3600s** window, and `EDITORIAL_RATE_LIMIT_PER_DAY = 300` over a rolling **86400s** window.
- **Global key, not per-IP** — the cap is on the API as a whole (one internal caller): `ratelimit:editorial:blog-post-jobs:3600` and `:86400`. Check both; a breach of *either* → **429** with the Arabic `RATE_LIMITED` envelope + `Retry-After` (seconds to the offending window's reset) + `X-RateLimit-Remaining-Hour` / `X-RateLimit-Remaining-Day` headers.
- **Counts only genuinely-new submissions.** Ordering in the route: `_verify_service_key` → **idempotency lookup** (existing key → return the existing job `200`, *before* the limiter, so retries are free) → rate-limit check+add → create job. So the 100/300 budget is spent on real questions, not retries or polls.
- **Fail-open** when Redis is down (consistent with the existing middleware) — a Redis blip must not block your own batch. This is a cost/safety cap, not a security boundary (the service key is the security boundary).

**Exempt the family from the global 60/min middleware.** The global limiter would otherwise 429 the 61st submission in a minute — but you want bursts up to the *hourly* cap. Add a prefix-skip for `/internal/blog-post-jobs` at the top of `RateLimitMiddleware.dispatch` (the current `EXEMPT_PATHS` is exact-match only, `rate_limit.py:39,50`, so it can't cover the `/{job_id}` sub-path). The dedicated limiter above then becomes the single source of truth for submissions; polls are unthrottled.

---

## 8. Endpoints + response contract

**Home: a new `backend/app/api/deepsearch_api/` package** (vertical slice). First *package* (vs flat file) under `backend/app/api/` — the home for HTTP surfaces that expose agent pipelines to external callers (room to add more `*_api` packages later). Kept in `backend/` because it's a **transport concern**: the FastAPI router, service-key auth, request/response models, the Arabic error envelope, and the rate-limit/CORS middleware all live in `backend/`, and the dependency stays one-way `backend → agents` (the package *calls* `agents.orchestrator.handle_message`, exactly as `message_service` already does — the agent it exposes doesn't move). `backend/app/main.py` mounts the router (`from backend.app.api.deepsearch_api.router import router`) under `prefix="/internal"` (next to `internal_webhooks`, `main.py:526`). (Path is ours; if marketing insists on `/v1/...` we can also mount there — but `/internal` keeps service-key routes visually separate from the JWT API. **Decided (§14 #1): keep `/internal`.**)

| Method · Route | Body / result |
|---|---|
| `POST /internal/blog-post-jobs` (`?wait=N` optional) | body = request §5 of the marketing doc → `202 {job_id, status:"queued", status_url}`. On idempotency replay → `200` with the existing job. With `?wait=N`, await the task up to N s and inline the result if ready. |
| `GET /internal/blog-post-jobs/{job_id}` | `200 {job_id, status, result?|error?}` matching the marketing doc §6. |

Response models in `backend/app/api/deepsearch_api/models.py`: `BlogJobSubmitResponse`, `BlogJobStatusResponse`, `BlogJobResult`, `BlogJobConfidence` (+ the `BlogPostJobRequest` body). `result` shape = their §6 (`post_id, token, url, is_published, confidence{label,score?,reasons[]}, title, question_text, summary, content_md, references{count,top[]}, workspace_item_id, created_at`). `summary` = `blog_service.make_snippet(content_md)` (`blog_service.py:75`) — the short outreach snippet.

---

## 9. Editorial bot user provisioning (one-time, manual + documented)

1. Create one Supabase Auth user (dashboard or admin API), e.g. `editorial-bot@rayhanai.com`. The `handle_new_user()` trigger auto-inserts its `public.users` row.
2. Read back its `users.user_id` → set `EDITORIAL_BOT_USER_ID` env var.
3. **No curator flag needed** — posts are private/unlisted (never gallery), so leave `users.can_access_blog=false`. Posts are reachable only via their `/blog/<token>` link, which is exactly what marketing sends 1:1.
4. Quota: not needed on the headless path (we bypass `quota.check`). If we ever route the bot through `send_message_stream`, assign it a marketing plan. Document either way in the runbook.

---

## 10. The "router answered directly (no WI)" case

Legal questions normally route to `deep_search`, but the router *can* return a `ChatResponse` (no workspace_item). Handling:

- **v1 (recommended, simplest):** trust the router. If a turn ends with **no `workspace_item_created`**, mark the job `completed` with `confidence.label="low"`, `is_published=false` (under `auto`), and put the chat text (if any) in `content_md` with empty `references`. Marketing's `auto` gate then simply won't publish it. This satisfies their acceptance criterion "weak/unanswerable → low + unpublished".
- **v2 (hardening, optional):** bypass the router and invoke the deep_search dispatch directly (`_run_deep_search` path in `orchestrator.py:1612`) to force grounded synthesis for every marketing question. More surgery; defer unless v1 shows too many chat-only routes.

---

## 11. Confidence, publish, idempotency, errors

- **confidence.label** = WI `metadata.confidence` (default `"medium"` if absent). **reasons** = derived (`"N مرجع عالي الصلة"`, subtype coverage). **score** = optional map (high≈0.85 / medium≈0.6 / low≈0.3) or from relevance counts — mark optional.
- **publish_policy:** `auto` → `is_published = (confidence.label ≥ min_confidence)`; `always`/`never` → force. **Always return `confidence` + `content_md`** even when unpublished (low-confidence ≠ error).
- **is_public (gallery): always `false`.** These are **private/unlisted** posts — a token link sent 1:1 to a specific user, never surfaced in the public `/blog` gallery. The `/blog/<token>` page is already anonymous-but-unguessable — *that unlisted token IS the privacy model*. So the bot needs no curator flag and the gallery is untouched.
- **Idempotency:** unique `idempotency_key`. On submit, existing key → return that job (never a second post). The unique index makes the dedup race-safe. The lookup runs **before** the rate-limit counter (§7), so retries never consume the 100/hr · 300/day budget.
- **Rate limit:** 100 submissions/rolling-hour + 300/rolling-day, enforced on POST only (§7). No other quota/plan gating — internal use.
- **Errors:** `{code, message, retryable}`. `generation_failed` (exception) retryable; `generation_timeout` retryable; validation `400`; auth `401`; `429` from the limiter; idempotency replay `200`.

---

## 12. File manifest / deliverables

**New — `backend/app/api/deepsearch_api/` package** (first *package* under the flat `backend/app/api/`)
- `backend/app/api/deepsearch_api/__init__.py`
- `backend/app/api/deepsearch_api/router.py` — `POST`/`GET` blog-post-jobs routes + task spawn/keepalive set.
- `backend/app/api/deepsearch_api/auth.py` — `_verify_service_key` (Bearer, constant-time, fail-closed).
- `backend/app/api/deepsearch_api/ratelimit.py` — two-window (100/hr + 300/day) sliding-window Redis limiter `Depends`, POST-only, fail-open.
- `backend/app/api/deepsearch_api/service.py` — `create_or_get_job`, `get_job`, `process_job`.
- `backend/app/api/deepsearch_api/generate.py` — `generate_answer_headless` (creates conversation + user msg + placeholder, consumes `orchestrator.handle_message`, captures the WI).
- `backend/app/api/deepsearch_api/models.py` — request body + job/result/confidence response models.
- `shared/db/migrations/086_blog_post_jobs.sql` — jobs table + indexes + RLS (global — can't move into the package).

**Modified**
- `shared/config.py` — `EDITORIAL_SERVICE_KEY`, `EDITORIAL_BOT_USER_ID`, `EDITORIAL_MAX_CONCURRENT_JOBS`, `EDITORIAL_RATE_LIMIT_PER_HOUR`, `EDITORIAL_RATE_LIMIT_PER_DAY` (global Settings — stays here).
- `backend/app/services/blog_service.py` — `insert_post(... is_published=True)` (honor publish_policy; `is_public` stays column-default false — private/unlisted).
- `backend/app/middleware/rate_limit.py` — prefix-skip `/internal/blog-post-jobs` (the dedicated limiter owns it; §7).
- `backend/app/main.py` — `from backend.app.api.deepsearch_api.router import router` + `include_router(..., prefix="/internal", tags=["deepsearch-api"])`; startup catch-up sweep for stuck jobs + daily idempotency prune in the scheduler block.

**Docs**
- Bot-user provisioning runbook + env-var list (Railway) + the finalized external contract handed back to marketing.

---

## 13. Build phases

1. **DB + config** — migration 086; `insert_post` `is_published` flag; the 5 `EDITORIAL_*` config fields. Provision the bot user; capture `EDITORIAL_BOT_USER_ID`.
2. **Headless generation core** — `generate_answer_headless` (conversation + user msg + placeholder + consume `handle_message` + capture WI). Unit-test against a real question locally.
3. **Job service + endpoints** — `create_or_get_job`/`process_job`/`get_job`, the two routes, service-key guard + two-window rate limiter + global-middleware prefix-skip, Semaphore-gated task spawn + keepalive set.
4. **Confidence + publish semantics + snapshot** — wire `metadata.confidence`, publish policy, `insert_post`, response contract.
5. **Durability + callback** — startup catch-up for stuck jobs; optional webhook POST; idempotency prune.
6. **Deploy + verify** — env vars on Railway; end-to-end: POST a known question → poll → resolvable `url` with `[n]` citations + `confidence.label`; re-POST same key → same `post_id`; weak question → low + unpublished (their §13 acceptance criteria).

---

## 14. Open decisions (LUNA side)

1. ~~**Route prefix.**~~ **DECIDED:** internal-only for now → keep **`/internal/blog-post-jobs`** (the prefix is now semantically correct). Revisit a versioned `/partner/v1/…` prefix only if this is ever opened to a third party.
2. ~~**Router trust.**~~ **DECIDED:** v1 — trust the router (legal Qs route to deep_search); a chat-only turn with no WI → `completed`, `confidence=low`, unpublished (§10).
3. ~~**Gallery listing.**~~ **DECIDED:** posts are **private/unlisted** — a token link sent 1:1 to a particular user. `is_public` **always false**; never in the `/blog` gallery; bot needs no curator flag.
4. ~~**Quota.**~~ **DECIDED:** bypass quota/plan gating; the only limits are **100/hr + 300/day** submissions (§7) + `EDITORIAL_MAX_CONCURRENT_JOBS=2`.
5. ~~**Worker model.**~~ **DECIDED:** Option A — in-process asyncio task (Semaphore-gated) + durable `blog_post_jobs` table + startup catch-up sweep. No external queue.
6. **Migration 086 go-ahead.**
7. **Rate-limit windows:** rolling (sliding-window ZSET, recommended — smooth) vs fixed calendar hour/day buckets (simpler, allows boundary bursts). Plan assumes rolling.

---

## 15. Risks / traps

- **Single-worker invariant** (`main.py:83`): in-process job tasks are fine, but never scale the backend to >1 worker without moving job pickup to a DB-claim (`UPDATE ... WHERE status='queued' RETURNING`) — otherwise two workers double-process. The status index + a claim update covers this if it ever changes.
- **Detached-task GC:** hold spawned job tasks in a module-level set (like `message_service._inflight_pipelines`, `message_service.py:52`) or they get GC'd mid-run.
- **`handle_message` needs a real, owned conversation + a pre-saved user message** — the headless helper must replicate that ordering exactly (Rule #7) or the pipeline misbehaves on resume/history loads.
- **No WI on chat-only routes** — see §10; don't assume every turn yields an artifact.
- **`blog_posts` write is service-role only** — correct; `process_job` uses `get_supabase` (service role, `deps.py:89`), never a user-scoped client.
- **Cost visibility** — bypassing `quota.check` skips the *gate*, not the *ledger*; `collect_llm_calls` still bills the bot user, so marketing generation cost is trackable via `llm_calls` / `user_cost_daily`.
