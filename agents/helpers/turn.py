"""``TurnHelpers``: the helpers of one user message (helper_frame.md §5.2).

Created by the orchestrator where OCR and the summary ran inline before, and
published on a ``ContextVar`` (:func:`bind_turn`) so every agent task created
later in the turn — the deep-search and writer tasks — can reach it without a new
parameter on every deps object. The editorial pinned-plan path creates none.

* ``start(name, item_ids)`` schedules a helper; as each run ends, the helpers
  that depend on it start (OCR → the summary of every item it filled, in
  parallel; summary → the segmenter, once registered).
* ``adopt(name, task)`` takes a run started elsewhere (the memory agent, whose
  per-conversation queue lives in its runner).
* ``await ready(consumer, item_ids=None)`` waits for every helper critical for
  ``consumer``, each up to its own ceiling, in dependency order. Never raises.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Iterable, Mapping

from .registry import REGISTRY, dependents, topo_order
from .spec import HelperContext, HelperSpec, Readiness, RunFn
from ._runtime import (
    _CURRENT,
    MESSAGE_KEY,
    HelperOutcome,
    current_turn,
    execute,
    inflight,
    latest_in_conversation,
    readiness_of,
    spawn,
    wait_tasks,
)
from .status import HelperRunStore, SupabaseHelperRunStore, auto_run

logger = logging.getLogger(__name__)


def bind_turn(turn: "TurnHelpers") -> None:
    """Publish ``turn`` for this context (and every task created from it)."""
    turn._token = _CURRENT.set(turn)


def unbind_turn() -> None:
    """Undo :func:`bind_turn`. No-op when nothing is bound. Never raises."""
    turn = _CURRENT.get()
    if turn is None:
        return
    token, turn._token = turn._token, None
    try:
        if token is None:
            raise ValueError
        _CURRENT.reset(token)
    except (ValueError, RuntimeError):
        _CURRENT.set(None)


@dataclass
class TurnReadiness:
    """What one ``ready`` call learnt. ``items``: per item, the worst of the item
    helpers waited for. ``helpers``: per helper (an item helper is ``ready`` only
    when all its items are)."""

    consumer: str
    helpers: dict[str, Readiness] = field(default_factory=dict)
    items: dict[str, Readiness] = field(default_factory=dict)

    def helper(self, name: str) -> Readiness:
        return self.helpers.get(name, Readiness.not_applicable("not_waited"))


_RANK = {"ready": 0, "not_applicable": 1, "fallback": 2}


def _worse(a: Readiness | None, b: Readiness) -> Readiness:
    if a is None:
        return b
    return b if _RANK[b.status] > _RANK[a.status] else a


def _aggregate(out: Mapping[str, Readiness]) -> Readiness:
    worst: Readiness | None = None
    for r in out.values():
        worst = _worse(worst, r)
    if worst is None:
        return Readiness.not_applicable("not_started")
    if worst.ok:
        return Readiness.ready({k: r.result for k, r in out.items()})
    return worst


class TurnHelpers:
    """The helpers of one user message. See the module docstring."""

    def __init__(
        self,
        *,
        supabase: Any,
        user_id: str,
        conversation_id: str,
        case_id: str | None = None,
        message_id: str | None = None,
        registry: Mapping[str, HelperSpec] | None = None,
        runs: Mapping[str, RunFn] | None = None,
        store: HelperRunStore | None = None,
    ) -> None:
        self.supabase = supabase
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.case_id = case_id
        self.message_id = message_id
        self._registry = REGISTRY if registry is None else registry
        self._runs: dict[str, RunFn] = dict(runs or {})
        self._store = store
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._token: Any = None

    # -- identity ----------------------------------------------------------- #

    def context(
        self,
        helper: str,
        item_id: str | None = None,
        deps: Mapping[str, Any] | None = None,
    ) -> HelperContext:
        return HelperContext(
            helper=helper,
            supabase=self.supabase,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            case_id=self.case_id,
            message_id=self.message_id,
            item_id=item_id,
            deps=dict(deps or {}),
        )

    def runner_for(self, name: str) -> RunFn | None:
        spec = self._registry.get(name)
        return self._runs.get(name) or (spec.run if spec is not None else None)

    def _message_key(self) -> str:
        return f"{self.conversation_id}:{self.message_id or id(self)}"

    def _store_for(self, spec: HelperSpec) -> HelperRunStore | None:
        if not spec.stored:
            return None
        if self._store is None:
            self._store = SupabaseHelperRunStore(self.supabase)
        return self._store

    # -- starting ----------------------------------------------------------- #

    def start(self, name: str, item_ids: Iterable[str] | None = None) -> None:
        """Schedule ``name`` (for each of ``item_ids`` if it is an item helper).
        A run already going for the same (helper, item) is joined. Never raises."""
        self._start(name, item_ids, {})

    def _start(
        self, name: str, item_ids: Iterable[str] | None, extra_deps: Mapping[str, Any]
    ) -> None:
        try:
            spec = self._registry.get(name)
            runner = self.runner_for(name)
            if spec is None or runner is None:
                logger.warning("helper %s cannot be started here", name)
                return
            if spec.scope == "message":
                self._start_one(spec, runner, None, extra_deps)
                return
            for item_id in dict.fromkeys(str(i) for i in (item_ids or []) if i):
                self._start_one(spec, runner, item_id, extra_deps)
        except Exception:  # noqa: BLE001 - a helper that cannot start is a fallback
            logger.warning("helper %s not started", name, exc_info=True)

    def _start_one(
        self,
        spec: HelperSpec,
        runner: RunFn,
        item_id: str | None,
        extra_deps: Mapping[str, Any],
    ) -> None:
        local = (spec.name, item_id or MESSAGE_KEY)
        if local in self._tasks:
            return
        key = item_id or self._message_key()
        running = inflight(spec.name, key)
        if running is not None:
            self._tasks[local] = running  # join it
            return
        ctx = self.context(spec.name, item_id, {**self._dep_results(spec, item_id), **extra_deps})
        after = partial(self._after, spec, item_id)
        if spec.stored:
            coro = auto_run(spec, ctx, runner, store=self._store_for(spec), after=after)
            task = spawn(spec.name, key, coro)
        else:
            previous = (
                latest_in_conversation(spec.name, self.conversation_id)
                if spec.queue_per_conversation else None
            )
            task = spawn(
                spec.name,
                key,
                execute(spec, ctx, runner, previous=previous, after=after),
                conversation_id=self.conversation_id,
                queue=spec.queue_per_conversation,
            )
        self._tasks[local] = task

    def adopt(self, name: str, task: asyncio.Task | None) -> None:
        """Take a run started elsewhere (the memory agent). ``None`` is ignored."""
        if task is None or name not in self._registry:
            return
        self._tasks[(name, MESSAGE_KEY)] = task

    def holds(self, task: Any) -> bool:
        return task is not None and any(t is task for t in self._tasks.values())

    def task(self, name: str, item_id: str | None = None) -> asyncio.Task | None:
        return self._tasks.get((name, item_id or MESSAGE_KEY))

    # -- the DAG ------------------------------------------------------------ #

    def _dep_results(self, spec: HelperSpec, item_id: str | None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for dep in spec.depends_on:
            dep_spec = self._registry.get(dep)
            if dep_spec is None:
                continue
            task = self._tasks.get((dep, item_id if dep_spec.scope == "item" else MESSAGE_KEY))
            if task is not None and task.done():
                got = readiness_of(task)
                if got.ok:
                    out[dep] = got.result
        return out

    def _deps_finished(
        self, spec: HelperSpec, item_id: str | None, finishing: tuple[str, str]
    ) -> bool:
        """Every dependency of ``spec`` for ``item_id`` has ended in this turn (or
        was never started in it). ``finishing`` is the run calling this, which
        has ended but whose task is not marked done yet."""
        for dep in spec.depends_on:
            dep_spec = self._registry.get(dep)
            if dep_spec is None:
                return False
            local = (dep, item_id if dep_spec.scope == "item" and item_id else MESSAGE_KEY)
            if local == finishing:
                continue
            task = self._tasks.get(local)
            if task is not None and not task.done():
                return False
        return True

    def _after(self, spec: HelperSpec, item_id: str | None, outcome: HelperOutcome) -> None:
        """A run ended: start what depends on it. Runs inside the run's task, so
        a consumer waiting on it wakes up with the dependents already started."""
        finishing = (spec.name, item_id or MESSAGE_KEY)
        extra = {spec.name: outcome.result} if outcome.status == "done" else {}
        if spec.scope == "message":
            items: list[str] = []
            if spec.produces_items is not None and outcome.status == "done":
                items = [str(i) for i in (spec.produces_items(outcome.result) or []) if i]
            targets: list[str | None] = list(items)
        else:
            targets = [item_id]
        for dep_spec in dependents(spec.name, self._registry):
            if not dep_spec.auto_start:
                continue
            if dep_spec.scope == "message":
                if self._deps_finished(dep_spec, None, finishing):
                    self._start(dep_spec.name, None, extra)
                continue
            ready_items = [
                i for i in targets if i and self._deps_finished(dep_spec, i, finishing)
            ]
            if ready_items:
                self._start(dep_spec.name, ready_items, extra)

    # -- waiting ------------------------------------------------------------ #

    def _turn_tasks(
        self, spec: HelperSpec, item_ids: Iterable[str] | None
    ) -> dict[str, asyncio.Task]:
        if spec.scope == "message":
            task = self._tasks.get((spec.name, MESSAGE_KEY))
            return {MESSAGE_KEY: task} if task is not None else {}
        wanted = None if item_ids is None else {str(i) for i in item_ids}
        return {
            key: task
            for (name, key), task in self._tasks.items()
            if name == spec.name and (wanted is None or key in wanted)
        }

    async def ready(
        self,
        consumer: str,
        item_ids: Iterable[str] | None = None,
        *,
        only: Iterable[str] | None = None,
    ) -> TurnReadiness:
        """Wait for the helpers critical for ``consumer``, in dependency order,
        each up to its ceiling counted from when the wait on it starts. Helpers
        not started in this turn are ``not_applicable``. ``only`` limits the wait
        to those helpers. Never raises (except the consumer's cancellation)."""
        report = TurnReadiness(consumer)
        wanted = None if only is None else set(only)
        item_list = None if item_ids is None else list(item_ids)
        try:
            for spec in topo_order(self._registry):
                if wanted is not None and spec.name not in wanted:
                    continue
                if not spec.waits(consumer):
                    continue
                tasks = self._turn_tasks(spec, item_list)
                if not tasks:
                    report.helpers[spec.name] = Readiness.not_applicable("not_started")
                    continue
                out = await wait_tasks(consumer, spec, tasks, timeout=spec.ceiling_s)
                if spec.scope == "message":
                    report.helpers[spec.name] = out[MESSAGE_KEY]
                else:
                    for key, got in out.items():
                        report.items[key] = _worse(report.items.get(key), got)
                    report.helpers[spec.name] = _aggregate(out)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("helper frame: ready(%s) failed", consumer, exc_info=True)
        return report

    async def settle(self, name: str, *, consumer: str) -> Readiness:
        """Wait for one message helper of this turn, as ``consumer``. A consumer it
        is not critical for gets its state now, without waiting."""
        try:
            spec = self._registry.get(name)
            task = self._tasks.get((name, MESSAGE_KEY))
            if spec is None or task is None:
                return Readiness.not_applicable("not_started")
            if not spec.waits(consumer):
                return readiness_of(task) if task.done() else Readiness.fallback("not_critical")
            out = await wait_tasks(consumer, spec, {MESSAGE_KEY: task}, timeout=spec.ceiling_s)
            return out[MESSAGE_KEY]
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("helper frame: settle(%s) failed", name, exc_info=True)
            return Readiness.fallback("frame_error")


__all__ = [
    "TurnHelpers",
    "TurnReadiness",
    "bind_turn",
    "current_turn",
    "unbind_turn",
]
