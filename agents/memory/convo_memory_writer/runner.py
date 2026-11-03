"""Runner for the convo_memory_writer agent, the "memory agent".

**Never raises, and never touches the turn.** A failure here costs an entry, and
what the user said is still in the chat history.

It runs on every user message, beside the router or ahead of a resumed specialist.
``orchestrator._handle_message_inner`` starts it and holds the task. Four things
are deliberate. The first two come from how a turn ends:

* **The run has its own task and its own ledger scope**, the ``style_profiler``
  pattern. A chat turn must not wait for it after ``done``: the pipeline task holds
  the conversation's slot in ``_active_runs`` until the generator closes, so
  holding it open would turn the user's next quick message into a duplicate
  rejection and lose that send. The turn's own scope may therefore flush first,
  and a row appended to its buffer after that is lost silently. Hence a fresh
  scope here, stamped with the turn's ``message_id``. In the ledger the insert IS
  the settle, so a second flush for one message is safe.
* **Nothing cancels it.** ``asyncio.wait`` in the orchestrator leaves the task
  running at the ceiling, and the strong reference in ``_BACKGROUND_TASKS`` carries
  it past the end of the turn. The context is copied at creation, so the turn's
  masking codec still decodes at the write after the turn has reset its own.
* **One run at a time per conversation.** ``_update_entry`` writes by seq, so
  overlapping runs could let an older message overwrite a newer one, and that
  wrong value would then outrank documents for good. ``schedule_memory_writer``
  queues a run behind the conversation's unfinished one, and every run reads the
  rows itself, so it sees what the run before it wrote. The queue lives in this
  process only.
* **A failed run keeps what its finished requests cost.** ``run_tracked`` writes
  the ledger on success only, so a run that dies on its retries or its timeout
  would leave no row. Hence ``track_stage`` with a ``RunUsage`` that outlives the
  exception or the cancellation. A request reports its usage when it finishes, so
  a run whose only request is still in flight at the timeout leaves no row.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from pydantic_ai.usage import RunUsage

from agents.models import ChatMessageSnapshot
from agents.utils.conversation_memory import (
    MemoryEntry,
    apply_memory_ops,
    encode_entries,
    load_conversation_memory,
)
from agents.utils.history import neutralize_system_brackets
from agents.utils.today import today_line
from agents.utils.tracking import track_stage
from agents.utils.usage_sink import collect_llm_calls
from shared.observability import get_logfire

from .agent import MEMORY_WRITER_LIMITS, create_convo_memory_writer
from .deps import MemoryWriterDeps
from .models import MemoryOps, MemoryWriterInput, MemoryWriterOutput
from .prompts import MAX_ITEM_TITLES, build_user_message, numbers_source

logger = logging.getLogger(__name__)
_logfire = get_logfire()

# Above the orchestrator's 60-second ceiling, so a run that outlasts the wait can
# still land its entries for the next turn, and nothing re-reads a lost message as
# new. It also has to fit a stalled head, which takes the 65 seconds of
# ``MEMORY_WRITER_REQUEST_TIMEOUT_S``, and then a full fallback attempt.
MEMORY_WRITER_TIMEOUT_S = 120.0

_BACKGROUND_TASKS: set[asyncio.Task] = set()
#: The newest run of each conversation, so the next one can queue behind it.
_LATEST: dict[str, asyncio.Task] = {}


def build_memory_writer_input(
    *,
    rows: list[ChatMessageSnapshot],
    question: str,
    conversation_id: str,
    user_id: str,
    message_id: str | None = None,
    case_id: str | None = None,
) -> MemoryWriterInput:
    """The input for this message, from text that is already encoded.

    ``rows`` is ``orchestrator._load_recent_messages``, ``question`` the turn's own
    message. The user's row is saved before the turn starts, so it is normally the
    last row. It is then taken as the newest message and left out of the context,
    because the row can carry the tag saying which file came with the message, and
    the same text must not also read as an old message. When the rows do not end
    on it, a failed read for one, ``question`` is the newest message and every row
    is context. An old row can never read as new.

    ``question`` is neutralized because the rows already are: raw text here could
    forge a system line, and would never match its own row.
    """
    question = neutralize_system_brackets(question)
    context = list(rows)
    newest = question
    if context and context[-1].role == "user" and context[-1].content.endswith(question.strip()):
        newest = context.pop().content
    return MemoryWriterInput(
        conversation_id=conversation_id,
        user_id=user_id,
        user_message=newest,
        recent_messages=context,
        today=today_line(),
        message_id=message_id,
        case_id=case_id,
    )


async def _current_entries(input: MemoryWriterInput, supabase: Any) -> list[MemoryEntry] | None:
    """The rows as they stand, encoded, or ``None`` if they could not be read.

    The read goes to a thread. The encode stays on the event loop, because it
    mutates the codec the turn shares, and its new fakes are persisted before the
    model reads them. ``strict`` matters here: a failed read that came back as an
    empty list would look like an empty memory and invite duplicates, so a run
    that cannot read stops.
    """
    try:
        from backend.app.services.masking_service import active_codec, persist_new_mappings

        rows = await asyncio.to_thread(
            load_conversation_memory,
            supabase,
            input.conversation_id,
            input.user_id,
            strict=True,
        )
        entries = encode_entries(rows)
        persist_new_mappings(supabase, input.user_id, active_codec())
        return entries
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "convo_memory_writer read failed, skipping this run: "
            "conversation_id=%s error=%s",
            input.conversation_id, type(exc).__name__,
        )
        return None


def _load_item_titles(supabase: Any, conversation_id: str, user_id: str) -> list[str]:
    """The newest document titles, oldest first, in REAL text.

    The compaction item is the model's own summary, not a document. The filter on
    both ids is the scope enforcement: this client is ``service_role``. A title is
    often a raw file name, so it must not be able to write a system line.
    """
    resp = (
        supabase.table("workspace_items")
        .select("title")
        .eq("conversation_id", conversation_id)
        .eq("user_id", user_id)
        .neq("kind", "convo_context")
        .is_("deleted_at", "null")
        .order("created_at", desc=True)
        .limit(MAX_ITEM_TITLES)
        .execute()
    )
    return [
        neutralize_system_brackets(str(row.get("title") or ""))
        for row in reversed(getattr(resp, "data", None) or [])
    ]


async def _current_titles(input: MemoryWriterInput, supabase: Any) -> list[str]:
    """The document titles, encoded the way :func:`_current_entries` encodes rows.

    Titles only help name a document, so a failed read costs them and nothing else.
    """
    try:
        from backend.app.services.masking_service import (
            active_codec,
            encode_active,
            persist_new_mappings,
        )

        titles = await asyncio.to_thread(
            _load_item_titles, supabase, input.conversation_id, input.user_id
        )
        encoded = [encode_active(title) for title in titles]
        persist_new_mappings(supabase, input.user_id, active_codec())
        return encoded
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "convo_memory_writer titles not read: conversation_id=%s error=%s",
            input.conversation_id, type(exc).__name__,
        )
        return []


def _record_run(span: Any, result: Any) -> None:
    """Land the ledger row. The ``LUNA_TRACK_DISABLE`` handle has no ``record_run``."""
    try:
        span.record_run(result, slot="convo_memory_writer")
    except Exception as exc:  # noqa: BLE001
        logger.debug("convo_memory_writer: record_run failed: %s", type(exc).__name__)


async def _plan_ops(input: MemoryWriterInput) -> MemoryOps:
    """One bounded model run. Raises; :func:`_read_and_write` catches it.

    ``spent`` keeps what completed requests cost when the run dies or is cancelled.
    A request still in flight then has reported nothing, so it is not in there.
    """
    agent = create_convo_memory_writer()
    prompt = build_user_message(input)
    deps = MemoryWriterDeps(
        conversation_id=input.conversation_id,
        case_id=input.case_id,
        entries=input.entries,
        source=prompt,
        allowed=numbers_source(input),
    )
    spent = RunUsage()
    with track_stage(
        "convo_memory_writer.run",
        conversation_id=input.conversation_id,
        case_id=input.case_id,
        agent_family="memory",
        input_obj=deps,
    ) as span:
        try:
            result = await asyncio.wait_for(
                agent.run(
                    prompt,
                    deps=deps,
                    usage_limits=MEMORY_WRITER_LIMITS,
                    usage=spent,
                ),
                timeout=MEMORY_WRITER_TIMEOUT_S,
            )
        except (Exception, asyncio.CancelledError):
            _record_run(span, SimpleNamespace(usage=lambda: spent))
            raise
        _record_run(span, result)
    return result.output


def _conversation_is_live(supabase: Any, conversation_id: str, user_id: str) -> bool:
    """False once the conversation is soft-deleted, so a write would orphan a row.

    The delete trigger removes the entries once, when ``deleted_at`` is set, and a
    run that is mid-flight then would write after it. The filter on both ids is the
    scope enforcement: this client is ``service_role``.

    A small race remains: a delete between this read and the write still leaves a
    row. Closing it needs a check inside the database, so a migration, and the
    window is one round trip.
    """
    resp = (
        supabase.table("conversations")
        .select("conversation_id")
        .eq("conversation_id", conversation_id)
        .eq("user_id", user_id)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    return bool(getattr(resp, "data", None))


async def _read_and_write(input: MemoryWriterInput, supabase: Any) -> MemoryWriterOutput:
    """Read the rows and titles, plan, write. Never raises."""
    entries = await _current_entries(input, supabase)
    if entries is None:
        return MemoryWriterOutput(entries=[], failed=True, read_ok=False)
    input = replace(
        input, entries=entries, item_titles=await _current_titles(input, supabase)
    )
    try:
        with collect_llm_calls(
            supabase,
            conversation_id=input.conversation_id,
            user_id=input.user_id,
            message_id=input.message_id,
            case_id=input.case_id,
        ):
            ops = await _plan_ops(input)
        if (ops.add or ops.update or ops.remove) and not await asyncio.to_thread(
            _conversation_is_live, supabase, input.conversation_id, input.user_id
        ):
            logger.info(
                "convo_memory_writer: conversation deleted, nothing written: "
                "conversation_id=%s", input.conversation_id,
            )
            return MemoryWriterOutput(entries=list(input.entries), failed=True)
        result = await asyncio.to_thread(
            apply_memory_ops,
            supabase,
            user_id=input.user_id,
            conversation_id=input.conversation_id,
            entries=input.entries,
            add=[op.model_dump() for op in ops.add],
            update=[op.model_dump(exclude_none=True) for op in ops.update],
            remove=list(ops.remove),
        )
        return MemoryWriterOutput(
            entries=result.entries,
            added=result.added,
            updated=result.updated,
            removed=result.removed,
            failed=result.failed,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "convo_memory_writer failed: conversation_id=%s error=%s",
            input.conversation_id, type(exc).__name__,
        )
        return MemoryWriterOutput(entries=list(input.entries), failed=True)


async def run_memory_writer(input: MemoryWriterInput, supabase: Any) -> MemoryWriterOutput:
    """Turn the newest message into stored entries. Never raises.

    Works from the rows as they stand when the run starts. On any failure the
    output keeps those rows with ``failed=True``, and has none with ``read_ok=False``
    if they could not be read: see :class:`MemoryWriterOutput`.

    Log lines and the ``done`` event carry counts and the exception type only: an
    exception's message can quote a row. The tracking span is another matter: on
    a failed run ``track_stage`` stamps ``str(err)`` on its ``error`` attribute.
    """
    started = time.perf_counter()
    output = await _read_and_write(input, supabase)
    _logfire.info(
        "convo_memory_writer.done",
        conversation_id=input.conversation_id,
        recent_count=len(input.recent_messages),
        added=output.added,
        updated=output.updated,
        removed=output.removed,
        failed=output.failed,
        entry_count=len(output.entries),
        duration_s=round(time.perf_counter() - started, 2),
    )
    return output


def pending_memory_writer(conversation_id: str) -> asyncio.Task | None:
    """The conversation's unfinished run, the newest if several are queued."""
    task = _LATEST.get(conversation_id)
    return task if task is not None and not task.done() else None


async def _run_in_turn(
    previous: asyncio.Task | None, input: MemoryWriterInput, supabase: Any
) -> MemoryWriterOutput:
    """Wait for the run ahead, then go.

    ``asyncio.wait`` and not ``await previous``: how that run ended is not this
    run's concern, and cancelling this one must not reach it.
    """
    if previous is not None:
        await asyncio.wait({previous})
    return await run_memory_writer(input, supabase)


def schedule_memory_writer(input: MemoryWriterInput, supabase: Any) -> asyncio.Task | None:
    """Start the run behind the conversation's unfinished one, and keep a strong
    reference until it ends. Never raises.

    ``None`` when the task could not be created: the caller carries on with the
    memory it has.
    """
    conversation_id = input.conversation_id
    try:
        task = asyncio.get_running_loop().create_task(
            _run_in_turn(pending_memory_writer(conversation_id), input, supabase),
            name="convo_memory_writer.run",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("convo_memory_writer not started: %s", type(exc).__name__)
        return None

    def forget(done: asyncio.Task) -> None:
        # Only its own entry: a finished run must not evict a newer one.
        if _LATEST.get(conversation_id) is done:
            del _LATEST[conversation_id]

    _LATEST[conversation_id] = task
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    task.add_done_callback(forget)
    return task


__all__ = [
    "MEMORY_WRITER_TIMEOUT_S",
    "build_memory_writer_input",
    "pending_memory_writer",
    "run_memory_writer",
    "schedule_memory_writer",
]
