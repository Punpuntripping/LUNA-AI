"""The Completion Search helper: rounds, handles, confidence, the code-first path.

Plan ``.claude/plans/completion_search.md`` §4.2–4.3 and the "tests that must exist"
list in §7. All texts are synthetic.
"""
from __future__ import annotations

import asyncio

import pytest

from agents.completion_search import render_for_caller, run_completion_search
from agents.completion_search.agent import create_completion_agent
from agents.completion_search.deps import CompletionDeps
from agents.completion_search.models import MAX_ROUNDS, MAX_SEARCHES, HelperDecision, Pin, SearchDecision
from agents.completion_search.runner import answer_by_code, build_result
from agents.completion_search.tests.fakes import (
    CIVIL,
    DRAFT,
    LABOR,
    PROC,
    FakeLibrary,
    answer,
    scripted_model,
)


def run(request: str, *steps, library: FakeLibrary | None = None, **kw):
    library = library or FakeLibrary()
    agent = create_completion_agent(model=scripted_model(*steps)) if steps else _no_model()
    result = asyncio.run(run_completion_search(request, library=library, agent=agent, **kw))
    return result, library


def _no_model():
    """An agent that fails the test if the model is ever called."""
    def boom(_returns):
        raise AssertionError("the model was called for a request code can answer alone")
    return create_completion_agent(model=scripted_model(boom))


# --------------------------------------------------------------------------- #
# Path 1 — code answers the plain request
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("request_text", [
    "نظام المعاملات المدنية المادة 56 والمادة 234",
    "المادة 56 والمادة 234 من نظام المعاملات المدنية",
    "المادتين 56 و234 من نظام المعاملات المدنية",
    "نص المادة السادسة والخمسين والمادة ٢٣٤ من نظام المعاملات المدنية",
])
def test_plain_request_is_answered_by_code(request_text):
    result, library = run(request_text)
    assert not result.model_used
    assert [s.status for s in result.searches] == ["found", "found"]
    assert [p.article_number for p in result.pins()] == ["56", "234"]
    assert all(p.confidence == "high" for p in result.pins())
    assert result.pins()[0].text == library.laws[CIVIL]["articles"]["56"]
    assert result.pins()[0].title == f"المادة 56 من {CIVIL}"


def test_code_path_reports_a_missing_article():
    result, _ = run("المادة 56 والمادة 999 من نظام المعاملات المدنية")
    assert not result.model_used
    assert [s.status for s in result.searches] == ["found", "not_found"]
    assert result.searches[1].pins == []


@pytest.mark.parametrize("request_text", [
    "نظام المعاملات المدنية المادة 56 وتعريف البيع",        # something else is asked too
    "المادة 77 من نظام العمل والمادة 56 من نظام المعاملات المدنية",   # two laws
    "المادة 76 من نظام المرافعات",                           # not a library title
    "المادة 5 من هذا النظام",                                # a back-reference
    "المادة 76/1 من نظام المرافعات الشرعية",                 # a compound number
    "تعميم مدد التقاضي",                                     # no article at all
    "المادة 3 من نظام التجربة المقترح",                      # a draft is never answered by code
])
def test_other_requests_are_left_to_the_agent(request_text):
    assert asyncio.run(answer_by_code(request_text, FakeLibrary())) is None


def test_empty_request():
    result, _ = run("   ")
    assert result.error == "empty_request" and result.searches == []


# --------------------------------------------------------------------------- #
# Path 2 — the agent
# --------------------------------------------------------------------------- #


def test_article_by_exact_name_is_high():
    result, _ = run(
        "المادة 77 من نظام العمل والمادة 56 من نظام المعاملات المدنية",
        lambda _: [
            ("get_article", {"search_id": "S1", "regulation": "نظام العمل", "article_number": "77"}),
            ("get_article", {"search_id": "S2", "regulation": "نظام المعاملات المدنية", "article_number": "56"}),
        ],
        lambda _: answer(
            {"search_id": "S1", "selected": ["T1"], "status": "found"},
            {"search_id": "S2", "selected": ["T2"], "status": "found"},
        ),
    )
    assert result.model_used
    assert [(s.status, s.confidence) for s in result.searches] == [("found", "high"), ("found", "high")]
    assert [p.article_number for p in result.pins("high")] == ["77", "56"]


def test_a_reworded_law_name_is_only_medium():
    # The request wrote «نظام المرافعات»; the model tried the full official name itself.
    result, _ = run(
        "المادة 76 من نظام المرافعات",
        lambda _: [("get_article", {"search_id": "S1", "regulation": "نظام المرافعات", "article_number": "76"})],
        lambda _: [("get_article", {"search_id": "S1", "regulation": PROC, "article_number": "76"})],
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    s = result.searches[0]
    assert (s.status, s.confidence, s.rounds) == ("found", "medium", 2)
    assert s.pins[0].regulation_id == "reg-proc"


def test_several_laws_are_listed_with_handles_and_a_pick_is_medium():
    seen: list[str] = []

    def second(returns):
        seen.extend(returns)
        return [("get_article", {"search_id": "S1", "regulation": "L1", "article_number": "76"})]

    result, _ = run(
        "المادة 76 من نظام المرافعات",
        lambda _: [("get_article", {"search_id": "S1", "regulation": "نظام المرافعات", "article_number": "76"})],
        second,
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    assert "L1" in seen[0] and "L2" in seen[0]          # both laws offered, no text
    assert "الدفع بعدم الاختصاص" not in seen[0]
    assert result.searches[0].confidence == "medium"     # the model chose — never high


def test_candidates_without_a_pick_return_titles_only():
    result, _ = run(
        "المادة 76 من نظام المرافعات",
        lambda _: [("get_article", {"search_id": "S1", "regulation": "نظام المرافعات", "article_number": "76"})],
        lambda _: answer({"search_id": "S1", "selected": ["L1", "L2"], "status": "candidates"}),
    )
    s = result.searches[0]
    assert (s.status, s.confidence, s.pins) == ("candidates", "low", [])
    assert len(s.candidates) == 2 and all("المرافعات" in t for t in s.candidates)


def test_a_law_that_is_not_in_the_library():
    result, _ = run(
        "المادة 5 من نظام الفضاء الخارجي",
        lambda _: [("get_article", {"search_id": "S1", "regulation": "نظام الفضاء الخارجي", "article_number": "5"})],
        lambda _: answer({"search_id": "S1", "selected": [], "status": "not_in_library"}),
    )
    assert result.searches[0].status == "not_in_library"
    assert result.pins() == []


def test_only_a_lookup_may_say_not_in_library():
    # The law exists; the article does not. The model's "not_in_library" is corrected.
    result, _ = run(
        "المادة 999 من نظام العمل وشيء آخر",
        lambda _: [("get_article", {"search_id": "S1", "regulation": "نظام العمل", "article_number": "999"})],
        lambda _: answer({"search_id": "S1", "selected": [], "status": "not_in_library"}),
    )
    assert result.searches[0].status == "not_found"


def test_a_draft_is_never_high():
    result, _ = run(
        f"المادة 3 من {DRAFT}",
        lambda _: [("get_article", {"search_id": "S1", "regulation": DRAFT, "article_number": "3"})],
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    pin = result.pins()[0]
    assert pin.confidence == "medium"
    assert "ليس نظامًا ساريًا" in pin.note


# -- phrases and definitions --------------------------------------------------


def test_a_definition_is_found_by_the_term_through_tashkeel():
    seen: list[str] = []

    def last(returns):
        seen.extend(returns)
        return answer({"search_id": "S1", "selected": ["T1"], "status": "found"})

    result, _ = run(
        "تعريف البيع في نظام المعاملات المدنية",
        lambda _: [("find_in_regulation", {
            "search_id": "S1", "regulation": "نظام المعاملات المدنية", "phrase": "البيع", "is_definition": True})],
        last,
    )
    assert "T1 — المادة 307" in seen[0] and "تعرّف المصطلح" in seen[0]
    s = result.searches[0]
    assert s.confidence == "high"
    assert s.pins[0].article_number == "307"
    assert s.pins[0].text.startswith("الْبَيْعُ")          # the official wording, tashkeel intact


def test_a_phrase_in_one_article_is_high_and_in_several_is_medium():
    one, _ = run(
        "أين ورد «المقايضة» في نظام المعاملات المدنية",
        lambda _: [("find_in_regulation", {"search_id": "S1", "regulation": CIVIL, "phrase": "المقايضة"})],
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    assert one.searches[0].confidence == "high"

    several, _ = run(
        "أين ورد «المبيع» في نظام المعاملات المدنية",
        lambda _: [("find_in_regulation", {"search_id": "S1", "regulation": CIVIL, "phrase": "المبيع"})],
        lambda _: answer({"search_id": "S1", "selected": ["T1", "T2"], "status": "found"}),
    )
    assert several.searches[0].confidence == "medium"
    assert len(several.searches[0].pins) == 2


def test_a_phrase_in_too_many_articles_returns_no_text():
    seen: list[str] = []

    def last(returns):
        seen.extend(returns)
        return answer({"search_id": "S1", "selected": [], "status": "not_found"})

    result, _ = run(
        "أين ورد «الأجر» في نظام العمل",
        lambda _: [("find_in_regulation", {"search_id": "S1", "regulation": LABOR, "phrase": "الأجر"})],
        last,
    )
    assert "ضيّق العبارة" in seen[0] and "T1" not in seen[0]
    assert result.pins() == []


def test_a_phrase_the_model_reworded_is_only_medium():
    result, _ = run(
        "أين ورد ذكر المبادلة في نظام المعاملات المدنية",
        lambda _: [("find_in_regulation", {"search_id": "S1", "regulation": CIVIL, "phrase": "المبادلة"})],
        lambda _: [("find_in_regulation", {"search_id": "S1", "regulation": CIVIL, "phrase": "المقايضة"})],
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    assert result.searches[0].confidence == "medium"


# -- circulars and services ----------------------------------------------------


def test_a_circular_by_its_exact_title_is_high():
    result, library = run(
        "تعميم ضوابط التبليغ الإلكتروني",
        lambda _: [("find_circular", {"search_id": "S1", "words": "تعميم ضوابط التبليغ الإلكتروني"})],
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    pin = result.pins()[0]
    assert (pin.source_type, pin.confidence, pin.circular_id) == ("circular", "high", "circ-3")
    assert pin.text == "نص التعميم الثالث."
    assert ("open_text", "circular", "circ-3") in library.calls


def test_the_issuer_settles_between_two_circulars():
    seen: list[str] = []

    def second(returns):
        seen.extend(returns)
        return [("find_circular", {"search_id": "S1", "words": "مدد التقاضي", "issuer": "وزارة العدل"})]

    result, _ = run(
        "تعميم وزارة العدل عن مدد التقاضي",
        lambda _: [("find_circular", {"search_id": "S1", "words": "مدد التقاضي"})],
        second,
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    assert "أكثر من تعميم" in seen[0]                    # round 1: two titles, no text
    assert "نص التعميم" not in seen[0]
    assert result.pins()[0].circular_id == "circ-1"
    assert result.searches[0].confidence == "medium"


def test_a_service_made_unique_by_its_provider():
    result, _ = run(
        "خدمة إصدار وكالة من وزارة العدل",
        lambda _: [("find_service", {"search_id": "S1", "name": "إصدار وكالة", "provider": "وزارة العدل"})],
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    pin = result.pins()[0]
    assert (pin.source_type, pin.service_id, pin.confidence) == ("service", "svc-3", "high")


# --------------------------------------------------------------------------- #
# Rounds — enforced by code
# --------------------------------------------------------------------------- #


def test_a_fourth_attempt_is_refused():
    seen: list[list[str]] = []

    def attempt(returns):
        seen.append(returns)
        return [("get_article", {"search_id": "S1", "regulation": "نظام الفضاء الخارجي", "article_number": "5"})]

    result, library = run(
        "المادة 5 من نظام الفضاء الخارجي",
        attempt, attempt, attempt, attempt,
        lambda returns: (seen.append(returns), answer({"search_id": "S1", "selected": [], "status": "not_in_library"}))[1],
    )
    assert len([c for c in library.calls if c[0] == "find_law"]) == MAX_ROUNDS
    assert "استُنفدت المحاولات الثلاث" in seen[4][0]
    assert "آخر محاولة" in seen[3][0]
    assert result.searches[0].rounds == MAX_ROUNDS


def test_a_settled_search_is_not_repeated():
    seen: list[list[str]] = []
    call = ("get_article", {"search_id": "S1", "regulation": "نظام العمل", "article_number": "77"})

    result, library = run(
        "المادة 77 من نظام العمل وتوضيح",
        lambda _: [call],
        lambda _: [call],
        lambda returns: (seen.append(returns), answer({"search_id": "S1", "selected": ["T1"], "status": "found"}))[1],
    )
    assert "اكتمل البحث S1" in seen[0][0]
    assert len([c for c in library.calls if c[0] == "article"]) == 1
    assert result.searches[0].rounds == 1


def test_no_more_than_ten_searches():
    refused: list[str] = []
    calls = [
        ("get_article", {"search_id": f"S{i}", "regulation": "نظام العمل", "article_number": str(89 + i)})
        for i in range(1, MAX_SEARCHES + 2)
    ]

    def last(returns):
        refused.extend(r for r in returns if "الحد الأقصى" in r)
        return answer()

    run("طلب طويل فيه مواد كثيرة من نظام العمل وغيره", lambda _: calls, last)
    assert len(refused) == 1


def test_a_settled_search_the_model_forgot_is_still_returned():
    result, _ = run(
        "المادة 77 من نظام العمل وتوضيح",
        lambda _: [("get_article", {"search_id": "S1", "regulation": "نظام العمل", "article_number": "77"})],
        lambda _: answer(),
    )
    assert [(s.search_id, s.status, s.confidence) for s in result.searches] == [("S1", "found", "high")]


# --------------------------------------------------------------------------- #
# The model cannot write legal text or invent a handle
# --------------------------------------------------------------------------- #


def test_the_models_output_has_no_field_for_legal_text():
    for model in (SearchDecision, HelperDecision):
        for name, info in model.model_fields.items():
            if info.annotation is str:
                limits = [m.max_length for m in info.metadata if hasattr(m, "max_length")]
                assert limits and max(limits) <= 200, name
    assert "text" not in SearchDecision.model_fields
    with pytest.raises(Exception):
        SearchDecision(search_id="S1", asked="x", status="found", note="ن" * 201)


def test_an_unknown_handle_is_sent_back():
    turns = {"n": 0}

    def final(_returns):
        turns["n"] += 1
        handle = "T9" if turns["n"] == 1 else "T1"
        return answer({"search_id": "S1", "selected": [handle], "status": "found"})

    result, _ = run(
        "المادة 77 من نظام العمل وتوضيح",
        lambda _: [("get_article", {"search_id": "S1", "regulation": "نظام العمل", "article_number": "77"})],
        final,
    )
    assert turns["n"] == 2
    assert result.pins()[0].article_number == "77"


def test_found_without_a_text_handle_is_sent_back():
    turns = {"n": 0}

    def final(_returns):
        turns["n"] += 1
        if turns["n"] == 1:
            return answer({"search_id": "S1", "selected": ["L1"], "status": "found"})
        return answer({"search_id": "S1", "selected": ["L1"], "status": "candidates"})

    result, _ = run(
        "ما هو نظام العمل",
        lambda _: [("find_regulation", {"search_id": "S1", "name": "نظام العمل"})],
        final,
    )
    assert turns["n"] == 2
    assert result.searches[0].status == "candidates" and result.pins() == []


def test_the_pin_text_comes_from_the_library_not_the_model():
    result, library = run(
        "المادة 77 من نظام العمل وتوضيح",
        lambda _: [("get_article", {"search_id": "S1", "regulation": "نظام العمل", "article_number": "77"})],
        lambda _: answer({"search_id": "S1", "asked": "نص آخر يكتبه النموذج", "selected": ["T1"], "status": "found",
                          "note": "ملاحظة"}),
    )
    assert result.pins()[0].text == library.laws[LABOR]["articles"]["77"]


# --------------------------------------------------------------------------- #
# Deps and rendering
# --------------------------------------------------------------------------- #


def test_as_asked_reads_the_request_itself():
    deps = CompletionDeps(library=FakeLibrary(), request="وفقاً لنِظَام العمل والمادة ٧٧")
    assert deps.as_asked("نظام العمل")
    assert deps.as_asked("«نظام العمل»")
    assert not deps.as_asked("نظام المرافعات الشرعية")
    assert not deps.as_asked("")


def test_build_result_ignores_a_duplicate_search_id():
    deps = CompletionDeps(library=FakeLibrary(), request="x")
    pin = Pin(key="article:1", source_type="article", title="t", text="نص", confidence="high")
    deps.begin("S1")
    hid = deps.add_pin("S1", pin)
    decision = HelperDecision(searches=[
        SearchDecision(search_id="S1", asked="a", selected=[hid], status="found"),
        SearchDecision(search_id="s1", asked="b", selected=[hid], status="found"),
    ])
    assert len(build_result("x", decision, deps).searches) == 1


def test_render_for_caller():
    result, _ = run("المادة 56 والمادة 999 من نظام المعاملات المدنية")
    text = render_for_caller(result, cap_text=lambda t: t[:10] + "…")
    assert f"## المادة 56 من {CIVIL}" in text
    assert "الحالة: وُجد — ثقة عالية" in text
    assert "الحالة: لم يُعثر عليه" in text
    assert "إذا كان مح…" in text
    assert render_for_caller(type(result)(request="x", error="boom")).startswith("تعذّر")


# --------------------------------------------------------------------------- #
# Searches code settles as "nothing to find"
# --------------------------------------------------------------------------- #

from agents.completion_search.tests.fakes import EMPTY  # noqa: E402


def test_a_missing_number_in_the_named_law_ends_the_search():
    seen: list[list[str]] = []
    call = ("get_article", {"search_id": "S1", "regulation": "نظام العمل", "article_number": "999"})

    result, library = run(
        "المادة 999 من نظام العمل وتوضيح",
        lambda _: [call],
        lambda returns: (seen.append(returns), [call])[1],
        lambda returns: (seen.append(returns), answer({"search_id": "S1", "selected": [], "status": "not_found"}))[1],
    )
    assert "لا تُعد المحاولة" in seen[0][0]
    assert "انتهى البحث S1" in seen[1][0]
    assert len([c for c in library.calls if c[0] == "article"]) == 1
    assert result.searches[0].status == "not_found"


def test_a_law_without_article_text_says_so():
    seen: list[str] = []
    result, _ = run(
        f"المادة 4 من {EMPTY} وتوضيح",
        lambda _: [("get_article", {"search_id": "S1", "regulation": EMPTY, "article_number": "4"})],
        lambda returns: (seen.extend(returns), answer({"search_id": "S1", "selected": [], "status": "not_found"}))[1],
    )
    assert "نصوص مواده غير متوفرة" in seen[0]
    assert result.searches[0].status == "not_found"

    by_code, _ = run(f"المادة 4 من {EMPTY}")
    assert not by_code.model_used
    assert "نصوص مواده غير متوفرة" in by_code.searches[0].note


def test_the_word_service_is_not_part_of_a_service_name():
    result, library = run(
        "خدمة إصدار وكالة من وزارة العدل",
        lambda _: [("find_service", {"search_id": "S1", "name": "خدمة إصدار وكالة", "provider": "وزارة العدل"})],
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    assert ("services", "إصدار وكالة", "وزارة العدل") in library.calls
    assert result.pins()[0].confidence == "high"


def test_a_definition_among_many_articles_is_high_in_one_attempt():
    laws = {CIVIL: {"id": "reg-civil", "status": "in_force", "articles": {
        "307": "##### المادة السابعة بعد الثلاثمائة\nالبيع عقد يملك بمقتضاه البائع المبيع للمشتري.",
        **{str(n): f"البيع بشرط رقم {n} يجوز." for n in range(308, 312)},
        **{str(n): f"تسري أحكام البيع على الحالة {n}." for n in range(312, 320)},
    }}}
    result, _ = run(
        "تعريف البيع في نظام المعاملات المدنية",
        lambda _: [("find_in_regulation", {
            "search_id": "S1", "regulation": CIVIL, "phrase": "البيع", "is_definition": True})],
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
        library=FakeLibrary(laws),
    )
    s = result.searches[0]
    assert (s.confidence, s.rounds, s.pins[0].article_number) == ("high", 1, "307")


# --------------------------------------------------------------------------- #
# Rulings by case number, judicial principles
# --------------------------------------------------------------------------- #


def test_a_ruling_by_its_case_number_is_high():
    result, library = run(
        "الحكم في القضية رقم 4455",
        lambda _: [("find_case", {"search_id": "S1", "case_number": "4455"})],
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    pin = result.pins()[0]
    assert (pin.source_type, pin.case_id, pin.confidence) == ("case", "case-1", "high")
    assert pin.text == "نص الحكم الأول كاملاً."
    assert "التجارية" in pin.title and "4455" in pin.title
    assert ("open_text", "case", "case-1") in library.calls


def test_arabic_indic_digits_in_a_case_number():
    result, _ = run(
        "القضية رقم ٤٤٥٥",
        lambda _: [("find_case", {"search_id": "S1", "case_number": "٤٤٥٥"})],
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    assert result.pins()[0].case_id == "case-1"


def test_rulings_that_share_a_number_are_listed_without_text():
    seen: list[str] = []
    result, library = run(
        "الحكم في القضية رقم 7001",
        lambda _: [("find_case", {"search_id": "S1", "case_number": "7001"})],
        lambda returns: (seen.extend(returns), answer({"search_id": "S1", "selected": [], "status": "candidates"}))[1],
    )
    assert "أكثر من حكم" in seen[0] and "نص حكم" not in seen[0]
    s = result.searches[0]
    assert (s.status, s.confidence, s.pins) == ("candidates", "medium", [])
    assert len(s.candidates) == 2 and "العامة" in " ".join(s.candidates)
    assert not [c for c in library.calls if c[0] == "open_text"]


def test_the_court_settles_between_rulings_that_share_a_number():
    result, _ = run(
        "الحكم في القضية رقم 7001 من المحكمة العامة",
        lambda _: [("find_case", {"search_id": "S1", "case_number": "7001", "court": "العامة"})],
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    assert (result.pins()[0].case_id, result.pins()[0].confidence) == ("case-3", "high")


def test_the_year_settles_between_rulings_that_share_a_number():
    result, _ = run(
        "الحكم في القضية رقم 7001 لعام 1443",
        lambda _: [("find_case", {"search_id": "S1", "case_number": "7001", "year": "1443"})],
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    assert result.pins()[0].case_id == "case-2"


@pytest.mark.parametrize("number", ["unk01", "قضية فسخ عقد", ""])
def test_a_placeholder_or_a_subject_is_never_looked_up(number):
    seen: list[str] = []
    result, library = run(
        "حكم قديم",
        lambda _: [("find_case", {"search_id": "S1", "case_number": number})],
        lambda returns: (seen.extend(returns), answer({"search_id": "S1", "selected": [], "status": "not_found"}))[1],
    )
    assert "بالرقم فقط" in seen[0]
    assert not [c for c in library.calls if c[0] == "cases"]
    assert result.pins() == []


def test_a_principle_by_its_number_is_high():
    result, _ = run(
        "المبدأ القضائي رقم 12",
        lambda _: [("find_principle", {"search_id": "S1", "number": "12"})],
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    pin = result.pins()[0]
    assert (pin.source_type, pin.principle_no, pin.confidence) == ("principle", "12", "high")
    assert pin.text.startswith("البينة على من ادعى")


def test_a_principle_by_words_is_never_high():
    result, _ = run(
        "المبدأ القضائي عن البينة واليمين",
        lambda _: [("find_principle", {"search_id": "S1", "words": "البينة واليمين"})],
        lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    assert result.pins()[0].confidence == "medium"


def test_several_principles_are_listed_without_text():
    seen: list[str] = []
    result, _ = run(
        "مبدأ قضائي في العقد",
        lambda _: [("find_principle", {"search_id": "S1", "words": "العقد"})],
        lambda returns: (seen.extend(returns), answer({"search_id": "S1", "selected": [], "status": "candidates"}))[1],
    )
    assert "أكثر من مبدأ" in seen[0] and "شريعة المتعاقدين" not in seen[0]
    assert result.searches[0].status == "candidates" and result.pins() == []
