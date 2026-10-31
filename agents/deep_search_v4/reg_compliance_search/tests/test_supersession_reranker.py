"""Supersession at the reranker: what the decider sees, and who gets a slot.

The live decider scores against a frozen rubric that drops anything flagged
«ملغي», so code decides how an old chunk reaches it. An eligible chunk arrives
looking like in-force law, a flagged one arrives exactly as it does today, and a
law with no supersession data must not change by a single byte.

Law names are invented. Sync on purpose: CI has no ``pytest-asyncio``.
"""
from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from agents.deep_search_v4.reg_compliance_search import reranker as reranker_mod
from agents.deep_search_v4.reg_compliance_search.models import (
    RegKeep,
    RegRerankerClassification,
)
from agents.deep_search_v4.reg_compliance_search.reranker import (
    TRANSITION_EXTRA_KEEP,
    _apply_keep_cap,
    _assemble_result,
    _cap_sort_key,
    _make_block,
    run_reranker_for_query,
)
from agents.deep_search_v4.shared import decider as decider_mod
from agents.deep_search_v4.shared.decider import DeciderReply
from shared.library.reg_status import REPEALED_LABEL_AR
from shared.library.supersession import (
    Pair,
    Successor,
    SupersessionMap,
    old_chunk_eligible,
)

_OLD_ID = "reg-old"
_NEW_ID = "reg-new"
_PLAIN_ID = "reg-plain"
_SCOPE = "ينظم هذا النص أعمال الوساطة"
_STATUS_LINE = "**حالة النظام:**"

_SURVIVING_REF = "reg_old_chunk_007"

_PERIOD_1_DAY = date(2031, 3, 15)
_PERIOD_2_DAY = date(2031, 9, 1)
_AFTER_DAY = date(2033, 1, 1)

_LAWS = {
    "nizam": ("نظام الوساطة البحرية (القديم)", "نظام الوساطة البحرية"),
    "laiha": (
        "اللائحة التنفيذية لنظام الوساطة البحرية (القديمة)",
        "اللائحة التنفيذية لنظام الوساطة البحرية",
    ),
}


@pytest.fixture(params=_LAWS, ids=str)
def law(request) -> tuple[str, str]:
    return _LAWS[request.param]


def _pair(old_title: str, new_title: str, *, pending: bool = True) -> Pair:
    successors = [Successor(title=new_title, reg_ref="ref-new", reg_id=_NEW_ID)]
    if pending:
        successors.append(
            Successor(
                title="نظام آخر", part="الجزء الباقي", pending=True,
                chunk_refs=(_SURVIVING_REF,),
            )
        )
    return Pair(
        old_id=_OLD_ID,
        old_ref="ref-old",
        old_title=old_title,
        superseded_on=date(2031, 3, 1),
        period_1_end=date(2031, 6, 1),
        period_2_end=date(2032, 3, 1),
        successors=tuple(successors),
    )


def _reg_row(title: str, *, repealed: bool = False) -> dict:
    return {
        "clean_title": title,
        "title": title,
        "scope": _SCOPE,
        "status_class": "cancelled" if repealed else "in_force",
        "status_raw": "لاغي" if repealed else "ساري",
    }


class _Query:
    def __init__(self, supabase: "_Supabase", table: str) -> None:
        self._supabase = supabase
        self._table = table
        self._data = None

    def select(self, columns: str) -> "_Query":
        self._supabase.selects.append((self._table, columns))
        return self

    def eq(self, _column: str, value: str) -> "_Query":
        self._data = self._supabase.regs.get(value)
        return self

    def in_(self, _column: str, _values: list[str]) -> "_Query":
        self._data = []
        return self

    def maybe_single(self) -> "_Query":
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _Supabase:
    def __init__(self, regs: dict[str, dict]) -> None:
        self.regs = regs
        self.selects: list[tuple[str, str]] = []

    def table(self, name: str) -> _Query:
        return _Query(self, name)


def _chunk(
    chunk_id: str,
    regulation_id: str,
    *,
    chunk_ref: str | None = None,
    mode: str = "simple",
    rrf: float = 0.5,
) -> dict:
    return {
        "id": chunk_id,
        "chunk_ref": chunk_ref or f"{chunk_id}_ref",
        "regulation_id": regulation_id,
        "title": "المادة الخامسة",
        "summary": "ملخص المقطع",
        "context": "سياق المقطع",
        "prev_chunk_id": None,
        "next_chunk_id": None,
        "source_type": "regulation",
        "_mode": mode,
        "_rrf": rrf,
    }


def _block(supabase: _Supabase, chunk: dict, **gate) -> dict:
    return _make_block(supabase, chunk, "C1", chunk["_mode"], chunk["_rrf"], **gate)


# -- A law with no pair: byte-identical ----------------------------------------


def _expected_block(mode: str, title: str, status_line: str = "") -> str:
    """Pinned against ``master``: the block as it was before this feature."""
    head = ["### [C1] المادة الخامسة", f"**النظام:** {title}"]
    if status_line:
        head.append(f"{_STATUS_LINE} {status_line}")
    head += [f"**نطاق النظام:** {_SCOPE}", "**درجة الصلة:** الترتيب: 0.5", ""]
    if mode == "simple":
        return "\n".join([*head, "**ملخص المقطع:** ملخص المقطع"])
    return "\n".join([
        *head,
        "**سياق المقطع السابق:** (بداية النظام — لا يوجد مقطع سابق)",
        "**سياق المقطع الحالي:** سياق المقطع",
        "**ملخص المقطع الحالي:** ملخص المقطع",
        "**سياق المقطع التالي:** (نهاية النظام — لا يوجد مقطع تالٍ)",
    ])


@pytest.mark.parametrize("mode", ["simple", "precise"])
@pytest.mark.parametrize("title", ["نظام الموانئ الجافة", "لائحة الموانئ الجافة"])
def test_no_pair_law_block_is_byte_identical(mode: str, title: str):
    supabase = _Supabase({_PLAIN_ID: _reg_row(title)})
    chunk = _chunk("c1", _PLAIN_ID, mode=mode)
    # A map full of OTHER laws is the production case.
    gate = {
        "supersessions": SupersessionMap([_pair("قديم آخر", "جديد آخر")]),
        "sub_query": "سؤال عن القديم",
        "today": _PERIOD_1_DAY,
    }

    block = _block(supabase, chunk, **gate)

    assert block["markdown"] == _expected_block(mode, title)
    assert block["markdown"] == _block(supabase, chunk)["markdown"]


@pytest.mark.parametrize("title", ["نظام الموانئ الجافة", "لائحة الموانئ الجافة"])
def test_no_pair_repealed_law_keeps_its_line_and_is_not_old(title: str):
    supabase = _Supabase({_PLAIN_ID: _reg_row(title, repealed=True)})
    gate = {"supersessions": SupersessionMap(), "sub_query": "س", "today": _PERIOD_1_DAY}

    block = _block(supabase, _chunk("c1", _PLAIN_ID), **gate)

    assert block["markdown"] == _expected_block(
        "simple", title, f"{REPEALED_LABEL_AR} (النص الأصلي: لاغي)"
    )


def test_no_extra_query_and_no_extra_column():
    supabase = _Supabase({_PLAIN_ID: _reg_row("نظام الموانئ الجافة")})
    gate = {"supersessions": SupersessionMap(), "sub_query": "س", "today": _PERIOD_1_DAY}

    _block(supabase, _chunk("c1", _PLAIN_ID), **gate)

    assert supabase.selects == [
        ("regulations_v2", "clean_title, title, scope, status_class, status_raw"),
    ]


# -- An old chunk ---------------------------------------------------------------


@pytest.mark.parametrize("mode", ["simple", "precise"])
def test_eligible_old_chunk_reaches_the_reranker_clean(law, mode: str):
    old_title, new_title = law
    supabase = _Supabase({_OLD_ID: _reg_row(old_title, repealed=True)})
    gate = {
        "supersessions": SupersessionMap([_pair(old_title, new_title)]),
        "sub_query": "سؤال",
        "today": _PERIOD_1_DAY,
    }

    block = _block(supabase, _chunk("c1", _OLD_ID, mode=mode), **gate)

    assert "القديم" not in block["markdown"]
    assert _STATUS_LINE not in block["markdown"]
    assert f"**النظام:** {new_title}\n" in block["markdown"]
    # ... while the truth survives for every later stage.
    result = _assemble_result(block, {"relevance": "high"})
    assert result.regulation_title == old_title


@pytest.mark.parametrize("mode", ["simple", "precise"])
@pytest.mark.parametrize("today", [_PERIOD_2_DAY, _AFTER_DAY])
def test_flagged_old_chunk_is_unchanged_from_today(law, mode: str, today: date):
    old_title, new_title = law
    supabase = _Supabase({_OLD_ID: _reg_row(old_title, repealed=True)})
    chunk = _chunk("c1", _OLD_ID, mode=mode)
    gate = {
        "supersessions": SupersessionMap([_pair(old_title, new_title)]),
        "sub_query": "سؤال",
        "today": today,
    }

    block = _block(supabase, chunk, **gate)

    assert block["markdown"] == _expected_block(
        mode, old_title, f"{REPEALED_LABEL_AR} (النص الأصلي: لاغي)"
    )
    assert block["markdown"] == _block(supabase, chunk)["markdown"]


@pytest.mark.parametrize(
    ("chunk_ref", "sub_query"),
    [(_SURVIVING_REF, "سؤال"), ("reg_old_chunk_001", "المادة 5 من النظام القديم")],
    ids=["surviving-chunk", "names-old-version"],
)
@pytest.mark.parametrize("today", [_PERIOD_2_DAY, _AFTER_DAY])
def test_exceptions_after_period_1_reach_the_reranker_clean(
    law, chunk_ref: str, sub_query: str, today: date
):
    old_title, new_title = law
    supabase = _Supabase({_OLD_ID: _reg_row(old_title, repealed=True)})
    gate = {
        "supersessions": SupersessionMap([_pair(old_title, new_title)]),
        "sub_query": sub_query,
        "today": today,
    }

    block = _block(supabase, _chunk("c1", _OLD_ID, chunk_ref=chunk_ref), **gate)

    assert "القديم" not in block["markdown"]
    assert _STATUS_LINE not in block["markdown"]


def test_new_law_chunk_is_untouched(law):
    old_title, new_title = law
    supabase = _Supabase({_NEW_ID: _reg_row(new_title)})
    gate = {
        "supersessions": SupersessionMap([_pair(old_title, new_title)]),
        "sub_query": "سؤال",
        "today": _PERIOD_1_DAY,
    }

    block = _block(supabase, _chunk("c1", _NEW_ID), **gate)

    assert block["markdown"] == _expected_block("simple", new_title)


# -- Extra slots: the pure cap --------------------------------------------------


def _kept(label: str, rrf: float, *, old: bool = False, relevance: str = "medium",
          score: float = 0.0) -> tuple[dict, dict]:
    return (
        {"label": label, "rrf": rrf, "old": old},
        {"relevance": relevance, "score": score},
    )


def _is_old(block: dict) -> bool:
    return block["old"]


def _labels(pairs: list[tuple[dict, dict]]) -> list[str]:
    return [block["label"] for block, _dec in pairs]


_OTHERS = [
    _kept("n1", 0.90, relevance="high", score=3.0),
    _kept("n2", 0.80, relevance="high", score=3.5),
    _kept("n3", 0.70, score=2.0),
    _kept("n4", 0.60, score=2.0),
    _kept("n5", 0.50, score=1.5),
    _kept("n6", 0.40, score=1.5),
    _kept("n7", 0.30, score=1.5),
    _kept("n8", 0.20, score=1.5),
    _kept("n9", 0.10, score=1.5),
]
_OLD = [
    _kept("o1", 0.99, old=True, relevance="high", score=4.0),
    _kept("o2", 0.95, old=True, relevance="high", score=3.0),
    _kept("o3", 0.85, old=True, score=2.5),
    _kept("o4", 0.75, old=True, score=2.0),
    _kept("o5", 0.65, old=True, score=1.5),
]


@pytest.mark.parametrize("max_keep", [1, 2, 4, 8, 9, 12])
@pytest.mark.parametrize("old_count", [1, 3, 5])
def test_other_chunks_are_chosen_as_if_old_ones_were_not_there(max_keep: int, old_count: int):
    old = _OLD[:old_count]
    alone, _ = _apply_keep_cap(_OTHERS, max_keep, _is_old)

    capped, cut = _apply_keep_cap(old + _OTHERS, max_keep, _is_old)

    labels = _labels(capped)
    added = old_count if old_count <= TRANSITION_EXTRA_KEEP else TRANSITION_EXTRA_KEEP
    # The rest first, then the old chunks, best score first.
    assert labels == _labels(alone) + _labels(_OLD[:added])
    assert len(labels) == len(alone) + added
    # Nothing vanishes: every candidate is either kept or a recorded cut.
    assert sorted(labels + _labels(cut)) == sorted(_labels(old + _OTHERS))


def test_at_most_three_extra_slots():
    assert TRANSITION_EXTRA_KEEP == 3


@pytest.mark.parametrize("max_keep", [1, 4, 8, 12])
def test_with_no_old_chunk_the_cap_is_the_flat_one(max_keep: int):
    capped, cut = _apply_keep_cap(_OTHERS, max_keep, _is_old)

    flat = sorted(_OTHERS, key=_cap_sort_key)
    assert capped == flat[:max_keep]
    assert cut == flat[max_keep:]


def test_old_chunks_tied_on_zero_scores_keep_rrf_order_then_arrival_order():
    """The qwen path has no decider score, so the sort must still be stable."""
    old = [
        _kept("a", 0.5, old=True),
        _kept("b", 0.9, old=True),
        _kept("c", 0.5, old=True),
        _kept("d", 0.5, old=True),
    ]

    capped, cut = _apply_keep_cap(old, 8, _is_old)

    assert _labels(capped) == ["b", "a", "c"]
    assert _labels(cut) == ["d"]


# -- Extra slots: through run_reranker_for_query, on both engines -----------------


class _MockUsage:
    requests = 1
    input_tokens = 10
    output_tokens = 5
    total_tokens = 15
    details: dict = {}


class _MockRun:
    def __init__(self, output) -> None:
        self.output = output

    def usage(self) -> _MockUsage:
        return _MockUsage()


class _MockAgent:
    def __init__(self, output) -> None:
        self._output = output

    async def run(self, _msg, **_kw) -> _MockRun:
        return _MockRun(self._output)


@pytest.fixture(params=["decider", "llm"])
def engine(request, monkeypatch) -> str:
    """Keep every candidate as high, on either engine."""
    settings = SimpleNamespace(
        REG_RERANKER_ENGINE=request.param,
        REG_RERANKER_DECIDERS="liquid/d1-ultra",
        OPENROUTER_RERANKER_API_KEY="sk-or-test",
    )

    async def _fake_decide(_state, questions, *, required, models):
        return DeciderReply(
            model=models[0],
            model_served=models[0],
            answers={label: {"score": 3.0} for label in questions},
            usage_entry={"agent": "reranker", "model": models[0]},
            fallback_from=[],
        )

    def _fake_agent(**_kw) -> _MockAgent:
        return _MockAgent(
            RegRerankerClassification(
                sufficient=True,
                keeps=[
                    RegKeep(label=f"C{n}", relevance="high", reasoning="ق")
                    for n in range(1, 21)
                ],
                summary_note="",
            )
        )

    async def _no_http(*_a, **_kw):
        raise AssertionError("unexpected HTTP call")

    monkeypatch.setattr(reranker_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(decider_mod, "decide", _fake_decide)
    monkeypatch.setattr(reranker_mod, "create_reranker_agent", _fake_agent)
    monkeypatch.setattr(httpx.AsyncClient, "send", _no_http)
    return request.param


def _rerank(
    monkeypatch,
    engine: str,
    chunks: list[dict],
    *,
    law: tuple[str, str],
    query: str = "سؤال",
    max_keep: int = 2,
    today: date = _PERIOD_1_DAY,
    round_trace: list[dict] | None = None,
):
    old_title, new_title = law
    supersessions = SupersessionMap([_pair(old_title, new_title)])
    monkeypatch.setattr(reranker_mod, "load_supersessions", lambda _s: supersessions)
    monkeypatch.setattr(reranker_mod, "riyadh_today", lambda: today)
    supabase = _Supabase({
        _OLD_ID: _reg_row(old_title, repealed=True),
        _NEW_ID: _reg_row(new_title),
    })
    result, _usage, _log = asyncio.run(
        run_reranker_for_query(
            query=query, rationale="ت", chunks=chunks, supabase=supabase,
            max_keep=max_keep, engine=engine, round_trace=round_trace,
        )
    )
    return result


def _ids(result) -> list[str]:
    return [r.db_id for r in result.results]


_NEW_CHUNKS = [_chunk(f"n{i}", _NEW_ID, rrf=1 - i / 10) for i in range(1, 6)]
_OLD_CHUNKS = [_chunk(f"o{i}", _OLD_ID, rrf=1.05 - i / 10) for i in range(1, 6)]


def test_old_chunks_take_extra_slots_on_either_engine(monkeypatch, engine, law):
    alone = _rerank(monkeypatch, engine, _NEW_CHUNKS, law=law)

    result = _rerank(monkeypatch, engine, _OLD_CHUNKS + _NEW_CHUNKS, law=law)

    assert _ids(alone) == ["n1", "n2"]
    assert _ids(result) == ["n1", "n2", "o1", "o2", "o3"]
    assert result.results[2].regulation_title == law[0]


def test_old_chunks_past_the_extra_slots_are_recorded_as_cap_drops(monkeypatch, engine, law):
    result = _rerank(monkeypatch, engine, _OLD_CHUNKS + _NEW_CHUNKS, law=law)

    dropped = {d["db_id"]: d["drop_reason"] for d in result.dropped_results}
    assert dropped == {
        "n3": "cap", "n4": "cap", "n5": "cap", "o4": "cap", "o5": "cap",
    }
    assert result.dropped_count == 5
    assert result.caps_applied == {"max_keep": 2, "truncated_by_cap": 5}


def test_sub_query_naming_the_old_version_ranks_normally(monkeypatch, engine, law):
    result = _rerank(
        monkeypatch, engine, _OLD_CHUNKS + _NEW_CHUNKS, law=law,
        query="المادة 5 من النظام القديم",
    )

    # One flat cap on rrf: o1 (0.95) and n1 (0.9) beat everything else.
    assert _ids(result) == ["o1", "n1"]


def test_flagged_old_chunk_stays_in_the_ordinary_pool(monkeypatch, engine, law):
    """Not eligible, so no extra slot: it competes exactly as it does today."""
    result = _rerank(
        monkeypatch, engine, _OLD_CHUNKS + _NEW_CHUNKS, law=law, today=_PERIOD_2_DAY,
    )

    assert _ids(result) == ["o1", "n1"]


def test_surviving_chunk_takes_an_extra_slot_after_period_1(monkeypatch, engine, law):
    surviving = _chunk("s1", _OLD_ID, chunk_ref=_SURVIVING_REF, rrf=0.05)

    result = _rerank(
        monkeypatch, engine, [surviving, *_NEW_CHUNKS], law=law, today=_PERIOD_2_DAY,
    )

    assert _ids(result) == ["n1", "n2", "s1"]


def test_the_sub_query_reaches_the_rendered_block(monkeypatch, engine, law):
    """Period 2: the same old chunk, flagged or clean, by the sub-query alone."""
    old_title, _new_title = law
    chunk = _chunk("o1", _OLD_ID)
    plain: list[dict] = []
    named: list[dict] = []

    _rerank(
        monkeypatch, engine, [chunk], law=law, today=_PERIOD_2_DAY, round_trace=plain,
    )
    _rerank(
        monkeypatch, engine, [chunk], law=law, today=_PERIOD_2_DAY, round_trace=named,
        query="المادة 5 من النظام القديم",
    )

    name_line = f"**النظام:** {old_title}\n"
    assert _STATUS_LINE in plain[0]["user_msg"]
    assert name_line in plain[0]["user_msg"]
    assert _STATUS_LINE not in named[0]["user_msg"]
    assert name_line not in named[0]["user_msg"]


def test_the_gate_and_the_cap_agree_on_an_old_chunk(law):
    """The block and the slot decide from the same chunk, or a clean chunk could
    be capped as ordinary, or a flagged one given a slot."""
    supersessions = SupersessionMap([_pair(*law)])

    assert old_chunk_eligible(
        supersessions, {"id": _OLD_ID}, "any_ref", "سؤال", _PERIOD_1_DAY
    )
    assert not old_chunk_eligible(
        supersessions, {"id": _NEW_ID}, "any_ref", "سؤال", _PERIOD_1_DAY
    )
    assert not old_chunk_eligible(
        supersessions, {"id": None}, None, "سؤال", _PERIOD_1_DAY
    )


# -- The provisions travel with the chunk ---------------------------------------

_NOTE = "المرسوم الملكي (م/1)\nثانياً: يسري النظام على …"
_PROVISIONS_LINE = f"**الأحكام الانتقالية:** {_NOTE}"
_UPCOMING_DAY = date(2031, 1, 1)


def _noted_pair(old_title: str, new_title: str) -> Pair:
    return Pair(
        old_id=_OLD_ID,
        old_ref="ref-old",
        old_title=old_title,
        superseded_on=date(2031, 3, 1),
        period_1_end=date(2031, 6, 1),
        period_2_end=date(2032, 3, 1),
        successors=(Successor(title=new_title, reg_ref="ref-new", reg_id=_NEW_ID, note=_NOTE),),
    )


def _noted_gate(law, today: date = _PERIOD_1_DAY) -> dict:
    return {
        "supersessions": SupersessionMap([_noted_pair(*law)]),
        "sub_query": "سؤال",
        "today": today,
    }


@pytest.mark.parametrize("mode", ["simple", "precise"])
@pytest.mark.parametrize("reg_id", [_OLD_ID, _NEW_ID], ids=["old", "new"])
def test_a_chunk_of_a_pair_reaches_the_decider_with_the_provisions(law, reg_id, mode):
    supabase = _Supabase({_OLD_ID: _reg_row(law[0]), _NEW_ID: _reg_row(law[1])})

    block = _block(supabase, _chunk("c1", reg_id, mode=mode), **_noted_gate(law))

    assert _PROVISIONS_LINE in block["markdown"]
    assert block["markdown"].index(_PROVISIONS_LINE) < block["markdown"].index("**نطاق النظام:**")


def test_no_provisions_for_the_decider_while_the_old_law_simply_applies(law):
    supabase = _Supabase({_OLD_ID: _reg_row(law[0])})

    block = _block(supabase, _chunk("c1", _OLD_ID), **_noted_gate(law, _UPCOMING_DAY))

    assert "الأحكام الانتقالية" not in block["markdown"]


def test_after_the_periods_a_new_law_chunk_is_plain_law_again(law):
    supabase = _Supabase({_NEW_ID: _reg_row(law[1])})

    block = _block(supabase, _chunk("c1", _NEW_ID), **_noted_gate(law, _AFTER_DAY))

    assert block["markdown"] == _expected_block("simple", law[1])


def test_the_provisions_do_not_reveal_or_flag_an_eligible_old_chunk(law):
    """It must still arrive looking like in-force law, with the text beside it."""
    supabase = _Supabase({_OLD_ID: _reg_row(law[0], repealed=True)})

    block = _block(supabase, _chunk("c1", _OLD_ID), **_noted_gate(law))

    assert _STATUS_LINE not in block["markdown"]
    assert REPEALED_LABEL_AR not in block["markdown"]
    assert _PROVISIONS_LINE in block["markdown"]
