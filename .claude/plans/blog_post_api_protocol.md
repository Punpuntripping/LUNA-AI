# Rayhan Blog‑Post Generation API — Protocol v2

**Audience:** the marketing / content pipeline (this repo).
**Status:** v2 — **live in production since 2026‑09‑02**.
**Owner:** Rayhan (LUNA) backend. This document is the authoritative external contract; the server implementation lives in `backend/app/api/deepsearch_api/`.

> **What changed in v2, in one line:** a generated post is no longer a private link you send to one person — it is a **published article in a public, browsable blog**, filed under a subject and a type, with a readable Arabic URL. Read §1, §5, §7.1 and §10 even if you knew v1.

---

## 1. What this API does

You POST a single **legal question**. The service runs it through the *same* grounded legal‑research pipeline that powers an in‑app answer (retrieval over the Saudi corpus → reranking → synthesis with `[n]` citations), then writes the result as an **article in the public blog** at `https://rayhanai.com/blog/<arabic-slug>` and returns the full text, a confidence label, and the cited sources.

Generation takes **~1–4 minutes** (hard cap 7 min), so the API is **asynchronous**: submit a job, then poll (or long‑poll).

### ⚠ The three things v1 said that are no longer true

1. **Posts are not private.** They land in the public wing: listed in the `/blog` gallery, on their subject page, and in the sitemap — indexable by Google. `publish_public` defaults to **`true`**.
2. **There is no token.** v1 identified a post by an unguessable 32‑hex `token` and called that "the privacy boundary." This wing has no token; the address is a readable **Arabic slug**. `result.token` is now always `null`.
3. **The article is written differently.** A dedicated editorial prompt masks the questioner's identity and produces an *article* — a headline, a lede, `##` sections, a خلاصة — not a question‑and‑answer. On a vague question it states its assumption in the lede rather than hedging. See §11.

---

## 2. Base URL

| Environment | Base URL |
|---|---|
| Production | `https://api.rayhanai.com` |
| ~~Railway alias~~ | ~~`https://luna-backend-production-35ba.up.railway.app`~~ — **DO NOT USE** |

⚠ **USE `api.rayhanai.com`.** The backend sits behind an **origin lock**: it rejects with `403` every request that did not transit Cloudflare, proven by an `X-Edge-Secret` header the edge injects. Traffic to the raw `*.up.railway.app` hostname bypasses Cloudflare by definition, carries no such header, and **403s — including authenticated calls**. The bearer key will not save it: the key is *authentication*, the lock is a *network boundary*, and `/internal/*` is deliberately not exempt.

This breaks silently from your side — a `403` on a route that worked yesterday, with no deploy on your end.

**In this repo:** `social_media/telegram_api/generate_blogs.py` reads `BLOG_API_BASE` and defaults to `https://api.rayhanai.com`. That default is correct — leave it unset.

All routes below are relative to the base URL. There is no `/api/v1` prefix — these are `/internal/*` service routes. Public links use the web origin `https://rayhanai.com`.

---

## 3. Authentication

```
Authorization: Bearer <EDITORIAL_SERVICE_KEY>
```

- Issued out‑of‑band. It is the **only** security boundary on this surface — treat a leak as full access to publish under the editorial identity, now to a *public* blog.
- **Fail‑closed:** if the server has no key configured, every call returns `401`.
- Constant‑time comparison. Missing, malformed or wrong key → `401`.

---

## 4. Rate limits

A single global budget on **submit** (POST). Polling (GET) and retract are **never** rate‑limited.

| Window | Limit |
|---|---|
| Rolling hour | **100** new submissions |
| Rolling day | **300** new submissions |

- API‑wide (one internal caller), not per‑IP.
- **Idempotency replays and polls are free** — only a genuinely new `idempotency_key` spends budget.
- Breach → `429` with `Retry-After` and `X-RateLimit-Remaining-Hour` / `-Day`. Successful `202`s carry the same headers so you can self‑throttle.
- The limiter **fails open** if Redis is down — it is a cost cap, not a security control. Rely on your own `idempotency_key` discipline.

Concurrency: the server drains at most 2 generations at once. A burst is accepted immediately and drains over time.

---

## 5. Core concepts

- **Async job.** `POST` creates a job (`queued`) and returns. `GET` until terminal (`completed` / `failed`). `?wait=N` long‑polls up to N seconds.
- **Idempotency — now enforced by the database.** One job publishes **at most one** blog. See §9; this is stronger than v1 and worth reading.
- **Type** — what the article *is doing*: `laws_explanation`, `judicial_research`, or `compliance`. It drives the badge, the filter, and (via §11) how the question is researched. It is about intent, not subject matter.
- **Subjects** — the browse axis. A closed, curated vocabulary of **English slugs**; a blog may carry several. Unknown slug ⇒ **400**, never a silent drop.
- **Slug** — the article's permanent Arabic URL segment. See §12.
- **Versioning** — the wing is versioned. Publishing creates v1; later edits append versions and the slug always serves the current one.
- **Review state** — every blog carries `review_status` (`pending` / `approved`). ⚠ **Not enforced yet:** nothing filters on it, so a `pending` blog is publicly visible today. Do not treat it as a gate.

---

## 6. Endpoints

### 6.1 Submit a job

```
POST /internal/blog-post-jobs[?wait=N]
Authorization: Bearer <key>
Content-Type: application/json; charset=utf-8
```

`wait` (optional): seconds to long‑poll, `0`–`60` (clamped). Resolves inline → terminal status body (`200`); otherwise `202`.

| Code | When |
|---|---|
| `202` | New job accepted |
| `200` | Idempotency replay, or `?wait` resolved |
| `400` | Missing `question`/`idempotency_key`; bad enum; **unknown subject slug**; **unmintable slug** |
| `401` | Bad/missing key |
| `429` | Rate limited |

> **Do not branch on the POST body beyond `job_id`.** Always poll `GET` (or use `?wait`) for the final `result`.

### 6.2 Poll a job

```
GET /internal/blog-post-jobs/{job_id}
```

`200` status body · `401` bad key · `404` unknown job (`BLOG_JOB_NOT_FOUND`) or non‑UUID (`INVALID_UUID`). Never rate‑limited; poll every ~3 s.

### 6.3 Retract a published blog — **new in v2**

```
POST /internal/public-blogs/{root_id}/retract
Authorization: Bearer <key>
```

```json
{ "root_id": "21c2c982-…", "is_public": false }
```

**Delists only.** The blog leaves the gallery, its subject page and both sitemap sections, and the page starts serving `noindex` — but **the URL keeps resolving** for anyone already holding the link, and the article is not deleted. That is deliberate: retraction is "take it out of the shop window," not "unpublish."

Address it by **`root_id`** (the logical blog), not `post_id`. Not owner‑scoped — the service key is the authority.

⚠ Delisting removes it from Google's *sitemap* and marks it `noindex`, but a page Google already crawled can linger in the index for days. Retract is not an emergency erase.

---

## 7. Schemas

### 7.1 Request body (`POST`)

| Field | Type | Req? | Default | Meaning |
|---|---|---|---|---|
| `idempotency_key` | string | **yes** | — | Stable dedup key. Same key ⇒ same job ⇒ **same blog**. |
| `question` | string | **yes** | — | Anonymized, self‑contained Arabic question. **No client PII** — the editorial prompt de‑identifies the *framing*, not your facts. |
| `title` | string | no | `null` | Article headline. When null, the engine's own H1 is lifted out of the body and used. |
| `type` | string | no | `null` | `laws_explanation` \| `judicial_research` \| `compliance`. **Send it** — it drives the badge and the filter. |
| `subjects` | string[] | no | `[]` | Subject slugs, e.g. `["work-law"]`. Unknown slug ⇒ 400. Empty is allowed today (the vocabulary is young) but leaves the article unfiled in the browse tree. |
| `slug` | string | no | `null` | Arabic URL segment. Minted from the title when null. **Permanent once published** (§12). |
| `publish_public` | bool | no | **`true`** | Land the article in the public gallery + sitemap. `false` ⇒ unlisted‑but‑reachable. |
| `mode` | string | no | `null` | `case_led` \| `reg_compliance_led` \| `full` \| `null`. Pins retrieval; `null` ⇒ the planner decides (§11). |
| `support` | bool | no | `null` | Pins the support executor. **`null` ≠ `false`** — see §11. |
| `editorial_voice` | bool | no | `true` | Use the editorial prompt (article form, masked identity). `false` gives the in‑app answer shape. |
| `publish_policy` | string | no | `"auto"` | `auto` \| `always` \| `never` (§10). |
| `min_confidence` | string | no | `"medium"` | `high` \| `medium` \| `low` — the `auto` threshold. |
| `subtype` | string | no | `"marketing_telegram"` | Free tag for filtering your own posts. |
| `metadata` | object | no | `{}` | Opaque provenance on the job row. Never surfaced publicly. |
| `callback_url` | string | no | `null` | Best‑effort completion POST (§14). |
| `display_mode` | string | no | `"question"` | **Legacy — ignored by this wing.** It belonged to the old token‑link path. |
| `language` | string | no | `"ar"` | Informational. |

Unknown fields are ignored. An entirely absent required field yields a non‑Arabic `422`; an empty one yields the Arabic `400`.

### 7.2 Submit response

```json
{ "job_id": "6439de2a-…", "status": "queued",
  "status_url": "https://api.rayhanai.com/internal/blog-post-jobs/6439de2a-…" }
```

### 7.3 Status response (`GET`, and terminal `?wait`)

```json
{
  "job_id": "6439de2a-…",
  "status": "completed",
  "result": {
    "root_id": "21c2c982-…",
    "post_id": "21c2c982-…",
    "token": null,
    "slug": "وقف-تنفيذ-حكم-العامل-النهائي-…",
    "url": "https://rayhanai.com/blog/وقف-تنفيذ-حكم-العامل-النهائي-…",
    "is_public": true,
    "is_published": true,
    "confidence": { "label": "medium", "score": 0.6, "reasons": ["15 مرجع مستشهد به", "8 مرجع عالي الصلة"] },
    "title": "وقف تنفيذ حكم العامل النهائي لحين الفصل في دعوى التعويض المقابلة لصاحب العمل",
    "question_text": "…", "summary": "…", "content_md": "…",
    "references": { "count": 15, "top": [{ "n": 1, "title": "اللائحة التنفيذية لنظام التنفيذ", "relevance": "high" }] },
    "workspace_item_id": "…", "created_at": "2026-09-02T18:29:51Z"
  }
}
```

| Field | Meaning |
|---|---|
| **`root_id`** | **The logical blog. Store this** — every later call (retract, and future edit endpoints) addresses it, and it survives version changes. |
| `post_id` | The uuid of the **version** that was written. Equals `root_id` on v1. Not stable across edits. |
| `token` | **Always `null`** in this wing. Retained for v1 compatibility. |
| `slug` | Permanent Arabic URL segment. |
| `url` | `https://rayhanai.com/blog/<slug>`. Resolves iff `is_published`. |
| `is_public` | In the gallery + sitemap. `false` ⇒ unlisted but still reachable. |
| `is_published` | Reachable at all. `false` ⇒ the URL 404s. |
| `confidence` | `{ label, score?, reasons[] }` — your outreach gate. |
| `content_md` | Full article, Markdown, inline `[n]`. Returned even when unpublished. |
| `references` | `{ count, top: [{ n, title, relevance }] }`. |
| `workspace_item_id` | Internal artifact id. `null` if no grounded analysis was produced. |

### 7.4 Failed‑job error

```json
{ "job_id": "…", "status": "failed",
  "error": { "code": "generation_timeout", "message": "…", "retryable": true } }
```

| `error.code` | retryable | Action |
|---|---|---|
| `generation_timeout` | yes | Exceeded the 7‑min cap. Re‑submit with a **new** key. |
| `generation_failed` | yes | Pipeline raised. New key; if it persists, contact us. |
| `generation_interrupted` | yes | Server restarted and the retry budget ran out. New key. |
| `configuration_error` | no | Server not provisioned. **Do not retry.** |

> A **low‑confidence** result is not a failure — it returns `completed` with `is_published: false`.

---

## 8. Job status lifecycle

```
queued → processing → completed
                    ↘ failed
```

Terminal states do not change afterward.

---

## 9. Idempotency & retries

- Supply a **stable `idempotency_key`** per logical question (a UUID you persist, or a hash of the normalized question).
- Re‑submitting the same key returns the **existing** job. No double generation, no double charge, no rate budget spent.
- To force a fresh generation, submit a **new** key.

### ⚠ Why this is stronger in v2 — and why you should not rely on the slug

v1 deduped on the job. A bug found on the first live run showed that was not enough: **one POST published two articles**, sixty seconds apart, with different slugs. The publish step's only guard was slug uniqueness, and the slug comes from an LLM‑written headline — so a re‑drive produced a *different* headline, took a *different* slug, and sailed straight past the check.

Dedup now lives on the **job id**, enforced by a unique index in the database, and the job check is asked *before* the slug check. One job → one blog, permanently.

**What this means for you:** re‑POSTing the same key is safe. Do **not** implement your own "does this slug already exist?" check as a dedup — it is not a stable identity. Key everything on `idempotency_key` and `root_id`.

---

## 10. Publishing, visibility & confidence

**Confidence** reflects how well‑grounded the answer is:

| Label | ~score | Meaning |
|---|---|---|
| `high` | 0.85 | Strongly grounded. |
| `medium` | 0.60 | Grounded but thinner — review before amplifying. |
| `low` | 0.30 | Weak, or no grounded analysis was produced. Don't publish. |

**Two independent flags** decide what a reader can do:

| Flag | Governed by | `true` | `false` |
|---|---|---|---|
| `is_published` | `publish_policy` + `min_confidence` | URL resolves | URL **404s** — a real draft |
| `is_public` | `publish_public` | In gallery + subject page + sitemap | **Unlisted but reachable** by direct link |

| `publish_policy` | Result |
|---|---|
| `auto` (default) | `is_published = (confidence ≥ min_confidence)`. Ranks `low < medium < high`. |
| `always` | Always published. |
| `never` | Always a draft. |

With defaults (`auto`, `medium`, `publish_public: true`): a `high` or `medium` article publishes **publicly and indexably**; only `low` stays a draft. You still get the full `content_md` back either way.

⚠ **`publish_public` defaults to `true`.** In v1 nothing you generated was public. In v2, a bare submit puts an article on the open internet. Send `"publish_public": false` if you want to review first.

---

## 11. Type → retrieval mode

`mode` and `support` pin how the question is researched. Both are optional; **both set** skips the planner's decision step entirely, **either absent** lets the planner decide and overlays whatever you supplied.

| `type` | `mode` | `support` | Why |
|---|---|---|---|
| `compliance` | `reg_compliance_led` | operator's call | A standing obligation. The regulatory executor already spans statute *and* the procedural/services side. |
| `judicial_research` | `full` | `null` | A موقف or a مبدأ only means something against the نظام it construes — it needs case law **and** regulatory ground. |
| `laws_explanation` | `null` | `null` | Genuinely varies per article; let the planner choose. |

⚠ **`support: null` is not `support: false`.** `null` means "not pinned — decide for me"; `false` means "pinned off, do not run the support executor." Sending `false` when you meant "unset" silently produces a thinner article with nothing in the response to say so. When `mode` is `full`, `support` is ignored — send `null`.

**The three types describe intent, not subject.** The same subject carries all three: سند لأمر *as a term to explain* is `laws_explanation`; a fight over one before قاضي التنفيذ is `judicial_research`. `compliance` is the preventive register — something to hold in mind **even when nothing has happened to you yet** (السعودة، التوطين، اشتراطات المطاعم).

---

## 12. Slugs & URLs

- The article's address is an **Arabic** slug: `https://rayhanai.com/blog/وقف-تنفيذ-حكم-العامل-…`. Percent‑encode it when building links programmatically.
- **Subject** slugs are **English** (`work-law`, `promissory-note`, `saudization`). This is enforced by the database, not by convention: a blog slug shaped like a subject slug is **rejected**, which is what makes `/blog/{ref}` unambiguous.
- ⚠ **An English `title` cannot mint a slug.** It would produce an ASCII‑kebab string, which is the subject shape and therefore refused. Send an Arabic title, or an explicit Arabic `slug`. This is now checked **at submit** (a free 400) rather than after generation — but it is worth not tripping.
- ⚠ **A published slug is permanent.** There is no redirect layer: renaming 404s the old URL. Editing an article may change its title; it must never change its slug.

---

## 13. HTTP error envelope

```json
{ "error": { "code": "VALIDATION_ERROR", "message": "السؤال مطلوب", "status": 400 },
  "detail": "السؤال مطلوب" }
```

| HTTP | `code` | Cause |
|---|---|---|
| `400` | `VALIDATION_ERROR` | Empty `question`/`idempotency_key`; bad enum; **unknown subject slug**; **unmintable/colliding slug** |
| `401` | `AUTH_INVALID` | Missing/wrong key, or server key unset |
| `404` | `BLOG_JOB_NOT_FOUND` | Unknown `job_id` |
| `404` | `INVALID_UUID` | Path param is not a UUID |
| `429` | `RATE_LIMITED` | Hour or day cap. Honour `Retry-After` |
| `500` | `INTERNAL_ERROR` | Unexpected failure. Retry with backoff |
| `422` | *(FastAPI default, non‑Arabic)* | Required field entirely absent or wrong JSON type |

Branch on `error.code` (stable), never on `message` (Arabic wording may change).

---

## 14. Completion callback (optional)

Pass `callback_url` and the server best‑effort POSTs the terminal body there (10 s timeout, no retries, failures swallowed). It is a latency optimization, **not** a guarantee — poll as the source of truth and make the handler idempotent.

---

## 15. ⚠ Encoding — a trap that costs a whole generation

Send the request as **UTF‑8 bytes with an explicit charset**, and build the JSON in code — not on a shell command line.

Passing Arabic through `curl -d '…'` on Windows (Git Bash) transcodes the question to literal `?????` **before curl sees it**. The API accepts it, the pipeline researches a string of question marks, and you get back a confident, well‑formed article **on an unrelated topic**, with no error anywhere and a full generation billed. This happened during v2 testing and took a while to spot precisely because the output looked fine.

```python
raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(url, data=raw, method="POST", headers={
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json; charset=utf-8",
})
```

Sanity check before submitting: round‑trip your own payload (`json.loads(raw.decode())["question"]`) and eyeball the Arabic.

---

## 16. Recommended client flow

1. Compute a stable `idempotency_key`.
2. `POST` with `question`, `type`, `subjects`, and the §11 `mode`/`support` for that type. Optionally `?wait=30`.
3. Read `job_id` — **and store `root_id` from the result**, since retract and future edits address it.
4. Poll `GET` every ~3 s until terminal (or await the callback).
5. On `completed`: check `confidence.label` and `is_published`. If it clears your bar, use `url` + `summary`.
6. On `failed`: if `retryable`, re‑submit with a **new** key.

```python
import hashlib, json, time, urllib.request

BASE = "https://api.rayhanai.com"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json; charset=utf-8"}

def submit(question, *, type_, subjects, mode=None, support=None, publish_public=True):
    body = {
        "idempotency_key": hashlib.sha256(question.strip().encode()).hexdigest(),
        "question": question,
        "type": type_,
        "subjects": subjects,
        "mode": mode,              # §11 — None means "planner decides"
        "support": support,        # None ≠ False
        "editorial_voice": True,
        "publish_public": publish_public,
        "subtype": "marketing_telegram",
    }
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")   # §15
    req = urllib.request.Request(f"{BASE}/internal/blog-post-jobs", data=raw, method="POST", headers=H)
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())

def poll(job_id):
    req = urllib.request.Request(f"{BASE}/internal/blog-post-jobs/{job_id}", headers=H)
    while True:
        with urllib.request.urlopen(req, timeout=30) as r:
            s = json.loads(r.read().decode())
        if s["status"] in ("completed", "failed"):
            return s
        time.sleep(3)

job = submit("…سؤال بالعربية…", type_="judicial_research", subjects=["work-law"],
             mode="full", support=None)
res = poll(job["job_id"])
if res["status"] == "completed":
    r = res["result"]
    print(r["root_id"], r["url"], r["confidence"]["label"], r["is_public"])
```

---

## 17. Versioning & stability

- **v2.** Request/result field names are load‑bearing and stable; new fields may be *added* — be tolerant of unknown fields.
- v1 fields are retained for compatibility. `token` is now always `null` and `display_mode` is ignored by this wing; neither has been removed.
- Error **codes** are stable; Arabic **messages** may be reworded — never parse messages.
- Routes live under `/internal`. If this API is opened to a third party, a versioned prefix may be introduced with the current path kept working or a deprecation window announced.
- Contact the Rayhan backend team for a service key, quota changes, subject‑vocabulary additions, or issues.
