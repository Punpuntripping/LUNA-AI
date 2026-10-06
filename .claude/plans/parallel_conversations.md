# Parallel conversations — up to 5 concurrent runs for `max` + `dev`

Status: PLAN (2026-10-04). Nothing built.

## Goal

`max` and `dev` users can have up to **5 conversations answering at the same
time**. Every other plan stays at **1**. «حتى 5 محادثات متزامنة» is added to the
`max` card's مميزات on the pricing page.

## Where we are today (verified in code)

| Layer | Behavior | Where |
|---|---|---|
| Backend | Dedup is **per conversation only** (`_active_runs[conversation_id]`). Different conversations already run in parallel; a disconnect/Stop **detaches** the pipeline, which runs to completion in the background. **No per-user or per-plan cap exists** — any plan can run N convos today via N tabs. | `backend/app/services/message_service.py:122-131, 620-662, 1366-1401` |
| Frontend | **One global stream slot** (`isStreaming`, `streamingConversationId`, `streamingContent`, `abortController`, reveal buffer, `deepSearchProgress`, reconnect counters, `isAgentRunning`…). | `frontend/stores/chat-store.ts:182-191, 506-580` |
| Frontend | `ChatInput` reads the **unscoped** `isStreaming` → while convo A streams, the composer is locked in **every** convo. This is the "one session stops all sessions" symptom. | `frontend/components/chat/ChatInput.tsx:132, 194, 1023-1042` |
| Frontend | Every send calls `storeStopStreaming()` first → sending in B aborts A's fetch (server keeps A running detached; A's live view is lost, recovered only by polling). | `frontend/hooks/use-chat.ts:113-119` |
| Quota | Gate blocks only when `used >= limit` per window; cost settles AFTER the run → N parallel sends all pass a near-empty window (overshoot). | `shared/quota/__init__.py:497-600` |

A paused run (`agent_question` / `ask_user` awaiting the user's reply) has **no
live task** → it already does not hold a slot, and it will not count toward the
parallel cap.

## Owner decisions

1. Cap: `max` = 5, `dev` = 5, everyone else = 1.
2. **Points reserve (owner rule 2026-10-04):** assume one message can cost at most
   **5 points**. A new send while `k` other runs of the same user are in flight
   needs `remaining >= 5 × (k + 1)` in every limited window.
   Example: 19 pts left → 1st send ok (today's rule), 2nd needs ≥10 ✓, 3rd needs
   ≥15 ✓, 4th needs ≥20 ✗ → only 3 can run together.
   - Ledger check (30d, 427 turns): p50 1.91 · p95 4.63 · p99 6.02 · max 6.65 pts;
     18/427 (4%) exceeded 5. So 5 is a sound reserve, and a burst can still
     overshoot by a little (≤ ~1.7 pts per run in the worst seen case). Accepted.
   - `k = 0` keeps today's rule (`used < limit`) — a single send is unchanged, so
     a user with 3 pts left can still send one message.
   - `used` already contains the partial spend of the in-flight runs, so the
     reserve double-counts slightly → conservative, accepted for simplicity.
   - Unlimited windows (NULL limit, e.g. `dev`) skip the reserve.

## Design

### 1. DB — migration `171_plan_max_parallel_runs.sql`

- `ALTER TABLE plans ADD COLUMN max_parallel_runs int NOT NULL DEFAULT 1 CHECK (max_parallel_runs BETWEEN 1 AND 10);`
- `UPDATE plans SET max_parallel_runs = 5 WHERE plan_id IN ('max','dev');`
- **Do NOT touch `get_user_quota_state`** — changing its RETURNS TABLE would force a
  DROP and a rebuild of `user_subscriptions_live` (137 §2/§3 keep signatures
  byte-identical for exactly that reason). The backend reads the column with a
  separate small query (below), only when it is needed.
- No per-user override (YAGNI — `dev` covers testing). Add one later if asked.
- Verify live after applying (migration-drift rule): `select plan_id, max_parallel_runs from plans`.

### 2. Backend — per-user cap + points reserve

`backend/app/services/message_service.py`
- `_ActiveRun` gains `user_id: str`. The reservation at step 0b passes it.
- New helper `_user_inflight_count(user_id, exclude_conversation_id) -> int`: counts
  `_active_runs` entries for the user whose task is live (`task is None` and not
  stale, or `not task.done()`). Single-worker invariant (`main.py` refuses
  `WEB_CONCURRENCY>1`) makes the in-process count authoritative across tabs/devices.
- Ordering inside the generator, right after the slot is reserved (step 0b) and
  before persistence — same "blocked send writes nothing" rule as the quota gate:
  1. `k = _user_inflight_count(...)`. If `k == 0` → skip straight to the normal gate.
  2. Else look up the cap: `effective_plan_id` (from the same quota-state row the
     gate reads) → `plans.max_parallel_runs` (tiny cached read, 60s TTL like other
     plan lookups). If `k + 1 > cap` → release slot, `_record_unsent(reason='parallel_limit')`,
     emit SSE `parallel_limit` and return.
  3. Pass `inflight_runs=k` into `quota.check(...)`.
- Race: two sends in different convos arriving in the same tick both reserve
  synchronously before any await, so each sees the other in the count → at worst
  both refused, never both admitted past the cap. Acceptable.

`shared/quota/__init__.py`
- `check(..., inflight_runs: int = 0)`. New constant `PARALLEL_RESERVE_POINTS = 5`.
- For each limited ord window: if `inflight_runs == 0` keep `used >= limit`; else
  breach when `limit - used < PARALLEL_RESERVE_POINTS * (inflight_runs + 1)`.
- A reserve breach raises `QuotaExceeded` with a distinct reason flag
  (`reason="parallel_reserve"`) so the client can say «لا يكفي رصيدك لتشغيل محادثة
  إضافية بالتوازي — انتظر انتهاء إحدى المحادثات الجارية» instead of the normal
  "you've hit your limit" copy (the user is NOT out of points).
- `current_usage_report` adds `max_parallel_runs` so the frontend can pre-gate.

New SSE event `parallel_limit`:
```json
{ "detail": "<Arabic>", "limit": 1, "running": 1,
  "running_conversation_ids": ["…"], "upgrade_plan": "max" | null }
```
Arabic copy (plan cap): non-max → «يمكنك تشغيل محادثة واحدة في الوقت نفسه. تتيح
باقة «القصوى» حتى 5 محادثات متزامنة.» · max/dev at 5 → «وصلت إلى الحدّ الأقصى
(5 محادثات جارية). انتظر اكتمال إحداها.»

Tests (`backend/tests/`): cap per plan (free=1, max=5, dev=5); paused run not
counted; stale reservation not counted; detached background run IS counted;
reserve math (19 pts → 3 admitted, 4th refused); `k=0` unchanged; NULL limits
skip reserve; refused send persists nothing + writes `unsent_messages`.

### 3. Frontend — per-conversation stream state (largest piece)

`frontend/stores/chat-store.ts`
- Replace the single slot with `streams: Record<conversationId, StreamState>`:
  `{ messageId, content, abortController, reconnectAttempts, isReconnecting,
  deepSearchProgress, deepSearchSealable, isAgentRunning, runningAgentFamily,
  runningAgentSubtype, quotaInfo? }`.
- Actions take `conversationId`: `startStreaming(cid, msgId)`, `appendToken(cid, t)`,
  `flushStreamBuffer(cid)`, `stopStreaming(cid)`, `finishStreaming(cid)`.
- Reveal buffer (`tokenBuffer`/`revealRafId`, module-level) → one rAF loop that
  drains a per-conversation buffer map.
- Selectors `useIsStreaming(cid)`, `useStreamState(cid)`, `useRunningCount()`.
- `reset()` aborts every controller.

`frontend/hooks/use-chat.ts`
- Remove the unconditional `storeStopStreaming()` supersede; only stop the stream
  of the SAME conversation (regenerate/edit/retry). Each send owns its own
  AbortController and reconnect counter.
- Handle `parallel_limit` like `quota_exceeded`: rehydrate composer text, show banner.

Consumers to migrate (all currently read the global slot):
`ChatInput.tsx` (lock only when THIS convo streams, or when `runningCount >= max_parallel_runs`
with the upgrade/limit note), `MessageList.tsx`, `MessageBubble.tsx`,
`components/analytics/run-tracker.ts`, `workspace/ReferencePanel.tsx`,
`workspace/NoteEditor.tsx`, `install/InstallNudgeCard.tsx` + `stores/install-nudge-store.ts`,
deep-search progress bar, `use-run-visibility`.

Sidebar: a small «جارٍ…» dot on each conversation that has a live stream, so the
user can see their parallel runs (cheap: `Object.keys(streams)`).

`frontend/lib/pricing.ts` — `max.features` add «حتى 5 محادثات متزامنة».

Gates: `npx tsc --noEmit`, `npm run lint`, `npm run build`.

## Rollout order

1. Apply migration 171 (additive; old backend ignores the column). Verify live.
2. Deploy backend (cap + reserve). Non-max plans are now capped at 1 server-side;
   their old frontend already behaves single-stream, so nothing visible changes
   except multi-tab users get a clean `parallel_limit` refusal (old frontend shows
   it as a generic error — acceptable for the short window).
3. Deploy frontend (per-convo streams + pricing copy).
4. Live validation on xl0rch (dev): start 5 deep searches in 5 convos, confirm 6th
   refused, all 5 complete with live tokens in their own convo, switching convos
   never leaks tokens. Then on a free/pro test account: 2nd convo shows the upgrade
   note. Check Logfire for `message.stream.pipeline_detached` spikes.

## Risks

- **Token leakage across convos** — the global slot exists precisely to prevent it;
  the refactor must key every read by `conversationId`. Main test focus.
- **Load** — single uvicorn process, 40-thread pool. 5 parallel deep searches for
  one user is real load; fine at current traffic, and the cap is the first real
  limit we have (today it is unbounded via tabs).
- **Stop does not free a slot** — Stop detaches; the run keeps spending until it
  finishes, so it keeps counting. Honest (it IS still running and billing), but
  the banner must say «انتظر اكتمال إحدى المحادثات الجارية», not imply Stop frees it.
- **Overshoot** — bounded by the reserve; ~4% of turns exceed 5 pts.
