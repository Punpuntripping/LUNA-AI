"""Run the merger: reports + angles + file → ONE answer, scoreboard, settled contradictions.

Flow (plan §4.6):
1. code builds the shared reference list and renumbers every report's ``[n]``;
2. the model reads the message of ``prompts.build_merger_user_message`` and
   returns ``MergerLLMOutput`` (validated by ``agent.create_merger_agent``);
3. code renumbers the answer 1..k, keeps only the cited references, clamps the
   confidence to the highest report's, repairs the scoreboard (unknown and
   duplicate ids dropped, missing ids filled as ``open``), strips ``[n]`` tags
   from text it does not renumber, and assembles ``MergerOutput`` for the
   publisher.

Masking: the inputs are pipeline text (encoded on a masked turn) and the output
stays encoded — the publisher decodes before storing. One ledger row
``deep_search.extended_merger`` through the stage span (``record_run``; a failed
run still bills what it spent). Raises on failure: the caller decides the
fallback (the extended search publishes the reports alone).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from types import SimpleNamespace

from pydantic_ai.usage import RunUsage, UsageLimits

from agents.deep_search_v4.aggregator.file_refs import build_file_refs
from agents.deep_search_v4.aggregator.postvalidator import strip_thinking_block
from agents.utils.tracking import track_stage
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP
from shared.workspace.file_refs import strip_file_tags

from .agent import MODEL_SLOT, create_merger_agent, missing_angles
from .models import (
    AngleCard,
    AngleStatus,
    LeftOutEntry,
    MergedReportEntry,
    MergerInput,
    MergerLLMOutput,
    MergerOutput,
)
from .prompts import FILE_REFS_PROMPT_VERSION, PROMPT_VERSION, build_merger_user_message, scoreboard_angle_ids
from .references import build_shared_references, renumber_answer, strip_citations, western_citations

logger = logging.getLogger(__name__)

LEDGER_STAGE = "deep_search.extended_merger"
MERGER_LIMITS = UsageLimits(output_tokens_limit=OUTPUT_TOKENS_CAP, request_limit=4)
_RANK = {"low": 0, "medium": 1, "high": 2}
_FILLED_NOTE = "لم يُقيَّم هذا الوجه في الجواب المدمج."


def clamp_confidence(confidence: str, inp: MergerInput) -> str:
    """Never above the highest confidence among the finished reports (§4.6)."""
    ranks = [_RANK.get(getattr(r.output, "confidence", ""), -1) for r in inp.reports if r.output is not None]
    ceiling = max(ranks, default=-1)
    if ceiling < 0:
        return confidence
    return confidence if _RANK.get(confidence, 1) <= ceiling else {v: k for k, v in _RANK.items()}[ceiling]


def _plain(text: str) -> str:
    """A note, topic, decision or gap: no ``[n]`` and no file tag (both belong in
    ``synthesis_md`` only)."""
    return strip_file_tags(strip_citations(text))


def repair_scoreboard(statuses: list[AngleStatus], expected: list[str]) -> tuple[list[AngleStatus], dict[str, int]]:
    """One entry per expected id, in the expected order: unknown ids and repeats
    dropped, missing ids filled as ``open`` with a code note; ``[n]`` stripped."""
    by_id: dict[str, AngleStatus] = {}
    unknown = dup = 0
    for s in statuses:
        aid = (s.angle or "").strip()
        if aid not in expected:
            unknown += 1
        elif aid in by_id:
            dup += 1
        else:
            by_id[aid] = s.model_copy(update={"angle": aid, "note": strip_citations(s.note)})
    out, filled = [], 0
    for aid in expected:
        if aid in by_id:
            out.append(by_id[aid])
        else:
            filled += 1
            out.append(AngleStatus(angle=aid, status="open", note=_FILLED_NOTE))
    return out, {"unknown": unknown, "duplicate": dup, "filled": filled}


def _usage_entry(result: Any) -> dict[str, Any]:
    u = result.usage()
    model = None
    try:
        for m in result.all_messages() or []:
            model = getattr(m, "model_name", None) or model
    except Exception:  # noqa: BLE001
        pass
    details = getattr(u, "details", None) or {}
    return {
        "model": model,
        "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
        "cached_tokens": int(getattr(u, "cache_read_tokens", 0) or 0),
        "requests": int(getattr(u, "requests", 0) or 0),
        "details": {"reasoning_tokens": int(details.get("reasoning_tokens", 0) or 0)},
    }


def _record(span: Any, result: Any) -> None:
    """One ``deep_search.extended_merger`` ledger row through the stage span (the
    ``LUNA_TRACK_DISABLE`` handle has no ``record_run``)."""
    try:
        span.record_run(result, slot=MODEL_SLOT)
    except Exception:  # noqa: BLE001
        pass


async def run_merger(
    inp: MergerInput, *, model: Any = None, conversation_id: str | None = None,
) -> tuple[MergerOutput, dict[str, Any]]:
    """Merge ``inp.reports``. Returns the output and the call's usage entry.

    Raises ``ValueError`` when no report finished, and whatever the model call
    raises after its retries."""
    if not any(r.output is not None for r in inp.reports):
        raise ValueError("extended_merger: no finished report to merge")

    shared, bodies, sources, dropped = build_shared_references(inp.reports)
    file_refs_on = bool(inp.file_ref_docs)
    expected = scoreboard_angle_ids(inp.reports)
    message = build_merger_user_message(inp, shared_refs=shared, report_bodies=bodies)
    agent = create_merger_agent(n_shared=len(shared), model=model, file_refs=file_refs_on)

    t0 = time.perf_counter()
    spent = RunUsage()
    with track_stage(
        LEDGER_STAGE,
        conversation_id=conversation_id or None,
        agent_family="deep_search",
        reports=len(inp.reports),
        reports_ok=sum(1 for r in inp.reports if r.output is not None),
        shared_refs=len(shared),
        angles=len(expected),
        input_chars=len(message),
        prompt_version=PROMPT_VERSION,
    ) as span:
        try:
            result = await agent.run(message, usage_limits=MERGER_LIMITS, usage=spent)
        except BaseException:
            _record(span, SimpleNamespace(usage=lambda: spent))  # a failed run still bills
            raise
        duration_ms = int((time.perf_counter() - t0) * 1000)
        usage = _usage_entry(result)
        _record(span, result)

        llm: MergerLLMOutput = result.output
        synthesis = western_citations(strip_thinking_block(llm.synthesis_md).strip())
        text, refs, final = renumber_answer(synthesis, shared)
        # The answer's file tags → the parts of the user's file it points at (only
        # tags of a document and section the reports could cite resolve; code never
        # moves or removes a tag — the web hides one with no row).
        file_refs = build_file_refs(text, inp.file_ref_docs) if file_refs_on else []
        ref_reports = {final[old]: sources.get(old, []) for old in final}
        board, board_stats = repair_scoreboard(llm.angle_status, expected)
        confidence = clamp_confidence(llm.confidence, inp)

        resolved = [
            r.model_copy(update={"topic": _plain(r.topic), "decision": _plain(r.decision),
                                 "reports": sorted({n for n in r.reports if 1 <= n <= len(inp.reports)})})
            for r in llm.resolved
        ]
        gaps = list(dict.fromkeys(g for g in (_plain(x) for x in llm.gaps) if g))
        board = [s.model_copy(update={"note": strip_file_tags(s.note)}) for s in board]
        angles = [
            AngleCard(id=a.id, kind=a.kind, strength=a.strength, title=a.title,
                      for_party=a.for_party, for_side=a.for_side, argument=a.argument,
                      legal_question=a.legal_question, weakness=a.weakness,
                      sections=list(a.sections), ocr_suspect=a.ocr_suspect)
            for a in (inp.angles.get(aid) for aid in expected) if a is not None
        ]
        left_out = [
            LeftOutEntry(angle=lo.angle_id,
                         title=getattr(inp.angles.get(lo.angle_id), "title", "") or "", why=lo.why)
            for lo in inp.left_out
        ]
        reports = [
            MergedReportEntry(n=j, title=r.title, mode=r.mode, angles=list(r.angle_ids), item_id=r.item_id,
                              confidence=getattr(r.output, "confidence", None), ok=r.output is not None)
            for j, r in enumerate(inp.reports, 1)
        ]
        stats = {
            "input_chars": len(message),
            "shared_refs": len(shared),
            "shared_refs_with_excerpt": sum(1 for r in shared if (r.snippet or "").strip()),
            "cited_refs": len(refs),
            "dropped_report_tags": dropped,
            "model_confidence": llm.confidence,
            "scoreboard_missing": len(missing_angles(llm, expected)),
            **{f"scoreboard_{k}": v for k, v in board_stats.items()},
            "duration_ms": duration_ms,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "reasoning_tokens": usage["details"]["reasoning_tokens"],
            "requests": usage["requests"],
        }
        if file_refs_on:
            stats["file_refs"] = len(file_refs)
            stats["file_refs_prompt_version"] = FILE_REFS_PROMPT_VERSION
        try:
            span.set(cited_refs=len(refs), resolved=len(resolved), confidence=confidence,
                     confidence_model=llm.confidence, scoreboard_filled=board_stats["filled"],
                     synthesis_chars=len(text))
            if file_refs_on:
                span.set(file_refs=len(file_refs), file_refs_prompt_version=FILE_REFS_PROMPT_VERSION)
        except Exception:  # noqa: BLE001
            pass

    out = MergerOutput(
        synthesis_md=text,
        references=refs,
        ref_reports=ref_reports,
        confidence=confidence,
        gaps=gaps,
        angle_status=board,
        resolved=resolved,
        angles=angles,
        left_out=left_out,
        reports=reports,
        model_used=usage.get("model") or "",
        prompt_version=PROMPT_VERSION,
        stats=stats,
        file_refs=file_refs,
    )
    return out, usage


__all__ = ["LEDGER_STAGE", "clamp_confidence", "repair_scoreboard", "run_merger"]
