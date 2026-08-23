"""Process-wide admission gate for Supabase search RPCs.

WHY THIS EXISTS (incident 2026-08-22)
-------------------------------------
A single production turn fanned out **16 concurrent** Supabase RPCs — 10×
``search_topics`` (reg/compliance) + 6× ``search_case_topics`` (cases) — and
ALL 16 died with ``httpx.ReadTimeout`` at exactly 15.0s, the
``POSTGREST_TIMEOUT`` read ceiling in ``shared/db/client.py``. Not *some* of
them: the whole retrieval phase returned nothing, so the turn had no sources to
write from.

The failure is a **throughput knee**, not a slow query. A single search RPC in
isolation takes ~0.9s. Measured batch wall time against fan-out width:

    | in-flight | batch wall time |
    |-----------|-----------------|
    |     1     | ~0.9s           |
    |     4     | 1.25 – 2.5s     |
    |     6     | 2.2 – 3.8s      |
    |     8     | 5.4 – 12.7s     |
    |     9     | 10.3 – 13.4s    |
    |    16     | >15s → total collapse |

Read that table carefully: past ~6 concurrent, adding concurrency makes the
**total** slower, not merely each individual call slower. That is contention
thrashing (pgvector probe work + shared buffers + connection pool churn on one
Postgres instance), and it means the naive intuition — "more parallelism
finishes the batch sooner" — is inverted in this regime. Widening the fan-out
past the knee is strictly worse than queueing behind it.

WHY PROCESS-WIDE, NOT PER-EXECUTOR
----------------------------------
Before this module there was no global ceiling. ``reg_compliance_search`` and
``case_search`` each construct their OWN ``asyncio.Semaphore(state.concurrency)``
(``reg_compliance_search/loop.py:262``, ``case_search/loop.py:320`` and
``:694``) and those executors run *in parallel*, so peak in-flight was ~20 —
each executor politely bounded, the database catastrophically not.

The scarce resource is the **Postgres instance**, which is shared across every
executor, every turn, and every user. A per-executor cap cannot express that;
only a cap that lives above all of them can. So the gate is a module-level
singleton keyed on the running event loop, and every search RPC in the process
queues through it regardless of which executor, which turn, or which user
issued it.

    async with search_gate() as gate_wait_ms:
        rows = await asyncio.to_thread(_call)

CAVEAT — REPLICAS MULTIPLY THE CAP
----------------------------------
"Process-wide" is exactly that: the ceiling is per Python process. With N
backend replicas serving traffic, the effective ceiling the database sees is
``N × SEARCH_RPC_CONCURRENCY``. The default of 5 assumes a small replica count;
if the backend is scaled out horizontally, ``LUNA_SEARCH_RPC_CONCURRENCY`` must
come DOWN proportionally, or the knee returns at the same absolute in-flight
count. A true cross-process ceiling needs a shared token (Redis) — deliberately
not built here, because a shared lease adds a network round trip and a failure
mode to the exact path we are trying to make more reliable.

Note that the gate bounds *admission*, not duration. It is the necessary
partner of the raised 25s ``POSTGREST_TIMEOUT``: a higher timeout without an
admission cap would just convert fast failures into slow ones while pinning
``asyncio.to_thread`` worker threads for longer. Cap first, then extend the
timeout.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import weakref
from contextlib import asynccontextmanager
from typing import AsyncIterator

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Read a positive int from the environment, falling back on anything odd.

    A malformed knob must never stop the process from booting — a typo in a
    Railway variable would otherwise take the whole backend down at import
    time, which is a far worse outcome than running on the default.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        logger.warning("%s=%r is not an int — falling back to %d", name, raw, default)
        return default
    if value < 1:
        logger.warning("%s=%d is not >= 1 — falling back to %d", name, value, default)
        return default
    return value


def _env_float(name: str, default: float) -> float:
    """Read a positive float from the environment, falling back on anything odd."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        logger.warning("%s=%r is not a float — falling back to %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("%s=%s is not > 0 — falling back to %s", name, value, default)
        return default
    return value


# Maximum search RPCs in flight per process. 5 sits below the measured knee
# (6 is still healthy at 2.2-3.8s; 8 is where the wall time starts to blow up),
# leaving one slot of headroom for the non-search PostgREST traffic that shares
# the same Postgres instance and the same httpx connection pool.
SEARCH_RPC_CONCURRENCY: int = _env_int("LUNA_SEARCH_RPC_CONCURRENCY", 5)

# How long a caller may queue for a slot before giving up. 30s is deliberately
# generous: a 16-wide fan-out through a 5-wide gate is ~4 waves, and at the
# measured 5-in-flight batch time (~2s) that drains in well under 10s. Waiting
# is nearly always better than failing — the alternative is the caller having
# no rows at all. The bound exists to stop an unbounded pile-up when the
# database is genuinely wedged, not to shape normal traffic.
GATE_WAIT_S: float = _env_float("LUNA_SEARCH_GATE_WAIT_S", 30.0)

# Above this, the gate is visibly shaping the turn's latency — worth a log line
# so the knee shows up in telemetry instead of silently inflating p95.
_SLOW_WAIT_LOG_S: float = 5.0


class SearchGateTimeout(Exception):
    """Could not acquire a search slot within GATE_WAIT_S."""


# One semaphore PER EVENT LOOP, not per process.
#
# asyncio primitives bind to the loop that first awaits them; a module-level
# ``asyncio.Semaphore()`` built at import time attaches to whatever loop (or no
# loop) happens to be current during import and then raises or silently
# misbehaves when awaited from a different one. That matters here because the
# same module is imported by the FastAPI app loop, by ``asyncio.run`` in
# scripts/tests, and by pytest-asyncio's per-test loops.
#
# The map is weak-keyed so a finished loop (a test's, a script's) drops its
# semaphore automatically instead of accumulating one entry per loop for the
# lifetime of the process.
_SEMAPHORES: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def _get_semaphore() -> asyncio.Semaphore:
    """Return this event loop's gate semaphore, creating it on first use.

    No lock is needed: this runs on the event loop thread and contains no
    ``await``, so it cannot be preempted between the lookup and the insert.
    """
    loop = asyncio.get_running_loop()
    sem = _SEMAPHORES.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(SEARCH_RPC_CONCURRENCY)
        _SEMAPHORES[loop] = sem
        logger.debug(
            "search_gate: created semaphore (limit=%d) for loop %r",
            SEARCH_RPC_CONCURRENCY,
            loop,
        )
    return sem


@asynccontextmanager
async def search_gate() -> AsyncIterator[float]:
    """Acquire one search-RPC slot. Yields gate wait time in MILLISECONDS.

    Usage::

        async with search_gate() as gate_wait_ms:
            rows = await asyncio.to_thread(_call)

    The yielded value is how long the caller queued before the RPC started —
    log it alongside the RPC duration so a slow turn can be attributed to
    *queueing* (gate is doing its job, fan-out is too wide) rather than to a
    *slow database* (gate wait ~0, RPC itself slow). Without that split the two
    are indistinguishable in a latency histogram.

    Raises:
        SearchGateTimeout: no slot became available within ``GATE_WAIT_S``.
            Callers should treat this like any other search failure — degrade
            to fewer sources, never crash the turn.
    """
    sem = _get_semaphore()
    started = time.perf_counter()
    try:
        # Python 3.11+ ``Semaphore.acquire`` is cancellation-safe: if the
        # wait_for timeout cancels a waiter that had already been handed the
        # slot, acquire gives the value back and wakes the next waiter. So a
        # timeout here cannot leak permits.
        await asyncio.wait_for(sem.acquire(), GATE_WAIT_S)
    except asyncio.TimeoutError as exc:
        waited_ms = (time.perf_counter() - started) * 1000.0
        logger.warning(
            "search_gate: timed out after %.0fms waiting for 1 of %d slots",
            waited_ms,
            SEARCH_RPC_CONCURRENCY,
        )
        raise SearchGateTimeout(
            f"Could not acquire a search slot within {GATE_WAIT_S}s "
            f"(limit={SEARCH_RPC_CONCURRENCY})"
        ) from exc

    waited_s = time.perf_counter() - started
    if waited_s >= _SLOW_WAIT_LOG_S:
        logger.warning(
            "search_gate: waited %.1fs for a slot (limit=%d) — fan-out exceeds the cap",
            waited_s,
            SEARCH_RPC_CONCURRENCY,
        )

    try:
        # ``finally`` and not a bare release-after-yield: the body is an RPC
        # that can raise (ReadTimeout is the whole reason this module exists)
        # or be cancelled mid-flight, and either one leaking a permit would
        # shrink the gate permanently until the process restarts.
        yield waited_s * 1000.0
    finally:
        sem.release()


__all__ = [
    "GATE_WAIT_S",
    "SEARCH_RPC_CONCURRENCY",
    "SearchGateTimeout",
    "search_gate",
]
