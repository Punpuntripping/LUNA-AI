"""The model-free citation scan (``.claude/plans/completion_search.md`` §4.6.1).

Every text here is SYNTHETIC — written for the test, never taken from a user's file.
The rules these tests pin are the ones that decide what gets pinned under an
attachment's segment: which article number was read, which law it is tied to, and
that the document's own headings are never taken for citations.
"""
from __future__ import annotations

import pytest

from agents.tool_repository.citation_scan import (
    BASIS_BACK_REF,
    BASIS_NEAREST,
    BASIS_NO_LAW,
    BASIS_OTHER_TARGET,
    BASIS_SAME_PHRASE,
    BASIS_SAME_SENTENCE,
    BASIS_SINGLE_LAW,
    KIND_BACK_REF,
    KIND_EXEC_BACK_REF,
    KIND_NAMED,
    KIND_NOT_A_LAW,
    TitleIndex,
    fold,
    scan_citations,
)

TITLES = TitleIndex([
    ("r-labor", "نظام العمل"),
    ("r-proc", "نظام المرافعات الشرعية"),
    ("r-proc-exec", "اللائحة التنفيذية لنظام المرافعات الشرعية"),
    ("r-civil", "نظام المعاملات المدنية"),
    ("r-abuse", "نظام الحماية من الإيذاء"),
    ("r-tender", "نظام المنافسات و المشتريات الحكومية"),
    ("r-rules", "قواعد المرافعات والإجراءات أمام ديوان المظالم"),
    ("r-dup-1", "نظام الشركات"),
    ("r-dup-2", "نظام الشركات"),
    ("r-one-word", "الأنظمة"),
])


def scan(text: str, doc_type: str = "memo", titles: TitleIndex | None = TITLES):
    return scan_citations(text, doc_type, titles)


def one(text: str, **kw):
    result = scan(text, **kw)
    assert len(result.mentions) == 1, [m.numbers for m in result.mentions]
    return result, result.mentions[0]


def law_text(result, mention) -> str | None:
    return result.laws[mention.law].text if mention.law is not None else None


# --------------------------------------------------------------------------- #
# Article numbers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text,numbers,form", [
    ("المادة 77 من نظام العمل", ("77",), "digits"),
    ("المادة (77) من نظام العمل", ("77",), "digits"),
    ("المادة رقم (77) من نظام العمل", ("77",), "digits"),
    ("المادة ٧٧ من نظام العمل", ("77",), "indic"),
    ("المادة (۷۷) من نظام العمل", ("77",), "indic"),
    ("المادة السابعة والسبعون من نظام العمل", ("77",), "words"),
    ("المادة السابعة والسبعين من نظام العمل", ("77",), "words"),
    ("المادة الأولى من نظام العمل", ("1",), "words"),
    ("المادة العاشرة من نظام العمل", ("10",), "words"),
    ("المادة الحادية عشرة من نظام العمل", ("11",), "words"),
    ("المادة العشرون من نظام العمل", ("20",), "words"),
    ("المادة الحادية عشرة بعد المائة من نظام العمل", ("111",), "words"),
    ("المادة المائة والخامسة والعشرون من نظام العمل", ("125",), "words"),
    ("المادة الثلاثون بعد المائتين من نظام العمل", ("230",), "words"),
    ("الماده (5) من نظام العمل", ("5",), "digits"),
    ("المَادَّة (٧٧) من نِظَام العَمَل", ("77",), "indic"),
    ("المـــادة 12 من نظام العمل", ("12",), "digits"),
])
def test_one_article_number(text, numbers, form):
    _, m = one(text)
    assert m.numbers == numbers
    assert m.number_form == form


def test_two_ordinals_are_two_articles_not_a_sum():
    # «الثانية والثالثة» is articles 2 and 3 — a plain sum would invent article 5.
    _, m = one("المادة الثانية والثالثة من نظام العمل")
    assert m.numbers == ("2", "3")


def test_teens_in_a_list():
    _, m = one("المادتين الثانية عشرة والثالثة عشرة من نظام العمل")
    assert m.numbers == ("12", "13")


@pytest.mark.parametrize("text,numbers", [
    ("المادتين 76 و77 من نظام العمل", ("76", "77")),
    ("المادتان (76) و (77) من نظام العمل", ("76", "77")),
    ("المواد 10، 11، 12 من نظام العمل", ("10", "11", "12")),
    ("المواد من 10 إلى 15 من نظام العمل", ("10", "11", "12", "13", "14", "15")),
    ("المواد (10-13) من نظام العمل", ("10", "11", "12", "13")),
    ("المواد الخامسة، والسادسة والسابعة من نظام العمل", ("5", "6", "7")),
    ("المواد من الخامسة إلى الثامنة من نظام العمل", ("5", "6", "7", "8")),
    ("المادة 76 و77 من نظام العمل", ("76", "77")),
])
def test_lists_and_ranges(text, numbers):
    _, m = one(text)
    assert m.numbers == numbers
    assert not m.range_wide


def test_a_wide_range_keeps_only_its_ends():
    _, m = one("المواد من 10 إلى 60 من نظام العمل")
    assert m.numbers == ("10", "60")
    assert m.range_wide


def test_a_singular_head_does_not_swallow_a_following_number():
    # «و30 يوماً» is not an article: the list is accepted only when it runs into «من».
    _, m = one("طبقاً للمادة 77 و30 يوماً من تاريخ الإشعار")
    assert m.numbers == ("77",)


@pytest.mark.parametrize("text,number", [
    ("المادة 76/1 من نظام العمل", "76/1"),
    ("المادة (1-1) من نظام العمل", "1-1"),
    ("المادة 25 مكرر من نظام العمل", "25 مكرر"),
])
def test_compound_numbers_are_kept_as_written(text, number):
    _, m = one(text)
    assert m.numbers == (number,)


def test_compound_is_flagged():
    assert one("المادة 76/1 من نظام العمل")[1].compound
    assert not one("المادة 76 من نظام العمل")[1].compound


@pytest.mark.parametrize("text", [
    "ضبطت المادة المخدرة بحوزته",
    "المواد الغذائية المنتهية",
    "أي مادة من مواد العقد",
    "المادة التي استند إليها",
    "المادة 2500 من نظام العمل",       # not an article number
])
def test_not_a_mention(text):
    assert scan(text).mentions == []


def test_paragraph_reference_keeps_the_article():
    _, m = one("الفقرة (أ) من المادة 77 من نظام العمل")
    assert m.numbers == ("77",)
    assert m.basis == BASIS_SAME_PHRASE


def test_prefixes_on_the_head_word():
    text = "وفقاً للمادة 5 وبالمادة 6 والمادة 7 فالمادة 8 كالمادة 9 من نظام العمل"
    assert [m.numbers[0] for m in scan(text).mentions] == ["5", "6", "7", "8", "9"]


# --------------------------------------------------------------------------- #
# Positions
# --------------------------------------------------------------------------- #


def test_spans_point_into_the_original_text_through_tashkeel():
    text = "وَحَيْثُ نَصَّتِ المَادَّةُ (٧٧) مِنْ نِظَامِ العَمَلِ عَلَى ذَلِكَ"
    result, m = one(text)
    assert text[m.char_start:m.char_end] == "المَادَّةُ (٧٧)"
    law = result.laws[m.law]
    assert fold(text[law.char_start:law.char_end]) == fold("نظام العمل")
    assert law.text == text[law.char_start:law.char_end]


def test_paragraph_number_and_sentence():
    text = "تمهيد قصير.\n\nوقائع الدعوى طويلة.\nوحيث إن المادة 77 من نظام العمل تقرر التعويض. ثم جملة أخرى."
    _, m = one(text)
    assert m.para_n == 3
    sentence = text[m.sentence_start:m.sentence_end]
    assert "المادة 77" in sentence
    assert "تمهيد" not in sentence and "جملة أخرى" not in sentence
    assert len(sentence) <= 300


def test_sentence_is_capped():
    text = ("كلمة " * 200) + "المادة 77 من نظام العمل " + ("كلمة " * 200)
    _, m = one(text)
    assert m.sentence_end - m.sentence_start <= 300


# --------------------------------------------------------------------------- #
# Law references
# --------------------------------------------------------------------------- #


def test_a_title_stops_the_name():
    result, m = one("المادة 94 من نظام المعاملات المدنية الصادر بالمرسوم الملكي رقم م/191")
    assert law_text(result, m) == "نظام المعاملات المدنية"
    assert result.laws[m.law].in_library


def test_a_title_that_does_not_start_with_nizam():
    result, m = one("المادة 3 من قواعد المرافعات والإجراءات أمام ديوان المظالم")
    assert result.laws[m.law].in_library
    assert m.basis == BASIS_SAME_PHRASE


def test_title_with_a_detached_waw_matches_the_attached_spelling():
    result, m = one("المادة 12 من نظام المنافسات والمشتريات الحكومية")
    assert result.laws[m.law].in_library
    assert TITLES.ids(result.laws[m.law].key) == ("r-tender",)


def test_a_title_containing_min_is_not_cut():
    result, m = one("المادة الثانية من نظام الحماية من الإيذاء")
    assert law_text(result, m) == "نظام الحماية من الإيذاء"


def test_two_regulations_under_one_title():
    result, m = one("المادة 5 من نظام الشركات")
    assert TITLES.ids(result.laws[m.law].key) == ("r-dup-1", "r-dup-2")


def test_one_word_titles_are_never_matched():
    assert scan("راجع الأنظمة ذات العلاقة").laws == []


def test_a_name_that_is_not_in_the_library_is_still_listed():
    result, m = one("المادة 9 من نظام التوثيق التجريبي الصادر عام 1440")
    law = result.laws[m.law]
    assert law.kind == KIND_NAMED and not law.in_library
    assert law.text == "نظام التوثيق التجريبي"


def test_a_name_that_may_run_on_is_flagged():
    # The library title matched, but the document may name a longer, different law.
    result, _ = one("المادة 5 من نظام العمل والعمال القديم")
    assert result.laws[0].open_end
    result, _ = one("المادة 5 من نظام العمل والمادة 6 من نظام المرافعات الشرعية".split(" والمادة")[0])
    assert not result.laws[0].open_end


def test_without_a_title_index_head_words_still_work():
    result, m = one("المادة 77 من نظام العمل الصادر بالمرسوم", titles=None)
    assert law_text(result, m) == "نظام العمل"
    assert not result.laws[m.law].in_library


@pytest.mark.parametrize("text", [
    "قدم المدعي لائحة الدعوى",
    "اللائحة الاعتراضية المقدمة من المدعى عليه",
    "لائحة اعتراضية",
    "وردت لائحة الاستئناف",
])
def test_pleadings_are_not_regulations(text):
    result = scan(text)
    assert [law.kind for law in result.laws] == [KIND_NOT_A_LAW]
    assert result.named_laws == []


def test_public_order_is_not_a_law():
    assert scan("لمخالفته النظام العام والآداب").laws == []


def test_company_bylaws_are_not_a_law():
    result, m = one("المادة 12 من النظام الأساسي للشركة")
    assert result.laws[0].kind == KIND_NOT_A_LAW
    assert m.basis == BASIS_OTHER_TARGET


@pytest.mark.parametrize("text,kind", [
    ("من هذا النظام", KIND_BACK_REF),
    ("من النظام المذكور", KIND_BACK_REF),
    ("وفق النظام.", KIND_BACK_REF),
    ("من هذه اللائحة", KIND_BACK_REF),
    ("من اللائحة التنفيذية.", KIND_EXEC_BACK_REF),
    ("من لائحته التنفيذية", KIND_EXEC_BACK_REF),
])
def test_back_references(text, kind):
    assert [law.kind for law in scan(text).laws] == [kind]


def test_executive_regulation_named_in_full():
    result, m = one("المادة 76/1 من اللائحة التنفيذية لنظام المرافعات الشرعية")
    assert TITLES.ids(result.laws[m.law].key) == ("r-proc-exec",)
    assert not result.laws[m.law].exec_of


def test_executive_regulation_of_a_library_law():
    # No title «اللائحة التنفيذية لنظام العمل» in the index: it is found through its parent law.
    result, m = one("المادة 14 من اللائحة التنفيذية لنظام العمل الصادرة بقرار وزاري")
    law = result.laws[m.law]
    assert law.in_library and law.exec_of
    assert TITLES.ids(law.key) == ("r-labor",)
    assert m.exec_reg and m.basis == BASIS_SAME_PHRASE
    assert law.text == "اللائحة التنفيذية لنظام العمل"


def test_a_law_and_its_executive_regulation_are_two_laws():
    result = scan("ينظم نظام العمل ذلك، وتفصله اللائحة التنفيذية لنظام العمل.\n\nوخالف المادة 9.")
    assert len(result.distinct_named()) == 2
    assert result.mentions[0].basis == BASIS_NEAREST


@pytest.mark.parametrize("text", [
    "يخالف النظام السعودي",
    "وفق النظام واللائحة",
    "النظام يجيز مطالبته",
])
def test_the_general_word_is_not_a_law_name(text):
    result = scan(text)
    assert result.named_laws == []
    assert all(law.kind == KIND_BACK_REF for law in result.laws)


@pytest.mark.parametrize("text", ["يطبق نظام آخر", "صدر نظام جديد", "وفق نظام معين"])
def test_a_common_noun_is_not_a_law(text):
    assert scan(text).laws == []


def test_executive_regulation_of_the_same_law():
    result = scan("المادة 76 من نظام المرافعات الشرعية والمادة 5 من اللائحة التنفيذية لذات النظام.")
    second = result.mentions[1]
    assert second.basis == BASIS_BACK_REF and second.exec_reg
    assert law_text(result, second) == "نظام المرافعات الشرعية"


# --------------------------------------------------------------------------- #
# How a mention is tied to a law
# --------------------------------------------------------------------------- #


def test_same_phrase():
    result, m = one("استناداً إلى المادة (77) من نظام العمل فإن المدعي يستحق التعويض.")
    assert m.basis == BASIS_SAME_PHRASE
    assert law_text(result, m) == "نظام العمل"
    assert not m.inherited


def test_a_wrapped_line_is_still_the_same_phrase():
    _, m = one("وفقاً لنص\nالمادة 77\nمن نظام العمل وما بعدها")
    assert m.basis == BASIS_SAME_PHRASE
    assert not m.heading


def test_law_named_before_with_a_possessive():
    result, m = one("نظام العمل في مادته الرابعة والسبعين حدد حالات انتهاء العقد.")
    assert m.numbers == ("74",)
    assert m.basis == BASIS_SAME_PHRASE
    assert law_text(result, m) == "نظام العمل"


def test_chained_mentions_share_the_law():
    result = scan("استند إلى المادة 94 والمادة 107 من نظام المعاملات المدنية.")
    first, second = result.mentions
    assert first.basis == second.basis == BASIS_SAME_PHRASE
    assert first.inherited and not second.inherited
    assert law_text(result, first) == "نظام المعاملات المدنية"


def test_back_reference_points_at_the_law_named_before():
    result = scan("نصت المادة 77 من نظام العمل على التعويض، وكذلك المادة 80 من النظام ذاته.")
    first, second = result.mentions
    assert second.basis == BASIS_BACK_REF
    assert law_text(result, second) == "نظام العمل"
    assert second.laws_before == 1


def test_of_it_is_a_back_reference():
    result = scan("نصت المادة 77 من نظام العمل على التعويض، والمادة ٨٠ منه على الإنهاء.")
    assert result.mentions[1].basis == BASIS_BACK_REF
    assert law_text(result, result.mentions[1]) == "نظام العمل"


def test_back_reference_with_nothing_before_has_no_law():
    _, m = one("مع مراعاة المادة 5 من هذا النظام")
    assert m.basis == BASIS_BACK_REF
    assert m.law is None


def test_executive_back_reference():
    result = scan("المادة 76 من نظام المرافعات الشرعية والمادة 76/1 من لائحته التنفيذية.")
    second = result.mentions[1]
    assert second.basis == BASIS_BACK_REF and second.exec_reg
    assert law_text(result, second) == "نظام المرافعات الشرعية"


def test_back_reference_counts_the_laws_in_reach():
    result = scan("يحكم نظام العمل العلاقة، ويحكم نظام المرافعات الشرعية الإجراءات، وتنص المادة 5 من النظام على ذلك.")
    m = result.mentions[0]
    assert m.basis == BASIS_BACK_REF
    assert m.laws_before == 2          # two candidates — the caller must not treat this as checkable


def test_other_target_is_never_a_law():
    _, m = one("بما ورد في المادة الثالثة من العقد المبرم بين الطرفين")
    assert m.basis == BASIS_OTHER_TARGET
    assert m.other_target == fold("العقد")
    assert m.law is None


def test_other_target_through_a_demonstrative():
    _, m = one("المادة 9 من هذه الاتفاقية")
    assert m.basis == BASIS_OTHER_TARGET


def test_same_sentence_one_law():
    result, m = one("وحيث إن نظام العمل قرر في شأن الإنهاء ما جاء في المادة 77 بوضوح.")
    assert m.basis == BASIS_SAME_SENTENCE
    assert law_text(result, m) == "نظام العمل"


def test_single_law_in_the_document():
    result = scan("ينظم نظام العمل العلاقة بين الطرفين.\n\nوقد خالف المدعى عليه المادة 77.")
    m = result.mentions[0]
    assert m.basis == BASIS_SINGLE_LAW
    assert law_text(result, m) == "نظام العمل"


def test_several_laws_make_the_link_a_guess():
    text = ("تنص المادة 77 على التعويض.\n\nوقد سبق بيان أن نظام العمل ينظم ذلك، "
            "وأن نظام المرافعات الشرعية يحكم الإجراءات.\n\nوالمادة 3 واضحة.")
    result = scan(text)
    assert [m.basis for m in result.mentions] == [BASIS_NEAREST, BASIS_NEAREST]
    assert law_text(result, result.mentions[1]) == "نظام المرافعات الشرعية"


def test_no_law_anywhere():
    _, m = one("وقد خالف المدعى عليه المادة 77 مخالفة صريحة.")
    assert m.basis == BASIS_NO_LAW
    assert m.law is None


def test_a_pleading_does_not_count_as_the_documents_single_law():
    _, m = one("قدم المدعي لائحة الدعوى.\n\nوقد خالف المدعى عليه المادة 77.")
    assert m.basis == BASIS_NO_LAW


# --------------------------------------------------------------------------- #
# The document's own headings
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("line", [
    "المادة الخامسة: مدة العقد",
    "المادة (5)",
    "المادة 5 - مدة العقد",
    "**المادة الخامسة**",
    "## المادة الخامسة مدة العقد",
    "- المادة الخامسة:",
    "3. المادة الخامسة:",
])
def test_headings(line):
    result = scan(f"تمهيد\n{line}\nيلتزم الطرف الأول بالسداد.")
    assert [m.heading for m in result.mentions] == [True]
    assert result.citing == []


def test_a_heading_is_not_chained_into_a_citation():
    result = scan("المادة الأولى:\nالمادة الثانية:\nتطبق المادة 77 من نظام العمل.")
    assert [m.heading for m in result.mentions] == [True, True, False]


def test_a_citation_at_the_start_of_a_line_is_not_a_heading():
    _, m = one("تمهيد\nالمادة 77 من نظام العمل: تنص على التعويض")
    assert not m.heading
    assert m.basis == BASIS_SAME_PHRASE


def test_a_line_start_sentence_is_not_a_heading():
    _, m = one("تمهيد\nالمادة 77 تنص على التعويض عن الإنهاء")
    assert m.line_start and not m.heading


# --------------------------------------------------------------------------- #
# Purity
# --------------------------------------------------------------------------- #


def test_same_input_same_output():
    text = "المادة 77 من نظام العمل والمادة 80 منه.\nالمادة الأولى: تعريفات"
    assert scan(text) == scan(text)


@pytest.mark.parametrize("text", ["", "   \n\n ", None])
def test_empty_text(text):
    result = scan_citations(text, "memo", TITLES)
    assert result.mentions == [] and result.laws == []


def test_doc_type_only_changes_paragraph_numbers():
    text = ("جملة طويلة جداً " * 60) + "\nالمادة 77 من نظام العمل"
    a = scan(text, doc_type="judgment").mentions[0]
    b = scan(text, doc_type="memo").mentions[0]
    assert (a.char_start, a.char_end, a.numbers, a.basis) == (b.char_start, b.char_end, b.numbers, b.basis)
    assert a.para_n > b.para_n      # 500-character paragraphs for judgments, 1,200 otherwise


# --------------------------------------------------------------------------- #
# Found on a real judgment (2026-11-23): the number written twice, quoted clauses
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", [
    "بحسب المادة (31) الحادية والثلاثين من نظام العمل، الصادر بالمرسوم الملكي",
    "بحسب المادة الحادية والثلاثين (31) من نظام العمل",
    "بحسب المادة ( ٣١ ) الحادية والثلاثون من نظام العمل",
])
def test_a_number_written_twice_is_one_number_and_keeps_its_law(text):
    result, m = one(text)
    assert m.numbers == ("31",) and not m.conflict
    assert m.basis == BASIS_SAME_PHRASE and law_text(result, m) == "نظام العمل"
    assert fold("الثلاث") in fold(text[m.char_start:m.char_end]) and "31" in fold(text[m.char_start:m.char_end])


def test_a_number_written_twice_then_the_same_law():
    text = ("تدخل في اختصاص المحكمة بحسب المادة (31) الحادية والثلاثين من نظام المرافعات الشرعية، "
            "وفقاً للفقرة (الأولى) من المادة (36) السادسة والثلاثين من ذات النظام.")
    result = scan(text)
    first, second = result.mentions
    assert (first.numbers, first.basis) == (("31",), BASIS_SAME_PHRASE)
    assert (second.numbers, second.basis) == (("36",), BASIS_BACK_REF)
    assert law_text(result, second) == "نظام المرافعات الشرعية" and second.laws_before == 1


@pytest.mark.parametrize("text", [
    "بحسب المادة (37) السادسة والثلاثين من نظام العمل",       # a digit misread by a scan
    "بحسب المادة السادسة والثلاثين (37) من نظام العمل",
])
def test_two_writings_that_disagree_are_flagged(text):
    _, m = one(text)
    assert m.conflict
    assert m.basis == BASIS_SAME_PHRASE                       # the law is still known; the number is not trusted


def test_plain_numbers_carry_no_conflict():
    assert not one("المادة 31 من نظام العمل")[1].conflict
    assert not one("المادة الحادية والثلاثين من نظام العمل")[1].conflict
    # a paragraph in words after the article is not a second writing of the number
    assert one("المادة (77) الفقرة الثانية من نظام العمل")[1].numbers == ("77",)


def test_a_clause_quoted_with_its_title_is_not_a_citation():
    text = ("ويحكم نظام العمل العلاقة. وقد جاء في العقد: (المادة الخامسة: التزامات المستأجر: 1/5 يلتزم المستأجر "
            "بدفع الأجرة)، (المادة السادسة عشرة: الإشعارات والمراسلات).")
    result = scan(text)
    assert [(m.numbers, m.heading) for m in result.mentions] == [(("5",), True), (("16",), True)]
    assert result.citing == []


def test_a_colon_after_a_real_citation_does_not_hide_it():
    _, m = one("نصت المادة 77 من نظام العمل: إذا أنهي العقد لسبب غير مشروع")
    assert not m.heading and m.basis == BASIS_SAME_PHRASE
