"""Helper declarations: .claude/plans/helper_frame.md §5.1.

A **helper** prepares something an agent reads (an attachment's OCR text, its
summary, the conversation's memory entries). A **consumer** is an agent that reads
it. Each helper declares what it depends on and which consumers it is critical
for: a critical consumer waits for it up to the helper's ceiling, every other
consumer runs alongside it.

Vocabulary: helpers are Layer 4 Memory agents or plain code steps. They never
talk to the user. Their model tier is per helper (CLAUDE.md "Layer vs Tier").
"""
from __future__ import annotations

import typing
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Literal, Mapping, Optional

#: Every agent that may wait for a helper. A wait point names one of these, and a
#: test checks every name used at a wait point is in this list.
Consumer = Literal[
    "router",
    "deep_planner",
    "writer_planner",
    "analyzer",
    "case_expander",
    "reg_expander",
    "aggregator",
    "writer",
    "other",
]
CONSUMERS: tuple[str, ...] = typing.get_args(Consumer)

#: ``item``: one run per workspace item (summary, segmenter). ``message``: one run
#: per user message (OCR batch, the pre-router memory hook, the memory agent).
Scope = Literal["item", "message"]
SCOPES: tuple[str, ...] = typing.get_args(Scope)


@dataclass(frozen=True)
class HelperContext:
    """What one run of a helper gets. Built by the frame, never by the helper."""

    helper: str
    supabase: Any
    user_id: str
    conversation_id: Optional[str] = None
    case_id: Optional[str] = None
    message_id: Optional[str] = None
    item_id: Optional[str] = None
    attempt: int = 1
    #: Results of the helpers this one depends on, by name, when they ran in this
    #: turn. Empty for a run started by a consumer (``ensure_ready``).
    deps: Mapping[str, Any] = field(default_factory=dict)


RunFn = Callable[[HelperContext], Awaitable[Any]]
#: ``(supabase, item_id) -> stored result | None``. Synchronous (the repo's
#: Supabase client is sync); the frame calls it in a thread.
LoadFn = Callable[[Any, str], Any]
AppliesFn = Callable[[HelperContext], "bool | Awaitable[bool]"]


@dataclass(frozen=True)
class HelperSpec:
    """One row of the matrix (helper_frame.md §4).

    ``critical_for``: consumers that wait for this helper before they start.
    ``covered_upstream``: consumers it is critical for, but an earlier consumer in
    the same turn already waited for it — the matrix's ``(C)``. They never wait
    again, so a helper that missed its ceiling once does not hold the turn twice.
    Every consumer in neither set runs alongside it.
    """

    name: str
    scope: Scope
    depends_on: tuple[str, ...] = ()
    critical_for: frozenset[str] = frozenset()
    covered_upstream: frozenset[str] = frozenset()
    #: The longest a critical consumer waits, from when it starts waiting. ``None``
    #: = no ceiling: the consumer waits until the run ends.
    ceiling_s: Optional[float] = None
    #: Does the work and stores the result. ``None``: the run is started elsewhere
    #: and handed to the frame (``TurnHelpers.adopt``) — the memory agent.
    run: Optional[RunFn] = None
    #: Reads a stored result for an item (another turn / another process).
    load: Optional[LoadFn] = None
    #: Checked before a run: ``False`` → readiness ``not_applicable``, nothing runs.
    applies_to: Optional[AppliesFn] = None
    #: Per-user switch (``users.user_id``). ``False`` → ``not_applicable``.
    enabled: Optional[Callable[[str], bool]] = None
    #: A message helper's result → the items its item-scoped dependents run on
    #: (OCR → the attachments it filled this turn).
    produces_items: Optional[Callable[[Any], Iterable[str]]] = None
    #: Started by the frame as soon as its ``depends_on`` finish in a turn.
    auto_start: bool = True
    #: Status + lease in ``helper_runs`` (new item helpers). OCR and the summary
    #: keep their ``metadata`` markers and are not stored here.
    stored: bool = False
    #: A new run of this helper waits for the conversation's unfinished one in
    #: this process (OCR: two scans must not OCR the same file twice).
    queue_per_conversation: bool = False
    #: The run gets its own ``llm_calls`` scope, keyed to the turn's message, so a
    #: run that outlives the turn still lands its cost (the memory agent's rule).
    own_ledger_scope: bool = True
    #: How long a claimed lease lives without a renewal. Renewed every third of it
    #: while the run is alive, so a killed run is noticed within one lease.
    lease_s: float = 90.0
    #: Runs per item (same ``version``) before the frame gives up on it.
    max_attempts: int = 3
    version: str = "1"
    #: At most this many runs of the helper at once in this process (provider
    #: rate limits). ``None`` = no limit.
    concurrency: Optional[int] = None
    #: What a consumer uses when the helper is not ready (documentation — the
    #: matrix's last column; each consumer implements its own fallback).
    fallback: str = ""

    def __post_init__(self) -> None:
        if self.scope not in SCOPES:
            raise ValueError(f"helper {self.name}: unknown scope {self.scope!r}")
        unknown = (set(self.critical_for) | set(self.covered_upstream)) - set(CONSUMERS)
        if unknown:
            raise ValueError(f"helper {self.name}: unknown consumers {sorted(unknown)}")
        both = set(self.critical_for) & set(self.covered_upstream)
        if both:
            raise ValueError(
                f"helper {self.name}: {sorted(both)} both critical and covered upstream"
            )
        if self.name in self.depends_on:
            raise ValueError(f"helper {self.name} depends on itself")
        if self.stored and self.scope != "item":
            raise ValueError(f"helper {self.name}: only item helpers are stored")
        if self.ceiling_s is not None and self.ceiling_s < 0:
            raise ValueError(f"helper {self.name}: negative ceiling")

    def waits(self, consumer: str) -> bool:
        """Does ``consumer`` wait for this helper (as opposed to running alongside)?"""
        return consumer in self.critical_for


ReadinessStatus = Literal["ready", "fallback", "not_applicable"]


@dataclass(frozen=True)
class Readiness:
    """What a consumer learns about one helper result. Never an exception.

    ``fallback`` reasons: ``timeout`` (missed the ceiling — the run goes on and
    lands for later turns), ``failed``, ``cancelled``, ``not_started``,
    ``running_elsewhere`` (another process holds a live lease), ``not_critical``
    (this consumer does not wait for it), ``attempts_exhausted``,
    ``unknown_helper``, ``frame_error``.
    """

    status: ReadinessStatus
    reason: Optional[str] = None
    result: Any = None

    @property
    def ok(self) -> bool:
        return self.status == "ready"

    @staticmethod
    def ready(result: Any = None) -> "Readiness":
        return Readiness("ready", None, result)

    @staticmethod
    def fallback(reason: str) -> "Readiness":
        return Readiness("fallback", reason)

    @staticmethod
    def not_applicable(reason: str = "not_applicable") -> "Readiness":
        return Readiness("not_applicable", reason)


__all__ = [
    "AppliesFn",
    "CONSUMERS",
    "Consumer",
    "HelperContext",
    "HelperSpec",
    "LoadFn",
    "Readiness",
    "ReadinessStatus",
    "RunFn",
    "SCOPES",
    "Scope",
]
