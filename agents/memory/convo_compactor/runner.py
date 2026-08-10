"""Runner for the convo_compactor agent.

Single LLM call. **Fail-closed** — and that inversion is the whole point of
this package.

``artifact_summarizer`` is best-effort: when its call fails it returns
``content_md[:500]``, because it degrades an *enrichment* field and a truncated
fallback is strictly better than nothing. Compaction is not enrichment. When
compaction proceeds, the cutoff advances, the messages leave every downstream
agent's context window, and whatever this runner returned stands in for them
permanently. A truncation fallback there would reproduce — in a subtler form —
the exact mock-summary bug this agent was built to remove.

So: ``failed=True`` on EVERY failure path (empty input, exception from
``agent.run``, empty/whitespace ``summary_md``), with ``summary_md=""`` and no
substitute text of any kind. ``failed=False`` is set on exactly one path — a
real summary came back. The caller (``compact_conversation``) must then refuse
to insert a ``convo_context`` item and refuse to advance
``conversations.compacted_through_message_id``; the conversation stays over the
token threshold and retries on the next turn, which is bounded by turn rate and
self-healing.

The runner itself never raises — a failure has to arrive at the caller as
``failed=True``, not as an exception that skips the check.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from agents.utils.tracking import _model_from_result, track_stage
from shared.observability import get_logfire

from .agent import COMPACTOR_LIMITS, create_convo_compactor
from .deps import CompactionDeps
from .models import CompactionInput, CompactionOutput
from .prompts import build_user_message

logger = logging.getLogger(__name__)
_logfire = get_logfire()


async def handle_compaction_turn(
    input: CompactionInput,
    deps: CompactionDeps,
) -> CompactionOutput:
    """Run one conversation-compaction LLM call.

    Returns a ``CompactionOutput``. ``failed=False`` means and only means that
    a real, non-empty summary was produced and is safe to persist; on every
    other outcome ``failed`` stays ``True`` and ``summary_md`` is empty. Never
    raises.
    """
    t0 = time.perf_counter()

    messages = input.messages or []
    workspace_items = input.workspace_items or []

    with track_stage(
        "convo_compactor.run",
        conversation_id=input.conversation_id,
        agent_family="memory",
        message_count=len(messages),
        wi_count=len(workspace_items),
        has_prior_summary=bool((input.prior_summary_md or "").strip()),
    ) as _cc_span:
        if not any(str(m.get("content") or "").strip() for m in messages if isinstance(m, dict)):
            logger.warning("convo_compactor: no message content to compact — failing closed")
            _set_span(_cc_span, "empty_input", failed=True)
            # Nothing to compact. No LLM call, and NO fallback text — the
            # caller must not advance the cutoff over an empty summary.
            return CompactionOutput(summary_md="", failed=True)

        try:
            user_message = build_user_message(
                messages=messages,
                workspace_items=workspace_items,
                prior_summary_md=input.prior_summary_md,
                user_call_name=input.user_call_name,
            )
            agent = create_convo_compactor()
            result = await agent.run(user_message, usage_limits=COMPACTOR_LIMITS)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "convo_compactor: LLM call failed (%s) — failing closed, "
                "conversation stays uncompacted and retries next turn",
                exc,
            )
            _set_span(
                _cc_span,
                "llm_failed",
                failed=True,
                error=str(exc),
                **{"error.type": type(exc).__name__},
            )
            return CompactionOutput(summary_md="", failed=True)

        try:
            # This is what lands the per-call llm_calls ledger row for the
            # slot — compaction emits none at all today. Guarded because the
            # LUNA_TRACK_DISABLE no-op handle has no record_run, and this
            # runner is contractually forbidden from raising.
            _cc_span.record_run(result, slot="convo_compactor")
        except Exception as exc:  # noqa: BLE001
            logger.debug("convo_compactor: record_run failed: %s", exc)

        summary_md = _summary_from_result(result)
        if not summary_md:
            logger.warning("convo_compactor: empty summary — failing closed")
            _set_span(_cc_span, "empty_output", failed=True)
            return CompactionOutput(summary_md="", failed=True)

        # The one path that clears the fail-closed flag.
        output = CompactionOutput(summary_md=summary_md, failed=False)

        # Token accounting is telemetry, not correctness — a hiccup here must
        # not discard a summary the model actually produced.
        try:
            usage = result.usage()
            details = dict(usage.details) if usage.details else {}
            output.tokens_in = int(usage.input_tokens or 0)
            output.tokens_out = int(usage.output_tokens or 0)
            output.tokens_reasoning = int(details.get("reasoning_tokens", 0) or 0)
            output.tokens_cached = int(getattr(usage, "cache_read_tokens", 0) or 0)
            output.model_used = _model_label_from_result(result)
        except Exception as exc:  # noqa: BLE001
            logger.debug("convo_compactor: usage extraction failed: %s", exc)

        duration_s = time.perf_counter() - t0

        _set_span(
            _cc_span,
            "ok",
            failed=False,
            model_used=output.model_used,
            tokens_in=output.tokens_in,
            tokens_out=output.tokens_out,
            tokens_reasoning=output.tokens_reasoning,
            summary_chars=len(summary_md),
            duration_s=round(duration_s, 3),
        )

        if deps.logger is not None:
            try:
                deps.logger.write_run(input, output, duration_s)
            except Exception as exc:  # noqa: BLE001
                logger.debug("convo_compactor: logger failed: %s", exc)

        return output


def _set_span(span: Any, outcome: str, **attrs: Any) -> None:
    """Stamp outcome + attrs onto the span, swallowing everything.

    Telemetry must never be the reason this runner raises, and the
    ``LUNA_TRACK_DISABLE`` no-op handle does not implement ``set_outcome``.
    """
    try:
        span.set_outcome(outcome)
    except Exception:  # noqa: BLE001
        pass
    try:
        span.set(**attrs)
    except Exception:  # noqa: BLE001
        pass


def _summary_from_result(result: Any) -> str:
    """Pull ``summary_md`` off the run result, tolerating a malformed output."""
    try:
        return (getattr(result.output, "summary_md", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _model_label_from_result(result: Any) -> str:
    """Which of the FallbackModel's cells actually fielded the request.

    Read off the last ``ModelResponse.model_name`` in the run — the only place
    pydantic_ai surfaces it. ``AgentRunResult`` exposes neither ``.model`` nor
    ``._model`` (checked against pydantic_ai 1.39), so probing those attributes
    silently yields the slot label on every single run and the fallback cell
    that served the request is never reported. ``agents/utils/tracking.py``
    (``_model_from_result``) is the canonical implementation and is what the
    ``llm_calls`` ledger row already uses; this mirrors it so the value on
    ``CompactionOutput`` agrees with the ledger.

    Falls back to the slot label only when the run carries no model name at all
    (e.g. a ``TestModel`` run).
    """
    try:
        name = _model_from_result(result)
        if name:
            return str(name)
    except Exception:  # noqa: BLE001
        pass
    return "convo_compactor:tier_2"


# Public alias matching the spec in the planning doc.
run_convo_compaction = handle_compaction_turn
