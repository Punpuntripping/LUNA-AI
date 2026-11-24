"""An in-memory ``Library`` and a scripted model for the Completion Search tests.

Every text here is SYNTHETIC. The fake applies the same title rules as the real
library (exact normalized title, word coverage) so the confidence gates are exercised
on real behaviour, with no database.
"""
from __future__ import annotations

from typing import Any, Callable

from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.completion_search.confidence import GateResult, TitleCandidate, title_gate
from agents.completion_search.lookups import ArticleText, LawLookup, narrow_by_owner
from agents.tool_repository.citation_scan import TitleIndex
from agents.tool_repository.fetch_article import _strict_exact, article_number_keys, coverage

CIVIL = "نظام المعاملات المدنية"
LABOR = "نظام العمل"
PROC = "نظام المرافعات الشرعية"
PROC_BOARD = "نظام المرافعات أمام ديوان المظالم"
DRAFT = "نظام التجربة المقترح"
EMPTY = "نظام الفراغ التجريبي"

LAWS: dict[str, dict] = {
    CIVIL: {"id": "reg-civil", "status": "in_force", "articles": {
        "56": "إذا كان محل الالتزام مخالفاً للنظام العام كان العقد باطلاً.",
        "234": "يجوز للدائن أن يطلب تنفيذ الالتزام عيناً متى كان ذلك ممكناً.",
        "307": "الْبَيْعُ عَقْدٌ يُمَلِّكُ بمقتضاه البائعُ المبيعَ للمشتري مقابل ثمن نقدي.",
        "308": "يلتزم البائع بتسليم المبيع، وأحكام البيع تسري على المقايضة.",
        "309": "ثمن المبيعات يحدد باتفاق الطرفين.",
    }},
    LABOR: {"id": "reg-labor", "status": "in_force_amended", "articles": {
        "77": "إذا أنهي العقد لسبب غير مشروع كان للطرف المتضرر الحق في تعويض.",
        "80": "لا يجوز لصاحب العمل فسخ العقد دون مكافأة إلا في حالات محددة.",
        **{str(n): f"نص المادة {n} ويذكر فيها الأجر صراحة." for n in range(90, 97)},
    }},
    PROC: {"id": "reg-proc", "status": "in_force", "articles": {"76": "الدفع بعدم الاختصاص النوعي تحكم به المحكمة."}},
    PROC_BOARD: {"id": "reg-proc-board", "status": "in_force", "articles": {"76": "نص آخر للمادة ذاتها في نظام آخر."}},
    DRAFT: {"id": "reg-draft", "status": "consultation_ended", "articles": {"3": "نص مشروع لم يصدر."}},
    EMPTY: {"id": "reg-empty", "status": "in_force", "articles": {}},
}

CIRCULARS = [
    {"id": "circ-1", "title": "تعميم بشأن مدد التقاضي في الدعاوى العمالية", "owner": "وزارة العدل", "text": "نص التعميم الأول."},
    {"id": "circ-2", "title": "تعميم بشأن مدد التقاضي في الدعاوى التجارية", "owner": "المجلس الأعلى للقضاء", "text": "نص التعميم الثاني."},
    {"id": "circ-3", "title": "تعميم ضوابط التبليغ الإلكتروني", "owner": "وزارة العدل", "text": "نص التعميم الثالث."},
]

SERVICES = [
    {"id": "svc-1", "title": "وزارة التجارة - قيد سجل تجاري لمؤسسة فردية", "owner": "وزارة التجارة", "text": "وصف الخدمة الأولى."},
    {"id": "svc-2", "title": "وزارة التجارة - قيد سجل تجاري لشركة", "owner": "وزارة التجارة", "text": "وصف الخدمة الثانية."},
    {"id": "svc-3", "title": "وزارة العدل - إصدار وكالة", "owner": "وزارة العدل", "text": "وصف الخدمة الثالثة."},
]


class FakeLibrary:
    """``Library`` over the dictionaries above. Records every call."""

    def __init__(self, laws: dict | None = None) -> None:
        self.laws = laws if laws is not None else LAWS
        self.calls: list[tuple] = []

    async def title_index(self) -> TitleIndex:
        return TitleIndex([(spec["id"], title) for title, spec in self.laws.items()])

    def _candidates(self, name: str) -> list[TitleCandidate]:
        out = []
        for title, spec in self.laws.items():
            out.append(TitleCandidate(
                spec["id"], title, _strict_exact(name, title), coverage(name, title),
                extra={"status": spec["status"]},
            ))
        out = [c for c in out if c.exact or c.coverage > 0]
        out.sort(key=lambda c: (c.exact, c.coverage), reverse=True)
        return out

    async def find_law(self, name: str) -> LawLookup:
        self.calls.append(("find_law", name))
        return LawLookup(title_gate(self._candidates(name)))

    def _spec(self, lookup: LawLookup) -> dict:
        return next(s for s in self.laws.values() if s["id"] == lookup.law.id)

    async def article(self, lookup: LawLookup, number: str) -> list[ArticleText]:
        self.calls.append(("article", lookup.law.title, number))
        spec = self._spec(lookup)
        for key in article_number_keys(number):
            if key in spec["articles"]:
                return [ArticleText(spec["id"], lookup.law.title, key, f"{spec['id']}-a{key}", spec["articles"][key])]
        return []

    async def articles(self, lookup: LawLookup) -> list[ArticleText]:
        self.calls.append(("articles", lookup.law.title))
        spec = self._spec(lookup)
        return [ArticleText(spec["id"], lookup.law.title, n, f"{spec['id']}-a{n}", text)
                for n, text in spec["articles"].items()]

    async def has_articles(self, lookup: LawLookup) -> bool:
        return bool(self._spec(lookup)["articles"])

    def _documents(self, rows: list[dict], words: str, owner: str) -> list[TitleCandidate]:
        cands = [
            TitleCandidate(r["id"], r["title"],
                           _strict_exact(words, r["title"]) or _strict_exact(words, r["title"].split(" - ", 1)[-1]),
                           coverage(words, r["title"]), label=r["owner"], extra={"owner": r["owner"]})
            for r in rows
        ]
        cands = [c for c in cands if c.exact or c.coverage > 0]
        cands.sort(key=lambda c: (c.exact, c.coverage), reverse=True)
        return narrow_by_owner(cands, owner)

    async def circulars(self, words: str, issuer: str = "") -> list[TitleCandidate]:
        self.calls.append(("circulars", words, issuer))
        return self._documents(CIRCULARS, words, issuer)

    async def services(self, name: str, provider: str = "") -> list[TitleCandidate]:
        self.calls.append(("services", name, provider))
        return self._documents(SERVICES, name, provider)

    async def open_text(self, kind: str, object_id: str, title: str) -> str:
        self.calls.append(("open_text", kind, object_id))
        rows = CIRCULARS if kind == "circular" else SERVICES
        return next(r["text"] for r in rows if r["id"] == object_id)


def exact_lookup(title: str, laws: dict | None = None) -> LawLookup:
    spec = (laws or LAWS)[title]
    cand = TitleCandidate(spec["id"], title, True, 1.0, extra={"status": spec["status"]})
    return LawLookup(GateResult("exact", cand, (cand,)))


Step = Callable[[list[str]], list[tuple[str, dict]] | dict]


def scripted_model(*steps: Step) -> FunctionModel:
    """A model that plays ``steps`` in order.

    Each step gets the tool results of the previous turn (a list of strings) and returns
    either tool calls — ``[(tool_name, args), …]`` — or the final answer as a dict.
    The last step is repeated if the run asks for more turns (output retries).
    """
    turn = {"n": 0}

    async def fn(messages: list, info: AgentInfo) -> ModelResponse:
        returns = [
            str(part.content)
            for part in (messages[-1].parts if messages else [])
            if isinstance(part, ToolReturnPart)
        ]
        step = steps[min(turn["n"], len(steps) - 1)]
        turn["n"] += 1
        out = step(returns)
        if isinstance(out, dict):
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, out)])
        return ModelResponse(parts=[ToolCallPart(name, args) for name, args in out])

    return FunctionModel(fn)


def answer(*searches: dict[str, Any]) -> dict:
    """The model's final answer: ``answer({"search_id": "S1", "selected": ["T1"], "status": "found"})``."""
    return {"searches": [{"asked": "", **s} for s in searches]}


# --------------------------------------------------------------------------- #
# Rulings and judicial principles (plan §4.2, group 2)
# --------------------------------------------------------------------------- #

CASES = [
    {"id": "case-1", "case_number": "4455", "court": "التجارية", "city": "الرياض", "date_hijri": "12/03/1444",
     "year_gregorian": 2022, "text": "نص الحكم الأول كاملاً."},
    {"id": "case-2", "case_number": "7001", "court": "التجارية", "city": "جدة", "date_hijri": "01/05/1443",
     "year_gregorian": 2021, "text": "نص حكم تجاري."},
    {"id": "case-3", "case_number": "7001", "court": "العامة", "city": "الدمام", "date_hijri": "20/09/1445",
     "year_gregorian": 2024, "text": "نص حكم عام."},
    {"id": "case-4", "case_number": "unk01", "court": "العامة", "city": "", "date_hijri": "", "year_gregorian": None,
     "text": "حكم قديم بلا رقم."},
]

PRINCIPLES = [
    {"id": "pr-1", "principle_no": 12, "chapter_title": "الإثبات", "text": "البينة على من ادعى واليمين على من أنكر."},
    {"id": "pr-2", "principle_no": 40, "chapter_title": "العقود", "text": "العقد شريعة المتعاقدين ما لم يخالف النظام."},
    {"id": "pr-3", "principle_no": 41, "chapter_title": "العقود", "text": "العقد لا يلزم غير عاقديه في الأصل."},
]


async def _fake_cases(self, case_number: str, court: str = "", year: str = ""):
    from agents.completion_search.lookups import case_title, clean_case_number, narrow_cases

    self.calls.append(("cases", case_number, court, year))
    number = clean_case_number(case_number)
    rows = [r for r in CASES if number and r["case_number"] == number]
    return [
        TitleCandidate(r["id"], case_title(r), True, 1.0, label=r["court"], extra={"owner": r["court"]})
        for r in narrow_cases(rows, court, year)
    ]


async def _fake_principles(self, number: str = "", words: str = ""):
    self.calls.append(("principles", number, words))

    def reach(r: dict) -> str:
        return r["chapter_title"] + " " + r["text"]

    if number:
        rows = [r for r in PRINCIPLES if str(r["principle_no"]) == str(number).strip()]
        exact = True
    else:
        rows = [r for r in PRINCIPLES if coverage(words, reach(r)) > 0]
        exact = False
    out = [
        TitleCandidate(
            r["id"], "المبدأ رقم " + str(r["principle_no"]) + " — " + r["chapter_title"], exact,
            1.0 if exact else coverage(words, reach(r)),
            label=r["chapter_title"], extra={"text": r["text"], "principle_no": str(r["principle_no"])},
        )
        for r in rows
    ]
    out.sort(key=lambda c: c.coverage, reverse=True)
    return out


_plain_open_text = FakeLibrary.open_text


async def _open_text(self, kind: str, object_id: str, title: str) -> str:
    if kind == "case":
        self.calls.append(("open_text", kind, object_id))
        return next(r["text"] for r in CASES if r["id"] == object_id)
    return await _plain_open_text(self, kind, object_id, title)


FakeLibrary.cases = _fake_cases
FakeLibrary.principles = _fake_principles
FakeLibrary.open_text = _open_text
