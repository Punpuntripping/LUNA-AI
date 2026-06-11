# Reliability Hardening — Master Implementation Plan

**Date:** 2026-06-11
**Source audit:** `agents_reports/reliability_audit_2026-06-11.md` (~50 endpoints, 5+2 audit agents)
**Designs:** 6 parallel design agents, one per domain — full code-level designs in this folder:

| Doc | Domain | Headline |
|---|---|---|
| `design_1_foundation.md` | Event loop + timeouts | `run_db()` to_thread wrapper (NOT async-client migration), httpx timeouts on postgrest/storage sessions, 40-thread executor |
| `design_2_sse_pipeline.md` | Messages/SSE | Producer-side 420s pipeline timeout, OCR 60s timeout + Arabic status, loud resolve_pause + stale-pause guard, publish-failure surfacing, WEB_CONCURRENCY boot guard |
| `design_3_auth_infra.md` | Auth + infra | ResilientJWKClient (last-known-good keys), GoTrue exception ladder (503 vs 401, no string-matching), Redis supervisor task, SERVICE_UNAVAILABLE error code |
| `design_4_quota_billing.md` | Quota + billing | WindowUsage fail-closed-on-unknown, settle-only-if-ledger-landed, batched rehydration, ingest timeout+concurrency cap, summarizer attempt-marker + NULL-sweep |
| `design_5_storage_upload.md` | Storage + uploads | Range-request magic bytes, insert-first legacy upload (reconciler-covered), workspace DELETE cleanup, startup reconciler catch-up |
| `design_6_data_integrity.md` | Data integrity | Migrations 065/066 (transactional RPCs + case_counts + merge_preferences), N+1 kill, pagination, references semaphore, silent-failure sweep |

---

## Cross-domain conflict resolutions (binding)

These override anything contradictory inside individual design docs.

1. **`shared/db/client.py` is owned by design 1 (foundation).** Design 5's storage-timeout values fold into foundation's `_harden_sessions`: postgrest `httpx.Timeout(connect=5, read=15, write=15, pool=5)`; storage `httpx.Timeout(connect=5, read=60, write=60, pool=5)` + `ClientOptions(postgrest_client_timeout=..., storage_client_timeout=60)` as belt-and-suspenders. 60s storage (not 10) until legacy single-shot upload is deprecated.
2. **Count-failure semantics: design 6 wins over design 3's Optional[int] proposal.** The `case_counts` RPC replaces the per-case helpers; a counts failure on the (now single) batched query propagates as 500. Response models (`conversation_count: int`) unchanged. Design 3's sweep table has been amended accordingly.
3. **`backend/app/main.py` lifespan accumulates changes from four domains.** Final lifespan order:
   1. `loop.set_default_executor(ThreadPoolExecutor(max_workers=40))` (design 1) — first.
   2. WEB_CONCURRENCY > 1 boot guard (design 2 fix 5a) — right after `get_settings()`.
   3. Supabase clients + pricing cache (existing).
   4. JWKS pre-warm `create_task` (design 3) — non-blocking.
   5. Redis supervisor task replaces the current ping-once block (design 3) — startup no longer gated on Redis.
   6. Scheduler: existing 03:00 + 03:15 jobs, NEW 03:30 summary-NULL sweep (design 4), NEW startup reconciler DateTrigger +60–90s (design 5). All `add_job` calls before `scheduler.start()`.
   7. NEW exception handler: `DbDeadlineExceeded` → 503 `SERVICE_UNAVAILABLE` (design 1).
   8. Shutdown: cancel supervisor + close the Redis **singleton** (not `app.state.redis`, which may be None mid-outage).
4. **`message_service.py` is touched by designs 1, 2, and 4.** Land in this order to keep diffs reviewable: design 2 fix 1 (producer timeout) → design 4 fix 1 (QuotaUnavailable arm) → design 1 phase 1 (run_db wraps + **dedup-slot reordering**, the one non-mechanical edit; reserve the `_active_runs` slot synchronously immediately after the dedup check, before the first `await run_db`).
5. **One canonical Arabic outage string**, module-level in `backend/app/errors.py`: `MSG_SERVICE_UNAVAILABLE = "الخدمة غير متاحة مؤقتاً، حاول مجدداً"` — used by auth, deps, DbDeadlineExceeded handler, and storage 503s.
6. **`orchestrator.py` is touched by design 2 fixes 2/3/4** — three separate commits (OCR surfacing; resolve_pause rename ×10 + stale guard; publish except blocks).

---

## Wave plan

### Wave R0 — Foundation + migrations (no behavior change beyond timeouts firing)
*Agents: @shared-foundation, @sql-migration. Est: 1 day + 1-week soak on timeouts.*

- [ ] `shared/db/run.py` (new): `run_db`, `run_db_deadline`, `DbDeadlineExceeded` (design 1)
- [ ] `shared/db/client.py`: `_harden_sessions` (postgrest session-swap + fresh `SyncStorageClient`), `ClientOptions` fallback timeouts, harden the **anon client too** (currently unhardened) (designs 1+5)
- [ ] `backend/app/main.py`: executor sizing, `DbDeadlineExceeded` → 503 handler, `ErrorCode.SERVICE_UNAVAILABLE` + `MSG_SERVICE_UNAVAILABLE` in errors.py
- [ ] Pin `supabase>=2.28,<2.29`
- [ ] **Live-schema pre-flight** via Supabase MCP (design 6 §pre-flight), then apply migrations **065** + **066**; smoke-test all four RPCs with `execute_sql`. Verify `llm_calls(user_id, created_at)` index exists (design 4).
- [ ] Soak gate: watch Logfire for `httpx.ReadTimeout` on legitimate slow queries for several days before Wave R1.

### Wave R1 — Chat hot path (the highest-value wave)
*Agents: @sse-streaming, @fastapi-backend. Est: 2–3 days.*

Order within the wave (per conflict resolution 4):
- [ ] OCR timeout: `mistral_ocr.py` wait_for(60), `runner.py` returns `OcrExtractionStats`, orchestrator 180s step budget + Arabic status event (design 2 fix 2)
- [ ] Pipeline timeout: `LUNA_PIPELINE_TIMEOUT_S=420` setting + producer-side `asyncio.timeout` + placeholder cleanup + `PIPELINE_TIMEOUT` SSE error (design 2 fix 1)
- [ ] `QuotaUnavailable` except arm in send_message_stream (design 4 fix 1 consumer side)
- [ ] `run_db` wraps: `messages.py` pre-flight + send_message_stream inline callsites + **dedup-slot reordering** (design 1 phase 1) — careful review required
- [ ] WEB_CONCURRENCY boot guard (design 2 fix 5a)
- [ ] Verification: hung-LLM simulation (`LUNA_TEST_HANG_S` env gate), pause non-interaction test, head-of-line load test (design 1 §verification 3)

### Wave R2 — Auth + infra resilience
*Agents: @fastapi-backend, @shared-foundation. Est: 2 days.*

- [ ] `ResilientJWKClient` + `prewarm_jwks` + decode retry-on-InvalidSignature + `AuthUnavailableError` (design 3 fix 1)
- [ ] `deps.get_current_user`: to_thread + 503 arm (ordered before the AuthError catch)
- [ ] GoTrue ladder in auth.py: `_gotrue_call` helper, login/refresh/logout rewrites, **delete the string-matching block** (design 3 fix 2)
- [ ] Redis supervisor replacing the ping-once block; shutdown fixes (design 3 fix 3)
- [ ] `run_db` wraps on auth routes (design 1 phase 2)
- [ ] **Frontend coordination (blocking for deploy):** frontend must treat `/refresh` 503 as transient (retain tokens, retry) — NOT as logged-out. New `SERVICE_UNAVAILABLE` + `QUOTA_UNAVAILABLE` + `PIPELINE_TIMEOUT` codes surfaced in the UI. @nextjs-frontend task.
- [ ] Verification: JWKS hosts-file block (warm + cold), GoTrue block → 503 within ~6s, Redis kill/restart recovery loop (design 3 §verification)

### Wave R3 — Quota + billing correctness
*Agents: @fastapi-backend (+ agents/ familiarity). Est: 2 days.*

- [ ] `WindowUsage` + `rehydrate_window_from_pg` (batched, None-sentinel) + fail-closed `check()` + `approximate` flag in usage report (design 4 fixes 1+3)
- [ ] `usage_sink._flush`: settle-only-if-insert-landed + retry + ERROR backfill payload (design 4 fix 2)
- [ ] Template ingest: `asyncio.wait_for(agent.run, 45)` + per-user concurrency limiter (Redis INCR, in-memory fallback) + `INGEST_TOO_MANY_AR` (design 4 fix 4)
- [ ] Summarizer attempt-marker + recently-attempted guard; `summary_sweeper.py` + 03:30 cron (design 4 fix 5)
- [ ] Logfire alerts: `llm_calls insert FAILED%` (page), `message.quota_unavailable` counter
- [ ] Verification: `/validate-calls` reconciliation, settle↔ledger round-trip probe, fail-closed matrix units, 3-parallel-ingest smoke (design 4 §verification)

### Wave R4 — Storage + upload integrity
*Agents: @fastapi-backend. Est: 2 days.*

- [ ] Range-request `download_head_bytes` + bounded-stream fallback (design 5 fix 1); one-time live curl verification of Range support
- [ ] Insert-first legacy upload (`upload_document_bytes`) + async-read/sync-work handler split; same pattern on the workspace attachment twin (design 5 fix 4)
- [ ] `delete_workspace_item` cleanup: best-effort storage remove + references hard-delete (design 5 fix 3)
- [ ] Startup reconciler DateTrigger catch-up (design 5 fix 5)
- [ ] `run_db` wraps on documents/workspace routes (design 1 phase 4)
- [ ] Verification: mid-flight-kill upload tests, delete-then-check-bucket, range-read latency proof (design 5 §verification)

### Wave R5 — Data integrity + API hygiene
*Agents: @fastapi-backend. Est: 1–2 days. (Migrations already applied in R0.)*

- [ ] case_service: RPC adoption (create/delete/list/update/status), drop count helpers, get_case_detail raises on fetch failure (design 6 §3, §7)
- [ ] preferences_service: `merge_preferences` RPC adoption (design 6 §3d)
- [ ] Workspace pagination (limit/offset + true `total`) + references `Semaphore(5)` (design 6 §4–5)
- [ ] references_service `_select_reference_rows` re-raise (design 3 sweep, coordinated)
- [ ] `run_db` wraps on remaining CRUD routes (design 1 phase 3)
- [ ] Verification: preferences race ×50, create_case atomicity probe, N+1 span-count proof, pagination seed test (design 6 §10)

### Post-wave gate
- [ ] @validate full pass → `agents_reports/validation_reliability.md`
- [ ] @security-reviewer pass over auth changes (new 503 paths, JWKS caching, webhook untouched)
- [ ] Logfire dashboard: per-route p99 before/after, `DbDeadlineExceeded` / `PoolTimeout` / `pipeline_timeout` / `quota_unavailable` counters

---

## Backlog (designed, not scheduled)

| Item | Design ref | Trigger to schedule |
|---|---|---|
| Redis SET NX send-dedup | design 2 fix 5b (full key/TTL/release design) | Before ever setting workers>1; requires R1's pipeline timeout (TTL safety) |
| Idempotency-Key on workspace creates | design 6 §6 | Frontend retry-UX work |
| Orphan storage sweeper (weekly, dry-run first) | design 5 fix 6 | After R4 stops new orphans; sweep historical ones |
| Supabase circuit breaker (fast-503 after N failures) | design 1 risk 3 | If outage thread-pool saturation observed |
| `quota_daily_usage` GROUP BY RPC | design 4 fix 3 | If per-user 30d llm_calls rows exceed ~10k |
| `redis_healthy` flag for quota store (skip dead-Redis timeout) | design 3 fix 3 limitation | With the dedup work |
| Scheduler health endpoint `/_meta/scheduler` | audit BL | Ops convenience |
| Drop storage timeout 60→10s | design 5 fix 2 | When legacy single-shot upload is deprecated |

## Key invariants the implementation must not break

1. **Single-worker is an enforced invariant** (boot guard), not a hope — in-memory dedup, in-memory ingest fallback, and the idempotency sketch all assume it.
2. **Cost accounting never blocks the pipeline**; the quota *gate* may. Settle is gated on ledger insert success — never settle without a row.
3. **User message saved BEFORE AI call** (Absolute Rule 7) — unchanged; the pipeline timeout's placeholder cleanup must never touch the user message.
4. **All user-facing errors in Arabic** (Absolute Rule 5) — every new error path has its string defined in the designs.
5. **No await between dedup check and slot reservation** in send_message_stream — the run_db sweep must preserve this by reserving first.
6. **Migrations applied via Supabase MCP with live-schema verification first** — files on disk ≠ prod schema (project memory: migration drift).
