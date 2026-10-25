"""Case-law decider rules — phase B of the reranker decider engine.

Plan: ``.claude/plans/reranker_decider_engine.md`` (build step 4, D10–D13).

The decider (``agents/deep_search_v4/shared/decider.py``) gets ONE request per
sub-query (D8): the reranker's user message as ``state`` plus two questions
per candidate ruling — ``R{p}`` (a 5-level score, per-channel rubric) and
``M{p}`` ("same mechanism?" noul). This module builds that request and turns
the answers into a ``CaseRerankerClassification`` that
``reranker._build_from_classification`` consumes unchanged.

Everything here mirrors the tested "lenient v2" arm of the case A/B:
the wording is copied verbatim from ``scripts/reranker_case_decider_ab.py``
(``_run_decider``, ``@v2``), the keep logic from ``_arm_keeps("d1u_v2", …)`` in
``scripts/reranker_case_answer_ab.py``. Changing either changes what was
judged; re-run the parity check (plan, "Validate before each flag flip").
"""
from __future__ import annotations

import re
from typing import Any

from .models import CaseKeep, CaseRerankerClassification
from .prompts import build_reranker_user_message

# -- Tested v2 wording (verbatim from scripts/reranker_case_decider_ab.py) -----

RUBRIC_V2_HEAD = (
    "Rayhan legal AI (Saudi law). Judge one candidate COURT RULING against the SUB-QUERY, for the "
    "aggregator that writes the final legal analysis. When a <planner_brief> opens the message, read it "
    "first: it tells you the user's situation and which side of the dispute the user is on; it is "
    "background, never a keyword list. You do not see the ruling text: only its matched topics "
    "(الموضوعات المطابقة) and an optional summary (الملخص). Tags: [اسانيد · الطرف · موقف المحكمة] = a "
    "ground a party relied on and how the court treated it (أساس الحكم = the ground the court decided on; "
    "رُفض/لم يُعتد به = rejected; أُثير ولم يُبنَ عليه or لم تُناقَش = not engaged); [مبدأ · النوع] = a "
    "principle the ruling lays down (موضوعي/شكلي is its subject, never a reason to demote); [وقائع] = facts. "
    "Judge only what is shown. Court level (ابتدائي/استئناف/عليا) is authority, never relevance. موقف المحكمة "
    "is bidirectional: a rejected ground on exactly this point is valuable when the user needs to know what "
    "courts refuse. If several candidates restate the same holding, only the strongest deserves the top "
    "levels. A candidate showing (لا توجد بيانات لهذا الحكم) is not relevant."
)
RUBRIC_V2_CHANNEL = {
    "principle": (
        "This sub-query asks for a LEGAL RULE or doctrine. The ruling is relevant only if the principle it "
        "lays down, or the ground it decided on, is the SAME legal mechanism the sub-query names, not a "
        "neighbouring one in the same family (انفساخ ≠ فسخ ≠ إبطال; كفالة غرمية ≠ كفالة حضورية; ترك الخصومة ≠ "
        "سقوط الخصومة ≠ سبق الفصل). A ruling on the same law but a different mechanism is low at most. A ruling "
        "where the court actually decided on the rule beats one that only mentions it."
    ),
    "facts": (
        "This sub-query describes a FACT PATTERN. The ruling is relevant when its facts match the dispute's "
        "shape: the same kind of parties, transaction, and breach or event. Shared words are not enough; a "
        "ruling with similar facts but decided on an unrelated issue is medium at most."
    ),
    "basis": (
        "This sub-query asks about a STATUTORY or PROCEDURAL GROUND. The ruling is relevant when the same "
        "ground (the same article, rule or procedural defence) was raised and the court ruled on it, "
        "accepting or rejecting it. A ruling that only cites the same law for a different point is low."
    ),
}
LEVELS_V2 = [
    "not relevant — a different dispute or mechanism, or nothing usable is shown",
    "low — same area or same law, but nothing shown would be cited for this sub-query",
    "medium — on point but partial: a secondary aspect, an applicable principle, or thin operative evidence",
    "high — the court decides this sub-query's issue on exactly this mechanism: a ground it relied on, "
    "accepted or rejected, or a principle it lays down on it",
    "critical — the operative holding that directly decides exactly what the sub-query asks",
]
MECH_Q = (
    "Does this ruling deal with the SAME specific legal mechanism, ground or fact pattern the sub-query asks "
    "about — not merely the same law, chapter or area of law?"
)
MECH_CRITERIA = {
    "true": "the same mechanism, ground or fact pattern",
    "false": "only the same law, area or wording",
}

# -- Keep rules (D10 / D11) ----------------------------------------------------

# rank = score × P(same mechanism). Per configured decider id (``DeciderReply.model``):
# (keep at rank >=, high at rank >=). d1-ultra's pair was calibrated on set 1 to qwen_b's
# keep count and held out on set 2 (the answer A/B); jev's is count-calibrated only (D11).
THRESHOLDS: dict[str, tuple[float, float]] = {
    "liquid/d1-ultra": (1.7, 2.23),
    "typesafe/jev-1.14": (0.75, 1.50),
}
# Fewer than FLOOR_N kept → the FLOOR_N best-ranked with a raw score >= FLOOR_MIN instead,
# so the mechanism product can't sink a whole sub-query (``_arm_keeps`` FLOOR_N / FLOOR_MIN).
FLOOR_N, FLOOR_MIN = 3, 1.0

# The sectioned loop passes ``rationale=f"[{channel}] {rationale}"``; the A/B's
# extract step read the channel back with this same pattern.
_CHANNEL_RE = re.compile(r"^\[(\w+)\]\s*")


def channel_of(rationale: str | None) -> str:
    """``principle`` / ``facts`` / ``basis`` from the rationale prefix; ``""`` if none."""
    m = _CHANNEL_RE.match(rationale or "")
    return m.group(1) if m else ""


def build_state(
    query: str,
    rationale: str,
    raw_markdown: str,
    *,
    planner_brief: str | None = None,
) -> str:
    """The decider ``state``: the reranker user message WITHOUT the max_keep line (as tested)."""
    return build_reranker_user_message(query, rationale, raw_markdown, planner_brief=planner_brief)


def build_questions(
    positions: list[int],
    channel: str,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """``(questions, required)``: ``R{p}`` score + ``M{p}`` noul per position, in position order."""
    rubric = f"{RUBRIC_V2_HEAD}\n\n{RUBRIC_V2_CHANNEL.get(channel, '')}"
    questions: dict[str, dict[str, Any]] = {}
    for p in positions:
        questions[f"R{p}"] = {"type": "score", "criteria": LEVELS_V2,
                              "instructions": f"{rubric}\n\nRate ONLY the ruling whose header is `### [{p}]`."}
        questions[f"M{p}"] = {"type": "noul",
                              "instructions": f"{MECH_Q}\n\nAnswer ONLY for the ruling whose header is `### [{p}]`.",
                              "criteria": dict(MECH_CRITERIA)}
    return questions, list(questions)


def scores_from_answers(
    answers: dict[str, dict[str, Any]],
    positions: list[int],
) -> dict[int, tuple[float, float]]:
    """position → (score, P(same mechanism)). Raises on a missing / non-numeric answer."""
    return {
        p: (float(answers[f"R{p}"]["score"]), float(answers[f"M{p}"]["noul"]))
        for p in positions
    }


def classify(
    scores: dict[int, tuple[float, float]],
    *,
    model: str,
    max_keep: int,
) -> CaseRerankerClassification:
    """Apply D10 to one sub-query's answers. Keeps come back capped and best first.

    Same logic as ``_arm_keeps("d1u_v2", …)``: rank by score × P(mech), ties to
    the better search position; keep at the model's cut; with fewer than
    ``FLOOR_N`` kept, the ``FLOOR_N`` best-ranked with raw score >= ``FLOOR_MIN``
    (an empty floor leaves the keeps as they were); high at the model's high
    cut; then ``max_keep``. Zero keeps is a valid answer (D4). No "not covered"
    signal for case law (D13): always sufficient, no note.

    Raises ``ValueError`` for a model with no calibrated thresholds.
    """
    if model not in THRESHOLDS:
        raise ValueError(f"no case-law thresholds for decider {model!r}")
    keep_min, high_min = THRESHOLDS[model]
    rank = {p: s * m for p, (s, m) in scores.items()}
    order = sorted(rank, key=lambda p: (-rank[p], p))   # tie → better search rank
    kept = [p for p in order if rank[p] >= keep_min]
    if len(kept) < FLOOR_N:   # floor on the raw score: the mechanism product can sink a whole set
        kept = [p for p in order if scores[p][0] >= FLOOR_MIN][:FLOOR_N] or kept
    return CaseRerankerClassification(
        sufficient=True,
        query_axes=[],
        keeps=[
            CaseKeep(
                position=p,
                relevance="high" if rank[p] >= high_min else "medium",
                reasoning="",
                satisfies_axes=[],
            )
            for p in kept[:max(0, max_keep)]
        ],
        summary_note="",
    )
