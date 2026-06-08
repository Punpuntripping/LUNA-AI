---
name: diagnose-shot
description: Diagnose an app error from a pasted screenshot — locate the conversation in Supabase, pull the real trace from Logfire, point at the offending code
user_invocable: true
allowed-tools: mcp__supabase__execute_sql, mcp__logfire__query_run, Read, Grep, Glob, Bash, Write
---

# /diagnose-shot — Diagnose an app error from a screenshot

The user hands you a **screenshot of an error/notice from the Luna app** (pasted
inline in this message, or a path in `$ARGUMENTS`). A screenshot has **no
conversation_id** — your job is to turn the pixels into a root cause:

1. **Read** the screenshot → extract the distinctive clues it shows.
2. **Locate** the real conversation in **Supabase** by text-matching the visible message.
3. **Trace** what actually happened in **Logfire** (exceptions, outcome, stack).
4. **Classify** known/expected notice vs real bug, **point at the offending code** (`file:line`), suggest a fix.
5. **Report** inline + write a short report under `agents_reports/error_triage/`.

Division of labor: **Supabase answers "which conversation is this?"** ·
**Logfire answers "what actually broke?"** Use both, in that order. This command
is **read-only** — it never edits app code; it names the file and proposes the fix.

## Argument: $ARGUMENTS

- A **file path** (e.g. `screenshots_temp/Screenshot ….png`) → `Read` that image.
- **Empty** → the screenshot is **attached in this same message**; read it from context.
- May also carry **hints** appended by the user (a date, a username/email, "prod",
  a conversation title). Use them to narrow the searches below.

## Step 1 — Read the screenshot (vision)

Extract every locator you can see and write them down before querying:
- **User message text** — the single most distinctive locator. Copy a short,
  *unusual* phrase (3–6 Arabic words). **Avoid digits** as the anchor — the UI may
  render Arabic-Indic numerals (٤٠٨٠) while the DB stores ASCII, so a numeric `LIKE`
  can miss. Prefer a rare word/phrase.
- **The red banner / error / notice text** — verbatim. This is what you classify in Step 2.
- **Conversation title**, visible **model label** (e.g. a bot name like `ريحان`),
  any **timestamps** ("الآن" = just now; otherwise the shown time), and any visible IDs.
- Whether a reply bubble exists / is empty / is mid-stream.

## Step 2 — Classify the banner FIRST (known notice vs real bug)

Match the banner against the table below **before** assuming a bug. Several red
strings are **expected product behavior**, not failures — calling them bugs is the
main failure mode of this command. Still trace it in Steps 3–4 to *confirm* the
expected path actually fired (e.g. that dedup fired because a first run was live).

| Banner text (Arabic) | SSE event / HTTP | Meaning | Real bug? | Source |
|---|---|---|---|---|
| ما زال يتم إنشاء الرد على رسالتك السابقة وسيظهر هنا حال اكتماله. | `duplicate` | Per-conversation **in-flight dedup**: a 2nd send arrived while the previous turn's pipeline was still running (resend / auto-reconnect re-POST). | **No** — expected | `backend/app/services/message_service.py:263` |
| (a clarifying question appears, stream pauses) | `ask_user` | Planner **paused** for user clarification (e.g. party ambiguity). | **No** — expected pause | `message_service.py:504` |
| (usage/limit payload, stream ends) | `quota_exceeded` | Monthly/usage **quota** hit. | **No** — expected limit | `message_service.py:330`, `shared/quota.py` |
| تم تجاوز الحد المسموح من الطلبات | HTTP 429 `RATE_LIMITED` | **Rate limiter** tripped. | **No** — expected | `backend/app/middleware/rate_limit.py:110` |
| حجم الملف يتجاوز الحد الأقصى (50 ميغابايت) | HTTP 413 | Upload too large. | **No** — expected | `document_service.py` / `workspace.py` / `upload_session_service.py` |
| حدث خطأ أثناء حفظ الرسالة | `error` | Failed to **persist** the user/assistant message row before the pipeline. | **Yes** | `message_service.py:281` |
| حدث خطأ داخلي | `error` | **Internal error** before pipeline start. | **Yes** | `message_service.py:345` |
| حدث خطأ أثناء معالجة الرسالة | `error` | The **pipeline raised** mid-stream. | **Yes** | `message_service.py:606`, `:657` |

If the banner isn't in this table, treat it as **unknown** and rely on the Logfire
exception evidence (Step 4) to classify it. The table is a starting point, not a
closed set — grep the repo (Step 5) for the exact string to confirm its source.

## Step 3 — Locate the conversation in Supabase

Supabase project: **`dwgghvxogtwyaxmbgjod`**. Use `mcp__supabase__execute_sql`.

Text-match the distinctive phrase from Step 1 (escape `'` as `''`):

```sql
select m.message_id, m.conversation_id, m.role,
       left(m.content, 160) as snippet, m.model, m.created_at,
       coalesce(c.title_ar, c.title_en) as title, c.user_id
from messages m
join conversations c on c.conversation_id = m.conversation_id
where m.role = 'user'
  and m.content ilike '%<distinctive phrase>%'
order by m.created_at desc
limit 10;
```

Resolve ambiguity:
- **One row** → that's the conversation. Capture `conversation_id`, `created_at`, `user_id`, `title`.
- **Several rows** → pick by the screenshot's timestamp / title / user hint; if still
  unclear, show the candidates and ask the user which one (one-line question).
- **Zero rows** → the phrase may be paraphrased/truncated in the UI. Retry with a
  shorter or different fragment. Still nothing → fall back: most recent messages
  (optionally filtered by the user/email hint) and confirm with the user, or ask
  them to paste the `conversation_id`. Do **not** guess silently.

Then dump the surrounding thread to see whether a reply was produced, stuck, or empty:

```sql
select message_id, role, left(content, 200) as snippet, model,
       finish_reason, created_at, metadata
from messages
where conversation_id = '<CONV>'
order by created_at desc
limit 12;
```

A user message with **no following assistant row**, or an assistant row with
**empty content**, corroborates a dropped/stuck turn.

## Step 4 — Pull the real trace from Logfire

Project is **`rihan`** — always pass `project="rihan"` to `query_run`. Hard
constraints (same as `/convo-monitor`):
- `query_run` applies a **default 30-minute window**; a SQL time filter is
  *intersected* with it and will NOT widen it. **Always pass explicit
  `start_timestamp`/`end_timestamp`** bracketing the message `created_at` from Step 3
  (e.g. `created_at − 15 min` to `created_at + 25 min`). Max range 14 days.
- **≤ 100 rows per call** regardless of `LIMIT` — paginate with `OFFSET` if needed.
- `conversation_id` is stamped **only on pipeline spans** (`message.stream`,
  `router.classify`, `dispatch.specialist`, `deep_search.*`, `publish.*`, …). Child
  spans (`chat <model>`, `agent run`, HTTP) join by **`trace_id`**, not conversation_id.

**4a — find the turn's trace and outcome:**
```sql
SELECT trace_id, start_timestamp, end_timestamp, duration,
       attributes->>'outcome'    AS outcome,
       attributes->>'task_label' AS task_label
FROM records
WHERE span_name='message.stream'
  AND attributes->>'conversation_id'='<CONV>'
ORDER BY start_timestamp DESC
LIMIT 10
```
Pick the `message.stream` row whose timestamp matches the screenshot. Note its
`trace_id` and `outcome` (e.g. `duplicate`, `ask_user`, `quota_exceeded`, `error`,
`done`) — the outcome usually confirms or refutes the Step-2 classification directly.

**4b — pull exceptions / errors in that trace:**
```sql
SELECT start_timestamp, span_name, service_name, level, is_exception,
       exception_type, exception_message,
       otel_status_code, otel_status_message,
       left(exception_stacktrace, 2500) AS stack
FROM records
WHERE trace_id='<TID>'
  AND (is_exception OR level >= 'error')
ORDER BY start_timestamp
LIMIT 50
```
(Pass the same explicit start/end timestamps.) The deepest exception span — its
`exception_type` + `exception_message` + the top app frame of `stack` — is your root
cause. If there are no exception rows but `outcome` is an error, read the
`message.stream` span's own attributes and the last few child spans to see where it
ended.

**4c — is it a recurring known issue? (optional)**
```sql
SELECT issue_index, issue_label, first_exception_type, first_exception_message,
       issue_state, last_opened_at, latest_trace_id
FROM alert_issues_ext
WHERE issue_state='open'
ORDER BY last_opened_at DESC
LIMIT 20
```
If your `exception_type`/message matches one (or `latest_trace_id` = your trace),
cite the `LF-<issue_index>` label — it's a known, grouped issue, not a one-off.

## Step 5 — Point at the offending code (read-only)

Now name where it breaks, using `Grep`/`Glob`/`Read`:
- Grep the repo for the **exact banner string** (Step 2) to confirm the emit site.
- Grep for the **`exception_message`** text or the **`exception_type`** + the failing
  span's `service_name`/function to find the raising line.
- `Read` a tight window around it and trace the immediate cause (bad input, null,
  external call, schema/validation, etc.).

Output: the `file:line`, a one-paragraph root-cause explanation, and a **concrete,
minimal proposed fix** (or, for an expected notice, a one-liner stating it's working
as designed and *why* it triggered). **Do not edit app code** — propose only.

## Step 6 — Report

Write a short markdown report to
`agents_reports/error_triage/<conv8>_<yyyymmdd-hhmm>.md` (use `<conv8>` = first 8
chars of the conversation_id; get the timestamp from `Bash` `Get-Date -Format
yyyyMMdd-HHmm` — script globals have no clock). Include: the screenshot's banner +
extracted clues, the resolved conversation (id, title, user_id, time), the Logfire
evidence (trace_id, outcome, exception_type/message, `LF-` issue if any), the
`file:line` verdict, and the proposed fix.

Then report back inline, tight:
- **Verdict** — `🔴 real bug` / `🟢 expected behavior` (+ which notice) / `🟡 needs more info`.
- **What the screenshot showed** (banner + the message you matched on).
- **Conversation** — `conversation_id` (short), title, user, time.
- **What actually happened** — `outcome` + `exception_type: message` (or "no
  exception; ended at `<span>`"); cite `LF-<n>` if recurring.
- **Root cause → `file:line`** and the **proposed fix** (one or two lines).
- Path to the written report.

## Rules
- **Read-only.** You only query Supabase + Logfire and write under
  `agents_reports/error_triage/`. Never modify pipeline/app code.
- **Never invent.** Every claim traces to a span, a DB row, or a code line. If the
  screenshot can't be tied to a conversation with confidence, say so and ask for the
  `conversation_id` rather than guessing.
- **Don't cry wolf.** Confirm a red banner is a real failure via the Logfire
  `outcome`/exception before calling it a bug — several banners are expected (Step 2).
- If `query_run` errors with "No project specified", add `project="rihan"`. If a
  Logfire lookback returns nothing, widen the explicit start/end window (older turns
  need a bracket further back), not the SQL filter.
- All user-facing app strings are Arabic — quote them verbatim when citing.
