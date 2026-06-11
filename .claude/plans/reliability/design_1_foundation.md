# Design 1 — Foundation: Event-Loop Unblocking + Supabase I/O Timeouts (S1 + S2)

**Scope:** `shared/db/client.py`, new `shared/db/run.py`, `backend/app/main.py` (lifespan), route-layer wraps across `backend/app/api/*`, inline wraps in `backend/app/services/message_service.py`.
**Installed stack (verified locally):** `supabase` 2.28.0, `postgrest` 2.28.0, `storage3` 2.28.0, `httpx >= 0.28`.

---

## Decision 1 — `run_db()` to_thread wrapper, NOT async-client migration

**Chosen: thin to_thread wrapper.** Rationale, in order of weight:

1. **Blast radius.** ~101 `.execute()` callsites across 18 backend service files, plus 64 sync-client usages across 26 files in `agents/`. Migration to `AsyncClient` forces every sync service function to become `async def`, cascading into every caller. The wrapper touches ~56 route-layer callsites with a one-line mechanical change and leaves all service signatures intact.
2. **The agents pipeline is architecturally committed to sync-client-in-threads.** `_harden_postgrest_session` in `shared/db/client.py:20-45` exists *specifically because* deep_search v4 fans out reg/compliance/case search via `asyncio.to_thread` over the shared sync client (HTTP/2 multiplexing broke under threaded concurrency; HTTP/1.1 + pool fixed it). An `AsyncClient` cannot be shared into threads; migrating agents would mean re-validating the entire deep_search concurrency model.
3. **Async client maturity is adequate but not boring.** Research: async support since supabase-py 2.2.0 with rough feature parity, but 2025 releases shipped a production-breaking regression where `ClientOptions` with `acreate_client()` raised `AttributeError: 'storage'` in v2.24.0 (supabase-py #1306); `create_async_client` being a coroutine complicates ASGI init (#798); session/RLS ergonomics issues open (#915, auth-py #537). Not disqualifying, but trades a known locally-proven pattern for new failure modes — for zero throughput benefit at this app's scale (single worker, tens of concurrent requests).
4. **to_thread propagates contextvars** (uses `contextvars.copy_context` internally), so Logfire trace context flows into the thread for free. Raw `loop.run_in_executor` does not — this rules out a hand-rolled executor call.

The existing `get_async_supabase_client()` factory stays for future/agent use; do not delete it.

## Decision 2 — Where the timeouts live (verified against installed 2.28.0)

**Verified facts about supabase-py 2.28.0** (inspected installed package):

- `ClientOptions(postgrest_client_timeout=..., storage_client_timeout=...)` **exist and are honored**. Defaults today: **postgrest 120s flat, storage 20s flat, functions 5s**. So "no timeout" in the audit is really "120s flat on postgrest" — functionally a hang.
- `postgrest_client_timeout` accepts a full `httpx.Timeout`; `storage_client_timeout` is **int seconds only**.
- Both `client.postgrest` and `client.storage` are **lazy properties** — they read options at first access, so configuration must happen before first use (or by assigning the backing field).
- postgrest/storage3 2.28 emit a `DeprecationWarning` for the `timeout` param — the library's direction is bring-your-own `httpx.Client`.
- `ClientOptions.httpx_client` injects ONE shared client into postgrest+storage+functions+auth — rejected: postgrest and storage need different timeout profiles.
- **Critical mechanism detail:** replacing `client.postgrest.session` post-hoc works (the prod `_harden_postgrest_session` proves it), but replacing `client.storage.session` does NOT — storage3's `from_()` builds bucket proxies from `self._client`, bound once in `__init__`. Storage must be constructed fresh with `http_client=` and assigned to `client._storage`.
- GoTrue builds its own `httpx.Client()` with no timeout arg → **httpx's default `Timeout(5.0)` already applies** to auth calls. The real auth problem is event-loop blocking, which `run_db` fixes; no GoTrue session surgery needed.

**New `_harden_sessions(client)` in `shared/db/client.py`** (replaces `_harden_postgrest_session`):

```python
import httpx
from storage3._sync.client import SyncStorageClient

# Railway <-> Supabase ap-south-1: intra-region RTT ~1-5ms (same region) or
# ~70-90ms cross-region. p99 PostgREST query well under 2s.
POSTGREST_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)
# httpx read/write timeouts are PER socket operation (per chunk), not whole-
# transfer — 60s/op is generous even for the 50 MB upload/download paths.
STORAGE_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0)

_LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20,
                       keepalive_expiry=30.0)

def _harden_sessions(client: Client) -> None:
    # postgrest: keep the proven session-swap (postgrest requests go via .session)
    try:
        old = client.postgrest.session
        client.postgrest.session = httpx.Client(
            base_url=old.base_url, headers=old.headers,
            timeout=POSTGREST_TIMEOUT, http2=False, limits=_LIMITS,
        )
    except Exception as e:
        logger.warning("Could not harden postgrest session: %s", e)

    # storage: session-swap does NOT work (from_() binds _client at init).
    # Build a fresh SyncStorageClient around our own httpx client and assign
    # the lazy property's backing field.
    try:
        client._storage = SyncStorageClient(
            url=str(client.storage_url),
            headers=client.options.headers,
            http_client=httpx.Client(timeout=STORAGE_TIMEOUT, http2=False,
                                     limits=_LIMITS, follow_redirects=True),
        )
    except Exception as e:
        logger.warning("Could not harden storage session: %s", e)
```

Plus belt-and-suspenders in the factories: `create_client(..., options=ClientOptions(postgrest_client_timeout=httpx.Timeout(connect=5, read=15, write=15, pool=5), storage_client_timeout=60))` so even if `_harden_sessions` degrades on a future supabase-py bump, timeouts still exist. Apply the same hardening to `get_supabase_anon_client()` (it currently gets NO hardening at all — it serves GoTrue auth in `app.state.supabase_auth`).

**Value justification:** connect=5s tolerates TLS + transient packet loss without letting a SYN blackhole hold a thread; read=15s is ~7× the slowest legitimate single PostgREST query (each query in the list_cases N+1 is individually fast — httpx timeouts are per-request, so multi-query services are unaffected); pool=5s converts pool exhaustion into a fast, diagnosable error instead of silent queueing; storage 60s/op covers 50 MB transfers since httpx read/write timeouts reset per chunk.

## Decision 3 — Wrap at the ROUTE layer; services stay sync

Converting services to async is churn for nothing. The point of the sync-service pattern is that one thread runs a service's 2–5 sequential round-trips (`get_user_id` then the real query) with zero event-loop involvement. **One `run_db()` per route call wraps the entire service invocation**; exceptions (`LunaHTTPException`) propagate unchanged through `to_thread`.

**New file `shared/db/run.py`:**

```python
"""Run sync Supabase service functions off the event loop."""
from __future__ import annotations
import asyncio
from typing import Any, Callable, TypeVar

T = TypeVar("T")

class DbDeadlineExceeded(Exception):
    """Outer wall-clock deadline hit. Backend maps this to 503 SERVICE_UNAVAILABLE."""

async def run_db(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Route-layer wrapper: await run_db(service.fn, supabase, auth_id, ...).
    Propagates exceptions (incl. LunaHTTPException) unchanged.
    Contextvars (Logfire trace context) flow into the thread."""
    return await asyncio.to_thread(fn, *args, **kwargs)

async def run_db_deadline(deadline_s: float, fn: Callable[..., T],
                          /, *args: Any, **kwargs: Any) -> T:
    """run_db with an outer deadline. WARNING: cancellation does not kill the
    thread — it runs until httpx's own timeout fires. Keep deadline_s >= the
    httpx per-request total (~20s) or threads pile up during an outage."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), deadline_s)
    except asyncio.TimeoutError:
        raise DbDeadlineExceeded(getattr(fn, "__name__", str(fn)))
```

It lives in `shared/` (not `backend/`) so `agents/` and `shared/quota/redis_store.py` can use it; it must NOT import `backend.app.errors` — instead `backend/app/main.py` registers an exception handler mapping `DbDeadlineExceeded` → 503 with Arabic detail (this also feeds the S5 fix domain: dependency failure ≠ user error).

**Canonical before/after — `GET /api/v1/conversations` (`backend/app/api/conversations.py:37-58`):**

```python
# BEFORE (blocks the event loop for 2 round-trips: get_user_id + list query)
data = conversation_service.list_conversations(
    supabase, current_user.auth_id, case_id=case_id, limit=limit, offset=offset,
)

# AFTER (identical semantics, off-loop)
from shared.db.run import run_db
data = await run_db(
    conversation_service.list_conversations,
    supabase, current_user.auth_id, case_id=case_id, limit=limit, offset=offset,
)
```

That is the entire per-route diff. Service file untouched.

**Hot-path exception:** `message_service.send_message_stream` is already `async def` with blocking calls inline (user-msg insert at line 273, audit log 284, attachment link 299, placeholder insert 336, plus the post-stream content/conversation updates). Those get wrapped *inside* the generator at each callsite — extract the existing small try-blocks into named module-level sync helpers and `await run_db(helper, ...)`. The dedup-slot comment at line 348-353 ("no await between the dedup check and slot reservation") must be preserved: the dedup check (line 256) and `_active_runs[conversation_id] = ...` (line 353) would have awaits introduced between them by this change — **the slot reservation must move up to immediately after the dedup check** (reserve before the first `await run_db`), with cleanup on the early-return error paths. This is the one non-mechanical edit in the whole domain; flag it for careful review.

`messages.py:55-56` (pre-flight `get_user_id` + `verify_conversation_ownership`) becomes two `await run_db(...)` lines.

## Decision 4 — Thread pool: resize the default executor, no dedicated pool

The default executor is `min(32, cpu_count + 4)` — on a 2-vCPU Railway container that is **6 threads**, which becomes the new concurrency ceiling once every route goes through `run_db`. Fix in lifespan (`backend/app/main.py`, before anything else):

```python
from concurrent.futures import ThreadPoolExecutor
loop = asyncio.get_running_loop()
loop.set_default_executor(ThreadPoolExecutor(max_workers=40, thread_name_prefix="luna-db"))
```

Why resize-default rather than dedicated executor: `asyncio.to_thread` only uses the default executor, and to_thread is required for contextvar propagation (Logfire). 40 workers < httpx `max_connections=50`, so threads never block on the connection pool; it also covers the deep_search to_thread fan-out, which shares this pool. Do NOT exceed ~45 without raising httpx limits, or `pool=5.0` timeouts fire under load.

## Rollout phases

**Phase 0 — Foundation (one PR, no callsite changes).** `shared/db/run.py` (new), `_harden_sessions` + `ClientOptions` in `shared/db/client.py`, executor sizing + `DbDeadlineExceeded` handler in `backend/app/main.py`. Ship and soak: the only behavior change is timeouts now actually firing (120s→15s postgrest). Watch Logfire for new `httpx.ReadTimeout` on legit slow queries before proceeding.

**Phase 1 — Chat hot path.** `backend/app/api/messages.py` (both routes) + the inline callsites in `message_service.send_message_stream` (with the dedup-slot reordering above) + `shared/quota/redis_store.py` `rehydrate_from_pg` sync call → `run_db`. ~80% of the user-felt win.

**Phase 2 — Auth.** `backend/app/api/auth.py`: wrap `sign_in_with_password` (line 51), `refresh_session` (116), `sign_out` (147), and the `/me` query (174-180) in `run_db`. GoTrue's implicit httpx 5s timeout already bounds these; the wrap removes the loop-block.

**Phase 3 — CRUD routes (mechanical).** `conversations.py`, `cases.py`, `memories.py`, `preferences.py`, `usage.py`, `templates.py` — ~25 one-line wraps.

**Phase 4 — Documents/workspace.** `documents.py`, `workspace.py` (19 callsites) — storage ops now also carry the hardened 60s/op timeouts from Phase 0. The 50 MB in-memory read loop is the storage domain's fix but its storage upload should land behind `run_db` here.

**Explicitly out:** `agents/` callsites (already to_thread'd), scripts/migrations, the in-process scheduler jobs (already `to_thread`'d in lifespan).

## Risks / regressions to watch

1. **Dedup race in send_message_stream** — introducing awaits between the duplicate check and slot reservation reopens the double-billing race (convo cb348fe6). Mitigation: reserve the slot synchronously right after the check.
2. **Timeout false positives** — any query legitimately >15s (large `count="exact"` scans) starts failing. The list_cases N+1 fix (design 6) reduces exposure; monitor `httpx.ReadTimeout` by route for a week.
3. **Thread leak during outages** — `run_db_deadline` cancellation leaves the thread running until httpx gives up (≤ ~20s with new timeouts, vs 120s today). With 40 workers and 15s caps, a full Supabase outage saturates the pool for ≤20s per wave — acceptable; the circuit breaker (backlog) is the long-term answer.
4. **supabase-py upgrade fragility** — `_harden_sessions` touches `client.postgrest.session` and `client._storage`. Both guarded by try/except with the `ClientOptions` timeouts as fallback; pin `supabase` to `>=2.28,<2.29` until the next deliberate bump.
5. **`maybe_single()` returning `None`** — unchanged semantics through to_thread.

## Verification

1. **Unit:** `run_db` propagates `LunaHTTPException` and contextvars (set a contextvar, assert visible inside `fn`).
2. **Timeout proof:** point `SUPABASE_URL` at a blackhole (e.g. `https://10.255.255.1`) in a test client → assert `GET /auth/me` fails in <8s (connect timeout), not 120s.
3. **Head-of-line load test (the S1 proof):** dev-only route `GET /_debug/slow` doing `await run_db(lambda: supabase.rpc("pg_sleep_wrapper", {"secs": 10}).execute())`. Fire 1 request at `/_debug/slow`, then 50 concurrent `GET /api/v1/conversations` with a real token. **Before Phase 1:** the 50 list requests serialize behind the sleep — p99 ≈ 10s. **After:** p99 within ~3× of single-request baseline (<300ms). Also 60 concurrent `/_debug/slow` + 10 `GET /healthz` → healthz stays <50ms (loop is free even when the 40-thread pool is saturated).
4. **Logfire dashboards:** per-route latency p99 before/after Phase 1; alert on `DbDeadlineExceeded` and `httpx.PoolTimeout` counts.

## Critical files
- `shared/db/client.py` — `_harden_sessions`, `ClientOptions` timeouts, both client factories
- `shared/db/run.py` — NEW: `run_db` / `run_db_deadline` / `DbDeadlineExceeded`
- `backend/app/main.py` — executor sizing in lifespan + 503 exception handler
- `backend/app/services/message_service.py` — inline hot-path wraps + dedup-slot reordering
- `backend/app/api/messages.py` — pre-flight wraps (Phase 1 entry point)

Sources: supabase-py issues #1306, #798, #915, #604; auth-py #537; Supabase Python docs.
