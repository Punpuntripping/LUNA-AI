"""The frame's moving parts, shared by ``turn.py`` and ``status.py``.

* **Tasks outlive the turn.** Every run is an ``asyncio`` task with a strong
  reference in ``_BACKGROUND`` (the memory agent's ``_BACKGROUND_TASKS`` rule): the
  SSE stream may end while a helper is still working, and its result must land.
* **One run per (helper, key).** ``_INFLIGHT`` maps it to the unfinished task in
  this process, whichever turn started it; a second request joins that task.
* **Waits never cancel.** ``asyncio.wait`` with a timeout, never ``wait_for``: a
  run that misses a consumer's ceiling goes on and its result is stored for later.
* **A run never raises into a consumer.** :func:`execute` catches the helper's
  exception, logs it, and returns a ``failed`` outcome; consumers see a fallback.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
import weakref
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterator, Literal, Mapping, Optional

from shared.observability import get_logfire

from .spec import HelperContext, HelperSpec, Readiness, RunFn

if TYPE_CHECKING:  # pragma: no cover
    from .status import HelperRunStore, RunRow
    from .turn import TurnHelpers

logger = logging.getLogger(__name__)
_logfire = get_logfire()

#: The key of a message helper's run inside one turn.
MESSAGE_KEY = "-"

#: Strong references: a helper task outlives the SSE stream that started it.
_BACKGROUND: set[asyncio.Task] = set()
#: (helper, key) → the unfinished run in this process, whichever turn started it.
#: key = item_id, or ``"<conversation_id>:<message_id>"`` for a message helper.
_INFLIGHT: dict[tuple[str, str], asyncio.Task] = {}
#: (helper, conversation_id) → the newest run, for ``queue_per_conversation``.
_CONVO_LATEST: dict[tuple[str, str], asyncio.Task] = {}
#: Per event loop, per helper: the ``concurrency`` semaphore. Keyed by loop
#: because an ``asyncio.Semaphore`` binds to the first loop that waits on it.
_SEMAPHORES: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]]" = (
    weakref.WeakKeyDictionary()
)

#: This turn's helpers. Set by the orchestrator before any agent task is created,
#: so ``asyncio.create_task`` copies it into deep-search / writer tasks.
_CURRENT: ContextVar[Optional["TurnHelpers"]] = ContextVar("helper_frame_turn", default=None)


def current_turn() -> Optional["TurnHelpers"]:
    """This turn's :class:`TurnHelpers`, or ``None`` (frame off, editorial pin)."""
    return _CURRENT.get()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


OutcomeStatus = Literal["done", "failed", "not_applicable", "running_elsewhere"]


@dataclass(frozen=True)
class HelperOutcome:
    """How one run ended. The value of every frame-made task (it never raises)."""

    status: OutcomeStatus
    result: Any = None
    reason: Optional[str] = None
    error: Optional[str] = None

    def readiness(self) -> Readiness:
        if self.status == "done":
            return Readiness.ready(self.result)
        if self.status == "not_applicable":
            return Readiness.not_applicable(self.reason or "not_applicable")
        return Readiness.fallback(self.reason or self.status)


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #


def inflight(helper: str, key: str) -> asyncio.Task | None:
    task = _INFLIGHT.get((helper, key))
    return task if task is not None and not task.done() else None


def latest_in_conversation(helper: str, conversation_id: str | None) -> asyncio.Task | None:
    if not conversation_id:
        return None
    task = _CONVO_LATEST.get((helper, conversation_id))
    return task if task is not None and not task.done() else None


def spawn(
    helper: str,
    key: str,
    coro: Awaitable[Any],
    *,
    conversation_id: str | None = None,
    queue: bool = False,
) -> asyncio.Task:
    """Start one run, keep it alive, register it. Raises only if the loop refuses."""
    try:
        task = asyncio.get_running_loop().create_task(coro, name=f"helper.{helper}")
    except Exception:
        if inspect.iscoroutine(coro):
            coro.close()
        raise
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    _INFLIGHT[(helper, key)] = task

    def _forget(done: asyncio.Task) -> None:
        # Only its own entry: a finished run must not evict a newer one.
        if _INFLIGHT.get((helper, key)) is done:
            del _INFLIGHT[(helper, key)]
        if queue and conversation_id and _CONVO_LATEST.get((helper, conversation_id)) is done:
            del _CONVO_LATEST[(helper, conversation_id)]

    task.add_done_callback(_forget)
    if queue and conversation_id:
        _CONVO_LATEST[(helper, conversation_id)] = task
    return task


def readiness_of(task: asyncio.Task) -> Readiness:
    """A task's readiness right now. A frame task returns a ``HelperOutcome``; an
    adopted one (the memory agent) returns its own result or raises."""
    if not task.done():
        return Readiness.fallback("timeout")
    if task.cancelled():
        return Readiness.fallback("cancelled")
    if task.exception() is not None:
        return Readiness.fallback("failed")
    result = task.result()
    if isinstance(result, HelperOutcome):
        return result.readiness()
    return Readiness.ready(result)


# --------------------------------------------------------------------------- #
# One run
# --------------------------------------------------------------------------- #


async def not_applicable_reason(spec: HelperSpec, ctx: HelperContext) -> str | None:
    """``None`` when the helper should run for ``ctx``; otherwise why not."""
    if spec.enabled is not None and not spec.enabled(ctx.user_id):
        return "disabled"
    if spec.applies_to is not None:
        verdict = spec.applies_to(ctx)
        if inspect.isawaitable(verdict):
            verdict = await verdict
        if not verdict:
            return "not_applicable"
    return None


def _limit(spec: HelperSpec) -> Any:
    if not spec.concurrency:
        return nullcontext()
    loop = asyncio.get_running_loop()
    per_loop = _SEMAPHORES.setdefault(loop, {})
    sem = per_loop.get(spec.name)
    if sem is None:
        sem = per_loop[spec.name] = asyncio.Semaphore(spec.concurrency)
    return sem


def _ledger_scope(spec: HelperSpec, ctx: HelperContext) -> Any:
    """Its own ``llm_calls`` scope, keyed to the turn's message (the memory agent's
    rule): the turn's scope may flush before a run that missed the ceiling ends,
    and a row added to a flushed buffer is lost. The insert is the settle, so a
    second flush for one message is safe. A run with no conversation (started by a
    consumer's ``ensure_ready`` outside a turn handle) runs DETACHED from the
    caller's scope, so a helper that opens its own scope when none is open (the
    segmenter, keyed to its item's conversation) does — rows added to the
    caller's buffer after it flushed would be lost."""
    if not spec.own_ledger_scope:
        return nullcontext()
    from agents.utils.usage_sink import collect_llm_calls, detached

    if not ctx.conversation_id:
        return detached()

    return collect_llm_calls(
        ctx.supabase,
        conversation_id=ctx.conversation_id,
        user_id=ctx.user_id,
        message_id=ctx.message_id,
        case_id=ctx.case_id,
    )


def _heartbeat(spec: HelperSpec, store: "HelperRunStore", row: "RunRow") -> asyncio.Task:
    """Renew the lease every third of it while the run is alive."""

    async def _beat() -> None:
        interval = max(spec.lease_s / 3.0, 0.01)
        while True:
            await asyncio.sleep(interval)
            try:
                kept = await asyncio.to_thread(
                    store.renew, row, utcnow() + timedelta(seconds=spec.lease_s)
                )
            except Exception as exc:  # noqa: BLE001 - try again next beat
                logger.warning("helper %s lease renewal failed: %s", spec.name, type(exc).__name__)
                continue
            if not kept:
                logger.warning(
                    "helper %s lost its lease: item_id=%s attempt=%s",
                    spec.name, row.item_id, row.attempt,
                )
                return

    return asyncio.get_running_loop().create_task(_beat(), name=f"helper.{spec.name}.lease")


def _set_span(span: Any, **attrs: Any) -> None:
    try:
        span.set_attributes(attrs)
    except Exception:  # noqa: BLE001 - telemetry never breaks a run
        pass


async def execute(
    spec: HelperSpec,
    ctx: HelperContext,
    runner: RunFn,
    *,
    previous: asyncio.Task | None = None,
    check_applicable: bool = True,
    store: "HelperRunStore | None" = None,
    row: "RunRow | None" = None,
    after: Callable[[HelperOutcome], None] | None = None,
) -> HelperOutcome:
    """Run a helper once. Returns its outcome; raises only ``CancelledError``.

    ``previous``: a run of the same helper this one queues behind (it may have
    ended any way at all). ``store`` + ``row``: the claimed lease, renewed while
    the run is alive and closed when it ends. ``after``: called with the outcome
    before the task completes, so dependents are registered before a consumer
    waiting on this run wakes up.
    """
    if previous is not None and not previous.done():
        await asyncio.wait({previous})
    started = time.perf_counter()
    beat: asyncio.Task | None = None
    outcome = HelperOutcome("failed", reason="failed")
    with _logfire.span(
        "helper.run",
        helper=spec.name,
        scope=spec.scope,
        item_id=ctx.item_id,
        attempt=ctx.attempt,
        conversation_id=ctx.conversation_id,
    ) as span:
        try:
            reason = await not_applicable_reason(spec, ctx) if check_applicable else None
            if reason is not None:
                outcome = HelperOutcome("not_applicable", reason=reason)
            else:
                if store is not None and row is not None:
                    beat = _heartbeat(spec, store, row)
                async with _limit(spec):
                    with _ledger_scope(spec, ctx):
                        result = await runner(ctx)
                outcome = HelperOutcome("done", result)
        except Exception as exc:  # noqa: BLE001 - consumers only ever see a fallback
            logger.warning(
                "helper %s failed: item_id=%s error=%s",
                spec.name, ctx.item_id, type(exc).__name__, exc_info=True,
            )
            outcome = HelperOutcome("failed", reason="failed", error=type(exc).__name__)
        finally:
            if beat is not None:
                beat.cancel()
        _set_span(
            span,
            ms=int((time.perf_counter() - started) * 1000),
            outcome=outcome.status,
            **({"reason": outcome.reason} if outcome.reason else {}),
        )
    if store is not None and row is not None:
        try:
            await asyncio.to_thread(
                store.finish,
                row,
                status="failed" if outcome.status == "failed" else "done",
                error=outcome.error or (outcome.reason if outcome.status != "done" else None),
            )
        except Exception as exc:  # noqa: BLE001 - the lease then just expires
            logger.warning("helper %s finish not stored: %s", spec.name, type(exc).__name__)
    if after is not None:
        try:
            after(outcome)
        except Exception:  # noqa: BLE001
            logger.warning("helper %s: starting its dependents failed", spec.name, exc_info=True)
    return outcome


# --------------------------------------------------------------------------- #
# Waits
# --------------------------------------------------------------------------- #


def wait_outcome(out: Mapping[str, Readiness]) -> str:
    """One word for a wait: ready / fallback / not_applicable."""
    statuses = {r.status for r in out.values()}
    if "fallback" in statuses:
        return "fallback"
    if "ready" in statuses:
        return "ready"
    return "not_applicable"


@contextmanager
def wait_span(consumer: str, helper: str, items: int) -> Iterator[dict[str, Readiness]]:
    """The ``helper.wait`` span (consumer, helper, items, waited_ms, outcome). The
    caller fills the yielded dict with what it learnt."""
    started = time.perf_counter()
    out: dict[str, Readiness] = {}
    with _logfire.span("helper.wait", consumer=consumer, helper=helper, items=items) as span:
        try:
            yield out
        finally:
            reasons = sorted({r.reason for r in out.values() if r.reason})
            _set_span(
                span,
                waited_ms=int((time.perf_counter() - started) * 1000),
                outcome=wait_outcome(out),
                **({"reasons": reasons} if reasons else {}),
            )


async def wait_tasks(
    consumer: str,
    spec: HelperSpec,
    tasks: Mapping[str, asyncio.Task],
    *,
    timeout: float | None,
) -> dict[str, Readiness]:
    """Wait for ``tasks`` up to ``timeout`` (``None`` = until they end). Never
    cancels them. Raises only when the waiting consumer itself is cancelled."""
    with wait_span(consumer, spec.name, len(tasks)) as out:
        pending = {task for task in tasks.values() if not task.done()}
        if pending:
            await asyncio.wait(pending, timeout=timeout)
        out.update({key: readiness_of(task) for key, task in tasks.items()})
        return dict(out)


async def call_on_wait(on_wait: Callable[[], Any] | None) -> None:
    """Fire the caller's "still waiting" hook (sync or async). Never raises."""
    if on_wait is None:
        return
    try:
        value = on_wait()
        if inspect.isawaitable(value):
            await value
    except Exception:  # noqa: BLE001 - a status line must not break the wait
        logger.debug("helper on_wait hook failed", exc_info=True)


__all__ = [
    "HelperOutcome",
    "MESSAGE_KEY",
    "call_on_wait",
    "current_turn",
    "execute",
    "inflight",
    "latest_in_conversation",
    "not_applicable_reason",
    "readiness_of",
    "spawn",
    "utcnow",
    "wait_outcome",
    "wait_span",
    "wait_tasks",
]
