"""manual_search — identity resolution for the ``simple_search`` searcher.

Plan: ``.claude/plans/simple_search_manual_search.md``. Wire contract:
``.claude/plans/simple_search_family.md`` §12a C2.

*Which document did the user mean?* — that one question, answered against the
whole public corpus, returned as a ranked candidate table the **searcher**
decides on. No reranker agent, no extra LLM call.

WHY THIS IS NOT "JUST A FALLBACK"
---------------------------------
``fetch_article`` (``agents/tool_repository/fetch_article.py``) is the repo's
ONLY identity resolver and it reaches exactly two things: a **regulation** by
title and an **article** by number inside one. For **judgments, services and
circulars there is nothing** — so for those three this module is the *primary
and only* resolution path, not a fallback. That asymmetry drives the per-type
ladder below and must not be flattened.

THE LADDER (§3.1) — forced by MEASURED coverage, re-verified live 2026-08-15
---------------------------------------------------------------------------
``search_index`` (BM25) vs the source tables:

===========  ==============  =============  ============  ==================
data_type    BM25 rows       source rows    BM25 cover    search_topics docs
===========  ==============  =============  ============  ==================
circulars    1,843           1,843          100 %         1,843
regs         1,686           3,951          42.7 %        3,951 + 1,184 ملاحق
judgments    10,000          30,531         32.8 %        **0 — absent**
services     **100**         4,746          **2.1 %**     4,746
article      **0**           51,792         **0 %**       0
===========  ==============  =============  ============  ==================

Three non-negotiable consequences:

* **BM25 can never find a مادة** — there is no ``article`` corpus. Hence the
  §3.3 two-stage: resolve the parent نظام, then key ``articles_v2`` exactly.
* **Services must not lead with BM25** (2.1 %). They lead semantic.
* **Judgments must never touch ``search_topics``** — it holds zero judgments
  (verified: only ``regulation`` / ``appendix`` / ``service`` / ``circular``).

THE CALIBRATION THAT INVERTS THE OBVIOUS APPROACH (§3.2 Gate 2, trap #1)
------------------------------------------------------------------------
Do **not** put an absolute floor on the raw BM25 score the way
``fetch_article`` floors its similarity ratio. BM25 magnitude tracks *query
term rarity*, not match quality. Measured live on this corpus:

===================================  =========  ==========  ========
query                                score      coverage    correct?
===================================  =========  ==========  ========
«نظام العمل»                          1003.14    1.00        yes
«النظام العمل»                        3.14       1.00        yes
«نظام العمل السعودي»                  3.51       0.67        yes
«اصدار سجل تجاري»                     5.46       0.67        yes
«نظام الفساد المالي والإداري» *(absent)*  **14.79**  0.50        **NO**
«تعميم التسجيل العقاري»               8.37       0.33        **NO**
«نظام حماية الفضاء السيبراني» *(absent)*  **12.52**  0.20        **NO**
===================================  =========  ==========  ========

The two **wrong** answers score HIGHER than every **correct** non-exact one.
A score floor would not merely fail — it would invert. **Title-term coverage**
is the transferable quantity; score is not.

WHAT USES THIS MODULE
---------------------
``register_manual_search(searcher_agent)`` — the searcher owns the decision;
this module owns the retrieval and the deterministic gates. All Supabase reads
are sync (house convention) and dispatched through ``asyncio.to_thread``.
``agents/`` never imports ``backend/`` — the BM25 / topic RPCs are called
directly here rather than through ``backend.app.services.search_service``.

Failure contract: **a plain string return, never ``ModelRetry``** (the
``edit_artifact`` house rule — react on the next model turn rather than burn
the tool-retry budget). All user-facing strings are Arabic.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal

from pydantic_ai import Agent, RunContext

# Reuse, do not re-implement: all four are part of fetch_article's public
# surface (``__all__``) except the two ambiguity constants, which are imported
# by name deliberately so the ask-the-user payload has ONE definition across
# both tools rather than a copy that drifts.
from agents.tool_repository.fetch_article import (
    _AMBIGUITY_MARGIN,
    _AMBIGUOUS_LIST_N,
    _fetch_article_content,
    _fetch_reg_candidates_full,
    _fetch_reg_candidates_token,
    _normalize_title,
    HasSupabase,
    STAGE_FULL,
    STAGE_TOKEN,
)

logger = logging.getLogger(__name__)

DataType = Literal["regs", "judgments", "services", "circulars", "article"]

# Async ``str -> 1024-d vector``. Structurally what
# ``agents.utils.embeddings.embed_regulation_query`` is.
EmbedFn = Callable[[str], Awaitable[list[float]]]


# --------------------------------------------------------------------------- #
# Constants — house convention: every cap carries how it was calibrated.
# All figures re-measured live against ``dwgghvxogtwyaxmbgjod`` on 2026-08-15.
# --------------------------------------------------------------------------- #

# ``bm25_search``'s ``p_exact_bonus`` default (verified in ``pg_proc``:
# ``p_exact_bonus numeric DEFAULT 1000.0``). It fires on
# ``luna_normalize_ar(query) = luna_normalize_ar(title)``, and the separation is
# absolute rather than a thumb on the scale — measured «نظام العمل» → 1003.14 vs
# rank-2 3.08 (325×), «نظام المرور» → 1006.41 vs 5.96 (169×), «نظام الشركات» →
# 1004.36. So ``score >= _EXACT_PIN_SCORE`` ⟺ normalized-title equality.
_EXACT_PIN_SCORE = 1000.0

# THE central calibration (§3.2 Gate 2). Correct resolutions measured at
# 1.00 / 1.00 / 0.67 / 0.67; wrong ones at 0.50 / 0.33 / 0.20. 0.60 sits in the
# widest gap. Deliberately applied to COVERAGE, never to the BM25 score — see
# the module docstring's inversion table and trap #1.
#
# The decisive experiment, measured live: two queries that each return exactly
# ONE row, at almost the same score, with OPPOSITE correct answers —
#
#     «نظام العمل التطوعي السعودي»   1 row, score 14.30, coverage 0.75 → resolve
#     «نظام الفساد المالي والإداري»  1 row, score 14.79, coverage 0.50 → not found
#
# A 0.5 % score difference. Any absolute floor on the score sits above both or
# below both and therefore gets one of them wrong; coverage separates them
# cleanly. That pair is why this constant exists and why no score floor does.
_MIN_TITLE_COVERAGE = 0.60

# ``bm25_search(p_candidates)``. The RPC narrows by ``ts_rank_cd`` to this many
# rows and only THEN applies the 1000-point exact bonus, so too small a cut can
# in principle drop a title before its bonus is ever computed (trap #8). The
# plan measured that risk on ONE query; re-measured here across 8 queries
# spanning 3 corpora («نظام العمل», «نظام المرور», «نظام الشركات»,
# «نظام العمل السعودي», «النظام العمل», «نظام المنافسة», «تعميم التسجيل
# العقاري», «اصدار سجل تجاري») at cand = 20 / 50 / 100 / 500: rank-1 title AND
# score identical at every setting. 100 is therefore safe with margin, and it is
# load-bearing for latency — measured EXPLAIN ANALYZE on «نظام العمل»
# (regulation): cand=100 → **377 ms**, cand=500 → **1204 ms** (3.2×).
_BM25_CANDIDATES = 100

# Candidates surfaced to the searcher. v4 shows ``_TOP_N_PER_QUERY = 15`` for
# *relevance*; identity needs far fewer — the answer is one object or an ask.
# 8 leaves headroom over ``_AMBIGUOUS_LIST_N = 3``.
_BM25_LIMIT = 8

# Per-type quota for the ``search_topics`` RPC. Matches reg_compliance_search's
# ``PER_TYPE = 15``; the RPC self-tunes ``hnsw.ef_search`` from ``p_overfetch``.
_TOPICS_PER_TYPE = 15

# ILIKE recall rung breadth. Measured full-table ILIKE is small: «نظام العمل» →
# 9 rows over 3,951 regulations; «سجل تجاري» → 4 over 4,746 services. 50 is
# ~5× the observed worst case and bounds a pathological single-letter pattern.
_ILIKE_CANDIDATE_CAP = 50

# Judgment recall rung (``hybrid_search_cases``) match_count. Small on purpose:
# judgments never resolve on coverage (see _NO_COVERAGE_RESOLVE), so these rows
# exist only to populate an ask-the-user candidate list.
_CASES_MATCH_COUNT = 10

# Per-candidate body budget in the resolved return. Matches v4's
# ``_FLAT_CONTENT_SNIPPET = 1_000``.
_SNIPPET_CHARS = 1_000

# One-line lead shown per candidate in the Gate-3 table. Short by design: the
# table is for PICKING, not reading — the body arrives after the pick.
_LEAD_CHARS = 120

# Shortest query token that may count toward coverage. A 1-character Arabic
# token (a stray «و» / «ب» conjunction) is a substring of almost any title and
# would inflate coverage toward 1.0 for free. Verified a no-op on the seven
# calibration queries above (their shortest token is 3 chars), so it tightens
# the gate without moving the measured floor.
_MIN_TERM_CHARS = 2

# §6 — the searcher's budget, exported so the searcher prompt/agent has ONE
# definition to cite. Not enforced here: this module is stateless per call.
# One broad attempt + one narrowed retry; a third is a different question.
MAX_CALLS_PER_CYCLE = 2

# Ladder rungs that are pure RECALL — they answer "does this title share a word
# with the query", which is a retrieval predicate, not a ranking one. Gate 2
# reads title-term COVERAGE, and a row fetched by one shared word can clear the
# floor without ever being a plausible answer, so these rungs may populate the
# candidate table but may NOT win Gate 2 alone. Gate 1/1b is unaffected: an
# exact-title pin is proof of identity no matter which rung surfaced it, and
# blocking pins here would re-break «نظام الإقامة المميزة» (below).
#
# Measured, eval §3.2: **3 of 3** wins by a ``score == 0.0`` candidate were on
# must-refuse fixtures; the recall rung produced a correct winner ZERO times.
# All three came from the distinctive-token retry specifically — for each of
# them the full-string ILIKE returned no rows at all (verified live 2026-08-16),
# which is why the full-string rung keeps its Gate-2 eligibility and only the
# one-word-in-common retry loses it.
_RECALL_ONLY_RUNGS: frozenset[str] = frozenset({"ilike_token"})

# data_types whose Gate-2 coverage resolution is DISABLED — only an exact-title
# pin (Gate 1/1b) may resolve them; everything else returns candidates for
# ``ask_user``. Judgments qualify because the corpus does not discriminate:
# measured «نزاع تجاري توريد» → 5.06 / 5.04 / 5.04 / 5.03 / 5.01 / 5.00 (top-6
# inside **1.2 %**) and «حكم المحكمة التجارية في نزاع التوريد» → 6.72 / 6.67 /
# 6.57 / 6.55 / 6.50 / 6.50 (**3.3 %**), over near-identical titles of the form
# «نزاع تجاري حول مستحقات توريد …». Naming "the commercial court's supply
# judgment" does not name ONE حكم — there are thousands. Answering with a
# winner there would be a coin flip wearing a confidence note.
_NO_COVERAGE_RESOLVE: frozenset[str] = frozenset({"judgments"})

# --- Schema config — a table/corpus rename is a one-line change here. ---------
_BM25_RPC = "bm25_search"
_TOPICS_RPC = "search_topics"
_CASES_RPC = "hybrid_search_cases"
_SERVICES_TABLE = "services"

# data_type → the ``search_index.corpus`` it ranks in. ``article`` maps to
# ``regulation`` because stage 1 of the two-stage resolves the parent نظام.
_CORPUS_BY_TYPE: dict[str, str] = {
    "regs": "regulation",
    "circulars": "circular",
    "services": "service",
    "judgments": "judgment",
    "article": "regulation",
}

# data_type → the noun used in the AMBIGUOUS: payload. «النظام» for regs/article
# is byte-identical to ``fetch_article._build_ambiguous`` on purpose (§9).
_AMBIGUOUS_NOUN: dict[str, str] = {
    "regs": "النظام",
    "article": "النظام",
    "judgments": "الحكم",
    "services": "الخدمة",
    "circulars": "التعميم",
}

# data_type → the noun used in the not-found string.
_NOT_FOUND_NOUN: dict[str, str] = {
    "regs": "نظام",
    "article": "نظام",
    "judgments": "حكم",
    "services": "خدمة",
    "circulars": "تعميم",
}


# --------------------------------------------------------------------------- #
# Embedding resolution — the answer to plan open question 3.
#
# The searcher's deps are written by a different hand and may or may not carry
# an ``embedding_fn``. Rather than block on that, ``manual_search_core`` takes
# the callable as an optional argument with THREE meaningful states:
#
#   * a callable          → use it (the searcher's, if its deps carry one);
#   * ``USE_HOUSE_EMBEDDER`` (default) → lazily resolve the house Alibaba
#     embedder, the same 1024-d ``text-embedding-v4`` space ``search_topics``
#     was built in. Degrades to None if the key/import is unavailable;
#   * ``None``            → hard-degrade: BM25 + ILIKE only. This is the state
#     the no-embedding tests pin.
#
# What collapses without an embedder: **services lose their PRIMARY rung** and
# fall back to ILIKE over 4,746 rows plus a BM25 corpus holding 100 of them
# (2.1 %) — by far the worst hit. ``regs`` lose only rung ③ (BM25 + ILIKE
# already reach all 3,951 rows). ``circulars`` lose nothing that matters (rung ①
# is 100 %). ``judgments`` lose ``hybrid_search_cases``, leaving BM25's 32.8 %.
# --------------------------------------------------------------------------- #


class _HouseEmbedder:
    """Sentinel type: resolve the house embedder lazily, at call time."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return "USE_HOUSE_EMBEDDER"


USE_HOUSE_EMBEDDER = _HouseEmbedder()


def _resolve_embedder(embedding_fn: EmbedFn | None | _HouseEmbedder) -> EmbedFn | None:
    """Turn the three-state ``embedding_fn`` argument into a callable or None.

    Never raises: a missing ``ALIBABA_API_KEY`` or an import failure degrades to
    ``None`` (BM25 + ILIKE only) rather than taking the whole lookup down.
    """
    if embedding_fn is None:
        return None
    if not isinstance(embedding_fn, _HouseEmbedder):
        return embedding_fn
    try:
        from agents.utils.embeddings import embed_regulation_query

        return embed_regulation_query
    except Exception as exc:  # noqa: BLE001
        logger.warning("manual_search: house embedder unavailable: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Pure layer — coverage. No DB, no I/O.
# --------------------------------------------------------------------------- #


def _query_terms(query: str) -> list[str]:
    """Normalized, de-duplicated query lexemes of at least ``_MIN_TERM_CHARS``.

    Each token goes through ``fetch_article._normalize_title`` — the STRICT
    fold (ة→ه, ى→ي, ؤ→و, ئ→ي, hamza-alef unification, and a dropped leading
    «ال»), which is why «الفساد» and «فساد» count as the same term. That is a
    deliberately deeper fold than SQL's ``luna_normalize_ar``; see ``_pin``.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in (query or "").split():
        term = _normalize_title(raw)
        if len(term) >= _MIN_TERM_CHARS and term not in seen:
            seen.add(term)
            out.append(term)
    return out


def _term_in_title(term: str, norm_title: str) -> bool:
    """Does one normalized query lexeme appear in a normalized title?

    Substring containment, plus one fold the plain test misses: a «و»
    conjunction the two sides attach differently. Arabic writes the conjunction
    joined («والمشتريات»), so a title spelling the same word bare («المشتريات»)
    fails a raw substring test on the user's token even though it is the very
    same word. ``_normalize_title`` re-attaches a DETACHED waw, which fixes the
    corpus side; this fixes the QUERY side, where the waw is a conjunction the
    title has no reason to carry.

    One-directional by construction — it can only ever count a term the plain
    test already missed, so coverage never drops and the measured floor cannot
    move underneath it.
    """
    if term in norm_title:
        return True
    if not term.startswith("و") or len(term) <= 1:
        return False
    stem = _normalize_title(term[1:])
    return len(stem) >= _MIN_TERM_CHARS and stem in norm_title


def coverage(query: str, title: str) -> float:
    """Fraction of the query's lexemes that appear in ``title``. Pure.

    Substring containment on the normalized forms, so «عمل» counts inside
    «العمل». Reproduces every measured value in the module docstring's table
    exactly — 1.00 / 1.00 / 0.67 / 0.67 for the correct resolutions and
    0.50 / 0.33 / 0.20 for the wrong ones.
    """
    terms = _query_terms(query)
    if not terms:
        return 0.0
    norm_title = _normalize_title(title or "")
    if not norm_title:
        return 0.0
    return sum(1 for t in terms if _term_in_title(t, norm_title)) / len(terms)


def _strict_exact(query: str, title: str) -> bool:
    """Gate 1b — the strict-normalized Python probe.

    SQL's ``luna_normalize_ar`` folds hamza-carrying alef, tatweel and harakat
    ONLY; ``_normalize_title`` also folds ة/ى/ؤ/ئ and drops a leading «ال». The
    two disagree on **91.7 %** of regulation titles, so relying on the SQL pin
    alone silently loses most exact matches — «النظام العمل» scores 3.14 on
    نظام العمل (no pin) where «نظام العمل» scores 1003.14. This recovers those.
    """
    q = _normalize_title(query or "")
    return bool(q) and q == _normalize_title(title or "")


# --------------------------------------------------------------------------- #
# Candidate shape. ``manual_search_core`` returns these as plain dicts (the
# §12a C2 pin says ``list[dict]``), so every gate below reads dicts too and the
# whole decision layer is testable off the pinned surface.
#
# Keys: id · title · lead · slug · score · coverage · rung · pin · data_type
# `pin` is True for a Gate-1 (SQL, score >= 1000) or Gate-1b (strict Python)
# exact-title match. `rung` is forensic — which ladder step produced the row.
# --------------------------------------------------------------------------- #


def _candidate(
    *,
    cid: str,
    title: str,
    query: str,
    score: float,
    rung: str,
    data_type: str,
    lead: str = "",
    slug: str = "",
    sql_pin: bool = False,
) -> dict:
    """Build one candidate dict with its coverage and pin flags computed."""
    title = (title or "").strip()
    return {
        "id": str(cid or ""),
        "title": title,
        "lead": " ".join((lead or "").split())[:_LEAD_CHARS],
        "slug": slug or "",
        "score": float(score or 0.0),
        "coverage": coverage(query, title),
        "rung": rung,
        "pin": bool(sql_pin) or _strict_exact(query, title),
        "data_type": data_type,
    }


def rank_candidates(candidates: list[dict]) -> list[dict]:
    """Sort best-first and de-duplicate. Pure.

    Order: pinned first, then **coverage**, then the rung-native score. Coverage
    outranks score deliberately — that is the whole point of Gate 2 (trap #1).
    De-dupes by id, then by normalized title, so the same document arriving from
    two ladder rungs is one candidate rather than a fake plurality (which would
    otherwise defeat the singleton guard).
    """
    out: list[dict] = []
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    for c in sorted(
        candidates,
        key=lambda c: (bool(c.get("pin")), c.get("coverage", 0.0), c.get("score", 0.0)),
        reverse=True,
    ):
        cid = c.get("id") or ""
        norm = _normalize_title(c.get("title") or "")
        if (cid and cid in seen_ids) or (norm and norm in seen_titles):
            continue
        if cid:
            seen_ids.add(cid)
        if norm:
            seen_titles.add(norm)
        out.append(c)
    return out[:_BM25_LIMIT]


# --------------------------------------------------------------------------- #
# The gate ladder (§3.2) — pure. First gate that decides, decides.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Decision:
    """Outcome of the gates over a ranked candidate pool.

    ``status`` ∈ ``resolved`` | ``candidates`` | ``ambiguous`` | ``not_found``.
    ``gate`` is forensic — which rule fired — and is logged with every drop so
    the reranker-forensics tooling sees the same shape it already expects.
    """

    status: str
    gate: str = ""
    winner: dict | None = None
    confidence: str = ""  # "high" | "medium" | ""
    candidates: list[dict] = field(default_factory=list)


def decide(candidates: list[dict], data_type: str) -> Decision:
    """Run gates 1 → 1b → 2 → 3 over an ALREADY-RANKED pool. Pure.

    Gate 1/1b — a UNIQUE exact-title pin resolves outright at ``high``
    confidence with zero LLM involvement. Note "score ≥ 1000" is not "unique":
    10 regulation, 44 circular and **176 judgment** duplicate-normalized-title
    groups exist (verified live), so a tie falls through to the candidate table
    rather than picking arbitrarily.

    Gate 2 — coverage ≥ ``_MIN_TITLE_COVERAGE``, **alone above it**, and found
    by a rung that RANKS (not one in ``_RECALL_ONLY_RUNGS``) resolves at
    ``medium``. Disabled entirely for ``_NO_COVERAGE_RESOLVE`` types.

    Gate 3 — everything else is a genuine "which one did they mean": return the
    table and let the searcher (which holds the user's verbatim request) pick.

    **The singleton guard (trap #2) is the inversion that matters.** A lone
    result is the most DANGEROUS case, not the safest: both absent-law queries
    returned exactly one row, at scores of 14.79 and 12.52. ``fetch_article``'s
    "single candidate above the floor → accept" would confidently return the
    wrong law, and ``n == 1`` also makes any rank-1/rank-2 ratio undefined —
    reading as infinite confidence. So a lone candidate below the floor is
    ``not_found``, never a winner.
    """
    if not candidates:
        return Decision(status="not_found", gate="empty")

    pins = [c for c in candidates if c.get("pin")]
    if len(pins) == 1:
        return Decision(
            status="resolved", gate="pin", winner=pins[0],
            confidence="high", candidates=candidates,
        )
    if len(pins) > 1:
        # Normalized-title collision — deterministic scoring cannot break it.
        return Decision(status="candidates", gate="pin_tie", candidates=pins)

    if data_type in _NO_COVERAGE_RESOLVE:
        above: list[dict] = []
    else:
        above = [c for c in candidates if c.get("coverage", 0.0) >= _MIN_TITLE_COVERAGE]

    if len(above) == 1:
        if above[0].get("rung") not in _RECALL_ONLY_RUNGS:
            return Decision(
                status="resolved", gate="coverage", winner=above[0],
                confidence="medium", candidates=candidates,
            )
        # A recall-only rung cleared the floor and nothing that RANKS did. The
        # row shares one word with the query; that is retrieval, not identity.
        # Show it, never commit to it — this is the whole of eval bug #1, where
        # «نظام الفساد المالي والإداري» (a law that does not exist) resolved onto
        # «الترتيبات التنظيمية…لمكافحة الفساد المالي والإداري» at MEDIUM
        # confidence, complete with a «(ثقة متوسطة …)» note. The searcher holds
        # the user's verbatim wording and can tell; the floor cannot.
        if len(candidates) == 1:
            return Decision(status="not_found", gate="recall_only_singleton")
        return Decision(status="candidates", gate="recall_only", candidates=candidates)
    if len(above) > 1:
        return Decision(status="candidates", gate="coverage_tie", candidates=candidates)

    # Nothing cleared the floor.
    if len(candidates) == 1:
        return Decision(status="not_found", gate="singleton_below_floor")

    top, second = candidates[0], candidates[1]
    gap = top.get("coverage", 0.0) - second.get("coverage", 0.0)
    if gap <= _AMBIGUITY_MARGIN:
        # Margin borrowed from fetch_article:70 but applied to COVERAGE — never
        # to a raw BM25 score, which is not a cross-query quantity (trap #1).
        return Decision(status="ambiguous", gate="margin", candidates=candidates)
    return Decision(status="candidates", gate="below_floor", candidates=candidates)


# --------------------------------------------------------------------------- #
# Ladder rungs — SYNC Supabase reads (house convention). Each returns raw rows
# and NEVER raises: a rung that fails contributes nothing and the ladder walks
# on, which is the difference between a degraded lookup and a dead one.
# --------------------------------------------------------------------------- #


def _bm25(supabase, query: str, corpus: str) -> list[dict]:
    """Rung: ``public.bm25_search()`` over ONE corpus, public rows only.

    ``p_owner=None`` matches ``owner_user_id IS NULL`` — the public corpus. The
    RPC is called directly rather than through ``backend.app.services.
    search_service`` because ``agents/`` never imports ``backend/``; note that
    module additionally drops ``service`` from its PUBLIC_CORPORA (the retired
    /compliance wing), a navigation-surface decision that must NOT leak into
    agent retrieval — the rows are still indexed and still searchable here.
    """
    try:
        resp = supabase.rpc(
            _BM25_RPC,
            {
                "p_corpora": [corpus],
                "p_query": query,
                "p_owner": None,
                "p_facets": {},
                "p_limit": _BM25_LIMIT,
                "p_offset": 0,
                "p_candidates": _BM25_CANDIDATES,
            },
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("manual_search: bm25 %s ~ %r failed: %s", corpus, query, exc)
        return []
    return list(getattr(resp, "data", None) or [])


def _topics(supabase, embedding: list[float], types: list[str]) -> list[dict]:
    """Rung: ``public.search_topics()`` — the 1024-d unified topic space.

    The RPC returns ``title`` directly (verified in ``pg_proc``), so unlike
    reg_compliance_search this needs no per-type content round trip.
    """
    try:
        resp = supabase.rpc(
            _TOPICS_RPC,
            {
                "p_query_embedding": embedding,
                "p_types": types,
                "p_per_type": _TOPICS_PER_TYPE,
            },
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("manual_search: search_topics %s failed: %s", types, exc)
        return []
    return list(getattr(resp, "data", None) or [])


def _ilike_services(supabase, query: str) -> list[dict]:
    """Rung: exact-char ILIKE over ``services.service_name_ar`` (4,746 rows).

    PostgREST ILIKE is exact-char, so the RAW query string is the pattern.
    Measured breadth is tiny — «سجل تجاري» → 4 rows over the full table versus
    2 reachable inside BM25's 100-row service corpus.
    """
    raw = (query or "").strip()
    if not raw:
        return []
    try:
        resp = (
            supabase.table(_SERVICES_TABLE)
            .select("id,service_name_ar,provider_name")
            .ilike("service_name_ar", f"%{raw}%")
            .limit(_ILIKE_CANDIDATE_CAP)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("manual_search: services ILIKE %r failed: %s", raw, exc)
        return []
    return list(getattr(resp, "data", None) or [])


def _hybrid_cases(supabase, query: str, embedding: list[float]) -> list[dict]:
    """Rung: ``public.hybrid_search_cases()`` — the judgments semantic path.

    ``search_topics`` holds ZERO judgments, so this separate RPC is the only
    semantic reach into ``cases``. All EIGHT parameters are passed because the
    function is **overloaded** — a 6-argument call matches both signatures and
    PostgREST cannot choose. Same call shape as ``case_search/search.py:215``.

    The rows carry no title (``cases`` has no title column); a label is built
    from court + case number, and coverage is measured against THAT label only,
    never against ``content`` — the floor was calibrated on titles, and scoring
    a full judgment body would push almost any query to ~1.0.
    """
    try:
        resp = supabase.rpc(
            _CASES_RPC,
            {
                "query_text": query,
                "query_embedding": embedding,
                "match_count": _CASES_MATCH_COUNT,
                "full_text_weight": 1.0,
                "semantic_weight": 1.0,
                "rrf_k": 60,
                "filter_entity_id": None,
                "filter_court_level": None,
            },
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("manual_search: hybrid_search_cases %r failed: %s", query, exc)
        return []
    return list(getattr(resp, "data", None) or [])


# --- row → candidate adapters ------------------------------------------------


def _from_bm25(rows: list[dict], query: str, data_type: str) -> list[dict]:
    return [
        _candidate(
            cid=r.get("content_id") or "",
            title=r.get("title") or "",
            query=query,
            score=float(r.get("score") or 0.0),
            rung="bm25",
            data_type=data_type,
            lead=r.get("lead") or "",
            slug=r.get("slug") or "",
            sql_pin=float(r.get("score") or 0.0) >= _EXACT_PIN_SCORE,
        )
        for r in rows
        if r.get("content_id")
    ]


def _from_topics(rows: list[dict], query: str, data_type: str) -> list[dict]:
    return [
        _candidate(
            cid=r.get("doc_id") or "",
            title=r.get("title") or "",
            query=query,
            score=float(r.get("score") or 0.0),
            rung="topics",
            data_type=data_type,
            lead=r.get("topic_text") or "",
        )
        for r in rows
        if r.get("doc_id")
    ]


def _from_regs_ilike(
    rows: list[dict], query: str, data_type: str, stage: str = ""
) -> list[dict]:
    """``regulations_v2`` rows from ``fetch_article._fetch_reg_candidates_staged``.

    ``stage`` carries WHICH ILIKE pattern found them, and the rung name records
    it: a full-string hit means the title contains the user's whole phrase,
    while ``STAGE_TOKEN`` means nothing did and these rows merely share the
    single most distinctive word. Only the latter is barred from Gate 2 (see
    ``_RECALL_ONLY_RUNGS``) — collapsing the two would also bar the full-string
    hits, which are the ones that carry the truth when BM25 misses a law.
    """
    rung = "ilike_token" if stage == STAGE_TOKEN else "ilike"
    return [
        _candidate(
            cid=r.get("id") or "",
            title=(r.get("clean_title") or r.get("title") or ""),
            query=query,
            score=0.0,  # ILIKE is a RECALL rung — it carries no ranking signal.
            rung=rung,
            data_type=data_type,
        )
        for r in rows
        if r.get("id")
    ]


def _from_services_ilike(rows: list[dict], query: str) -> list[dict]:
    return [
        _candidate(
            cid=r.get("id") or "",
            title=r.get("service_name_ar") or "",
            query=query,
            score=0.0,
            rung="ilike",
            data_type="services",
            lead=r.get("provider_name") or "",
        )
        for r in rows
        if r.get("id")
    ]


def _from_cases(rows: list[dict], query: str) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        if not r.get("id"):
            continue
        label = " - ".join(
            p for p in (r.get("court") or "", r.get("case_number") or "") if p
        ) or (r.get("case_ref") or "")
        out.append(
            _candidate(
                cid=r.get("id") or "",
                title=label,
                query=query,
                score=float(r.get("score") or 0.0),
                rung="cases",
                data_type="judgments",
                lead=r.get("content") or "",
            )
        )
    return out


# --------------------------------------------------------------------------- #
# The ladder (§3.1) — the pinned core.
# --------------------------------------------------------------------------- #


async def manual_search_core(
    supabase,
    query: str,
    data_type: str,
    *,
    embedding_fn: EmbedFn | None | _HouseEmbedder = USE_HOUSE_EMBEDDER,
) -> list[dict]:
    """Walk the per-type ladder and return the RANKED candidate table. Pure-ish.

    The §12a C2 pin: ``(supabase, query, data_type) -> list[dict]``. Keyword-only
    ``embedding_fn`` is additive (see the module's embedding section — it is the
    answer to plan open question 3, decided here rather than blocking on the
    searcher's deps).

    **Rungs are DB round-trips, not searcher cycles** (§6): all of them run
    inside this one call, so the searcher never walks the ladder itself.

    A rung advances to the next unless the pool so far RESOLVES. That rule is
    load-bearing, not conservatism — measured: «نظام المنافسة» returns a
    *wrong* document from BM25 («دليل تعزيز المنافسة في قطاع منصات توصيل
    الطعام», score 7.72, coverage 0.50) because the real نظام is one of the
    2,265 regulations missing from ``search_index``; ILIKE at rung ② finds it.
    Stopping at "rung ① returned a row" would answer that query wrongly.

    **With ONE exception, and it is the mirror image of that argument.** The
    regs ILIKE rung runs unconditionally, because "advance only if not
    resolved" also stops the ladder when rung ① resolves to the WRONG document —
    and the rung it skips is the one holding an exact-title pin on the right
    one (eval §3.3, spelled out at the call site). The gating rule protects
    against a rung-① miss; it cannot protect against a rung-① false positive,
    so the cheap deterministic rung is no longer gated behind it. Rungs that
    cost an embedding call stay gated.

    ``query`` must arrive VERBATIM. A paraphrase destroys the Gate-1 exact pin —
    the same failure mode as ``router_no_describe_query``.
    """
    query = (query or "").strip()
    if not query:
        return []
    if data_type not in _CORPUS_BY_TYPE:
        logger.warning("manual_search: unknown data_type %r", data_type)
        return []

    embedder = _resolve_embedder(embedding_fn)
    pool: list[dict] = []

    async def _embed() -> list[float] | None:
        if embedder is None:
            return None
        try:
            return await embedder(query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("manual_search: embedding failed: %s", exc)
            return None

    def _resolved(p: list[dict]) -> bool:
        return decide(rank_candidates(p), data_type).status == "resolved"

    corpus = _CORPUS_BY_TYPE[data_type]

    if data_type == "services":
        # ① semantic — BM25 reaches 100 of 4,746 services (2.1 %).
        vec = await _embed()
        if vec is not None:
            pool += _from_topics(
                await asyncio.to_thread(_topics, supabase, vec, ["service"]),
                query, "services",
            )
        # ② ILIKE over the full table.
        if not _resolved(pool):
            pool += _from_services_ilike(
                await asyncio.to_thread(_ilike_services, supabase, query), query
            )
        # ③ BM25 last — thin, but it carries the only exact-title pin.
        if not _resolved(pool):
            pool += _from_bm25(
                await asyncio.to_thread(_bm25, supabase, query, corpus),
                query, "services",
            )

    elif data_type == "judgments":
        # ① BM25 (32.8 %). ``search_topics`` is NEVER consulted — zero judgments.
        pool += _from_bm25(
            await asyncio.to_thread(_bm25, supabase, query, corpus), query, "judgments"
        )
        # ② hybrid_search_cases — the only semantic reach into ``cases``.
        if not _resolved(pool):
            vec = await _embed()
            if vec is not None:
                pool += _from_cases(
                    await asyncio.to_thread(_hybrid_cases, supabase, query, vec), query
                )

    elif data_type == "circulars":
        # ① BM25 at 100 % coverage; ② semantic. No third rung is warranted.
        pool += _from_bm25(
            await asyncio.to_thread(_bm25, supabase, query, corpus), query, "circulars"
        )
        if not _resolved(pool):
            vec = await _embed()
            if vec is not None:
                pool += _from_topics(
                    await asyncio.to_thread(_topics, supabase, vec, ["circular"]),
                    query, "circulars",
                )

    else:  # "regs" and stage 1 of "article" share one ladder.
        # ① BM25 owns the exact pin but covers 1,686 of 3,951 (42.7 %).
        pool += _from_bm25(
            await asyncio.to_thread(_bm25, supabase, query, corpus), query, data_type
        )
        # ②a full-string ILIKE reaches all 3,951 rows — reuses fetch_article's
        # candidate fetch. Run UNCONDITIONALLY, unlike every other rung here.
        #
        # "Advance only if the pool does not resolve" is sound when rung ① MISSES
        # and actively harmful when rung ① HITS WRONGLY, and at 42.7 % BM25
        # coverage the second case is systematic. Verified in SQL: «نظام الإقامة
        # المميزة» (e446f5ec…) has **0** rows in ``search_index`` while its لائحة
        # (93489f79…) has 1 — so BM25 can only return the لائحة, which scores
        # coverage 1.00, stands alone above the floor and resolves, one rung
        # before the ILIKE row carrying an exact PIN on the actual نظام. The
        # deterministic leg meanwhile answered «نظام الإقامة المميزة», so the same
        # query returned two different laws depending on which tool the searcher
        # reached for first, with nothing arbitrating (eval §3.3).
        #
        # Merging the pools needs no new tie-break: ``rank_candidates`` already
        # sorts ``pin`` above coverage, so the pinned row wins by construction,
        # and the dedup keeps the higher-scoring copy of a document both rungs
        # return. The cost is one PostgREST round trip on queries that used to
        # stop early — deterministic, no embedding call.
        pool += _from_regs_ilike(
            await asyncio.to_thread(_fetch_reg_candidates_full, supabase, query),
            query, data_type, STAGE_FULL,
        )
        # ②b …but the distinctive-TOKEN retry stays gated, and deliberately so.
        # It is pure recall — «السيبراني» alone returns 29 rows — and running it
        # unconditionally measurably costs true positives: it breaks the
        # singleton on «نظام العمل التطوعي السعودي» (the calibration pair's
        # positive half, BM25 score 14.30 / coverage 0.75) by piling
        # coverage-0.5+ neighbours next to it, turning a correct resolve into a
        # candidate table. The gate that protects rung ② from a rung-① false
        # positive is not needed here: a token row can never resolve anyway.
        if not _resolved(pool):
            pool += _from_regs_ilike(
                await asyncio.to_thread(_fetch_reg_candidates_token, supabase, query),
                query, data_type, STAGE_TOKEN,
            )
        # ③ semantic over regulation + appendix chunks.
        if not _resolved(pool):
            vec = await _embed()
            if vec is not None:
                pool += _from_topics(
                    await asyncio.to_thread(
                        _topics, supabase, vec, ["regulation", "appendix"]
                    ),
                    query, data_type,
                )

    ranked = rank_candidates(pool)
    logger.info(
        "manual_search: q=%r type=%s → %d candidates (rungs=%s embedder=%s)",
        query, data_type, len(ranked),
        sorted({c["rung"] for c in ranked}), embedder is not None,
    )
    return ranked


# --------------------------------------------------------------------------- #
# Rendering (§5.1). All Arabic. Plain strings only.
# --------------------------------------------------------------------------- #


def _build_ambiguous(candidates: list[dict], data_type: str) -> str:
    """Render the ``AMBIGUOUS:`` payload — mirrors ``fetch_article``'s wording.

    For ``regs`` / ``article`` this is BYTE-IDENTICAL to
    ``fetch_article._build_ambiguous`` (same noun, same separators, same
    ``_AMBIGUOUS_LIST_N``), so the searcher's ask-the-user payload does not
    change shape depending on which tool produced it.
    """
    titles: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        t = c.get("title") or ""
        if t and t not in seen:
            seen.add(t)
            titles.append(t)
        if len(titles) >= _AMBIGUOUS_LIST_N:
            break
    noun = _AMBIGUOUS_NOUN.get(data_type, "العنصر")
    # fetch_article's tail names the نظام explicitly; keep that exact wording
    # for regs/article so the payload stays byte-identical, and use a generic
    # pronoun for the three types fetch_article never handled.
    tail = "أيّ نظام يقصد" if data_type in ("regs", "article") else "أيّهم يقصد"
    return (
        f"AMBIGUOUS: تعذّر تحديد {noun} المقصود بدقة. "
        f"المرشحون المحتملون: {'، '.join(titles)}. "
        f"اسأل المستخدم {tail} قبل المتابعة."
    )


def _render_candidates(candidates: list[dict]) -> str:
    """Render the Gate-3 table with stable ``C1…Cn`` labels.

    Labels are minted by CODE, never renumbered and never a UUID — the v4
    reranker convention (``reg_compliance_search/reranker.py:477``). Every
    candidate is shown, not a keep-subset: for a one-of-N identity pick the
    rejected rows ARE the evidence the searcher reasons over.
    """
    lines = ["المرشحون:"]
    for i, c in enumerate(candidates, start=1):
        bits = [f"C{i} · {c.get('title') or '—'}"]
        if c.get("lead"):
            bits.append(str(c["lead"]))
        lines.append(" — ".join(bits))
    lines.append(
        "اختر المرشّح المطابق لما ذكره المستخدم ثم أعد الاستدعاء باسمه الكامل، "
        "أو اسأل المستخدم إن لم يكن أيٌّ منها مطابقًا."
    )
    return "\n".join(lines)


def _render_resolved(winner: dict, confidence: str) -> str:
    """``## <title>`` + the lead, plus fetch_article's «(ثقة متوسطة …)» note."""
    title = winner.get("title") or "—"
    body = (winner.get("lead") or "").strip()[:_SNIPPET_CHARS]
    text = f"## {title}" + (f"\n\n{body}" if body else "")
    if confidence == "medium":
        text += (
            f"\n\n— (ثقة متوسطة: «{title}» هو أقرب نتيجة مطابقة للاسم المذكور، "
            "وليس تطابقًا تامًّا؛ تأكّد أنه المقصود قبل اعتماده.)"
        )
    return text


def _not_found(query: str, data_type: str) -> str:
    """Names the USER's own string, never a resolved one (§5.1)."""
    noun = _NOT_FOUND_NOUN.get(data_type, "عنصر")
    return f"لم يتم العثور على {noun} بهذا الاسم: «{(query or '').strip()}»"


# --------------------------------------------------------------------------- #
# Result layer — ladder → gates → string.
# --------------------------------------------------------------------------- #


async def manual_search_result(
    supabase,
    query: str,
    data_type: str,
    *,
    article_number: str = "",
    embedding_fn: EmbedFn | None | _HouseEmbedder = USE_HOUSE_EMBEDDER,
) -> str:
    """Full lookup → the plain string the searcher reads. Never raises.

    ``article`` runs the §3.3 two-stage: stage 1 resolves the parent نظام
    through the ``regs`` ladder, stage 2 keys ``articles_v2`` by exact
    ``(regulation_id, article_number)`` text equality — literally
    ``fetch_article._fetch_article_content``, reused unchanged. Only **1,806 of
    3,951** regulations (45.7 %) have article rows at all, so a resolved نظام
    with no articles is a NORMAL outcome and gets its own distinct message so
    the searcher stops retrying.
    """
    candidates = await manual_search_core(
        supabase, query, data_type, embedding_fn=embedding_fn
    )
    decision = decide(candidates, data_type)

    # Forensic drop record — every candidate with its score, coverage and the
    # gate that eliminated it (the shape reranker-forensics already expects).
    logger.info(
        "manual_search: q=%r type=%s → %s via %s | %s",
        query, data_type, decision.status, decision.gate,
        [
            {"t": c.get("title"), "score": round(c.get("score", 0.0), 2),
             "cov": round(c.get("coverage", 0.0), 2), "rung": c.get("rung")}
            for c in candidates
        ],
    )

    if decision.status == "ambiguous":
        return _build_ambiguous(decision.candidates, data_type)
    if decision.status == "candidates":
        return _render_candidates(decision.candidates)

    if data_type != "article":
        if decision.status == "resolved" and decision.winner:
            return _render_resolved(decision.winner, decision.confidence)
        return _not_found(query, data_type)

    # --- article, stage 2 ----------------------------------------------------
    num = (article_number or "").strip()
    if decision.status != "resolved" or not decision.winner:
        # Name the USER's own string — the resolver never got far enough to
        # have a better one. Mirrors fetch_article's not-found wording.
        return f"المادة {num} غير موجودة في {(query or '').strip()}"

    reg_id = decision.winner.get("id") or ""
    reg_name = decision.winner.get("title") or (query or "").strip()
    content = await asyncio.to_thread(_fetch_article_content, supabase, reg_id, num)
    if content:
        text = f"## نص المادة {num} من {reg_name}\n\n{content.strip()}"
        if decision.confidence == "medium":
            text += (
                f"\n\n— (ثقة متوسطة: «{reg_name}» هو أقرب نظام مطابق للاسم "
                "المذكور، وليس تطابقًا تامًّا؛ تأكّد أنه النظام المقصود قبل اعتماده.)"
            )
        return text

    # Distinguish "this نظام has NO articles indexed at all" from "that one
    # article is missing" — otherwise the searcher retries a lookup that can
    # never succeed (2,145 of 3,951 regulations have zero articles_v2 rows).
    any_article = await asyncio.to_thread(_regulation_has_articles, supabase, reg_id)
    if not any_article:
        return f"{reg_name} غير مفهرس على مستوى المواد"
    return f"المادة {num} غير موجودة في {reg_name}"


def _regulation_has_articles(supabase, regulation_id: str) -> bool:
    """True when ``articles_v2`` holds ANY row for this regulation. Never raises."""
    if not regulation_id:
        return False
    try:
        resp = (
            supabase.table("articles_v2")
            .select("article_number")
            .eq("regulation_id", regulation_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("manual_search: article-index probe failed: %s", exc)
        return False
    return bool(getattr(resp, "data", None))


# --------------------------------------------------------------------------- #
# Pydantic AI tool.
# --------------------------------------------------------------------------- #


def register_manual_search(agent: Agent) -> None:
    """Register the ``manual_search`` tool on the ``simple_search`` searcher.

    The agent's deps must structurally satisfy :class:`HasSupabase`
    (``.supabase``). If the deps ALSO carry a callable ``.embedding_fn`` it is
    used for the semantic rungs; otherwise the house Alibaba embedder is
    resolved lazily. Neither is required — the tool degrades to BM25 + ILIKE.
    """

    @agent.tool
    async def manual_search(  # noqa: RUF029 — supabase client is sync by design
        ctx: RunContext[HasSupabase],
        query: str,
        data_type: Literal["regs", "judgments", "services", "circulars", "article"],
        article_number: str = "",
    ) -> str:
        """Find ONE legal document when you don't already have its id.

        Use this to turn the user's phrasing into a specific document. For
        **judgments, services and circulars this is the only lookup that
        exists** — reach for it first. For **regulations and articles**, use it
        when ``fetch_article`` found nothing, returned ``AMBIGUOUS:``, or the
        user named the law too loosely for an exact title.

        Pass ``query`` EXACTLY as the user wrote it — do not paraphrase,
        translate, expand or "clean up" the name. The search has an exact-title
        match that only fires on the user's own wording; rewriting the string
        destroys it and turns a certain answer into a guess.

        Returns:
            - ``## <title>`` plus a short lead when one document was
              identified (with a «(ثقة متوسطة …)» note when the match was
              approximate — verify it's the right one before relying on it).
            - ``المرشحون:`` followed by a ``C1…Cn`` list when several documents
              could be meant. Pick the one matching what the user said and call
              again with its full title, or ask the user.
            - A string starting ``AMBIGUOUS:`` when nothing is close enough to
              choose between — use ``ask_user``.
            - ``لم يتم العثور على …`` when nothing matched. Don't retry the same
              wording; ask the user or say you couldn't find it.
            - For ``data_type="article"``: the article body, or «المادة N غير
              موجودة في …», or «… غير مفهرس على مستوى المواد» — the last one
              means that law has no articles indexed, so stop retrying it.

        Args:
            query: The document as the user named it, VERBATIM.
            data_type: Which corpus to look in.
            article_number: Required only when ``data_type="article"`` — the
                plain string form ("81", "1-1"). Convert Arabic-Indic digits
                («٨١») or Arabic ordinals («الحادية والثمانون») first.
        """
        dep_fn = getattr(ctx.deps, "embedding_fn", None)
        embedder: EmbedFn | None | _HouseEmbedder = (
            dep_fn if callable(dep_fn) else USE_HOUSE_EMBEDDER
        )
        try:
            return await manual_search_result(
                ctx.deps.supabase, query, data_type,
                article_number=article_number, embedding_fn=embedder,
            )
        except Exception as exc:  # noqa: BLE001
            # Degrade to the not-found string. NEVER ModelRetry — the searcher
            # reacts on its next turn instead of burning the retry budget.
            logger.warning(
                "manual_search error for q=%r type=%r: %s", query, data_type, exc
            )
            if data_type == "article":
                return (
                    f"المادة {(article_number or '').strip()} غير موجودة في "
                    f"{(query or '').strip()}"
                )
            return _not_found(query, data_type)


__all__ = [
    "register_manual_search",
    "manual_search_core",
    "manual_search_result",
    "decide",
    "rank_candidates",
    "coverage",
    "Decision",
    "DataType",
    "EmbedFn",
    "USE_HOUSE_EMBEDDER",
    "MAX_CALLS_PER_CYCLE",
]
