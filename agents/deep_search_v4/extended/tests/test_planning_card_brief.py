"""The plan card (options, payload, text — §4.4, Q13) and each search's brief (§4.9 b);
the planner prompts' two variants (as tested / D33)."""
from __future__ import annotations

import pytest

from agents.deep_search_v4.extended import budget as B
from agents.deep_search_v4.extended.brief import build_planner_brief
from agents.deep_search_v4.extended.card import build_card, build_options, card_text_ar, points_ar
from agents.deep_search_v4.extended.models import (
    AngleCard,
    CheaperOption,
    DocView,
    ExtendedPlan,
    ExtendedPlanCard,
    ExtendedRequest,
    PlannerView,
    SectionView,
)
from agents.deep_search_v4.extended.prompts import (
    SEARCH_INSTRUCTION,
    STRENGTHS_WEAKNESSES,
    SYSTEM_PROMPT_BUDGET,
    SYSTEM_PROMPT_NO_BUDGET,
    system_prompt,
)


def r(title: str, mode: str = "reg_compliance_led", cap: int | None = 6, **kw) -> ExtendedRequest:
    return ExtendedRequest(title=title, mode=mode, max_sub_queries=cap, focus=kw.pop("focus", "تركيز"), **kw)


R1 = r("استحقاق قيمة أمر التغيير والملحق", "full", 6)
R2 = r("جدول الدفعات والفواتير", cap=5)
R3 = r("مبلغ 43,700 دون أمر تغيير ثانٍ", cap=4)
R23 = r("الدفعات والفواتير والمبلغ", cap=6)


def the_plan(cheaper: list[ExtendedRequest] | None = None, note: str = "أقدر أدمج 2 مع 3 في بحث واحد.") -> ExtendedPlan:
    return ExtendedPlan(
        understanding="يريد المقاول معرفة استحقاقه.", client_side="claimant", requests=[R1, R2, R3],
        cheaper=CheaperOption(note=note, requests=cheaper) if cheaper else None,
    )


# ── options (Q13) ────────────────────────────────────────────────────────────


def test_plan_cheaper_ordinary_priced_by_code():
    opts = build_options(the_plan([R1, R23]), planner_points=0.4)
    assert [o.id for o in opts] == ["plan", "cheaper", "ordinary"]
    assert opts[0].estimate_points == pytest.approx(3 * 1.95 + 21 * 0.285 + 2 + 0.4)
    assert opts[1].estimate_points == pytest.approx(2 * 1.95 + 18 * 0.285 + 2 + 0.4)
    assert opts[2].estimate_points == pytest.approx(B.ordinary_estimate_points()) and opts[2].requests == []
    assert [x.title for x in opts[1].requests] == [R1.title, R23.title]


def test_a_cheaper_option_that_saves_under_one_point_is_dropped():
    # One sub-query less = 0.285 points: the same rounded number → no cheaper option.
    almost = [R1, R2, r(R3.title, cap=3)]
    assert [o.id for o in build_options(the_plan(almost), planner_points=0.4)] == ["plan", "ordinary"]
    dearer = [r("a", "full", 8), r("b", "full", 8), r("c", "full", 8)]
    assert [o.id for o in build_options(the_plan(dearer), planner_points=0.4)] == ["plan", "ordinary"]


def test_no_cheaper_from_the_planner_no_cheaper_option():
    assert [o.id for o in build_options(the_plan(None), planner_points=0.0)] == ["plan", "ordinary"]


# ── the card (§4.4) ──────────────────────────────────────────────────────────


def test_the_card_payload():
    plan = the_plan([R1, R23])
    card = build_card(build_options(plan, planner_points=0.4), plan, run_id="run-1", points_left=18.7)
    assert isinstance(card, ExtendedPlanCard) and card.version == 1 and card.run_id == "run-1"
    assert card.points_left == 18 and card.outcome is None and card.outcome_at is None
    assert [(o.id, o.label, o.points) for o in card.options] == [
        ("plan", "نفّذ الخطة", 14), ("cheaper", "نفّذ الأوفر", 11), ("ordinary", "بحث عادي", 5)]
    assert [(s.title, s.sides) for s in card.options[0].searches] == [
        (R1.title, "both"), (R2.title, "regulations"), (R3.title, "regulations")]
    assert card.options[2].searches == []
    assert (card.cheaper_note, card.cheaper_saving) == ("أقدر أدمج 2 مع 3 في بحث واحد.", 3)
    dumped = card.model_dump()
    assert isinstance(dumped["points_left"], int) and all(isinstance(o["points"], int) for o in dumped["options"])


def test_no_cheaper_option_no_cheaper_line():
    plan = the_plan(None)
    card = build_card(build_options(plan, planner_points=0.4), plan, run_id="x", points_left=30)
    assert [o.id for o in card.options] == ["plan", "ordinary"]
    assert card.cheaper_note is None and card.cheaper_saving is None
    text = card_text_ar(card)
    assert "وأوفّر" not in text and "[ نفّذ الأوفر" not in text


def test_unlimited_points_left_stays_none_and_the_text_omits_it():
    plan = the_plan([R1, R23])
    card = build_card(build_options(plan, planner_points=0.4), plan, run_id="x", points_left=None)
    assert card.points_left is None
    assert "المتبقي لك" not in card_text_ar(card)
    assert "التكلفة التقريبية: نحو 14 نقطة\n" in card_text_ar(card)


def test_points_left_never_below_zero():
    plan = the_plan(None)
    assert build_card(build_options(plan, planner_points=0), plan, run_id="x", points_left=-2.5).points_left == 0


def test_the_card_text_is_the_mock_in_words():
    plan = the_plan([R1, R23])
    card = build_card(build_options(plan, planner_points=0.4), plan, run_id="run-1", points_left=18.7)
    assert card_text_ar(card) == "\n".join([
        "بحث شامل — الخطة المقترحة",
        "1. استحقاق قيمة أمر التغيير والملحق — الأنظمة والأحكام",
        "2. جدول الدفعات والفواتير — الأنظمة",
        "3. مبلغ 43,700 دون أمر تغيير ثانٍ — الأنظمة",
        "ثم أجمع النتائج في جواب واحد",
        "التكلفة التقريبية: نحو 14 نقطة · المتبقي لك: 18 نقطة",
        "أقدر أدمج 2 مع 3 في بحث واحد — وأوفّر نحو 3 نقاط",
        "[ نفّذ الخطة · 14 ]   [ نفّذ الأوفر · 11 ]   [ بحث عادي · 5 ]",
        "أو اكتب طلبك في خانة الرسالة",
    ])


@pytest.mark.parametrize("n, text", [(1, "1 نقطة"), (2, "2 نقطة"), (3, "3 نقاط"), (10, "10 نقاط"), (11, "11 نقطة")])
def test_points_words(n, text):
    assert points_ar(n) == text


# ── the brief (§4.9 b) ───────────────────────────────────────────────────────


def brief_view() -> PlannerView:
    return PlannerView(
        map_id="m", message_id=None,
        documents=[DocView(id="D1", item_id="i1", title="t", doc_type="contract", summary="", content_chars=1),
                   DocView(id="D2", item_id="i2", title="t", doc_type="invoice", summary="", content_chars=1)],
        angles=[
            AngleCard(id="A1", kind="strengthen", strength="strong", for_side="claimant", for_party="المقاول",
                      title="أمر التغيير ملزم", argument="أمر التغيير موقّع من المالك.",
                      legal_question="هل يلزم أمر التغيير المالك؟", weakness="الملحق غير موقّع",
                      sections=[SectionView(ref="D1.S7", label="الموكّل", owner="المقاول"),
                                SectionView(ref="D2.S3", label="?", owner="")]),
            AngleCard(id="A2", kind="answer", strength="medium", for_side="claimant", for_party="المقاول",
                      title="دفع المالك بالتأخير", argument="المالك يحتج بالتأخير.",
                      legal_question="هل التأخير يسقط المستحق؟", weakness="",
                      sections=[SectionView(ref="D1.S12", label="الخصم", owner="المالك، قدّمه المدعى عليه")]),
        ],
        section_refs=frozenset({"D1.S7", "D1.S12", "D2.S3"}),
    )


def test_the_brief_layout_exactly():
    plan = the_plan(None)
    request = r("بحث", focus="جهة المقاول: استحقاق قيمة أمر التغيير.", angles=["A2", "A1", "A9"],
                sections=["D1.S7", "D2.S3"])
    assert build_planner_brief(request, plan=plan, view=brief_view()) == "\n".join([
        "تحليل آلي لملف المستخدم (ليس نصاً من الملف ولا كلام المستخدم):",
        "- جهة المستخدم: claimant — يريد المقاول معرفة استحقاقه.",
        "- تركيز هذا البحث: جهة المقاول: استحقاق قيمة أمر التغيير.",
        "- وجه A2 [يجب الرد · متوسط]: دفع المالك بالتأخير — المالك يحتج بالتأخير. سؤال البحث: هل التأخير يسقط المستحق؟"
        " الأقسام: D1·S12 (الخصم: المالك، قدّمه المدعى عليه)",
        "- وجه A1 [يقوّي · قوي]: أمر التغيير ملزم — أمر التغيير موقّع من المالك. سؤال البحث: هل يلزم أمر التغيير المالك؟"
        " ما يضعفه: الملحق غير موقّع الأقسام: D1·S7 (الموكّل: المقاول)، D2·S3",
        "- أقسام يقرؤها البحث بتركيز: D1·S7، D2·S3",
    ]) + "\n\n" + SEARCH_INSTRUCTION


def test_a_request_without_sections_has_no_sections_line():
    out = build_planner_brief(r("x", angles=["A1"]), plan=the_plan(None), view=brief_view())
    assert "أقسام يقرؤها البحث بتركيز" not in out
    assert out.endswith("\n\n" + SEARCH_INSTRUCTION)


# ── the prompts ──────────────────────────────────────────────────────────────


def test_the_two_planner_prompt_variants():
    assert system_prompt(B.MODES[15]) is SYSTEM_PROMPT_BUDGET
    assert system_prompt(B.NO_BUDGET) is SYSTEM_PROMPT_NO_BUDGET
    for p in (SYSTEM_PROMPT_BUDGET, SYSTEM_PROMPT_NO_BUDGET):
        assert STRENGTHS_WEAKNESSES in p
        assert "Never mention points, prices or the budget in any field" in p
        assert "Fewer is better." in p and "«بحث شامل»" in p
    assert "Plan inside it." in SYSTEM_PROMPT_BUDGET and "inside the same budget" in SYSTEM_PROMPT_BUDGET
    assert "null" not in SYSTEM_PROMPT_BUDGET.replace("`cheaper` null", "").replace("or null)", "")
    assert "no budget limit" in SYSTEM_PROMPT_NO_BUDGET and "null for no cap" in SYSTEM_PROMPT_NO_BUDGET
    assert "inside the same budget" not in SYSTEM_PROMPT_NO_BUDGET
    assert "at most the budget's number" not in SYSTEM_PROMPT_NO_BUDGET


def test_a_typed_change_to_the_card_is_planned_from_the_users_version():
    # owner 2026-11-27 (convo 4b55445a): the card's side words map to modes, the
    # user's version is the plan, a request for both sides is the reason `full` needs.
    for p in (SYSTEM_PROMPT_BUDGET, SYSTEM_PROMPT_NO_BUDGET):
        assert "## When the user changes the plan card" in p
        assert "«الأنظمة» = `reg_compliance_led`, «الأحكام» = `case_led`, «الأنظمة والأحكام» = `full`" in p
        assert "the user's version is the plan" in p
        assert "a user who asks for both sides on a search has given the reason" in p
        assert "without the sides (code adds them)" in p
        assert "{" not in p.split("## When the user changes the plan card")[1].split("## The focus")[0]
    # Beyond the budget: room is made (a merge first) — only a budget mode has one.
    assert "**Beyond the budget**" in SYSTEM_PROMPT_BUDGET and "first merge two searches" in SYSTEM_PROMPT_BUDGET
    assert "Beyond the budget" not in SYSTEM_PROMPT_NO_BUDGET
    assert "`<budget>` sets no limit, so the user's version is the plan" in SYSTEM_PROMPT_NO_BUDGET


def test_the_search_instruction_is_the_plan_text():
    assert SEARCH_INSTRUCTION.startswith("How to use the analysis above (instruction for this search):\n1. ")
    assert SEARCH_INSTRUCTION.rstrip().endswith("say plainly that it remains open and what would decide it.")
    assert SEARCH_INSTRUCTION.count("\n") == 8
