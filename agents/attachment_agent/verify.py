"""Code has the last word: every quote must be in a file, and in what was read.

A quote is matched in the file's MASKED text (``FileDoc.view.text``) — the text
the model read, numbers and e-mails masked the same way. Matching ignores layout
(``artifact_analyzer.verify.normalize``) and letter / digit shapes
(``citation_scan.find_folded``), nothing else: «٢٥/٣/١٤٤٧» finds «25/3/1447», a
changed word does not. A quote must also lie inside a range a tool returned (or a
short file's whole text): the model may not quote what it never read.

Failing quotes go back to the model once (``agent._check_output``); after that
:func:`finalize` drops them. The quote's text is taken from the file at the match
and its location (file, section, owner, page) is set from the match offset —
never from the model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents.utils.history import neutralize_system_brackets

from .deps import AttachmentDeps, FileDoc
from .models import AttachmentAgentResult, PointOut, QuoteOut
from .output import AttachmentOutputLLM, QuoteLLM
from .text import locate, normalize

NO_VERIFIED_QUOTE = "لم يُعثر على نصٍّ في الملفات يثبت ذلك فيما قُرئ منها."
DEFAULT_CLARIFY = "ما الذي تريد معرفته من الملف تحديدًا؟"
DEFAULT_CLARIFY_EDIT = "ما الذي تريد تغييره تحديدًا، وفي أي موضع من الملف؟"
#: A change was asked but none could be staged, and the model gave no reason.
NOTHING_CHANGED = "لم يُنفَّذ أي تعديل."
_NOTE_MAX_CHARS = 600
_MAX_SEARCHED_NAMED = 5


@dataclass
class Located:
    file: FileDoc
    start: int   # masked offsets
    end: int


@dataclass
class Stats:
    quotes_kept: int = 0
    quotes_dropped: int = 0
    points_dropped: int = 0


def locate_quote(deps: AttachmentDeps, quote: QuoteLLM) -> tuple[Located | None, str]:
    """Where the quote sits in what was read: the named file first, then the
    others (a mislabelled file is not a reason to drop a true quote). Reason when
    not: ``"not_read"`` (in a file, outside what was read) or ``"not_in_files"``."""
    named = deps.file(quote.file)
    order = ([named] if named else []) + [f for f in deps.files.values() if f is not named]
    unread = False
    for f in order:
        for start, end in locate(f.view.text, quote.text):
            if f.was_seen(start, end):
                return Located(f, start, end), ""
            unread = True
    return None, ("not_read" if unread else "not_in_files")


def quote_out(loc: Located) -> QuoteOut:
    """The quote as the router gets it: the file's own words at the match, and
    the location code read from the match offset."""
    f = loc.file
    pos = f.view.to_orig(loc.start)
    section = f.section_at(pos)
    return QuoteOut(
        text=neutralize_system_brackets(normalize(f.view.text[loc.start:loc.end])),
        item_id=f.item_id,
        file_title=f.title,
        wi=f.wi,
        section_ref=section.ref if section else None,
        section_what=neutralize_system_brackets(section.what) if section and section.what else None,
        owner=neutralize_system_brackets(section.owner) if section and section.owner else None,
        page=f.page_at(pos),
    )


def retry_reasons(deps: AttachmentDeps, out: AttachmentOutputLLM) -> list[str]:
    """What the model must fix, named. Empty = nothing to fix. Quotes are checked
    on every request (a change request may carry points too); a found answer's
    shape only on a read."""
    reading = out.request == "read"
    not_in: list[QuoteLLM] = []
    not_read: list[QuoteLLM] = []
    bare: list[str] = []
    for point in out.points:
        for q in point.quotes:
            loc, why = locate_quote(deps, q)
            if loc is None:
                (not_read if why == "not_read" else not_in).append(q)
        if reading and out.outcome == "found" and not point.quotes:
            bare.append(point.statement)
    reasons: list[str] = []
    if not_in:
        listed = "\n".join(f"- {q.file}: «{q.text[:160]}»" for q in not_in)
        reasons.append(
            "These quotes are not word for word in the files:\n"
            f"{listed}\nCopy the exact text from what a tool returned (one continuous passage, "
            "no '…'), or remove the quote."
        )
    if not_read:
        listed = "\n".join(f"- {q.file}: «{q.text[:160]}»" for q in not_read)
        reasons.append(
            "These quotes are in a file but not in anything you have read:\n"
            f"{listed}\nRead that part first (find, read_sections or read_pages), or remove the quote."
        )
    if bare:
        listed = "\n".join(f"- {s[:120]}" for s in bare)
        reasons.append(
            f"These points of a found answer have no quote:\n{listed}\n"
            "Add the passage that shows each one, or drop the point."
        )
    if reading and out.outcome == "found" and not out.points:
        reasons.append("A found answer needs at least one point with a quote; otherwise answer not_found.")
    return reasons


def read_summary(deps: AttachmentDeps) -> str:
    """What was read and searched, in Arabic — the router's basis for "not found"."""
    files = list(deps.files.values())
    whole = [f for f in files if f.read_whole()]
    partly = [f for f in files if f.seen and f not in whole]
    parts: list[str] = []
    if whole:
        parts.append("قُرئ كاملًا: " + "، ".join(f"«{f.title}»" for f in whole))
    if partly:
        parts.append("قُرئت أجزاء من: " + "، ".join(f"«{f.title}»" for f in partly))
    if deps.searched:
        named = "، ".join(f"«{p}»" for p in deps.searched[:_MAX_SEARCHED_NAMED])
        parts.append(f"بُحث في نص الملفات كاملًا عن: {named}")
    if not parts:
        parts.append("لم يُقرأ شيء من نص الملفات")
    line = "؛ ".join(parts) + "."
    if len(whole) < len(files):
        line += " لم تُقرأ الملفات كلها كاملة، فلا يُجزم بأن المطلوب غير موجود فيها."
    return line


def _clean(text: str, limit: int = _NOTE_MAX_CHARS) -> str:
    text = neutralize_system_brackets(" ".join(str(text or "").split()))
    return text[:limit].rstrip()


def _which_file_question(deps: AttachmentDeps) -> str:
    titles = [f"«{f.title}»" for f in list(deps.files.values())[:5]]
    if len(titles) == 1:
        return f"هل تقصد الملف {titles[0]}؟"
    if len(titles) == 2:
        return f"أي الملفين تقصد: {titles[0]} أم {titles[1]}؟"
    return "أي الملفات تقصد: " + "، ".join(titles) + "؟"


def finalize(deps: AttachmentDeps, out: AttachmentOutputLLM) -> tuple[AttachmentAgentResult, Stats]:
    """Checked points and the outcome. For a change / re-analysis request the
    outcome is provisional: :func:`edit_outcome` settles it from what was applied."""
    stats = Stats()
    points: list[PointOut] = []
    for point in out.points:
        quotes: list[QuoteOut] = []
        for q in point.quotes:
            loc, _why = locate_quote(deps, q)
            if loc is None:
                stats.quotes_dropped += 1
                continue
            built = quote_out(loc)
            if any(x.text == built.text and x.item_id == built.item_id for x in quotes):
                continue
            quotes.append(built)
        statement = _clean(point.statement)
        if not quotes or not statement:
            stats.points_dropped += 1
            continue
        stats.quotes_kept += len(quotes)
        points.append(PointOut(statement=statement, quotes=quotes))

    outcome = out.outcome
    note = _clean(out.note)
    ask_user = _clean(out.ask_user, 300) or None
    if out.request != "read":
        if outcome == "which_file":
            ask_user = ask_user or _which_file_question(deps)
        elif outcome == "needs_clarification":
            ask_user = ask_user or DEFAULT_CLARIFY_EDIT
        else:
            ask_user = None
        return AttachmentAgentResult(outcome=outcome, points=points, ask_user=ask_user, note=note or None), stats
    if outcome == "edited":   # not a read outcome: what the points show decides
        outcome = "found" if points else "not_found"
    if outcome == "found" and not points:
        outcome = "not_found"
        note = NO_VERIFIED_QUOTE
    if outcome == "not_found":
        note = " ".join(x for x in (note, read_summary(deps)) if x)
        ask_user = None
    elif outcome == "which_file":
        ask_user = ask_user or _which_file_question(deps)
    elif outcome == "needs_clarification":
        ask_user = ask_user or DEFAULT_CLARIFY
    else:
        ask_user = None
    return AttachmentAgentResult(
        outcome=outcome,
        points=points,
        ask_user=ask_user,
        note=note or None,
    ), stats


def edit_outcome(result: AttachmentAgentResult, report: Any | None) -> AttachmentAgentResult:
    """The outcome of a change / re-analysis request, from what was applied.

    ``report`` — ``apply.ApplyReport``, or ``None`` when nothing was staged.
    Something written → ``edited``; only a re-analysis started →
    ``reanalysis_started``; nothing → ``not_edited`` (the failure lines, or the
    model's note, say why). A question the model asked about the rest (an unclear
    part) is kept; a which-file / clarification answer with nothing staged stays
    as it is (D19: unclear → one short question, nothing changed)."""
    asked = result.outcome in ("which_file", "needs_clarification")
    if report is None:
        if asked:
            return result
        return result.model_copy(update={
            "outcome": "not_edited", "ask_user": None, "note": result.note or NOTHING_CHANGED,
        })
    if report.edits_applied > 0:
        outcome = "edited"
    elif report.reanalysis_started > 0:
        outcome = "reanalysis_started"
    else:
        outcome = "not_edited"
    return result.model_copy(update={
        "outcome": outcome,
        "edits": list(report.lines),
        "ask_user": result.ask_user if asked else None,
    })


__all__ = [
    "DEFAULT_CLARIFY",
    "DEFAULT_CLARIFY_EDIT",
    "NOTHING_CHANGED",
    "NO_VERIFIED_QUOTE",
    "Located",
    "Stats",
    "edit_outcome",
    "finalize",
    "locate_quote",
    "quote_out",
    "read_summary",
    "retry_reasons",
]
