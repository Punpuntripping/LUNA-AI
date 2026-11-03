"""Contracts for the artifact_analyzer (Layer-4 Memory, plan artifact_analyzer.md).

The analyzer decides whether what this conversation already holds answers the
user's message, so a follow-up does not re-run a full deep search. Three layers
of shape live here:

- ``AnalyzerCall``    what the caller hands in.
- ``AnalyzerOutputLLM`` what the model emits, on the R1/S1 label surface.
- ``AnalyzerResult``  what the caller gets, after code has verified every quote
  and applied the verdict rules (``verify.py``). The model's verdict is an
  input to that step, not the answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from pydantic import BaseModel, Field

Verdict = Literal["sufficient", "partial", "insufficient"]

#: Up to this many reports the analyzer reads them all. Above it, the router's
#: picks are read (the newest ones when it picked none).
MAX_REPORTS_WITHOUT_PICK = 3

#: Chat rows before the current message.
CONVERSATION_ROWS = 6


# ── LLM surface ──────────────────────────────────────────────────────────────


class QuoteLLM(BaseModel):
    source: str = Field(
        description=(
            "Where the text is from: a report label (R1) or the label of a source "
            "you opened with open_sources (S3). Never a source you did not open."
        ),
    )
    text: str = Field(
        description="The passage copied word for word. Never reworded, never a summary.",
    )


class MissingLLM(BaseModel):
    text: str = Field(
        description=(
            "Legal text the question needs and we do not hold: something a source "
            "could contain. A fact only the user knows goes in ask_user instead."
        ),
    )
    reported_gap: bool = Field(
        default=False,
        description=(
            "True only when this is a gap a report already stated, copied word for "
            "word from its <gaps>."
        ),
    )


class AnalyzerOutputLLM(BaseModel):
    restatement: str = Field(
        description=(
            "The user's actual question in clear Arabic, with every fact the "
            "conversation has established (dates, amounts, type of debt, parties)."
        ),
    )
    changes_governing_law: bool = Field(
        default=False,
        description=(
            "True when a fact in the conversation puts the matter under a different "
            "law, version or remedy route than the one the report analysed as its "
            "main case. A closing note on the other case does not count as analysing it."
        ),
    )
    verdict: Verdict
    quotes: list[QuoteLLM] = Field(default_factory=list)
    missing: list[MissingLLM] = Field(default_factory=list)
    ask_user: list[str] = Field(
        default_factory=list,
        description=(
            "Facts only the user can supply that would change the answer. These "
            "are not missing: no search can find them."
        ),
    )
    rationale: str = Field(default="", description="Short reason. Logged, never shown.")


# ── Loaded context ───────────────────────────────────────────────────────────


@dataclass
class ReportRow:
    """One prior search report, already encoded under وضع السرية."""

    label: str
    item_id: str
    wi_seq: int | None
    title: str
    content_md: str
    describe_query: str
    confidence: str
    gaps: list[str]
    summary: str


@dataclass
class SourceRow:
    """One source kept by a prior search, cited or not, deduplicated by ref_id."""

    label: str
    ref_id: str
    domain: str
    #: Primary key of the row holding the whole text (see tools.DOMAIN_TABLES).
    open_key: str
    #: How a reply names this source.
    name: str
    #: The reranker-style block the model reads.
    block: str
    #: «ملغي — لم يعد سارياً» when the parent law is repealed, else "".
    status: str = ""
    #: Text kept in the search result, used when the source table cannot be read.
    stored_text: str = ""
    #: Parent law, for the law-change notice. "" for anything but a regulation.
    regulation_id: str = ""
    #: (report label, n) for each report that cited it. Empty = never cited.
    cited_in: list[tuple[str, int]] = field(default_factory=list)
    #: Reports whose search kept this source, newest first.
    report_ids: list[str] = field(default_factory=list)


# ── Call and result ──────────────────────────────────────────────────────────


@dataclass
class AnalyzerCall:
    """Built by code. The router writes none of it (no-describe-query rule)."""

    question: str
    user_id: str
    conversation_id: str
    case_id: str | None = None
    #: Recent chat rows, oldest first, already encoded. May end with ``question``.
    recent_messages: Sequence[Any] = ()
    #: The router's picks, read only above MAX_REPORTS_WITHOUT_PICK reports.
    picked_item_ids: Sequence[str] = ()
    #: True when the planner sent this turn back (D14).
    after_revert: bool = False
    #: Replay only: see the conversation as it stood before this timestamp.
    reports_before: str | None = None


@dataclass
class VerifiedQuote:
    """A quote proven to exist in the text the model was given."""

    text: str
    source_name: str
    #: The report it came from, or None for a quote taken from an opened chunk.
    item_id: str | None = None
    ref_id: str | None = None
    #: Repeal line of the parent law, kept with the quote.
    status: str = ""
    #: From a source no report cited: the reply says we found a related source.
    uncited: bool = False
    report_confidence: str | None = None
    #: Set when the words are the report's own, not a law's: the reply must say so.
    report_title: str = ""
    #: The report the answer leans on: the one whose words these are, or the one
    #: whose search found this source. The reply points back to it.
    report_id: str | None = None
    #: Laws behind this quote, for the law-change notice.
    regulation_ids: list[str] = field(default_factory=list)


class CoveredQuote(BaseModel):
    text: str
    source_name: str


class AnalyzerHandoff(BaseModel):
    """What deep search receives. JSON-safe: it survives a pause and a turn."""

    restatement: str
    covered_quotes: list[CoveredQuote] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


@dataclass
class AnalyzerResult:
    verdict: Verdict
    restatement: str = ""
    quotes: list[VerifiedQuote] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    #: Facts only the user holds. The responder may ask for one; a search cannot.
    ask_user: list[str] = field(default_factory=list)
    #: The fixed sentence when a quoted law is in transition, else "".
    transition_line: str = ""
    #: Failed or had nothing to read. The caller searches, as it does today.
    degraded: bool = False
    #: What the model said before the rules ran. Telemetry only.
    verdict_before_rules: Verdict | None = None
    quotes_dropped: int = 0
    reports_n: int = 0
    sources_n: int = 0
    opened_n: int = 0
    #: Every report read, newest first.
    report_ids: list[str] = field(default_factory=list)

    def handoff(self) -> AnalyzerHandoff | None:
        if self.degraded:
            return None
        return AnalyzerHandoff(
            restatement=self.restatement,
            covered_quotes=[
                CoveredQuote(text=q.text, source_name=q.source_name)
                for q in self.quotes
            ],
            missing=list(self.missing),
        )
