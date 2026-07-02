# ريحان Marketing → LUNA: Blog-Post Generation API

**Status:** Request / proposed contract — for the LUNA team to review, correct, and implement.
**Owner (requester):** Rayhan Marketing (`C:\Programming\marketing`)
**Audience:** LUNA app team (`C:\Programming\LUNA_AI`)
**Backend:** Supabase project `Legal_AI` (`dwgghvxogtwyaxmbgjod`)

---

## 1. The ask in one line

Give us **one authenticated endpoint** that takes a **legal question** and returns a **finished, published `blog_posts` row** — i.e. it runs the same answer generation a user gets in-app, then performs the same "مشاركة" (share) that creates a blog post, and hands us back the **public URL (`rayhanai.com/blog/<token>`)** plus the answer text and a **confidence label**.

Today this only happens when a human user generates a `workspace_item` and clicks **مشاركة**. We need that exact result reachable **programmatically, start-to-end, from a single call**.

---

## 2. Why we need it

Marketing is building a daily content + outreach pipeline:

1. Pull yesterday's questions from Saudi-lawyer Telegram groups.
2. An agent (DeepSeek V4 Flash) selects 1–5 high-value, evergreen questions.
3. **→ Call this API** to turn each into a published Q&A blog post.
4. Gate on the returned **confidence** (publish/outreach only when `high`).
5. Prepare a 1:1 message — «بخصوص سؤالك… [snippet]… لمشاهدة الرد كاملًا: <link>» — for **manual** sending (no auto-send).

Step 3 is the only piece we can't build on our side, because the answer quality comes from **your** generation engine and **your** regulations corpus (the internal `reg:` citations). We don't want to re-implement it with web search — we want the real thing.

---

## 3. How this maps to your existing system (please correct anything wrong)

As we read the DB:

- A post lives in **`public.blog_posts`**; the public route is **`rayhanai.com/blog/<token>`** where `token` = `encode(gen_random_bytes(16),'hex')`.
- `blog_posts.owner_user_id` → `public.users.user_id` (NOT NULL).
- The answer content originates in **`public.workspace_items`** (`content_md`, `title`) with references in **`public.workspace_item_references`**.
- **مشاركة** copies a `workspace_item` into `blog_posts` (sets `content_md`, `title`, builds `references_json`, `source_item_id = item_id`, `subtype = 'legal_synthesis'`, `display_mode = 'question'`, `is_published = true`, auto `token`). There is **no DB RPC** for this today — it appears to be a client-side insert.

**So the API = "generate a WI for this question as a bot user" + "do the share insert" + "return the post."** We can replicate the share insert ourselves via service-role SQL if you prefer, but we'd rather you own the whole thing so the citations and logic stay identical to organic posts.

---

## 4. Endpoint & transport

Because legal synthesis is slow, we propose an **async job** model. (If your generation reliably finishes in a few seconds, a synchronous version is fine too — see §11.)

| | |
|---|---|
| Submit | `POST /v1/blog-post-jobs` → `202 { job_id, status:"queued", status_url }` |
| Poll | `GET /v1/blog-post-jobs/{job_id}` → status + result |
| Webhook (optional) | if request has `callback_url`, POST the final result there on completion |
| Sync convenience (optional) | `POST /v1/blog-post-jobs?wait=30` long-polls up to 30s, returns result inline if ready |
| Auth | `Authorization: Bearer <service_key>`; the key identifies the **editorial bot user** that owns the posts |
| Versioning | `/v1` prefix |

We only run ~1–5 calls/day, so polling is acceptable; a webhook is a nice-to-have.

---

## 5. Request body (what we send)

```jsonc
{
  "idempotency_key": "tg:AMFDT_WORK:386505",   // REQUIRED. Stable per source question → retries never duplicate a post
  "question": "ما التصنيف الصحيح لدعوى استرداد رأس المال في شراكة لم تُصفَّ، وهل تُطلب التصفية ومحاسبة الشريك أم استرداد الحصة مباشرة؟ وهل يُندب خبير لحصر الأصول؟",
                                                // REQUIRED. Anonymized, self-contained. Becomes question_text + drives generation
  "title": null,                               // optional; engine writes one if null
  "display_mode": "question",                  // → blog_posts.display_mode
  "subtype": "marketing_telegram",             // tag so marketing posts are distinguishable from organic
  "language": "ar",
  "publish_policy": "auto",                    // auto | always | never  (auto = publish iff confidence ≥ min_confidence)
  "min_confidence": "high",
  "metadata": {                                // provenance; please store on the WI and/or echo back
    "source": "telegram",
    "group": "AMFDT_WORK",
    "message_id": 386505,
    "campaign": "tg_2026_07",
    "question_raw": "…original text, our records only…"
  },
  "callback_url": "https://…/webhooks/blog"    // optional
}
```

Required: `idempotency_key`, `question`. Everything else has defaults.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `idempotency_key` | string | ✅ | Dedupe key; same key returns the existing post, never a new row |
| `question` | string | ✅ | Anonymized question to answer + publish (`question_text`) |
| `title` | string\|null | | Optional title; engine generates if null |
| `display_mode` | enum | | `question` (default) |
| `subtype` | string | | Defaults to your choice; we suggest `marketing_telegram` |
| `language` | string | | `ar` |
| `publish_policy` | enum | | `auto`\|`always`\|`never` |
| `min_confidence` | enum | | `high`\|`medium`\|`low` threshold for `auto` |
| `metadata` | object | | Free-form provenance, stored on the WI |
| `callback_url` | string | | Optional completion webhook |

---

## 6. Response (what we need back)

### `GET /v1/blog-post-jobs/{job_id}` — completed

```jsonc
{
  "job_id": "job_01H…",
  "status": "completed",                       // queued | processing | completed | failed
  "result": {
    "post_id": "uuid",
    "token": "9f3a…(32 hex)",
    "url": "https://rayhanai.com/blog/9f3a…",   // we drop this in the outreach message
    "is_published": true,                       // false if confidence < min_confidence under auto
    "confidence": {
      "label": "high",                          // high | medium | low  ← we gate on this
      "score": 0.86,                            // optional 0–1
      "reasons": ["7 high-relevance refs", "يغطي نظام الشركات + المرافعات"]
    },
    "title": "تكييف دعوى استرداد رأس المال في الشراكة غير المُصفّاة",
    "question_text": "…",
    "summary": "خلاصة من 2–3 أسطر جاهزة للاقتباس في رسالة التواصل.",  // SHORT — the message snippet
    "content_md": "## التكييف الصحيح\n…full answer with [1][2] markers…",
    "references": { "count": 7, "top": [{ "n": 1, "title": "نظام الشركات", "relevance": "high" }] },
    "workspace_item_id": "uuid",
    "created_at": "2026-07-01T…Z"
  }
}
```

### failed

```jsonc
{ "job_id":"…", "status":"failed",
  "error": { "code":"generation_failed", "message":"…", "retryable": true } }
```

**Fields we depend on:** `url`, `confidence.label`, `summary` (or `content_md` to cut our own snippet), `is_published`, `post_id`. The rest is useful but optional.

---

## 7. Behavioral requirements (what the API must do internally)

1. Resolve the **bot/editorial user** from the API key; use it as `owner_user_id`.
2. Generate the answer **through the same pipeline as an in-app question** so `content_md` + references are grounded in the regulations corpus (internal `reg:` citations), not web sources.
3. Compute a **confidence** score/label (you have the signals — reference count/relevance, coverage).
4. Perform the **share insert** into `blog_posts` from the generated WI: copy `content_md`, `title`; build `references_json`; set `source_item_id`, `subtype`, `display_mode`, and `is_published` per `publish_policy`/`min_confidence`; let `token` default.
5. Honor **idempotency**: same `idempotency_key` → return the existing post.
6. Return the result (and POST `callback_url` if provided).

---

## 8. Confidence & publish semantics

- `publish_policy:"auto"` → set `is_published = (confidence.label ≥ min_confidence)`. Otherwise create it as an unpublished draft (still return `url`).
- `"always"` / `"never"` → force published / draft regardless.
- **Either way, always return `confidence` and `content_md`** — a low-confidence result is not an error; we simply won't publish/outreach it, but we still want to see it.

---

## 9. Errors, idempotency, limits

- Standard HTTP codes: `202` accepted, `200` on poll, `400` validation, `401` auth, `409`/replay on idempotency, `429` rate limit, `5xx` generation failure.
- Error object: `{ code, message, retryable }`.
- Idempotency keys retained ≥ 30 days.
- Tell us your **rate limit** and **max concurrency** so we pace the daily batch.

---

## 10. Security & non-functional

- Service-to-service Bearer key, rotatable; scope it to this endpoint only.
- No PII from us beyond the (already anonymized) question + provenance metadata; please don't surface `metadata.question_raw` publicly.
- Target latency: whatever a normal WI generation takes — just tell us the number.

---

## 11. Scope boundary — what marketing handles (so you don't)

- Selecting questions and **anonymizing** them (stripping case-identifying facts) before we call you.
- Generating the `idempotency_key`.
- Gating outreach on `confidence.label` and writing/sending the 1:1 messages (manual send on our side).

You only own: question → published post → response.

---

## 12. Open questions we need you to answer

1. Typical **generation time** and **max concurrency** for one question.
2. Can you expose a **confidence** score/label, or should we derive it from `references` relevance counts?
3. Will you **provision the editorial bot user** (`public.users` row + auth) mapped to the API key, or should we create it?
4. Preferred **`subtype`** value for marketing posts (`marketing_telegram`, or keep `legal_synthesis`?).
5. Async jobs OK, or do you prefer a single **synchronous** endpoint (generation fast enough)?
6. Anything in the share flow beyond the columns above (e.g. SEO fields, slug, indexing, listing visibility) we should set?

---

## 13. Acceptance criteria

- `POST` a known question → within the agreed time, `GET` returns `status:"completed"` with a resolvable `url`, non-empty `content_md` with `[n]` citation markers, ≥1 `references`, and a `confidence.label`.
- Opening `url` shows a Q&A post styled identically to an organic shared post.
- Re-`POST` with the same `idempotency_key` → returns the **same** `post_id`/`url`, no duplicate row.
- A deliberately weak/unanswerable question → `completed` with `confidence.label:"low"` and `is_published:false` under `publish_policy:"auto"`.

---

## 14. Appendix — `blog_posts` column mapping

| `blog_posts` column | Source |
|---|---|
| `token` | default (auto) |
| `owner_user_id` | editorial bot user (from API key) |
| `source_item_id` | generated `workspace_items.item_id` |
| `subtype` | request `subtype` |
| `question_text` | request `question` |
| `title` | request `title` or engine-generated |
| `content_md` | from generated WI |
| `references_json` | built from `workspace_item_references` |
| `display_mode` | request `display_mode` (`question`) |
| `is_published` | per `publish_policy` / `min_confidence` |
