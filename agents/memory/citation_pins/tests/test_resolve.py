"""Which citations of an attachment are pinned — decided by code (plan §4.6.3, §7.1).

Synthetic text only. These tests pin the rule that keeps a WRONG law's article from
being printed under a segment: only a checkable link is ``pinned``.
"""
from __future__ import annotations

import pytest

from agents.memory.citation_pins.resolve import (
    MEDIUM,
    NOT_IN_LIBRARY,
    OTHER_TARGET,
    PINNED,
    UNRESOLVED,
    DocFacts,
    RegFact,
    all_number_keys,
    candidate_regulations,
    counts,
    number_keys,
    resolve_citations,
)
from agents.tool_repository.citation_scan import TitleIndex, scan_citations

LABOR, PROC, PROC_EXEC, CIVIL, SOCIAL, DRAFT, EMPTY, DUP_OLD, DUP_NEW = (
    "r-labor", "r-proc", "r-proc-exec", "r-civil", "r-social", "r-draft", "r-empty", "r-dup-old", "r-dup-new",
)

TITLES = TitleIndex([
    (LABOR, "نظام العمل"),
    (PROC, "نظام المرافعات الشرعية"),
    (CIVIL, "نظام المعاملات المدنية"),
    (SOCIAL, "نظام التأمينات الاجتماعية"),
    (DRAFT, "نظام التجربة المقترح"),
    (EMPTY, "نظام الفراغ التجريبي"),
    (DUP_OLD, "نظام الشركات"),
    (DUP_NEW, "نظام الشركات"),
    ("r-board", "نظام المرافعات أمام ديوان المظالم"),
])

REGS = {
    LABOR: RegFact(LABOR, "نظام العمل", "in_force_amended"),
    PROC: RegFact(PROC, "نظام المرافعات الشرعية", "in_force"),
    PROC_EXEC: RegFact(PROC_EXEC, "اللائحة التنفيذية لنظام المرافعات الشرعية", "in_force"),
    CIVIL: RegFact(CIVIL, "نظام المعاملات المدنية", "in_force"),
    SOCIAL: RegFact(SOCIAL, "نظام التأمينات الاجتماعية", "in_force"),
    DRAFT: RegFact(DRAFT, "نظام التجربة المقترح", "consultation_ended"),
    EMPTY: RegFact(EMPTY, "نظام الفراغ التجريبي", "in_force", has_articles=False),
    DUP_OLD: RegFact(DUP_OLD, "نظام الشركات", "cancelled"),
    DUP_NEW: RegFact(DUP_NEW, "نظام الشركات", "in_force"),
    "r-board": RegFact("r-board", "نظام المرافعات أمام ديوان المظالم", "in_force"),
}

ARTICLES = {
    (LABOR, "77"): "a-labor-77", (LABOR, "80"): "a-labor-80", (LABOR, "74"): "a-labor-74",
    (PROC, "76"): "a-proc-76", (PROC_EXEC, "76/1"): "a-exec-76-1", (PROC_EXEC, "5"): "a-exec-5",
    (CIVIL, "94"): "a-civil-94", (CIVIL, "107"): "a-civil-107",
    (SOCIAL, "5"): "a-social-5", (DRAFT, "3"): "a-draft-3",
    (DUP_OLD, "5"): "a-old-5", (DUP_NEW, "5"): "a-new-5",
}


def facts(**overrides) -> DocFacts:
    return DocFacts(titles=TITLES, regs=overrides.get("regs", REGS), articles=overrides.get("articles", ARTICLES),
                    executive_of=overrides.get("executive_of", {PROC: [PROC_EXEC]}))


def resolve(text: str, doc_type: str = "memo", **overrides):
    scan = scan_citations(text, doc_type, TITLES)
    return scan, resolve_citations(scan, facts(**overrides))


def only(text: str, **overrides):
    _, out = resolve(text, **overrides)
    assert len(out) == 1, [(c.article_number, c.status) for c in out]
    return out[0]


# --------------------------------------------------------------------------- #
# Pinned — the link can be checked
# --------------------------------------------------------------------------- #


def test_law_in_the_same_phrase_is_pinned():
    c = only("استناداً إلى المادة (77) من نظام العمل فإن المدعي يستحق التعويض.")
    assert (c.status, c.regulation_id, c.article_id, c.decided_by) == (PINNED, LABOR, "a-labor-77", "code")
    assert c.law_as_written == "نظام العمل" and c.article_number == "77"


def test_every_number_of_a_mention_gets_its_own_row_at_the_same_place():
    text = "وفقاً للمادتين 77 و80 من نظام العمل."
    _, out = resolve(text)
    assert [(c.seq, c.article_number, c.status) for c in out] == [(1, "77", PINNED), (2, "80", PINNED)]
    assert out[0].char_start == out[1].char_start == text.index("للمادتين")


def test_words_and_indic_digits_reach_the_same_article():
    assert only("المادة السابعة والسبعون من نظام العمل").article_id == "a-labor-77"
    assert only("المادة ٧٧ من نظام العمل").article_id == "a-labor-77"


def test_back_reference_with_one_law_in_reach_is_pinned():
    _, out = resolve("نصت المادة 77 من نظام العمل على التعويض، وكذلك المادة 80 منه.")
    assert [(c.article_number, c.status, c.regulation_id) for c in out] == [("77", PINNED, LABOR), ("80", PINNED, LABOR)]


def test_the_documents_only_law_is_pinned():
    c = only("ينظم نظام العمل العلاقة بين الطرفين.\n\nوقد خالف المدعى عليه المادة 77.")
    assert (c.status, c.regulation_id) == (PINNED, LABOR)


def test_executive_regulation_through_its_parent_law():
    _, out = resolve("المادة 76 من نظام المرافعات الشرعية والمادة 5 من لائحته التنفيذية.")
    assert [(c.article_number, c.status, c.regulation_id) for c in out] == [
        ("76", PINNED, PROC), ("5", PINNED, PROC_EXEC),
    ]


def test_a_compound_number_stored_as_written_is_pinned():
    _, out = resolve("المادة 76 من نظام المرافعات الشرعية والمادة 76/1 من لائحته التنفيذية.")
    assert (out[1].status, out[1].article_id) == (PINNED, "a-exec-76-1")


def test_two_regulations_under_one_title_take_the_one_in_force():
    c = only("المادة 5 من نظام الشركات")
    assert (c.status, c.regulation_id, c.article_id) == (PINNED, DUP_NEW, "a-new-5")


# --------------------------------------------------------------------------- #
# Medium — found, but never delivered
# --------------------------------------------------------------------------- #


def test_a_law_named_elsewhere_among_several_is_only_medium():
    text = ("وقد سبق بيان أن نظام العمل ينظم ذلك، وأن نظام المرافعات الشرعية يحكم الإجراءات.\n\n"
            "وعليه فإن المادة 76 واضحة.")
    c = only(text)
    assert (c.status, c.reason) == (MEDIUM, "link_is_a_guess")
    assert c.regulation_id == PROC                       # kept for the record, not delivered


def test_a_short_name_of_one_title_is_only_medium():
    c = only("المادة 5 من نظام التأمينات")
    assert (c.status, c.reason, c.regulation_id) == (MEDIUM, "short_form_of_one_title", SOCIAL)


def test_a_compound_number_that_falls_back_is_only_medium():
    # the paragraph first: which of the two is the article is a guess
    c = only("المادة 2/77 من نظام العمل")
    assert (c.status, c.reason, c.article_id) == (MEDIUM, "compound_number_fallback", "a-labor-77")


# --------------------------------------------------------------------------- #
# Never pinned
# --------------------------------------------------------------------------- #


def test_a_law_that_is_not_in_the_library():
    c = only("المادة 13 من نظام ديوان المظالم")
    assert (c.status, c.regulation_id, c.article_id) == (NOT_IN_LIBRARY, None, None)


def test_a_name_that_opens_several_titles_is_not_chosen():
    c = only("المادة 76 من نظام المرافعات")
    assert (c.status, c.reason) == (UNRESOLVED, "name_opens_several_titles")


def test_the_contract_is_not_a_law():
    c = only("بما ورد في المادة الثالثة من العقد المبرم بين الطرفين")
    assert c.status == OTHER_TARGET


@pytest.mark.parametrize("text,reason", [
    ("وقد خالف المدعى عليه المادة 77 مخالفة صريحة.", "no_law_named"),
    ("مع مراعاة المادة 5 من هذا النظام", "back_reference_without_law"),
    ("المادة 999 من نظام العمل", "article_not_in_law"),
    ("المادة 3 من نظام التجربة المقترح", "not_in_force"),
    ("المادة 4 من نظام الفراغ التجريبي", "law_has_no_article_text"),
    ("المواد من 10 إلى 60 من نظام العمل", "range_too_wide"),
    ("المادة 5 من نظام العمل والعمال القديم", "name_may_run_on"),
    ("المادة 14 من اللائحة التنفيذية لنظام العمل", "executive_regulation_not_found"),
])
def test_unresolved(text, reason):
    _, out = resolve(text)
    assert out and all((c.status, c.reason) == (UNRESOLVED, reason) for c in out)
    assert all(c.article_id is None or reason == "not_in_force" or c.status != PINNED for c in out)


def test_back_reference_with_several_laws_in_reach_is_not_pinned():
    c = only("يحكم نظام العمل العلاقة، ويحكم نظام المرافعات الشرعية الإجراءات، وتنص المادة 76 من النظام على ذلك.")
    assert (c.status, c.reason) == (UNRESOLVED, "back_reference_several_laws")


def test_the_documents_own_headings_produce_no_row():
    scan, out = resolve("المادة الأولى: تعريفات\nالمادة الثانية: مدة العقد\nتطبق المادة 77 من نظام العمل.")
    assert [c.article_number for c in out] == ["77"]
    assert counts(scan, out) == {"mentions": 1, "headings": 2, "pinned": 1, "medium": 0, "not_in_library": 0}


def test_ids_are_kept_only_for_pinned_and_medium_rows():
    _, out = resolve("المادة 999 من نظام العمل والمادة 13 من نظام ديوان المظالم والمادة 77 من نظام العمل")
    for c in out:
        if c.status in (PINNED, MEDIUM):
            assert c.regulation_id and c.article_id
        else:
            assert c.regulation_id is None and c.article_id is None
        row = c.row("file-1")
        assert row["item_id"] == "file-1" and row["decided_by"] == "code"


# --------------------------------------------------------------------------- #
# What the caller must load
# --------------------------------------------------------------------------- #


def test_candidate_regulations():
    scan = scan_citations("المادة 77 من نظام العمل والمادة 5 من نظام التأمينات والمادة 1 من نظام ديوان المظالم",
                          "memo", TITLES)
    assert candidate_regulations(scan, TITLES) == {LABOR, SOCIAL}


def test_number_keys():
    assert number_keys("77") == (["77"], [])
    exact, fallback = number_keys("76/1")
    assert exact[:2] == ["76/1", "76-1"] and "1/76" in exact and fallback == ["76"]
    assert number_keys("1-1") == (["1-1", "1/1"], ["1"])
    assert all_number_keys(["77", "76/1"])[:2] == ["77", "76/1"]


# --------------------------------------------------------------------------- #
# Found on a real judgment (2026-11-23)
# --------------------------------------------------------------------------- #


def test_the_court_style_double_number_is_pinned():
    c = only("تدخل في اختصاص المحكمة بحسب المادة (77) السابعة والسبعين من نظام العمل، الصادر بالمرسوم الملكي.")
    assert (c.status, c.article_id, c.article_number) == (PINNED, "a-labor-77", "77")


def test_the_same_law_after_a_double_number_is_pinned():
    _, out = resolve(
        "بحسب المادة (77) السابعة والسبعين من نظام العمل، ووفقاً للفقرة (الأولى) من المادة (80) الثمانين من ذات النظام."
    )
    assert [(c.article_number, c.status, c.regulation_id) for c in out] == [("77", PINNED, LABOR), ("80", PINNED, LABOR)]


def test_a_number_written_two_ways_that_disagree_is_never_pinned():
    c = only("بحسب المادة (78) السابعة والسبعين من نظام العمل")
    assert (c.status, c.reason, c.article_id) == (UNRESOLVED, "number_written_twice_disagrees", None)


def test_article_then_paragraph_is_the_article():
    # «77/2» in a law with plainly numbered articles: article 77, paragraph 2.
    c = only("وبناء على المادة (77/2) من نظام العمل قررت الدائرة")
    assert (c.status, c.article_id, c.article_number) == (PINNED, "a-labor-77", "77/2")


@pytest.mark.parametrize("number", ["2/77", "77/80", "77/25"])
def test_other_compound_shapes_stay_a_guess(number):
    c = only(f"وبناء على المادة ({number}) من نظام العمل قررت الدائرة")
    assert c.status != PINNED


def test_a_clause_quoted_from_a_contract_produces_no_row():
    scan, out = resolve("نصت المادة 77 من نظام العمل. وقد جاء في العقد: (المادة الخامسة: التزامات المستأجر).")
    assert [c.article_number for c in out] == ["77"]
    assert counts(scan, out)["headings"] == 1


def test_a_name_that_differs_only_by_al_is_a_guess_not_a_missing_law():
    # «نظام عمل» for «نظام العمل» — found on a real hearing record («نظام اثبات»).
    c = only("واستنادا على ما جاء في المادة السابعة والسبعين من نظام عمل ما نصه: يجوز")
    assert (c.status, c.reason, c.regulation_id) == (MEDIUM, "name_differs_by_al", LABOR)


def test_al_is_ignored_only_when_exactly_one_title_fits():
    assert TITLES.spelled_loosely(("نظام", "عمل")) == [("نظام", "العمل")]
    assert TITLES.spelled_loosely(("نظام", "العمل")) == []            # the title itself is not a loose match
    assert TITLES.spelled_loosely(("نظام", "فضاء")) == []
    c = only("المادة 5 من نظام فضاء خارجي")
    assert c.status == NOT_IN_LIBRARY
