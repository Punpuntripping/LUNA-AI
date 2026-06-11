# Design 4 — Quota + Billing Correctness

Philosophy preserved throughout: **cost accounting never blocks the pipeline** (settle, ledger insert, attempt markers stay best-effort) — but the **gate** (quota.check) is allowed to block, because blocking new spend is its entire job. The line: writes stay fail-open, the admission read fails closed when its answer is unknowable.

**Migrations needed: none.** Everything uses existing columns + `workspace_items.metadata` JSONB.

---

## Fix 1 — Fail-closed policy on quota reads

### Policy decision

Key observation: **every path that reaches `quota.check` has already completed a successful PG write/read** — `message_service` inserts the user message (~line 270) *before* the gate at line 309, and `GET /usage` calls `get_user_id` (PG read) first. A full PG outage never reaches the gate. The reachable dangerous case is a **partial PG failure**: `llm_calls` SELECT failing while the `messages` table works — combined with a **cold Redis key**. That is silent unlimited spend in an otherwise-healthy app.

**Decision: uniform fail-closed on "unknown" in `check()`, fail-soft in the report.**

- The narrow policy ("fail-closed only on cold-key rehydrate failure") and uniform are *behaviorally identical in practice* (both-down branch unreachable on serving paths). Uniform is one less branch to reason about.
- Refinement: **a partial sum that already exceeds the limit is a valid rejection** — if 5 of 7 days are known and they alone exceed the weekly limit, raise `QuotaExceeded` (we know enough), not 503.
- `GET /usage` (the UI dialog) must NOT 503 — render the known partial sum with an `approximate` flag.

### Decision matrix

| Redis | Key state | PG rehydrate | Today | New: `check()` | New: `GET /usage` |
|---|---|---|---|---|---|
| up | all hot | not called | sum (correct) | unchanged | unchanged |
| up | some cold | succeeds | rehydrate + backfill | unchanged (now batched + off-loop) | unchanged |
| up | some cold | **fails** | cold days count **0** → fail-open | partial ≥ limit → `QuotaExceeded`; else **`QuotaUnavailable`** | partial sum, `approximate: true` |
| down | — | succeeds | full per-day PG loop (blocking) | unchanged (batched + `to_thread`) | unchanged |
| down | — | **fails** | **0 → fail-open** | `QuotaUnavailable` (practically unreachable) | 0-bars, `approximate: true` |

### Code changes

**`shared/quota/redis_store.py`** — `usage_window` (lines 114–158) returns a richer value:

```python
@dataclass
class WindowUsage:
    total: float        # sum over days whose value is KNOWN
    missing_days: int   # days whose value could not be determined

    @property
    def complete(self) -> bool:
        return self.missing_days == 0

async def usage_window(redis, supabase, user_id, meter, days) -> WindowUsage:
    if days < 1:
        return WindowUsage(0.0, 0)
    dates = last_n_days_utc(days)
    keys = [day_key(meter, user_id, d) for d in dates]

    raw: list | None = None
    if redis is not None:
        try:
            raw = await redis.mget(keys)
        except Exception as e:
            logger.warning("quota.usage_window MGET failed (PG fallback): %s", e)

    if raw is None:  # Redis absent or MGET failed → whole window from PG
        per_day = await asyncio.to_thread(
            rehydrate_window_from_pg, supabase, user_id, meter, dates)
        if per_day is None:
            return WindowUsage(0.0, len(dates))          # ← sentinel, not 0
        return WindowUsage(float(sum(per_day.values())), 0)

    total, cold = 0.0, []
    for i, v in enumerate(raw):
        if v is None:
            cold.append(i)
        else:
            try: total += float(v)
            except (TypeError, ValueError): cold.append(i)

    if cold:
        per_day = await asyncio.to_thread(
            rehydrate_window_from_pg, supabase, user_id, meter,
            [dates[i] for i in cold])
        if per_day is None:
            return WindowUsage(total, len(cold))          # ← THE fix: cold+PG-fail no longer 0
        ttl = _ttl_for(meter)
        for i in cold:
            val = per_day.get(dates[i], 0.0)
            total += val
            try: await redis.set(keys[i], val, ex=ttl)    # backfill, best-effort
            except Exception: pass
    return WindowUsage(total, 0)
```

**`shared/quota/__init__.py`** — new exception next to `QuotaExceeded` (~line 65):

```python
QUOTA_UNAVAILABLE_AR = "تعذّر التحقق من حدود الاستخدام مؤقتًا. الرجاء المحاولة مرة أخرى بعد قليل."

@dataclass
class QuotaUnavailable(Exception):
    meter: Meter
    period: Period
    def to_event_payload(self) -> dict:
        return {"meter": self.meter, "period": self.period,
                "message_ar": QUOTA_UNAVAILABLE_AR}
```

`check()` (lines 113–154) per meter:

```python
    if needs_ord:
        d = await redis_store.usage_window(redis, supabase, user_id, "ord", 1)
        w = await redis_store.usage_window(redis, supabase, user_id, "ord", 7)
        if d.total >= d_limit:                      # known overage wins, even if partial
            raise QuotaExceeded("ord", "daily", d.total, d_limit, resets)
        if w.total >= w_limit:
            raise QuotaExceeded("ord", "weekly", w.total, w_limit, resets)
        if not d.complete or not w.complete:        # under limit but unknowable → closed
            raise QuotaUnavailable("ord", "daily" if not d.complete else "weekly")
```

**`backend/app/services/message_service.py:321`** — second except arm after `QuotaExceeded`:

```python
    except quota.QuotaUnavailable as qu:
        _logfire.warn("message.quota_unavailable", conversation_id=conversation_id,
                      meter=qu.meter, period=qu.period)
        yield _sse_event("error", {"detail": quota.QUOTA_UNAVAILABLE_AR,
                                   "code": "QUOTA_UNAVAILABLE"})
        return
```

(Sits before the assistant-placeholder insert at line 334 — no orphan placeholder.)

**`current_usage_report`** (lines 170–216): use `.total` everywhere, add `"approximate": not x.complete` per bar. Never raises. `backend/app/api/usage.py` unchanged.

---

## Fix 2 — Settle↔ledger atomicity in `usage_sink._flush`

**Why skip settle on insert failure**: the ledger is the rehydration source of truth. Settling Redis without a ledger row creates drift that *resurfaces forever* — every Redis key expiry recomputes a smaller number than was settled; `validate-calls` can never reconcile. Skipping settle keeps both stores consistently missing the same turn; the ERROR payload allows manual backfill into `llm_calls`, after which the next cold-key rehydrate heals quota automatically. Exposure: one free turn per insert outage — bounded, loud.

**`agents/utils/usage_sink.py:180-210`** — replacement (add `import json`):

```python
def _flush(supabase: Any, buf: list[dict[str, Any]] | None) -> int:
    """Insert buffered rows; settle quota ONLY if the insert landed.

    Never raises. Insert gets one immediate retry. If both attempts fail,
    the full row payload is logged at ERROR so cost can be backfilled into
    llm_calls manually, and settle is SKIPPED — ledger and quota stay
    consistent (both missing the turn) rather than drifting apart."""
    if not buf:
        return 0

    insert_ok = False
    for attempt in (1, 2):
        try:
            supabase.table("llm_calls").insert(buf).execute()
            insert_ok = True
            break
        except Exception as exc:
            if attempt == 1:
                logger.warning("llm_calls insert failed (attempt 1/2, %d rows), retrying: %s",
                               len(buf), exc)
            else:
                try:
                    payload = json.dumps(buf, ensure_ascii=False, default=str)
                except Exception:
                    payload = repr(buf)
                logger.error(
                    "llm_calls insert FAILED after retry — quota settle SKIPPED "
                    "(backfill these rows manually): error=%s rows=%s", exc, payload)

    if not insert_ok:
        return 0  # no ledger row ⇒ no settle ⇒ no drift

    user_id = buf[0].get("user_id")
    if user_id:
        total_cost = sum(float(r.get("cost_usd") or 0.0) for r in buf)
        total_pages = sum(int(r.get("pages_used") or 0) for r in buf)
        try:
            from shared.quota import settle_ocr_sync, settle_ord_sync
            if total_cost:
                settle_ord_sync(str(user_id), total_cost)
            if total_pages:
                settle_ocr_sync(str(user_id), total_pages)
        except Exception as exc:
            logger.debug("quota settle skipped: %s", exc)
    return len(buf)
```

Logfire alert on `"llm_calls insert FAILED"` (page-worthy — means manual backfill work exists).

---

## Fix 3 — Batch rehydration (one PG round-trip per window)

PostgREST can't `GROUP BY` without an RPC. **Chosen: one raw-row SELECT of two tiny columns over the date span, grouped in Python.** A user's 30-day `llm_calls` row count is low-thousands worst case — one round trip vs today's up-to-30 sequential blocking queries. An RPC `quota_daily_usage` is a future optimization if row counts grow past ~10k.

**`shared/quota/redis_store.py`** — replaces `rehydrate_from_pg` (lines 72–109; no callers outside this module):

```python
def rehydrate_window_from_pg(
    supabase: SupabaseClient, user_id: str, meter: Meter, dates: list[date],
) -> dict[date, float] | None:
    """ONE PG query for all requested days. Returns {day: value} zero-filled
    for days with no rows, or None when the query failed (the caller treats
    None as 'unknown' — never as zero). Sync; callers wrap in to_thread."""
    if not dates:
        return {}
    if meter == "web":                      # no PG backing yet — known zero
        return {d: 0.0 for d in dates}
    col = "cost_usd" if meter == "ord" else "pages_used"
    start, end = min(dates), max(dates) + timedelta(days=1)
    try:
        result = (
            supabase.table("llm_calls")
            .select(f"created_at,{col}")
            .eq("user_id", user_id)
            .gte("created_at", f"{start.isoformat()}T00:00:00Z")
            .lt("created_at", f"{end.isoformat()}T00:00:00Z")
            .execute()
        )
    except Exception as e:
        logger.warning("quota.rehydrate_window_from_pg(meter=%s, %s..%s) failed: %s",
                       meter, start, end, e)
        return None
    out = {d: 0.0 for d in dates}
    for r in (getattr(result, "data", None) or []):
        try:
            day = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00")).date()
        except Exception:
            continue
        if day in out:                       # span may cover non-requested days
            out[day] += float(r.get(col) or 0)
    return out
```

Verify the composite index `llm_calls(user_id, created_at)` from migration 058 exists live (`EXPLAIN`); if absent, that IS one small index migration worth doing.

---

## Fix 4 — Template ingest guard rails

### (a) Wall-clock timeout on the LLM call

**`agents/memory/template_ingester/runner.py:130`**:

```python
INGEST_LLM_TIMEOUT_S = 45.0
...
        try:
            result = await asyncio.wait_for(
                agent.run(user_msg, usage_limits=INGESTER_LIMITS),
                timeout=INGEST_LLM_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.warning("template_ingester: LLM call timed out after %.0fs", INGEST_LLM_TIMEOUT_S)
            _set_outcome(span, "llm_timeout")
            return None
```

**Billed-partial-cost note (must go in the code comment):** on timeout the `agent.run` task is cancelled, so `record_run` never executes — the provider may charge for tokens generated up to cancellation that produce **no ledger row**. Bounded by `INGESTER_LIMITS`, acceptable (we under-bill, never over-bill). Surfaces in `validate-calls` as provider chat spans with no matching ledger rows — the span's `outcome=llm_timeout` is the disambiguator; tag it reliably.

### (b) Per-user concurrency limit (Redis INCR + in-memory fallback)

Helpers at the top of **`backend/app/services/templates_service.py`**:

```python
INGEST_CONCURRENCY_LIMIT = 2
_INGEST_CONC_TTL_S = 120          # > 45s LLM timeout: a leaked slot self-heals
_INGEST_CONC_KEY = "ingest:conc:{user_id}"

_local_counts: dict[str, int] = {}          # single-worker in-memory fallback
_local_lock = asyncio.Lock()

class IngestConcurrencyExceeded(Exception): ...

async def _acquire_ingest_slot(redis, user_id: str) -> str:
    """Returns 'redis' | 'local'. Raises IngestConcurrencyExceeded at LIMIT."""
    if redis is not None:
        try:
            pipe = redis.pipeline()
            pipe.incr(_INGEST_CONC_KEY.format(user_id=user_id))
            pipe.expire(_INGEST_CONC_KEY.format(user_id=user_id), _INGEST_CONC_TTL_S)
            n, _ = await pipe.execute()
            if int(n) > INGEST_CONCURRENCY_LIMIT:
                try: await redis.decr(_INGEST_CONC_KEY.format(user_id=user_id))
                except Exception: pass     # TTL self-heals
                raise IngestConcurrencyExceeded()
            return "redis"
        except IngestConcurrencyExceeded:
            raise
        except Exception as e:             # Redis hiccup → degrade, don't block
            logger.warning("ingest limiter: redis failed, in-memory fallback: %s", e)
    async with _local_lock:
        if _local_counts.get(user_id, 0) >= INGEST_CONCURRENCY_LIMIT:
            raise IngestConcurrencyExceeded()
        _local_counts[user_id] = _local_counts.get(user_id, 0) + 1
    return "local"

async def _release_ingest_slot(redis, user_id: str, mode: str) -> None:
    if mode == "redis" and redis is not None:
        try: await redis.decr(_INGEST_CONC_KEY.format(user_id=user_id))
        except Exception: pass             # TTL self-heals the leak
        return
    async with _local_lock:
        n = _local_counts.get(user_id, 0) - 1
        if n <= 0: _local_counts.pop(user_id, None)
        else: _local_counts[user_id] = n
```

Hook into `ingest_template` (lines 197–247) — signature gains `redis`:

```python
async def ingest_template(supabase, auth_id, item_id, redis=None) -> dict:
    user_id = get_user_id(supabase, auth_id)
    try:
        mode = await _acquire_ingest_slot(redis, user_id)
    except IngestConcurrencyExceeded:
        return {"ok": False, "error": INGEST_TOO_MANY_AR}
    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            ... existing body unchanged ...
    finally:
        await _release_ingest_slot(redis, user_id, mode)
```

Returning `{ok: false, error}` (not 429) preserves this endpoint's established never-5xx contract. **`backend/app/api/templates.py:90-108`**: add `redis: Optional[AsyncRedis] = Depends(get_redis)` and pass through.

### (c) Arabic strings

| Constant | String |
|---|---|
| `INGEST_TOO_MANY_AR` | `لديك عمليات تحويل قوالب قيد التنفيذ بالفعل. يرجى الانتظار حتى تكتمل ثم المحاولة مجددًا.` |
| `QUOTA_UNAVAILABLE_AR` | `تعذّر التحقق من حدود الاستخدام مؤقتًا. الرجاء المحاولة مرة أخرى بعد قليل.` |

---

## Fix 5 — Summarizer double-billing guard + summary-NULL sweep

### Attempt marker (no migration — `workspace_items.metadata` JSONB)

**`agents/memory/summarize.py`**:

1. `ATTEMPT_RECENT_WINDOW_S = 3600`.
2. Guard after the `already_summarized` guard (~line 350), before the `content_md` guards:

```python
            attempt = ((row.get("metadata") or {}).get("summary_attempt") or {})
            attempted_raw = attempt.get("at")
            if not force and attempted_raw:
                try:
                    ts = datetime.fromisoformat(str(attempted_raw))
                    age = (datetime.now(timezone.utc) - ts).total_seconds()
                    if 0 <= age < ATTEMPT_RECENT_WINDOW_S:
                        _span.set_outcome("recently_attempted")   # in-flight or just failed
                        return False
                except Exception:
                    pass   # unparseable marker → proceed
```

3. `_mark_attempt` helper, called after all guards pass, **before** either flow's LLM call:

```python
def _mark_attempt(supabase, item_id: str, existing_metadata: dict) -> dict:
    """Stamp metadata.summary_attempt.at BEFORE the LLM fires, so a retry that
    arrives while we're in flight (or right after a failed persist) is skipped
    instead of re-billing. Best-effort: a failed marker write proceeds anyway."""
    md = dict(existing_metadata or {})
    md["summary_attempt"] = {"at": datetime.now(timezone.utc).isoformat()}
    try:
        supabase.table("workspace_items").update({"metadata": md}) \
            .eq("item_id", item_id).execute()
    except Exception as exc:
        logger.warning("summarize: attempt-marker write failed item_id=%s: %s", item_id, exc)
    return md
```

Pass the returned `md` into `_persist_summary` so the success-path persist doesn't clobber the marker. Residual window: marker-write fails AND LLM succeeds AND persist fails — double-bill survives only when PG is flaky at exactly both moments; bounded by the sweep's 24h cadence.

The webhook handler needs no change — it delegates to `summarize_workspace_item`, which carries the guard.

### Summary-NULL sweep job

New file **`backend/app/services/summary_sweeper.py`**:

```python
SWEEP_CAP = 25            # max LLM re-runs per day — hard spend bound
SWEEP_FETCH_LIMIT = 100
MIN_ITEM_AGE_MIN = 30     # never race a just-inserted item's webhook
RETRY_AFTER_S = 86_400    # only re-attempt items whose last attempt is >24h old

async def sweep_missing_summaries(supabase) -> dict:
    now = datetime.now(timezone.utc)
    def _fetch():
        return (supabase.table("workspace_items")
            .select("item_id, content_md, metadata, created_at")
            .is_("summary", "null")
            .is_("deleted_at", "null")
            .not_.is_("content_md", "null")
            .lt("created_at", (now - timedelta(minutes=MIN_ITEM_AGE_MIN)).isoformat())
            .order("created_at", desc=False)        # oldest debt first
            .limit(SWEEP_FETCH_LIMIT)
            .execute())
    rows = (await asyncio.to_thread(_fetch)).data or []

    picked = []
    for r in rows:
        if len((r.get("content_md") or "").strip()) < MIN_CONTENT_LENGTH_CHARS:
            continue                                  # legitimately unsummarized
        at = ((r.get("metadata") or {}).get("summary_attempt") or {}).get("at")
        if at:
            try:
                if (now - datetime.fromisoformat(str(at))).total_seconds() < RETRY_AFTER_S:
                    continue
            except Exception:
                pass
        picked.append(r["item_id"])
        if len(picked) >= SWEEP_CAP:
            break

    ok = 0
    for item_id in picked:                            # sequential — no LLM stampede
        if await summarize_workspace_item(supabase, item_id):
            ok += 1
    return {"candidates": len(rows), "attempted": len(picked), "summarized": ok}
```

Catches both dropped pg_net webhooks (never attempted) and the persist-failed case (attempted, summary still NULL). Each call opens its own `collect_llm_calls` scope → cost lands in the ledger against the item's owner; spend bounded at 25/day.

Register in **`backend/app/main.py`** (pattern identical to existing jobs):

```python
    scheduler.add_job(_run_summary_sweep,
                      trigger=CronTrigger(hour=3, minute=30),   # after 03:00/03:15 jobs
                      id="summary_null_sweep", replace_existing=True)
```

---

## Migration summary

| Need | Decision |
|---|---|
| New columns | **None.** `summary_attempt` in `workspace_items.metadata` JSONB. |
| GROUP BY RPC | **Deferred.** Python-side grouping; revisit past ~10k rows/user/month. |
| Index | Verify `llm_calls (user_id, created_at)` exists live from migration 058; add only if missing. |
| Error codes | `QUOTA_UNAVAILABLE` is an SSE payload string; optionally add to `ErrorCode` enum. |

## Verification

1. **`scripts/validate_llm_calls.py` (`/validate-calls`)** — drift detector for Fixes 2/4a. Failed-insert turn: zero ledger rows + real provider spans → report flags + ERROR log carries backfill payload; after backfill, re-run goes clean. Timed-out ingest: same signature with `outcome=llm_timeout` — document as expected/benign.
2. **Settle↔ledger probe**: compare Redis `quota:{user}:ord:day:{d}` vs `SUM(cost_usd)` from `llm_calls` — must match now that settle is gated. `DEL` the key, re-read `GET /usage` — rehydrated value equals pre-delete (round-trip proof).
3. **Fail-closed matrix units** (mocked redis/supabase): all five rows; (a) cold+rehydrate-None+partial ≥ limit → `QuotaExceeded`, (b) under limit → `QuotaUnavailable`, (c) `GET /usage` never raises, sets `approximate`.
4. **Manual smoke**: stop Redis → send message (works, ONE batched PG query — confirm in Logfire); 3 parallel `/templates/ingest` → third gets `INGEST_TOO_MANY_AR` immediately; wait 120s TTL → succeeds.
5. **Sweep**: seed row with content_md >300 chars, summary NULL, backdated → run sweep → summary written, one llm_calls row, marker present; run again → skip, no second LLM row.
6. **Logfire alerts**: `llm_calls insert FAILED%` (page) + counter on `message.quota_unavailable` (spike = PG partial degradation).

## Critical files
- `shared/quota/redis_store.py` (Fixes 1+3), `shared/quota/__init__.py` (Fix 1)
- `agents/utils/usage_sink.py` (Fix 2)
- `backend/app/services/templates_service.py` + `agents/memory/template_ingester/runner.py` (Fix 4)
- `agents/memory/summarize.py` + new `backend/app/services/summary_sweeper.py` + `backend/app/main.py` (Fix 5)
