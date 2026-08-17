"""Axis 1 — the labeled resolution fixture set.

Every ``reg_id`` below was pulled from the LIVE corpus (project
``dwgghvxogtwyaxmbgjod``) on 2026-08-16, not invented. The labels are what a
Saudi lawyer would call correct, decided BEFORE running either resolver — that
ordering is the whole point of a labeled set.

WHAT IS BEING SCORED
--------------------
The searcher has **two** identity legs and they are calibrated independently:

* ``det``    — ``fetch_article.resolve_regulation_id``: PostgREST ILIKE
  candidate-fetch + ``difflib`` ranking, floored by ``_MIN_MATCH_SCORE = 0.40``
  and split by ``_AMBIGUITY_MARGIN = 0.1``. This is what the searcher's
  ``resolve_regulation`` / ``resolve_article`` tools call FIRST.
* ``manual`` — ``manual_search.manual_search_core`` + ``decide``: the BM25 →
  ILIKE → semantic ladder gated by ``_MIN_TITLE_COVERAGE = 0.60`` and the same
  ``_AMBIGUITY_MARGIN``. The fallback leg, and the ONLY leg for judgments /
  services / circulars.

A fixture is scored against both legs, because a class the plan calls "must
refuse" has to be refused by whichever leg the LLM happens to reach for.

VERDICT VOCABULARY
------------------
``resolve`` — hand one specific document to the synthesizer. ``expect_reg_id``
              must match, or the fixture counts as a WRONG-DOCUMENT failure,
              which is strictly worse than a refusal.
``refuse``  — return nothing resolvable. ``not_found`` is the ideal shape;
              ``candidates`` / ``ambiguous`` are accepted as "did not commit"
              (the searcher can still ``ask_user``), and are reported
              separately so an over-broad candidate table stays visible.
``ask``     — the corpus genuinely cannot decide; the correct output is an
              ``ambiguous`` payload or a candidate table, never a winner.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --- Live corpus ids (verified 2026-08-16) ---------------------------------- #
AMAL = "da51024f-a713-48e7-af87-b6a541f055e4"          # نظام العمل (233 مواد)
AMAL_TATAWUI = "271b646f-42ce-472d-80a9-4248209e57b1"  # نظام العمل التطوعي
AMAL_LAIHA = "be7a89c5-04f3-4546-8c04-c1c543ef06ff"    # اللائحة التنفيذية لنظام العمل … الجزء 2
MADANIYA = "1406637f-0f07-440a-b4aa-fb0637539677"      # نظام المعاملات المدنية (716 مواد)
TANFEETH = "a49cc765-8023-4d21-88dd-27b6e720efe8"      # نظام التنفيذ (98 مواد)
TANFEETH_MATHALIM = "8e692dfa-4cb1-4981-b893-a8be026ed5fa"  # نظام التنفيذ أمام ديوان المظالم
IQAMA = "9bf4d616-5a3b-4469-896d-8713f4e19c0b"         # title «نظام الإقامة لعام 1371هـ» / clean «نظام الإقامة»
MUNAFASAT = "90d782fa-71e7-4828-b63d-1c1767de8164"     # «نظام المنافسات و المشتريات الحكومية» (note the spaced «و»)
NITAQ = "b4a01da2-e571-4060-a235-693aaa2f7006"         # اللائحة …النطاق العمراني — carries «1-1»
KOD_BINA = "16a94b17-b946-4e24-b1d5-5eb2e71830fb"      # title «كود البناء السعودي العام ( SBC 201 )» / clean without the code
MURUR = "326288aa-6717-4760-acc0-25fe33c9cd78"         # نظام المرور
SHARIKAT = "92b8d296-8ab1-4cdd-a220-98cbe693d9fe"      # نظام الشركات
RAQABA = "18815961-f7e4-4062-be01-098f9bc5f44e"        # نظام هيئة الرقابة ومكافحة الفساد — the absent-law FP magnet


@dataclass(frozen=True)
class Fixture:
    """One labeled lookup.

    ``query`` is passed VERBATIM to both legs — a paraphrase would destroy the
    BM25 exact-title pin and make the measurement meaningless (manual_search
    trap #11).
    """

    fid: str
    cls: str            # fixture class — the reporting bucket
    query: str
    data_type: str      # regs | article | judgments | services | circulars
    expect: str         # resolve | refuse | ask
    expect_reg_id: str = ""
    article_number: str = ""
    # A resolve-fixture may also name documents that must NOT win. A hit here is
    # reported as a cross-resolution failure even when `expect_reg_id` matched
    # on the other leg.
    forbid_reg_ids: tuple[str, ...] = ()
    why: str = ""
    legs: tuple[str, ...] = ("det", "manual")


FIXTURES: list[Fixture] = [
    # ─────────────────────────────────────────────────────────────────────────
    # CLASS: tp_article — «المادة N من نظام X» in every surface form the user
    # can type it. The number normalization is the searcher LLM's job; these
    # fixtures pin what the DETERMINISTIC layer does once it has a plain key.
    # ─────────────────────────────────────────────────────────────────────────
    Fixture("art-01", "tp_article", "نظام العمل", "article", "resolve", AMAL,
            article_number="81",
            why="Western digits, exact title. The baseline everything else is compared to."),
    Fixture("art-02", "tp_article", "نظام العمل", "article", "resolve", AMAL,
            article_number="٨١",
            why="Arabic-Indic digits reaching articles_v2 UN-normalized. The corpus stores "
                "article_number as TEXT '81'; if nothing folds ٨١→81 this misses."),
    Fixture("art-03", "tp_article", "نظام العمل", "article", "resolve", AMAL,
            article_number="الحادية والثمانون",
            why="Arabic ordinal — the form the article body itself uses "
                "(«المادة الحادية والثمانون»). Pins whether the deterministic layer "
                "folds it or relies entirely on the LLM."),
    Fixture("art-04", "tp_article", "نظام التنفيذ", "article", "resolve", TANFEETH,
            article_number="67", forbid_reg_ids=(TANFEETH_MATHALIM,),
            why="The router-prompt's own type-4 example. «نظام التنفيذ أمام ديوان المظالم» "
                "is a real lookalike that must not win."),
    Fixture("art-05", "tp_article", "نظام المعاملات المدنية", "article", "resolve", MADANIYA,
            article_number="1",
            why="716-article law, exact title."),
    Fixture("art-06", "tp_article", "اللائحة التنفيذية المحدثة لقواعد النطاق العمراني حتى عام ١٤٥٠ هـ",
            "article", "resolve", NITAQ, article_number="1-1",
            why="COMPOUND number. articles_v2.article_number is TEXT so '1-1' is a real key; "
                "the chunks_v2.owns fallback is structurally unable to serve it (owns.MADDA "
                "is an INTEGER array) — plan §4 L3."),
    Fixture("art-07", "tp_article", "نظام الإقامة", "article", "resolve", IQAMA,
            article_number="25 مكرر",
            why="«مكرر» compound + clean_title/title divergence in ONE fixture: the user "
                "types «نظام الإقامة» (= clean_title) but title is «نظام الإقامة لعام 1371هـ»."),
    Fixture("art-08", "tp_article", "نظام العمل", "article", "refuse", "",
            article_number="9999",
            why="Right law, absent article. Must be a clean miss naming the RESOLVED law, "
                "never a neighbouring article."),

    # ─────────────────────────────────────────────────────────────────────────
    # CLASS: tp_full_reg — «اش يقول نظام X». Whole-document identity.
    # ─────────────────────────────────────────────────────────────────────────
    Fixture("reg-01", "tp_full_reg", "نظام العمل", "regs", "resolve", AMAL,
            forbid_reg_ids=(AMAL_TATAWUI, AMAL_LAIHA),
            why="Exact title. BM25 exact pin should fire at ~1003."),
    Fixture("reg-02", "tp_full_reg", "نظام المرور", "regs", "resolve", MURUR,
            why="Second exact-pin control (plan measured 1006.41)."),
    Fixture("reg-03", "tp_full_reg", "نظام الشركات", "regs", "resolve", SHARIKAT,
            why="Third exact-pin control (plan measured 1004.36)."),
    Fixture("reg-04", "tp_full_reg", "نظام المعاملات المدنية", "regs", "resolve", MADANIYA,
            why="The dangerous-pair law, asked WHOLE. Must resolve."),
    Fixture("reg-05", "tp_full_reg", "نظام الإقامة", "regs", "resolve", IQAMA,
            why="clean_title vs title DIVERGENCE. The user's string equals clean_title; "
                "title carries a «لعام 1371هـ» suffix. 2,711 of 3,951 regs diverge."),
    Fixture("reg-06", "tp_full_reg", "كود البناء السعودي العام", "regs", "resolve", KOD_BINA,
            why="Divergence the other way: clean_title is the user-facing name, title "
                "carries « ( SBC 201 )». Also NO «نظام» prefix at all."),
    Fixture("reg-07", "tp_full_reg", "النظام العمل", "regs", "resolve", AMAL,
            forbid_reg_ids=(AMAL_TATAWUI,),
            why="The Gate-1b case. SQL luna_normalize_ar does NOT drop a leading «ال», so "
                "the 1000-pt pin never fires (measured 3.14 vs 3.08 rank-2 — a 1.9% gap). "
                "Only the strict Python probe recovers this."),
    Fixture("reg-08", "tp_full_reg", "نظام العمل السعودي", "regs", "resolve", AMAL,
            forbid_reg_ids=(AMAL_TATAWUI,),
            why="Superfluous qualifier. Plan measures coverage 0.67 → should clear the "
                "0.60 floor at medium confidence."),
    Fixture("reg-09", "tp_full_reg", "نظام المنافسات والمشتريات الحكومية", "regs", "resolve", MUNAFASAT,
            why="The plan's OWN flagship example (§1). The corpus title is «نظام المنافسات "
                "و المشتريات الحكومية» — with a SPACED «و». Tests whether tokenization "
                "survives a whitespace difference the user cannot see."),
    Fixture("reg-10", "tp_full_reg", "نظام العمل التطوعي السعودي", "regs", "resolve", AMAL_TATAWUI,
            forbid_reg_ids=(AMAL,),
            why="SINGLETON TRAP, positive half. Plan: 1 row, score 14.30, coverage 0.75 → "
                "resolve. Paired with fp-04 which scores HIGHER and must refuse."),

    # ─────────────────────────────────────────────────────────────────────────
    # CLASS: fp_absent — laws that do not exist. Refusing is the deliverable.
    # ─────────────────────────────────────────────────────────────────────────
    Fixture("fp-01", "fp_absent", "نظام الفساد المالي والإداري", "regs", "refuse", "",
            forbid_reg_ids=(RAQABA,),
            why="THE headline FP. Scores 14.79 — higher than EVERY correct non-exact "
                "answer (3.14–8.37) — at coverage 0.50. A score floor would invert; "
                "coverage must refuse it."),
    Fixture("fp-02", "fp_absent", "نظام حماية الفضاء السيبراني الوطني", "regs", "refuse", "",
            why="Second absent law. Plan: score 12.52, coverage 0.20."),
    Fixture("fp-03", "fp_absent", "تعميم التسجيل العقاري", "circulars", "refuse", "",
            why="Absent تعميم. Plan: score 8.37, coverage 0.33. Circulars are 100% "
                "BM25-covered, so a miss here is a ranking failure, not a coverage gap."),
    Fixture("fp-04", "fp_absent", "نظام الفساد المالي والإداري", "article", "refuse", "",
            article_number="5",
            why="SINGLETON TRAP, negative half — same string as fp-01 but down the ARTICLE "
                "path, where a wrong parent silently yields a real article of the wrong law."),
    Fixture("fp-05", "fp_absent", "نظام الذكاء الاصطناعي السعودي", "regs", "refuse", "",
            why="Plausible-sounding absent law with a high-IDF rare token («الاصطناعي») — "
                "exactly the shape BM25 over-scores."),

    # ─────────────────────────────────────────────────────────────────────────
    # CLASS: fp_lookalike — three real, distinct documents whose names nest.
    # Cross-resolution here is the worst failure mode in the family: the user
    # gets a confident answer about the wrong law.
    # ─────────────────────────────────────────────────────────────────────────
    Fixture("lk-01", "fp_lookalike", "نظام العمل التطوعي", "regs", "resolve", AMAL_TATAWUI,
            forbid_reg_ids=(AMAL,),
            why="Exact title of the LONGER name. Must NOT collapse onto نظام العمل, whose "
                "shorter-title tiebreak actively pulls the other way."),
    Fixture("lk-02", "fp_lookalike", "اللائحة التنفيذية لنظام العمل", "regs", "resolve", AMAL_LAIHA,
            forbid_reg_ids=(AMAL,),
            why="The executive regulation, not the law. doc_type_bucket gives «لائحة» a "
                "+0.05 nudge — measure whether that is enough."),
    Fixture("lk-03", "fp_lookalike", "نظام التنفيذ أمام ديوان المظالم", "regs", "resolve",
            TANFEETH_MATHALIM, forbid_reg_ids=(TANFEETH,),
            why="Exact title, nests «نظام التنفيذ» whole."),
    Fixture("lk-04", "fp_lookalike", "نظام التنفيذ", "regs", "resolve", TANFEETH,
            forbid_reg_ids=(TANFEETH_MATHALIM,),
            why="The reverse direction of lk-03."),

    # ─────────────────────────────────────────────────────────────────────────
    # CLASS: fp_described — a described SUBSET, not a named object. §1.1 Test 1
    # says the router should never send these here; when one leaks through,
    # resolving it to a document is the failure the abort path exists for.
    # ─────────────────────────────────────────────────────────────────────────
    Fixture("desc-01", "fp_described", "المواد اللي تبين الشرط التعسفي", "regs", "refuse", "",
            why="The plan's own example. Only 3 of المعاملات المدنية's 716 مواد contain "
                "«تعسف» literally — it is a concept, not a string."),
    Fixture("desc-02", "fp_described", "الأحكام المتعلقة بفسخ عقد الإيجار", "regs", "refuse", "",
            why="«الأحكام المتعلقة بـ» is a named narrowing trigger in the router prompt."),
    Fixture("desc-03", "fp_described", "تطبيقات نظام العمل", "regs", "refuse", "",
            why="«تطبيقات» + a real law name. The law name alone must not carry it to a "
                "resolve — the object asked for is not the نظام."),
    Fixture("desc-04", "fp_described", "نظام المعاملات المدنية عن علاقة الإيجار", "regs", "refuse", "",
            why="THE dangerous pair, handed to the resolver verbatim. Contains an exact "
                "title as a strict prefix — measures whether the trailing qualifier "
                "survives normalization or is simply diluted away."),

    # ─────────────────────────────────────────────────────────────────────────
    # CLASS: must_ask — the corpus itself cannot decide.
    # ─────────────────────────────────────────────────────────────────────────
    Fixture("ask-01", "must_ask", "اللائحة التنفيذية", "regs", "ask", "",
            why="Dozens of executive regulations share this exact prefix. Committing to "
                "one is a coin flip."),
    Fixture("ask-02", "must_ask", "نظام الإقامة المميزة", "regs", "ask", "",
            why="Two real documents («نظام الإقامة المميزة» and «اللائحة التنفيذية لنظام "
                "الإقامة المميزة») plus «تنظيم مركز الإقامة المميزة». Labeled ask because "
                "an exact-title pin on the first is ALSO defensible — reported either way."),
    Fixture("ask-03", "must_ask", "حكم المحكمة التجارية في نزاع التوريد", "judgments", "ask", "",
            why="Plan measured top-6 inside 3.3% over near-identical titles. "
                "_NO_COVERAGE_RESOLVE should force a candidate table, never a winner."),
    Fixture("ask-04", "must_ask", "نزاع تجاري توريد", "judgments", "ask", "",
            why="Second judgment control — top-6 inside 1.2%."),

    # ─────────────────────────────────────────────────────────────────────────
    # CLASS: coverage_probe — non-regs types, to check the ladder's asymmetry
    # actually holds (services 2.1% BM25, circulars 100%).
    # ─────────────────────────────────────────────────────────────────────────
    Fixture("cov-01", "coverage_probe", "اصدار سجل تجاري", "services", "resolve", "",
            why="Plan measured coverage 0.67 → resolve, via the ILIKE rung (BM25 holds "
                "100 of 4,746 services). expect_reg_id blank: any commercial-register "
                "service is acceptable; graded by hand."),
    Fixture("cov-02", "coverage_probe", "خدمة اصدار رخصة بناء وهمية", "services", "refuse", "",
            why="Absent service. The 2.1% BM25 corpus makes a confident wrong answer cheap."),
]


def by_class() -> dict[str, list[Fixture]]:
    """Group the fixtures by reporting bucket, preserving declaration order."""
    out: dict[str, list[Fixture]] = {}
    for f in FIXTURES:
        out.setdefault(f.cls, []).append(f)
    return out
