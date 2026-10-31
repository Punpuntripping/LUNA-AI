"""``simple_search`` for a law with a replacement: the question and the opened text.

Two surfaces. The searcher asks old or new in period 1 only, and a re-call with a
marker resolves with no second question. The unfold labels every level that opens
a regulation, so a result reached through ``manual_search`` is covered too.

The tools run on a ``FunctionModel``, which scripts one tool call and reads its
return back, so nothing here needs an LLM. A law with no pair must return the same
bytes, and that is compared against literals. Every law is invented. Sync only,
no network: CI has no pytest-asyncio.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from agents.simple_search import searcher as searcher_module
from agents.simple_search import unfold as unfold_module
from agents.simple_search.models import ResolvedObject
from agents.simple_search.searcher import (
    SEARCHER_LIMITS,
    SearcherDecision,
    SearcherDeps,
    create_searcher_agent,
)
from agents.simple_search.unfold import unfold
from shared.library.supersession import reset_cache

SUPERSEDED_ON = date(2030, 3, 15)
PERIOD_1_END = date(2030, 6, 15)
PERIOD_2_END = date(2031, 3, 15)
DAY = timedelta(days=1)

IN_UPCOMING = SUPERSEDED_ON - DAY
IN_PERIOD_1 = SUPERSEDED_ON + DAY
IN_PERIOD_2 = PERIOD_1_END + DAY
IN_AFTER = PERIOD_2_END + DAY

OLD_ID = "a0000000-0000-4000-8000-000000000001"
NEW_ID = "a0000000-0000-4000-8000-000000000002"
OTHER_ID = "a0000000-0000-4000-8000-000000000003"
OLD_CHUNK_ID = "c0000000-0000-4000-8000-000000000001"
NEW_CHUNK_ID = "c0000000-0000-4000-8000-000000000002"
OTHER_CHUNK_ID = "c0000000-0000-4000-8000-000000000003"
OLD_46_ID = "d0000000-0000-4000-8000-000000000001"
NEW_46_ID = "d0000000-0000-4000-8000-000000000002"
OLD_70_ID = "d0000000-0000-4000-8000-000000000003"
NEW_5_ID = "d0000000-0000-4000-8000-000000000004"
OTHER_7_ID = "d0000000-0000-4000-8000-000000000005"

NOTE = "ثانياً: يستمر العمل بأحكام الرعي الموسمي حتى صدور نظام المراعي."
OTHER_LAW = "نظام المراعي"
OTHER_ARTICLE = "المادة السابعة: يُنشأ سجل عام للمراعي."

KINDS = {
    "نظام": ("نظام الواحات", "نظام الواحات (القديم)", "law_statute"),
    "لائحة": (
        "اللائحة التنفيذية لنظام الواحات",
        "اللائحة التنفيذية لنظام الواحات (القديم)",
        "executive_regulation",
    ),
}


class FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self.rows = list(client.tables.get(table, []))
        self.negated = False
        self.single = False

    def select(self, _columns):
        return self

    @property
    def not_(self):
        if self.client.load_fails:
            raise RuntimeError("no such column: superseded_by")
        self.negated = True
        return self

    def is_(self, column, _value):
        wanted = self.negated
        self.rows = [r for r in self.rows if (r.get(column) is not None) == wanted]
        return self

    def eq(self, column, value):
        self.rows = [r for r in self.rows if str(r.get(column)) == str(value)]
        return self

    def in_(self, column, values):
        self.rows = [r for r in self.rows if r.get(column) in set(values)]
        return self

    def ilike(self, column, pattern):
        needle = pattern.replace("%", "")
        self.rows = [r for r in self.rows if needle in str(r.get(column) or "")]
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, low, high):
        self.rows = self.rows[low : high + 1]
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def maybe_single(self):
        self.single = True
        return self

    def execute(self):
        if self.single:
            return SimpleNamespace(data=self.rows[0] if self.rows else None)
        return SimpleNamespace(data=self.rows)


class FakeSupabase:
    def __init__(self, tables):
        self.tables = tables
        self.load_fails = False

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, _name, _params):
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[]))


def regulation(reg_id, title, bucket="law_statute", **extra):
    return {
        "id": reg_id,
        "reg_ref": reg_id,
        "title": title,
        "clean_title": title,
        "doc_type_bucket": bucket,
        "doc_type_raw": "نظام",
        "landing_url": "",
        "scope": "",
        "status_class": "in_force",
        "status_raw": "ساري",
        **extra,
    }


def article(article_id, reg_id, number, content):
    return {
        "id": article_id,
        "regulation_id": reg_id,
        "article_number": number,
        "content": content,
        "chunk_parent_id": None,
    }


def chunk(chunk_id, reg_id, position):
    return {
        "id": chunk_id,
        "chunk_ref": f"{reg_id}_chunk_{position}",
        "regulation_id": reg_id,
        "position": position,
        "corpus": "with_articles",
        "word_count": 10,
        "title": "الباب الأول",
        "summary": "ملخص",
        "context": "",
        "content": "نص المقطع",
        "prev_chunk_id": None,
        "next_chunk_id": None,
    }


def make_client(kind="نظام", *, with_pair=True, pending=True):
    new, old, bucket = KINDS[kind]
    superseded_by = [{"reg_ref": NEW_ID, "title": new}]
    if pending:
        superseded_by.append(
            {"reg_ref": None, "title": OTHER_LAW, "part": "الرعي", "pending": True,
             "chunk_refs": [f"{OLD_ID}_chunk_1"]}
        )
    pair = {
        "superseded_by": superseded_by,
        "superseded_dates": {
            "superseded_on": SUPERSEDED_ON.isoformat(),
            "period_1_end": PERIOD_1_END.isoformat(),
            "period_2_end": PERIOD_2_END.isoformat(),
        },
    } if with_pair else {}
    return FakeSupabase({
        "regulations_v2": [
            regulation(OLD_ID, old, bucket, status_class="cancelled", status_raw="لاغي", **pair),
            regulation(NEW_ID, new, bucket, transition_period_note=NOTE if with_pair else None),
            regulation(OTHER_ID, OTHER_LAW),
        ],
        "articles_v2": [
            article(OLD_46_ID, OLD_ID, "46", "نص المادة 46 القديمة"),
            article(NEW_46_ID, NEW_ID, "46", "نص المادة 46 الجديدة"),
            article(OLD_70_ID, OLD_ID, "70", "نص المادة 70 القديمة"),
            article(NEW_5_ID, NEW_ID, "5", "نص المادة 5 الجديدة"),
            article(OTHER_7_ID, OTHER_ID, "7", OTHER_ARTICLE),
        ],
        "chunks_v2": [
            chunk(OLD_CHUNK_ID, OLD_ID, 1),
            chunk(NEW_CHUNK_ID, NEW_ID, 1),
            chunk(OTHER_CHUNK_ID, OTHER_ID, 1),
        ],
    })


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_cache()
    yield
    reset_cache()


@pytest.fixture(params=sorted(KINDS))
def law(request):
    new, old, _ = KINDS[request.param]
    return SimpleNamespace(
        kind=request.param, new=new, old=old, new_label=f"{new} (الجديد)",
        client=make_client(request.param),
    )


@pytest.fixture
def call_tool(monkeypatch):
    """Run one resolver tool on a real agent; returns ``(text, deps, fees)``."""

    def _call(client, tool, args, today):
        calls = []

        def model(_messages, info):
            calls.append(1)
            if len(calls) == 1:
                return ModelResponse(parts=[ToolCallPart(tool_name=tool, args=args)])
            done = SearcherDecision(aborted=True, abort_reason="تم")
            return ModelResponse(
                parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=done.model_dump())]
            )

        fees = []
        monkeypatch.setattr(
            searcher_module, "get_agent_model", lambda *_a, **_k: FunctionModel(model)
        )
        monkeypatch.setattr(searcher_module, "riyadh_today", lambda: today)
        monkeypatch.setattr(searcher_module, "record_search_fee", lambda **k: fees.append(k))
        deps = SearcherDeps(supabase=client, user_id="u1", conversation_id="c1")
        result = create_searcher_agent().run_sync("س", deps=deps, usage_limits=SEARCHER_LIMITS)
        [text] = [
            part.content
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == tool
        ]
        return text, deps, fees

    return _call


def resolve_law(call_tool, client, title, today):
    return call_tool(client, "resolve_regulation", {"regulation_title": title}, today)


def resolve_art(call_tool, client, title, number, today):
    return call_tool(
        client, "resolve_article",
        {"regulation_title": title, "article_number": number}, today,
    )


def assert_question(text, deps, law):
    assert text.startswith("AMBIGUOUS:")
    assert law.old in text
    assert law.new_label in text
    assert "ولا تسأله مرة أخرى" in text
    assert deps.candidates == {}  # nothing is handed on until the user answers


# -- resolve_regulation -------------------------------------------------------


def test_period_1_a_plain_title_asks_old_or_new(call_tool, law):
    text, deps, fees = resolve_law(call_tool, law.client, law.new, IN_PERIOD_1)
    assert_question(text, deps, law)
    assert len(fees) == 1  # the fee is per call, the same as any resolve


@pytest.mark.parametrize(
    "spelling, reg_id",
    [
        ("{old}", OLD_ID),
        ("{new} القديم", OLD_ID),
        ("{new} (الجديد)", NEW_ID),
    ],
)
def test_a_marked_recall_resolves_with_no_question(call_tool, law, spelling, reg_id):
    title = spelling.format(old=law.old, new=law.new)
    text, deps, _ = resolve_law(call_tool, law.client, title, IN_PERIOD_1)
    assert text.startswith("C1 — ")
    assert deps.candidates["C1"].regulation_id == reg_id


def test_the_question_lists_titles_that_resolve_when_passed_back(call_tool, law):
    # The contract: «أعد النداء باسمه كما هو مكتوب أعلاه حرفيًّا».
    text, _, _ = resolve_law(call_tool, law.client, law.new, IN_PERIOD_1)
    for label, reg_id in ((law.old, OLD_ID), (law.new_label, NEW_ID)):
        assert f"«{label}»" in text
        _, deps, _ = resolve_law(call_tool, law.client, label, IN_PERIOD_1)
        assert deps.candidates["C1"].regulation_id == reg_id


@pytest.mark.parametrize("today", [IN_PERIOD_2, IN_AFTER])
def test_from_period_2_a_plain_title_opens_the_new_law(call_tool, law, today):
    text, deps, _ = resolve_law(call_tool, law.client, law.new, today)
    assert text == f"C1 — {law.new_label}"
    assert deps.candidates["C1"].regulation_id == NEW_ID
    assert deps.candidates["C1"].title == law.new  # the card keeps the real title


@pytest.mark.parametrize("today", [IN_PERIOD_2, IN_AFTER])
def test_from_period_2_an_explicit_old_still_reaches_the_old_law(call_tool, law, today):
    text, deps, _ = resolve_law(call_tool, law.client, f"{law.new} القديم", today)
    assert text == f"C1 — {law.old}"
    assert deps.candidates["C1"].regulation_id == OLD_ID


def test_upcoming_asks_nothing_and_opens_the_law_in_force(call_tool, law):
    text, deps, _ = resolve_law(call_tool, law.client, law.new, IN_UPCOMING)
    assert text == f"C1 — {law.old}"
    assert deps.candidates["C1"].regulation_id == OLD_ID


# -- resolve_article ----------------------------------------------------------


def test_period_1_a_number_in_both_asks(call_tool, law):
    text, deps, fees = resolve_art(call_tool, law.client, law.new, "46", IN_PERIOD_1)
    assert_question(text, deps, law)
    assert len(fees) == 1


@pytest.mark.parametrize(
    "spelling, reg_id, article_id",
    [("{old}", OLD_ID, OLD_46_ID), ("{new} (الجديد)", NEW_ID, NEW_46_ID)],
)
def test_a_marked_recall_opens_that_article(call_tool, law, spelling, reg_id, article_id):
    title = spelling.format(old=law.old, new=law.new)
    text, deps, _ = resolve_art(call_tool, law.client, title, "46", IN_PERIOD_1)
    assert text.startswith("C1 — المادة 46 من ")
    assert (deps.candidates["C1"].regulation_id, deps.candidates["C1"].article_id) == (
        reg_id, article_id,
    )


@pytest.mark.parametrize(
    "number, reg_id, article_id",
    [("70", OLD_ID, OLD_70_ID), ("5", NEW_ID, NEW_5_ID)],
)
@pytest.mark.parametrize("today", [IN_UPCOMING, IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_a_number_in_one_version_skips_the_question(
    call_tool, law, today, number, reg_id, article_id
):
    text, deps, _ = resolve_art(call_tool, law.client, law.new, number, today)
    assert not text.startswith("AMBIGUOUS:")
    assert "الترقيم يختلف" in text
    assert deps.candidates["C1"].regulation_id == reg_id
    assert deps.candidates["C1"].article_id == article_id


def test_a_marker_for_the_side_without_the_number_opens_the_other_side(call_tool, law):
    text, deps, _ = resolve_art(call_tool, law.client, law.old, "5", IN_PERIOD_1)
    assert deps.candidates["C1"].regulation_id == NEW_ID
    assert "الترقيم يختلف" in text


@pytest.mark.parametrize("today", [IN_PERIOD_2, IN_AFTER])
def test_from_period_2_a_number_in_both_opens_the_new_article(call_tool, law, today):
    text, deps, _ = resolve_art(call_tool, law.client, law.new, "46", today)
    assert text == f"C1 — المادة 46 من {law.new_label}"
    assert deps.candidates["C1"].article_id == NEW_46_ID


@pytest.mark.parametrize("today", [IN_PERIOD_2, IN_AFTER])
def test_from_period_2_an_explicit_old_reaches_the_old_article(call_tool, law, today):
    text, deps, _ = resolve_art(call_tool, law.client, f"{law.new} القديم", "46", today)
    assert text == f"C1 — المادة 46 من {law.old}"
    assert deps.candidates["C1"].article_id == OLD_46_ID


def test_upcoming_a_number_in_both_opens_the_old_article(call_tool, law):
    text, deps, _ = resolve_art(call_tool, law.client, law.new, "46", IN_UPCOMING)
    assert text == f"C1 — المادة 46 من {law.old}"
    assert deps.candidates["C1"].article_id == OLD_46_ID


def test_a_number_in_neither_keeps_the_owns_fallback(call_tool, law):
    text, deps, _ = resolve_art(call_tool, law.client, law.new, "999", IN_PERIOD_2)
    assert "غير موجودة في جدول المواد" in text
    obj = deps.candidates["C1"]
    assert (obj.regulation_id, obj.article_id, obj.article_number) == (NEW_ID, "", "999")


def test_the_card_keeps_the_real_title_and_the_model_gets_the_label(call_tool, law):
    _, deps, _ = resolve_art(call_tool, law.client, law.new, "46", IN_PERIOD_2)
    assert deps.candidates["C1"].subtitle == law.new


# -- A pair sharing one title: the usual shape of a replaced law --------------


@pytest.fixture(
    params=[(None, "{new}"), ("{new} 1425", "{new} 1440")], ids=["identical", "tied"]
)
def shared(request, law):
    """The pair stored under one ``title``, with these ``clean_title``s."""
    old_clean, new_clean = request.param
    old_row, new_row = law.client.tables["regulations_v2"][:2]
    old_row.update(title=law.new, clean_title=old_clean and old_clean.format(new=law.new))
    new_row.update(title=law.new, clean_title=new_clean.format(new=law.new))
    return law.client


def assert_asks_between_versions(text, deps):
    assert text.startswith("AMBIGUOUS:")
    assert "(القديم)" in text
    assert "(الجديد)" in text
    assert "ولا تسأله مرة أخرى" in text
    assert deps.candidates == {}


def test_a_shared_title_asks_in_period_1(call_tool, law, shared):
    text, deps, _ = resolve_law(call_tool, shared, law.new, IN_PERIOD_1)
    assert_asks_between_versions(text, deps)
    text, deps, _ = resolve_art(call_tool, shared, law.new, "46", IN_PERIOD_1)
    assert_asks_between_versions(text, deps)


@pytest.mark.parametrize("today", [IN_PERIOD_2, IN_AFTER])
def test_a_shared_title_opens_the_new_law_from_period_2(call_tool, law, shared, today):
    _, deps, _ = resolve_law(call_tool, shared, law.new, today)
    assert deps.candidates["C1"].regulation_id == NEW_ID
    _, deps, _ = resolve_art(call_tool, shared, law.new, "46", today)
    assert deps.candidates["C1"].article_id == NEW_46_ID


def test_a_shared_title_reaches_the_old_law_when_named(call_tool, law, shared):
    _, deps, _ = resolve_law(call_tool, shared, f"{law.new} القديم", IN_PERIOD_1)
    assert deps.candidates["C1"].regulation_id == OLD_ID
    _, deps, _ = resolve_art(call_tool, shared, f"{law.new} القديم", "46", IN_PERIOD_1)
    assert deps.candidates["C1"].article_id == OLD_46_ID


# -- Laws with no pair: the same bytes ----------------------------------------


@pytest.mark.parametrize("today", [IN_UPCOMING, IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_a_no_pair_law_is_byte_identical(call_tool, law, today):
    text, deps, _ = resolve_law(call_tool, law.client, OTHER_LAW, today)
    assert text == f"C1 — {OTHER_LAW}"
    assert deps.candidates["C1"].regulation_id == OTHER_ID

    text, deps, _ = resolve_art(call_tool, law.client, OTHER_LAW, "7", today)
    assert text == f"C1 — المادة 7 من {OTHER_LAW}"
    assert deps.candidates["C1"].article_id == OTHER_7_ID

    text, _, _ = resolve_art(call_tool, law.client, OTHER_LAW, "999", today)
    assert text == (
        f"C1 — المادة 999 من {OTHER_LAW} "
        "(غير موجودة في جدول المواد؛ سيُحاول استخراجها من المقطع الذي يضمّها)"
    )


def test_unknown_titles_are_byte_identical(call_tool, law):
    text, _, _ = resolve_law(call_tool, law.client, "نظام لا وجود له", IN_PERIOD_1)
    assert text == "لم يُعثر على نظام بهذا الاسم"
    text, _, _ = resolve_art(call_tool, law.client, "نظام لا وجود له", "7", IN_PERIOD_1)
    assert text == "لم يُعثر على المادة 7: النظام «نظام لا وجود له» غير موجود"


def test_a_failed_load_turns_the_question_off(call_tool, law):
    # Fail open: the map is empty, so the title resolves as it does today.
    client = make_client(law.kind)
    client.load_fails = True
    text, _, _ = resolve_law(call_tool, client, law.new, IN_PERIOD_1)
    assert text == f"C1 — {law.new}"


# -- The opened document ------------------------------------------------------


def open_object(monkeypatch, client, obj, today):
    monkeypatch.setattr(unfold_module, "riyadh_today", lambda: today)
    return asyncio.run(unfold(client, obj))


LEVELS = {
    "regulation_doc": lambda reg_id, chunk_id, article_id: ResolvedObject(
        level="regulation_doc", regulation_id=reg_id
    ),
    "chunk": lambda reg_id, chunk_id, article_id: ResolvedObject(level="chunk", chunk_id=chunk_id),
    "article": lambda reg_id, chunk_id, article_id: ResolvedObject(
        level="article", regulation_id=reg_id, article_id=article_id
    ),
}


@pytest.mark.parametrize("level", sorted(LEVELS))
@pytest.mark.parametrize(
    "reg_id, chunk_id, article_id",
    [(OLD_ID, OLD_CHUNK_ID, OLD_46_ID), (NEW_ID, NEW_CHUNK_ID, NEW_46_ID)],
)
@pytest.mark.parametrize("today", [IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_every_level_carries_the_label_and_the_note(
    monkeypatch, law, level, reg_id, chunk_id, article_id, today
):
    result = open_object(monkeypatch, law.client, LEVELS[level](reg_id, chunk_id, article_id), today)
    assert result.ok
    if today == IN_AFTER and reg_id == NEW_ID:
        # After the second period a new law is plain law, even with a part of the
        # old one still pending: that part has no end date.
        assert "## حالة هذه النسخة" not in result.text
        assert "## الأحكام الانتقالية" not in result.text
        return
    label = law.old if reg_id == OLD_ID else law.new_label
    assert f"**النسخة:** {label}" in result.text
    assert f"## الأحكام الانتقالية\n\n{NOTE}" in result.text
    assert "اذكر في جوابك أي نسخة هذه" in result.text
    assert result.chars == len(result.text)  # the section is inside the measurement


def test_the_section_precedes_the_body(monkeypatch, law):
    obj = ResolvedObject(level="article", regulation_id=OLD_ID, article_id=OLD_46_ID)
    text = open_object(monkeypatch, law.client, obj, IN_PERIOD_1).text
    assert text.index("## حالة هذه النسخة") < text.index("نص المادة 46 القديمة")


def test_upcoming_gives_the_date_and_keeps_the_provisions_out(monkeypatch, law):
    obj = ResolvedObject(level="regulation_doc", regulation_id=OLD_ID)
    text = open_object(monkeypatch, law.client, obj, IN_UPCOMING).text
    assert "2030/03/15م" in text
    assert NOTE not in text


def test_a_new_law_with_nothing_pending_reads_like_any_other(monkeypatch, law):
    client = make_client(law.kind, pending=False)
    obj = ResolvedObject(level="regulation_doc", regulation_id=NEW_ID)
    text = open_object(monkeypatch, client, obj, IN_AFTER).text
    assert "حالة هذه النسخة" not in text


def test_an_old_law_with_nothing_pending_is_labelled_repealed(monkeypatch, law):
    client = make_client(law.kind, pending=False)
    obj = ResolvedObject(level="regulation_doc", regulation_id=OLD_ID)
    text = open_object(monkeypatch, client, obj, IN_AFTER).text
    assert f"ملغي — حلّ محله {law.new}" in text
    assert "الأحكام الانتقالية" not in text


def test_an_old_chunk_is_not_called_repealed_while_it_applies(monkeypatch, law):
    # The framing's status line reads status_class, which is already `cancelled`.
    obj = ResolvedObject(level="chunk", chunk_id=OLD_CHUNK_ID)
    text = open_object(monkeypatch, law.client, obj, IN_PERIOD_1).text
    assert "لم يعد سارياً" not in text


def test_a_repealed_law_with_no_pair_keeps_its_status_line(monkeypatch):
    client = make_client(with_pair=False)
    obj = ResolvedObject(level="chunk", chunk_id=OLD_CHUNK_ID)
    assert "لم يعد سارياً" in open_object(monkeypatch, client, obj, IN_PERIOD_1).text


@pytest.mark.parametrize("level", sorted(LEVELS))
@pytest.mark.parametrize("today", [IN_UPCOMING, IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_a_no_pair_regulation_opens_byte_identically(monkeypatch, law, level, today):
    obj = LEVELS[level](OTHER_ID, OTHER_CHUNK_ID, OTHER_7_ID)
    with_pairs_loaded = open_object(monkeypatch, law.client, obj, today)
    reset_cache()
    no_pairs_at_all = open_object(monkeypatch, make_client(law.kind, with_pair=False), obj, today)
    assert with_pairs_loaded == no_pairs_at_all
    assert "النسخة" not in with_pairs_loaded.text


def test_a_no_pair_article_renders_the_literal_text(monkeypatch, law):
    obj = ResolvedObject(level="article", regulation_id=OTHER_ID, article_id=OTHER_7_ID)
    text = open_object(monkeypatch, law.client, obj, IN_PERIOD_1).text
    assert text == f"## نص المادة 7 من {OTHER_LAW}\n\n{OTHER_ARTICLE}"
