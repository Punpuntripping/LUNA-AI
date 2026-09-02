"""fetch_article — deterministic lookup of ONE article (مادة) by number.

A Pydantic AI **planner tool** that fetches the verbatim body of a single
article from a **named regulation**, *before* the planner decides the search,
so the planner can fold that text into ``planner_brief`` (the facts channel
that reaches the executors and the aggregator).

Why this exists: semantic search cannot reliably retrieve an article by its
*number* — the corpus writes article numbers as Arabic words inside the prose
("المادة الحادية والثمانون"), not the digit "81". A user asking about «المادة
81 من نظام العمل» can run the whole pipeline and still get an answer whose own
gaps say *"لم تتضمن المراجع النص الحرفي للمادة 81"*. The fix is a deterministic
structured lookup against the article-grain table ``articles_v2``.

Two deterministic steps (see FETCH_ARTICLE_PLAN.md §4, and
``.claude/plans/fetch_article_bm25_resolution.md`` for the 2026-09-02 rewrite):

1. **Resolve ``regulation_title`` → ``regulation_id``** — the only fuzzy part.
   A three-rung ladder — ``bm25_search`` over the ``regulation`` corpus, the
   full-string ILIKE, and a recall-only distinctive-token ILIKE — merged and
   ranked by ``(pin, coverage, rung score)``. **A unique exact title pin is the
   only thing that resolves.** Everything else returns a shortlist for the
   planner to pick from or decline; nothing is ever accepted on similarity
   alone. See :func:`resolve_regulation_id`.
2. **Fetch the article** — ``articles_v2.content`` keyed by
   ``(regulation_id, article_number:text)``. ``article_number`` is matched by
   exact text equality (compound values like ``"1-1"`` exist). Returns the
   article body as TEXT only — never an ``[n]`` citation, never ``article_ref``
   or ``chunk_parent_id``.

**Two audiences, one fetch.** ``FetchArticleResult.text`` is what the planner
reads and is capped at :data:`_PLANNER_ARTICLE_CAP`; ``.content`` is the whole
body and is what gets pinned and what reaches the executors and the aggregator
through the ``statute_articles`` context block. The cap exists because article
length has a brutal tail — p50 325 chars, max 244,419 — and the planner is the
one reader that only needs enough to plan.

The resolver / normalizer / fetch layers are split out as pure functions so
they unit-test against a fake Supabase without an agent or a live DB. Mirrors
the structure of ``unfold_workspace_item`` (plain-string return, registered on
the decider only, sync PostgREST calls wrapped in ``asyncio.to_thread``).

Registration::

    from agents.tool_repository.fetch_article import register_fetch_article
    register_fetch_article(agent)   # deps must expose .supabase

The deps object must structurally satisfy :class:`HasSupabase` (``PlannerDeps``
already does, via ``.supabase``).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic_ai import Agent, RunContext

# Arabic-Indic → ASCII digits. Already existed for the PDPL masking codec and
# was imported by neither resolver, which is why «٨١» missed (eval §4.3). Pure
# stdlib underneath — no I/O, no config, no circular import back into agents/.
from shared.privacy.codec import normalize_digits

logger = logging.getLogger(__name__)


# --- Schema config: a table/column rename is a one-line change here. ----------
_REGS_TABLE = "regulations_v2"
_ARTICLES_TABLE = "articles_v2"

# Candidate-fetch select — one row per regulation, the cols the resolver ranks.
_REG_COLUMNS = "id,title,clean_title,doc_type_bucket,status_class"
# Article fetch — content is the ONLY column we need (never article_ref).
_ARTICLE_COLUMNS = "content"

# Ambiguity gate margin. NO LONGER USED BY THIS MODULE — ``resolve_regulation_id``
# stopped gating on a similarity margin when it moved to the BM25 ladder (below):
# below the exact pin nothing resolves at all, so there is no "close call" left to
# adjudicate. Kept because ``manual_search`` imports it BY NAME so the two tools
# share ONE definition of the margin rather than a copy that drifts.
_AMBIGUITY_MARGIN = 0.1
# How many candidate titles to surface in the shortlist payload.
_AMBIGUOUS_LIST_N = 3

# --- BM25 resolution ladder ---------------------------------------------------
# Rung ① of the title resolver. Ported from ``simple_search/manual_search.py``,
# whose calibration this module now shares; see FETCH_ARTICLE_BM25 plan §2–3
# (.claude/plans/fetch_article_bm25_resolution.md).
_BM25_RPC = "bm25_search"
# ``search_index.corpus`` holding regulations. ``articles`` has NO corpus (0 rows
# indexed of 52,012), which is precisely why resolution is two-stage: BM25 can
# never reach a مادة, so it resolves the parent نظام and ``articles_v2`` is keyed
# exactly.
_BM25_CORPUS = "regulation"
# ``bm25_search``'s ``p_exact_bonus`` default (``pg_proc``: numeric DEFAULT
# 1000.0). It fires on ``luna_normalize_ar(query) = luna_normalize_ar(title)``,
# and the separation is absolute rather than a thumb on the scale. Measured live
# 2026-09-02 on the two articles this ladder was built for:
#   «نظام المرافعات الشرعية» → 1016.59 vs rank-2 11.41   (89×)
#   «نظام الإيجار التمويلي»  → 1014.19 vs rank-2 13.40   (76×)
# The second is the whole point: ``luna_normalize_ar`` folds the hamza, so the
# corpus's bare-alef «نظام الايجار التمويلي» pins on a hamza query — the exact
# match the old exact-char ILIKE could not see (it returned the لائحة instead,
# silently, with a different مادة 22).
_EXACT_PIN_SCORE = 1000.0
# Rows the RPC returns. Identity needs few — the answer is one نظام or a
# shortlist. 8 leaves headroom over ``_SHORTLIST_N``.
_BM25_LIMIT = 8
# ``bm25_search(p_candidates)``: the RPC narrows by ``ts_rank_cd`` and only THEN
# applies the 1000-point bonus, so too small a cut can drop a title before its
# pin is ever computed. 100 measured safe across 8 queries spanning 3 corpora
# (manual_search trap #8); 500 costs 3.2× the latency for no rank change.
_BM25_CANDIDATES = 100

# Shortlist admission floor, applied to title-term COVERAGE — never to the raw
# BM25 score. That inversion is the single most important calibration carried
# over from ``manual_search``: BM25 magnitude tracks query-term RARITY, not match
# quality, and measured on this corpus the two WRONG answers scored 14.79 and
# 12.52 while every correct non-exact one scored 3.14–5.46. A score floor sits
# above both or below both and therefore gets one of them wrong.
#
# The value is the brief's (0.85) and is deliberately STRICTER than
# ``manual_search._MIN_TITLE_COVERAGE = 0.60``, which was calibrated at the widest
# gap between correct resolutions (1.00 / 1.00 / 0.75 / 0.67) and wrong ones
# (0.50 / 0.33 / 0.20). The trade is explicit: at 0.85 a correct near-miss like
# «نظام العمل السعودي» (coverage 0.67) is not shown at all, so the planner sees an
# empty list and falls through to the normal search. That can never produce a
# WRONG article — it can only miss a right one — which is the safe side of this
# particular fence, because a wrong article body reads exactly like a right one.
# A one-line change to 0.60 buys the recall back; see the plan §3A.
_SHORTLIST_MIN_COVERAGE = 0.85
# Candidates shown to the planner when nothing pins. Matches _AMBIGUOUS_LIST_N.
_SHORTLIST_N = _AMBIGUOUS_LIST_N

# --- The planner-side cap -----------------------------------------------------
# Ceiling on the article text handed to the PLANNER, and to the planner only.
# ``FetchArticleResult`` already separated the two audiences — ``text`` is what
# the model sees, ``content`` is the verbatim body that gets pinned — so the cap
# lands on that seam and nowhere else: the downstream ``statute_articles`` context
# block and the ``statute_package`` workspace item both carry ``content``, whole.
#
# Calibrated live 2026-09-02 over all 52,012 rows of ``articles_v2``:
#   p50 325 · p90 1,332 · p95 2,008 · p99 5,088 · max 244,419
#   >4,000 chars: 768 rows (1.48 %) · >8,000: 270 (0.52 %) · >20,000: 86 (0.17 %)
# 4,000 passes 98.5 % of articles through untouched and trims only the
# pathological tail. The tail is real, not theoretical: a live statute_package
# row (conversation 90cd5a8d…, 2026-08-09) is 244,519 chars — ONE article of
# اللائحة التنفيذية لنظام ضريبة القيمة المضافة that went whole into a decider
# context window.
_PLANNER_ARTICLE_CAP = 4_000
# Appended when the cap bites. Load-bearing, not decoration: without it the
# planner retypes a fragment into ``planner_brief`` as though it were the whole
# rule. It also tells the planner the truth — the full text DID reach the search.
_CAP_MARKER = (
    "\n\n… [اقتُطع نص المادة هنا للتخطيط فقط — "
    "النص الكامل وصل إلى البحث والتحرير كاملًا]"
)

# --- Ladder rung names --------------------------------------------------------
# Forensic, and load-bearing for the recall bar below.
RUNG_BM25 = "bm25"

STAGE_FULL = "full"
STAGE_TOKEN = "token"

# Rungs that are pure RECALL — they answer "does this title share a word with the
# query", a retrieval predicate, not a ranking one. A row fetched by one shared
# word can clear a coverage floor without ever being a plausible answer, so these
# rungs may POPULATE the shortlist but may never be its sole evidence. Measured in
# manual_search's eval: 3 of 3 wins by a ``score == 0.0`` candidate were on
# must-refuse fixtures; the token retry produced a correct winner ZERO times. The
# pin gate is unaffected — an exact-title match is proof of identity no matter
# which rung surfaced it.
_RECALL_ONLY_RUNGS: frozenset[str] = frozenset({STAGE_TOKEN})

# (``_RECALL_ONLY_RUNGS`` is defined next to STAGE_TOKEN, which it names.)

# doc_type_bucket labels — used to name a candidate's type in the shortlist so
# the planner can tell a نظام from its لائحة at a glance. No longer a scoring
# nudge: ranking is (pin, coverage, rung score), and the bucket earned its
# keep as INFORMATION rather than as a thumb on the scale.
_BUCKET_LAW = "law_statute"
_BUCKET_EXEC = "executive_regulation"
_BUCKET_LABELS: dict[str, str] = {
    _BUCKET_LAW: "نظام",
    _BUCKET_EXEC: "لائحة تنفيذية",
    "controls": "ضوابط",
    "rules": "قواعد",
    "instructions": "تعليمات",
    "guide": "دليل",
    "policy": "سياسة",
    "organizational_framework": "إطار تنظيمي",
    "regulation_generic": "لائحة",
}

# Shortest query token that may count toward coverage. A 1-character Arabic token
# (a stray «و» / «ب» conjunction) is a substring of almost any title and would
# inflate coverage toward 1.0 for free.
_MIN_TERM_CHARS = 2

# --- Statute-package pin config — a lean reuse of save_memo's persistence -------
# Successful fetches in a turn accumulate on ``deps._fetched_articles`` and are
# flushed into ONE durable workspace item per search (``flush_statute_package``)
# so they survive conversation compaction, re-load as a summary, and unfold on
# demand. Distinct from save_memo's USER-message memo: this is corpus text, so it
# carries its own ``subtype='statute_package'`` and marker — never the «رسالة
# أساسية من المستخدم» memo identity. Lean: a fire-and-forget insert (no router
# force-attach/chip sinks, which PlannerDeps doesn't carry) — the articles
# already ground THIS turn via planner_brief; the pin is for FUTURE turns.
_STATUTE_KIND = "note"
_STATUTE_PACKAGE_SUBTYPE = "statute_package"
_STATUTE_CREATED_BY = "agent"
_STATUTE_PACKAGE_MARKER = "> 📌 نصوص المواد المثبّتة من البحث"


# --------------------------------------------------------------------------- #
# Deps contract — leaner than HasWorkspaceContext: fetch_article only needs the
# supabase client (no wi_alias_map, no user_id scoping — the corpus is public).
# --------------------------------------------------------------------------- #


@runtime_checkable
class HasSupabase(Protocol):
    """Structural deps contract for the tool.

    ``.supabase`` is the only hard requirement (the corpus read). Pinning a
    successful fetch additionally uses ``.user_id`` / ``.conversation_id`` (to
    scope the workspace item) and ``.emit_sse`` (an optional chip) — all read
    via ``getattr`` and skipped when absent, so the tool degrades to a pure
    fetch on minimal deps. ``PlannerDeps`` carries all four. Kept loose
    (``object``) to avoid a hard import of the supabase client here.
    """

    supabase: object


# --------------------------------------------------------------------------- #
# Arabic title normalization — pure, unit-testable.
# --------------------------------------------------------------------------- #

# Arabic combining diacritics (tashkeel) — fatha/damma/kasra/shadda/sukun/
# tanween + the dagger alef and superscript marks. Stripped before comparison.
_TASHKEEL_RE = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ࣓-ࣿ]")
_TATWEEL = "ـ"  # kashida / tatweel — decorative letter-stretch, dropped.
_WS_RE = re.compile(r"\s+")
# A leading definite article «ال» that prefixes the whole title.
_LEADING_AL_RE = re.compile(r"^ال")
# A DETACHED «و» conjunction — «نظام المنافسات و المشتريات» — which Arabic
# orthography always writes attached («والمشتريات»). The ``\b`` keeps it from
# firing on a word that merely ENDS in waw («ذو المال»): both sides of that
# pair are word characters, so there is no boundary to match.
_DETACHED_WAW_RE = re.compile(r"\bو\s+")
# The same conjunction the other way round — used ONLY to build an ILIKE
# pattern that matches a corpus row spelling it detached. Never used to
# normalize, where it would split «وزارة» into a word that means nothing.
_ATTACHED_WAW_RE = re.compile(r"\bو(?=\S)")


def _normalize_title(text: str) -> str:
    """Normalize an Arabic regulation title for comparison.

    Strips tashkeel + tatweel, unifies alef forms (أ/إ/آ/ٱ → ا), ة → ه,
    ى → ي, ؤ → و, ئ → ي, collapses whitespace, **re-attaches a detached «و»
    conjunction**, and drops a single leading «ال». Returns the normalized
    lowercase string (lowercasing is a no-op on Arabic letters but harmlessly
    normalizes any embedded Latin).

    The waw fold is not cosmetic. The corpus title of the flagship procurement
    law is «نظام المنافسات و المشتريات الحكومية» — with a space no user can
    see or type. Without the fold, «نظام المنافسات والمشتريات الحكومية» is not
    an exact match, so neither this resolver's ``exact`` win nor manual_search's
    1000-point pin fires, and the law ranks SIXTH behind five لوائح that merely
    cite it (eval report §4.2). Measured live 2026-08-16: only **13 of 3,951**
    titles (and 5 clean_titles) carry a detached waw, every one a typography
    artifact, and collapsing it creates **zero** new duplicate-normalized-title
    groups (32 before, 32 after) — it recovers the pin without widening any
    collision.

    Pure: no DB, no I/O — the comparison key for both the query title and each
    candidate ``title``/``clean_title``.
    """
    if not text:
        return ""
    s = _TASHKEEL_RE.sub("", text)
    s = s.replace(_TATWEEL, "")
    # Unify alef variants → bare alef.
    s = (
        s.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ٱ", "ا")
    )
    # Common letter-shape unifications.
    s = s.replace("ة", "ه").replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي")
    s = _WS_RE.sub(" ", s).strip()
    s = _DETACHED_WAW_RE.sub("و", s)
    s = _LEADING_AL_RE.sub("", s).strip()
    return s.lower()


# --------------------------------------------------------------------------- #
# Title-term COVERAGE — the quantity every non-pin gate reads. Pure, no I/O.
#
# These four functions were written for ``simple_search/manual_search.py`` and
# live HERE now because ``fetch_article`` is the lower layer: manual_search
# already imports six names from this module, and the dependency may not run the
# other way. ``manual_search`` re-exports ``coverage`` so its own importers (the
# eval harness, its tests) are unaffected. ONE definition, per the same house
# rule that keeps ``_AMBIGUITY_MARGIN`` shared rather than copied.
#
# WHY COVERAGE AND NOT SCORE — the calibration that inverts the obvious approach.
# Measured live on this corpus:
#
#   query                                  BM25    coverage   correct?
#   «نظام العمل»                           1003.14   1.00      yes
#   «النظام العمل»                            3.14   1.00      yes
#   «نظام العمل السعودي»                      3.51   0.67      yes
#   «اصدار سجل تجاري»                         5.46   0.67      yes
#   «نظام الفساد المالي والإداري» (absent)   14.79   0.50      NO
#   «تعميم التسجيل العقاري»                   8.37   0.33      NO
#   «نظام حماية الفضاء السيبراني» (absent)   12.52   0.20      NO
#
# The two WRONG answers score HIGHER than every correct non-exact one. A score
# floor would not merely fail — it would invert. Coverage separates them cleanly.
# --------------------------------------------------------------------------- #


def _query_terms(query: str) -> list[str]:
    """Normalized, de-duplicated query lexemes of at least ``_MIN_TERM_CHARS``.

    Each token goes through :func:`_normalize_title` — the STRICT fold (ة→ه,
    ى→ي, ؤ→و, ئ→ي, hamza-alef unification, and a dropped leading «ال») — which
    is why «الفساد» and «فساد» count as the same term. A deliberately deeper fold
    than SQL's ``luna_normalize_ar``; see :func:`_strict_exact`.
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

    Substring containment, plus one fold the plain test misses: a «و» conjunction
    the two sides attach differently. Arabic writes the conjunction joined
    («والمشتريات»), so a title spelling the same word bare («المشتريات») fails a
    raw substring test on the user's token even though it is the very same word.
    :func:`_normalize_title` re-attaches a DETACHED waw, which fixes the corpus
    side; this fixes the QUERY side.

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
    «العمل». Reproduces every measured value in the table above exactly —
    1.00 / 1.00 / 0.67 / 0.67 for the correct resolutions and 0.50 / 0.33 / 0.20
    for the wrong ones.
    """
    terms = _query_terms(query)
    if not terms:
        return 0.0
    norm_title = _normalize_title(title or "")
    if not norm_title:
        return 0.0
    return sum(1 for t in terms if _term_in_title(t, norm_title)) / len(terms)


def title_precision(query: str, title: str) -> float:
    """The mirror of :func:`coverage` — how much of the TITLE the query explains.

    Coverage is asymmetric on purpose, and that asymmetry has a blind spot: it
    asks only how much of the QUERY appears in the title, so a long title
    trivially contains a short query's terms. Measured live 2026-09-02 on
    «نظام العمل السعودي» — terms {نظام، عمل، سعودي}:

        «قواعد وإجراءات عمل لجنة النظر في مخالفات نظام كود البناء السعودي
         ومكافأة أعضائها»                      coverage 1.00   ← ranked FIRST
        «نظام العمل»                            coverage 0.67   ← the right answer

    All three query terms really do occur in that 12-word title, scattered. So
    coverage alone ranks a document about the building code above نظام العمل.

    This is the other half: the fraction of the title's own lexemes the query
    accounts for — 1.00 for «نظام العمل», ≈0.25 for the building-code title.
    Neither number identifies a document alone; their harmonic mean
    (:func:`_relevance`) does the ordering.

    Pure. Same normalization and the same ``_MIN_TERM_CHARS`` floor as coverage,
    so the two are measured on one vocabulary.
    """
    title_terms = _query_terms(title)
    if not title_terms:
        return 0.0
    q_norm = _normalize_title(query or "")
    if not q_norm:
        return 0.0
    return sum(1 for t in title_terms if _term_in_title(t, q_norm)) / len(title_terms)


def _relevance(query: str, title: str) -> float:
    """Harmonic mean of :func:`coverage` and :func:`title_precision`. Pure.

    The ordering quantity — an F1 over title lexemes. Harmonic rather than
    arithmetic because it must punish a lopsided pair: the building-code title
    above averages to 0.63 but scores 0.40 here, while «نظام العمل» averages 0.84
    and scores 0.80. That inversion is the whole reason this exists.

    **This ranks; it does not admit.** Admission stays on
    :data:`_SHORTLIST_MIN_COVERAGE` against raw coverage, which is the measured,
    calibrated gate. Ranking and gating are kept separate deliberately — the
    gate's calibration table would not transfer to a quantity it was never
    measured against.
    """
    cov = coverage(query, title)
    prec = title_precision(query, title)
    if cov <= 0.0 or prec <= 0.0:
        return 0.0
    return 2 * cov * prec / (cov + prec)


def _strict_exact(query: str, title: str) -> bool:
    """The strict-normalized Python pin probe — Gate 1b.

    SQL's ``luna_normalize_ar`` folds hamza-carrying alef, tatweel and harakat
    ONLY; :func:`_normalize_title` also folds ة/ى/ؤ/ئ and drops a leading «ال».
    The two disagree on **91.7 %** of regulation titles, so relying on the SQL
    pin alone silently loses most exact matches — «النظام العمل» scores 3.14 on
    نظام العمل (no SQL pin) where «نظام العمل» scores 1003.14. This recovers them,
    and it is also the rung that pins a row the BM25 index does not carry at all
    (57 % of regulations).
    """
    q = _normalize_title(query or "")
    return bool(q) and q == _normalize_title(title or "")


def _distinctive_token(title: str) -> str:
    """Pick the single most distinctive raw token of a title for ILIKE retry.

    The longest word is the most specific (e.g. «التطوعي» / «المرور») — used
    only when the full-string ILIKE returns no candidates. Returns the raw
    (un-normalized) token because ILIKE is exact-char.
    """
    tokens = [t for t in _WS_RE.split(title.strip()) if t]
    if not tokens:
        return title.strip()
    return max(tokens, key=len)


# --------------------------------------------------------------------------- #
# Article-number keys — Arabic-Indic digits and Arabic ordinals. Pure.
#
# ``articles_v2.article_number`` is TEXT and the corpus writes it in Western
# digits («81», «1-1», «25 مكرر»). A user — and the article's own heading —
# writes «٨١» or «الحادية والثمانون». Both tool docstrings ask the model to
# convert first; eval §4.3 measured what happens when it does not: art-02 (٨١)
# and art-03 (الحادية والثمانون) FAILED on BOTH resolver legs, with the parent
# نظام resolving correctly and only the article key missing. This is the
# deterministic safety net for a responsibility that was delegated with none.
# --------------------------------------------------------------------------- #

# Ordinal words as a lawyer writes them. The lookup keys are their
# ``_normalize_title`` folds, built once at import, so this module keeps ONE
# spelling convention instead of a second hand-folded table that can drift.
_ORD_UNIT_WORDS: dict[str, int] = {
    "الأولى": 1, "الحادية": 1, "الواحدة": 1, "الأول": 1, "الحادي": 1,
    "الثانية": 2, "الثاني": 2,
    "الثالثة": 3, "الثالث": 3,
    "الرابعة": 4, "الرابع": 4,
    "الخامسة": 5, "الخامس": 5,
    "السادسة": 6, "السادس": 6,
    "السابعة": 7, "السابع": 7,
    "الثامنة": 8, "الثامن": 8,
    "التاسعة": 9, "التاسع": 9,
    "العاشرة": 10, "العاشر": 10,
}
# The teen marker: «الحادية عشرة» = 1 + 10. Distinct from «العاشرة» = 10.
_ORD_TEEN_WORDS: tuple[str, ...] = ("عشرة", "عشر")
_ORD_TENS_WORDS: dict[str, int] = {
    "العشرون": 20, "العشرين": 20,
    "الثلاثون": 30, "الثلاثين": 30,
    "الأربعون": 40, "الأربعين": 40,
    "الخمسون": 50, "الخمسين": 50,
    "الستون": 60, "الستين": 60,
    "السبعون": 70, "السبعين": 70,
    "الثمانون": 80, "الثمانين": 80,
    "التسعون": 90, "التسعين": 90,
}
_ORD_HUNDREDS_WORDS: dict[str, int] = {
    "المائة": 100, "المئة": 100,
    "المائتان": 200, "المائتين": 200, "المئتان": 200, "المئتين": 200,
    "الثلاثمائة": 300, "الثلاثمئة": 300,
    "الأربعمائة": 400, "الأربعمئة": 400,
    "الخمسمائة": 500, "الخمسمئة": 500,
    "الستمائة": 600, "الستمئة": 600,
    "السبعمائة": 700, "السبعمئة": 700,
    "الثمانمائة": 800, "الثمانمئة": 800,
    "التسعمائة": 900, "التسعمئة": 900,
}
# Structural words carrying no value: «المادة السابعة عشرة بعد المائة».
_ORD_SKIP_WORDS: tuple[str, ...] = ("المادة", "بعد")

_ORD_UNITS: dict[str, int] = {_normalize_title(w): v for w, v in _ORD_UNIT_WORDS.items()}
_ORD_TENS: dict[str, int] = {_normalize_title(w): v for w, v in _ORD_TENS_WORDS.items()}
_ORD_HUNDREDS: dict[str, int] = {
    _normalize_title(w): v for w, v in _ORD_HUNDREDS_WORDS.items()
}
_ORD_TEENS: frozenset[str] = frozenset(_normalize_title(w) for w in _ORD_TEEN_WORDS)
_ORD_SKIP: frozenset[str] = frozenset(_normalize_title(w) for w in _ORD_SKIP_WORDS)

# Recognized, contributes nothing. No real ordinal value is negative.
_ORD_SKIP_VALUE = -1


def _ordinal_token_variants(token: str) -> tuple[str, ...]:
    """The folded token, plus its form without a leading «و» conjunction.

    Order matters: «الواحدة» must resolve as ITSELF (1) before anything strips
    its first letter, while «والثمانون» only resolves once the conjunction is
    gone. Trying the whole token first gets both right.
    """
    folded = _normalize_title(token)
    if not folded:
        return ()
    if len(folded) > 1 and folded.startswith("و"):
        return (folded, _LEADING_AL_RE.sub("", folded[1:]).strip())
    return (folded,)


def _ordinal_token_value(token: str) -> int | None:
    """One token's contribution, :data:`_ORD_SKIP_VALUE`, or ``None`` if unknown."""
    for variant in _ordinal_token_variants(token):
        if variant in _ORD_SKIP:
            return _ORD_SKIP_VALUE
        for table in (_ORD_UNITS, _ORD_TENS, _ORD_HUNDREDS):
            if variant in table:
                return table[variant]
        if variant in _ORD_TEENS:
            return 10
    return None


def _arabic_ordinal_to_int(text: str) -> int | None:
    """«الحادية والثمانون» → 81. ``None`` when the string is not an ordinal.

    Purely additive across units, the «عشرة» teen marker, tens and hundreds —
    which is exactly how the corpus writes an article heading: «المادة السابعة
    عشرة بعد المائة» = 7 + 10 + 100 = 117. Covers 1–999, and the corpus tops out
    at 716 مادة (نظام المعاملات المدنية).

    **Any** unrecognized token aborts the whole parse. A partial reading would
    invent an article number and hand back a real article of the wrong مادة,
    which is strictly worse than not folding at all — so «25 مكرر» returns
    ``None`` here and keeps resolving on its raw key.
    """
    total = 0
    seen_value = False
    for token in _WS_RE.split((text or "").strip()):
        if not token:
            continue
        value = _ordinal_token_value(token)
        if value is None:
            return None
        if value == _ORD_SKIP_VALUE:
            continue
        total += value
        seen_value = True
    return total if seen_value and total > 0 else None


def article_number_keys(article_number: str) -> list[str]:
    """Every exact-text key one article number could be stored under, best first.

    The RAW string always comes first, so compound keys («1-1», «25 مكرر») still
    resolve on the first round trip and nothing that already worked changes
    shape. The folds are additive: Arabic-Indic digits via
    ``shared.privacy.codec.normalize_digits`` (which already existed and was
    imported by neither resolver), then the Arabic ordinal.
    """
    keys: list[str] = []

    def _add(candidate: str) -> None:
        candidate = (candidate or "").strip()
        if candidate and candidate not in keys:
            keys.append(candidate)

    raw = (article_number or "").strip()
    _add(raw)
    _add(normalize_digits(raw))
    ordinal = _arabic_ordinal_to_int(raw)
    if ordinal is not None:
        _add(str(ordinal))
    return keys


# --------------------------------------------------------------------------- #
# Ranking — pure, unit-testable.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RegCandidate:
    """One regulation candidate, scored the way the ladder's gates read it.

    ``exact`` is the **pin**: an exact normalized title match, by either the SQL
    fold (BM25 ``score >= 1000``) or the strict Python fold
    (:func:`_strict_exact`). A pin is proof of identity and is the ONLY thing
    that resolves without the planner.

    ``coverage`` is the fraction of query lexemes present in the title, and it is
    what every non-pin gate reads — never ``score``. ``score`` is the rung-native
    number (BM25's, or ``0.0`` from an ILIKE rung) and serves only as a last
    tiebreak. ``rung`` is forensic and gates the recall bar. ``bucket_label`` is
    the Arabic type word shown in the shortlist so «نظام» and its «لائحة
    تنفيذية» are distinguishable at a glance.
    """

    reg_id: str
    display: str
    score: float
    exact: bool
    coverage: float = 0.0
    relevance: float = 0.0
    rung: str = ""
    bucket_label: str = ""


def _make_candidate(query_title: str, row: dict, *, rung: str,
                    score: float = 0.0, sql_pin: bool = False) -> RegCandidate:
    """Build one candidate with its coverage and pin flag computed. Pure.

    Considers BOTH ``title`` and ``clean_title`` for the pin (best of the two
    wins) and for coverage, mirroring the old scorer's two-column reach. The
    ``display`` is ``clean_title or title`` — the human name shown in the
    shortlist and in the success header.
    """
    title = (row.get("title") or "").strip()
    clean = (row.get("clean_title") or "").strip()
    display = clean or title
    variants = [t for t in (title, clean) if t]

    pin = bool(sql_pin) or any(_strict_exact(query_title, t) for t in variants)
    cov = max((coverage(query_title, t) for t in variants), default=0.0)
    rel = max((_relevance(query_title, t) for t in variants), default=0.0)
    bucket = (row.get("doc_type_bucket") or "").strip()

    return RegCandidate(
        reg_id=str(row.get("id") or ""),
        display=display or "—",
        score=float(score or 0.0),
        exact=pin,
        coverage=cov,
        relevance=rel,
        rung=rung,
        bucket_label=_BUCKET_LABELS.get(bucket, ""),
    )


def _rank_candidates(
    query_title: str, rows: list[dict], *, rung: str = STAGE_FULL
) -> list[RegCandidate]:
    """Score + sort rows best-first, de-duplicated. Pure (no DB).

    Order: **pinned first, then coverage, then the rung-native score.** Coverage
    outranks score deliberately — that is the whole point of the calibration
    above. De-dupes by ``reg_id`` and then by normalized title, so the same
    نظام arriving from two rungs is ONE candidate rather than a fake plurality
    (which would otherwise defeat the pin-uniqueness test).

    Kept as a thin wrapper over :func:`_rank_mixed` for the single-rung callers
    (the eval harness) that still hand it a bare row list.
    """
    return _rank_mixed([(rung, rows)], query_title)


def _rank_mixed(
    rung_rows: list[tuple[str, list[dict]]], query_title: str
) -> list[RegCandidate]:
    """Rank candidates gathered from several rungs at once. Pure.

    ``rung_rows`` is ordered strongest-rung-first; on a de-dup collision the
    first occurrence wins, so a row that BM25 also returned keeps BM25's score
    and rung rather than an ILIKE rung's ``0.0``.
    """
    built: list[RegCandidate] = []
    for rung, rows in rung_rows:
        for row in rows or []:
            if not row.get("id"):
                continue
            built.append(
                _make_candidate(
                    query_title, row, rung=rung,
                    score=float(row.get("_score") or 0.0),
                    sql_pin=bool(row.get("_sql_pin")),
                )
            )

    # pin ▸ relevance ▸ coverage ▸ rung score. Relevance leads because coverage
    # alone ranks a long, loosely-related title above the right answer (see
    # `title_precision`); coverage stays in the key as the tiebreak that keeps
    # the calibrated quantity visible in the ordering.
    built.sort(key=lambda c: (c.exact, c.relevance, c.coverage, c.score), reverse=True)

    out: list[RegCandidate] = []
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    for c in built:
        norm = _normalize_title(c.display)
        if (c.reg_id and c.reg_id in seen_ids) or (norm and norm in seen_titles):
            continue
        if c.reg_id:
            seen_ids.add(c.reg_id)
        if norm:
            seen_titles.add(norm)
        out.append(c)
    return out


# --------------------------------------------------------------------------- #
# Supabase reads — sync client (matches the rest of agents/). Wrapped in
# asyncio.to_thread at the call site inside the tool body.
# --------------------------------------------------------------------------- #


# The candidate-fetch stages, weakest last. ``full`` means a title CONTAINS the
# user's whole phrase; ``token`` means it merely shares one word with it. The
# difference is the whole of eval bug #1 — see :func:`_fetch_reg_candidates_staged`.
# (STAGE_FULL / STAGE_TOKEN / _RECALL_ONLY_RUNGS are defined with the other
# ladder constants near the top — they are referenced by default arguments.)


def _reg_ilike_patterns(raw: str) -> list[str]:
    """Every full-string ILIKE pattern worth trying. All exact-char safe.

    PostgREST ILIKE compares characters, so a pattern may only be transformed
    in ways that keep it spelled the way the CORPUS spells it. Three do:
    dropping a leading «ال», and attaching or detaching a «و» conjunction. The
    rest of ``_normalize_title``'s folds — ة→ه, ى→ي, alef unification — do NOT,
    because they rewrite the string into an orthography no row contains.

    This is eval bug #3, and it has two instances, both the same shape: the
    resolver normalized before RANKING but not before FETCHING, so the
    candidate pool could not contain the answer and ``difflib`` confidently
    picked the best of what was left.

    * **Leading «ال».** «النظام العمل» matches no title verbatim, so the old
      code fell straight to the distinctive-token retry on «النظام» — 117 rows
      of every title containing it, and «نظام العمل» is not among them because
      that title does not contain the substring «النظام». Result: **«النظام
      العمل» → «النظام الصحي»**. Measured live 2026-08-16, the al-stripped
      pattern returns 9 rows with «نظام العمل» among them, which then wins as an
      exact normalized match.
    * **Detached «و».** «نظام المنافسات والمشتريات الحكومية» DOES return 5 rows
      — five لوائح that cite the law — and the law itself is in none of them,
      because its corpus title spells the conjunction detached («نظام المنافسات
      و المشتريات الحكومية»). Returning early on those 5 is what made the
      resolver commit to «نطاق تطبيق نظام المنافسات والمشتريات الحكومية
      ولائحته التنفيذية» (eval R7). Hence the caller MERGES every pattern's
      rows instead of stopping at the first non-empty one: a hit on the raw
      string is no evidence that a variant has nothing better.

    Cost, measured live 2026-08-16 (no trigram index exists on this table, so
    each pattern is a sequential scan over 3,951 rows × 2 columns): **~370 ms
    per pattern**. Most queries generate exactly one and are unchanged; a
    leading «ال» or a «و»-initial token adds a second (~760 ms total). Worth
    knowing before adding a third fold — and the durable fix is a normalized,
    indexed title column, not more patterns.
    """
    out = [raw]
    for variant in (
        _LEADING_AL_RE.sub("", raw).strip(),
        _DETACHED_WAW_RE.sub("و", raw).strip(),
        _ATTACHED_WAW_RE.sub("و ", raw).strip(),
    ):
        if variant and variant not in out:
            out.append(variant)
    return out


def _bm25_regulations(supabase, query_title: str) -> list[dict]:
    """Rung ① — ``public.bm25_search()`` over the ``regulation`` corpus.

    Returns rows shaped like ``regulations_v2`` rows (``id`` / ``title``) plus
    two private keys the ranker reads: ``_score`` (the BM25 score) and
    ``_sql_pin`` (``score >= _EXACT_PIN_SCORE``, i.e. the RPC's own
    ``luna_normalize_ar`` title equality fired).

    ``p_owner=None`` matches ``owner_user_id IS NULL`` — the public corpus. The
    RPC is called directly rather than through ``backend.app.services.
    search_service`` because ``agents/`` never imports ``backend/``.

    ``bm25_search`` returns ``content_id``, which for the ``regulation`` corpus
    IS ``regulations_v2.id`` (verified live 2026-09-02). It does NOT return
    ``clean_title`` or ``doc_type_bucket``; those stay empty here and are filled
    in by the ILIKE rungs when the same row arrives from both. Never raises: a
    failed rung contributes nothing and the ladder walks on.
    """
    q = (query_title or "").strip()
    if not q:
        return []
    try:
        resp = supabase.rpc(
            _BM25_RPC,
            {
                "p_corpora": [_BM25_CORPUS],
                "p_query": q,
                "p_owner": None,
                "p_facets": {},
                "p_limit": _BM25_LIMIT,
                "p_offset": 0,
                "p_candidates": _BM25_CANDIDATES,
            },
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_article: bm25 ~ %r failed: %s", q, exc)
        return []

    out: list[dict] = []
    for row in list(getattr(resp, "data", None) or []):
        score = float(row.get("score") or 0.0)
        out.append({
            "id": str(row.get("content_id") or ""),
            "title": (row.get("title") or "").strip(),
            "clean_title": None,
            "doc_type_bucket": "",
            "_score": score,
            "_sql_pin": score >= _EXACT_PIN_SCORE,
        })
    return out


def _reg_ilike(supabase, col: str, token: str) -> list[dict]:
    """One ``%token%`` ILIKE over one column. Never raises."""
    try:
        resp = (
            supabase.table(_REGS_TABLE)
            .select(_REG_COLUMNS)
            .ilike(col, f"%{token}%")
            .execute()
        )
        return list(getattr(resp, "data", None) or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_article: reg ILIKE %s ~ %r failed: %s", col, token, exc)
        return []


def _merge_rows(*lists: list[dict]) -> list[dict]:
    """Concatenate row lists, de-duplicating by ``id``, first occurrence wins."""
    by_id: dict[str, dict] = {}
    for rows in lists:
        for row in rows:
            rid = str(row.get("id") or "")
            if rid and rid not in by_id:
                by_id[rid] = row
    return list(by_id.values())


def _fetch_reg_candidates_full(supabase, query_title: str) -> list[dict]:
    """Rows whose title/clean_title contains the user's WHOLE phrase.

    The strong stage: every row here shares the entire query string with the
    user, in one of the orthographic variants :func:`_reg_ilike_patterns`
    enumerates. Merged across all patterns and both columns.
    """
    raw = (query_title or "").strip()
    if not raw:
        return []
    lists: list[list[dict]] = []
    for pattern in _reg_ilike_patterns(raw):
        lists.append(_reg_ilike(supabase, "title", pattern))
        lists.append(_reg_ilike(supabase, "clean_title", pattern))
    return _merge_rows(*lists)


def _fetch_reg_candidates_token(supabase, query_title: str) -> list[dict]:
    """Rows sharing only the single most distinctive WORD with the query.

    The weak stage, and a genuinely last resort: «السيبراني» alone returns 29
    rows, «النظام» returns 117. It exists so a mistyped or over-qualified name
    still reaches a candidate list, and callers must treat what it returns as
    recall, never as evidence of identity — see the ``manual_search`` rung
    ``ilike_token`` and its ``_RECALL_ONLY_RUNGS`` membership.
    """
    raw = (query_title or "").strip()
    token = _distinctive_token(raw) if raw else ""
    if not token or token == raw:
        return []
    return _merge_rows(
        _reg_ilike(supabase, "title", token),
        _reg_ilike(supabase, "clean_title", token),
    )


def _fetch_reg_candidates_staged(supabase, query_title: str) -> tuple[list[dict], str]:
    """:func:`_fetch_reg_candidates` plus WHICH stage produced the rows.

    Returns ``(rows, stage)`` where stage is :data:`STAGE_FULL` (some title
    contains the user's whole phrase) or :data:`STAGE_TOKEN` (nothing did, so
    these rows share only the single most distinctive word). Empty rows carry
    an empty stage.

    Callers that rank with ``difflib`` can ignore the stage — the score already
    reflects the weakness. ``manual_search`` cannot: its Gate 2 reads title-term
    COVERAGE, which a one-word-in-common row can clear without ever being a
    plausible answer. Measured live, all three of the eval's wrong-document
    ILIKE wins came from this stage and the full-string stage returned **zero**
    rows for each of them:

    ===================================  ===========  ==========================
    query (absent from the corpus)       full-string  token retry
    ===================================  ===========  ==========================
    «نظام الفساد المالي والإداري»          0 rows       «والإداري» → 2 rows
    «نظام حماية الفضاء السيبراني الوطني»   0 rows       «السيبراني» → 29 rows
    «تطبيقات نظام العمل»                  0 rows       «تطبيقات» → 9 rows
    ===================================  ===========  ==========================

    ``manual_search`` calls :func:`_fetch_reg_candidates_full` and
    :func:`_fetch_reg_candidates_token` separately rather than using this
    composition, because it runs them at different points on its ladder: the
    strong stage unconditionally, the weak one only if nothing has resolved.
    """
    rows = _fetch_reg_candidates_full(supabase, query_title)
    if rows:
        return rows, STAGE_FULL
    rows = _fetch_reg_candidates_token(supabase, query_title)
    return (rows, STAGE_TOKEN) if rows else ([], "")


def _fetch_reg_candidates(supabase, query_title: str) -> list[dict]:
    """Candidate-fetch regulations whose title/clean_title ILIKE the query.

    The rows of :func:`_fetch_reg_candidates_staged`, without the stage — the
    shape every existing caller (``resolve_regulation_id``, the eval harness)
    already expects. Never raises: a failed query logs and contributes nothing.
    """
    return _fetch_reg_candidates_staged(supabase, query_title)[0]


def _fetch_article_content(supabase, regulation_id: str, article_number: str) -> str | None:
    """Fetch ``articles_v2.content`` for ``(regulation_id, article_number)``.

    ``article_number`` is matched by exact TEXT equality (the corpus stores
    compound values like ``"1-1"`` as strings), tried across every key
    :func:`article_number_keys` derives — the raw string first, then its
    Arabic-Indic-digit and Arabic-ordinal folds. Returns the content string, or
    ``None`` when no such article row exists. Never raises.

    The extra round trips only ever happen on a MISS, which used to be the end
    of the road anyway: «٨١» and «الحادية والثمانون» both resolved the right
    نظام and then failed on the key alone.
    """
    for key in article_number_keys(article_number):
        try:
            resp = (
                supabase.table(_ARTICLES_TABLE)
                .select(_ARTICLE_COLUMNS)
                .eq("regulation_id", regulation_id)
                .eq("article_number", key)
                .limit(1)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "fetch_article: article fetch failed for reg=%s art=%r: %s",
                regulation_id, key, exc,
            )
            return None
        data = getattr(resp, "data", None)
        if not data:
            continue
        # ``.limit(1)`` returns a list; some fakes may return a single dict.
        row = data[0] if isinstance(data, list) else data
        content = (row or {}).get("content")
        if content:
            return content
    return None


# --------------------------------------------------------------------------- #
# Resolution layer — pure orchestration over the sync reads. Returns either a
# resolved (reg_id, display) pair, an ``AMBIGUOUS:`` payload, or a not-found
# sentinel. Synchronous; the tool body dispatches it via asyncio.to_thread.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResolveResult:
    """Outcome of resolving a regulation title.

    Exactly one of ``reg_id`` (**pinned** — an exact normalized title match) or
    ``ambiguous`` (the shortlist payload for the planner to pick from) is
    populated; both empty ⇒ nothing worth showing matched at all.

    ``exact`` is now redundant with ``reg_id`` — below the pin nothing resolves —
    and is kept so existing readers (the eval harness) keep working.
    ``shortlist`` is the structured form of ``ambiguous``, for tests and callers
    that want the candidates rather than the rendered Arabic.
    """

    reg_id: str = ""
    display: str = ""
    ambiguous: str = ""  # the rendered shortlist payload when set
    exact: bool = False  # True only on an exact normalized title match (→ HIGH)
    shortlist: tuple[RegCandidate, ...] = ()


def _build_ambiguous(candidates: list[RegCandidate]) -> str:
    """Render the shortlist the planner picks from — or declines.

    Replaces the old ``AMBIGUOUS:`` / «اسأل المستخدم» payload. The contract
    changed with the gates: below a pin, nothing resolves, so this is no longer
    "two candidates are too close to call" — it is "here is what exists; you hold
    the user's wording, you decide."

    Two properties are load-bearing:

    * **Titles are quoted VERBATIM in corpus spelling**, because the re-call is
      keyed on them — and because seeing «الايجار» next to the «الإيجار» that was
      asked for is exactly how the planner catches an orthography split.
    * **"Choose nothing" is written in as an explicit option.** A model handed a
      numbered list picks from it; an implicit refusal would rebuild the silent
      auto-commit this rewrite exists to delete.
    """
    lines: list[str] = []
    for c in candidates[:_SHORTLIST_N]:
        suffix = f" — {c.bucket_label}" if c.bucket_label else ""
        lines.append(f"{len(lines) + 1}. «{c.display}»{suffix}")
    listed = "\n".join(lines)
    return (
        "AMBIGUOUS: لم يتطابق الاسم المذكور مع نظام واحد بعينه. أقرب المرشحين:\n"
        f"{listed}\n\n"
        "إن كان أحدها هو المقصود فأعد النداء باسمه كما هو مكتوب أعلاه حرفيًّا.\n"
        "وإن لم يكن أيٌّ منها المقصود فلا تختر — اترك الأمر للبحث العادي."
    )


def resolve_regulation_id(supabase, regulation_title: str) -> ResolveResult:
    """Resolve a user-supplied regulation title to a ``regulations_v2.id``.

    Returns a :class:`ResolveResult` in exactly one of three states:

    * **pinned** — ``reg_id`` set, ``exact=True``. A unique exact normalized
      title match. The only state that resolves without the planner.
    * **shortlist** — ``ambiguous`` / ``shortlist`` set. Nothing pinned, but
      candidates cleared :data:`_SHORTLIST_MIN_COVERAGE`. The planner picks one
      by re-calling with the corpus spelling, or declines.
    * **empty** — nothing worth showing.

    There is deliberately no fourth state. The old resolver had one — accept a
    non-exact match above a similarity floor, hedge it with «ثقة متوسطة» — and
    that state is what returned the wrong law's article for
    «نظام الإيجار التمويلي» (see :func:`_bm25_regulations` for the fold that
    fixes it). Synchronous (sync PostgREST + RPC reads inside).
    """
    ranked = _resolve_candidates(supabase, regulation_title)
    if not ranked:
        return ResolveResult()

    # --- Gate 1 / 1b — the pin. The ONLY thing that resolves on its own. ------
    # An exact normalized title match, by either fold. Uniqueness is checked
    # rather than assumed: 10 duplicate-normalized-title groups exist among
    # regulations (and 44 among circulars, 176 among judgments), so "there is a
    # pin" is not "there is ONE pin". A tie falls through to the shortlist.
    pins = [c for c in ranked if c.exact]
    if len(pins) == 1:
        top = pins[0]
        return ResolveResult(reg_id=top.reg_id, display=top.display, exact=True)
    if len(pins) > 1:
        return ResolveResult(
            ambiguous=_build_ambiguous(pins), shortlist=tuple(pins[:_SHORTLIST_N])
        )

    # --- Gate 2 — no pin ⇒ NOTHING resolves. Shortlist or nothing. -----------
    # This is the behavioural change. The old resolver accepted a non-exact match
    # above a similarity floor and hung a «ثقة متوسطة» note on it; that is how
    # «نظام الإيجار التمويلي» came back as its لائحة with a different مادة 22
    # (conversation 631a69af, 2026-09-02). A wrong article body reads exactly like
    # a right one, so the resolver no longer guesses — it shows its work.
    #
    # Admission is on COVERAGE, never on the rung score (see the calibration
    # table above), and a candidate found ONLY by a recall-only rung may not
    # stand alone: sharing one word with the query is retrieval, not identity.
    above = [c for c in ranked if c.coverage >= _SHORTLIST_MIN_COVERAGE]
    shortlist = [c for c in above if c.rung not in _RECALL_ONLY_RUNGS]
    if not shortlist:
        return ResolveResult()

    return ResolveResult(
        ambiguous=_build_ambiguous(shortlist),
        shortlist=tuple(shortlist[:_SHORTLIST_N]),
    )


def _resolve_candidates(supabase, regulation_title: str) -> list[RegCandidate]:
    """Walk the ladder and return one ranked, de-duplicated candidate pool.

    Rungs, strongest first:

    ① ``bm25_search`` — carries the SQL exact pin, and is the rung that fixes the
      orthography split: ``luna_normalize_ar`` folds hamza-alef, so a hamza query
      pins the corpus's bare-alef title. Reaches 1,689 of 3,956 regulations.
    ② full-string ILIKE — every row whose title CONTAINS the user's whole phrase,
      across the orthographic variants :func:`_reg_ilike_patterns` enumerates.
      Reaches all 3,956, which is why BM25 does not replace it: dropping this
      rung would make 57 % of the corpus unresolvable by title.
    ③ distinctive-token ILIKE — one shared word. RECALL ONLY, and run only when
      ① and ② found nothing at all, because it is a full sequential scan whose
      rows may never stand alone as evidence anyway.

    Rows are merged, not raced: a non-empty strong rung is no evidence that a
    weaker one has nothing better, and the de-dup in :func:`_rank_mixed` keeps
    the strongest rung's score when the same نظام arrives twice.
    """
    title = (regulation_title or "").strip()
    if not title:
        return []

    bm25_rows = _bm25_regulations(supabase, title)
    full_rows = _fetch_reg_candidates_full(supabase, title)

    rung_rows: list[tuple[str, list[dict]]] = [
        (RUNG_BM25, bm25_rows),
        (STAGE_FULL, full_rows),
    ]
    if not bm25_rows and not full_rows:
        rung_rows.append((STAGE_TOKEN, _fetch_reg_candidates_token(supabase, title)))

    ranked = _rank_mixed(rung_rows, title)
    return _enrich_buckets(supabase, ranked)


def _enrich_buckets(supabase, ranked: list[RegCandidate]) -> list[RegCandidate]:
    """Fill ``bucket_label`` for candidates that only BM25 returned.

    ``bm25_search`` does not select ``doc_type_bucket``, so a نظام the ILIKE rungs
    missed would reach the shortlist with no type word — and telling a نظام from
    its لائحة تنفيذية is the entire reason the shortlist shows one. One batched
    read over at most ``_SHORTLIST_N`` ids, and only when something is actually
    missing. Best-effort: on failure the labels stay empty and the shortlist is
    merely less legible.
    """
    missing = [c.reg_id for c in ranked[:_SHORTLIST_N] if c.reg_id and not c.bucket_label]
    if not missing:
        return ranked
    try:
        resp = (
            supabase.table(_REGS_TABLE)
            .select("id,doc_type_bucket")
            .in_("id", missing)
            .execute()
        )
        buckets = {
            str(r.get("id")): _BUCKET_LABELS.get((r.get("doc_type_bucket") or "").strip(), "")
            for r in (getattr(resp, "data", None) or [])
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_article: bucket enrich failed: %s", exc)
        return ranked

    return [
        c if c.bucket_label or not buckets.get(c.reg_id)
        else RegCandidate(
            reg_id=c.reg_id, display=c.display, score=c.score, exact=c.exact,
            coverage=c.coverage, relevance=c.relevance, rung=c.rung,
            bucket_label=buckets[c.reg_id],
        )
        for c in ranked
    ]


@dataclass(frozen=True)
class FetchArticleResult:
    """Rich outcome of a fetch — drives both the tool return and the pin.

    ``text`` is what the **planner** sees — header + body, and the body is capped
    at :data:`_PLANNER_ARTICLE_CAP`. ``content`` is the verbatim, UNCAPPED article
    body (no header) — the body that gets pinned and that reaches the executors
    and the aggregator via the ``statute_articles`` context block. Those two
    fields are the audience seam; do not collapse them.

    ``status`` is one of ``"ok"`` / ``"ambiguous"`` (a shortlist to pick from) /
    ``"not_found"``. ``confidence`` is ``"high"`` (the only resolving state — a
    unique exact title pin) or ``""``. The former ``"medium"`` value is gone with
    the non-exact auto-commit that produced it.
    """

    text: str
    status: str  # "ok" | "ambiguous" | "not_found"
    confidence: str = ""  # "high" | "medium" | ""
    reg_id: str = ""
    reg_name: str = ""
    article_number: str = ""
    content: str = ""


def fetch_article_result(
    supabase, regulation_title: str, article_number: str
) -> FetchArticleResult:
    """Full deterministic fetch: resolve title → fetch article → render + classify.

    Synchronous (the tool body wraps this in ``asyncio.to_thread``). On success
    returns ``status="ok"`` with the rendered ``text`` (header + body), a
    ``confidence`` (``high`` on an exact regulation match, ``medium`` on an
    above-floor non-exact one), and the verbatim ``content`` for pinning. On a
    medium match the resolved law differs from what the user typed, so a short
    confidence note is appended to ``text`` (NOT to ``content``) so the planner
    can verify before trusting it. Ambiguous / not-found return the
    corresponding plain string with no confidence and no pinnable content.

    TEXT ONLY — never a citation, never ``article_ref`` / ``chunk_parent_id``.
    """
    num = (article_number or "").strip()
    resolved = resolve_regulation_id(supabase, regulation_title)

    if resolved.ambiguous:
        return FetchArticleResult(text=resolved.ambiguous, status="ambiguous")

    if not resolved.reg_id:
        # No regulation matched at all — let the planner fall back to search.
        return FetchArticleResult(
            text=f"المادة {num} غير موجودة في {regulation_title.strip()}",
            status="not_found",
        )

    content = _fetch_article_content(supabase, resolved.reg_id, num)
    reg_name = resolved.display or regulation_title.strip()
    if not content:
        return FetchArticleResult(
            text=f"المادة {num} غير موجودة في {reg_name}",
            status="not_found", reg_id=resolved.reg_id, reg_name=reg_name,
            article_number=num,
        )

    # Only a pin gets here, so confidence is always high — there is no longer a
    # "medium" state to hedge (see resolve_regulation_id's third-state note).
    body = content.strip()
    header = f"## نص المادة {num} من {reg_name}"
    # The header names the RESOLVED title, not the one that was asked for. That
    # is the receipt for the orthography fold: a planner that asked for
    # «الإيجار» and reads «الايجار» back can see which row it actually got.
    return FetchArticleResult(
        text=f"{header}\n\n{_cap_for_planner(body)}",
        status="ok", confidence="high",
        reg_id=resolved.reg_id, reg_name=reg_name,
        article_number=num, content=body,
    )


def _cap_for_planner(body: str) -> str:
    """Trim an article body to :data:`_PLANNER_ARTICLE_CAP` for the planner ONLY.

    Applied to ``FetchArticleResult.text`` and never to ``.content`` — the seam
    that lets the planner read a summary-length article while the executors, the
    aggregator and the pinned workspace item all receive the whole thing. 98.5 %
    of articles are shorter than the cap and pass through byte-identical.

    Cuts on the last paragraph break inside the budget when there is one, so the
    fragment ends on a complete clause rather than mid-sentence, and always
    appends :data:`_CAP_MARKER` so the planner knows it is holding a fragment.
    """
    text = body or ""
    if len(text) <= _PLANNER_ARTICLE_CAP:
        return text
    head = text[:_PLANNER_ARTICLE_CAP]
    # Prefer a paragraph boundary, but only if it keeps most of the budget —
    # otherwise a document whose first break is at char 20 would lose everything.
    cut = head.rfind("\n\n")
    if cut < _PLANNER_ARTICLE_CAP // 2:
        cut = head.rfind("\n")
    if cut < _PLANNER_ARTICLE_CAP // 2:
        cut = _PLANNER_ARTICLE_CAP
    return head[:cut].rstrip() + _CAP_MARKER


def fetch_article_text(supabase, regulation_title: str, article_number: str) -> str:
    """Thin wrapper → the rendered ``text`` only (back-compat for callers/tests)."""
    return fetch_article_result(supabase, regulation_title, article_number).text


# --------------------------------------------------------------------------- #
# Statute package — ONE durable workspace item per search. Successful fetches
# accumulate on ``deps._fetched_articles`` during the decider's tool loop; the
# runner calls ``flush_statute_package`` once per turn to write the bundle. Lean
# reuse of save_memo's pattern: a fire-and-forget insert, best-effort (a failure
# NEVER affects the fetch). Lazy backend import + a monkeypatchable insert
# wrapper keep this module import-light and unit-testable.
# --------------------------------------------------------------------------- #


def accumulate_fetched_article(deps, *, reg_name: str, article_number: str,
                               content: str, confidence: str) -> None:
    """Record a successful fetch on the turn's accumulator for the package flush.

    Best-effort: appends ``{regulation, article_number, content, confidence}`` to
    ``deps._fetched_articles`` when that list slot exists (``PlannerDeps`` has
    it). A list append is atomic on the event loop, so concurrent ``fetch_article``
    calls accumulate safely without a lock. No-op on minimal deps (the fetch
    still returns its text).
    """
    bucket = getattr(deps, "_fetched_articles", None)
    if isinstance(bucket, list) and content:
        bucket.append({
            "regulation": reg_name,
            "article_number": str(article_number),
            "content": content,
            "confidence": confidence,
        })


def _package_title(articles: list[dict]) -> str:
    """Title for the statute package card: name the single article, else count."""
    if len(articles) == 1:
        a = articles[0]
        return f"نص المادة {a['article_number']} من {a['regulation']}"
    return f"نصوص المواد المستشهد بها ({len(articles)})"


def build_statute_package_md(articles: list[dict]) -> str:
    """Render the package body: the marker + one ``## نص المادة …`` section per
    article (verbatim content). Pure — unit-testable without a DB."""
    sections = "\n\n".join(
        f"## نص المادة {a['article_number']} من {a['regulation']}\n\n"
        f"{(a.get('content') or '').strip()}"
        for a in articles
    )
    return f"{_STATUTE_PACKAGE_MARKER}\n\n{sections}"


def _insert_statute_item(supabase, *, user_id: str, conversation_id: str,
                         title: str, content_md: str, metadata: dict) -> dict:
    """Insert the statute package row. Lazy backend import keeps this module
    light; tests monkeypatch THIS function rather than the heavy service layer."""
    from backend.app.services.workspace_service import create_workspace_item

    return create_workspace_item(
        supabase,
        user_id,
        kind=_STATUTE_KIND,
        created_by=_STATUTE_CREATED_BY,
        title=title,
        conversation_id=conversation_id,
        content_md=content_md,
        metadata=metadata,
    )


def flush_statute_package(deps) -> str | None:
    """Write the turn's accumulated articles as ONE ``statute_package`` workspace
    item, then clear the accumulator. One package per search.

    Deduped within the turn by ``(regulation, article_number)`` (the planner's
    retry loop may fetch the same article twice). Best-effort + fully guarded — a
    flush failure never propagates. Returns the new ``item_id``, or ``None`` when
    nothing was accumulated / scope is missing / on any error. The accumulator is
    snapshotted-and-cleared up front so a second flush can't double-write.
    """
    bucket = getattr(deps, "_fetched_articles", None)
    if not isinstance(bucket, list) or not bucket:
        return None
    articles = list(bucket)
    bucket.clear()

    # Hand the snapshot to the sibling slot BEFORE any early return below.
    #
    # This flush runs inside the planner's decider phase (runner.py — both the
    # decided branch and the ask_user pause branch), which is EARLIER than
    # ``run_retrieval``'s context-block construction. The snapshot-and-clear
    # above is the guard that stops a second flush double-writing the workspace
    # item; without this line it would also erase the only copy of the article
    # text the downstream ``statute_articles`` block is built from, and the
    # executors would receive nothing — the failure the whole cap depends on
    # not happening. Assigned unconditionally: the block must survive a missing
    # user_id/conversation_id scope, and even a failed insert.
    if isinstance(getattr(deps, "_flushed_articles", None), list):
        deps._flushed_articles.extend(articles)

    user_id = getattr(deps, "user_id", "") or ""
    conversation_id = getattr(deps, "conversation_id", "") or ""
    if not (user_id and conversation_id):
        return None

    try:
        # Dedup within the turn — keep first occurrence of each (reg, article).
        seen: set[tuple[str, str]] = set()
        uniq: list[dict] = []
        for a in articles:
            key = (a.get("regulation", ""), str(a.get("article_number", "")))
            if key in seen:
                continue
            seen.add(key)
            uniq.append(a)
        if not uniq:
            return None

        row = _insert_statute_item(
            deps.supabase,
            user_id=user_id,
            conversation_id=conversation_id,
            title=_package_title(uniq),
            content_md=build_statute_package_md(uniq),
            metadata={
                "subtype": _STATUTE_PACKAGE_SUBTYPE,
                "articles": [
                    {
                        # The RESOLVED corpus title, not what was asked for. On a
                        # corpus/query orthography split («نظام الايجار التمويلي»
                        # vs «نظام الإيجار التمويلي») this is the only record of
                        # which row was actually read — a later turn reading this
                        # package can tell without re-resolving.
                        "regulation": a.get("regulation", ""),
                        "article_number": str(a.get("article_number", "")),
                        # Always "high" now — only a unique exact pin resolves.
                        # Kept as a field so older rows stay readable.
                        "confidence": a.get("confidence", ""),
                    }
                    for a in uniq
                ],
            },
        )
        item_id = row.get("item_id") or row.get("artifact_id") or None
        if item_id:
            _emit_pin_chip(deps, item_id, _package_title(uniq), len(uniq))
        return item_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_article: statute package flush failed: %s", exc)
        return None


def _emit_pin_chip(deps, item_id: str, title: str, count: int) -> None:
    """Best-effort: surface a ``workspace_item_created`` chip via the planner's
    SSE sink so the pinned package card appears immediately. If no sink is wired,
    the card still loads on the next turn (persistence is the guarantee)."""
    emit = getattr(deps, "emit_sse", None)
    if not callable(emit):
        return
    try:
        emit({
            "type": "workspace_item_created",
            "item_id": item_id,
            "kind": _STATUTE_KIND,
            "subtype": _STATUTE_PACKAGE_SUBTYPE,
            "title": title,
            "created_by": _STATUTE_CREATED_BY,
        })
    except Exception:  # noqa: BLE001 — chip is cosmetic; never break the fetch
        logger.debug("fetch_article: pin chip emit failed", exc_info=True)


# --------------------------------------------------------------------------- #
# Pydantic AI tool.
# --------------------------------------------------------------------------- #


def register_fetch_article(agent: Agent) -> None:
    """Register the ``fetch_article`` tool on a Pydantic AI agent.

    The agent's deps must structurally satisfy :class:`HasSupabase`
    (``.supabase``). Registered on the **planner decider only** — it grounds the
    decider's ``planner_brief`` on the verbatim text of a numbered article
    before the search is decided.
    """

    @agent.tool
    async def fetch_article(  # noqa: RUF029 — supabase client is sync by design
        ctx: RunContext[HasSupabase],
        regulation_title: str,
        article_number: str,
    ) -> str:
        """Fetch the verbatim text of ONE article (مادة) from a named regulation.

        Use this BEFORE deciding the search, when the user cites a specific
        article *by number* in a specific law/regulation (e.g. «المادة 81 من
        نظام العمل»). Semantic search can't reliably retrieve an article by its
        number, so this does a deterministic structured lookup and returns the
        article's actual text — which you should then carry into
        ``planner_brief`` verbatim so it reaches the executors and aggregator.

        This does NOT replace the search: still run the normal reg_search so the
        answer gets its supporting sources and citations. The fetched article
        stays purely as ``planner_brief`` text — never turn it into a citation.

        Pass ``article_number`` as the plain string form the user used ("81",
        "1-1") — convert Arabic ordinals («الحادية والثمانون») or Arabic-Indic
        digits («٨١») to the plain Western-digit form first.

        All articles you fetch this turn are bundled into one durable reference
        card (it persists across turns) — you don't manage that.
        On an approximate (non-exact) regulation match the text carries a brief
        «(ثقة متوسطة …)» note: verify it's the intended law (``ask_user`` if
        unsure), and carry only the article body — not that note — into
        ``planner_brief``.

        Returns:
            - The article text, prefixed with a one-line header naming the
              resolved regulation, on success (plus a confidence note on a
              non-exact match).
            - A string starting ``AMBIGUOUS:`` listing candidate regulation
              titles when the named regulation is ambiguous — in which case use
              your ``ask_user`` tool to ask which one the user means.
            - ``"المادة N غير موجودة في <نظام>"`` when the article isn't found —
              fall back to a normal semantic search instead.

        Args:
            regulation_title: The regulation as the user named it, e.g.
                «نظام العمل».
            article_number: The article number as an exact-text key, e.g.
                "81" or compound "1-1".
        """
        try:
            result = await asyncio.to_thread(
                fetch_article_result, ctx.deps.supabase, regulation_title, article_number,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "fetch_article error for title=%r art=%r: %s",
                regulation_title, article_number, exc,
            )
            return f"المادة {(article_number or '').strip()} غير موجودة في {(regulation_title or '').strip()}"

        # A successful fetch is accumulated on the turn's deps slot; the runner
        # flushes all of them into ONE statute_package workspace item per search
        # (flush_statute_package). A plain list append — no DB write here.
        if result.status == "ok":
            accumulate_fetched_article(
                ctx.deps,
                reg_name=result.reg_name,
                article_number=result.article_number,
                content=result.content,
                confidence=result.confidence,
            )

        logger.info(
            "fetch_article: title=%r art=%r → status=%s conf=%s (%d chars)",
            regulation_title, article_number, result.status, result.confidence,
            len(result.text),
        )
        return result.text


__all__ = [
    "register_fetch_article",
    "fetch_article_result",
    "fetch_article_text",
    "resolve_regulation_id",
    "accumulate_fetched_article",
    "flush_statute_package",
    "build_statute_package_md",
    "article_number_keys",
    "_fetch_article_content",
    "_fetch_reg_candidates",
    "_fetch_reg_candidates_full",
    "_fetch_reg_candidates_staged",
    "_fetch_reg_candidates_token",
    "_normalize_title",
    # The pure coverage layer — manual_search re-exports `coverage` from here so
    # both resolvers share ONE definition of the quantity their gates read.
    "coverage",
    "title_precision",
    "_query_terms",
    "_term_in_title",
    "_strict_exact",
    "_MIN_TERM_CHARS",
    "_bm25_regulations",
    "_cap_for_planner",
    "STAGE_FULL",
    "STAGE_TOKEN",
    "RUNG_BM25",
    "FetchArticleResult",
    "RegCandidate",
    "ResolveResult",
    "HasSupabase",
]
