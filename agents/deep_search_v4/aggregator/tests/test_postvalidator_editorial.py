"""Post-validator behaviour on the editorial (public-blog) variants.

Plan: ``.claude/plans/blog_subjects.md`` §6 — *"any check assuming the in-app
answer shape — section headers, opening form, length bands — will fight the
article form and burn a retry on every editorial job. Loosen or branch those
checks; do **not** weaken the citation checks."*

Two checks were branched, and this module pins both plus the thing that must
NOT have moved: the citation gate.
"""
from __future__ import annotations

import pytest

from agents.deep_search_v4.aggregator.postvalidator import (
    check_query_anchoring,
    check_structure,
    extract_cited_numbers,
)


EDITORIAL_KEYS = [
    "prompt_editorial_case",
    "prompt_editorial_reg_compliance",
    "prompt_editorial_full",
]


# A well-formed editorial article: H1 headline first line, two-paragraph lede,
# self-describing ordinal sections, closing «## الخلاصة».
ARTICLE_GOOD = """# إصلاح المركبة قبل تقدير التلفيات

يواجه كثير من المؤمَّن لهم في السعودية معضلة عملية بعد وقوع حادث مروري، إذ تطول
إجراءات التقدير بينما تبقى المركبة معطلة عن الاستعمال.

الجواب المختصر أن النظام لا يمنع الإصلاح المسبق، لكنه يعلّق أثره على إثبات التلف [1].

## أولاً: النظام لا يمنع الإصلاح المسبق
لا يوجد نص يحظر المبادرة إلى الإصلاح قبل التقدير [1,2].

## ثانياً: عبء الإثبات ينتقل إلى المؤمَّن له
متى بادر إلى الإصلاح لزمه إثبات مقدار التلف [2].

## الخلاصة
الإصلاح المسبق جائز مع تحمل تبعة الإثبات [1,2].
"""


# ---------------------------------------------------------------------------
# check_structure — the article contract, not the answer contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", EDITORIAL_KEYS)
def test_article_shape_passes_structure(key: str) -> None:
    ok, notes = check_structure(ARTICLE_GOOD, key)
    assert ok, f"notes={notes}"
    assert notes == [], f"unexpected notes={notes}"


@pytest.mark.parametrize("key", EDITORIAL_KEYS)
def test_editorial_is_not_flagged_as_unknown_prompt_key(key: str) -> None:
    """Before the branch existed, every editorial job carried an alarming
    'unknown prompt_key' note in its report."""
    _, notes = check_structure(ARTICLE_GOOD, key)
    assert not any("unknown prompt_key" in n for n in notes)


def test_in_app_answer_shape_would_fail_the_article_contract() -> None:
    """Proof the branch is doing real work rather than rubber-stamping: the
    in-app opening («## الخلاصة» first, no headline) is NOT a valid article."""
    in_app_shape = """## الخلاصة
الإصلاح المسبق جائز [1].

## الأساس المرجعي
نصت المادة على ذلك [1].
"""
    ok, notes = check_structure(in_app_shape, "prompt_editorial_case")
    assert not ok
    assert any("headline" in n for n in notes)


def test_missing_headline_fails() -> None:
    body = ARTICLE_GOOD.split("\n", 1)[1].lstrip("\n")
    ok, notes = check_structure(body, "prompt_editorial_case")
    assert not ok
    assert any("first line is not an '# ' headline" in n for n in notes)


def test_second_h1_fails() -> None:
    """A stray H1 survives the publish path's first-line strip and
    double-renders in ``BlogArticleView``."""
    body = ARTICLE_GOOD.replace(
        "## ثانياً: عبء الإثبات ينتقل إلى المؤمَّن له",
        "# ثانياً: عبء الإثبات ينتقل إلى المؤمَّن له",
    )
    ok, notes = check_structure(body, "prompt_editorial_case")
    assert not ok
    assert any("H1 headings" in n for n in notes)


def test_not_closing_on_khulasa_fails() -> None:
    body = ARTICLE_GOOD.replace("## الخلاصة", "## خاتمة")
    ok, notes = check_structure(body, "prompt_editorial_case")
    assert not ok
    assert any("does not close on" in n for n in notes)


def test_bare_ordinal_heading_is_a_soft_note_only() -> None:
    """Headings become TOC entries, so «أولاً» alone is useless — but it is a
    quality signal, not a structural failure."""
    body = ARTICLE_GOOD.replace("## أولاً: النظام لا يمنع الإصلاح المسبق", "## أولاً")
    ok, notes = check_structure(body, "prompt_editorial_case")
    assert ok, f"notes={notes}"
    assert any("self-describing" in n for n in notes)


def test_in_app_keys_are_untouched_by_the_editorial_branch() -> None:
    """``prompt_mode_*`` keeps its pre-existing lenient fallthrough exactly."""
    ok, notes = check_structure(ARTICLE_GOOD, "prompt_mode_case")
    assert ok
    assert any("unknown prompt_key" in n for n in notes)


# ---------------------------------------------------------------------------
# check_query_anchoring — exempt for editorial
# ---------------------------------------------------------------------------


# A genuinely de-identified opening: no second person, no first-person
# possessive, and none of the question's own vocabulary — the class of people
# is named instead of the asker. This is what §6(a) asks for, and it is
# exactly what ``check_query_anchoring`` was built to penalise.
#
# NOTE: an editorial article does not *always* fail anchoring — a headline
# often reuses the topic's words by coincidence and scores a passing hit. The
# exemption is not a workaround for a guaranteed failure; it is the removal of
# a signal that is meaningless on this path, since restating the question is
# forbidden here and required in-app.
DEIDENTIFIED_OPENING = (
    "# متى ينتقل عبء الإثبات إلى المؤمَّن له\n\n"
    "يواجه كثير من المؤمَّن لهم في السعودية معضلة عملية بعد وقوع حادث مروري.\n"
)
RAW_QUESTION = "هل يجوز لي إصلاح سيارتي قبل أن تقدّر شركة التأمين التلفيات؟"


@pytest.mark.parametrize("key", EDITORIAL_KEYS)
def test_editorial_is_exempt_from_query_anchoring(key: str) -> None:
    """De-identification and query anchoring are in direct conflict: the
    article is instructed to open on the class of people, never to restate the
    question. Without the exemption every editorial job carries a false
    'anchoring weak' note."""
    assert check_query_anchoring(DEIDENTIFIED_OPENING, RAW_QUESTION, key) is True


def test_same_text_still_fails_anchoring_on_the_in_app_path() -> None:
    """The exemption is scoped to editorial keys — it must not leak."""
    assert check_query_anchoring(DEIDENTIFIED_OPENING, RAW_QUESTION, "prompt_mode_case") is False


def test_anchoring_default_arg_preserves_legacy_callers() -> None:
    """``prompt_key`` is optional so ``scripts/`` and the v3 audit path keep
    working; omitting it must behave exactly as before."""
    assert check_query_anchoring(DEIDENTIFIED_OPENING, RAW_QUESTION) is False


# ---------------------------------------------------------------------------
# The citation gate — explicitly NOT loosened
# ---------------------------------------------------------------------------


def test_citation_extraction_is_shape_agnostic() -> None:
    """The two gates that trigger a correction retry (citation_ok,
    gap_honesty_ok) read the citations, not the headings — so the article form
    cannot affect them, and nothing here relaxes them."""
    assert extract_cited_numbers(ARTICLE_GOOD) == [1, 2]


def test_non_contiguous_citation_numbers_are_accepted() -> None:
    """Numbers are pre-assigned to the whole reference set; gaps are correct
    and must never be renumbered."""
    md = "# عنوان\n\nنص [2] ونص آخر [4,5] وثالث [7] ورابع [10].\n\n## الخلاصة\nخلاصة [2].\n"
    assert extract_cited_numbers(md) == [2, 4, 5, 7, 10]
