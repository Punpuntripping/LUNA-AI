# Blog-Post Generation API — Operator Runbook

Operational steps to turn the built code (`backend/app/api/deepsearch_api/`) into
a live, service-authed endpoint. Do these AFTER migration `086_blog_post_jobs.sql`
is applied to prod and the code is deployed.

Endpoints (mounted under `/internal`):
- `POST /internal/blog-post-jobs`            — submit (service key + rate limit)
- `GET  /internal/blog-post-jobs/{job_id}`   — poll (service key only)

---

## 1. Provision the editorial bot user (one-time)

`public.users` rows are created ONLY by the `handle_new_user()` trigger on
`auth.users` insert — so the bot must be a real Supabase Auth identity.

1. Supabase Dashboard → **Authentication → Users → Add user** (or the Admin API):
   - Email: `editorial-bot@rayhanai.com`
   - Password: a strong random secret (store in the password manager; it is never
     used by the API — the endpoint auths with the service key, not this login).
   - Auto-confirm the email so no confirmation flow is needed.
2. The `handle_new_user()` trigger auto-inserts the matching `public.users` row.
3. Leave `users.can_access_blog = false`. These posts are **private/unlisted**
   (a token link sent 1:1), never gallery-listed, so the bot needs **no curator
   flag**.
4. Quota/plan: NOT needed. The headless path (`generate_answer_headless`) bypasses
   `quota.check` entirely — the bot is our own cost and is billed to the
   `llm_calls` ledger, not gated by plan windows. (Only assign a plan if you ever
   route the bot through `send_message_stream`, which we do not.)

### Capture `EDITORIAL_BOT_USER_ID`

Read back the bot's `public.users.user_id` (NOT `auth_id`):

```sql
select user_id, auth_id, email
from public.users
where email = 'editorial-bot@rayhanai.com';
```

Use the `user_id` value for the env var below. (This is the same id
`blog_posts.owner_user_id` and `workspace_items.user_id` are written with.)

---

## 2. Railway env vars (backend service)

Set on the **backend** Railway service. The app boots cleanly with these unset
(dev), but the endpoint is **fail-closed**: with `EDITORIAL_SERVICE_KEY` unset,
every call returns 401.

| Var | Required | Example / default | Notes |
|---|---|---|---|
| `EDITORIAL_SERVICE_KEY` | **Yes** | `openssl rand -hex 32` | Bearer key marketing sends as `Authorization: Bearer <key>`. Rotatable. THIS is the security boundary. |
| `EDITORIAL_BOT_USER_ID` | **Yes** | `<uuid from §1>` | `public.users.user_id` of the bot. Until set, jobs fail with `configuration_error` (not retryable). The boot catch-up sweep skips gracefully while unset. |
| `EDITORIAL_MAX_CONCURRENT_JOBS` | No | `2` | In-flight generation cap (Semaphore). Protects the single-worker backend. |
| `EDITORIAL_RATE_LIMIT_PER_HOUR` | No | `100` | Submissions / rolling hour (global, all callers). |
| `EDITORIAL_RATE_LIMIT_PER_DAY` | No | `300` | Submissions / rolling day (global). |

> Railway trap (from memory `feedback_railway_master_pull_trap`): setting env
> vars triggers a GitHub master-pull deploy that can overwrite a snapshot deploy.
> Set the vars, then confirm the resulting deploy is the intended commit.

Generate a key:
```bash
openssl rand -hex 32
```

---

## 3. Smoke test (after deploy)

```bash
BASE=https://api.rayhanai.com          # or the Railway backend URL
KEY=<EDITORIAL_SERVICE_KEY>

# Submit
curl -sS -X POST "$BASE/internal/blog-post-jobs" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "idempotency_key": "smoke:2026-07-01:1",
    "question": "ما شروط صحة عقد البيع في النظام السعودي؟",
    "publish_policy": "auto",
    "min_confidence": "high",
    "subtype": "marketing_telegram"
  }'
# → 202 {"job_id":"…","status":"queued","status_url":"…/internal/blog-post-jobs/…"}

# Poll (repeat until completed|failed; typical deep_search ~1–4 min)
curl -sS "$BASE/internal/blog-post-jobs/<job_id>" \
  -H "Authorization: Bearer $KEY"
# completed → result.url = https://rayhanai.com/blog/<token>, confidence.label, content_md
```

Acceptance checks (marketing §13):
- `GET` eventually returns `status:"completed"` with a resolvable `url`, non-empty
  `content_md` with `[n]` markers, ≥1 `references`, a `confidence.label`.
- Re-`POST` with the SAME `idempotency_key` → **same** `post_id`/`url`, no new row,
  and it does NOT consume rate-limit budget.
- A deliberately weak/unanswerable question under `publish_policy:"auto"` →
  `completed` with `confidence.label:"low"` and `is_published:false` (the
  `/blog/<token>` page 404s until published — draft behavior).

Auth checks:
- Missing/wrong Bearer → 401 (Arabic). Unset `EDITORIAL_SERVICE_KEY` → 401 for all.

Rate-limit checks:
- Bursts up to 100/hr pass (the global 60/min middleware skips this path). The
  101st in an hour (or 301st in a day) → 429 with `Retry-After`,
  `X-RateLimit-Remaining-Hour`, `X-RateLimit-Remaining-Day`. Redis down → fail-open.

---

## 4. Key rotation

1. Set a new `EDITORIAL_SERVICE_KEY` on Railway (confirm the deploy).
2. Hand the new key to marketing.
3. Old key stops working immediately on the new deploy (single active key).

## 5. Operational notes

- **Single-worker invariant**: `main.py` refuses `WEB_CONCURRENCY>1`. Job pickup is
  in-process (asyncio task + keepalive set). Never scale to >1 worker without
  moving pickup to a DB-claim (`UPDATE … WHERE status='queued' RETURNING`), or two
  workers double-process (plan §15).
- **Boot catch-up**: ~75s after each boot, a one-shot sweep re-queues jobs stuck in
  `queued`/`processing`; a job attempted ≥3× is failed (retryable). Skips when
  `EDITORIAL_BOT_USER_ID` is unset.
- **Cost visibility**: generation cost lands in `llm_calls` attributed to the bot
  user → visible in `user_cost_daily`. Bypassing quota skips the gate, not the
  ledger.
- **Provenance**: each job row (`blog_post_jobs`) records `conversation_id`,
  `workspace_item_id`, `post_id`, the request echo, and `result`/`error`. Service-
  role only (RLS-on, no policies).
