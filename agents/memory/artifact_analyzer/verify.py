"""Code has the last word: quotes must exist, and the verdict follows from them.

The model's verdict goes in; the rules below decide what comes out. A quote is
checked by exact match after normalising layout only (whitespace, ``[n]``
markers, bold markers). There is deliberately no fuzzy matching: a quote that
differs by a word is a rephrase, and a rephrase is exactly what this agent must
not produce.
"""
from __future__ import annotations

import html
import re

from .deps import AnalyzerDeps
from .full_text import MAX_FULL_READS, eligible
from .models import (
    AnalyzerOutputLLM,
    AnalyzerResult,
    QuoteLLM,
    ReadingCorrection,
    ReadingErrorLLM,
    ReportRow,
    SourceRow,
    VerifiedQuote,
)

_CITE_RE = re.compile(r"\[\s*\d+(?:\s*[,،]\s*\d+)*\s*\]")
_BOLD_RE = re.compile(r"\*\*")
_NUMBER_RE = re.compile(r"\d+")
_EDGE_CHARS = " \t\n«»\"'…"

_MAX_NAMES = 2

# What a rule-made partial is missing when the model listed nothing. Seen live:
# without it the search after «نعم، ابحث» got no "still needed" at all.
_OTHER_LAW_MISSING = (
    "أحكام النظام المنطبق على هذه الوقائع ووسائل الاسترداد فيه، إذ بُني التقرير "
    "السابق على نظام آخر"
)
_LOW_CONFIDENCE_MISSING = "تأكيد هذا الحكم من مصادر نظامية، إذ إن ثقة التقرير السابق فيه منخفضة"
#: How far past a quote its citation may sit, within the same paragraph.
_CITE_LOOKAHEAD_CHARS = 200


def _clean_with_map(raw: str) -> tuple[str, list[int]]:
    """Text without layout, plus each kept character's index in ``raw``."""
    cites = {m.start(): m.end() for m in _CITE_RE.finditer(raw)}
    bolds = {m.start(): m.end() for m in _BOLD_RE.finditer(raw)}
    chars: list[str] = []
    positions: list[int] = []
    i = 0
    while i < len(raw):
        if i in cites:
            # "الثلث [5]." must read "الثلث."
            if chars and chars[-1] == " ":
                chars.pop()
                positions.pop()
            i = cites[i]
            continue
        if i in bolds:
            i = bolds[i]
            continue
        ch = raw[i]
        if ch.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                positions.append(i)
        else:
            chars.append(ch)
            positions.append(i)
        i += 1
    return "".join(chars), positions


def normalize(text: str) -> str:
    # The context is HTML-escaped, so a model may copy an entity back.
    return _clean_with_map(html.unescape(text or ""))[0].strip(_EDGE_CHARS)


def _sources_behind(deps: AnalyzerDeps, report: ReportRow, raw_slice: str) -> list[SourceRow]:
    numbers: list[int] = []
    for marker in _CITE_RE.findall(raw_slice):
        numbers.extend(int(n) for n in _NUMBER_RE.findall(marker))
    found: list[SourceRow] = []
    for n in numbers:
        source = deps.source_for_citation(report.label, n)
        if source is not None and source not in found:
            found.append(source)
    return found


def _verify_report_quote(deps: AnalyzerDeps, report: ReportRow, text: str) -> VerifiedQuote | None:
    raw = html.unescape(report.content_md)
    clean, positions = _clean_with_map(raw)
    start = clean.find(text)
    if start < 0:
        return None
    raw_start = positions[start]
    raw_end = positions[start + len(text) - 1] + 1
    # A sentence's citation usually sits after the words quoted.
    tail = raw[raw_end:raw_end + _CITE_LOOKAHEAD_CHARS].split("\n", 1)[0]
    next_cite = _CITE_RE.search(tail)
    if next_cite:
        raw_end += next_cite.end()
    behind = _sources_behind(deps, report, raw[raw_start:raw_end])
    names = [s.name for s in behind][:_MAX_NAMES]
    return VerifiedQuote(
        text=text,
        # The laws behind the sentence, if its citation resolves. The words are
        # still the report's, which report_title records for the reply.
        source_name=" · ".join(names),
        report_title=report.title,
        report_id=report.item_id,
        item_id=report.item_id,
        report_confidence=report.confidence,
        regulation_ids=[s.regulation_id for s in behind if s.regulation_id],
        cited_labels=[s.label for s in behind],
    )


def _verify_quote(deps: AnalyzerDeps, quote: QuoteLLM) -> VerifiedQuote | None:
    text = normalize(quote.text)
    if not text:
        return None
    label = (quote.source or "").strip().upper()
    report = deps.reports.get(label)
    if report is not None:
        return _verify_report_quote(deps, report, text)
    # Only text the model read whole: an opened source, or the full half of
    # read_full_text. A summary, and «ما قرأه البحث», are never quotable.
    read = [t for t in (deps.opened.get(label), deps.full_texts.get(label)) if t]
    if not any(text in normalize(t) for t in read):
        return None
    source = deps.sources[label]
    return VerifiedQuote(
        text=text,
        source_name=source.name,
        ref_id=source.ref_id,
        status=source.status,
        uncited=not source.cited_in,
        report_id=source.report_ids[0] if source.report_ids else None,
        regulation_ids=[source.regulation_id] if source.regulation_id else [],
        domain=source.domain,
        open_key=source.open_key,
    )


def unverified_quotes(deps: AnalyzerDeps, output: AnalyzerOutputLLM) -> list[QuoteLLM]:
    return [q for q in output.quotes if _verify_quote(deps, q) is None]


def _verify_reading_error(deps: AnalyzerDeps, error: ReadingErrorLLM) -> ReadingCorrection | None:
    """Both sides word for word: the report's in the report, the other in a full text."""
    report = deps.reports.get((error.report or "").strip().upper())
    label = (error.source or "").strip().upper()
    full = deps.full_texts.get(label)
    report_text = normalize(error.report_text)
    corrected = normalize(error.full_text)
    if report is None or full is None or not report_text or not corrected:
        return None
    if report_text not in normalize(report.content_md) or corrected not in normalize(full):
        return None
    if report_text == corrected:
        return None  # the same words cannot contradict each other
    source = deps.sources[label]
    return ReadingCorrection(
        report_id=report.item_id,
        report_text=report_text,
        corrected_text=corrected,
        source_name=source.name,
        domain=source.domain,
        open_key=source.open_key,
    )


def reading_errors_of(output: AnalyzerOutputLLM) -> list[ReadingErrorLLM]:
    """Present only on the full-text schema."""
    return list(getattr(output, "reading_errors", None) or [])


def unverified_reading_errors(deps: AnalyzerDeps, output: AnalyzerOutputLLM) -> list[ReadingErrorLLM]:
    return [e for e in reading_errors_of(output) if _verify_reading_error(deps, e) is None]


def _corrections(
    deps: AnalyzerDeps, output: AnalyzerOutputLLM,
) -> list[tuple[ReadingCorrection, str]]:
    """Verified corrections, each with the label of the source it was read from."""
    found: list[tuple[ReadingCorrection, str]] = []
    for error in reading_errors_of(output):
        correction = _verify_reading_error(deps, error)
        if correction is None:
            continue
        key = (correction.report_text, correction.open_key)
        if all((c.report_text, c.open_key) != key for c, _ in found):
            found.append((correction, (error.source or "").strip().upper()))
    return found


def unread_citations(deps: AnalyzerDeps, output: AnalyzerOutputLLM) -> list[tuple[str, str]]:
    """D10: (report sentence, source label) quoted on a ruling or guided service
    that was not read in full. Empty once the turn's reads are used up."""
    if not deps.full_text or len(deps.full_read) >= MAX_FULL_READS:
        return []
    unread: list[tuple[str, str]] = []
    for quote in output.quotes:
        verified = _verify_quote(deps, quote)
        if verified is None or verified.item_id is None:
            continue
        for label in verified.cited_labels:
            source = deps.sources.get(label)
            if source is not None and eligible(source) and label not in deps.full_texts:
                unread.append((verified.text, label))
    return unread


def _corrected(
    deps: AnalyzerDeps, quote: VerifiedQuote, corrections: list[ReadingCorrection],
) -> bool:
    """A report's own sentence that a correction, new or saved, shows to be wrong."""
    if quote.item_id is None:
        return False
    wrong = [c.report_text for c in corrections if c.report_id == quote.item_id]
    wrong += [
        normalize(text)
        for report in deps.reports.values() if report.item_id == quote.item_id
        for text in report.corrected_texts
    ]
    return any(w and (w in quote.text or quote.text in w) for w in wrong)


def _is_reported_gap(deps: AnalyzerDeps, text: str) -> bool:
    """Reports from before gaps were saved state them in their summary."""
    wanted = normalize(text)
    if not wanted:
        return False
    for report in deps.reports.values():
        for stated in (*report.gaps, report.summary):
            if wanted in normalize(stated):
                return True
    return False


def finalize(deps: AnalyzerDeps, output: AnalyzerOutputLLM) -> AnalyzerResult:
    found = _corrections(deps, output)
    corrections = [c for c, _ in found]
    quotes: list[VerifiedQuote] = []
    seen: set[str] = set()
    # Each correction's full-text words come first: the reply prints only the
    # first few quotes, and a correction the user never sees corrects nothing.
    # They count as support like any other quote; they never move the verdict (D12).
    for correction, label in found:
        verified = _verify_quote(deps, QuoteLLM(source=label, text=correction.corrected_text))
        if verified is not None and verified.text not in seen:
            seen.add(verified.text)
            quotes.append(verified)
    printed_by_corrections = set(seen)
    quotes_dropped = 0
    for quote in output.quotes:
        verified = _verify_quote(deps, quote)
        # Code must never print a report sentence a full text showed wrong.
        if verified is None or _corrected(deps, verified, corrections):
            quotes_dropped += 1
        elif verified.text in printed_by_corrections:
            continue  # the model quoted a correction's words itself: kept above
        elif verified.text in seen:
            quotes_dropped += 1
        else:
            seen.add(verified.text)
            quotes.append(verified)

    missing = [m for m in output.missing if (m.text or "").strip()]
    # Scope decides the verdict: a point no prior search looked for was never
    # examined, so a report's silence on it answers nothing.
    outside = [p.strip() for p in output.outside_search if (p or "").strip()]
    only_reported_gaps = not outside and bool(missing) and all(
        m.reported_gap and _is_reported_gap(deps, m.text) for m in missing
    )

    verdict = output.verdict
    # D11: a gap a search already reported is not a reason to search on our own.
    if verdict == "insufficient" and only_reported_gaps:
        verdict = "partial"
    if verdict != "insufficient" and not quotes and not only_reported_gaps:
        verdict = "insufficient"
    # Something a source could hold is missing, or was never looked for, so it
    # was not answered in full.
    if verdict == "sufficient" and (not quotes or missing or outside):
        verdict = "partial"
    missing_out = [m.text.strip() for m in missing]
    missing_out += [p for p in outside if p not in missing_out]
    # The report analysed another law. Quotes from it can read as an answer while
    # carrying its deadlines and remedies over to the wrong law (turn 5).
    if verdict == "sufficient" and output.changes_governing_law:
        verdict = "partial"
        missing_out = missing_out or [_OTHER_LAW_MISSING]
    # D18: a low-confidence report cannot settle a question on its own text. A
    # chunk quote is the law itself, so it is not affected.
    if verdict == "sufficient" and all(q.report_confidence == "low" for q in quotes):
        verdict = "partial"
        missing_out = missing_out or [_LOW_CONFIDENCE_MISSING]

    return AnalyzerResult(
        verdict=verdict,
        restatement=(output.restatement or "").strip(),
        quotes=quotes,
        # A rule-made partial still tells the search what to look for.
        missing=missing_out,
        ask_user=[a.strip() for a in output.ask_user if (a or "").strip()],
        verdict_before_rules=output.verdict,
        outside_search=outside,
        reading_errors=corrections,
        reading_errors_dropped=len(reading_errors_of(output)) - len(corrections),
        full_read_n=len(deps.full_texts),
        quotes_dropped=quotes_dropped,
        reports_n=len(deps.reports),
        sources_n=len(deps.sources),
        opened_n=len(deps.opened),
        report_ids=[r.item_id for r in deps.reports.values()],
    )
