"""``shared.library.supersession`` — a replaced law during its transition.

Two failures matter most. Hiding an old law while it can still apply gives a
lawyer the wrong rule; and any change at all for a regulation with no pair would
void every prompt cache and A/B already measured. So the phase table is pinned
row by row, for a نظام and a لائحة alike, and the no-pair path is compared with
today's ``status_line`` exactly.

Every law here is invented. Sync only, no network: CI has no pytest-asyncio.
"""
from __future__ import annotations

import importlib.util
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared.library import supersession as sup
from shared.library.reg_status import REPEALED_LABEL_AR, status_line
from shared.library.supersession import (
    AFTER,
    PERIOD_1,
    PERIOD_2,
    UPCOMING,
    AggregatorStatus,
    Pair,
    Successor,
    SupersessionMap,
    aggregator_status,
    both_versions_instruction,
    chunk_provisions,
    group_by_note,
    load_supersessions,
    names_old_version,
    old_chunk_eligible,
    phase,
    phase_instruction,
    reranker_name,
    reranker_status,
    reset_cache,
    responder_line,
    riyadh_today,
    strip_version_marker,
    takes_extra_slot,
    version_choice,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

SUPERSEDED_ON = date(2030, 3, 15)
PERIOD_1_END = date(2030, 6, 15)
PERIOD_2_END = date(2031, 3, 15)
DAY = timedelta(days=1)

IN_UPCOMING = SUPERSEDED_ON - DAY
IN_PERIOD_1 = SUPERSEDED_ON + DAY
IN_PERIOD_2 = PERIOD_1_END + DAY
IN_AFTER = PERIOD_2_END + DAY

NOTE = "ثانياً: يستمر العمل بأحكام اختراعية."

KINDS = {
    "نظام": ("نظام الواحات", "نظام الواحات (القديم)"),
    "لائحة": ("اللائحة التنفيذية لنظام الواحات", "اللائحة التنفيذية لنظام الواحات (القديم)"),
}
SURVIVING_CHUNK = "900_reg_001_chunk_004"
OTHER_CHUNK = "900_reg_001_chunk_009"


def make_pair(
    new_title="نظام الواحات",
    old_title="نظام الواحات (القديم)",
    pending=True,
    old_id="old-uuid",
    old_ref="900_reg_001",
):
    successors = [Successor(title=new_title, reg_ref="900_reg_002", reg_id="new-uuid", note=NOTE)]
    if pending:
        successors.append(
            Successor(
                title="نظام المراعي",
                part="الرعي الموسمي",
                pending=True,
                chunk_refs=(SURVIVING_CHUNK,),
            )
        )
    return Pair(
        old_id=old_id,
        old_ref=old_ref,
        old_title=old_title,
        superseded_on=SUPERSEDED_ON,
        period_1_end=PERIOD_1_END,
        period_2_end=PERIOD_2_END,
        successors=tuple(successors),
    )


def old_row(title="نظام الواحات (القديم)", status_class="cancelled", status_raw="لاغي"):
    return {"id": "old-uuid", "clean_title": title, "status_class": status_class, "status_raw": status_raw}


def new_row():
    return {"id": "new-uuid", "clean_title": "نظام الواحات", "status_class": "in_force", "status_raw": "ساري"}


@pytest.fixture(params=sorted(KINDS))
def kind(request):
    """The same rules, run for a نظام and for a لائحة."""
    new_title, old_title = KINDS[request.param]
    return SimpleNamespace(
        new_title=new_title,
        old_title=old_title,
        supersessions=SupersessionMap([make_pair(new_title, old_title)]),
        no_pending=SupersessionMap([make_pair(new_title, old_title, pending=False)]),
        row=old_row(old_title),
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_cache()
    yield
    reset_cache()


# -- phase --------------------------------------------------------------------


# End dates are inclusive, so each boundary is pinned from both sides.
@pytest.mark.parametrize(
    "today, expected",
    [
        (SUPERSEDED_ON - DAY, UPCOMING),
        (SUPERSEDED_ON, PERIOD_1),
        (PERIOD_1_END, PERIOD_1),
        (PERIOD_1_END + DAY, PERIOD_2),
        (PERIOD_2_END, PERIOD_2),
        (PERIOD_2_END + DAY, AFTER),
    ],
)
def test_phase_boundaries(today, expected):
    assert phase(make_pair(), today) == expected


def test_riyadh_today_rolls_over_three_hours_before_utc(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2030, 3, 14, 21, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(sup, "datetime", FrozenDatetime)
    assert riyadh_today() == date(2030, 3, 15)


# -- reranker: the phase table ------------------------------------------------


@pytest.mark.parametrize("today", [IN_UPCOMING, IN_PERIOD_1])
def test_upcoming_and_period_1_show_no_line_on_any_old_chunk(kind, today):
    for chunk in (SURVIVING_CHUNK, OTHER_CHUNK):
        assert reranker_status(kind.supersessions, kind.row, chunk, "سؤال", today) == ""


@pytest.mark.parametrize("today", [IN_PERIOD_2, PERIOD_2_END, IN_AFTER])
def test_a_surviving_chunk_shows_no_line(kind, today):
    assert reranker_status(kind.supersessions, kind.row, SURVIVING_CHUNK, "سؤال", today) == ""


@pytest.mark.parametrize("today", [IN_PERIOD_2, IN_AFTER])
def test_any_other_old_chunk_gets_the_standard_repeal_line(kind, today):
    line = reranker_status(kind.supersessions, kind.row, OTHER_CHUNK, "سؤال", today)
    assert line == status_line("cancelled", "لاغي")


@pytest.mark.parametrize("today", [IN_PERIOD_2, IN_AFTER])
def test_a_sub_query_naming_the_old_version_clears_the_line(kind, today):
    query = "إجراءات الرعي في النظام القديم"
    assert reranker_status(kind.supersessions, kind.row, OTHER_CHUNK, query, today) == ""


def test_period_2_with_nothing_pending_still_honours_an_old_sub_query(kind):
    query = "النظام القديم"
    assert reranker_status(kind.no_pending, kind.row, OTHER_CHUNK, query, IN_PERIOD_2) == ""
    line = reranker_status(kind.no_pending, kind.row, OTHER_CHUNK, "سؤال", IN_PERIOD_2)
    assert REPEALED_LABEL_AR in line


def test_after_with_nothing_pending_is_plainly_repealed(kind):
    for query in ("سؤال", "النظام القديم"):
        line = reranker_status(kind.no_pending, kind.row, OTHER_CHUNK, query, IN_AFTER)
        assert line == status_line("cancelled", "لاغي")


def test_the_dates_decide_even_if_status_class_was_never_flipped(kind):
    row = old_row(kind.old_title, status_class="in_force", status_raw="ساري")
    line = reranker_status(kind.supersessions, row, OTHER_CHUNK, "سؤال", IN_AFTER)
    assert line == REPEALED_LABEL_AR


@pytest.mark.parametrize("today", [IN_UPCOMING, IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_a_new_row_is_never_flagged(kind, today):
    assert reranker_status(kind.supersessions, new_row(), OTHER_CHUNK, "سؤال", today) == ""


# -- reranker: the name shown -------------------------------------------------


def test_an_eligible_old_chunk_shows_the_successor_title(kind):
    shown = reranker_name(kind.supersessions, kind.row, OTHER_CHUNK, "سؤال", IN_PERIOD_1)
    assert shown == kind.new_title
    assert "القديم" not in shown


def test_a_flagged_old_chunk_keeps_its_real_name(kind):
    shown = reranker_name(kind.supersessions, kind.row, OTHER_CHUNK, "سؤال", IN_PERIOD_2)
    assert shown == kind.old_title


def test_the_shown_name_skips_pending_successors():
    pair = Pair(
        old_id="old-uuid",
        old_ref="900_reg_001",
        old_title="نظام الواحات (القديم)",
        superseded_on=SUPERSEDED_ON,
        period_1_end=PERIOD_1_END,
        period_2_end=PERIOD_2_END,
        successors=(
            Successor(title="نظام المراعي", part="الرعي الموسمي", pending=True),
            Successor(title="نظام الواحات", reg_ref="900_reg_002"),
        ),
    )
    shown = reranker_name(SupersessionMap([pair]), old_row(), OTHER_CHUNK, "سؤال", IN_PERIOD_1)
    assert shown == "نظام الواحات"


def test_old_chunk_eligible_is_false_off_the_old_side(kind):
    assert old_chunk_eligible(kind.supersessions, new_row(), OTHER_CHUNK, "سؤال", IN_PERIOD_1) is False
    assert old_chunk_eligible(SupersessionMap(), kind.row, OTHER_CHUNK, "سؤال", IN_PERIOD_1) is False


@pytest.mark.parametrize(
    "today, sub_query, chunk_ref, expected",
    [
        # Before the new law is in force the old law IS the law: it competes equally.
        (IN_UPCOMING, "شروط الاختراع", "any_chunk", False),
        (IN_PERIOD_1, "شروط الاختراع", "any_chunk", True),
        # A sub-query naming the old version wants it, so it ranks normally.
        (IN_PERIOD_1, "شروط الاختراع في النظام القديم", "any_chunk", False),
        # Flagged chunks aren't eligible at all, so the reranker should have dropped them.
        (IN_PERIOD_2, "شروط الاختراع", "any_chunk", False),
    ],
)
def test_takes_extra_slot(kind, today, sub_query, chunk_ref, expected):
    assert takes_extra_slot(kind.supersessions, kind.row, chunk_ref, sub_query, today) is expected


def test_a_new_row_never_takes_an_extra_slot(kind):
    assert not takes_extra_slot(kind.supersessions, new_row(), "any_chunk", "سؤال", IN_PERIOD_1)


# -- aggregator: the phase table ----------------------------------------------


@pytest.mark.parametrize(
    "today, expected_phase",
    [(IN_UPCOMING, UPCOMING), (IN_PERIOD_1, PERIOD_1), (IN_PERIOD_2, PERIOD_2)],
)
def test_a_live_transition_gets_a_block_and_no_status_on_either_law(kind, today, expected_phase):
    for row, version in ((kind.row, "old"), (new_row(), "new")):
        view = aggregator_status(kind.supersessions, row, today)
        assert view.status == ""
        assert view.version == version
        assert [t.phase for t in view.transitions] == [expected_phase]
        assert view.transitions[0].note == NOTE
        assert view.transitions[0].old_title == kind.old_title
        assert view.transitions[0].new_title == kind.new_title


def test_after_with_a_pending_part_the_block_stays_on_the_old_side_only(kind):
    # A pending part has no end date, so a block on the new side would ride on
    # every answer under the new law for good.
    old = aggregator_status(kind.supersessions, kind.row, IN_AFTER)
    assert old.status == "" and old.version == "old"
    assert [t.phase for t in old.transitions] == [AFTER]
    assert old.transitions[0].note == NOTE
    assert aggregator_status(kind.supersessions, new_row(), IN_AFTER) == AggregatorStatus()


def test_after_with_nothing_pending_is_status_only(kind):
    view = aggregator_status(kind.no_pending, kind.row, IN_AFTER)
    assert view == AggregatorStatus(status=f"ملغي — حلّ محله {kind.new_title}")


def test_the_new_law_is_plain_once_nothing_is_left(kind):
    assert aggregator_status(kind.no_pending, new_row(), IN_AFTER) == AggregatorStatus()


@pytest.mark.parametrize("today", [IN_UPCOMING, IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_lam_ya3ud_sariyan_is_never_shown_for_a_row_with_a_pair(kind, today):
    for supersessions in (kind.supersessions, kind.no_pending):
        assert REPEALED_LABEL_AR not in aggregator_status(supersessions, kind.row, today).status


# -- No pair: exactly today's behaviour ---------------------------------------


@pytest.mark.parametrize(
    "status_class, status_raw",
    [("cancelled", "لاغي"), ("cancelled", "ملغي"), ("in_force", "ساري"), ("consultation_ended", None), (None, None)],
)
@pytest.mark.parametrize("supersessions", [SupersessionMap(), SupersessionMap([make_pair()])])
@pytest.mark.parametrize("today", [IN_UPCOMING, IN_PERIOD_1, IN_PERIOD_2, IN_AFTER])
def test_a_no_pair_regulation_is_unchanged(supersessions, today, status_class, status_raw):
    row = {"id": "unrelated", "title": "نظام المراعي", "status_class": status_class, "status_raw": status_raw}
    today_line = status_line(status_class, status_raw)
    assert reranker_status(supersessions, row, "any_chunk", "النظام القديم", today) == today_line
    assert reranker_name(supersessions, row, "any_chunk", "سؤال", today) == "نظام المراعي"
    assert aggregator_status(supersessions, row, today) == AggregatorStatus(status=today_line)
    assert supersessions.for_regulation("unrelated") == ()
    assert supersessions.transition_note("unrelated") == ""


def test_a_missing_id_finds_nothing():
    supersessions = SupersessionMap([make_pair()])
    assert supersessions.for_regulation(None) == ()
    assert supersessions.old_side(None) is None


# -- Finding pairs: split, merge, chain, pending ------------------------------


def test_a_pair_is_found_from_either_side_by_uuid_or_ref():
    pair = make_pair()
    supersessions = SupersessionMap([pair])
    for key in ("old-uuid", "900_reg_001", "new-uuid", "900_reg_002"):
        assert supersessions.for_regulation(key) == (pair,)
    assert supersessions.old_side("old-uuid") is pair
    assert supersessions.old_side("new-uuid") is None


def test_a_split_is_one_pair_with_several_replacements():
    pair = Pair(
        old_id="old-uuid",
        old_ref="900_reg_001",
        old_title="نظام الواحات",
        superseded_on=SUPERSEDED_ON,
        period_1_end=PERIOD_1_END,
        period_2_end=PERIOD_2_END,
        successors=(
            Successor(title="نظام المياه", reg_ref="900_reg_002", reg_id="b", note="أ"),
            Successor(title="نظام النخيل", reg_ref="900_reg_003", reg_id="c", note="ب"),
        ),
    )
    supersessions = SupersessionMap([pair])
    assert supersessions.for_regulation("b") == supersessions.for_regulation("c") == (pair,)
    assert pair.new_title == "نظام المياه ونظام النخيل"
    # Each successor keeps its own note; the old row sees both.
    assert supersessions.transition_note("b") == "أ"
    assert supersessions.transition_note("old-uuid") == "أ\n\nب"
    status = aggregator_status(supersessions, {"id": "old-uuid"}, IN_AFTER).status
    assert status == "ملغي — حلّ محله نظام المياه ونظام النخيل"


def test_a_merge_lists_every_old_law_on_the_new_side():
    first = make_pair(old_id="a", old_ref="900_reg_001", old_title="نظام الآبار")
    second = make_pair(old_id="b", old_ref="900_reg_004", old_title="نظام السدود")
    supersessions = SupersessionMap([first, second])
    assert supersessions.for_regulation("new-uuid") == (first, second)
    view = aggregator_status(supersessions, new_row(), IN_PERIOD_1)
    assert [t.old_title for t in view.transitions] == [
        "نظام الآبار (القديم)", "نظام السدود (القديم)"
    ]
    assert view.version == "new"
    # Each old law stays independently findable.
    assert supersessions.old_side("a") is first
    assert supersessions.old_side("b") is second


def test_a_chain_lists_the_pair_retiring_the_row_first():
    earlier = make_pair()  # 001 -> 002
    later = Pair(
        old_id="new-uuid",
        old_ref="900_reg_002",
        old_title="نظام الواحات",
        superseded_on=SUPERSEDED_ON,
        period_1_end=PERIOD_1_END,
        period_2_end=PERIOD_2_END,
        successors=(Successor(title="نظام البوادي", reg_ref="900_reg_005"),),
    )
    supersessions = SupersessionMap([earlier, later])
    assert supersessions.for_regulation("900_reg_002") == (later, earlier)
    assert supersessions.old_side("900_reg_002") is later


def test_a_pending_successor_already_in_the_corpus_is_not_a_new_side():
    pair = Pair(
        old_id="old-uuid",
        old_ref="900_reg_001",
        old_title="نظام الواحات",
        superseded_on=SUPERSEDED_ON,
        period_1_end=PERIOD_1_END,
        period_2_end=PERIOD_2_END,
        successors=(
            Successor(title="نظام الواحات الموحد", reg_ref="900_reg_002"),
            Successor(title="نظام المراعي", reg_ref="900_reg_007", reg_id="m", part="الرعي", pending=True),
        ),
    )
    supersessions = SupersessionMap([pair])
    assert supersessions.for_regulation("m") == ()
    assert supersessions.for_regulation("900_reg_007") == ()


def test_removing_the_pending_entry_ends_the_exception():
    assert phase(make_pair(pending=False), IN_AFTER) == AFTER
    still = SupersessionMap([make_pair(pending=True)])
    ended = SupersessionMap([make_pair(pending=False)])
    assert aggregator_status(still, old_row(), IN_AFTER).transitions
    assert not aggregator_status(ended, old_row(), IN_AFTER).transitions


def test_an_old_row_reads_its_successors_note():
    supersessions = SupersessionMap([make_pair()])
    assert supersessions.transition_note("old-uuid") == NOTE
    assert supersessions.transition_note("900_reg_002") == NOTE


# -- Version markers ----------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("نظام الواحات القديم", "old"),
        ("نظام الواحات (القديم)", "old"),
        ("اللائحة القديمة لنظام الواحات", "old"),
        ("اللائحة التنفيذية لنظام الواحات (القديمة)", "old"),
        ("نظام الواحات الجديد", "new"),
        ("نظام الواحات (الجديد)", "new"),
        ("اللائحة الجديدة لنظام الواحات", "new"),
        ("نظام الواحات", None),
        ("", None),
        (None, None),
        ("قديم", None),  # no article: an adjective in a sentence, not a marker
        ("الفرق بين النظام القديم والجديد", None),  # both named: no single choice
    ],
)
def test_version_choice(text, expected):
    assert version_choice(text) == expected


def test_a_comparison_still_names_the_old_version():
    assert names_old_version("الفرق بين النظام القديم والجديد") is True
    assert names_old_version("مقارنة بالقديم") is True
    assert names_old_version("نظام الواحات") is False
    assert names_old_version(None) is False


@pytest.mark.parametrize(
    "text, expected",
    [
        ("نظام الواحات القديم", "نظام الواحات"),
        ("نظام الواحات (القديم)", "نظام الواحات"),
        ("نظام الواحات - الجديد", "نظام الواحات"),
        ("نظام الواحات (الجديد)", "نظام الواحات"),
        ("اللائحة التنفيذية القديمة لنظام الواحات", "اللائحة التنفيذية لنظام الواحات"),
        ("اللائحة التنفيذية لنظام الواحات (القديم)", "اللائحة التنفيذية لنظام الواحات"),
        ("نظام الواحات", "نظام الواحات"),
        ("", ""),
        (None, ""),
    ],
)
def test_strip_version_marker(text, expected):
    assert strip_version_marker(text) == expected


# -- Instructions and the responder line --------------------------------------


@pytest.mark.parametrize("current", [UPCOMING, PERIOD_1, PERIOD_2, AFTER])
def test_every_phase_has_an_instruction_with_the_shared_rules(current):
    text = phase_instruction(current)
    assert "`[n]`" in text
    assert "gaps section" in text


def test_instructions_differ_where_the_plan_says_they_do():
    assert "old law" in phase_instruction(UPCOMING)
    assert "إن كان النظام القديم هو المنطبق" in phase_instruction(PERIOD_1)
    assert "opening sentences" in phase_instruction(PERIOD_2)
    # After the second period the new law is plain law: nothing asks the answer to
    # announce it, and the old text is relied on only within the surviving part.
    assert "opening sentences" not in phase_instruction(AFTER)
    assert "remains in force" in phase_instruction(AFTER)


def test_responder_lines_by_phase():
    supersessions = SupersessionMap([make_pair()])
    period_1 = aggregator_status(supersessions, old_row(), IN_PERIOD_1).transitions
    period_2 = aggregator_status(supersessions, old_row(), IN_PERIOD_2).transitions
    assert responder_line(period_1) == (
        "بدأ نفاذ «نظام الواحات» في 2030/03/15م بدلًا من «نظام الواحات (القديم)»، "
        "والتقرير يبيّن أيهما ينطبق على حالتك."
    )
    assert responder_line(period_2) == (
        "بدأ نفاذ «نظام الواحات» في 2030/03/15م بدلًا من «نظام الواحات (القديم)»، "
        "والتحليل مبني على النسخة الجديدة إلا ما يذكره التقرير صراحةً."
    )


@pytest.mark.parametrize("today", [IN_PERIOD_1, IN_PERIOD_2])
def test_a_responder_line_never_claims_which_law_the_analysis_is_under(today):
    # The facts can put a matter under the old law, and the line is fixed text
    # that can't know. It must stay true whichever law the analysis landed on.
    supersessions = SupersessionMap([make_pair()])
    line = responder_line(aggregator_status(supersessions, old_row(), today).transitions)
    assert line
    assert "هذا التحليل وفق" not in line
    assert "لا وفق" not in line


def test_a_replacement_that_keeps_the_old_name_still_reads_as_two_laws():
    # The normal case: only a hand-renamed old row carries «(القديم)» in its title.
    same_name = make_pair(new_title="نظام المراعي", old_title="نظام المراعي")
    supersessions = SupersessionMap([same_name])
    transitions = aggregator_status(supersessions, {"id": "old-uuid"}, IN_PERIOD_1).transitions
    assert transitions[0].old_title == "نظام المراعي (القديم)"
    assert transitions[0].new_title == "نظام المراعي"
    assert "بدلًا من «نظام المراعي (القديم)»" in responder_line(transitions)


@pytest.mark.parametrize("current", [PERIOD_1, PERIOD_2])
def test_instructions_assume_nothing_about_the_law(current):
    # A law may have no transitional provisions at all, or carry them as articles
    # rather than as decree clauses, and may not concern enforcement.
    text = phase_instruction(current)
    assert "`<provisions>`" in text
    assert "«المادة (…) من …»" in text
    assert "requests pending" not in text


def test_no_responder_line_without_a_transition_or_while_upcoming_or_after():
    supersessions = SupersessionMap([make_pair()])
    assert responder_line(()) == ""
    assert responder_line(aggregator_status(supersessions, old_row(), IN_UPCOMING).transitions) == ""
    assert responder_line(aggregator_status(supersessions, old_row(), IN_AFTER).transitions) == ""


def test_a_law_and_its_laiha_share_one_note_entry_and_one_line():
    law = make_pair()
    laiha = make_pair(
        new_title="اللائحة التنفيذية لنظام الواحات",
        old_title="اللائحة التنفيذية لنظام الواحات (القديم)",
        old_id="old-laiha",
        old_ref="900_reg_010",
    )
    supersessions = SupersessionMap([law, laiha])
    transitions = (
        aggregator_status(supersessions, {"id": "old-uuid"}, IN_PERIOD_1).transitions
        + aggregator_status(supersessions, {"id": "old-laiha"}, IN_PERIOD_1).transitions
        # A second reference from the same law repeats its transition.
        + aggregator_status(supersessions, {"id": "old-uuid"}, IN_PERIOD_1).transitions
    )
    grouped = group_by_note(transitions)
    assert list(grouped) == [NOTE]
    assert len(grouped[NOTE]) == 2
    assert responder_line(transitions).count("بدأ نفاذ") == 1


# -- Loading ------------------------------------------------------------------


class FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.filters = {}
        self.payload = None
        self.negated = False

    def select(self, columns):
        self.columns = columns
        return self

    @property
    def not_(self):
        self.negated = True
        return self

    def is_(self, column, value):
        self.filters[("not_is" if self.negated else "is", column)] = value
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def in_(self, column, values):
        self.filters[("in", column)] = list(values)
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def execute(self):
        self.client.calls.append(self)
        if self.client.error:
            raise self.client.error
        if self.payload is not None:
            self.client.writes.append((self.filters.get("reg_ref"), self.payload))
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=self.client.rows_for(self))


class FakeClient:
    """Answers the two reads ``load_supersessions`` makes, and counts them."""

    def __init__(self, rows=(), error=None):
        self.rows = list(rows)
        self.error = error
        self.calls = []
        self.writes = []

    def table(self, name):
        return FakeQuery(self, name)

    def rows_for(self, query):
        wanted = query.filters.get(("in", "reg_ref"))
        if wanted is not None:
            return [r for r in self.rows if r["reg_ref"] in wanted]
        return [r for r in self.rows if r.get("superseded_by") is not None]


def corpus_rows():
    return [
        {
            "id": "old-uuid",
            "reg_ref": "900_reg_001",
            "clean_title": "نظام الواحات (القديم)",
            "title": "نظام الواحات",
            "superseded_by": [
                {"reg_ref": "900_reg_002", "title": "نظام الواحات"},
                {
                    "reg_ref": None,
                    "title": "نظام المراعي",
                    "part": "الرعي الموسمي",
                    "pending": True,
                    "chunk_refs": [SURVIVING_CHUNK],
                },
            ],
            "superseded_dates": {
                "superseded_on": SUPERSEDED_ON.isoformat(),
                "period_1_end": PERIOD_1_END.isoformat(),
                "period_2_end": PERIOD_2_END.isoformat(),
            },
        },
        {"id": "new-uuid", "reg_ref": "900_reg_002", "transition_period_note": NOTE},
    ]


@pytest.fixture
def clock(monkeypatch):
    now = SimpleNamespace(value=1000.0)
    monkeypatch.setattr(sup, "_clock", lambda: now.value)
    return now


def test_load_builds_pairs_from_rows(clock):
    supersessions = load_supersessions(FakeClient(corpus_rows()))
    pair = supersessions.old_side("900_reg_001")
    assert pair.superseded_on == SUPERSEDED_ON
    assert pair.surviving_chunks == {SURVIVING_CHUNK}
    assert supersessions.for_regulation("new-uuid") == (pair,)
    assert supersessions.transition_note("old-uuid") == NOTE
    assert reranker_status(supersessions, old_row(), OTHER_CHUNK, "سؤال", IN_PERIOD_1) == ""


def test_an_empty_superseded_by_is_a_plain_regulation(clock):
    rows = corpus_rows()
    rows[0]["superseded_by"] = []
    supersessions = load_supersessions(FakeClient(rows))
    assert supersessions.for_regulation("old-uuid") == ()


def test_the_cache_serves_within_the_ttl_and_refreshes_after(clock):
    client = FakeClient(corpus_rows())
    first = load_supersessions(client)
    reads = len(client.calls)

    clock.value += sup._CACHE_TTL_SECONDS - 1
    assert load_supersessions(client) is first
    assert len(client.calls) == reads

    clock.value += 2
    assert load_supersessions(client) is not first
    assert len(client.calls) > reads


def test_first_failure_is_empty_and_warns(clock, caplog):
    with caplog.at_level(logging.WARNING, logger=sup.__name__):
        supersessions = load_supersessions(FakeClient(error=RuntimeError("boom")))
    assert supersessions.for_regulation("old-uuid") == ()
    assert "boom" in caplog.text


def test_a_failure_keeps_the_last_good_map(clock):
    good = load_supersessions(FakeClient(corpus_rows()))
    clock.value += sup._CACHE_TTL_SECONDS + 1
    assert load_supersessions(FakeClient(error=RuntimeError("boom"))) is good


def test_a_failure_is_retried_soon_but_not_per_chunk(clock):
    broken = FakeClient(error=RuntimeError("boom"))
    load_supersessions(broken)
    load_supersessions(broken)
    assert len(broken.calls) == 1

    clock.value += sup._RETRY_AFTER_FAILURE_SECONDS + 1
    assert load_supersessions(FakeClient(corpus_rows())).for_regulation("old-uuid")


def test_a_failed_load_gives_today_status_for_the_old_law(clock):
    supersessions = load_supersessions(FakeClient(error=RuntimeError("boom")))
    line = reranker_status(supersessions, old_row(), OTHER_CHUNK, "سؤال", IN_PERIOD_1)
    assert line == status_line("cancelled", "لاغي")


def test_reset_cache_forgets_the_map(clock):
    load_supersessions(FakeClient(corpus_rows()))
    reset_cache()
    assert load_supersessions(FakeClient()).for_regulation("old-uuid") == ()


def test_importing_the_module_touches_no_network():
    # The map starts empty and is only filled through a passed-in client.
    reset_cache()
    assert sup._cached.for_regulation("anything") == ()


# -- Principle 1: no law in code ----------------------------------------------


@pytest.mark.parametrize("relative", ["shared/library/supersession.py", "scripts/set_supersession.py"])
def test_no_law_ref_or_date_in_code(relative):
    source = (REPO_ROOT / relative).read_text(encoding="utf-8")
    assert not re.search(r"\d+_reg_\d+", source)
    assert not re.search(r"20\d\d-\d\d-\d\d", source)
    assert "م/" not in source


# -- scripts/set_supersession.py ----------------------------------------------


@pytest.fixture(scope="module")
def script():
    path = REPO_ROOT / "scripts" / "set_supersession.py"
    spec = importlib.util.spec_from_file_location("set_supersession", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeScriptClient(FakeClient):
    def rows_for(self, query):
        if query.table == "chunks_v2":
            wanted = query.filters[("in", "chunk_ref")]
            return [{"chunk_ref": r} for r in wanted if r.startswith(query.filters["regulation_id"])]
        return [r for r in self.rows if r["reg_ref"] == query.filters.get("reg_ref")]


def script_rows():
    return [
        {"id": "900_reg_001", "reg_ref": "900_reg_001", "clean_title": "نظام الواحات (القديم)", "title": "x"},
        {
            "id": "900_reg_002",
            "reg_ref": "900_reg_002",
            "clean_title": "نظام الواحات",
            "effective_date": SUPERSEDED_ON.isoformat(),
            "intro": f"مقدمة {NOTE} خاتمة",
        },
    ]


@pytest.mark.parametrize(
    "start, months, expected",
    [
        (date(2030, 3, 15), 3, date(2030, 6, 15)),
        (date(2030, 3, 15), 12, date(2031, 3, 15)),
        (date(2030, 11, 30), 3, date(2031, 2, 28)),  # clamped
        (date(2030, 10, 15), 3, date(2031, 1, 15)),  # across the year
    ],
)
def test_add_months(script, start, months, expected):
    assert script.add_months(start, months) == expected


def test_parse_surviving(script):
    parsed = script.parse_surviving(["الرعي=a_chunk_1, a_chunk_2", "الآبار=a_chunk_3"])
    assert parsed == {"الرعي": ["a_chunk_1", "a_chunk_2"], "الآبار": ["a_chunk_3"]}


@pytest.mark.parametrize("value", ["الرعي", "الرعي=", "=a_chunk_1", "الرعي= , "])
def test_parse_surviving_rejects_malformed_values(script, value):
    with pytest.raises(ValueError):
        script.parse_surviving([value])


def run_script(script, tmp_path, client, *extra, note=NOTE):
    note_file = tmp_path / "note.txt"
    note_file.write_text(note, encoding="utf-8")
    argv = ["--old", "900_reg_001", "--new", "900_reg_002", "--note-file", str(note_file), *extra]
    return script.main(argv, client=client)


def test_dry_run_writes_nothing(script, tmp_path, capsys):
    client = FakeScriptClient(script_rows())
    assert run_script(script, tmp_path, client) == 0
    assert client.writes == []
    assert "DRY RUN" in capsys.readouterr().out


def test_apply_writes_the_new_row_before_the_old_row(script, tmp_path):
    client = FakeScriptClient(script_rows())
    surviving = "--surviving", "الرعي=900_reg_001_chunk_004"
    title = "--surviving-title", "الرعي=نظام المراعي"
    assert run_script(script, tmp_path, client, *surviving, *title, "--apply") == 0

    assert [ref for ref, _ in client.writes] == ["900_reg_002", "900_reg_001"]
    assert client.writes[0][1] == {"transition_period_note": NOTE}
    old_update = client.writes[1][1]
    assert old_update["superseded_dates"] == {
        "superseded_on": "2030-03-15",
        "period_1_end": "2030-06-15",
        "period_2_end": "2031-03-15",
    }
    assert old_update["superseded_by"] == [
        {"reg_ref": "900_reg_002", "title": "نظام الواحات"},
        {
            "reg_ref": None,
            "title": "نظام المراعي",
            "part": "الرعي",
            "pending": True,
            "chunk_refs": ["900_reg_001_chunk_004"],
        },
    ]


def test_date_overrides(script, tmp_path):
    client = FakeScriptClient(script_rows())
    assert run_script(script, tmp_path, client, "--period-1-end", "2030-04-01", "--apply") == 0
    assert client.writes[1][1]["superseded_dates"]["period_1_end"] == "2030-04-01"


@pytest.mark.parametrize(
    "extra, message",
    [
        (("--surviving", "الرعي=901_reg_001_chunk_004"), "is not on"),
        (("--period-1-end", "2029-01-01"), "must ascend"),
        (("--surviving-title", "الآبار=نظام"), "unknown part"),
    ],
)
def test_validation_errors_write_nothing(script, tmp_path, capsys, extra, message):
    client = FakeScriptClient(script_rows())
    assert run_script(script, tmp_path, client, *extra, "--apply") == 1
    assert client.writes == []
    assert message in capsys.readouterr().out


def test_a_missing_row_writes_nothing(script, tmp_path, capsys):
    client = FakeScriptClient(script_rows()[:1])
    assert run_script(script, tmp_path, client, "--apply") == 1
    assert client.writes == []
    assert "new row not found" in capsys.readouterr().out


def test_a_new_row_without_effective_date_needs_superseded_on(script, tmp_path, capsys):
    rows = script_rows()
    del rows[1]["effective_date"]
    assert run_script(script, tmp_path, FakeScriptClient(rows), "--apply") == 1
    assert "effective_date" in capsys.readouterr().out

    client = FakeScriptClient(rows)
    assert run_script(script, tmp_path, client, "--superseded-on", "2030-03-15", "--apply") == 0


def test_a_retyped_note_warns_but_is_not_blocked(script, tmp_path, capsys):
    client = FakeScriptClient(script_rows())
    assert run_script(script, tmp_path, client, note="نص مختلف") == 0
    assert "WARNING" in capsys.readouterr().out


# -- The provisions travel with the chunk ---------------------------------------


@pytest.mark.parametrize("today", [IN_PERIOD_1, IN_PERIOD_2])
@pytest.mark.parametrize("row", [old_row(), new_row()], ids=["old", "new"])
def test_both_sides_carry_the_provisions_during_the_transition(row, today):
    standing = aggregator_status(SupersessionMap([make_pair()]), row, today)
    assert chunk_provisions(standing.transitions) == NOTE


def test_no_provisions_while_the_old_law_simply_applies():
    standing = aggregator_status(SupersessionMap([make_pair()]), old_row(), IN_UPCOMING)
    assert standing.transitions
    assert chunk_provisions(standing.transitions) == ""


def test_after_the_periods_only_a_part_still_in_force_carries_them():
    supersessions = SupersessionMap([make_pair()])
    assert chunk_provisions(aggregator_status(supersessions, new_row(), IN_AFTER).transitions) == ""
    assert chunk_provisions(aggregator_status(supersessions, old_row(), IN_AFTER).transitions) == NOTE


def test_a_row_with_no_pair_carries_nothing():
    plain = {"id": "plain", "clean_title": "نظام السجل", "status_class": "in_force"}
    standing = aggregator_status(SupersessionMap([make_pair()]), plain, IN_PERIOD_1)
    assert chunk_provisions(standing.transitions) == ""


def test_a_row_in_two_pairs_joins_their_distinct_notes():
    pair = make_pair()
    same = chunk_provisions((*aggregator_status(SupersessionMap([pair]), old_row(), IN_PERIOD_1).transitions,) * 2)
    assert same == NOTE


# -- What the prompts ask for -----------------------------------------------------


@pytest.mark.parametrize("current", [PERIOD_1, PERIOD_2])
def test_the_chat_reply_is_not_described_as_the_opening_alone(current):
    text = phase_instruction(current)
    assert "opening of the synthesis only" not in text
    assert "always including its opening" in text


@pytest.mark.parametrize("pinned", [True, False])
def test_both_versions_leaves_a_way_to_say_a_term_is_the_old_versions(pinned):
    text = both_versions_instruction(pinned_articles=pinned)
    assert "only old-version references use" in text
    assert "never present it as the new law's" in text
    assert "say that the references do not show it" in text


def test_the_version_rule_names_no_law():
    assert "نظام" not in sup.RESPONDER_VERSION_RULE
    assert "لائحة" not in sup.RESPONDER_VERSION_RULE
