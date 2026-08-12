# Rayhan Blog‑Post Generation API — Protocol v1

**Audience:** external integrators (the marketing / content pipeline).
**Status:** v1 — implemented; goes live on deploy once a service key + editorial bot are provisioned.
**Owner:** Rayhan (LUNA) backend. This document is the authoritative external contract; the server implementation lives in `backend/app/api/deepsearch_api/`.

---

## 1. What this API does

You POST a single **legal question**. The service runs it through the *same* grounded legal‑research + synthesis pipeline that powers an in‑app answer (retrieval over the Saudi regulations corpus → reranking → synthesis with `[n]` citations), then snapshots the answer into a **private, unlisted blog post** and returns:

- a stable public URL — `https://rayhanai.com/blog/<token>` — you can send 1:1 to a recipient,
- the full answer (`content_md`) + a short outreach `summary`,
- a **confidence** label (`high` / `medium` / `low`) you can gate outreach on,
- the cited sources.

Generation takes **~1–4 minutes** (hard cap 7 min), so the API is **asynchronous**: you submit a job, then poll (or long‑poll) for the result.

Posts are **private/unlisted** — reachable only via their unguessable `token` URL, never listed in the public `/blog` gallery or a sitemap. The token *is* the privacy boundary.

---

## 2. Base URL

| Environment | Base URL |
|---|---|
| Production | `https://api.rayhanai.com` |
| ~~Production (Railway alias)~~ | ~~`https://luna-backend-production-35ba.up.railway.app`~~ — **DO NOT USE**, see below |

⚠ **USE `api.rayhanai.com`. THE RAILWAY ALIAS IS SCHEDULED TO STOP WORKING.** It was listed
here as an equivalent fallback and it is not one. The Cloudflare cutover
(`.claude/plans/cloudflare_navigation_hardening.md` §3.4) arms an **origin lock**: the backend
begins rejecting, with `403`, every request that did not transit Cloudflare — proven by an
`X-Edge-Secret` header the edge injects. Traffic to the raw `*.up.railway.app` hostname bypasses
Cloudflare by definition, so it carries no such header and **every call on the alias will 403**,
including authenticated ones. The bearer key will not save it: the key is *authentication*, the
lock is a *network boundary*, and `/internal/*` is deliberately NOT exempt from it.

This breaks silently from the caller's side — a `403` on a route that worked yesterday, with no
deploy on your end. If you hold a copy of this document, change your base URL to
`https://api.rayhanai.com` now; it already works and will keep working.

All routes below are relative to the base URL. There is no `/api/v1` prefix — these are `/internal/*` service routes.

Public post links returned in results use the web origin `https://rayhanai.com` (not the API host).

---

## 3. Authentication

Every request must carry a **bearer service key**:

```
Authorization: Bearer <EDITORIAL_SERVICE_KEY>
```

- The key is issued to you out‑of‑band by the Rayhan team. Keep it secret; it is the **only** security boundary on this surface. Treat a leak as full access to generate posts under the editorial identity.
- **Fail‑closed:** if the server has no key configured, *every* call returns `401` — the endpoint is closed until provisioned.
- Comparison is constant‑time. A missing, malformed, or wrong key → `401`.

There is no OAuth, no refresh, no per‑request signing. One static bearer key.

---

## 4. Rate limits

A single global budget applies to the **submit** endpoint (POST). Polling (GET) is **never** rate‑limited.

| Window | Limit |
|---|---|
| Rolling hour (3600s) | **100** new submissions |
| Rolling day (86400s) | **300** new submissions |

- The budget is **API‑wide** (one internal caller), not per‑IP or per‑key.
- **Idempotency replays and polls are free** — only a genuinely‑new submission (a new `idempotency_key`) spends budget.
- Breaching *either* window → `429` with:
  - `Retry-After: <seconds>` — seconds until the offending window frees a slot,
  - `X-RateLimit-Remaining-Hour` / `X-RateLimit-Remaining-Day`.
- Successful `202` submissions also carry the two `X-RateLimit-Remaining-*` headers so you can self‑throttle.
- The limiter **fails open** if the server's Redis is unavailable (it is a cost cap, not a security control) — so do not rely on it for correctness; rely on your own `idempotency_key` discipline.

Concurrency: the server drains at most 2 generations at once, so a burst is *accepted* immediately (cheap insert) and *drains* over time. This is invisible to you — you just poll.

---

## 5. Core concepts

- **Async job.** `POST` creates a job (`status: queued`) and returns immediately. You then `GET` the job until `status` is terminal (`completed` or `failed`). Optionally, `?wait=N` lets a single POST block up to N seconds and inline the result if it finishes fast.
- **Idempotency.** You supply an `idempotency_key`. Re‑submitting the same key **never** creates a second post — it returns the existing job. Use this for safe retries.
- **Confidence gating.** Each result carries `confidence.label`. You decide whether to publish/send based on it (see §10). `publish_policy` lets the server apply that gate for you.
- **Publish state.** A post is always created. Whether it is *published* (its URL resolves publicly) depends on `publish_policy` + confidence. An unpublished draft's URL returns `404` until published.

---

## 6. Endpoints

### 6.1 Submit a job

```
POST /internal/blog-post-jobs[?wait=N]
Authorization: Bearer <key>
Content-Type: application/json
```

`wait` (optional, query): seconds to long‑poll, `0`–`60` (values above 60 are clamped to 60). If the job finishes within the window, the response is the **terminal status** body (§7.3) with code `200`. Otherwise you get the accepted body (`202`).

**Responses**

| Code | When | Body |
|---|---|---|
| `202` | New job accepted, still processing | Submit body (§7.2) + `X-RateLimit-Remaining-*` headers |
| `200` | Idempotency replay, **or** `?wait` that resolved | Submit body *or* Status body (§7.3) — see note |
| `400` | Missing/empty `question` or `idempotency_key`, or bad enum value | Error envelope (§11) |
| `401` | Bad/missing key | Error envelope |
| `429` | Rate limit exceeded | Error envelope + `Retry-After` |

> **Note on the `200` body shape.** A replay (or a `?wait` hit) returns `200`; the body is the **submit** shape if the job is still in flight, or the **status** shape (with `result`/`error`) if it already reached a terminal state. **Robust clients should not branch on the POST body beyond reading `job_id`** — always poll `GET` (or use `?wait`) to obtain the final `result`.

### 6.2 Poll a job

```
GET /internal/blog-post-jobs/{job_id}
Authorization: Bearer <key>
```

**Responses**

| Code | When | Body |
|---|---|---|
| `200` | Job found | Status body (§7.3) |
| `401` | Bad/missing key | Error envelope |
| `404` | No such job | Error envelope (`BLOG_JOB_NOT_FOUND`) |
| `404` | `job_id` is not a valid UUID | Error envelope (`INVALID_UUID`) |

Never rate‑limited — poll as often as you like (a sensible cadence is every ~2–5 s).

---

## 7. Schemas

### 7.1 Request body (`POST`)

| Field | Type | Req? | Default | Meaning |
|---|---|---|---|---|
| `idempotency_key` | string | **yes** | — | Stable dedup key. Same key ⇒ same job, never a second post. Use a UUID or a content hash. |
| `question` | string | **yes** | — | Anonymized, self‑contained legal question (Arabic). Becomes `question_text` and drives generation. **Do not include real client PII** — use neutral placeholders (الطرف الأول/الثاني…). |
| `title` | string | no | `null` | Optional post title. When null, the engine's artifact title is used. |
| `display_mode` | string | no | `"question"` | `"question"` (shows the question block) or `"title"` (title‑led مدونة layout). |
| `subtype` | string | no | `"marketing_telegram"` | Free tag stored on the post so your posts are filterable. |
| `language` | string | no | `"ar"` | Informational; answers are Arabic. |
| `publish_policy` | string | no | `"auto"` | `auto` \| `always` \| `never` — see §10. |
| `min_confidence` | string | no | `"medium"` | `high` \| `medium` \| `low` — the threshold `auto` compares against. Default `medium` ⇒ medium **and** high publish; only `low` stays a draft. |
| `metadata` | object | no | `{}` | Opaque provenance echoed onto the job row. **Never surfaced publicly.** |
| `callback_url` | string | no | `null` | If set, the server best‑effort `POST`s the result here on completion (§12). |

Unknown fields are ignored. A *totally absent* required field yields a non‑Arabic `422` (FastAPI default); an empty/whitespace one yields the Arabic `400` envelope.

### 7.2 Submit response body

```json
{
  "job_id": "b1f2…",
  "status": "queued",
  "status_url": "https://api.rayhanai.com/internal/blog-post-jobs/b1f2…"
}
```

### 7.3 Status response body (`GET`, and terminal `?wait`)

`result` is present only when `status == "completed"`; `error` only when `status == "failed"`. Absent fields are omitted (not `null`).

```json
{
  "job_id": "b1f2…",
  "status": "completed",
  "result": {
    "post_id": "9a…",
    "token": "3f9c…",
    "url": "https://rayhanai.com/blog/3f9c…",
    "is_published": true,
    "confidence": { "label": "high", "score": 0.85, "reasons": ["7 مرجع مستشهد به", "5 مرجع عالي الصلة"] },
    "title": "…",
    "question_text": "…",
    "summary": "…short outreach snippet…",
    "content_md": "# …full answer in Markdown with [1] [2] citations…",
    "references": {
      "count": 7,
      "top": [ { "n": 1, "title": "نظام العمل — المادة 77", "relevance": "high" } ]
    },
    "workspace_item_id": "c4…",
    "created_at": "2026-07-02T10:12:00Z"
  }
}
```

**`result` fields**

| Field | Type | Meaning |
|---|---|---|
| `post_id` | string? | Internal post id (provenance). |
| `token` | string? | Unguessable public token. |
| `url` | string | Public link `https://rayhanai.com/blog/<token>`. Resolves iff `is_published` (else `404` until published). |
| `is_published` | bool | Whether the URL is live now (§10). |
| `confidence` | object | `{ label, score?, reasons[] }` — your outreach gate. |
| `title` | string? | Final post title. |
| `question_text` | string | The question as stored/shown. |
| `summary` | string | Short snippet for outreach messages. |
| `content_md` | string | Full answer, Markdown, inline `[n]` citations. Returned **even when unpublished**. |
| `references` | object | `{ count, top: [{ n, title, relevance? }] }`. `relevance ∈ {high, medium}`. |
| `workspace_item_id` | string? | Internal artifact id. `null` if the question produced no grounded analysis (see §10). |
| `created_at` | string | ISO‑8601 UTC. |

### 7.4 Failed‑job error object

When `status == "failed"`:

```json
{ "job_id": "b1f2…", "status": "failed",
  "error": { "code": "generation_timeout", "message": "…Arabic…", "retryable": true } }
```

| `error.code` | `retryable` | Meaning / action |
|---|---|---|
| `generation_timeout` | `true` | Pipeline exceeded the 7‑min cap. Re‑submit with a **new** `idempotency_key`. |
| `generation_failed` | `true` | Pipeline raised. Retry with a new key; if it persists, contact us. |
| `generation_interrupted` | `true` | Server restarted mid‑job and the retry budget was exhausted. Re‑submit with a new key. |
| `configuration_error` | `false` | Server not fully provisioned (bot user unset). **Do not retry** — contact us. |

> A **low‑confidence / unpublished** result is **not** a failure — it comes back as `status: completed` with `is_published: false`. Only the codes above are failures.

---

## 8. Job status lifecycle

```
queued → processing → completed
                    ↘ failed
```

- `queued` — accepted, waiting for a worker slot.
- `processing` — generating.
- `completed` — `result` is populated (published or draft).
- `failed` — `error` is populated (see §7.4).

`completed` and `failed` are terminal; the row does not change afterward.

---

## 9. Idempotency & retries

- Supply a **stable `idempotency_key`** per logical question (a UUID you persist, or a hash of the normalized question).
- Re‑submitting the same key returns the **existing** job — you will never double‑generate or double‑charge, and the retry does **not** spend rate budget.
- Retries are safe at any point (network blip, timeout on your side): same key ⇒ same job.
- To force a **fresh** generation of the same question (e.g. after a `generation_failed`), submit a **new** key.

---

## 10. Confidence & publishing

**Confidence** (`result.confidence.label`) reflects how well‑grounded the answer is:

| Label | ~score | Meaning |
|---|---|---|
| `high` | 0.85 | Strongly grounded — safe to send. |
| `medium` | 0.60 | Grounded but thinner — review before sending. |
| `low` | 0.30 | Weak/unanswerable or the router did not produce grounded analysis. Don't send. |

**`publish_policy`** controls whether the server publishes the post (makes its `url` resolve):

| Policy | Result |
|---|---|
| `auto` (default) | `is_published = (confidence.label ≥ min_confidence)`. Ranks: `low < medium < high`. |
| `always` | Always published, regardless of confidence. |
| `never` | Never published — always a draft (you can publish later out‑of‑band). |

Examples with defaults (`auto`, `min_confidence: medium`): a `high` or `medium` result publishes; only a `low` result is created as an unpublished draft (`is_published: false`, `url` 404s) — you still get the full `content_md` + `confidence` back to decide what to do. (Pass `min_confidence: high` to publish only high-confidence answers, or `publish_policy: always` to publish regardless.)

`is_public` (gallery listing) is **always false** — these posts are never listed publicly; the token link is the only entry point.

---

## 11. HTTP error envelope

All server‑side (non‑job) errors use one envelope:

```json
{ "error": { "code": "VALIDATION_ERROR", "message": "السؤال مطلوب", "status": 400 },
  "detail": "السؤال مطلوب" }
```

| HTTP | `error.code` | Cause |
|---|---|---|
| `400` | `VALIDATION_ERROR` | Empty `question`/`idempotency_key`, or invalid `display_mode`/`publish_policy`/`min_confidence`. |
| `401` | `AUTH_INVALID` | Missing/wrong key, or server key unset (fail‑closed). |
| `404` | `BLOG_JOB_NOT_FOUND` | Unknown `job_id`. |
| `404` | `INVALID_UUID` | `job_id` path param is not a UUID. |
| `429` | `RATE_LIMITED` | Hour or day submission cap hit. Honor `Retry-After`. |
| `500` | `INTERNAL_ERROR` | Unexpected server/dependency failure. Retry with backoff. |
| `422` | *(FastAPI default, non‑Arabic)* | A required field is entirely absent or of the wrong JSON type. |

Messages are Arabic. Branch on `error.code` (stable), not on `message` (wording may change).

---

## 12. Completion callback (optional)

If you pass `callback_url`, the server best‑effort `POST`s JSON there when the job reaches a terminal state:

- On success: `{ "job_id": "…", "status": "completed", "result": { …§7.3 result… } }`
- On failure: `{ "job_id": "…", "status": "failed", "error": { …§7.4… } }`

The callback is **fire‑and‑forget** (10 s timeout, no retries, failures swallowed). It is a latency optimization, **not** a guarantee — always treat `GET` polling / `?wait` as the source of truth, and make your callback handler idempotent (it may race with your own poll).

---

## 13. Recommended client flow

1. Compute a stable `idempotency_key` for the question.
2. `POST /internal/blog-post-jobs` with the key + `question` (+ optional `publish_policy`, `min_confidence`, `callback_url`). Optionally `?wait=30` to try for an inline result.
3. Read `job_id`. If the POST already returned a terminal `result`/`error`, you're done.
4. Otherwise poll `GET /internal/blog-post-jobs/{job_id}` every ~3 s until `status` is `completed` or `failed` (or await your callback).
5. On `completed`: read `confidence.label`; if it clears your bar and `is_published` is true, send `url` + `summary`. Keep `content_md` for your records.
6. On `failed`: if `error.retryable`, re‑submit with a **new** key; else surface to an operator.

**Python (requests):**

```python
import hashlib, time, requests

BASE = "https://api.rayhanai.com"
KEY  = "…your service key…"
H    = {"Authorization": f"Bearer {KEY}"}

def generate_post(question: str, *, min_confidence="high", publish_policy="auto"):
    idem = hashlib.sha256(question.strip().encode("utf-8")).hexdigest()
    r = requests.post(
        f"{BASE}/internal/blog-post-jobs",
        headers=H,
        params={"wait": 30},                      # try for an inline result
        json={
            "idempotency_key": idem,
            "question": question,
            "publish_policy": publish_policy,
            "min_confidence": min_confidence,
            "subtype": "marketing_telegram",
        },
        timeout=60,
    )
    r.raise_for_status()
    job = r.json()
    if job.get("result") or job.get("error"):     # ?wait resolved inline
        return job

    job_id = job["job_id"]
    while True:                                    # poll to terminal
        time.sleep(3)
        s = requests.get(f"{BASE}/internal/blog-post-jobs/{job_id}", headers=H, timeout=30).json()
        if s["status"] in ("completed", "failed"):
            return s

res = generate_post("ما هي مدة الإشعار في إنهاء عقد العمل غير محدد المدة؟")
if res["status"] == "completed":
    r = res["result"]
    if r["is_published"] and r["confidence"]["label"] == "high":
        print("SEND:", r["url"], "—", r["summary"])
    else:
        print("HOLD:", r["confidence"]["label"], r["is_published"])
else:
    print("FAILED:", res["error"])
```

---

## 14. Worked examples (curl)

**Submit (long‑poll 30 s):**
```bash
curl -sS -X POST "https://api.rayhanai.com/internal/blog-post-jobs?wait=30" \
  -H "Authorization: Bearer $EDITORIAL_SERVICE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "idempotency_key": "campaign-2026-07-labor-notice-01",
        "question": "ما هي مدة الإشعار في إنهاء عقد العمل غير محدد المدة؟",
        "publish_policy": "auto",
        "min_confidence": "high"
      }'
```

**Poll:**
```bash
curl -sS "https://api.rayhanai.com/internal/blog-post-jobs/<job_id>" \
  -H "Authorization: Bearer $EDITORIAL_SERVICE_KEY"
```

**Rate‑limited (429):**
```json
{ "error": { "code": "RATE_LIMITED", "message": "تم تجاوز الحد المسموح من الطلبات", "status": 429 },
  "detail": "تم تجاوز الحد المسموح من الطلبات" }
```
`Retry-After: 143`, `X-RateLimit-Remaining-Hour: 0`, `X-RateLimit-Remaining-Day: 84`.

---

## 15. Versioning & stability

- **v1.** Field names in the request/result are load‑bearing and stable; new fields may be *added* (be tolerant of unknown fields). Renames/removals would be a new version.
- Error **codes** are stable; error **messages** (Arabic) may be reworded — never parse messages.
- The route is currently under `/internal`. If this API is ever opened to a third party, a versioned prefix (e.g. `/partner/v1/…`) may be introduced; the current path will be kept working or a deprecation window announced.
- Contact the Rayhan backend team for a service key, quota changes, or issues.
