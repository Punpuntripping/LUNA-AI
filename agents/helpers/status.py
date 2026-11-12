"""Across turns and processes: helper_frame.md §5.3 (owner decision H6).

A consumer looking for an item helper's result checks, in order:

1. a run in this process (this turn, or an earlier turn still going) → wait on it;
2. a stored result (``spec.load``) → use it;
3. a live lease in ``helper_runs`` held by another turn / process → poll the
   stored status until the ceiling;
4. nothing, an expired lease (a redeploy killed the run) or a retryable failure
   → claim a fresh lease with a conditional write, so two consumers can't both
   start it, and start it now.

``helper_runs`` (migration 216) is for **new** item helpers (``spec.stored``).
OCR and the summary keep their ``workspace_items.metadata`` markers
(``ocr_status``, ``summary_attempt``), which other code reads. The memory agent
is message-scoped and keeps its own per-conversation queue.

Public entry point: :func:`ensure_ready`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Literal, Mapping, Optional, Protocol

from .spec import Consumer, HelperContext, HelperSpec, Readiness
from ._runtime import (
    HelperOutcome,
    call_on_wait,
    current_turn,
    execute,
    inflight,
    not_applicable_reason,
    readiness_of,
    spawn,
    utcnow,
    wait_span,
)

logger = logging.getLogger(__name__)

TABLE = "helper_runs"
#: How often a consumer re-reads a lease another process holds.
POLL_S = 2.0
#: After this long a waiting consumer's ``on_wait`` fires once (helper_frame.md
#: §7 Q4: the status line «نقرأ مستنداتك كاملة…» after ~5 s).
ON_WAIT_AFTER_S = 5.0
#: The status line a consumer shows through ``on_wait``.
WAIT_STATUS_TEXT = "نقرأ مستنداتك كاملة…"

RunStatus = Literal["running", "done", "failed"]


@dataclass(frozen=True)
class RunRow:
    """One ``helper_runs`` row."""

    item_id: str
    helper: str
    status: str
    attempt: int
    lease_until: Optional[datetime] = None
    version: Optional[str] = None
    error: Optional[str] = None

    def lease_live(self, now: datetime) -> bool:
        return self.status == "running" and self.lease_until is not None and self.lease_until > now


class HelperRunStore(Protocol):
    """Status + lease storage. Synchronous (the frame calls it in a thread)."""

    def get(self, item_id: str, helper: str) -> RunRow | None: ...

    def claim(
        self,
        *,
        item_id: str,
        helper: str,
        user_id: str,
        prev: RunRow | None,
        attempt: int,
        lease_until: datetime,
        version: str,
    ) -> RunRow | None:
        """Take the lease. ``prev is None``: insert if no row exists. Otherwise a
        conditional update that matches only ``prev`` as read (same attempt and
        status, and for ``running`` an expired lease). ``None`` = someone else won."""
        ...

    def renew(self, row: RunRow, lease_until: datetime) -> bool: ...

    def finish(self, row: RunRow, *, status: str, error: str | None) -> bool: ...


def _parse_ts(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_row(data: Mapping[str, Any]) -> RunRow:
    return RunRow(
        item_id=str(data.get("item_id")),
        helper=str(data.get("helper")),
        status=str(data.get("status")),
        attempt=int(data.get("attempt") or 0),
        lease_until=_parse_ts(data.get("lease_until")),
        version=data.get("version"),
        error=data.get("error"),
    )


class SupabaseHelperRunStore:
    """``helper_runs`` over PostgREST, as service_role (the table has no client
    grants — migration 216). Every write is conditional on the row as read."""

    _COLS = "item_id,helper,status,attempt,lease_until,version,error"

    def __init__(self, supabase: Any) -> None:
        self._db = supabase

    def get(self, item_id: str, helper: str) -> RunRow | None:
        resp = (
            self._db.table(TABLE)
            .select(self._COLS)
            .eq("item_id", item_id)
            .eq("helper", helper)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        return _to_row(rows[0]) if rows else None

    def claim(
        self,
        *,
        item_id: str,
        helper: str,
        user_id: str,
        prev: RunRow | None,
        attempt: int,
        lease_until: datetime,
        version: str,
    ) -> RunRow | None:
        now = utcnow().isoformat()
        fields = {
            "status": "running",
            "attempt": attempt,
            "lease_until": lease_until.isoformat(),
            "started_at": now,
            "finished_at": None,
            "error": None,
            "version": version,
            "user_id": user_id,
        }
        if prev is None:
            # Insert-if-absent: a conflicting row is ignored and nothing comes back.
            resp = (
                self._db.table(TABLE)
                .upsert(
                    {**fields, "item_id": item_id, "helper": helper},
                    on_conflict="item_id,helper",
                    ignore_duplicates=True,
                )
                .execute()
            )
        else:
            query = (
                self._db.table(TABLE)
                .update(fields)
                .eq("item_id", item_id)
                .eq("helper", helper)
                .eq("attempt", prev.attempt)
                .eq("status", prev.status)
            )
            if prev.status == "running":
                query = query.lt("lease_until", now)  # only an expired lease is taken over
            resp = query.execute()
        rows = getattr(resp, "data", None) or []
        return _to_row(rows[0]) if rows else None

    def renew(self, row: RunRow, lease_until: datetime) -> bool:
        resp = (
            self._db.table(TABLE)
            .update({"lease_until": lease_until.isoformat()})
            .eq("item_id", row.item_id)
            .eq("helper", row.helper)
            .eq("attempt", row.attempt)
            .eq("status", "running")
            .execute()
        )
        return bool(getattr(resp, "data", None))

    def finish(self, row: RunRow, *, status: str, error: str | None) -> bool:
        resp = (
            self._db.table(TABLE)
            .update({
                "status": status,
                "finished_at": utcnow().isoformat(),
                "lease_until": None,
                "error": (error or None) and str(error)[:200],
            })
            .eq("item_id", row.item_id)
            .eq("helper", row.helper)
            .eq("attempt", row.attempt)
            .execute()
        )
        return bool(getattr(resp, "data", None))


# --------------------------------------------------------------------------- #
# One look at the stored state
# --------------------------------------------------------------------------- #

Verdict = Literal["ready", "run", "wait", "give_up", "not_applicable", "missing"]


async def decide(
    spec: HelperSpec,
    ctx: HelperContext,
    store: HelperRunStore | None,
    *,
    claim: bool,
) -> tuple[Verdict, Any]:
    """Steps 2–4 of §5.3, once, without waiting. ``claim=False`` (a consumer the
    helper is not critical for) never starts a run.

    Returns ``("ready", result)``, ``("run", RunRow | None)`` (this caller holds
    the lease — or the helper is not stored), ``("wait", RunRow | None)`` (another
    run holds it), ``("give_up", reason)``, ``("not_applicable", reason)`` or
    ``("missing", None)``.
    """
    item_id = str(ctx.item_id)
    # Off for this user → nothing to read or wait for (and no full-text read per
    # attachment while the helper is switched off).
    if spec.enabled is not None and not spec.enabled(ctx.user_id):
        return "not_applicable", "disabled"
    if spec.load is not None:
        stored = await asyncio.to_thread(spec.load, ctx.supabase, item_id)
        if stored is not None:
            return "ready", stored
    reason = await not_applicable_reason(spec, ctx)
    if reason is not None:
        return "not_applicable", reason
    if store is None:
        return ("run", None) if claim else ("missing", None)

    row = await asyncio.to_thread(store.get, item_id, spec.name)
    now = utcnow()
    if row is not None and row.lease_live(now):
        return "wait", row
    if not claim:
        return "missing", None
    attempt = 1
    if row is not None:
        # Done but nothing loads (a stale or missing result), failed, or a lease
        # that expired (the run was killed). Same version → counts against the
        # attempts; a new version starts over.
        if row.version == spec.version:
            if row.attempt >= spec.max_attempts:
                return "give_up", "attempts_exhausted"
            attempt = row.attempt + 1
    claimed = await asyncio.to_thread(
        lambda: store.claim(
            item_id=item_id,
            helper=spec.name,
            user_id=ctx.user_id,
            prev=row,
            attempt=attempt,
            lease_until=now + timedelta(seconds=spec.lease_s),
            version=spec.version,
        )
    )
    if claimed is None:
        return "wait", None  # someone else claimed it first
    return "run", claimed


def _with_attempt(ctx: HelperContext, row: RunRow | None) -> HelperContext:
    if row is None:
        return ctx
    return HelperContext(
        helper=ctx.helper,
        supabase=ctx.supabase,
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        case_id=ctx.case_id,
        message_id=ctx.message_id,
        item_id=ctx.item_id,
        attempt=row.attempt,
        deps=ctx.deps,
    )


def start_claimed(
    spec: HelperSpec,
    ctx: HelperContext,
    runner: Callable[[HelperContext], Any],
    *,
    store: HelperRunStore | None,
    row: RunRow | None,
    after: Callable[[HelperOutcome], None] | None = None,
) -> asyncio.Task:
    """Start a run whose lease this caller holds (or that is not stored)."""
    return spawn(
        spec.name,
        str(ctx.item_id),
        execute(
            spec,
            _with_attempt(ctx, row),
            runner,
            check_applicable=False,  # decide() already checked
            store=store,
            row=row,
            after=after,
        ),
    )


async def auto_run(
    spec: HelperSpec,
    ctx: HelperContext,
    runner: Callable[[HelperContext], Any],
    *,
    store: HelperRunStore | None,
    after: Callable[[HelperOutcome], None] | None = None,
) -> HelperOutcome:
    """A stored helper started by its dependencies finishing in a turn: claim and
    run it if needed, else report why not. Never polls (consumers do)."""
    try:
        verdict, value = await decide(spec, ctx, store, claim=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("helper %s: status lookup failed: %s", spec.name, type(exc).__name__)
        return HelperOutcome("failed", reason="store_error", error=type(exc).__name__)
    if verdict == "ready":
        return HelperOutcome("done", value)
    if verdict == "run":
        return await execute(
            spec, _with_attempt(ctx, value), runner,
            check_applicable=False, store=store, row=value, after=after,
        )
    if verdict == "wait":
        return HelperOutcome("running_elsewhere", reason="running_elsewhere")
    if verdict == "not_applicable":
        return HelperOutcome("not_applicable", reason=value)
    return HelperOutcome("failed", reason=value or "failed")


# --------------------------------------------------------------------------- #
# ensure_ready
# --------------------------------------------------------------------------- #


def _remaining(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


async def _ensure_item(
    spec: HelperSpec,
    ctx: HelperContext,
    consumer: str,
    runner: Callable[[HelperContext], Any] | None,
    store: HelperRunStore | None,
    deadline: float | None,
) -> Readiness:
    """One item, §5.3 steps 1–4, until ready or the deadline. Never raises
    (except the caller's own cancellation)."""
    critical = spec.waits(consumer)
    key = str(ctx.item_id)
    try:
        while True:
            # 1. A run in this process — this turn's or an earlier one's.
            task = inflight(spec.name, key)
            if task is not None:
                if not critical:
                    return Readiness.fallback("not_critical")
                pending = {task}
                await asyncio.wait(pending, timeout=_remaining(deadline))
                got = readiness_of(task)
                if got.reason != "running_elsewhere":
                    return got
                # It found another process's lease: poll like any waiter.
            # 2–4. The stored state.
            verdict, value = await decide(spec, ctx, store, claim=critical and runner is not None)
            if verdict == "ready":
                return Readiness.ready(value)
            if verdict == "not_applicable":
                return Readiness.not_applicable(value)
            if verdict == "give_up":
                return Readiness.fallback(value)
            if verdict == "missing":
                return Readiness.fallback("not_critical" if not critical else "not_started")
            if verdict == "run":
                start_claimed(spec, ctx, runner, store=store, row=value)
                continue  # step 1 now finds it and waits
            # verdict == "wait": another process holds a live lease.
            if not critical:
                return Readiness.fallback("running_elsewhere")
            left = _remaining(deadline)
            if left is not None and left <= 0:
                return Readiness.fallback("timeout")
            await asyncio.sleep(POLL_S if left is None else min(POLL_S, left))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a consumer only ever sees a fallback
        logger.warning(
            "helper %s: ensure_ready failed for item_id=%s: %s",
            spec.name, key, type(exc).__name__, exc_info=True,
        )
        return Readiness.fallback("frame_error")


async def ensure_ready(
    helper: str,
    item_ids: Iterable[str],
    *,
    consumer: Consumer,
    supabase: Any,
    user_id: str,
    on_wait: Callable[[], Any] | None = None,
    store: HelperRunStore | None = None,
    registry: Mapping[str, HelperSpec] | None = None,
) -> dict[str, Readiness]:
    """The result of ``helper`` for each item, waiting if ``consumer`` must.

    For a consumer the helper is critical for: wait for a run in this process,
    else load a stored result, else poll another process's live lease, else
    claim a lease and start the run now — all within the helper's ceiling,
    counted from this call. For any other consumer: no wait and no new run;
    whatever is ready now, else ``fallback("not_critical")``.

    ``on_wait`` (sync or async, no arguments) fires once if the wait passes
    ``ON_WAIT_AFTER_S`` — the caller's cue to show ``WAIT_STATUS_TEXT``.

    Returns ``{item_id: Readiness}`` for every distinct id. Never raises; a
    missed ceiling leaves the run going and its result lands for later turns.
    """
    ids = [str(i) for i in dict.fromkeys(item_ids or []) if i]
    if not ids:
        return {}
    try:
        from .registry import REGISTRY

        spec = (REGISTRY if registry is None else registry).get(helper)
        if spec is None:
            return {i: Readiness.fallback("unknown_helper") for i in ids}
        if spec.scope != "item":
            return {i: Readiness.fallback("not_item_scoped") for i in ids}
        turn = current_turn()
        runner = (turn.runner_for(helper) if turn is not None else None) or spec.run
        if store is None and spec.stored:
            store = SupabaseHelperRunStore(supabase)
        deadline = None if spec.ceiling_s is None else time.monotonic() + spec.ceiling_s

        def _ctx(item_id: str) -> HelperContext:
            if turn is not None:
                return turn.context(helper, item_id)
            return HelperContext(helper=helper, supabase=supabase, user_id=user_id, item_id=item_id)

        with wait_span(consumer, helper, len(ids)) as out:
            gathered = asyncio.ensure_future(asyncio.gather(*(
                _ensure_item(spec, _ctx(i), consumer, runner, store, deadline) for i in ids
            )))
            try:
                done, _ = await asyncio.wait({gathered}, timeout=ON_WAIT_AFTER_S)
                if not done:
                    await call_on_wait(on_wait)
                    await asyncio.wait({gathered})
                out.update(zip(ids, gathered.result()))
            finally:
                # Our own pollers only — the helper runs are separate tasks and
                # are never cancelled here.
                if not gathered.done():
                    gathered.cancel()
            return dict(out)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("helper %s: ensure_ready failed: %s", helper, type(exc).__name__, exc_info=True)
        return {i: Readiness.fallback("frame_error") for i in ids}


__all__ = [
    "HelperRunStore",
    "ON_WAIT_AFTER_S",
    "POLL_S",
    "RunRow",
    "SupabaseHelperRunStore",
    "TABLE",
    "WAIT_STATUS_TEXT",
    "auto_run",
    "decide",
    "ensure_ready",
    "start_claimed",
]
