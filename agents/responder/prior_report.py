"""Turn an analyzer verdict into the reply (plan artifact_analyzer.md §4.6).

The responder writes the plain sentences and marks each with the numbers of the
passages it rests on: «… [1] [2]». **Code holds the quotes**, because a model that
retypes legal text is exactly what the analyzer exists to prevent: they ride
``messages.metadata.quote_refs`` and the user opens each one from its number. If
the responder fails, the user still gets the verified quotes printed with their
sources: never a clipped text.
"""
from __future__ import annotations

import re
from typing import Any, Sequence

from agents.memory.artifact_analyzer.agent import SLOT as ANALYZER_SLOT
from agents.memory.artifact_analyzer.models import AnalyzerResult, VerifiedQuote
from agents.models import MAX_NEXT_STEPS, NextStep, SpecialistResult
from agents.utils.agent_models import AGENT_MODELS
from shared.library.supersession import RESPONDER_VERSION_RULE

from .models import PriorReportArtifact, PriorReportResponse, ResponderInput
from .runner import run_responder
from .wiring import load_profession_group, load_style_profile

#: A reply is not a report. The rest stay in the card.
MAX_SHOWN_QUOTES = 4

FOUND_RELATED = "وجدنا مصدرًا متعلقًا بسؤالك:"
REPORT_ATTRIBUTION = "من بحثنا السابق «{title}»"
BASED_ON = "، استنادًا إلى: "
FALLBACK_LINE = "هذا ما لدينا في هذه المسألة من بحثنا السابق:"
FALLBACK_OFFER = "هل تريد أن أبحث في هذه النقطة؟"
YES_SEARCH = "نعم، ابحث"
NO_SEARCH = "لا، يكفي"


def _attribution(quote: VerifiedQuote) -> str:
    """Whose words these are. A report's sentence never passes for a law's text."""
    if not quote.report_title:
        return quote.source_name
    own = REPORT_ATTRIBUTION.format(title=quote.report_title)
    return f"{own}{BASED_ON}{quote.source_name}" if quote.source_name else own


def _source_line(quote: VerifiedQuote) -> str:
    source = _attribution(quote)
    return f"{source} ({quote.status})" if quote.status else source


def render_quote(quote: VerifiedQuote) -> str:
    block = f"> «{quote.text}»\n— {_source_line(quote)}"
    return f"{FOUND_RELATED}\n{block}" if quote.uncited else block


#: ``[1]``, ``[1، 2]``, ``[١]``: a passage number the responder wrote, with the
#: space before it (dropped with a number that points at no passage).
_MARKER_RE = re.compile(r"([ \t]*)\[\s*(\d+(?:\s*[,،]\s*\d+)*)\s*\]")


def quote_ref(n: int, quote: VerifiedQuote) -> dict[str, Any]:
    """What the user reads on clicking ``[n]``: the words and whose words they are."""
    ref: dict[str, Any] = {"n": n, "text": quote.text, "source": _source_line(quote)}
    if quote.uncited:
        ref["note"] = FOUND_RELATED.rstrip(":")
    return ref


def cite_quotes(line: str, quotes: Sequence[VerifiedQuote]) -> tuple[str, list[dict]]:
    """The responder's text with checked passage numbers, and the refs they open.

    Numbers follow the order the text first cites them, so the reader meets
    [1] before [2]. A number that points at no passage is dropped. A passage the
    text never cites keeps a number at the end of the first paragraph, the one that
    answers: the user can still open every quote the answer rests on.
    """
    shown = list(quotes[:MAX_SHOWN_QUOTES])
    order: list[int] = []  # passage index, in display order

    def renumber(match: re.Match[str]) -> str:
        numbers: list[int] = []
        for raw in re.split(r"[,،]", match.group(2)):
            index = int(raw) - 1
            if not 0 <= index < len(shown):
                continue
            if index not in order:
                order.append(index)
            n = order.index(index) + 1
            if n not in numbers:
                numbers.append(n)
        if not numbers:
            return ""
        return match.group(1) + " ".join(f"[{n}]" for n in numbers)

    text = _MARKER_RE.sub(renumber, line.strip())
    rest = [i for i in range(len(shown)) if i not in order]
    if rest:
        tail = " ".join(f"[{len(order) + k + 1}]" for k in range(len(rest)))
        order.extend(rest)
        head, sep, after = text.partition("\n\n")
        text = f"{head.rstrip()} {tail}".lstrip() + sep + after
    return text, [quote_ref(n, shown[i]) for n, i in enumerate(order, 1)]


def compose_message(
    line: str,
    quotes: Sequence[VerifiedQuote],
    *,
    transition_line: str = "",
) -> str:
    parts = [line.strip()]
    parts.extend(render_quote(q) for q in quotes[:MAX_SHOWN_QUOTES])
    if transition_line:
        parts.append(transition_line)
    return "\n\n".join(p for p in parts if p)


def _search_chips() -> list[NextStep]:
    return [
        NextStep(kind="answer", label=YES_SEARCH, prompt=YES_SEARCH),
        NextStep(kind="answer", label=NO_SEARCH, prompt=NO_SEARCH),
    ]


def _report_chip(result: AnalyzerResult) -> list[dict]:
    """The «راجع: …» chip, on every reply built from prior reports.

    It points at the report the quotes lean on: its own words, or the search that
    found the law quoted. Otherwise the newest report read. A reply that quoted only
    a law used to get no chip, which also left its follow-ups unlinked.
    """
    item_id = next((q.report_id for q in result.quotes if q.report_id), None)
    item_id = item_id or (result.report_ids[0] if result.report_ids else None)
    return [{"type": "referenced_existing_item", "item_id": item_id}] if item_id else []


def assemble(result: AnalyzerResult, out: PriorReportResponse | None) -> SpecialistResult:
    """Pure: the reply for a verdict, with or without the responder's text."""
    partial = result.verdict == "partial"
    events = _report_chip(result)
    line = (out.chat_summary_md if out else "").strip()
    if line and result.quotes:
        message, refs = cite_quotes(line, result.quotes)
        if result.transition_line:
            message = f"{message}\n\n{result.transition_line}"
        # Server-only, merged into messages.metadata (message_service).
        events.append({"type": "reply_metadata", "metadata": {"quote_refs": refs}})
    else:
        message = compose_message(
            line or FALLBACK_LINE, result.quotes, transition_line=result.transition_line,
        )

    if partial:
        actions = [s for s in (out.next_steps if out else []) if s.kind != "answer"]
        offer = ((out.search_offer if out else None) or "").strip() or FALLBACK_OFFER
        return SpecialistResult(
            chat_summary=message,
            sse_events=events,
            follow_up_question=offer,
            next_steps=(_search_chips() + actions)[:MAX_NEXT_STEPS],
        )
    return SpecialistResult(
        chat_summary=message,
        sse_events=events,
        follow_up_question=out.follow_up_question if out else None,
        question_basis=out.question_basis if out else None,
        next_steps=list(out.next_steps) if out else [],
    )


async def write_prior_report_reply(
    result: AnalyzerResult,
    *,
    supabase: Any,
    user_id: str,
    conversation_id: str,
    case_id: str | None = None,
    router_context: Any = None,
    fallback_context: str | None = None,
    conversation_memory_md: str | None = None,
) -> SpecialistResult:
    artifact = PriorReportArtifact(
        question=result.restatement,
        verdict=result.verdict,
        quotes=list(result.quotes[:MAX_SHOWN_QUOTES]),
        missing=list(result.missing),
        ask_user=list(result.ask_user),
        version_rule=RESPONDER_VERSION_RULE if result.transition_line else "",
        corrections=list(result.reading_errors),
    )
    # The analyzer's lighter reasoning, not the responder slot's: here it writes one
    # or two sentences over quotes that code prints. Live, on medium it spent 7k-10k
    # reasoning tokens and 40-60s on that.
    out = await run_responder(ResponderInput(
        mode="prior_report",
        artifact=artifact,
        router_context=router_context,
        fallback_context=fallback_context,
        profession_group=load_profession_group(supabase, user_id),
        style_profile=load_style_profile(supabase, user_id),
        conversation_id=conversation_id,
        case_id=case_id,
        conversation_memory_md=conversation_memory_md,
    ), model_override=AGENT_MODELS[ANALYZER_SLOT])
    return assemble(result, out if isinstance(out, PriorReportResponse) else None)
