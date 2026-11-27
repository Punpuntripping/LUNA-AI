"""Entry point of the attachment agent (.claude/plans/attachment_agent.md §2.2).

One router tool call = one run: load the files (encoded, the user's text fixes
applied) → persist the new fakes → run the model (90 s; edit tools only STAGE)
→ check every quote → for a change / re-analysis request, apply the staged
edits → the router's result. A run that errors or times out applies nothing.
Never raises: any failure comes back as ``outcome="error"`` with a plain Arabic
``note``.

Cost: ``record_run(slot="attachment_agent")`` feeds the ``llm_calls`` ledger of the
router turn's capture scope, like ``edit_artifact``. A run cut by the timeout or a
limit still records what it used. Edits cost no model call.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agents.utils.tracking import track_stage
from backend.app.services.masking_service import active_codec, persist_new_mappings

from .agent import ATTACHMENT_AGENT_LIMITS, SLOT, create_attachment_agent
from .apply import apply_staged
from .context import _enc, load_context, recent_messages, render_turn_message, turn_encoder
from .deps import AttachmentDeps
from .models import AttachmentAgentCall, AttachmentAgentResult
from .output import AttachmentOutputLLM
from .verify import Stats, edit_outcome, finalize

logger = logging.getLogger(__name__)

STAGE = "attachment_agent.run"
#: Around the model run only (loading is not counted).
AGENT_TIMEOUT_S = 90

NOTE_TIMEOUT = "تعذّر إكمال قراءة الملفات في الوقت المتاح."
NOTE_ERROR = "تعذّر الاطلاع على الملفات الآن بسبب خطأ تقني."
NOTE_NO_FILES = "لا توجد ملفات مرفوعة في هذه المحادثة."
NOTE_PICKED_MISSING = "لم تُوجد الملفات المقصودة بين مرفقات هذه المحادثة."
NOTE_NO_TEXT = "لم يُستخرج نص {names} بعد، فلا يمكن قراءته الآن."


def _error(note: str, error: str) -> AttachmentAgentResult:
    return AttachmentAgentResult(outcome="error", note=note, error=error[:300])


async def _drive(agent: Any, message: str, deps: AttachmentDeps, holder: dict) -> Any:
    """``agent.run`` by hand, so a run cut short still shows what it used."""
    async with agent.iter(message, deps=deps, usage_limits=ATTACHMENT_AGENT_LIMITS) as run:
        holder["run"] = run
        async for _node in run:
            pass
    return run.result


async def run_attachment_agent(
    call: AttachmentAgentCall, *, supabase: Any,
) -> AttachmentAgentResult:
    """Read and edit the user's files for one router call.

    Never raises: any failure comes back as ``outcome="error"`` with a plain
    ``note`` for the router. Handles its own time limit internally, so the
    router tool only awaits it.
    """
    t0 = time.perf_counter()
    deps: AttachmentDeps | None = None
    stats = Stats()
    result: AttachmentAgentResult
    try:
        with track_stage(
            STAGE,
            conversation_id=call.conversation_id,
            agent_family="attachment",
            items_picked=len(call.item_ids),
        ) as span:
            result, deps, stats = await _run(call, supabase, span)
            span.set(
                outcome_value=result.outcome,
                files_n=len(deps.files) if deps else 0,
                chars_read=deps.chars_read if deps else 0,
                searched_n=len(deps.searched) if deps else 0,
                quotes_kept=stats.quotes_kept,
                quotes_dropped=stats.quotes_dropped,
                points_dropped=stats.points_dropped,
                edits_staged=len(deps.staged_edits) if deps else 0,
                error=result.error,
            )
            if result.outcome == "error":
                span.set_outcome("error")
    except Exception as exc:  # noqa: BLE001 — the contract: never raise into the router
        logger.warning("attachment_agent failed: %s", type(exc).__name__, exc_info=True)
        result = _error(NOTE_ERROR, f"{type(exc).__name__}: {exc}")
    logger.info(
        "attachment_agent: outcome=%s files=%d chars_read=%d searched=%d quotes_kept=%d "
        "quotes_dropped=%d points_dropped=%d edits_staged=%d edits=%d duration_ms=%d%s",
        result.outcome,
        len(deps.files) if deps else 0,
        deps.chars_read if deps else 0,
        len(deps.searched) if deps else 0,
        stats.quotes_kept,
        stats.quotes_dropped,
        stats.points_dropped,
        len(deps.staged_edits) if deps else 0,
        len(result.edits),
        int((time.perf_counter() - t0) * 1000),
        f" error={result.error}" if result.error else "",
    )
    return result


async def _run(
    call: AttachmentAgentCall, supabase: Any, span: Any,
) -> tuple[AttachmentAgentResult, AttachmentDeps | None, Stats]:
    stats = Stats()
    try:
        loaded = await asyncio.to_thread(load_context, supabase, call)
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachment_agent: load failed (%s)", type(exc).__name__)
        return _error(NOTE_ERROR, f"load: {type(exc).__name__}"), None, stats

    if not loaded.files:
        if loaded.without_text:
            names = "، ".join(f"«{t}»" for t in loaded.without_text)
            note = NOTE_NO_TEXT.format(names=names)
        else:
            note = NOTE_PICKED_MISSING if call.item_ids else NOTE_NO_FILES
        return AttachmentAgentResult(outcome="not_found", note=note), None, stats

    deps = AttachmentDeps(
        supabase=supabase,
        user_id=call.user_id,
        conversation_id=call.conversation_id,
        message_id=call.message_id,
        files={f.label: f for f in loaded.files},
        maps=loaded.maps,
        # The evidence guard compares in the space the model read.
        user_message=_enc(turn_encoder(), call.user_message),
    )
    recent = await asyncio.to_thread(recent_messages, supabase, call)
    message = render_turn_message(call, loaded, recent)
    deps.source_text = message
    # Fakes minted while encoding must exist before the model reads them.
    persist_new_mappings(supabase, call.user_id, active_codec())
    span.set(
        files_n=len(loaded.files),
        whole_files_n=sum(1 for f in loaded.files if f.given_whole),
        file_chars=sum(f.chars for f in loaded.files),
        angle_maps_n=len(loaded.angle_maps),
        message_chars=len(message),
    )

    holder: dict = {}
    try:
        run_result = await asyncio.wait_for(
            _drive(create_attachment_agent(), message, deps, holder), AGENT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        _record_partial(span, holder)
        return _error(NOTE_TIMEOUT, f"timeout after {AGENT_TIMEOUT_S}s"), deps, stats
    except Exception as exc:  # noqa: BLE001
        _record_partial(span, holder)
        logger.warning("attachment_agent: run failed (%s)", type(exc).__name__, exc_info=True)
        return _error(NOTE_ERROR, f"run: {type(exc).__name__}: {exc}"), deps, stats

    span.record_run(run_result, slot=SLOT)
    output = getattr(run_result, "output", None)
    if not isinstance(output, AttachmentOutputLLM):
        return _error(NOTE_ERROR, f"output {type(output).__name__}"), deps, stats
    if output.request == "read" and output.outcome == "edited" and deps.staged_edits:
        # The model staged changes and says so: a mislabelled request is a change.
        output = output.model_copy(update={"request": "change"})
    span.set(request=output.request, model_outcome=output.outcome, rationale=(output.rationale or "")[:300])
    result, stats = finalize(deps, output)
    if output.request == "read":
        if deps.staged_edits:
            # Edits are applied only when the model says the user asked for a change.
            logger.warning("attachment_agent: %d staged edit(s) dropped on a read request", len(deps.staged_edits))
            span.set(edits_dropped=len(deps.staged_edits))
        return result, deps, stats
    report = await apply_staged(deps) if deps.staged_edits else None
    span.set(
        edits_applied=report.edits_applied if report else 0,
        edits_failed=len(report.failed) if report else 0,
        reanalysis_started=report.reanalysis_started if report else 0,
    )
    return edit_outcome(result, report), deps, stats


def _record_partial(span: Any, holder: dict) -> None:
    """The tokens of a run cut short still go on the turn's ledger."""
    run = holder.get("run")
    if run is None:
        return
    try:
        span.record_run(run, slot=SLOT)
    except Exception:  # noqa: BLE001 — telemetry must not change the result
        logger.debug("attachment_agent: partial usage not recorded", exc_info=True)


__all__ = ["AGENT_TIMEOUT_S", "STAGE", "run_attachment_agent"]
