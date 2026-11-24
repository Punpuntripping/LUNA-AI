"""The agent step of the attachment citation run.

Plan ``.claude/plans/completion_search.md`` §4.6.3 and owner decisions C14, C15, C26
(2026-11-23): code finds the citations and decides the ones it can PROVE. Everything
else that reading can settle goes to the Completion Search agent, which is responsible
for the fetching: it reads the passage, decides which law the article belongs to and
fetches it with its own ``get_article`` tool.

What the agent fetched is delivered (``pinned``, ``decided_by='model'``) when code
confirms three facts: the article number is the one the document wrote, the article
exists in that law, and the law is in force. The agent's words are never used — it
selects handles and code opens the official text.

Sent to the agent — citations that are not ``pinned`` and where reading can change
the answer:

- the law is named elsewhere in the document and the link is a guess;
- «من النظام» with several laws (or none) named before it;
- a name that is not a library title — short, misspelt, running on, opening several
  titles — and a name the library does not hold (three attempts, then C17);
- a «2/77» number; an executive regulation code did not find.

Never sent: an article of something that is not a law («من العقد»), a range too wide
to expand, a number written twice that disagrees, a law with no article text, a law
not in force, and a missing article of a law the document itself named.

The step never raises and never loses a code decision: a request that fails twice, or
runs out of time, leaves its citations exactly as code decided them.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Mapping, Sequence

from agents.completion_search.models import MAX_SEARCHES, SearchOutcome
from agents.completion_search.prompts import ATTACHMENT_TEMPLATE
from agents.memory.citation_pins.resolve import (
    IN_FORCE,
    MEDIUM,
    NOT_IN_LIBRARY,
    OTHER_TARGET,
    PINNED,
    R_BACKREF_NONE,
    R_BACKREF_SEVERAL,
    R_LINK_GUESS,
    R_LOOSE_NAME,
    R_NAME_OPEN,
    R_NO_ARTICLE,
    R_NO_EXEC,
    R_NO_LAW,
    R_NOT_IN_FORCE,
    R_NUMBER_FALLBACK,
    R_SEVERAL_TITLES,
    R_SHORT_NAME,
    UNRESOLVED,
    Citation,
    number_keys,
)
from agents.tool_repository.citation_scan import (
    BASIS_SAME_PHRASE,
    BASIS_SINGLE_LAW,
    KIND_NAMED,
    Mention,
    ScanResult,
    fold,
)

logger = logging.getLogger(__name__)

AGENT_STEP_VERSION = "agent-v1"
CALLER = "attachment"
#: What the scan row records as its model: the slot the agent runs on.
MODEL_LABEL = "completion_search"

#: At most this many searches per file go to the agent; the rest stay as code left them.
MAX_AGENT_SEARCHES = 40
#: One request holds what one Completion Search request may hold (plan §8 Q2).
CHUNK = MAX_SEARCHES
AGENT_CONCURRENCY = 3
CHUNK_TIMEOUT_S = 60.0
CHUNK_ATTEMPTS = 2
#: The whole step. Well inside the helper's 300 s ceiling.
STEP_TIMEOUT_S = 200.0

#: Characters of the document shown around a mention.
WINDOW_BEFORE = 260
WINDOW_AFTER = 140
MAX_LAWS_BEFORE = 3
MAX_DOC_LAWS = 12
MARK_OPEN, MARK_CLOSE = "⟦", "⟧"

# reasons the agent step writes (attachment_citations.reason)
R_AGENT = "agent"                            # the agent picked the law; its name is written in the document
R_AGENT_CONFIRMED = "agent_confirmed_guess"  # … and it is the law code had guessed
R_AGENT_REWORDED = "agent_reworded_name"     # the agent fetched under a name the document did not write as is
R_AGENT_OTHER = "agent_named_another_instrument"   # not the law code had tied it to, and not in the library

_NAME_REASONS = frozenset({R_SHORT_NAME, R_LOOSE_NAME, R_SEVERAL_TITLES, R_NAME_OPEN})
_LINK_REASONS = frozenset({
    R_LINK_GUESS, R_BACKREF_SEVERAL, R_BACKREF_NONE, R_NO_LAW, R_NUMBER_FALLBACK, R_NO_EXEC,
})
#: A link the scan itself proved: when the article is then missing, there is nothing to read.
_PROVEN_LINKS = (BASIS_SAME_PHRASE, BASIS_SINGLE_LAW)


@dataclass(frozen=True)
class AgentTask:
    """One search handed to the agent."""

    seqs: tuple[int, ...]            # the citations this search answers (a repeated name + number is one)
    number: str
    law_in_phrase: str               # the law's name written in the same phrase, or ""
    laws_before: tuple[str, ...]     # laws named before this place, nearest first
    window: str                      # the passage around the mention, the mention marked


@dataclass
class AgentStats:
    sent: int = 0                # citations handed to the agent
    searches: int = 0            # the searches they made
    pinned: int = 0              # citations the agent's fetch pinned
    not_in_library: int = 0      # citations the agent settled as "not in our data"
    failed: int = 0              # citations whose request failed twice or ran out of time
    over_cap: int = 0            # citations left to code because the file passed the cap
    seconds: float = 0.0
    model: str | None = None

    def row(self) -> dict[str, int]:
        """The counters stored on the scan row."""
        return {"sent_to_agent": self.sent, "pinned_by_agent": self.pinned, "agent_failed": self.failed}


# --------------------------------------------------------------------------- #
# What goes to the agent
# --------------------------------------------------------------------------- #


def needs_agent(citation: Citation) -> bool:
    """True when reading the passage can change what code decided."""
    if citation.status in (PINNED, OTHER_TARGET):
        return False
    if citation.status == NOT_IN_LIBRARY:
        return True
    if citation.reason in _NAME_REASONS or citation.reason in _LINK_REASONS:
        return True
    return citation.reason == R_NO_ARTICLE and citation.basis not in _PROVEN_LINKS


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _laws_before(scan: ScanResult, mention: Mention) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    named = [law for law in scan.laws if law.kind == KIND_NAMED and law.char_end <= mention.char_start]
    for law in sorted(named, key=lambda ref: ref.char_start, reverse=True):
        name = _clean(law.text)
        key = fold(name)
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) == MAX_LAWS_BEFORE:
            break
    return tuple(out)


def document_laws(scan: ScanResult) -> list[str]:
    """The laws the document names, in the order it names them."""
    seen: set[str] = set()
    out: list[str] = []
    for law in sorted((ref for ref in scan.laws if ref.kind == KIND_NAMED), key=lambda ref: ref.char_start):
        name = _clean(law.text)
        key = fold(name)
        if name and key not in seen:
            seen.add(key)
            out.append(name)
    return out[:MAX_DOC_LAWS]


def _window(content: str, mention: Mention) -> str:
    start = max(0, mention.char_start - WINDOW_BEFORE)
    end = min(len(content), mention.char_end + WINDOW_AFTER)
    text = (
        ("… " if start else "")
        + content[start:mention.char_start]
        + MARK_OPEN + content[mention.char_start:mention.char_end] + MARK_CLOSE
        + content[mention.char_end:end]
        + (" …" if end < len(content) else "")
    )
    return _clean(text)


def build_tasks(scan: ScanResult, citations: Sequence[Citation], content: str) -> tuple[list[AgentTask], int]:
    """``(searches for the agent, citations left out over the cap)``.

    The same law name with the same article number, written several times, is ONE
    search: what the name means does not depend on where it is written. A link that
    has to be read is one search per citation, because the passage decides it.
    """
    pairs = [(mention, number) for mention in scan.citing for number in mention.numbers]
    if len(pairs) != len(citations):          # not this scan's citations — nothing is sent
        return [], 0
    tasks: list[AgentTask] = []
    by_name: dict[tuple[str, str], int] = {}
    for (mention, _number), citation in zip(pairs, citations):
        if not needs_agent(citation):
            continue
        named = citation.basis == BASIS_SAME_PHRASE and bool(citation.law_as_written)
        if named and (citation.status == NOT_IN_LIBRARY or citation.reason in _NAME_REASONS):
            key = (fold(citation.law_as_written), citation.article_number)
            if key in by_name:
                first = tasks[by_name[key]]
                tasks[by_name[key]] = replace(first, seqs=first.seqs + (citation.seq,))
                continue
            by_name[key] = len(tasks)
        tasks.append(AgentTask(
            seqs=(citation.seq,),
            number=citation.article_number,
            law_in_phrase=_clean(citation.law_as_written) if named else "",
            laws_before=_laws_before(scan, mention),
            window=_window(content, mention),
        ))
    over = sum(len(t.seqs) for t in tasks[MAX_AGENT_SEARCHES:])
    return tasks[:MAX_AGENT_SEARCHES], over


def _names(names: Sequence[str]) -> str:
    return "؛ ".join(f"«{n}»" for n in names)


def render_request(tasks: Sequence[AgentTask], scan: ScanResult, doc_type: str = "") -> str:
    """The message one agent request reads: the rules, the document's laws, the searches."""
    laws = document_laws(scan)
    laws_line = (
        "الأنظمة واللوائح المسمّاة في المستند بترتيب ورودها: " + _names(laws)
        if laws else "لم يُسمَّ في المستند أي نظام باسمه."
    )
    blocks: list[str] = []
    for i, task in enumerate(tasks, 1):
        lines = [f"[S{i}] المادة {task.number}"]
        lines.append(
            f"النظام في العبارة نفسها: «{task.law_in_phrase}»" if task.law_in_phrase
            else "النظام في العبارة نفسها: لم يُسمَّ"
        )
        if task.laws_before:
            lines.append("سُمّي قبل هذا الموضع (الأقرب أولًا): " + _names(task.laws_before))
        lines.append(f"المقطع: {task.window}")
        blocks.append("\n".join(lines))
    return ATTACHMENT_TEMPLATE.format(
        doc_type=doc_type or "غير محدد", laws=laws_line, mentions="\n\n".join(blocks),
    )


# --------------------------------------------------------------------------- #
# What comes back
# --------------------------------------------------------------------------- #


def picked_regulations(outcomes: Mapping[int, SearchOutcome]) -> list[str]:
    """Regulation ids of everything the agent fetched (their status is checked by code)."""
    return sorted({p.regulation_id for o in outcomes.values() for p in o.pins if p.regulation_id})


def _from_agent(citation: Citation, outcome: SearchOutcome | None, status_of: Mapping[str, str]) -> Citation | None:
    """The citation as the agent settled it, or ``None`` to keep code's decision."""
    if outcome is None:
        return None
    if outcome.status == "not_in_library":
        # Code had tied it to a law that IS in the library; the agent read that the passage
        # cites another instrument («قواعد عمل اللجان …») which the library does not hold.
        other = citation.status != NOT_IN_LIBRARY
        return replace(citation, status=NOT_IN_LIBRARY, reason=R_AGENT_OTHER if other else "",
                       regulation_id=None, article_id=None, decided_by="model")
    if outcome.status != "found":
        return None                 # the agent declined («ليست مادة نظام», «النظام غير محدد») — code's row stands
    exact, fallback = number_keys(citation.article_number)
    keys = {*exact, *fallback}
    pin = next(
        (p for p in outcome.pins
         if p.source_type == "article" and p.article_id and p.regulation_id and p.article_number.strip() in keys),
        None,
    )
    if pin is None:
        return None                 # a different number than the document wrote is never accepted
    if status_of.get(pin.regulation_id, "") not in IN_FORCE:
        return replace(citation, status=UNRESOLVED, reason=R_NOT_IN_FORCE, regulation_id=None, article_id=None,
                       decided_by="model")
    if citation.status == MEDIUM and citation.article_id == pin.article_id:
        reason = R_AGENT_CONFIRMED
    elif pin.confidence == "high":
        reason = R_AGENT
    else:
        reason = R_AGENT_REWORDED
    return replace(citation, status=PINNED, reason=reason, regulation_id=pin.regulation_id,
                   article_id=pin.article_id, decided_by="model")


def apply_result(
    citations: Sequence[Citation],
    tasks: Sequence[AgentTask],
    outcomes: Mapping[int, SearchOutcome],
    status_of: Mapping[str, str],
) -> tuple[list[Citation], int, int]:
    """``(citations, pinned by the agent, settled as not in the library)``.

    ``outcomes`` maps a task's index to what the agent returned for it; ``status_of``
    maps a regulation id to its status in the library. Pure.
    """
    out = list(citations)
    pinned = missing = 0
    for index, task in enumerate(tasks):
        outcome = outcomes.get(index)
        for seq in task.seqs:
            new = _from_agent(out[seq - 1], outcome, status_of)
            if new is None:
                continue
            out[seq - 1] = new
            pinned += int(new.status == PINNED)
            missing += int(new.status == NOT_IN_LIBRARY)
    return out, pinned, missing


# --------------------------------------------------------------------------- #
# The step
# --------------------------------------------------------------------------- #


async def run_agent_step(
    scan: ScanResult,
    citations: Sequence[Citation],
    content: str,
    *,
    library: Any,
    reg_status: Callable[[Sequence[str]], Awaitable[Mapping[str, str]]],
    doc_type: str = "",
    encode: Callable[[str], str] | None = None,
    before_call: Callable[[], Awaitable[None]] | None = None,
    agent: Any = None,
    user_id: Any = None,
    conversation_id: Any = None,
    case_id: Any = None,
    trace: list | None = None,
) -> tuple[list[Citation], AgentStats]:
    """Hand what code could not prove to the Completion Search agent. Never raises.

    ``encode`` masks the request (وضع السرية); ``before_call`` runs after encoding and
    before the first model call (new fakes are persisted there). ``reg_status`` reads
    the library status of the laws the agent fetched from.
    """
    from agents.completion_search.runner import run_completion_search

    stats = AgentStats()
    try:
        tasks, stats.over_cap = build_tasks(scan, citations, content)
    except Exception:  # noqa: BLE001
        logger.warning("citation_pins: agent tasks failed", exc_info=True)
        return list(citations), stats
    if not tasks:
        return list(citations), stats
    stats.searches = len(tasks)
    stats.sent = sum(len(t.seqs) for t in tasks)
    stats.model = MODEL_LABEL
    started = time.monotonic()

    try:
        chunks = [tasks[i:i + CHUNK] for i in range(0, len(tasks), CHUNK)]
        requests = [render_request(chunk, scan, doc_type) for chunk in chunks]
        if encode is not None:
            requests = [encode(r) for r in requests]
        if before_call is not None:
            await before_call()
    except Exception:  # noqa: BLE001
        logger.warning("citation_pins: agent request build failed", exc_info=True)
        stats.failed = stats.sent
        return list(citations), stats

    gate = asyncio.Semaphore(AGENT_CONCURRENCY)

    async def one(request: str) -> Any:
        async with gate:
            for _attempt in range(CHUNK_ATTEMPTS):
                try:
                    result = await asyncio.wait_for(
                        run_completion_search(
                            request, library=library, caller=CALLER, agent_family="memory",
                            conversation_id=conversation_id, case_id=case_id, user_id=user_id,
                            agent=agent, user_message=request, code_first=False, trace=trace,
                        ),
                        CHUNK_TIMEOUT_S,
                    )
                    if not result.error:
                        return result
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — a failed request keeps code's rows
                    logger.warning("citation_pins: agent request failed (%s)", type(exc).__name__)
            return None

    jobs = [asyncio.create_task(one(r)) for r in requests]
    done, pending = await asyncio.wait(jobs, timeout=STEP_TIMEOUT_S)
    for job in pending:
        job.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    outcomes: dict[int, SearchOutcome] = {}
    offset = 0
    for chunk, job in zip(chunks, jobs):
        result = job.result() if job in done and not job.cancelled() and job.exception() is None else None
        if result is None:
            stats.failed += sum(len(t.seqs) for t in chunk)
        else:
            by_sid = {str(s.search_id).strip().upper(): s for s in result.searches}
            for i in range(len(chunk)):
                outcome = by_sid.get(f"S{i + 1}")
                if outcome is not None:
                    outcomes[offset + i] = outcome
        offset += len(chunk)

    try:
        picked = picked_regulations(outcomes)
        status_of = await reg_status(picked) if picked else {}
        out, stats.pinned, stats.not_in_library = apply_result(citations, tasks, outcomes, status_of)
    except Exception:  # noqa: BLE001 — unreadable status: nothing the agent fetched is taken
        logger.warning("citation_pins: agent result could not be applied", exc_info=True)
        stats.failed = stats.sent
        out = list(citations)
    stats.seconds = round(time.monotonic() - started, 2)
    return out, stats


__all__ = [
    "AGENT_STEP_VERSION",
    "AgentStats",
    "AgentTask",
    "CHUNK",
    "MAX_AGENT_SEARCHES",
    "MODEL_LABEL",
    "R_AGENT",
    "R_AGENT_CONFIRMED",
    "R_AGENT_OTHER",
    "R_AGENT_REWORDED",
    "apply_result",
    "build_tasks",
    "document_laws",
    "needs_agent",
    "picked_regulations",
    "render_request",
    "run_agent_step",
]
