"""Axis 2 — the labeled routing fixture set.

The router prompt section ("When to route to simple_search",
``agents/router/router.py:227-256``) encodes two tests, and this set scores each
of the four types the task named, plus the comparison guard.

THE PAIR THAT MATTERS
---------------------
Types 1 and 2 differ only by a trailing qualifier. «اش يقول نظام المعاملات
المدنية» is simple_search; «اش يقول نظام المعاملات المدنية **عن علاقة الإيجار**»
is deep_search. Same opening, same law, three trailing words apart. A model that
pattern-matches «اش يقول نظام X» routes them identically, so the ``pair_*``
fixtures below are deliberately over-weighted: five variants of the narrowing
shape against five of the whole-document shape, built on the SAME laws so the
only moving part is the qualifier.

Each fixture carries ≥5 paraphrases so one lucky completion cannot read as a
pass. Labels are the plan's, not mine — every one traces to a row in §1.1 or to
the router prompt's own examples.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteFixture:
    rid: str
    cls: str
    expect: str            # simple_search | deep_search
    paraphrases: tuple[str, ...]
    why: str = ""


ROUTE_FIXTURES: list[RouteFixture] = [
    # ── TYPE 2 — whole named object, wants to see it → simple_search ────────
    RouteFixture(
        "t2-whole", "type2_whole_object", "simple_search",
        (
            "اش يقول نظام العمل",
            "اش يقول نظام المعاملات المدنية، اهم احكامه",
            "اش نظام المنافسات والمشتريات؟",
            "ابغى اطلع على نظام المرور",
            "وريني نظام الشركات",
            "اش هو نظام التنفيذ",
        ),
        why="Router prompt rows 1–2 verbatim plus the plan §1 flagship. The whole "
            "document is the object; no qualifier narrows inside it.",
    ),

    # ── TYPE 1 — the SAME opening + a narrowing qualifier → deep_search ─────
    RouteFixture(
        "t1-narrow", "type1_narrowed", "deep_search",
        (
            "اش يقول نظام المعاملات المدنية عن علاقة الإيجار",
            "اش يقول نظام العمل عن الفصل التعسفي",
            "اش يقول نظام الشركات عن مسؤولية الشريك",
            "اش يقول نظام المرور فيما يخص المخالفات المرورية",
            "اش يقول نظام التنفيذ عن الحجز على الرواتب",
            "اش يقول نظام المعاملات المدنية بخصوص الأحكام المتعلقة بالتقادم",
        ),
        why="THE DANGEROUS PAIR. Identical opening to t2-whole, one trailing "
            "qualifier apart. «عن كذا» / «فيما يخص» / «الأحكام المتعلقة بـ» are "
            "named narrowing triggers in the prompt.",
    ),

    # ── TYPE 1b — «تطبيقات» and friends → deep_search ──────────────────────
    RouteFixture(
        "t1-apps", "type1_applications", "deep_search",
        (
            "اعطيني تطبيقات نظام العمل",
            "ابغى تطبيقات نظام المعاملات المدنية في المحاكم",
            "وش تطبيقات نظام التنفيذ عمليا",
            "اعطيني السوابق القضائية على نظام العمل",
            "كيف طبقت المحاكم نظام الشركات",
        ),
        why="Router prompt row 4. Applications need rulings too — several sources, "
            "not one document.",
    ),

    # ── TYPE 4 — article by number, wants its text → simple_search ──────────
    RouteFixture(
        "t4-article", "type4_article_identity", "simple_search",
        (
            "اش هي المادة 67 من نظام التنفيذ",
            "اعطيني نص المادة 81 من نظام العمل",
            "وش تقول المادة الخامسة من نظام الشركات",
            "ابغى نص المادة 74 من نظام العمل",
            "اقرأ لي المادة 1 من نظام المعاملات المدنية",
        ),
        why="Test 2, identity side — they want the article's TEXT.",
    ),

    # ── TYPE 3 — article by number, but APPLIED to them → deep_search ───────
    RouteFixture(
        "t3-applied", "type3_article_applied", "deep_search",
        (
            "اتطبقت عليّ المادة 67 من نظام التنفيذ وصار لي شهرين موقوف عن السفر، وش اسوي",
            "اتنفذت عليّ المادة 67 من نظام التنفيذ وصار لي ضرر، ايش حقوقي",
            "أنا خايفة من تطبيق المادة 67 عليّ، وش الحل",
            "صاحب العمل فصلني واستشهد بالمادة 80 من نظام العمل، هل يحق له؟",
            "رفعوا عليّ دعوى بالمادة 74 من نظام العمل، كيف أدافع عن نفسي",
        ),
        why="Test 2, application side. All five NAME an article and all five are "
            "deep_search — a cited number is never by itself a reason to route here.",
    ),

    # ── COMPARISON — explicitly never simple_search ────────────────────────
    RouteFixture(
        "cmp", "comparison", "deep_search",
        (
            "قارن نظام العمل بنظام العمل التطوعي",
            "وش الفرق بين نظام العمل ونظام العمل التطوعي",
            "قارن بين نظام الشركات ونظام المعاملات المدنية في مسؤولية الشريك",
            "ايهما اشد نظام المرور ولا نظام التنفيذ في العقوبات",
            "اعطيني مقارنة بين نظام التنفيذ ونظام التنفيذ أمام ديوان المظالم",
        ),
        why="§0 D6 + the prompt's «Never simple_search» block. This family opens "
            "one object at a time.",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# ADVERSARIAL EXTENSION — added after the first pass scored 6/6 and 6/6 on the
# obvious forms of the dangerous pair. Passing the textbook shapes proves the
# prompt's EXAMPLES were learned; it does not prove the RULE generalizes. These
# two classes attack from both sides:
#
#   adv_narrow — narrowing qualifiers the prompt does NOT enumerate («بخصوص»،
#     «حول»، «في موضوع») or that carry no trigger word at all (a bare condition).
#     A model matching on the listed trigger strings rather than the underlying
#     addressability test fails here.
#
#   adv_whole — whole-document requests dressed in language that LOOKS like
#     narrowing («اهم نقاطه»، «ملخص»، «محتويات»). This is the OPPOSITE error and
#     the more expensive one: over-triggering deep_search on a legitimate lookup
#     burns minutes and the user's points, which is the entire reason the family
#     exists.
# ─────────────────────────────────────────────────────────────────────────────

ADVERSARIAL_FIXTURES: list[RouteFixture] = [
    RouteFixture(
        "adv-narrow", "adv_narrowed_unlisted", "deep_search",
        (
            "اش يقول نظام العمل بخصوص الإجازات السنوية",
            "ايش نص نظام المعاملات المدنية حول الرهن",
            "نظام العمل وش يقول في موضوع ساعات العمل",
            "اش يقول نظام الشركات لما يكون احد الشركاء متوفى",
            "نظام المرور وش فيه عن حوادث الدهس",
            "وش موقف نظام التنفيذ من الكفالة الغرمية",
        ),
        why="Narrowing WITHOUT the enumerated trigger words. «بخصوص» / «حول» / "
            "«في موضوع» are not in the prompt's list; row 4 carries no trigger "
            "word at all, only a condition. Tests the RULE, not the examples.",
    ),
    RouteFixture(
        "adv-whole", "adv_whole_overview", "simple_search",
        (
            "اش يقول نظام العمل، اهم نقاطه",
            "اعطيني ملخص نظام المرور",
            "وش محتويات نظام الشركات",
            "ابغى نظام التنفيذ كامل",
            "اش موضوع نظام المعاملات المدنية بشكل عام",
        ),
        why="Whole-document overview requests that LOOK narrowed. The prompt "
            "explicitly blesses «اهم احكامه» as an overview; these test whether "
            "that generalizes to «اهم نقاطه» / «ملخص» / «محتويات». A miss here is "
            "an over-trigger of deep_search on the family's core use case.",
    ),
]

ALL_FIXTURES: list[RouteFixture] = ROUTE_FIXTURES + ADVERSARIAL_FIXTURES


def total_calls(fixtures: list[RouteFixture] | None = None) -> int:
    return sum(len(f.paraphrases) for f in (fixtures or ALL_FIXTURES))
