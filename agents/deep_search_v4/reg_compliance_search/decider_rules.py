"""Phase A decider rules — the regulations/compliance reranker on a decision model.

Plan: ``.claude/plans/reranker_decider_engine.md`` (build step 3; D3, D4, D9).

The request is the tested A/B shape (``scripts/reranker_decisions_ab.py``,
``--shape batch --levels 5``): ONE request per sub-query (D8) whose ``state``
is the whole reranker user message, with one 5-level ``score`` question per
``[Cn]`` label. The engine (``agents/deep_search_v4/shared/decider.py``) sends
it; this module only builds the questions and turns the answers into the
``RegRerankerClassification`` the reranker's existing post-processing expects.

A ``score`` answer is the expected level index (0..4). Keep rules (D9, the same
for every decider): keep if score >= 1.5, ``high`` if score >= 2.5, otherwise
``medium``. Zero keeps is a valid answer (D4) — there is no reconsider retry.

Official judicial principles (``official_principles_refs.md`` §3.3) carry no
النظام / نطاق النظام and no الجهة, so the tested SCOPE-FIRST rubric has nothing
to read for them. Their questions alone get :data:`PRINCIPLE_CLAUSE` (judge the
rule's subject instead); every other question stays the A/B's verbatim wording,
and a batch with no principle in it is byte-identical to the tested request.
"""
from __future__ import annotations

from typing import Any

from .models import RegKeep, RegRerankerClassification

# -- Tested wording (verbatim) ---------------------------------------------------
#
# Copied verbatim from scripts/reranker_decisions_ab.py (RUBRIC5 / LEVELS5) — the
# instructions the d1-ultra A/B ran with. Any edit here invalidates that A/B and
# the D9 thresholds; re-run the parity check first.

RUBRIC5 = (
    "Rayhan legal AI (Saudi law). Judge a search candidate against the SUB-QUERY for the aggregator that "
    "writes the final legal analysis. SCOPE FIRST: does the parent law's scope (النظام / نطاق النظام) — or "
    "for a circular/service the entity (الجهة) — actually govern the sub-query's situation? Off-scope = "
    "not relevant, however closely the words match (Saudi law has many parallel laws with near-identical "
    "penalty/definition chunks). A REPEALED law (حالة النظام: ملغي) is never above low. Ask: would a lawyer "
    "answering THIS sub-query actually cite this chunk? Right topic is not enough — a chunk that only shares "
    "keywords or the regulation name, but would not be cited, is low. درجة الصلة is a retrieval rank, not relevance."
)
LEVELS5 = [
    "not relevant — off-scope: wrong sector/entity/law, repealed text, or a different subject",
    "low — right topic or right regulation, but nothing the answer would cite",
    "medium — supporting material the answer would actually cite: a definition, procedure, or cross-reference it relies on",
    "high — directly answers part of the sub-query: a condition, exception, penalty, deadline or obligation asked about",
    "critical — the governing operative rule that directly decides exactly what the sub-query asks",
]

# -- Official judicial principle (NOT part of the tested wording) ---------------
#
# Added on top of RUBRIC5, only to the question of a ``### [Cn] مبدأ قضائي رسمي``
# candidate. Same spirit as the qwen prompt's Kind 4: subject in place of scope,
# kept on subject, top levels only when it decides the mechanism.

PRINCIPLE_CLAUSE = (
    "This candidate is an OFFICIAL JUDICIAL PRINCIPLE (مبدأ قضائي رسمي): an authoritative rule distilled by "
    "the highest judicial body. It has no النظام / نطاق النظام and no الجهة — judge SCOPE by its SUBJECT "
    "instead: its الباب and what the rule itself governs, against the sub-query's matter. Off-subject = not "
    "relevant, however closely the words match. On-subject = at least medium (the answer would cite it); "
    "high or critical only when the rule decides the very mechanism the sub-query asks about."
)

# -- Keep rules (D9) -------------------------------------------------------------

KEEP_MIN = 1.5
HIGH_MIN = 2.5

# -- "Not covered" signal (D3) ---------------------------------------------------
#
# OFF until validation step A2 (agreement with qwen's ``sufficient`` >= 75%, a
# "not covered" rate within 0.5-2x qwen's) has run on live calls. Off = the
# request is exactly what the A/B tested: no extra question, ``sufficient=True``,
# empty ``summary_note``.
ASK_SUFFICIENT = False

SUFFICIENT_KEY = "__sufficient"
SUFFICIENT_QUESTION: dict[str, Any] = {
    "type": "noul",
    "instructions": (
        "Rayhan legal AI (Saudi law). Taken together, do the search candidates above cover at least 80% of "
        "what a lawyer needs to answer THIS sub-query: the governing rule on the exact mechanism asked, under "
        "a law (or for a circular/service an entity) whose scope actually governs the sub-query's situation? "
        "Candidates that only share keywords, the regulation name, or come from an off-scope or repealed law "
        "do not count."
    ),
    "criteria": {
        "true": "covered — the candidates hold what is needed to answer the sub-query",
        "false": "not covered — the governing rule or a main part of the sub-query is missing",
    },
}
# Fixed note rendered to the aggregator (``<note>``) when ``sufficient`` is False.
NOT_COVERED_NOTE = "لم تغطِّ نتائج البحث هذا الاستعلام الفرعي تغطيةً كافية؛ قد يكون الحكم الحاكم غير موجود فيها."


def _score_question(label: str, *, principle: bool = False) -> dict[str, Any]:
    rubric = f"{RUBRIC5}\n\n{PRINCIPLE_CLAUSE}" if principle else RUBRIC5
    return {
        "type": "score",
        "instructions": f"{rubric}\n\nRate ONLY the candidate whose header is `### [{label}]`.",
        "criteria": LEVELS5,
    }


def build_questions(
    labels: list[str],
    principle_labels: frozenset[str] | set[str] = frozenset(),
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """``(questions, required)`` for one sub-query, in ``labels`` order.

    ``labels`` are the block labels in render order (``C1``…``Cn``) — the order
    their ``### [Cn]`` headers appear in the state. Every question is required:
    a candidate left unscored is a decider failure (D2), not a drop.
    ``principle_labels`` are the official-principle candidates among them; only
    their questions carry :data:`PRINCIPLE_CLAUSE`.
    """
    questions = {
        lab: _score_question(lab, principle=lab in principle_labels) for lab in labels
    }
    if ASK_SUFFICIENT:
        questions[SUFFICIENT_KEY] = SUFFICIENT_QUESTION
    return questions, list(questions)


def label_scores(answers: dict[str, dict[str, Any]], labels: list[str]) -> dict[str, float]:
    """``label -> score`` (expected level 0..4). Raises ``ValueError`` on a missing score."""
    scores: dict[str, float] = {}
    for lab in labels:
        score = (answers.get(lab) or {}).get("score")
        if score is None:
            raise ValueError(f"decider answer missing score for {lab}")
        scores[lab] = float(score)
    return scores


def sufficient_p(answers: dict[str, dict[str, Any]]) -> float | None:
    """P(covered) from the D3 question, or ``None`` when it wasn't asked."""
    if not ASK_SUFFICIENT:
        return None
    p = (answers.get(SUFFICIENT_KEY) or {}).get("noul")
    if p is None:
        raise ValueError(f"decider answer missing {SUFFICIENT_KEY}")
    return float(p)


def classify(answers: dict[str, dict[str, Any]], labels: list[str]) -> RegRerankerClassification:
    """Apply D9 (+ D3 when on) to one sub-query's decider answers.

    One ``RegKeep`` per kept label, in ``labels`` order; the reranker's cap sort
    re-orders them by ``(tier, -score, -rrf)``. ``reasoning`` is empty — decision
    models give no per-keep text; the scores are logged on ``round_trace``.
    """
    scores = label_scores(answers, labels)
    keeps = [
        RegKeep(label=lab, relevance="high" if s >= HIGH_MIN else "medium", reasoning="")
        for lab, s in scores.items()
        if s >= KEEP_MIN
    ]
    p = sufficient_p(answers)
    sufficient = True if p is None else p >= 0.5
    return RegRerankerClassification(
        sufficient=sufficient,
        keeps=keeps,
        summary_note="" if sufficient else NOT_COVERED_NOTE,
    )
