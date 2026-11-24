"""The pure parts of the lookups, the confidence gates, and the two hard guarantees:
no search fee, no similarity (plan §7 "tests that must exist"). Synthetic text only.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

import agents.completion_search.agent as agent_mod
import agents.completion_search.lookups as lookups_mod
import agents.completion_search.runner as runner_mod
from agents.completion_search.confidence import (
    TitleCandidate,
    article_confidence,
    document_confidence,
    phrase_confidence,
    title_gate,
)
from agents.completion_search.lookups import (
    ArticleText,
    SupabaseLibrary,
    compound_keys,
    find_phrase,
    narrow_by_owner,
)
from agents.tool_repository.citation_scan import find_folded


def art(number: str, content: str) -> ArticleText:
    return ArticleText("reg", "نظام تجريبي", number, f"a{number}", content)


# --------------------------------------------------------------------------- #
# A phrase inside a law
# --------------------------------------------------------------------------- #


def test_find_folded_ignores_tashkeel_and_keeps_original_offsets():
    text = "الْبَيْعُ عَقْدٌ، وأحكامُ البيع تسري."
    spots = find_folded(text, "البيع")
    # A span ends on the word's last LETTER; a vowel mark after it stays outside.
    assert [text[a:b] for a, b, _ in spots] == ["الْبَيْع", "البيع"]
    assert text.index("الْبَيْع") == spots[0][0]
    assert all(whole for *_, whole in spots)


@pytest.mark.parametrize("text,phrase,whole", [
    ("والبيع جائز", "البيع", True),
    ("للبيع شروط", "بيع", True),
    ("البيع جائز", "بيع", True),            # «ال» in front does not make it another word
    ("ثمن المبيعات", "بيع", False),
    ("المبيع للمشتري", "البيع", None),      # no match at all
    ("بيعه باطل", "بيع", False),
    ("عقد   البيع\nالنافذ", "عقد البيع", True),
])
def test_whole_word_rule(text, phrase, whole):
    spots = find_folded(text, phrase)
    if whole is None:
        assert spots == []
    else:
        assert [w for *_, w in spots] == [whole]


def test_find_phrase_one_hit_per_article_in_article_order():
    hits = find_phrase(
        [art("10", "ذكر البيع هنا والبيع هناك."), art("2", "البيع عقد."), art("5", "لا شيء.")], "البيع"
    )
    assert [h.article.number for h in hits] == ["2", "10"]


@pytest.mark.parametrize("content,is_definition", [
    ("البيع عقد يملك به البائع المبيع.", True),
    ("المادة السابعة بعد الثلاثمائة: البيع عقد يملك به البائع المبيع.", True),
    ("يقصد بالألفاظ الآتية:\nالبيع: عقد تمليك.\nالإيجار: عقد منفعة.", True),
    ("في تطبيق هذا النظام يقصد بالبيع نقل الملكية.", True),
    ("يلتزم البائع بتسليم المبيع، وأحكام البيع تسري على المقايضة.", False),
    ("تسري أحكام هذا الباب على البيع.", False),
])
def test_definition_like(content, is_definition):
    (hit,) = find_phrase([art("1", content)], "البيع")
    assert hit.definition_like is is_definition


def test_excerpt_is_short_and_holds_the_phrase():
    content = ("كلام " * 100) + "البيع عقد" + (" كلام" * 100)
    (hit,) = find_phrase([art("1", content)], "البيع")
    assert "البيع عقد" in hit.excerpt and len(hit.excerpt) < 300
    assert hit.excerpt.startswith("…") and hit.excerpt.endswith("…")


def test_compound_keys():
    assert compound_keys("76/1")[:2] == ["76/1", "76-1"]
    assert "81" in compound_keys("الحادية والثمانون")
    assert compound_keys("٨١")[:2] == ["٨١", "81"]


def test_narrow_by_owner_never_hides_everything():
    pool = [
        TitleCandidate("1", "تعميم أ", extra={"owner": "وزارة العدل"}),
        TitleCandidate("2", "تعميم ب", extra={"owner": "المجلس الأعلى للقضاء"}),
    ]
    assert [c.id for c in narrow_by_owner(pool, "وزارة العدل")] == ["1"]
    assert [c.id for c in narrow_by_owner(pool, "هيئة لا وجود لها")] == ["1", "2"]
    assert [c.id for c in narrow_by_owner(pool, "")] == ["1", "2"]


# --------------------------------------------------------------------------- #
# Confidence — from facts
# --------------------------------------------------------------------------- #


def c(cid: str, *, exact=False, coverage=0.0, recall_only=False) -> TitleCandidate:
    return TitleCandidate(cid, f"عنوان {cid}", exact, coverage, recall_only)


def test_title_gate():
    assert title_gate([]).state == "none"
    assert title_gate([c("a", exact=True, coverage=1), c("b", coverage=0.9)]).winner.id == "a"
    assert title_gate([c("a", exact=True), c("b", exact=True)]).state == "several"
    best = title_gate([c("a", coverage=0.8), c("b", coverage=0.5)])
    assert (best.state, best.winner.id) == ("best", "a")
    assert title_gate([c("a", coverage=0.8), c("b", coverage=0.75)]).state == "several"
    assert title_gate([c("a", coverage=0.59)]).state == "none"          # never on a weak cover
    assert title_gate([c("a", coverage=0.59)]).shown[0].id == "a"       # … but shown as a hint
    assert title_gate([c("a", coverage=0.9, recall_only=True)]).state == "none"


def test_article_confidence():
    assert article_confidence(title_state="exact", as_asked=True) == "high"
    assert article_confidence(title_state="exact", as_asked=False) == "medium"
    assert article_confidence(title_state="exact", as_asked=True, in_force=False) == "medium"
    assert article_confidence(title_state="best", as_asked=True) == "medium"
    assert article_confidence(title_state="several", as_asked=True) is None


def test_phrase_confidence():
    sure = dict(title_state="exact", as_asked=True)
    assert phrase_confidence(**sure, whole_hits=1) == "high"
    assert phrase_confidence(**sure, whole_hits=3) == "medium"
    assert phrase_confidence(**sure, whole_hits=6) == "low"
    assert phrase_confidence(**sure, whole_hits=0) == "low"
    assert phrase_confidence(**sure, whole_hits=9, definition_hits=1, is_definition=True) == "high"
    assert phrase_confidence(**sure, whole_hits=9, definition_hits=2, is_definition=True) == "low"
    assert phrase_confidence(**sure, whole_hits=1, phrase_as_asked=False) == "medium"
    assert phrase_confidence(title_state="best", as_asked=True, whole_hits=1) == "medium"
    assert phrase_confidence(title_state="none", as_asked=True, whole_hits=1) is None


def test_document_confidence():
    assert document_confidence(title_state="exact", as_asked=True) == "high"
    assert document_confidence(title_state="exact", as_asked=False) == "medium"
    assert document_confidence(title_state="best", as_asked=True) == "medium"
    assert document_confidence(title_state="several", as_asked=True) is None


# --------------------------------------------------------------------------- #
# No search fee, no similarity (owner decisions C12, C13)
# --------------------------------------------------------------------------- #


def test_the_helper_never_touches_the_search_fee_or_an_embedder():
    for module in (agent_mod, lookups_mod, runner_mod):
        source = inspect.getsource(module)
        for banned in ("record_search_fee", "embed_regulation_query", "search_topics",
                       "hybrid_search_cases", "USE_HOUSE_EMBEDDER", "judgment_access_resolver"):
            assert banned not in source, f"{module.__name__} mentions {banned}"


class _Query:
    def __init__(self, sb, table):
        self.sb, self.table = sb, table

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.sb.ops.append((self.table, name, args))
            return self
        return call

    def execute(self):
        return type("R", (), {"data": []})()


class _Supabase:
    """Records every call; every read is empty."""

    def __init__(self):
        self.ops: list[tuple] = []
        self.rpcs: list[str] = []

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params=None):
        self.rpcs.append(name)
        return _Query(self, f"rpc:{name}")


def test_circular_lookup_makes_no_similarity_call(monkeypatch):
    import agents.simple_search.manual_search as ms
    import agents.utils.usage_sink as usage_sink

    def forbidden(*_a, **_k):
        raise AssertionError("a similarity or fee function was called")

    monkeypatch.setattr(ms, "_topics", forbidden)
    monkeypatch.setattr(ms, "_hybrid_cases", forbidden)
    monkeypatch.setattr(usage_sink, "record_search_fee", forbidden)
    sb = _Supabase()
    out = asyncio.run(SupabaseLibrary(sb).circulars("تعميم مدد التقاضي", "وزارة العدل"))
    assert out == []
    assert set(sb.rpcs) <= {"bm25_search"}


def test_service_lookup_uses_word_match_only():
    sb = _Supabase()
    assert asyncio.run(SupabaseLibrary(sb).services("قيد سجل تجاري", "")) == []
    assert sb.rpcs == []
    methods = {op[1] for op in sb.ops if op[0] == "services"}
    assert {"ilike", "text_search"} <= methods


# --------------------------------------------------------------------------- #
# Short and long forms of a law's name
# --------------------------------------------------------------------------- #

from agents.completion_search.confidence import PREFIX_LONG, PREFIX_SHORT  # noqa: E402
from agents.completion_search.lookups import executive_parent, prefix_relation  # noqa: E402


def test_prefix_relation():
    assert prefix_relation("نظام المرافعات", "نظام المرافعات الشرعية") == PREFIX_SHORT
    assert prefix_relation("نظام العمل السعودي", "نظام العمل") == PREFIX_LONG
    assert prefix_relation("نظام العمل", "نظام العمل") == ""
    assert prefix_relation("نظام ديوان المظالم", "نظام المرافعات أمام ديوان المظالم") == ""
    assert prefix_relation("نِظام المُرافعات", "نظام المرافعات الشرعية") == PREFIX_SHORT


def p(cid, title, prefix="", coverage=1.0, relevance=None) -> TitleCandidate:
    return TitleCandidate(cid, title, False, coverage, relevance=relevance, prefix=prefix)


def test_gate_takes_the_one_title_the_name_opens():
    gate = title_gate([p("a", "نظام التأمينات الاجتماعية", PREFIX_SHORT), p("b", "لائحة أخرى طويلة")])
    assert (gate.state, gate.winner.id) == ("best", "a")


def test_gate_lists_several_titles_the_name_opens():
    gate = title_gate([p("a", "نظام المرافعات الشرعية", PREFIX_SHORT),
                       p("b", "نظام المرافعات أمام ديوان المظالم", PREFIX_SHORT)])
    assert gate.state == "several" and len(gate.shown) == 2


def test_gate_takes_the_longest_title_inside_a_longer_name():
    gate = title_gate([p("a", "نظام العمل", PREFIX_LONG), p("b", "نظام العمل والعمال", PREFIX_LONG)])
    assert (gate.state, gate.winner.id) == ("best", "b")


def test_a_long_title_that_merely_contains_the_words_is_not_a_best_guess():
    # Both cover every asked word; neither is that law. Relevance keeps them apart from a real match.
    gate = title_gate([p("a", "نظام المرافعات أمام ديوان المظالم", relevance=0.75),
                       p("b", "نظام التنفيذ أمام ديوان المظالم", relevance=0.75)])
    assert gate.state == "several"
    assert title_gate([p("a", "عنوان طويل جدا يحوي الكلمات", relevance=0.4)]).state == "none"


def test_executive_parent():
    assert executive_parent("اللائحة التنفيذية لنظام العمل") == "نظام العمل"
    assert executive_parent("اللائحة التنفيذية للنظام الجزائي لجرائم التزوير") == "النظام الجزائي لجرائم التزوير"
    assert executive_parent("لائحة التنفيذية لنظام العمل") == "نظام العمل"
    assert executive_parent("نظام العمل") == ""
    assert executive_parent("اللائحة التنفيذية") == ""


def test_an_article_text_may_open_with_a_markdown_heading():
    content = "##### المادة السابعة بعد الثلاثمائة\nالبيع عقد يُمَلِّكُ بمقتضاه البائع المبيع."
    (hit,) = find_phrase([art("307", content)], "البيع")
    assert hit.definition_like and hit.defines


def test_defines_tells_the_definition_from_an_article_that_only_opens_with_the_term():
    hits = find_phrase([
        art("307", "البيع عقد يملك به البائع المبيع."),
        art("320", "البيع بشرط التجربة يجوز فيه الرد."),
    ], "البيع")
    assert [(h.article.number, h.definition_like, h.defines) for h in hits] == [
        ("307", True, True), ("320", True, False),
    ]


# --------------------------------------------------------------------------- #
# Rulings: the number, the tie-breakers, and NO unlock (owner decision C24)
# --------------------------------------------------------------------------- #

from agents.completion_search.lookups import case_title, clean_case_number, narrow_cases  # noqa: E402


@pytest.mark.parametrize("raw,clean", [
    ("4455", "4455"), ("٤٤٥٥", "4455"), (" (4455) ", "4455"), ("رقم 4455", "4455"),
    ("القضية رقم: 4455", "4455"), ("35/2/ق", "35/2/ق"), ("unk01", ""), ("UNK7", ""), ("", ""),
    ("فسخ عقد", ""),
])
def test_clean_case_number(raw, clean):
    assert clean_case_number(raw) == clean


def test_narrow_cases_drops_a_filter_that_matches_nothing():
    rows = [
        {"id": "a", "court": "التجارية", "city": "الرياض", "date_hijri": "01/05/1443", "year_gregorian": 2021},
        {"id": "b", "court": "العامة", "city": "جدة", "date_hijri": "20/09/1445", "year_gregorian": 2024},
    ]
    assert [r["id"] for r in narrow_cases(rows, "العامة", "")] == ["b"]
    assert [r["id"] for r in narrow_cases(rows, "", "١٤٤٣")] == ["a"]
    assert [r["id"] for r in narrow_cases(rows, "", "2024")] == ["b"]
    assert [r["id"] for r in narrow_cases(rows, "محكمة لا وجود لها", "1400")] == ["a", "b"]


def test_case_title_tells_two_rulings_apart():
    title = case_title({"case_number": "7001", "court": "العامة", "city": "الدمام", "date_hijri": "20/09/1445"})
    assert title == "الحكم في القضية رقم 7001 — العامة الدمام — 20/09/1445هـ"


class _CaseSupabase(_Supabase):
    """Returns one ruling for any read of the cases table; everything else is empty."""

    ROW = {"id": "11111111-1111-1111-1111-111111111111", "case_ref": "c-1", "court": "التجارية", "city": "الرياض",
           "case_number": "4455", "judgment_number": "", "date_hijri": "12/03/1444", "content": "نص الحكم الكامل هنا."}

    def table(self, name):
        query = _Query(self, name)
        if name == "cases":
            query.execute = lambda: type("R", (), {"data": [dict(self.ROW)]})()
        return query


def test_a_ruling_is_opened_without_the_librarys_unlock(monkeypatch):
    import agents.simple_search.runner as ss_runner

    def forbidden(*_a, **_k):
        raise AssertionError("the unlock of the library was called")

    monkeypatch.setattr(ss_runner, "judgment_access_resolver", forbidden)
    try:
        import backend.app.services.library_service as library_service
        monkeypatch.setattr(library_service, "resolve_access", forbidden)
    except Exception:  # noqa: BLE001 — the backend package is optional here
        pass
    sb = _CaseSupabase()
    text = asyncio.run(SupabaseLibrary(sb).open_text("case", _CaseSupabase.ROW["id"], "حكم"))
    assert "نص الحكم الكامل هنا." in text
    assert sb.rpcs == []
