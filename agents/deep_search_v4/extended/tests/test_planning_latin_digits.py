"""The plan card and the report titles show Latin digits only (e2e run 2026-11-24: the model
copied the file's «٢١٠/٣١٠ ألف» into a search title)."""
from agents.deep_search_v4.extended.models import CheaperOption, ExtendedPlan, ExtendedRequest
from agents.deep_search_v4.extended.planner import decode_plan


def _req(title: str) -> ExtendedRequest:
    return ExtendedRequest(title=title, mode="reg_compliance_led", max_sub_queries=6, focus="تركيز ٢١٠")


def test_titles_and_note_become_latin_without_codec():
    plan = ExtendedPlan(
        understanding="فهم ٣",
        requests=[_req("اختلاف السند ٢١٠/٣١٠ ألف"), _req("مدة ۱۲ شهراً")],
        cheaper=CheaperOption(note="أدمج البحث ٢ مع ٣", requests=[_req("بحث ٤")]),
    )
    out = decode_plan(None, plan)
    assert [r.title for r in out.requests] == ["اختلاف السند 210/310 ألف", "مدة 12 شهراً"]
    assert out.cheaper.note == "أدمج البحث 2 مع 3"
    assert out.cheaper.requests[0].title == "بحث 4"
    # internal fields are left as the model wrote them
    assert out.requests[0].focus == "تركيز ٢١٠"
    assert out.understanding == "فهم ٣"


def test_plan_without_cheaper_is_fine():
    out = decode_plan(None, ExtendedPlan(understanding="", requests=[_req("عنوان 5")]))
    assert out.cheaper is None and out.requests[0].title == "عنوان 5"
