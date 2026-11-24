"""Decide each citation of an attachment — by code, from checkable facts. Pure.

Plan: ``.claude/plans/completion_search.md`` §4.6.3 (changed after Phase 0, §7.1).
Input: what the scan found (``ScanResult``) and what the library holds for the laws
the document names (``DocFacts``). Output: one ``Citation`` per article number per
mention, with a status:

- ``pinned``         exact library title · the law is in force · the article exists ·
                     the link to the law is checkable (named in the same phrase, or a
                     back-reference with exactly one law in reach, or the document's
                     only law);
- ``medium``         law and article found, but the link is a guess (the law is named
                     elsewhere and the document names several), or the name is a short
                     form of exactly one title, or the number is a «76/1» fallback —
                     stored, never delivered;
- ``not_in_library`` the law is named, no library title matches or opens with it (C17);
- ``other_target``   the article belongs to something that is not a law («من العقد»);
- ``unresolved``     everything else — ``reason`` says why.

No model is involved HERE: this module is what code can prove (about half of all
citations on real attachments). The citations it could not prove and reading can
settle — a guessed link, «من النظام» after several laws, a name that is not a title —
are handed to the Completion Search agent by ``agent_step.py`` (owner decision C26).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from agents.tool_repository.citation_scan import (
    BASIS_BACK_REF,
    BASIS_NO_LAW,
    BASIS_OTHER_TARGET,
    BASIS_SAME_PHRASE,
    BASIS_SINGLE_LAW,
    LawRef,
    Mention,
    ScanResult,
    TitleIndex,
)

PINNED = "pinned"
MEDIUM = "medium"
NOT_IN_LIBRARY = "not_in_library"
OTHER_TARGET = "other_target"
UNRESOLVED = "unresolved"

IN_FORCE = frozenset({"in_force", "in_force_amended"})

# reasons (stored in attachment_citations.reason; English, forensic)
R_LINK_GUESS = "link_is_a_guess"
R_SHORT_NAME = "short_form_of_one_title"
R_NUMBER_FALLBACK = "compound_number_fallback"
R_NAME_OPEN = "name_may_run_on"
R_SEVERAL_TITLES = "name_opens_several_titles"
R_BACKREF_SEVERAL = "back_reference_several_laws"
R_BACKREF_NONE = "back_reference_without_law"
R_NO_LAW = "no_law_named"
R_RANGE = "range_too_wide"
R_NO_ARTICLE = "article_not_in_law"
R_NO_TEXT = "law_has_no_article_text"
R_NOT_IN_FORCE = "not_in_force"
R_NO_EXEC = "executive_regulation_not_found"
R_NUMBER_CONFLICT = "number_written_twice_disagrees"
R_LOOSE_NAME = "name_differs_by_al"

#: «المادة 57/2» in a law whose articles are plainly numbered is article 57, paragraph 2.
#: Taken as sure only in that shape: the article first and larger, a small paragraph number.
_MAX_PARAGRAPH = 20


@dataclass(frozen=True)
class RegFact:
    id: str
    title: str
    status_class: str = ""
    has_articles: bool = True


@dataclass
class DocFacts:
    """What the library holds for the laws ONE document names. Loaded by the caller."""

    titles: TitleIndex
    regs: Mapping[str, RegFact] = field(default_factory=dict)
    #: (regulation_id, article_number as stored) → article id
    articles: Mapping[tuple[str, str], str] = field(default_factory=dict)
    #: parent law id → its executive regulations' ids
    executive_of: Mapping[str, Sequence[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Citation:
    seq: int
    char_start: int
    char_end: int
    para_n: int
    sentence_start: int
    sentence_end: int
    article_number: str
    law_as_written: str
    basis: str
    status: str
    reason: str = ""
    regulation_id: str | None = None
    article_id: str | None = None
    decided_by: str = "code"

    def row(self, item_id: str) -> dict:
        """The ``attachment_citations`` row."""
        return {
            "item_id": item_id, "seq": self.seq, "char_start": self.char_start, "char_end": self.char_end,
            "para_n": self.para_n, "sentence_start": self.sentence_start, "sentence_end": self.sentence_end,
            "article_number": self.article_number, "law_as_written": self.law_as_written[:200],
            "basis": self.basis, "decided_by": self.decided_by, "status": self.status, "reason": self.reason,
            "regulation_id": self.regulation_id, "article_id": self.article_id,
        }


def number_keys(number: str) -> tuple[list[str], list[str]]:
    """``(exact keys, fallback keys)`` an article number may be stored under.

    «76/1» is tried as written and with the other separator and order; only then the
    plain article number, which is a fallback and never a sure match.
    """
    for sep in ("/", "-", "."):
        if sep in number:
            a, _, b = number.partition(sep)
            b = b.split()[0] if b.split() else b
            exact = [number, f"{a}-{b}", f"{a}/{b}", f"{b}/{a}", f"{b}-{a}"]
            try:
                base = str(max(int(a), int(b)))
            except ValueError:
                base = a
            return list(dict.fromkeys(exact)), [base]
    return [number], []


def all_number_keys(numbers: Sequence[str]) -> list[str]:
    """Every key the library may hold for these numbers (what the caller fetches)."""
    out: list[str] = []
    for n in numbers:
        exact, fallback = number_keys(n)
        for key in (*exact, *fallback):
            if key not in out:
                out.append(key)
    return out


def _pick(ids: Sequence[str], facts: DocFacts) -> RegFact | None:
    """Among regulations that share one title: in force with article text first."""
    known = [facts.regs[i] for i in ids if i in facts.regs]
    if not known:
        return None
    return sorted(known, key=lambda r: (r.status_class not in IN_FORCE, not r.has_articles, r.id))[0]


def candidate_regulations(scan: ScanResult, facts_titles: TitleIndex) -> set[str]:
    """Regulation ids the caller must load facts for: every title the document names,
    and the one title a short name opens."""
    ids: set[str] = set()
    for law in scan.named_laws:
        if law.in_library:
            ids.update(facts_titles.ids(law.key))
        else:
            opened = {facts_titles.ids(t) for t in _guessed_titles(law, facts_titles)}
            if len(opened) == 1:
                ids.update(next(iter(opened)))
    return ids


def _guessed_titles(law: LawRef, titles: TitleIndex) -> list[tuple[str, ...]]:
    """Library titles a name that is NOT a title may still mean: the titles it opens
    («نظام التأمينات»), or — when it opens none — a title that differs only by «ال»
    («نظام اثبات»)."""
    return titles.starting_with(law.key) or titles.spelled_loosely(law.key)


def _checkable(scan: ScanResult, mention: Mention) -> bool:
    if mention.basis in (BASIS_SAME_PHRASE, BASIS_SINGLE_LAW):
        return True
    if mention.basis == BASIS_BACK_REF:
        return mention.laws_before == 1 or len(scan.distinct_named()) == 1
    return False


def _decide(scan: ScanResult, mention: Mention, number: str, facts: DocFacts) -> tuple[str, str, str | None, str | None]:
    """``(status, reason, regulation_id, article_id)`` for one article number of one mention."""
    if mention.basis == BASIS_OTHER_TARGET:
        return OTHER_TARGET, mention.other_target, None, None
    if mention.range_wide:
        return UNRESOLVED, R_RANGE, None, None
    if mention.conflict:
        # «المادة (37) السادسة والثلاثين»: one of the two was misread — usually a scan.
        return UNRESOLVED, R_NUMBER_CONFLICT, None, None
    if mention.basis == BASIS_NO_LAW:
        return UNRESOLVED, R_NO_LAW, None, None
    if mention.law is None:
        return UNRESOLVED, R_BACKREF_NONE, None, None

    law: LawRef = scan.laws[mention.law]
    short_name = False
    loose_name = False
    if law.in_library:
        if law.open_end:
            return UNRESOLVED, R_NAME_OPEN, None, None
        reg = _pick(facts.titles.ids(law.key), facts)
    else:
        opened = {facts.titles.ids(t) for t in _guessed_titles(law, facts.titles)}
        if not opened:
            return NOT_IN_LIBRARY, "", None, None
        if len(opened) > 1:
            return UNRESOLVED, R_SEVERAL_TITLES, None, None
        reg = _pick(next(iter(opened)), facts)
        short_name = True
        loose_name = not facts.titles.starting_with(law.key)
    if reg is None:
        return NOT_IN_LIBRARY, "", None, None

    if mention.exec_reg or law.exec_of:
        reg = _pick(list(facts.executive_of.get(reg.id, ())), facts)
        if reg is None:
            return UNRESOLVED, R_NO_EXEC, None, None
    if not reg.has_articles:
        return UNRESOLVED, R_NO_TEXT, reg.id, None

    exact, fallback = number_keys(number)
    article_id = next((facts.articles[(reg.id, k)] for k in exact if (reg.id, k) in facts.articles), None)
    via_fallback = False
    if article_id is None:
        article_id = next((facts.articles[(reg.id, k)] for k in fallback if (reg.id, k) in facts.articles), None)
        via_fallback = article_id is not None
    if article_id is None:
        return UNRESOLVED, R_NO_ARTICLE, reg.id, None
    if reg.status_class not in IN_FORCE:
        return UNRESOLVED, R_NOT_IN_FORCE, reg.id, article_id

    if short_name:
        return MEDIUM, (R_LOOSE_NAME if loose_name else R_SHORT_NAME), reg.id, article_id
    if via_fallback and not _article_then_paragraph(number, fallback):
        return MEDIUM, R_NUMBER_FALLBACK, reg.id, article_id
    if _checkable(scan, mention):
        return PINNED, "", reg.id, article_id
    if mention.basis == BASIS_BACK_REF:
        return UNRESOLVED, R_BACKREF_SEVERAL, reg.id, article_id
    return MEDIUM, R_LINK_GUESS, reg.id, article_id


def _article_then_paragraph(number: str, fallback: Sequence[str]) -> bool:
    """«57/2» read as article 57, paragraph 2 — the only reading there is when the law
    holds no article keyed «57/2» (the caller got here through the fallback) and the
    article comes first and is the larger number. «2/57» or «3/5» stay a guess."""
    for sep in ("/", "-", "."):
        if sep in number:
            a, _, b = number.partition(sep)
            try:
                first, second = int(a), int(b.split()[0])
            except (ValueError, IndexError):
                return False
            return first > second >= 1 and second <= _MAX_PARAGRAPH and list(fallback) == [str(first)]
    return False


def resolve_citations(scan: ScanResult, facts: DocFacts) -> list[Citation]:
    """One ``Citation`` per article number of every citing mention, in document order."""
    out: list[Citation] = []
    for mention in scan.citing:
        law_text = scan.laws[mention.law].text if mention.law is not None else ""
        for number in mention.numbers:
            status, reason, reg_id, article_id = _decide(scan, mention, number, facts)
            keep = status in (PINNED, MEDIUM)
            out.append(Citation(
                seq=len(out) + 1,
                char_start=mention.char_start,
                char_end=mention.char_end,
                para_n=mention.para_n,
                sentence_start=mention.sentence_start,
                sentence_end=mention.sentence_end,
                article_number=number,
                law_as_written=" ".join(law_text.split()),
                basis=mention.basis,
                status=status,
                reason=reason,
                regulation_id=reg_id if keep else None,
                article_id=article_id if keep else None,
            ))
    return out


def counts(scan: ScanResult, citations: Sequence[Citation]) -> dict[str, int]:
    """The counters stored on the scan row."""
    return {
        "mentions": len(scan.citing),
        "headings": len(scan.mentions) - len(scan.citing),
        "pinned": sum(1 for c in citations if c.status == PINNED),
        "medium": sum(1 for c in citations if c.status == MEDIUM),
        "not_in_library": sum(1 for c in citations if c.status == NOT_IN_LIBRARY),
    }


__all__ = [
    "Citation",
    "DocFacts",
    "IN_FORCE",
    "MEDIUM",
    "NOT_IN_LIBRARY",
    "OTHER_TARGET",
    "PINNED",
    "RegFact",
    "UNRESOLVED",
    "all_number_keys",
    "candidate_regulations",
    "counts",
    "number_keys",
    "resolve_citations",
]
