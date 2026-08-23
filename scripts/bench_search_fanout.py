"""Find the concurrency knee for the Supabase vector-search RPCs.

⚠ THIS BENCHMARK DELIBERATELY SATURATES THE PRODUCTION DATABASE. Run it
OFF-PEAK, against a database nobody is depending on for the next few minutes.
It refuses to start without ``--yes-i-know`` (or an interactive confirmation).

WHY THIS EXISTS
---------------
On **2026-08-22** a production turn fanned out 16 concurrent search RPCs. All
sixteen died — every single one — with ``httpx.ReadTimeout`` at 15.0s, which is
``POSTGREST_TIMEOUT.read`` in ``shared/db/client.py``. Not one degraded call:
total loss of the retrieval phase.

Scraping Logfire for the batches around it produced this, at 1-2 samples per
width, which is why you are reading a benchmark instead of a conclusion:

    concurrent │ batch wall time
    ───────────┼──────────────────────────
        4      │ 1.25 - 2.5s
        5      │ 1.09 - 1.48s
        6      │ 2.2  - 3.8s
        7      │ 3.1  - 4.5s
        8      │ 5.4  - 12.7s
        9      │ 10.3 - 13.4s
       16      │ >15s, TOTAL FAILURE

A single query, alone, is ~0.9s. So the shape of the hypothesis is not "each
call gets slower under load" — that would be benign, throughput would still
climb. It is that past a knee somewhere around 4-6, the *whole batch* takes
longer than a narrower batch would have. More concurrency buys negative work.
If that is real, the fix is a cap, and the cap should come from measurement
rather than from picking a number that feels safe.

This script sweeps N = 1..16 with ``--reps`` repetitions per width and reports
p50/p95 batch wall time, p50/p95 per-call latency, and the timeout/error count
at every width, then names the knee.

WHAT IT MEASURES, AND THROUGH WHAT
----------------------------------
Both live RPCs, verified against prod (project ``dwgghvxogtwyaxmbgjod``):

    search_topics(
        p_query_embedding vector,
        p_types           text[] DEFAULT NULL,   -- NULL = all four source types
        p_per_type        integer DEFAULT 15,
        p_sectors         text[] DEFAULT NULL,
        p_entity_ref      text    DEFAULT NULL,
        p_overfetch       integer DEFAULT 4      -- RPC self-tunes hnsw.ef_search
    )

    search_case_topics(
        p_kind            case_topic_kind,       -- fact | principle | basis
        p_query_embedding vector,
        p_sectors         text[] DEFAULT NULL,
        p_match_count     integer DEFAULT 60
    )

It calls them through ``shared.db.client.get_supabase_client()`` — the SAME
hardened httpx session production uses: HTTP/1.1 only (no h2 multiplexing),
``POSTGREST_TIMEOUT`` per socket op, a 50-connection pool. Measuring through a
freshly built client, or through asyncpg, or through the MCP SQL tool would
produce a number about a different system and tell you nothing about the
incident. The fan-out mechanism is production's too: ``asyncio.to_thread`` over
the shared sync client, launched with ``asyncio.gather``.

It does NOT import ``agents/deep_search_v4/shared/db_gate.py``. A gate is the
thing whose limit we are trying to choose; benchmarking through it would
measure the gate. This runs UNGATED on purpose.

CENSORED OBSERVATIONS (read this before trusting a p95)
-------------------------------------------------------
``POSTGREST_TIMEOUT.read`` is a hard ceiling on what is *measurable*. A call
that trips it did not take exactly that long — it took *at least* that long and
we will never know by how much. Recording it as the bound would drag every
percentile toward a number the database never produced, and would make a total
wipeout look like a merely slow batch.

The bound is IMPORTED from ``shared.db.client``, never copied, because it
moves: the 2026-08-22 incident censored at read=15.0s and the constant has
since been raised to 25.0s. Every run stamps the value it actually used into
the artifact (``read_timeout_bound_s``), so two runs taken under different
bounds are never silently compared.

So timeouts are recorded as **right-censored at the read bound**, never as a
latency value:

  * Percentiles use nearest-rank over ALL observations, with censored ones
    sorted above every completed one (they are, by construction). If the
    order statistic for a quantile lands inside the censored set, that
    quantile is itself unknown and prints as ``>=15.0`` — an honest
    lower bound, not a fabricated number.
  * A batch containing at least one timeout has a censored wall time too:
    its duration is an artifact of the timeout, not of the database's work.
  * Non-timeout errors (connection reset, 5xx, pool exhaustion) are NOT
    latency observations at all. They are counted and excluded.
  * Throughput and the knee are computed from clean batches only. A width
    with no clean batch has collapsed, and that is reported as collapse
    rather than as a slow number.

EMBEDDINGS
----------
Pulled ONCE at startup from real rows (``search_topics.topic_embedding`` and
``case_topics.embedding``, both vector(1024)) and reused for every timed call.
No embedding API is touched inside the timed loop — an Alibaba round trip in
there would be measuring Alibaba. Each call in a batch gets a *different*
vector from the pool, rotating across batches, so Postgres cannot serve a
whole width out of one hot HNSW neighbourhood and flatter the high-N numbers.
Different vectors do have different neighbourhood costs; ``--reps`` averages
that out, and ``--seed`` makes the sample reproducible across runs.

METHOD NOTES
------------
  * ``--warmup`` calls run before timing so TLS handshakes and pool fill are
    not billed to width 1.
  * The sweep is REP-MAJOR by default (rep 1 visits every width, then rep 2,
    …). A width-major sweep would load any drift in database load onto the
    late — i.e. the high-N — widths and manufacture the very knee we are
    looking for. ``--sequential`` restores width-major if you want it.
  * ``--settle`` seconds of quiet between batches so one batch's queue does
    not spill into the next one's measurement.
  * The default thread pool is sized ``max_concurrency + 4`` explicitly. The
    stdlib default is ``min(32, cpu_count + 4)``, which on a small container
    can serialize a width-16 batch and disguise a thread-pool bottleneck as
    database contention.

USAGE
-----
    python scripts/bench_search_fanout.py --dry-run
    python scripts/bench_search_fanout.py --yes-i-know --reps 5
    python scripts/bench_search_fanout.py --yes-i-know --max-concurrency 8
    python scripts/bench_search_fanout.py --yes-i-know --rpc mixed --reps 7

Artifacts land in ``agents_reports/bench_search_fanout_<timestamp>.{json,csv}``.

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY (via shared.config / shared.db.client).
Secrets are read through ``shared.config`` and are never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

# Make the repo root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252, which can't encode Arabic — force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

try:
    from dotenv import load_dotenv

    # Pin to the repo root: bare load_dotenv() searches upward from the CWD, so
    # running this from anywhere but the repo root would silently find no .env
    # and fail deep inside get_settings() on a missing SUPABASE_* field.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # noqa: BLE001
    pass

import httpx

# NOTE: get_supabase_client is imported, not called, at module scope — --dry-run
# must work on a machine with no credentials. POSTGREST_TIMEOUT is the censoring
# bound; it is read from the client module so the two can never drift apart.
from shared.db.client import POSTGREST_TIMEOUT, get_supabase_client

# ---------------------------------------------------------------------------
# Defaults mirrored from the production callers, so the benchmark asks the
# database for the same amount of work a real turn does.
#   PER_TYPE      — agents/deep_search_v4/reg_compliance_search/search.py:55
#   MATCH_COUNT   — search_case_topics p_match_count default (migration 101)
#   CASE_KINDS    — the case_topic_kind enum, verified live
# ---------------------------------------------------------------------------
PER_TYPE = 15
MATCH_COUNT = 60
CASE_KINDS = ("fact", "principle", "basis")

# The read bound past which nothing is measurable — only censorable.
READ_BOUND = float(POSTGREST_TIMEOUT.read or 15.0)

REPORTS_DIR = Path(__file__).resolve().parent.parent / "agents_reports"

BANNER = r"""
================================================================================
  ⚠  bench_search_fanout — THIS SATURATES THE PRODUCTION DATABASE  ⚠
================================================================================
  It fires up to {maxn} concurrent vector-search RPCs at a time, repeatedly,
  with no gate and no backpressure. While it runs, real user turns sharing the
  same Postgres will contend with it and may time out at {bound:.1f}s exactly
  the way the 2026-08-22 incident did.

  RUN OFF-PEAK. RUN IT ON PURPOSE.

  planned load : {batches} batches, {calls} RPC calls, ~{secs:.0f}s best case
  target       : {host}
================================================================================
"""


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


@dataclass
class CallObs:
    """One RPC call. ``status`` decides how it may be used.

    ok      — ``elapsed`` is a real latency.
    timeout — right-censored: the call took AT LEAST ``READ_BOUND``. ``elapsed``
              is kept for the record but must never enter a percentile as a
              value; it is a lower bound.
    error   — not a latency observation at all. Counted, then excluded.
    """

    rpc: str
    width: int
    rep: int
    slot: int
    status: str
    elapsed: float
    rows: int = 0
    error: str = ""


@dataclass
class BatchObs:
    """One fan-out of ``width`` simultaneous calls."""

    rpc: str
    width: int
    rep: int
    wall: float
    ok: int
    timeouts: int
    errors: int
    calls: list[CallObs] = field(default_factory=list)

    @property
    def censored(self) -> bool:
        """A batch holding a timeout has a wall time bounded by the timeout,
        not by the database. Treat it as censored, same as the call."""
        return self.timeouts > 0

    @property
    def clean(self) -> bool:
        return self.timeouts == 0 and self.errors == 0


# ---------------------------------------------------------------------------
# Censored statistics
# ---------------------------------------------------------------------------


@dataclass
class Quantile:
    """A quantile that knows whether it is a number or a lower bound."""

    value: float
    censored: bool

    def __str__(self) -> str:
        if math.isnan(self.value):
            return "  n/a "
        return f">={self.value:.2f}" if self.censored else f"{self.value:.2f}"

    def as_json(self) -> Optional[dict]:
        if math.isnan(self.value):
            return None
        return {"value": round(self.value, 4), "censored": self.censored}


def censored_quantile(
    completed: Iterable[float],
    n_censored: int,
    q: float,
    bound: float = READ_BOUND,
) -> Quantile:
    """Nearest-rank quantile over completed + right-censored observations.

    Every censored observation is known to be >= ``bound`` and >= every
    completed one, so they occupy the top of the sorted order. If the rank for
    ``q`` falls among them the quantile is unknown and comes back as the bound,
    flagged censored — e.g. 10% of calls timing out makes p95 report ``>=15.0``
    rather than inventing a value the database never produced.
    """
    vals = sorted(completed)
    total = len(vals) + n_censored
    if total == 0:
        return Quantile(float("nan"), False)
    rank = max(1, math.ceil(q * total))
    if rank <= len(vals):
        return Quantile(vals[rank - 1], False)
    return Quantile(bound, True)


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


def _parse_embedding(raw: Any) -> Optional[list[float]]:
    """PostgREST hands pgvector back as the text form ``"[0.1,0.2,...]"``.
    Accept a list too, in case a future postgrest-py decodes it natively."""
    if isinstance(raw, list):
        return [float(x) for x in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:  # noqa: BLE001
            return None
        if isinstance(parsed, list):
            return [float(x) for x in parsed]
    return None


def load_embedding_pool(
    client: Any, table: str, column: str, want: int, seed: int
) -> list[list[float]]:
    """Pull ``want`` real embeddings once, before any timing starts.

    Over-fetches and samples so the pool is not one contiguous ingest slice
    (which would be one topic cluster, one HNSW neighbourhood, one unusually
    warm set of pages).
    """
    over = max(want * 4, want + 50)
    resp = (
        client.table(table)
        .select(f"id,{column}")
        .not_.is_(column, "null")
        .limit(over)
        .execute()
    )
    rows = resp.data or []
    pool: list[list[float]] = []
    for row in rows:
        vec = _parse_embedding(row.get(column))
        if vec:
            pool.append(vec)
    if not pool:
        raise RuntimeError(
            f"no usable embeddings in {table}.{column} — cannot benchmark"
        )
    rng = random.Random(seed)
    rng.shuffle(pool)
    return pool[:want]


# ---------------------------------------------------------------------------
# The two calls under test
# ---------------------------------------------------------------------------


def _classify(exc: BaseException) -> tuple[str, str]:
    """Split a read timeout (censored observation) from every other failure.

    postgrest-py lets httpx exceptions through raw — the incident surfaced as a
    bare ``httpx.ReadTimeout`` — but walk the ``__cause__`` chain anyway in case
    a wrapper appears, and keep a name-based fallback so a future re-export
    cannot silently reclassify timeouts as generic errors and poison the stats.
    """
    seen: BaseException | None = exc
    depth = 0
    while seen is not None and depth < 6:
        if isinstance(seen, httpx.TimeoutException):
            return "timeout", f"{type(seen).__name__}: {seen}"
        if "timeout" in type(seen).__name__.lower():
            return "timeout", f"{type(seen).__name__}: {seen}"
        seen = seen.__cause__
        depth += 1
    return "error", f"{type(exc).__name__}: {exc}"[:300]


def make_topics_call(client: Any, per_type: int) -> Callable[[list[float]], tuple]:
    """search_topics as reg_compliance_search calls it: p_types omitted (NULL =
    all four source types), p_sectors omitted (an empty array would match
    nothing), p_overfetch left at the RPC default that self-tunes ef_search."""

    def _call(embedding: list[float]) -> tuple[str, float, int, str]:
        t0 = time.perf_counter()
        try:
            res = client.rpc(
                "search_topics",
                {"p_query_embedding": embedding, "p_per_type": per_type},
            ).execute()
            return "ok", time.perf_counter() - t0, len(res.data or []), ""
        except Exception as exc:  # noqa: BLE001
            status, msg = _classify(exc)
            return status, time.perf_counter() - t0, 0, msg

    return _call


def make_cases_call(client: Any, match_count: int) -> Callable[..., tuple]:
    """search_case_topics as case_search calls it: p_sectors always NULL (D3 —
    the filter is off pending a legal_domains backfill), kind cycled across the
    three enum values the way the per-channel fan-out does."""

    def _call(embedding: list[float], kind: str) -> tuple[str, float, int, str]:
        t0 = time.perf_counter()
        try:
            res = client.rpc(
                "search_case_topics",
                {
                    "p_kind": kind,
                    "p_query_embedding": embedding,
                    "p_sectors": None,
                    "p_match_count": match_count,
                },
            ).execute()
            return "ok", time.perf_counter() - t0, len(res.data or []), ""
        except Exception as exc:  # noqa: BLE001
            status, msg = _classify(exc)
            return status, time.perf_counter() - t0, 0, msg

    return _call


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


class Bench:
    def __init__(self, args: argparse.Namespace, client: Any) -> None:
        self.args = args
        self.client = client
        self.topics_call = make_topics_call(client, args.per_type)
        self.cases_call = make_cases_call(client, args.match_count)
        self.topic_pool: list[list[float]] = []
        self.case_pool: list[list[float]] = []
        self._offset = 0  # rotates so no two batches reuse the same vectors

    # -- one call ----------------------------------------------------------

    def _invoke(self, rpc: str, idx: int) -> tuple[str, float, int, str]:
        if rpc == "cases":
            pool = self.case_pool
            return self.cases_call(
                pool[idx % len(pool)], CASE_KINDS[idx % len(CASE_KINDS)]
            )
        pool = self.topic_pool
        return self.topics_call(pool[idx % len(pool)])

    # -- one batch of `width` simultaneous calls ---------------------------

    async def run_batch(self, rpc: str, width: int, rep: int) -> BatchObs:
        base = self._offset
        self._offset += width

        # `mixed` reproduces the incident's shape: reg and case searches in the
        # air at the same moment, contending for one pool and one database.
        if rpc == "mixed":
            kinds = ["topics" if i % 2 == 0 else "cases" for i in range(width)]
        else:
            kinds = [rpc] * width

        t0 = time.perf_counter()
        raw = await asyncio.gather(
            *[
                asyncio.to_thread(self._invoke, kinds[i], base + i)
                for i in range(width)
            ]
        )
        wall = time.perf_counter() - t0

        calls = [
            CallObs(
                rpc=kinds[i],
                width=width,
                rep=rep,
                slot=i,
                status=status,
                elapsed=elapsed,
                rows=rows,
                error=err,
            )
            for i, (status, elapsed, rows, err) in enumerate(raw)
        ]
        return BatchObs(
            rpc=rpc,
            width=width,
            rep=rep,
            wall=wall,
            ok=sum(c.status == "ok" for c in calls),
            timeouts=sum(c.status == "timeout" for c in calls),
            errors=sum(c.status == "error" for c in calls),
            calls=calls,
        )

    # -- warmup ------------------------------------------------------------

    async def warmup(self, rpcs: list[str]) -> None:
        n = self.args.warmup
        if n <= 0:
            return
        print(f"  warmup: {n} serial call(s) per RPC (pool fill + TLS, untimed)")
        for rpc in rpcs:
            probe = "topics" if rpc in ("topics", "mixed") else "cases"
            for _ in range(n):
                await self.run_batch(probe, 1, rep=-1)
            if rpc == "mixed":
                for _ in range(n):
                    await self.run_batch("cases", 1, rep=-1)

    # -- the whole sweep ---------------------------------------------------

    async def sweep(self, rpcs: list[str], widths: list[int]) -> list[BatchObs]:
        units: list[tuple[str, int, int]] = []
        if self.args.sequential:
            for rpc in rpcs:
                for width in widths:
                    for rep in range(self.args.reps):
                        units.append((rpc, width, rep))
        else:
            # Rep-major: spread any drift in database load evenly across all
            # widths instead of piling it onto the high-N end of the sweep.
            for rep in range(self.args.reps):
                for rpc in rpcs:
                    for width in widths:
                        units.append((rpc, width, rep))

        out: list[BatchObs] = []
        total = len(units)
        for i, (rpc, width, rep) in enumerate(units, start=1):
            batch = await self.run_batch(rpc, width, rep)
            out.append(batch)
            flag = ""
            if batch.timeouts:
                flag = f"  ⚠ {batch.timeouts} TIMEOUT"
            elif batch.errors:
                flag = f"  ⚠ {batch.errors} error"
            print(
                f"  [{i:>3}/{total}] {rpc:<6} N={width:<3} rep={rep} "
                f"wall={batch.wall:6.2f}s ok={batch.ok}{flag}",
                flush=True,
            )
            if self.args.settle > 0 and i < total:
                await asyncio.sleep(self.args.settle)
        return out


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def summarize(batches: list[BatchObs], rpc: str) -> list[dict]:
    """Per-width row: batch wall p50/p95, per-call latency p50/p95, failures.

    Percentiles are censored-aware; throughput uses clean batches only, because
    a batch that timed out has a wall time set by the timeout rather than by
    the database and would flatter the very widths we suspect of collapsing.
    """
    rows: list[dict] = []
    widths = sorted({b.width for b in batches if b.rpc == rpc})
    for width in widths:
        mine = [b for b in batches if b.rpc == rpc and b.width == width]

        clean_walls = [b.wall for b in mine if not b.censored]
        n_censored_batches = sum(1 for b in mine if b.censored)
        wall_p50 = censored_quantile(clean_walls, n_censored_batches, 0.50)
        wall_p95 = censored_quantile(clean_walls, n_censored_batches, 0.95)

        calls = [c for b in mine for c in b.calls]
        ok_lat = [c.elapsed for c in calls if c.status == "ok"]
        n_timeout = sum(1 for c in calls if c.status == "timeout")
        n_error = sum(1 for c in calls if c.status == "error")
        lat_p50 = censored_quantile(ok_lat, n_timeout, 0.50)
        lat_p95 = censored_quantile(ok_lat, n_timeout, 0.95)

        # Throughput from clean batches only; a width with none has collapsed.
        clean = [b for b in mine if b.clean]
        if clean:
            med = sorted(b.wall for b in clean)[len(clean) // 2]
            throughput = width / med if med > 0 else float("nan")
        else:
            throughput = float("nan")

        rows.append(
            {
                "rpc": rpc,
                "concurrent": width,
                "batches": len(mine),
                "clean_batches": len(clean),
                "calls": len(calls),
                "ok": len(ok_lat),
                "timeouts": n_timeout,
                "errors": n_error,
                "batch_wall_p50": wall_p50,
                "batch_wall_p95": wall_p95,
                "call_p50": lat_p50,
                "call_p95": lat_p95,
                "throughput_rps": throughput,
                "rows_median": (
                    sorted(c.rows for c in calls if c.status == "ok")[
                        max(0, len(ok_lat) // 2)
                    ]
                    if ok_lat
                    else 0
                ),
            }
        )
    return rows


def find_knee(rows: list[dict]) -> dict:
    """Name the width where more concurrency stops buying throughput.

    Three numbers, because they answer three different questions:

      peak_width      — where N / batch_wall is highest. The most work the
                        database will do for you at once.
      knee_width      — the last width before throughput turns over for real:
                        scanning upward, the first width that either COLLAPSED
                        or fell below `TOL` of the best throughput seen so far
                        ends the climb, and the width before it is the knee.
                        The `TOL` band matters — testing "is this sample lower
                        than the previous one" would call a knee on one noisy
                        batch, and with 5 reps against a live database there
                        will be noisy batches.
      recommended_cap — the NARROWEST width still within `TOL` of peak
                        throughput. Same work, less contention, more headroom
                        for whatever else is on the database. This is the
                        number to put in production.
    """
    TOL = 0.95
    usable = [r for r in rows if not math.isnan(r["throughput_rps"])]
    if not usable:
        return {
            "peak_width": None,
            "knee_width": None,
            "recommended_cap": None,
            "note": "every width collapsed — no clean batch anywhere",
        }

    peak = max(usable, key=lambda r: r["throughput_rps"])
    peak_tp = peak["throughput_rps"]

    # Walk EVERY width in order, collapsed ones included — a collapse is the
    # most decisive knee evidence there is, and skipping over it (as filtering
    # to `usable` first would) could let a noisy higher width mask it.
    knee: Optional[int] = None
    turned_over = False
    running_peak = float("-inf")
    last_good: Optional[int] = None
    for r in sorted(rows, key=lambda r: r["concurrent"]):
        tp = r["throughput_rps"]
        if math.isnan(tp) or (last_good is not None and tp < TOL * running_peak):
            knee = last_good
            turned_over = True
            break
        running_peak = max(running_peak, tp)
        last_good = r["concurrent"]
    if knee is None:
        # Never turned over inside the swept range. The knee is at or beyond
        # the widest width tried — say so rather than implying we found it.
        knee = last_good

    collapsed = [r["concurrent"] for r in rows if math.isnan(r["throughput_rps"])]

    cheapest = min(
        (r for r in usable if r["throughput_rps"] >= TOL * peak_tp),
        key=lambda r: r["concurrent"],
    )
    return {
        "peak_width": peak["concurrent"],
        "peak_throughput_rps": round(peak_tp, 3),
        "knee_width": knee,
        "knee_within_swept_range": turned_over,
        "recommended_cap": cheapest["concurrent"],
        "recommended_throughput_rps": round(cheapest["throughput_rps"], 3),
        "tolerance": TOL,
        "collapsed_widths": collapsed,
    }


def print_table(rows: list[dict], rpc: str) -> None:
    print()
    print(f"  ── {rpc} ───────────────────────────────────────────────────────")
    print(
        f"  {'N':>3} {'batches':>7} {'ok':>5} {'t/o':>4} {'err':>4} "
        f"{'wall p50':>9} {'wall p95':>9} {'call p50':>9} {'call p95':>9} "
        f"{'thru/s':>7}"
    )
    for r in rows:
        tp = r["throughput_rps"]
        tp_s = "COLLAPSE" if math.isnan(tp) else f"{tp:7.2f}"
        print(
            f"  {r['concurrent']:>3} {r['batches']:>7} {r['ok']:>5} "
            f"{r['timeouts']:>4} {r['errors']:>4} "
            f"{str(r['batch_wall_p50']):>9} {str(r['batch_wall_p95']):>9} "
            f"{str(r['call_p50']):>9} {str(r['call_p95']):>9} {tp_s:>7}"
        )


def print_knee(knee: dict, rpc: str) -> None:
    print()
    if knee.get("peak_width") is None:
        print(f"  KNEE ({rpc}): {knee.get('note')}")
        return
    print(f"  KNEE ({rpc}):")
    print(
        f"    peak throughput   N={knee['peak_width']} "
        f"({knee['peak_throughput_rps']} calls/s)"
    )
    if knee.get("knee_within_swept_range"):
        print(f"    curve turns over  N={knee['knee_width']}")
    else:
        print(
            f"    curve turns over  NOT REACHED — still climbing at "
            f"N={knee['knee_width']}; re-run with a larger --max-concurrency"
        )
    print(
        f"    RECOMMENDED CAP   N={knee['recommended_cap']} "
        f"({knee['recommended_throughput_rps']} calls/s — within "
        f"{int(knee['tolerance'] * 100)}% of peak at lower contention)"
    )
    if knee.get("collapsed_widths"):
        print(f"    collapsed widths  {knee['collapsed_widths']}")


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def _json_row(r: dict) -> dict:
    out = dict(r)
    for k in ("batch_wall_p50", "batch_wall_p95", "call_p50", "call_p95"):
        out[k] = r[k].as_json()
    tp = r["throughput_rps"]
    out["throughput_rps"] = None if math.isnan(tp) else round(tp, 4)
    return out


def write_artifacts(
    stamp: str,
    args: argparse.Namespace,
    per_rpc: dict[str, list[dict]],
    knees: dict[str, dict],
    batches: list[BatchObs],
) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS_DIR / f"bench_search_fanout_{stamp}.json"
    csv_path = REPORTS_DIR / f"bench_search_fanout_{stamp}.csv"

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label": args.label,
        "read_timeout_bound_s": READ_BOUND,
        "censoring": (
            "timeouts are right-censored at read_timeout_bound_s; quantiles "
            "landing in the censored set are reported as {censored:true} and "
            "mean '>= bound', not '== bound'"
        ),
        "config": {
            "reps": args.reps,
            "min_concurrency": args.min_concurrency,
            "max_concurrency": args.max_concurrency,
            "rpc": args.rpc,
            "per_type": args.per_type,
            "match_count": args.match_count,
            "warmup": args.warmup,
            "settle_s": args.settle,
            "threads": args.threads,
            "embeddings": args.embeddings,
            "seed": args.seed,
            "order": "width-major" if args.sequential else "rep-major",
        },
        "summary": {k: [_json_row(r) for r in v] for k, v in per_rpc.items()},
        "knee": knees,
        "batches": [
            {
                "rpc": b.rpc,
                "width": b.width,
                "rep": b.rep,
                "wall": round(b.wall, 4),
                "censored": b.censored,
                "ok": b.ok,
                "timeouts": b.timeouts,
                "errors": b.errors,
                "calls": [asdict(c) for c in b.calls],
            }
            for b in batches
        ],
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "rpc",
                "concurrent",
                "batches",
                "clean_batches",
                "calls",
                "ok",
                "timeouts",
                "errors",
                "batch_wall_p50",
                "batch_wall_p50_censored",
                "batch_wall_p95",
                "batch_wall_p95_censored",
                "call_p50",
                "call_p50_censored",
                "call_p95",
                "call_p95_censored",
                "throughput_rps",
            ]
        )
        for rpc, rows in per_rpc.items():
            for r in rows:
                tp = r["throughput_rps"]
                w.writerow(
                    [
                        rpc,
                        r["concurrent"],
                        r["batches"],
                        r["clean_batches"],
                        r["calls"],
                        r["ok"],
                        r["timeouts"],
                        r["errors"],
                        "" if math.isnan(r["batch_wall_p50"].value) else f"{r['batch_wall_p50'].value:.4f}",
                        int(r["batch_wall_p50"].censored),
                        "" if math.isnan(r["batch_wall_p95"].value) else f"{r['batch_wall_p95'].value:.4f}",
                        int(r["batch_wall_p95"].censored),
                        "" if math.isnan(r["call_p50"].value) else f"{r['call_p50'].value:.4f}",
                        int(r["call_p50"].censored),
                        "" if math.isnan(r["call_p95"].value) else f"{r['call_p95'].value:.4f}",
                        int(r["call_p95"].censored),
                        "" if math.isnan(tp) else f"{tp:.4f}",
                    ]
                )
    return json_path, csv_path


# ---------------------------------------------------------------------------
# Plan / consent
# ---------------------------------------------------------------------------


def build_plan(args: argparse.Namespace) -> dict:
    widths = list(range(args.min_concurrency, args.max_concurrency + 1))
    rpcs = ["topics", "cases"] if args.rpc == "both" else [args.rpc]
    batches = len(widths) * args.reps * len(rpcs)
    calls = sum(widths) * args.reps * len(rpcs)
    warmup_calls = args.warmup * len(rpcs) * (2 if args.rpc == "mixed" else 1)
    # ~0.9s solo per call, and a batch is at least as long as one call.
    best_case = batches * (0.9 + args.settle) + warmup_calls * 0.9
    return {
        "widths": widths,
        "rpcs": rpcs,
        "batches": batches,
        "calls": calls + warmup_calls,
        "best_case_secs": best_case,
    }


def confirm(args: argparse.Namespace, plan: dict) -> bool:
    settings_host = "(SUPABASE_URL from shared.config — not printed)"
    try:
        from shared.config import get_settings

        # Host only. Never the key, never the full URL with any query part.
        settings_host = httpx.URL(get_settings().SUPABASE_URL).host
    except Exception:  # noqa: BLE001
        pass

    print(
        BANNER.format(
            maxn=args.max_concurrency,
            bound=READ_BOUND,
            batches=plan["batches"],
            calls=plan["calls"],
            secs=plan["best_case_secs"],
            host=settings_host,
        )
    )
    if args.yes_i_know:
        print("  --yes-i-know given. Proceeding.\n")
        return True
    if not sys.stdin.isatty():
        print(
            "  REFUSING: no --yes-i-know and stdin is not a terminal, so there\n"
            "  is nobody to confirm. Re-run with --yes-i-know.\n"
        )
        return False
    try:
        answer = input("  Type SATURATE to proceed: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  aborted.")
        return False
    if answer != "SATURATE":
        print("  aborted.\n")
        return False
    print()
    return True


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


async def run(args: argparse.Namespace, plan: dict) -> int:
    loop = asyncio.get_running_loop()
    # Size the pool ourselves — the stdlib default (min(32, cpu+4)) can be
    # narrower than max_concurrency on a small box and would serialize the wide
    # batches, disguising a thread-pool bottleneck as database contention.
    executor = ThreadPoolExecutor(
        max_workers=args.threads, thread_name_prefix="bench_fanout"
    )
    loop.set_default_executor(executor)
    print(f"  thread pool: {args.threads} workers (max width {args.max_concurrency})")

    client = get_supabase_client()
    bench = Bench(args, client)

    need_topics = any(r in ("topics", "mixed") for r in plan["rpcs"])
    need_cases = any(r in ("cases", "mixed") for r in plan["rpcs"])

    print("  loading fixed embedding pool (once, before any timing)...")
    if need_topics:
        bench.topic_pool = load_embedding_pool(
            client, "search_topics", "topic_embedding", args.embeddings, args.seed
        )
        print(f"    search_topics.topic_embedding: {len(bench.topic_pool)} vectors")
    if need_cases:
        try:
            bench.case_pool = load_embedding_pool(
                client, "case_topics", "embedding", args.embeddings, args.seed + 1
            )
            print(f"    case_topics.embedding:        {len(bench.case_pool)} vectors")
        except Exception as exc:  # noqa: BLE001
            if not bench.topic_pool:
                raise
            # Same 1024-d space; the database does comparable work either way.
            bench.case_pool = bench.topic_pool
            print(f"    case_topics.embedding unavailable ({exc}) — reusing topics pool")
    if need_topics and not bench.topic_pool:
        bench.topic_pool = bench.case_pool

    await bench.warmup(plan["rpcs"])

    print(f"\n  sweeping widths {plan['widths'][0]}..{plan['widths'][-1]}, "
          f"reps={args.reps}, order={'width-major' if args.sequential else 'rep-major'}\n")
    t0 = time.perf_counter()
    batches = await bench.sweep(plan["rpcs"], plan["widths"])
    elapsed = time.perf_counter() - t0

    per_rpc: dict[str, list[dict]] = {}
    knees: dict[str, dict] = {}
    for rpc in plan["rpcs"]:
        rows = summarize(batches, rpc)
        per_rpc[rpc] = rows
        knees[rpc] = find_knee(rows)
        print_table(rows, rpc)
        print_knee(knees[rpc], rpc)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path, csv_path = write_artifacts(stamp, args, per_rpc, knees, batches)

    total_timeouts = sum(b.timeouts for b in batches)
    print()
    print(f"  sweep took {elapsed:.1f}s; {total_timeouts} censored (timed-out) calls")
    if total_timeouts:
        print(
            f"  ⚠ censored calls are NOT latency values. Any p50/p95 printed as\n"
            f"    '>={READ_BOUND:.1f}' is a lower bound — the real number is unknown."
        )
    print(f"  wrote {json_path}")
    print(f"  wrote {csv_path}")
    executor.shutdown(wait=False)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Find the concurrency knee for search_topics / search_case_topics. "
            "SATURATES THE DATABASE — off-peak only."
        )
    )
    ap.add_argument(
        "--yes-i-know",
        action="store_true",
        help="acknowledge that this saturates the production database",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and exit without touching the database",
    )
    ap.add_argument("--reps", type=int, default=5, help="repetitions per width (default 5)")
    ap.add_argument(
        "--max-concurrency",
        type=int,
        default=16,
        help="widest fan-out to attempt (default 16 — the incident's width)",
    )
    ap.add_argument("--min-concurrency", type=int, default=1, help="narrowest width (default 1)")
    ap.add_argument(
        "--rpc",
        choices=("both", "topics", "cases", "mixed"),
        default="both",
        help="both = a separate sweep per RPC (default); mixed = one sweep "
        "alternating the two, closest to the incident's traffic shape",
    )
    ap.add_argument("--per-type", type=int, default=PER_TYPE, help=f"search_topics p_per_type (default {PER_TYPE})")
    ap.add_argument("--match-count", type=int, default=MATCH_COUNT, help=f"search_case_topics p_match_count (default {MATCH_COUNT})")
    ap.add_argument("--warmup", type=int, default=3, help="untimed serial calls per RPC first (default 3)")
    ap.add_argument("--settle", type=float, default=1.0, help="quiet seconds between batches (default 1.0)")
    ap.add_argument("--embeddings", type=int, default=64, help="size of the fixed embedding pool (default 64)")
    ap.add_argument("--threads", type=int, default=0, help="thread pool size (default max-concurrency + 4)")
    ap.add_argument("--seed", type=int, default=20260822, help="embedding sample seed (reproducible runs)")
    ap.add_argument(
        "--sequential",
        action="store_true",
        help="width-major sweep; default is rep-major so load drift does not "
        "land entirely on the high-N widths",
    )
    ap.add_argument("--label", default="", help="free-text label recorded in the artifact")
    args = ap.parse_args()

    if args.min_concurrency < 1 or args.max_concurrency < args.min_concurrency:
        print("error: need 1 <= --min-concurrency <= --max-concurrency")
        return 2
    if args.reps < 1:
        print("error: --reps must be >= 1")
        return 2
    if args.threads <= 0:
        args.threads = args.max_concurrency + 4

    plan = build_plan(args)

    if args.dry_run:
        print("\n  bench_search_fanout — DRY RUN (nothing touched the database)\n")
        print(f"    widths            {plan['widths']}")
        print(f"    reps per width    {args.reps}")
        print(f"    RPCs              {', '.join(plan['rpcs'])}")
        print(f"    batches           {plan['batches']}")
        print(f"    RPC calls         {plan['calls']} (incl. {args.warmup} warmup per RPC)")
        print(f"    order             {'width-major' if args.sequential else 'rep-major'}")
        print(f"    settle            {args.settle}s between batches")
        print(f"    thread pool       {args.threads} workers")
        print(f"    embedding pool    {args.embeddings} vectors, seed {args.seed}")
        print(f"    search_topics     p_per_type={args.per_type}, p_types=NULL, p_sectors=NULL")
        print(f"    search_case_topics p_match_count={args.match_count}, p_sectors=NULL, kinds={list(CASE_KINDS)}")
        print(f"    censoring bound   {READ_BOUND:.1f}s (POSTGREST_TIMEOUT.read)")
        print(f"    best case runtime ~{plan['best_case_secs']:.0f}s")
        print(f"    artifacts         {REPORTS_DIR / 'bench_search_fanout_<ts>.{json,csv}'}")
        print("\n    add --yes-i-know to actually run it. OFF-PEAK ONLY.\n")
        return 0

    if not confirm(args, plan):
        return 1

    try:
        return asyncio.run(run(args, plan))
    except KeyboardInterrupt:
        print("\n  interrupted — partial results discarded.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
