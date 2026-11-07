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

Official judicial principles (``.claude/plans/official_principles_refs.md``
D2) are the one exception: a principle block gets its own rubric and keep rule
(``PRINCIPLE_*`` below) and its own cap, and never touches the ruling logic.
"""
from __future__ import annotations

import re
from typing import Any

from .models import (
    PRINCIPLE_EXTRA_KEEP,
    PRINCIPLE_HEADER_AR,
    CaseKeep,
    CaseRerankerClassification,
)
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

# -- Official judicial principles (official_principles_refs.md D2) -------------
# NOT from the A/B: no principle candidate existed when the v2 wording above was
# judged. Same owner rule as prompt_2's «keep by default» section — keep unless
# the principle's SUBJECT is unrelated to the sub-query's matter; high when it
# decides the sub-query's mechanism. The thresholds are uncalibrated midpoints
# between adjacent levels; re-check them against qwen keeps before the decider
# engine is flipped on with principles in the window.

PRINCIPLE_RUBRIC = (
    "Rayhan legal AI (Saudi law). Judge one candidate OFFICIAL JUDICIAL PRINCIPLE (مبدأ قضائي رسمي) "
    "against the SUB-QUERY, for the aggregator that writes the final legal analysis. When a "
    "<planner_brief> opens the message, read it first: it is background, never a keyword list. An "
    "official principle is a rule published by the highest judicial body for the courts to follow, not "
    "the ruling of one case; it is shown as its chapter (الباب, may be absent) and the rule text, and "
    "nothing else. Because it is official it is kept by default: the ONLY reason to rate it not relevant "
    "is that its subject (its الباب and what the rule governs) is unrelated to the sub-query's matter. "
    "A procedural rule (jurisdiction, form, deadlines, objection) is never demoted for being procedural, "
    "and a principle is never demoted because another candidate states a similar rule."
)
PRINCIPLE_LEVELS = [
    "not relevant — the rule governs a different subject or sector than the sub-query's matter",
    "related — the same area as the sub-query's matter; the rule bears on it only indirectly",
    "on the matter — the rule governs the sub-query's matter, but not the specific mechanism it asks about",
    "decides the mechanism — the rule states how the sub-query's specific mechanism is decided",
    "critical — the rule directly answers exactly what the sub-query asks",
]
PRINCIPLE_MECH_Q = (
    "Does this official principle govern the SAME specific legal mechanism the sub-query asks about — "
    "not merely the same area of law?"
)
PRINCIPLE_MECH_CRITERIA = {
    "true": "the rule governs the same mechanism",
    "false": "the rule is only in the same area of law",
}
# Keep at raw score >= PRINCIPLE_KEEP_MIN (nearer "related" than "not
# relevant"); high at raw >= PRINCIPLE_HIGH_MIN (nearer "decides the mechanism"
# than "on the matter") AND P(same mechanism) >= PRINCIPLE_HIGH_MECH.
PRINCIPLE_KEEP_MIN = 0.5
PRINCIPLE_HIGH_MIN = 2.5
PRINCIPLE_HIGH_MECH = 0.5

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
    *,
    principle_positions: frozenset[int] | set[int] = frozenset(),
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """``(questions, required)``: ``R{p}`` score + ``M{p}`` noul per position, in position order.

    A position in ``principle_positions`` (an official-principle block) gets
    the principle rubric instead of the ruling one; every other position's
    questions are exactly the tested v2 wording.
    """
    rubric = f"{RUBRIC_V2_HEAD}\n\n{RUBRIC_V2_CHANNEL.get(channel, '')}"
    questions: dict[str, dict[str, Any]] = {}
    for p in positions:
        if p in principle_positions:
            header = f"`### [{p}] {PRINCIPLE_HEADER_AR}`"
            questions[f"R{p}"] = {"type": "score", "criteria": PRINCIPLE_LEVELS,
                                  "instructions": f"{PRINCIPLE_RUBRIC}\n\n"
                                                  f"Rate ONLY the official principle whose header is {header}."}
            questions[f"M{p}"] = {"type": "noul",
                                  "instructions": f"{PRINCIPLE_MECH_Q}\n\n"
                                                  f"Answer ONLY for the official principle whose header is {header}.",
                                  "criteria": dict(PRINCIPLE_MECH_CRITERIA)}
            continue
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


def _principle_keeps(
    scores: dict[int, tuple[float, float]],
    principle_positions: frozenset[int] | set[int],
) -> list[CaseKeep]:
    """Official-principle keeps — the owner rule, capped at ``PRINCIPLE_EXTRA_KEEP``.

    Keep unless the decider leans "not relevant" (raw score below
    ``PRINCIPLE_KEEP_MIN``) — the subject verdict is the only drop reason.
    High when the rule decides the mechanism (``PRINCIPLE_HIGH_MIN`` and
    ``PRINCIPLE_HIGH_MECH``). No floor: the keep cut is already the floor.
    Ordered high first, then score × P(mech), ties to the better position.
    """
    kept: list[tuple[bool, float, int]] = []
    for p in principle_positions:
        if p not in scores:
            continue
        s, m = scores[p]
        if s < PRINCIPLE_KEEP_MIN:
            continue
        high = s >= PRINCIPLE_HIGH_MIN and m >= PRINCIPLE_HIGH_MECH
        kept.append((high, s * m, p))
    kept.sort(key=lambda t: (not t[0], -t[1], t[2]))
    return [
        CaseKeep(position=p, relevance="high" if high else "medium",
                 reasoning="", satisfies_axes=[])
        for high, _rank, p in kept[:PRINCIPLE_EXTRA_KEEP]
    ]


def classify(
    scores: dict[int, tuple[float, float]],
    *,
    model: str,
    max_keep: int,
    principle_positions: frozenset[int] | set[int] = frozenset(),
) -> CaseRerankerClassification:
    """Apply D10 to one sub-query's answers. Keeps come back capped and best first.

    Same logic as ``_arm_keeps("d1u_v2", …)``: rank by score × P(mech), ties to
    the better search position; keep at the model's cut; with fewer than
    ``FLOOR_N`` kept, the ``FLOOR_N`` best-ranked with raw score >= ``FLOOR_MIN``
    (an empty floor leaves the keeps as they were); high at the model's high
    cut; then ``max_keep``. Zero keeps is a valid answer (D4). No "not covered"
    signal for case law (D13): always sufficient, no note.

    ``principle_positions`` (official principles, D2) never enter that ruling
    logic — not its ranks, floor or ``max_keep``. They follow
    ``_principle_keeps`` and are appended after the rulings, so the ruling
    keeps are exactly what they would be without them.

    Raises ``ValueError`` for a model with no calibrated thresholds.
    """
    if model not in THRESHOLDS:
        raise ValueError(f"no case-law thresholds for decider {model!r}")
    keep_min, high_min = THRESHOLDS[model]
    rulings = {p: v for p, v in scores.items() if p not in principle_positions}
    rank = {p: s * m for p, (s, m) in rulings.items()}
    order = sorted(rank, key=lambda p: (-rank[p], p))   # tie → better search rank
    kept = [p for p in order if rank[p] >= keep_min]
    if len(kept) < FLOOR_N:   # floor on the raw score: the mechanism product can sink a whole set
        kept = [p for p in order if rulings[p][0] >= FLOOR_MIN][:FLOOR_N] or kept
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
        ] + _principle_keeps(scores, principle_positions),
        summary_note="",
    )
