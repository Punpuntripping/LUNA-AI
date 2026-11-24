"""Run one Completion Search request (plan §4.2, §4.4).

Two paths, code first:

1. **By code, no model.** A request that is only "this law, these article numbers" —
   «نظام المعاملات المدنية المادة 56 والمادة 234» — is read by the citation scan and
   answered straight from the library. This is the request the search planner sends
   most, and it stays as fast as the old ``fetch_article``.
2. **The helper agent** for everything else: a name that needs another wording, a
   phrase or a definition, a circular, a service, several laws in one request.

Either way the caller gets a ``CompletionResult``: per search a status, a confidence
that code computed, and the opened official text.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.completion_search.agent import LIMITS, SLOT, get_completion_agent
from agents.completion_search.confidence import HIGH, article_confidence
from agents.completion_search.deps import CompletionDeps, written_in
from agents.completion_search.lookups import Library
from agents.completion_search.models import (
    MAX_CANDIDATE_TITLES,
    MAX_SEARCHES,
    CompletionResult,
    HelperDecision,
    Pin,
    SearchOutcome,
    lowest,
)
from agents.completion_search.prompts import build_user_message
from agents.tool_repository.citation_scan import (
    BASIS_OTHER_TARGET,
    KIND_NAMED,
    ScanResult,
    fold,
    scan_citations,
)

logger = logging.getLogger(__name__)

STAGE = "completion_search.run"

#: Words that may sit between a law's name and its article numbers in a plain request.
_FILLER = frozenset("و من في نص نصوص ثم او كذلك ايضا رقم مع".split())

_STATUS_AR = {
    "found": "وُجد",
    "candidates": "أكثر من احتمال",
    "not_in_library": "غير موجود في المكتبة",
    "not_found": "لم يُعثر عليه",
}
_CONFIDENCE_AR = {"high": "ثقة عالية", "medium": "ثقة متوسطة", "low": "ثقة منخفضة"}


# --------------------------------------------------------------------------- #
# Path 1 — by code
# --------------------------------------------------------------------------- #


def _only_law_and_numbers(request: str, scan: ScanResult) -> bool:
    """True when nothing is left in the request once the law and the article mentions are taken out."""
    taken = sorted(
        [(law.char_start, law.char_end) for law in scan.laws]
        + [(m.char_start, m.char_end) for m in scan.mentions]
    )
    rest: list[str] = []
    pos = 0
    for start, end in taken:
        if start > pos:
            rest.append(request[pos:start])
        pos = max(pos, end)
    rest.append(request[pos:])
    for word in fold(" ".join(rest)).split():
        cleaned = word.strip("()[]{}«»\"'،,.:؛;-–—/\\")
        if cleaned and cleaned not in _FILLER:
            return False
    return True


async def answer_by_code(request: str, library: Library, source_text: str | None = None) -> CompletionResult | None:
    """Answer a plain "one law + article numbers" request with no model. ``None`` = not that shape.

    ``source_text`` — what the caller itself was given. A law name that is not written
    there is the caller's own guess, so its articles come back medium, not high.
    """
    titles = await library.title_index()
    scan = scan_citations(request, None, titles)
    if not scan.mentions or len(scan.distinct_named()) != 1:
        return None
    if any(law.kind != KIND_NAMED for law in scan.laws):
        return None                       # «من النظام», a pleading … — needs reading
    law_ref = scan.named_laws[0]
    if not law_ref.in_library or law_ref.open_end or law_ref.exec_of:
        return None
    numbers: list[str] = []
    for m in scan.mentions:
        if m.compound or m.range_wide or m.law is None or m.basis == BASIS_OTHER_TARGET:
            return None
        numbers += [n for n in m.numbers if n not in numbers]
    if not numbers or len(numbers) > MAX_SEARCHES or not _only_law_and_numbers(request, scan):
        return None

    lookup = await library.find_law(law_ref.text)
    if lookup.gate.state != "exact" or not lookup.in_force:
        return None
    law = lookup.law
    as_asked = written_in(law_ref.text, source_text) if source_text else True
    confidence = article_confidence(title_state="exact", as_asked=as_asked, in_force=True)
    outcomes: list[SearchOutcome] = []
    has_text: bool | None = None
    for i, number in enumerate(numbers, 1):
        asked = f"المادة {number} من {law.title}"
        texts = await library.article(lookup, number)
        if not texts:
            if has_text is None:
                has_text = await library.has_articles(lookup)
            note = (f"المادة {number} غير موجودة في «{law.title}»." if has_text
                    else f"«{law.title}» موجود في المكتبة، لكن نصوص مواده غير متوفرة فيها.")
            outcomes.append(SearchOutcome(
                search_id=f"S{i}", asked=asked, status="not_found", rounds=1, note=note,
            ))
            continue
        pins = [
            Pin(
                key=f"article:{t.article_id or t.regulation_id + ':' + t.number}",
                source_type="article",
                title=f"المادة {t.number} من {t.regulation_title}",
                source_title=t.regulation_title,
                text=t.content,
                confidence=confidence,
                note=t.note,
                regulation_id=t.regulation_id,
                article_id=t.article_id,
                article_number=t.number,
            )
            for t in texts
        ]
        outcomes.append(SearchOutcome(
            search_id=f"S{i}", asked=asked, status="found", confidence=confidence, pins=pins, rounds=1,
        ))
    return CompletionResult(request=request, searches=outcomes, model_used=False)


# --------------------------------------------------------------------------- #
# Path 2 — the agent's decision → outcomes
# --------------------------------------------------------------------------- #


def build_result(request: str, decision: HelperDecision, deps: CompletionDeps) -> CompletionResult:
    """Turn the model's handle selection into outcomes. Confidence comes from the handles."""
    outcomes: list[SearchOutcome] = []
    seen: set[str] = set()
    for search in decision.searches:
        sid = deps.sid(search.search_id)
        if sid in seen:
            continue
        seen.add(sid)
        handles = [h for h in (deps.get(raw) for raw in search.selected) if h is not None]
        pins: list[Pin] = []
        for h in handles:
            if h.pin is not None and all(p.key != h.pin.key for p in pins):
                pins.append(h.pin)
        rounds = deps.rounds.get(sid, 0)
        if pins:
            outcomes.append(SearchOutcome(
                search_id=sid, asked=search.asked, status="found",
                confidence=lowest([p.confidence for p in pins]), pins=pins, note=search.note, rounds=rounds,
            ))
            continue
        titles = [h.title for h in handles if h.law is not None] or deps.hints.get(sid, [])
        status = search.status
        if status == "found":                       # the validator blocks this; belt and braces
            status = "candidates" if titles else "not_found"
        if status == "candidates" and not titles:
            status = "not_in_library" if sid in deps.missing else "not_found"
        if status == "not_in_library" and sid not in deps.missing:
            status = "not_found"                    # only a lookup may say the law is absent
        outcomes.append(SearchOutcome(
            search_id=sid, asked=search.asked, status=status,
            # Rulings that share one number are a sure match on the number itself: medium (plan §4.3).
            confidence=(deps.hint_level.get(sid, "low") if status == "candidates" else None),
            candidates=titles[:MAX_CANDIDATE_TITLES] if status == "candidates" else [],
            note=search.note, rounds=rounds,
        ))

    # A search the tools settled at high confidence is never lost because the model left it out.
    for sid in sorted(deps.closed - seen):
        pins = []
        for h in deps.handles.values():
            if h.search_id == sid and h.pin is not None and h.pin.confidence == HIGH:
                if all(p.key != h.pin.key for p in pins):
                    pins.append(h.pin)
        if pins:
            outcomes.append(SearchOutcome(
                search_id=sid, asked=pins[0].title, status="found", confidence=HIGH,
                pins=pins, rounds=deps.rounds.get(sid, 0),
            ))
    return CompletionResult(request=request, searches=outcomes, model_used=True)


async def run_completion_search(
    request: str,
    *,
    library: Library,
    caller: str = "deep_planner",
    agent_family: str | None = None,
    conversation_id: Any = None,
    case_id: Any = None,
    turn_number: int | None = None,
    user_id: Any = None,
    agent: Any = None,
    user_message: str | None = None,
    code_first: bool = True,
    trace: list | None = None,
    source_text: str | None = None,
) -> CompletionResult:
    """Find what ``request`` names. Never raises for a lookup miss; a failed run sets ``error``.

    ``ctx.usage`` of the calling agent is deliberately NOT accepted: this run writes its
    own ledger row through ``run_tracked``, and sharing the usage object would bill it twice.
    """
    from agents.utils.tracking import run_tracked

    request = (request or "").strip()
    if not request:
        return CompletionResult(request="", error="empty_request")
    if code_first:
        try:
            done = await answer_by_code(request, library, source_text)
        except Exception:  # noqa: BLE001 — the code path is an optimisation, never a failure
            logger.warning("completion_search: code path failed; using the agent", exc_info=True)
            done = None
        if done is not None:
            return done

    deps = CompletionDeps(
        library=library, request=request, caller=caller, source_text=source_text,
        conversation_id=conversation_id,
        case_id=case_id, turn_number=turn_number, user_id=user_id,
    )
    run = await run_tracked(
        agent or get_completion_agent(),
        user_message or build_user_message(request, caller),
        deps=deps,
        stage=STAGE,
        slot=SLOT,
        agent_family=agent_family,
        usage_limits=LIMITS,
    )
    if trace is not None:          # evaluation only: the run's messages
        trace.extend(run.all_messages())
    return build_result(request, run.output, deps)


# --------------------------------------------------------------------------- #
# What the caller reads
# --------------------------------------------------------------------------- #


def render_for_caller(result: CompletionResult, *, cap_text=None, ids: dict[str, str] | None = None) -> str:
    """The Arabic text a calling agent reads: one block per search.

    ``cap_text`` trims a long text for the caller only (the pin keeps the whole text).
    ``ids`` maps a pin's key to the short id the caller uses to accept a medium result.
    """
    if result.error:
        return "تعذّر إتمام البحث عن النصوص المطلوبة في الوقت الحالي."
    if not result.searches:
        return "لم يتبيّن من الطلب نص محدد يُبحث عنه."
    blocks: list[str] = []
    for s in result.searches:
        head = f"### {s.asked or s.search_id}\nالحالة: {_STATUS_AR[s.status]}"
        if s.confidence:
            head += f" — {_CONFIDENCE_AR[s.confidence]}"
        parts = [head]
        for pin in s.pins:
            body = cap_text(pin.text) if cap_text else pin.text
            tag = f" [{ids[pin.key]}]" if ids and pin.key in ids else ""
            parts.append(f"## {pin.title}{tag}\n\n{body}")
            if pin.note:
                parts.append(pin.note)
        if s.candidates:
            parts.append("الاحتمالات: " + "؛ ".join(f"«{t}»" for t in s.candidates))
        if s.note:
            parts.append(s.note)
        blocks.append("\n\n".join(parts))
    return "\n\n---\n\n".join(blocks)


__all__ = ["STAGE", "answer_by_code", "build_result", "render_for_caller", "run_completion_search"]
