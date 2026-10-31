"""``regulation_versions`` and ``fetch_article`` for a law with a replacement.

The failure this guards is quiet: an old law's number returning an unrelated
article of the new law, which reads exactly like a right answer. So every cell of
the plan's ``fetch_article`` table is pinned across the phases, for a نظام and a
لائحة alike. The other half is that a law with no pair stays byte-identical, and
that half is checked against literals rather than against the code under test.

Every law here is invented. Sync only, no network: CI has no pytest-asyncio.
"""
from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.tool_repository import fetch_article as fetch_article_module
from agents.tool_repository.fetch_article import (
    _CAP_MARKER,
    _PLANNER_ARTICLE_CAP,
    FetchArticleResult,
    build_statute_package_md,
    fetch_article_result,
    flush_statute_package,
    register_fetch_article,
    resolve_regulation_id,
)
from agents.tool_repository.regulation_versions import (
    VersionedTitle,
    in_surviving_part,
    opened_section,
    resolve,
    version_question,
    version_title,
)
from shared.library.supersession import Pair, Successor, reset_cache

SUPERSEDED_ON = date(2030, 3, 15)
PERIOD_1_END = date(2030, 6, 15)
PERIOD_2_END = date(2031, 3, 15)
DAY = timedelta(days=1)

IN_UPCOMING = SUPERSEDED_ON - DAY
IN_PERIOD_1 = SUPERSEDED_ON + DAY
IN_PERIOD_2 = PERIOD_1_END + DAY
IN_AFTER = PERIOD_2_END + DAY
IN_FORCE = "2030/03/15م"

OLD_ID = "a0000000-0000-4000-8000-000000000001"
NEW_ID = "a0000000-0000-4000-8000-000000000002"
OTHER_ID = "a0000000-0000-4000-8000-000000000003"
MARKER_LAW_ID = "a0000000-0000-4000-8000-000000000004"
SECOND_OLD_ID = "a0000000-0000-4000-8000-000000000005"

SURVIVING_CHUNK_ID = "c0000000-0000-4000-8000-000000000001"
ENDED_CHUNK_ID = "c0000000-0000-4000-8000-000000000002"
SURVIVING_REF = "900_reg_001_chunk_004"
ENDED_REF = "900_reg_001_chunk_009"

NOTE = "ثانياً: يستمر العمل بأحكام الرعي الموسمي حتى صدور نظام المراعي."

OLD_46 = "المادة السادسة والأربعون: يُقدَّم طلب سقي الواحة إلى اللجنة المحلية قبل الموسم."
NEW_46 = "المادة السادسة والأربعون: تُفرض رسوم الاستخدام السنوي على كل بئر مرخصة."
SURVIVING_BODY = "يُقدَّم طلب الرعي الموسمي قبل دخول القطيع إلى الحمى بثلاثين يومًا."
NEW_25 = "المادة الخامسة والعشرون: تُنشأ لجنة دائمة لفض منازعات الآبار."
OLD_70 = "المادة السبعون: يُعاقب من حفر بئرًا دون ترخيص بغرامة لا تتجاوز خمسة آلاف."
NEW_5 = "المادة الخامسة: تُحدَّد مواسم السقي بقرار من الوزير."
OLD_COMPOUND = "١/١١\n\nيجب أن يُرفق بطلب السقي صك ملكية الواحة أو ما يقوم مقامه."
OTHER_ARTICLE = "المادة السابعة: يُنشأ سجل عام للمراعي."

OTHER_LAW = "نظام المراعي"
MARKER_LAW = "نظام حماية الشتلات الجديدة"

KINDS = {
    "نظام": SimpleNamespace(
        new="نظام الواحات",
        old="نظام الواحات (القديم)",
        bucket="law_statute",
        # The old law carries chunk_parent_id, so this path is exercised.
        old_25=f"المادة الخامسة والعشرون: {SURVIVING_BODY}",
        parent_of_25=SURVIVING_CHUNK_ID,
        parent_of_rest=ENDED_CHUNK_ID,
    ),
    "لائحة": SimpleNamespace(
        new="اللائحة التنفيذية لنظام الواحات",
        old="اللائحة التنفيذية لنظام الواحات (القديم)",
        bucket="executive_regulation",
        # The old لائحة has no chunk_parent_id at all, so only the text can tell.
        old_25=f"٢/٢٥\n\n{SURVIVING_BODY}",
        parent_of_25=None,
        parent_of_rest=None,
    ),
}


# --------------------------------------------------------------------------- #
# Fake client: the chains the resolver, the loader and the fetch use.
# --------------------------------------------------------------------------- #


class FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self.rows = list(client.tables.get(table, []))
        self.negated = False
        self.max_rows = None

    def select(self, columns):
        self.columns = columns
        return self

    @property
    def not_(self):
        if self.client.load_fails:
            raise RuntimeError("no such column: superseded_by")
        self.negated = True
        return self

    def is_(self, column, value):
        assert value == "null"
        wanted = self.negated
        self.rows = [r for r in self.rows if (r.get(column) is not None) == wanted]
        return self

    def eq(self, column, value):
        self.rows = [r for r in self.rows if r.get(column) == value]
        return self

    def in_(self, column, values):
        self.rows = [r for r in self.rows if r.get(column) in set(values)]
        return self

    def ilike(self, column, pattern):
        needle = pattern.replace("%", "").casefold()
        self.rows = [r for r in self.rows if needle in str(r.get(column) or "").casefold()]
        return self

    def limit(self, count):
        self.max_rows = count
        return self

    def execute(self):
        self.client.calls.append(self.table_name)
        if self.table_name in self.client.broken:
            raise RuntimeError("boom")
        rows = self.rows if self.max_rows is None else self.rows[: self.max_rows]
        return SimpleNamespace(data=rows)


class FakeRpc:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return SimpleNamespace(data=self.data)


class FakeSupabase:
    """``stale_pins`` is ``search_index`` lagging the corpus: title → row id."""

    def __init__(self, tables, stale_pins=None):
        self.tables = tables
        self.stale_pins = stale_pins or {}
        self.calls = []
        self.broken = set()
        self.load_fails = False

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, name, params):
        self.calls.append(name)
        query = " ".join(str(params.get("p_query") or "").split())
        stale = self.stale_pins.get(query)
        if stale:
            return FakeRpc([{"content_id": stale, "title": query, "score": 1006.0}])
        rows = [
            {"content_id": r["id"], "title": r["title"], "score": 1003.0}
            for r in self.tables.get("regulations_v2", [])
            if r["title"] == query
        ]
        return FakeRpc(rows)


def regulation(reg_id, title, *, bucket="law_statute", ref=None, **extra):
    return {
        "id": reg_id,
        "reg_ref": ref or reg_id,
        "title": title,
        "clean_title": title,
        "doc_type_bucket": bucket,
        "status_class": "in_force",
        **extra,
    }


def article(reg_id, number, content, parent=None):
    return {
        "id": f"{reg_id}-{number}",
        "regulation_id": reg_id,
        "article_number": number,
        "content": content,
        "chunk_parent_id": parent,
    }


def make_client(name, *, pending=True, stale_pins=None):
    """One old row, its replacement, and two laws with no pair."""
    kind = KINDS[name]
    superseded_by = [{"reg_ref": "900_reg_002", "title": kind.new}]
    if pending:
        superseded_by.append(
            {
                "reg_ref": None,
                "title": OTHER_LAW,
                "part": "الرعي الموسمي",
                "pending": True,
                "chunk_refs": [SURVIVING_REF],
            }
        )
    regs = [
        regulation(
            OLD_ID, kind.old, bucket=kind.bucket, ref="900_reg_001",
            status_class="cancelled",
            superseded_by=superseded_by,
            superseded_dates={
                "superseded_on": SUPERSEDED_ON.isoformat(),
                "period_1_end": PERIOD_1_END.isoformat(),
                "period_2_end": PERIOD_2_END.isoformat(),
            },
        ),
        regulation(
            NEW_ID, kind.new, bucket=kind.bucket, ref="900_reg_002",
            transition_period_note=NOTE,
        ),
        regulation(OTHER_ID, OTHER_LAW),
        regulation(MARKER_LAW_ID, MARKER_LAW),
    ]
    articles = [
        article(OLD_ID, "46", OLD_46, kind.parent_of_rest),
        article(NEW_ID, "46", NEW_46),
        article(OLD_ID, "25", kind.old_25, kind.parent_of_25),
        article(NEW_ID, "25", NEW_25),
        article(OLD_ID, "70", OLD_70, kind.parent_of_rest),
        article(NEW_ID, "5", NEW_5),
        article(OLD_ID, "1-11", OLD_COMPOUND, kind.parent_of_rest),
        article(OTHER_ID, "7", OTHER_ARTICLE),
    ]
    chunks = [
        {
            "id": SURVIVING_CHUNK_ID,
            "regulation_id": OLD_ID,
            "chunk_ref": SURVIVING_REF,
            "content": f"الباب الثاني\n{SURVIVING_BODY}\nمادة أخرى.",
        },
        {
            "id": ENDED_CHUNK_ID,
            "regulation_id": OLD_ID,
            "chunk_ref": ENDED_REF,
            "content": OLD_46,
        },
    ]
    return FakeSupabase(
        {"regulations_v2": regs, "articles_v2": articles, "chunks_v2": chunks},
        stale_pins,
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_cache()
    yield
    reset_cache()


@pytest.fixture(params=sorted(KINDS))
def law(request):
    """The same rules, run for a نظام and for a لائحة."""
    kind = KINDS[request.param]
    return SimpleNamespace(
        name=request.param,
        new=kind.new,
        old=kind.old,
        new_label=f"{kind.new} (الجديد)",
        client=make_client(request.param),
    )


def fetch(law, title, number, today):
    return fetch_article_result(law.client, title, number, today=today)


def no_pending(law):
    reset_cache()
    return make_client(law.name, pending=False)


def plain_or_new(law):
    return [law.new, f"{law.new} (الجديد)", f"{law.new} الجديد"]


# --------------------------------------------------------------------------- #
# resolve
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("today", [IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
@pytest.mark.parametrize("spelling", ["{} القديم", "{} (القديم)", "{} - القديم", "{} القديمة"])
def test_resolve_a_title_naming_the_old_version(law, today, spelling):
    versioned = resolve(law.client, spelling.format(law.new), today)
    assert versioned.resolved.reg_id == OLD_ID
    assert versioned.resolved.exact is True
    assert versioned.choice == "old"


def test_resolve_a_stored_old_title_verbatim(law):
    versioned = resolve(law.client, law.old, IN_PERIOD_2)
    assert versioned.resolved.reg_id == OLD_ID
    assert versioned.choice == "old"


@pytest.mark.parametrize("spelling", ["{} (الجديد)", "{} الجديد", "{} - الجديدة"])
def test_resolve_a_title_naming_the_new_version(law, spelling):
    versioned = resolve(law.client, spelling.format(law.new), IN_PERIOD_1)
    assert versioned.resolved.reg_id == NEW_ID
    assert versioned.choice == "new"


def test_resolve_a_marker_inside_a_laiha_title():
    client = make_client("لائحة")
    versioned = resolve(client, "اللائحة التنفيذية القديمة لنظام الواحات", IN_PERIOD_1)
    assert versioned.resolved.reg_id == OLD_ID


@pytest.mark.parametrize("today", [IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_resolve_a_plain_title_means_the_new_law(law, today):
    versioned = resolve(law.client, law.new, today)
    assert versioned.resolved.reg_id == NEW_ID
    assert versioned.resolved.display == law.new
    assert versioned.choice is None
    assert [v.side for v in versioned.versions] == ["new", "old"]


def test_resolve_a_plain_title_before_the_new_law_is_in_force(law):
    assert resolve(law.client, law.new, IN_UPCOMING).resolved.reg_id == OLD_ID


def test_resolve_survives_a_stale_exact_pin_on_the_old_row(law):
    # search_index still holds the old row under the plain title until the
    # refresh, so the resolver pins the WRONG side. The pair corrects it.
    reset_cache()
    client = make_client(law.name, stale_pins={law.new: OLD_ID})
    assert resolve_regulation_id(client, law.new).reg_id == OLD_ID
    assert resolve(client, law.new, IN_PERIOD_1).resolved.reg_id == NEW_ID
    assert resolve(client, f"{law.new} القديم", IN_PERIOD_1).resolved.reg_id == OLD_ID


def test_resolve_after_the_index_refresh_with_a_longer_title_competing(law):
    # The refresh removes the stale pin, leaving a title that merely contains the
    # phrase. It may rank, but only the exact title pins.
    law.client.tables["regulations_v2"].append(
        regulation("competitor", f"{law.new} أمام ديوان الواحات")
    )
    assert resolve(law.client, law.new, IN_PERIOD_1).resolved.reg_id == NEW_ID
    assert resolve(law.client, f"{law.new} القديم", IN_PERIOD_1).resolved.reg_id == OLD_ID


def test_a_real_title_containing_a_marker_word_is_not_a_version(law):
    versioned = resolve(law.client, MARKER_LAW, IN_PERIOD_1)
    assert versioned.resolved.reg_id == MARKER_LAW_ID
    assert versioned.versions == ()


def test_a_marker_word_never_moves_a_title_onto_an_unrelated_law(law):
    # «نظام المراعي» is real and has no pair. The stripped title pins it, and
    # that is not evidence the user meant it: the typed result stands.
    title = f"{OTHER_LAW} الجديد"
    expected = resolve_regulation_id(law.client, title)
    versioned = resolve(law.client, title, IN_PERIOD_1)
    assert versioned.resolved == expected
    assert versioned.resolved.reg_id == ""
    assert versioned.versions == ()


def test_a_comparison_title_with_both_markers_chooses_neither(law):
    versioned = resolve(law.client, f"{law.new} القديم والجديد", IN_PERIOD_1)
    assert versioned.resolved.reg_id == NEW_ID
    assert versioned.choice is None


def test_an_ordinary_law_costs_no_extra_round_trip(law):
    resolve(law.client, OTHER_LAW, IN_PERIOD_1)  # loads the map
    law.client.calls.clear()
    resolve_regulation_id(law.client, OTHER_LAW)
    plain_cost = list(law.client.calls)

    law.client.calls.clear()
    resolve(law.client, OTHER_LAW, IN_PERIOD_1)
    assert law.client.calls == plain_cost

    # Nor does a real title that happens to end in a marker word.
    law.client.calls.clear()
    resolve_regulation_id(law.client, MARKER_LAW)
    marker_cost = list(law.client.calls)
    law.client.calls.clear()
    resolve(law.client, MARKER_LAW, IN_PERIOD_1)
    assert law.client.calls == marker_cost


def test_a_failed_load_behaves_as_today(law):
    # Also the state between deploy and migration: the column doesn't exist yet.
    reset_cache()
    client = make_client(law.name)
    client.load_fails = True
    versioned = resolve(client, law.new, IN_PERIOD_1)
    assert versioned.resolved == resolve_regulation_id(client, law.new)
    assert versioned.versions == ()
    result = fetch_article_result(client, law.new, "46", today=IN_PERIOD_1)
    assert (result.reg_id, result.companions) == (NEW_ID, ())
    assert result.text == f"## نص المادة 46 من {law.new}\n\n{NEW_46}"


@pytest.mark.parametrize(
    "title, version, expected",
    [
        ("نظام الواحات", "old", "نظام الواحات (القديم)"),
        ("نظام الواحات", "new", "نظام الواحات (الجديد)"),
        ("نظام الواحات (القديم)", "old", "نظام الواحات (القديم)"),
    ],
)
def test_version_title(title, version, expected):
    assert version_title(title, version) == expected


def test_a_labelled_title_resolves_back_to_its_own_side(law):
    # An old row whose stored title has no marker still gets one, and the label
    # is what a re-call passes back, so it must land on that side.
    for version, reg_id in (("old", OLD_ID), ("new", NEW_ID)):
        label = version_title(law.new, version)
        assert resolve(law.client, label, IN_PERIOD_1).resolved.reg_id == reg_id


# --------------------------------------------------------------------------- #
# The fetch_article table
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("today", [IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_row_1_title_names_the_old_version(law, today):
    result = fetch(law, law.old, "46", today)
    assert result.status == "ok"
    assert result.reg_id == OLD_ID
    assert result.reg_name == law.old
    assert result.content == OLD_46
    assert result.companions == ()
    assert result.text.startswith(f"## نص المادة 46 من {law.old}\n\n{OLD_46}")
    assert NEW_46 not in result.text


def test_row_1_keeps_the_old_law_even_where_a_part_survives(law):
    result = fetch(law, law.old, "25", IN_PERIOD_2)
    assert result.reg_id == OLD_ID
    assert result.companions == ()


def test_row_2_period_1_returns_both_labelled(law):
    for title in plain_or_new(law):
        result = fetch(law, title, "46", IN_PERIOD_1)
        assert (result.reg_id, result.reg_name, result.content) == (NEW_ID, law.new_label, NEW_46)
        [other] = result.companions
        assert (other.reg_id, other.reg_name, other.content) == (OLD_ID, law.old, OLD_46)
        assert other.status == "ok"
        assert other.confidence == "high"
        assert other.article_number == "46"
        assert f"## نص المادة 46 من {law.new_label}\n\n{NEW_46}" in result.text
        assert f"## نص المادة 46 من {law.old}\n\n{OLD_46}" in result.text


def test_row_2_period_2_returns_the_new_article_with_a_numbering_warning(law):
    for title in plain_or_new(law):
        result = fetch(law, title, "46", IN_PERIOD_2)
        assert (result.reg_id, result.content) == (NEW_ID, NEW_46)
        assert result.companions == ()
        assert result.reg_name == law.new_label
        assert "الترقيم تغيّر" in result.text
        assert law.old in result.text  # the re-call the planner would make
        assert OLD_46 not in result.text


def test_row_2_after_is_the_new_article_reading_like_any_other_law(law):
    for title in (law.new, f"{law.new} (الجديد)"):
        result = fetch(law, title, "46", IN_AFTER)
        assert result == FetchArticleResult(
            text=f"## نص المادة 46 من {law.new}\n\n{NEW_46}",
            status="ok", confidence="high", reg_id=NEW_ID, reg_name=law.new,
            article_number="46", content=NEW_46,
        )


@pytest.mark.parametrize("today", [IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_row_3_a_surviving_article_returns_both(law, today):
    result = fetch(law, law.new, "25", today)
    assert (result.reg_id, result.content) == (NEW_ID, NEW_25)
    [other] = result.companions
    assert other.reg_id == OLD_ID
    assert SURVIVING_BODY in other.content


def test_row_3_ends_when_the_pending_entry_is_removed(law):
    client = no_pending(law)
    result = fetch_article_result(client, law.new, "25", today=IN_AFTER)
    assert result.reg_id == NEW_ID
    assert result.companions == ()


@pytest.mark.parametrize("today", [IN_PERIOD_2, IN_AFTER])
def test_row_3_says_why_the_old_text_is_shown(law, today):
    assert "ما زال ساريًا" in fetch(law, law.new, "25", today).text


@pytest.mark.parametrize("today", [IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_row_4_a_number_only_the_old_law_has(law, today):
    for title in (law.new, law.old, f"{law.new} (الجديد)"):
        result = fetch(law, title, "70", today)
        assert (result.reg_id, result.content) == (OLD_ID, OLD_70)
        assert result.companions == ()
        assert result.reg_name == law.old
        assert "الترقيم يختلف" in result.text
        assert law.new_label in result.text


@pytest.mark.parametrize("today", [IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_row_4_a_number_only_the_new_law_has(law, today):
    for title in (law.new, law.old, f"{law.new} (الجديد)"):
        result = fetch(law, title, "5", today)
        assert (result.reg_id, result.content) == (NEW_ID, NEW_5)
        assert result.companions == ()
        assert result.reg_name == law.new_label
        assert "الترقيم يختلف" in result.text
        assert law.old in result.text


@pytest.mark.parametrize("today", [IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_row_4_a_compound_old_number_with_a_plain_title(law, today):
    result = fetch(law, law.new, "1-11", today)
    assert (result.reg_id, result.content) == (OLD_ID, OLD_COMPOUND)
    assert result.article_number == "1-11"
    assert "الترقيم يختلف" in result.text


@pytest.mark.parametrize("today", [IN_UPCOMING, IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_row_5_a_number_in_neither_is_not_found(law, today):
    for title in (law.new, law.old):
        result = fetch(law, title, "999", today)
        assert result.status == "not_found"
        assert result.content == ""
        assert result.companions == ()
        assert result.text.startswith("المادة 999 غير موجودة في ")
        assert law.old in result.text
        assert law.new_label in result.text


def test_arabic_indic_digits_still_find_both_versions(law):
    result = fetch(law, law.new, "٤٦", IN_PERIOD_1)
    assert (result.content, result.companions[0].content) == (NEW_46, OLD_46)


# -- upcoming -----------------------------------------------------------------


def test_upcoming_returns_the_old_law_as_current_and_gives_the_date(law):
    result = fetch(law, law.new, "46", IN_UPCOMING)
    assert (result.reg_id, result.content) == (OLD_ID, OLD_46)
    assert result.companions == ()
    assert result.text.startswith(f"## نص المادة 46 من {law.old}\n\n{OLD_46}")
    assert IN_FORCE in result.text
    assert "الأحكام الانتقالية\n\n" not in result.text


def test_upcoming_an_explicit_new_title_gets_the_new_text(law):
    result = fetch(law, f"{law.new} (الجديد)", "46", IN_UPCOMING)
    assert (result.reg_id, result.content) == (NEW_ID, NEW_46)
    assert IN_FORCE in result.text


def test_upcoming_a_number_only_the_new_law_has(law):
    result = fetch(law, law.new, "5", IN_UPCOMING)
    assert (result.reg_id, result.content) == (NEW_ID, NEW_5)
    assert "الترقيم يختلف" in result.text
    assert IN_FORCE in result.text


# -- The note: text only, and telling the planner what to do -------------------


@pytest.mark.parametrize(
    "number, today",
    [("46", IN_PERIOD_1), ("46", IN_PERIOD_2), ("25", IN_PERIOD_2), ("25", IN_AFTER), ("70", IN_PERIOD_2)],
)
def test_the_transition_note_is_in_text_and_never_in_content(law, number, today):
    result = fetch(law, law.new, number, today)
    assert NOTE in result.text
    for item in (result, *result.companions):
        assert NOTE not in item.content
        assert "planner_brief" not in item.content
        assert "⚠" not in item.content


def test_the_note_stays_out_of_a_law_past_its_transition(law):
    client = no_pending(law)
    result = fetch_article_result(client, law.old, "46", today=IN_AFTER)
    assert result.reg_id == OLD_ID
    assert NOTE not in result.text
    assert "ملغي" in result.text


def test_the_planner_is_told_to_carry_both_labels(law):
    text = fetch(law, law.new, "46", IN_PERIOD_1).text
    assert "planner_brief" in text
    assert "كليهما" in text


def test_a_single_version_is_carried_under_its_own_title(law):
    text = fetch(law, law.new, "46", IN_PERIOD_2).text
    assert "planner_brief" in text
    assert "كليهما" not in text


def test_the_status_sentence_names_the_phase(law):
    assert "ما زالت الفترة الانتقالية" in fetch(law, law.new, "46", IN_PERIOD_1).text
    assert "بدلًا من" in fetch(law, law.new, "46", IN_PERIOD_2).text


def test_each_version_is_capped_for_the_planner_and_whole_in_content(law):
    long_body = "سطر طويل من نص المادة.\n" * 400
    for row in law.client.tables["articles_v2"]:
        if row["article_number"] == "46":
            row["content"] = long_body
    result = fetch(law, law.new, "46", IN_PERIOD_1)
    assert len(long_body) > _PLANNER_ARTICLE_CAP
    assert result.content == long_body.strip()
    assert result.companions[0].content == long_body.strip()
    assert result.text.count(_CAP_MARKER) == 2


# --------------------------------------------------------------------------- #
# The surviving-article check
# --------------------------------------------------------------------------- #


def a_pair():
    return Pair(
        old_id=OLD_ID,
        old_ref="900_reg_001",
        old_title="نظام الواحات (القديم)",
        superseded_on=SUPERSEDED_ON,
        period_1_end=PERIOD_1_END,
        period_2_end=PERIOD_2_END,
        successors=(
            Successor(title="نظام الواحات", reg_ref="900_reg_002", reg_id=NEW_ID),
            Successor(title=OTHER_LAW, part="الرعي الموسمي", pending=True, chunk_refs=(SURVIVING_REF,)),
        ),
    )


@pytest.mark.parametrize(
    "parent, expected", [(SURVIVING_CHUNK_ID, True), (ENDED_CHUNK_ID, False)]
)
def test_surviving_by_chunk_parent_id(parent, expected):
    # The body is deliberately in the surviving chunk: the link outranks the text.
    row = {"chunk_parent_id": parent, "content": SURVIVING_BODY}
    assert in_surviving_part(make_client("نظام"), a_pair(), row) is expected


@pytest.mark.parametrize(
    "content, expected",
    [
        (SURVIVING_BODY, True),
        (OLD_46, False),
        ("", False),
    ],
)
def test_surviving_by_text_when_there_is_no_link(content, expected):
    row = {"chunk_parent_id": None, "content": content}
    assert in_surviving_part(make_client("لائحة"), a_pair(), row) is expected


@pytest.mark.parametrize("opening", ["٤/٣٣", "4/33", "1-11", "١/٧٧ "])
def test_the_probe_skips_the_articles_own_number(opening):
    # The live straddle: the number ends one chunk and the body opens the next,
    # so a probe that kept the number would miss an article still in force.
    row = {"chunk_parent_id": None, "content": f"{opening}\n\n{SURVIVING_BODY}"}
    assert in_surviving_part(make_client("لائحة"), a_pair(), row) is True


def test_only_the_probe_has_to_match():
    row = {"chunk_parent_id": None, "content": SURVIVING_BODY[:45] + " تتمة لا ترد في المقطع"}
    assert in_surviving_part(make_client("لائحة"), a_pair(), row) is True


def test_no_pending_part_means_no_query():
    client = make_client("نظام")
    pair = Pair(
        old_id=OLD_ID, old_ref="900_reg_001", old_title="x",
        superseded_on=SUPERSEDED_ON, period_1_end=PERIOD_1_END, period_2_end=PERIOD_2_END,
        successors=(Successor(title="نظام الواحات"),),
    )
    assert in_surviving_part(client, pair, {"chunk_parent_id": SURVIVING_CHUNK_ID}) is False
    assert client.calls == []


def test_a_failed_chunk_read_shows_both_versions(law):
    law.client.broken.add("chunks_v2")
    result = fetch(law, law.new, "46", IN_PERIOD_2)
    assert [law.old] == [c.reg_name for c in result.companions]


# --------------------------------------------------------------------------- #
# The statute package
# --------------------------------------------------------------------------- #


class StubAgent:
    def __init__(self):
        self.tools = {}

    def tool(self, fn):
        self.tools[fn.__name__] = fn
        return fn


def run_tool(client, title, number, deps=None):
    agent = StubAgent()
    register_fetch_article(agent)
    deps = deps or SimpleNamespace(supabase=client, _fetched_articles=[], _flushed_articles=[])
    deps.supabase = client
    text = asyncio.run(agent.tools["fetch_article"](SimpleNamespace(deps=deps), title, number))
    return text, deps


def test_the_tool_accumulates_each_version_separately(law, monkeypatch):
    monkeypatch.setattr(fetch_article_module, "riyadh_today", lambda: IN_PERIOD_1)
    text, deps = run_tool(law.client, law.new, "46")
    assert [
        (a["regulation"], a["article_number"], a["content"], a["confidence"])
        for a in deps._fetched_articles
    ] == [(law.new_label, "46", NEW_46, "high"), (law.old, "46", OLD_46, "high")]
    assert text.count("## نص المادة 46 من") == 2


def test_the_statute_package_shows_both_under_their_own_titles(law, monkeypatch):
    monkeypatch.setattr(fetch_article_module, "riyadh_today", lambda: IN_PERIOD_1)
    written = {}

    def capture(_supabase, **kwargs):
        written.update(kwargs)
        return {"item_id": "item-1"}

    monkeypatch.setattr(fetch_article_module, "_insert_statute_item", capture)
    deps = SimpleNamespace(
        _fetched_articles=[], _flushed_articles=[], user_id="u1", conversation_id="c1"
    )
    _, deps = run_tool(law.client, law.new, "46", deps)
    assert flush_statute_package(deps) == "item-1"

    # Same number, two entries: the dedup key must keep them apart.
    assert [a["regulation"] for a in written["metadata"]["articles"]] == [law.new_label, law.old]
    assert f"## نص المادة 46 من {law.new_label}\n\n{NEW_46}" in written["content_md"]
    assert f"## نص المادة 46 من {law.old}\n\n{OLD_46}" in written["content_md"]

    block = build_statute_package_md(deps._flushed_articles)
    assert NEW_46 in block and OLD_46 in block
    assert NOTE not in block


def test_a_single_version_accumulates_once(law, monkeypatch):
    monkeypatch.setattr(fetch_article_module, "riyadh_today", lambda: IN_PERIOD_2)
    _, deps = run_tool(law.client, law.new, "46")
    assert [a["content"] for a in deps._fetched_articles] == [NEW_46]


# --------------------------------------------------------------------------- #
# Merge and split
# --------------------------------------------------------------------------- #


def merged_client():
    """Two old laws replaced by one new law."""
    client = make_client("نظام")
    regs = client.tables["regulations_v2"]
    regs.append(
        regulation(
            SECOND_OLD_ID, "نظام البساتين", ref="900_reg_003",
            superseded_by=[{"reg_ref": "900_reg_002", "title": "نظام الواحات"}],
            superseded_dates={
                "superseded_on": SUPERSEDED_ON.isoformat(),
                "period_1_end": PERIOD_1_END.isoformat(),
                "period_2_end": PERIOD_2_END.isoformat(),
            },
        )
    )
    client.tables["articles_v2"].append(article(SECOND_OLD_ID, "46", "نص من نظام البساتين."))
    return client


def test_a_merge_shows_every_old_law_in_period_1():
    result = fetch_article_result(merged_client(), "نظام الواحات", "46", today=IN_PERIOD_1)
    assert [result.reg_id, *(c.reg_id for c in result.companions)] == [
        NEW_ID, OLD_ID, SECOND_OLD_ID,
    ]
    assert len({*(c.reg_name for c in result.companions), result.reg_name}) == 3


def test_a_split_from_the_new_side_never_pulls_in_the_other_law():
    client = make_client("نظام")
    third = "a0000000-0000-4000-8000-000000000006"
    client.tables["regulations_v2"].append(regulation(third, "نظام النخيل", ref="900_reg_004"))
    old = client.tables["regulations_v2"][0]
    old["superseded_by"] = [
        {"reg_ref": "900_reg_002", "title": "نظام الواحات"},
        {"reg_ref": "900_reg_004", "title": "نظام النخيل"},
    ]
    new_side = resolve(client, "نظام الواحات", IN_PERIOD_1)
    assert [v.reg_id for v in new_side.versions] == [NEW_ID, OLD_ID]
    old_side = resolve(client, "نظام الواحات (القديم)", IN_PERIOD_1)
    assert [v.reg_id for v in old_side.versions] == [NEW_ID, third, OLD_ID]


# --------------------------------------------------------------------------- #
# A pair sharing one title: the usual shape of a replaced law
# --------------------------------------------------------------------------- #


def same_title_client(law, old_clean, new_clean):
    """The pair stored under one ``title``, with these ``clean_title``s."""
    reset_cache()
    client = make_client(law.name)
    old_row, new_row = client.tables["regulations_v2"][:2]
    old_row.update(title=law.new, clean_title=old_clean)
    new_row.update(title=law.new, clean_title=new_clean)
    return client


def identical_titles(law):
    # The resolver's title de-dupe folds these into one pin on an arbitrary side.
    client = same_title_client(law, None, law.new)
    assert resolve_regulation_id(client, law.new).reg_id
    return client


def tied_titles(law):
    # Different clean titles survive that de-dupe, so both rows pin and tie.
    client = same_title_client(law, f"{law.new} 1425", f"{law.new} 1440")
    typed = resolve_regulation_id(client, law.new)
    assert (typed.reg_id, len(typed.shortlist)) == ("", 2)
    return client


@pytest.fixture(params=[identical_titles, tied_titles], ids=["identical", "tied"])
def shared(request, law):
    return request.param(law)


@pytest.mark.parametrize("today", [IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_a_shared_title_means_the_new_law(law, shared, today):
    versioned = resolve(shared, law.new, today)
    assert versioned.resolved.reg_id == NEW_ID
    assert versioned.resolved.ambiguous == ""
    assert [v.side for v in versioned.versions] == ["new", "old"]


def test_a_shared_title_before_the_new_law_is_in_force(law, shared):
    assert resolve(shared, law.new, IN_UPCOMING).resolved.reg_id == OLD_ID


def test_a_shared_title_with_a_marker_picks_its_side(law, shared):
    assert resolve(shared, f"{law.new} القديم", IN_PERIOD_1).resolved.reg_id == OLD_ID
    assert resolve(shared, f"{law.new} الجديد", IN_PERIOD_1).resolved.reg_id == NEW_ID


def test_fetch_article_returns_both_versions_of_a_shared_title(law, shared):
    result = fetch_article_result(shared, law.new, "46", today=IN_PERIOD_1)
    assert result.status == "ok"
    assert [result.reg_id, *(c.reg_id for c in result.companions)] == [NEW_ID, OLD_ID]


def test_the_question_for_a_shared_title_tells_the_versions_apart(law, shared):
    question = version_question(resolve(shared, law.new, IN_PERIOD_1), IN_PERIOD_1)
    assert "(القديم)" in question
    assert "(الجديد)" in question


def test_the_labels_of_a_shared_title_resolve_back_to_their_side(law, shared):
    for version in resolve(shared, law.new, IN_PERIOD_1).versions:
        assert resolve(shared, version.label, IN_PERIOD_1).resolved.reg_id == version.reg_id


def test_a_tie_across_a_merge_finds_every_version():
    client = merged_client()
    regs = {row["id"]: row for row in client.tables["regulations_v2"]}
    for year, reg_id in enumerate((OLD_ID, NEW_ID, SECOND_OLD_ID), start=1420):
        regs[reg_id].update(title="نظام الواحات", clean_title=f"نظام الواحات {year}")
    versions = resolve(client, "نظام الواحات", IN_PERIOD_1).versions
    assert [v.reg_id for v in versions] == [NEW_ID, OLD_ID, SECOND_OLD_ID]


def test_a_tie_outside_any_pair_is_byte_identical(law):
    title = "نظام السدود الوطنية"
    law.client.tables["regulations_v2"] += [
        regulation("r-a", title, clean_title=f"{title} 1425"),
        regulation("r-b", title, clean_title=f"{title} 1440"),
    ]
    typed = resolve_regulation_id(law.client, title)
    assert len(typed.shortlist) == 2
    assert resolve(law.client, title, IN_PERIOD_1) == VersionedTitle(typed)


def test_a_third_row_on_the_title_leaves_the_tie_to_the_planner(law):
    client = tied_titles(law)
    client.tables["regulations_v2"].append(
        regulation("r-third", law.new, clean_title=f"{law.new} 1390")
    )
    typed = resolve_regulation_id(client, law.new)
    assert len(typed.shortlist) == 3
    assert resolve(client, law.new, IN_PERIOD_1) == VersionedTitle(typed)


def test_a_shortlist_with_no_pin_stays_the_planners(law):
    # A phrase in both titles is retrieval. Only an exact title is identity.
    typed = resolve_regulation_id(law.client, "الواحات")
    assert typed.shortlist and not any(c.exact for c in typed.shortlist)
    assert resolve(law.client, "الواحات", IN_PERIOD_1) == VersionedTitle(typed)


# --------------------------------------------------------------------------- #
# simple_search's question and the opened section (their wiring is tested there)
# --------------------------------------------------------------------------- #


def test_the_question_is_period_1_only_and_lists_both(law):
    versioned = resolve(law.client, law.new, IN_PERIOD_1)
    question = version_question(versioned, IN_PERIOD_1)
    assert question.startswith("AMBIGUOUS:")
    assert law.old in question
    assert law.new_label in question
    assert "ولا تسأله مرة أخرى" in question
    for today in (IN_UPCOMING, IN_PERIOD_2, IN_AFTER):
        assert version_question(resolve(law.client, law.new, today), today) == ""


def test_a_marker_means_no_question(law):
    for title in (law.old, f"{law.new} (الجديد)"):
        assert version_question(resolve(law.client, title, IN_PERIOD_1), IN_PERIOD_1) == ""


def test_opened_section_is_empty_outside_a_pair(law):
    assert opened_section(law.client, OTHER_ID, IN_PERIOD_1) == []
    assert opened_section(law.client, None, IN_PERIOD_1) == []


def test_opened_section_leaves_a_new_law_alone_once_nothing_is_pending(law):
    client = no_pending(law)
    assert opened_section(client, NEW_ID, IN_AFTER) == []
    assert "ملغي" in "\n".join(opened_section(client, OLD_ID, IN_AFTER))


# --------------------------------------------------------------------------- #
# Laws with no pair: byte-identical
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("today", [IN_UPCOMING, IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_a_no_pair_law_is_byte_identical(law, today):
    assert fetch(law, OTHER_LAW, "7", today) == FetchArticleResult(
        text=f"## نص المادة 7 من {OTHER_LAW}\n\n{OTHER_ARTICLE}",
        status="ok", confidence="high", reg_id=OTHER_ID, reg_name=OTHER_LAW,
        article_number="7", content=OTHER_ARTICLE,
    )


def test_a_no_pair_law_missing_the_article_is_byte_identical(law):
    assert fetch(law, OTHER_LAW, "999", IN_PERIOD_1) == FetchArticleResult(
        text=f"المادة 999 غير موجودة في {OTHER_LAW}",
        status="not_found", reg_id=OTHER_ID, reg_name=OTHER_LAW, article_number="999",
    )


def test_an_unknown_law_is_byte_identical(law):
    assert fetch(law, "نظام لا وجود له", "7", IN_PERIOD_1) == FetchArticleResult(
        text="المادة 7 غير موجودة في نظام لا وجود له", status="not_found",
    )


def test_an_ambiguous_title_is_byte_identical(law):
    # Both titles contain the phrase and neither pins, so it stays a shortlist.
    first, second = "نظام السدود الوطنية", "لائحة السدود الوطنية"
    law.client.tables["regulations_v2"] += [regulation("r-a", first), regulation("r-b", second)]
    assert fetch(law, "السدود الوطنية", "7", IN_PERIOD_1) == FetchArticleResult(
        text=(
            "AMBIGUOUS: لم يتطابق الاسم المذكور مع نظام واحد بعينه. أقرب المرشحين:\n"
            f"1. «{first}» — نظام\n"
            f"2. «{second}» — نظام\n\n"
            "إن كان أحدها هو المقصود فأعد النداء باسمه كما هو مكتوب أعلاه حرفيًّا.\n"
            "وإن لم يكن أيٌّ منها المقصود فلا تختر — اترك الأمر للبحث العادي."
        ),
        status="ambiguous",
    )


def test_a_default_field_keeps_the_existing_result_shape():
    assert FetchArticleResult(text="x", status="ok").companions == ()


# --------------------------------------------------------------------------- #
# Principle 1
# --------------------------------------------------------------------------- #


def test_no_law_ref_or_date_in_the_module():
    path = Path(fetch_article_module.__file__).with_name("regulation_versions.py")
    source = path.read_text(encoding="utf-8")
    assert not re.search(r"\d+_reg_\d+", source)
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b", source)
    assert "م/" not in source
