# Migration: `agent_runs` → `llm_calls` trace + `paused_runs`

**Status:** Phases 0–4 CODE COMPLETE in working tree (not deployed). Migrations
058–061 applied to prod (additive/safe). Migration 062 (DROP agent_runs) WRITTEN
BUT NOT APPLIED — gated on the new code deploying first. `agents/runs.py` deleted.
**Date:** 2026-06-01
**Owner:** —

### Execution log (2026-06-01)
- **Phase 1 done.** `rehydrate_from_pg` + `_count_prior_ocr_runs` read `llm_calls`.
  Migration 059 backfilled 125 historical agent_runs rows → llm_calls; cost parity
  verified exact ($2.6812 == $2.6812).
- **Phase 2 done.** Migration 060 `paused_runs` applied. New `agents/paused_runs.py`
  (record_pause/find_open_pause/resolve_pause/is_expired). Orchestrator rewired:
  pause→INSERT, resume/abandon/timeout→DELETE, completed-run record removed
  (outcome stamped on dispatch span instead).
- **Phase 3 done.** Migration 061 `llm_calls.run_id` applied. `bind_run_id`
  ContextVar; dispatch pre-allocates run_id, resume reuses it; both raw planner
  deciders fed to the ledger so pause/resume legs aren't $0.
- **Phase 4 code done.** Removed all remaining writers (OCR, summarize×2,
  item_analyzer→record_call, memory/agent mock). Deleted `agents/runs.py`.
  Migration 062 written (DROP) — NOT applied. 66 tests pass; backend imports clean.
- **Remaining:** deploy → bake → run 062 pre-apply checklist → apply 062.
  Optional: repoint convo_monitor/run_monitor skills off the gone
  `agent_runs.record` span; tidy residual agent_runs mentions in writer_planner
  docstrings.

## Thesis

`agent_runs` (28 columns) is a kitchen-sink doing three unrelated jobs. Split it
into two single-purpose stores and **delete the table**:

| Job | New home | Shape |
|---|---|---|
| token / cost ledger | **`llm_calls`** (migration 058, built) | immutable append-only **log** — `sum()`, never mutated |
| pause / resume state | **`paused_runs`** (new, tiny) | mutable **working state** — created `awaiting_user`, deleted on resolve |
| forensics / trace | **Logfire** (already there) | spans, queried by conversation_id / message_id |

Guiding principle: **a trace is an immutable log; pause/resume is mutable working
state.** They must not share a table. Folding pause state into `llm_calls` would
mean inserting non-call rows carrying a heavy `message_history` BYTEA into a hot
cost-append path and *mutating* ledger rows (status flips) — which breaks the
append-only contract that makes `sum(cost_usd)` trustworthy. So we do NOT move
pause/resume into the trace; we give it its own 9-column table.

## Why (diagnosis recap)

- Cost was opt-in per agent; the writer hardcoded `tokens_in=0` → every `writing`
  run billed $0 (0/10 memos had cost).
- `trace_id` NULL on 100% of rows — dead Logfire join.
- Pricing keys are canonical (`qwen3.6-plus`); provider echo-names (`qwen-plus`)
  silently missed the registry → $0.
- 28 columns conflating cost, pause-state, and audit.

## Reader/writer audit (point-in-time; verify before each phase)

**Writers** (`record_agent_run` / `update_run_status`):
| Site | Today writes | After migration |
|---|---|---|
| `orchestrator._dispatch` finally | completed-run identity row | **removed** (cost→llm_calls; identity→Logfire span) |
| `orchestrator._record_deferred` | `awaiting_user` row | **INSERT `paused_runs`** |
| `orchestrator._resume_major_agent` finally + abandon/error/timeout | `update_run_status` | **DELETE `paused_runs`** (run resolved) |
| `orchestrator.handle_message` expired | `update_run_status('timeout')` | **DELETE `paused_runs`** |
| `ocr_extractor/runner.py` | ocr telemetry row | **removed** (llm_calls + Logfire have it) |
| `memory/summarize.py` `_record_cost` / `_record_attachment_cost` | cost row | **removed** |
| `memory/item_analyzer/runner.py` | cost row | **removed** |
| `memory/agent.py` | cost row | **removed** (verify) |

**Readers** (`.table("agent_runs").select`):
| Site | Reads | After migration |
|---|---|---|
| `orchestrator._find_awaiting_user` | pause lookup | **`paused_runs`** |
| `orchestrator._resume_major_agent` / `_expired` | pause state | **`paused_runs`** |
| `shared/quota/redis_store.py:rehydrate_from_pg` | `cost_usd` / `pages_used` | **`llm_calls`** ⚠ (see Phase 1) |
| `ocr_extractor._count_prior_ocr_runs` | ocr row count | **`llm_calls WHERE pages_used>0`** |
| monitors (`convo_monitor_extract`, `run_monitor`) | Logfire spans / deps — **not the table** | no change |

**Confirmed NOT a reader:** `_load_prior_search_summaries` (planner prior-task
enumeration) reads `workspace_items` (kind='agent_search'), not `agent_runs`. So
no completed-run rows are ever read back — they are write-only audit today.

---

## Phase 0 — DONE (already in working tree)

Per `project_llm_calls_ledger` memory + migration 058:
- `llm_calls` table (19 cols), RLS service-role-only, indexes on
  message_id / conversation_id / (user_id, created_at).
- `agents/utils/usage_sink.py` — ContextVar buffer; `collect_llm_calls(...)`
  scope (auto-flush + quota settle on exit); `record_call`; `in_scope`.
- `agents/utils/tracking.py` — `run_tracked` + `record_run` feed the sink;
  `_cost` hardened to price by slot-canonical when echo-name misses pricing.
- `agents/orchestrator.py` — `handle_message` opens the turn scope;
  `_feed_deep_search_ledger` feeds deep_search from `per_phase_stats`.
- OCR + summarize explicit / conditional scopes.
- `agents/runs.py` — stripped cost derivation, quota settle, trace hydration.
- `agent_runs.trace_id` / `span_id` columns DROPPED.

**Not yet done / known gap:** the cost columns on `agent_runs` are now NULL going
forward, which silently breaks `rehydrate_from_pg` (Phase 1).

---

## Phase 1 — Repoint quota + OCR reads to `llm_calls`  (REQUIRED NOW)

This is needed **even if we never delete `agent_runs`**, because Phase 0 already
stopped writing cost there. It is a live billing-durability bug.

1. **`shared/quota/redis_store.py:rehydrate_from_pg`**
   - `ord` meter: `SELECT cost_usd FROM llm_calls WHERE user_id=… AND created_at in [day]` → `sum`.
   - `ocr` meter: `SELECT pages_used FROM llm_calls WHERE user_id=… AND created_at in [day]` → `sum`.
2. **`ocr_extractor._count_prior_ocr_runs`** → count `llm_calls WHERE user_id=…
   AND agent='memory.ocr_extraction'` (or `pages_used > 0`). Keeps the lifetime
   OCR quota gate working.
3. Verify Redis-cold rehydration matches pre-migration numbers on a test user.

**Exit criteria:** quota rehydrate + OCR gate read from `llm_calls`; agent_runs
is no longer read for any billing decision.

---

## Phase 2 — Build `paused_runs`; move pause/resume off `agent_runs`

### 2a. Migration `059_paused_runs.sql`
```sql
CREATE TABLE public.paused_runs (
  run_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id  uuid NOT NULL,
  user_id          uuid NOT NULL,
  case_id          uuid,
  agent_family     text,            -- only 'deep_search' resumes today
  task_label       text,
  message_history  bytea,           -- pydantic-ai serialized history
  deferred_payload jsonb,           -- tool_call_id + args
  question_text    text,
  pause_reason     text NOT NULL DEFAULT 'clarify',  -- clarify | approve_plan
  asked_at         timestamptz,
  expires_at       timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_paused_runs_lookup ON public.paused_runs (conversation_id, user_id);
ALTER TABLE public.paused_runs ENABLE ROW LEVEL SECURITY;  -- service-role only, no policies
```

**Design choice — delete on resolve, no `status` column.** The table only ever
holds *currently-open* pauses; resume-ok / abandon / timeout / expire all `DELETE`
the row. Self-cleaning, tiny forever. (If we later want pause analytics — how
often runs pause/expire — that comes from Logfire spans, not this table.)

### 2b. New module `agents/paused_runs.py` (replaces the pause half of `runs.py`)
- `record_pause(supabase, PauseRecord) -> run_id` — INSERT.
- `find_open_pause(supabase, conversation_id, user_id) -> dict | None`
  — `… WHERE conversation_id=$1 AND user_id=$2 ORDER BY asked_at DESC LIMIT 1`.
- `resolve_pause(supabase, run_id) -> None` — DELETE.
- `is_expired(row) -> bool` (moved from `_expired`).
- BYTEA hex encode/decode helpers (lifted from `runs.py`).

### 2c. Rewire `orchestrator.py`
- `_record_deferred` → `record_pause(...)` (INSERT paused_runs) instead of
  `record_agent_run(status='awaiting_user', …)`.
- `_find_awaiting_user` → `find_open_pause(...)`.
- `_resume_major_agent` + `handle_message` expired branch: every
  `update_run_status(run_id, 'ok'|'abandoned'|'error'|'timeout', …)` →
  `resolve_pause(supabase, run_id)` (DELETE). The merged `deferred_payload` /
  cost no longer need persisting (cost is in llm_calls).
- The `_dispatch` finally's `record_agent_run` for COMPLETED runs → **deleted**
  (nothing reads it; the dispatch Logfire span keeps forensics). Keep
  `_feed_deep_search_ledger` (that feeds llm_calls).

**Exit criteria:** a full pause → resume cycle works end-to-end reading/writing
`paused_runs` only; no `agent_runs` access remains in the pause path. Expired
pauses are cleaned. Smoke-test ask_user AND approve_plan flavours.

---

## Phase 3 — (Optional) `run_id` on `llm_calls` for pause↔resume cost linkage

Only if we want one logical run's cost to roll up across the pause boundary
(turn-1 pause leg + turn-2 resume leg share a `run_id`).

1. `ALTER TABLE llm_calls ADD COLUMN run_id uuid;` + index.
2. **Pre-allocate** `run_id = uuid4()` at the start of each sub-run (dispatch /
   OCR / summarize) — pass it to both the `paused_runs` insert (when it pauses)
   and a finer `bind_run_id(R)` ContextVar that `record_call` reads.
3. **Resume reuses** the existing `run_id` from the open pause row → turn-2 calls
   inherit turn-1's `run_id` automatically. That's the whole link.
4. Route the two raw planner-decider calls (`planner/runner.py:~260` fresh,
   `orchestrator.py:~508` resume) through `run_tracked` so the pause-triggering
   and resume calls actually appear in the trace with `outcome='paused'` / `ok`.

Deliverables: cost-per-logical-run, "cost to ask a clarifying question"
(`WHERE outcome='paused'`), and `JOIN paused_runs USING(run_id)`.

*Deferrable — not required to delete `agent_runs`.*

---

## Phase 4 — Drop `agent_runs`

Only after Phases 1–2 land and bake (1–2 days in prod with telemetry sane).

1. Re-grep `agent_runs` across the repo → must be zero non-test references.
2. Delete `agents/runs.py` (`AgentRunRecord`, `record_agent_run`,
   `update_run_status`) and remove all imports / call sites (OCR, summarize,
   item_analyzer, memory/agent.py, orchestrator).
3. Migration `060_drop_agent_runs.sql`:
   ```sql
   DROP TABLE public.agent_runs;
   -- drop the now-orphaned enums if nothing else uses them:
   --   agent_family enum, agent_run status enum  (verify with pg_depend first)
   ```
4. Update `convo_monitor_extract.py` / `run_monitor.py` to read `llm_calls`
   (cost) where they referenced agent_runs cost; the `agent_runs.record` span is
   gone — drop or rename that span expectation.
5. Update `CLAUDE.md`, `MEMORY.md`, and `project_llm_calls_ledger` memory.

**Exit criteria:** `agent_runs` gone; two tables remain — `llm_calls` (immutable
cost trace) + `paused_runs` (mutable suspension state). Full smoke test:
chat → deep_search → pause → resume → OCR turn → quota reflects cost; Redis-cold
rehydrate matches.

---

## Risks & rollback

- **Billing.** Quota settle (Phase 0) + rehydrate (Phase 1) both move to
  `llm_calls`. Settle and rehydrate must agree. Mitigation: dual-read check —
  before dropping, compare `sum(llm_calls.cost_usd)` vs old
  `sum(agent_runs.cost_usd)` per user/day for the overlap window.
- **Resume regression.** Pause/resume is load-bearing and easy to break.
  Mitigation: keep Phase 2 a pure move (same fields, same BYTEA encoding);
  smoke-test both pause flavours before Phase 4.
- **Rollback.** Phases are independent and ordered so each is safe alone. Don't
  run Phase 4 (`DROP TABLE`) until 1–3 are verified in prod. `DROP TABLE` is the
  only irreversible step — take a backup / keep the migration revertible by
  re-create + backfill-from-llm_calls if needed (identity columns are lost, but
  nothing reads them).

## Verification checklist (per phase)
- [ ] P1: Redis-cold rehydrate for a test user equals expected llm_calls sum.
- [ ] P1: OCR lifetime gate still blocks at the quota.
- [ ] P2: ask_user pause → resume completes; row gone from paused_runs after.
- [ ] P2: approve_plan pause → resume completes.
- [ ] P2: expired pause cleaned; fresh route proceeds.
- [ ] P3 (if done): `WHERE run_id=R` spans both legs; `outcome='paused'` present.
- [ ] P4: zero `agent_runs` refs; smoke test green; dashboards repointed.
