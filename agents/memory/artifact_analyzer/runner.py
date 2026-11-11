"""``analyze`` — one call, one verdict. Never raises.

Any failure returns a degraded insufficient, so the caller searches: exactly what
the turn would have done without the analyzer.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from agents.utils.prior_coverage import full_text_enabled
from agents.utils.tracking import track_stage
from backend.app.services.masking_service import active_codec, persist_new_mappings

from .agent import SLOT, create_artifact_analyzer, limits_for
from .context import law_change_line, load_reports, load_sources, render_turn_message
from .deps import AnalyzerDeps
from .models import AnalyzerCall, AnalyzerOutputLLM, AnalyzerResult
from .verify import finalize

logger = logging.getLogger(__name__)

STAGE = "artifact_analyzer.run"


def _degraded(reason: str) -> AnalyzerResult:
    logger.info("artifact_analyzer degraded: %s", reason)
    return AnalyzerResult(verdict="insufficient", degraded=True)


async def analyze(call: AnalyzerCall, supabase: Any) -> AnalyzerResult:
    with track_stage(
        STAGE,
        conversation_id=call.conversation_id,
        case_id=call.case_id,
        agent_family="memory",
        after_revert=call.after_revert,
    ) as span:
        try:
            reports = await asyncio.to_thread(load_reports, supabase, call)
            if not reports:
                return _degraded("no reports")
            full_text = full_text_enabled(call.user_id)
            span.set(full_text=full_text)
            sources = await asyncio.to_thread(
                load_sources, supabase, reports, full_text=full_text,
            )

            deps = AnalyzerDeps(
                supabase=supabase,
                user_id=call.user_id,
                conversation_id=call.conversation_id,
                case_id=call.case_id,
                full_text=full_text,
                reports={r.label: r for r in reports},
                sources={s.label: s for s in sources},
            )
            message = render_turn_message(call, reports, sources)
            # Fakes minted while encoding must exist before the LLM reads them.
            persist_new_mappings(supabase, call.user_id, active_codec())

            run = await create_artifact_analyzer(full_text).run(
                message, deps=deps, usage_limits=limits_for(full_text),
            )
            span.record_run(run, slot=SLOT)
            if not isinstance(run.output, AnalyzerOutputLLM):
                return _degraded(f"output {type(run.output).__name__}")

            result = finalize(deps, run.output)
            result.transition_line = await asyncio.to_thread(
                law_change_line, supabase, result.quotes,
            )
            span.set(
                verdict=result.verdict,
                verdict_before_rules=result.verdict_before_rules,
                reports_n=result.reports_n,
                sources_n=result.sources_n,
                opened_n=result.opened_n,
                quotes_n=len(result.quotes),
                quotes_dropped=result.quotes_dropped,
                outside_search_n=len(result.outside_search),
                points_n=result.points_n,
                full_read_n=result.full_read_n,
                reading_errors_n=len(result.reading_errors),
                reading_errors_dropped=result.reading_errors_dropped,
                reports_quoted_n=len({q.item_id for q in result.quotes if q.item_id}),
                rationale=(run.output.rationale or "")[:300],
            )
            return result
        except Exception as exc:  # noqa: BLE001 — falling back to a search is the contract
            logger.warning("artifact_analyzer failed: %s", exc, exc_info=True)
            span.set(error=str(exc)[:300])
            span.set_outcome("error")
            return _degraded(type(exc).__name__)
