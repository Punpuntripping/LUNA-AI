"""Official judicial principles on the reg side (official_principles_refs.md §3.1, §3.3).

Covers the flag-gated ``search_topics`` side-call (params, gate slot, isolation
from its own failures, the sector retry staying keyed on the main rows), the
one-pool merge and TOP_K cut, the ``official_principles`` content fetch, the
reranker block (D4: rule text + الباب, nothing else), the block / result /
drop-row dispatch, the decider clause, and the prompt wording.

Sync on purpose (``asyncio.run``): CI has no ``pytest-asyncio``. No live RPC,
DB or LLM call anywhere — the package's ``MockSupabase`` and fakes only.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from agents.deep_search_v4.reg_compliance_search import decider_rules
from agents.deep_search_v4.reg_compliance_search import reranker as reranker_mod
from agents.deep_search_v4.reg_compliance_search import search as search_mod
from agents.deep_search_v4.reg_compliance_search.loop import _render_chunks_summary
from agents.deep_search_v4.reg_compliance_search.models import (
    RegKeep,
    RegRerankerClassification,
    RerankedResult,
)
from agents.deep_search_v4.reg_compliance_search.prompts import (
    get_expander_prompt,
    get_reranker_prompt,
)
from agents.deep_search_v4.reg_compliance_search.reranker import (
    _assemble_result,
    _dropped_row,
    _make_block,
    run_reranker_for_query,
)
from agents.deep_search_v4.reg_compliance_search.search import (
    PRECISE_BAND,
    PRINCIPLE_PER_QUERY,
    PRINCIPLE_SELECT,
    TOP_K,
    search_regulations_pipeline,
)
from agents.deep_search_v4.reg_compliance_search.unfold_reranker import (
    MAX_PRINCIPLE_TEXT_CHARS,
    _format_principle_block,
    principle_title,
)
from agents.deep_search_v4.shared import decider as decider_mod
from agents.deep_search_v4.shared.decider import DeciderReply
from shared.library.supersession import SupersessionMap

from .conftest import (
    MockSupabase,
    chunk_content,
    make_deps,
    principle_content,
    principles_flag,
    regulation_meta_row,
    rpc_row,
)

EMB = [0.0] * 1024
MAIN_RPC = "search_topics_per_chunk"
SIDE_RPC = "search_topics"


@pytest.fixture
def flag_on(monkeypatch) -> None:
    monkeypatch.setattr(search_mod, "get_settings", lambda: principles_flag(True))


def principle_rpc_row(source_id: str, score: float, **over) -> dict:
    """A ``search_topics(p_types => ['official_principle'])`` row."""
    return rpc_row(
        "official_principle", source_id, score,
        title="المبدأ رقم 12 — الوعد", sectors=["CIV"], entity_ref="18329",
        **over,
    )


def _run(supa: MockSupabase, mock_embedding_fn, **kw):
    deps = make_deps(supa, mock_embedding_fn)
    outcome = asyncio.run(
        search_regulations_pipeline(query="q", deps=deps, precomputed_embedding=EMB, **kw)
    )
    return outcome, deps


def _split_handler(main: list[dict], principles: list[dict]):
    """RPC handler: the per-chunk RPC answers ``main``, the side-call ``principles``."""
    def handler(name, _params):
        return list(principles) if name == SIDE_RPC else list(main)
    return handler


# -- Constants ------------------------------------------------------------------


def test_principle_constants_and_select():
    assert search_mod.PRINCIPLE_RPC == "search_topics"
    assert search_mod.PRINCIPLE_TYPE == "official_principle"
    assert PRINCIPLE_PER_QUERY == 10
    assert PRINCIPLE_SELECT == (
        "id, principle_ref, entity_ref, principle_no, chapter_no, chapter_title, "
        "text, citations, issuing_bodies, issuing_body_names, source"
    )
    cols = {c.strip() for c in PRINCIPLE_SELECT.split(",")}
    assert "*" not in PRINCIPLE_SELECT
    # The stored ``content`` carries Arabic-Indic digits and coded body names (D5).
    assert "content" not in cols


# -- Flag off: nothing changes --------------------------------------------------


@pytest.mark.parametrize("setting", [False, None], ids=["off", "missing"])
def test_flag_off_makes_no_side_call(mock_embedding_fn, monkeypatch, setting):
    """Off — or a Settings that has no such field yet — is today's pipeline."""
    monkeypatch.setattr(search_mod, "get_settings", lambda: principles_flag(setting))
    supa = MockSupabase(
        _split_handler([rpc_row("regulation", "c1", 0.9)], [principle_rpc_row("p1", 0.99)]),
        tables={"chunks_v2": [chunk_content("c1")],
                "official_principles": [principle_content("p1")]},
    )

    outcome, deps = _run(supa, mock_embedding_fn)

    assert [name for name, _ in supa.rpc_calls] == [MAIN_RPC]
    assert "official_principles" not in {e["table"] for e in supa.table_log}
    assert [r["id"] for r in outcome.rows] == ["c1"]
    texts = [e["text"] for e in deps._events]
    assert texts == [
        "جاري البحث في الأنظمة والتعاميم والخدمات: q...",
        "جاري البحث في قاعدة بيانات الأنظمة والتعاميم والخدمات...",
        "تم استرجاع 1 نتيجة من الأنظمة والتعاميم والخدمات.",
    ]


# -- Flag on: the side-call -----------------------------------------------------


def test_side_call_params_never_carry_sectors(mock_embedding_fn, flag_on):
    supa = MockSupabase(
        _split_handler([rpc_row("regulation", "c1", 0.9)], [principle_rpc_row("p1", 0.8)]),
        tables={"chunks_v2": [chunk_content("c1")],
                "official_principles": [principle_content("p1")]},
    )

    _run(supa, mock_embedding_fn, filter_sectors=["الصحة"])

    assert [name for name, _ in supa.rpc_calls] == [MAIN_RPC, SIDE_RPC]
    main_params = supa.rpc_calls[0][1]
    side_params = supa.rpc_calls[1][1]
    assert main_params["p_sectors"] == ["الصحة"]   # the main call is unchanged
    assert side_params == {
        "p_query_embedding": EMB,
        "p_types": ["official_principle"],
        "p_per_type": 10,
    }


def test_side_call_runs_inside_the_same_gate_slot(mock_embedding_fn, flag_on, monkeypatch):
    seq: list[str] = []

    @asynccontextmanager
    async def _spy() -> AsyncIterator[float]:
        seq.append("enter")
        yield 7.0
        seq.append("exit")

    monkeypatch.setattr(search_mod, "search_gate", _spy)

    def handler(name, _params):
        seq.append(name)
        if name == SIDE_RPC:
            return [principle_rpc_row("p1", 0.8)]
        return [rpc_row("regulation", "c1", 0.9)]

    supa = MockSupabase(handler, tables={
        "chunks_v2": [chunk_content("c1")],
        "official_principles": [principle_content("p1")],
    })

    outcome, _ = _run(supa, mock_embedding_fn)

    assert seq == ["enter", MAIN_RPC, SIDE_RPC, "exit"]
    assert outcome.gate_wait_ms == pytest.approx(7.0)
    assert outcome.count == 2


def test_side_call_failure_is_isolated(mock_embedding_fn, flag_on, caplog):
    def handler(name, _params):
        if name == SIDE_RPC:
            raise httpx.ReadTimeout("principles down")
        return [rpc_row("regulation", "c1", 0.9)]

    supa = MockSupabase(handler, tables={"chunks_v2": [chunk_content("c1")]})

    outcome, _ = _run(supa, mock_embedding_fn)

    assert outcome.error is None
    assert [r["id"] for r in outcome.rows] == ["c1"]
    assert "principle side-call failed" in caplog.text


def test_principle_fetch_failure_is_isolated(mock_embedding_fn, flag_on):
    class _NoPrinciplesTable(MockSupabase):
        def table(self, name):
            if name == "official_principles":
                raise RuntimeError("table down")
            return super().table(name)

    supa = _NoPrinciplesTable(
        _split_handler([rpc_row("regulation", "c1", 0.9)], [principle_rpc_row("p1", 0.95)]),
        tables={"chunks_v2": [chunk_content("c1")]},
    )

    outcome, _ = _run(supa, mock_embedding_fn)

    assert outcome.error is None
    assert [r["id"] for r in outcome.rows] == ["c1"]
    # The principle still counted in the score head — it WAS retrieved.
    assert outcome.max_score == pytest.approx(0.95)


def test_main_rpc_failure_skips_the_side_call(mock_embedding_fn, flag_on):
    def handler(name, _params):
        if name == MAIN_RPC:
            raise RuntimeError("db down")
        return [principle_rpc_row("p1", 0.8)]

    supa = MockSupabase(handler, tables={})

    outcome, deps = _run(supa, mock_embedding_fn)

    assert outcome.error == "RuntimeError"
    assert outcome.rows == []
    assert [name for name, _ in supa.rpc_calls] == [MAIN_RPC]
    assert deps._events[-1]["text"] == (
        "حدث خطأ أثناء البحث في الأنظمة والتعاميم والخدمات والمبادئ القضائية."
    )


def test_status_lines_name_the_principles(mock_embedding_fn, flag_on):
    supa = MockSupabase(
        _split_handler([rpc_row("regulation", "c1", 0.9)], [principle_rpc_row("p1", 0.8)]),
        tables={"chunks_v2": [chunk_content("c1")],
                "official_principles": [principle_content("p1")]},
    )

    _, deps = _run(supa, mock_embedding_fn)

    texts = [e["text"] for e in deps._events]
    assert texts == [
        "جاري البحث في الأنظمة والتعاميم والخدمات والمبادئ القضائية: q...",
        "جاري البحث في قاعدة بيانات الأنظمة والتعاميم والخدمات والمبادئ القضائية...",
        "تم استرجاع 2 نتيجة من الأنظمة والتعاميم والخدمات والمبادئ القضائية.",
    ]


# -- The sector retry stays keyed on the MAIN rows -------------------------------


def test_zero_main_rows_with_sectors_still_retries(mock_embedding_fn, flag_on):
    """Principles found, the filtered main call found nothing → retry unfiltered,
    and the side-call is not repeated (it never took a filter)."""
    def handler(name, params):
        if name == SIDE_RPC:
            return [principle_rpc_row("p1", 0.8)]
        if "p_sectors" in params:
            return []
        return [rpc_row("regulation", "c1", 0.9)]

    supa = MockSupabase(handler, tables={
        "chunks_v2": [chunk_content("c1")],
        "official_principles": [principle_content("p1")],
    })

    outcome, deps = _run(supa, mock_embedding_fn, filter_sectors=["الصحة"])

    assert [name for name, _ in supa.rpc_calls] == [MAIN_RPC, SIDE_RPC, MAIN_RPC]
    assert "p_sectors" not in supa.rpc_calls[2][1]
    assert {r["id"] for r in outcome.rows} == {"c1", "p1"}
    assert any("بدون تصفية" in e["text"] for e in deps._events)


def test_main_rows_found_means_no_retry_even_without_principles(mock_embedding_fn, flag_on):
    supa = MockSupabase(
        _split_handler([rpc_row("regulation", "c1", 0.9)], []),
        tables={"chunks_v2": [chunk_content("c1")]},
    )

    outcome, _ = _run(supa, mock_embedding_fn, filter_sectors=["الصحة"])

    assert [name for name, _ in supa.rpc_calls] == [MAIN_RPC, SIDE_RPC]
    assert outcome.count == 1


def test_principles_alone_are_results(mock_embedding_fn, flag_on):
    """An honest-empty main pool does not throw away what the side-call found."""
    supa = MockSupabase(
        _split_handler([], [principle_rpc_row("p1", 0.7)]),
        tables={"official_principles": [principle_content("p1")]},
    )

    outcome, _ = _run(supa, mock_embedding_fn)

    assert outcome.error is None
    assert [r["id"] for r in outcome.rows] == ["p1"]


# -- One pool, one TOP_K cut -----------------------------------------------------


def test_principles_compete_in_the_top_k_cut(mock_embedding_fn, flag_on):
    main = [rpc_row("regulation", f"chunk-{i}", 0.90 - i * 0.01) for i in range(22)]
    principles = [
        principle_rpc_row(f"p-{i}", s)
        for i, s in enumerate([0.95, 0.80, 0.75, 0.50, 0.40])
    ]
    supa = MockSupabase(
        _split_handler(main, principles),
        tables={
            "chunks_v2": [chunk_content(f"chunk-{i}") for i in range(22)],
            "official_principles": [principle_content(f"p-{i}") for i in range(5)],
        },
    )

    outcome, _ = _run(supa, mock_embedding_fn)

    assert outcome.count == TOP_K == 25
    ids = [r["id"] for r in outcome.rows]
    # The two weakest rows of the pool are both principles: they are the cut.
    assert {"p-0", "p-1", "p-2"} <= set(ids)
    assert "p-3" not in ids and "p-4" not in ids
    assert ids[0] == "p-0"                     # sorted by score, principles included
    rrfs = [r["_rrf"] for r in outcome.rows]
    assert rrfs == sorted(rrfs, reverse=True)
    assert outcome.max_score == pytest.approx(0.95)


def test_a_principle_never_takes_a_precise_slot(mock_embedding_fn, flag_on):
    main = [rpc_row("regulation", f"chunk-{i}", 0.80 - i * 0.01) for i in range(7)]
    supa = MockSupabase(
        _split_handler(main, [principle_rpc_row("p-top", 0.99)]),
        tables={
            "chunks_v2": [chunk_content(f"chunk-{i}") for i in range(7)],
            "official_principles": [principle_content("p-top")],
        },
    )

    outcome, _ = _run(supa, mock_embedding_fn)

    by_id = {r["id"]: r for r in outcome.rows}
    assert by_id["p-top"]["_mode"] == "flat"
    for i in range(PRECISE_BAND):
        assert by_id[f"chunk-{i}"]["_mode"] == "precise"
    assert by_id[f"chunk-{PRECISE_BAND}"]["_mode"] == "simple"


def test_side_call_rows_of_another_type_are_ignored(mock_embedding_fn, flag_on):
    """The side-call is typed: nothing else can ride in on it."""
    supa = MockSupabase(
        _split_handler(
            [rpc_row("regulation", "c1", 0.5)],
            [rpc_row("regulation", "stray", 0.99), principle_rpc_row("p1", 0.6)],
        ),
        tables={
            "chunks_v2": [chunk_content("c1"), chunk_content("stray")],
            "official_principles": [principle_content("p1")],
        },
    )

    outcome, _ = _run(supa, mock_embedding_fn)

    assert {r["id"] for r in outcome.rows} == {"c1", "p1"}


# -- Content fetch + provenance --------------------------------------------------


def test_principle_content_fetch_and_provenance(mock_embedding_fn, flag_on):
    supa = MockSupabase(
        _split_handler(
            [rpc_row("regulation", "c1", 0.9)],
            [principle_rpc_row("p1", 0.8, doc_ref="18329", doc_id="doc-p1")],
        ),
        tables={"chunks_v2": [chunk_content("c1")],
                "official_principles": [principle_content("p1")]},
    )

    outcome, _ = _run(supa, mock_embedding_fn)

    log = [e for e in supa.table_log if e["table"] == "official_principles"]
    assert len(log) == 1
    assert log[0]["cols"] == PRINCIPLE_SELECT
    assert log[0]["in"] == ("id", ["p1"])

    row = next(r for r in outcome.rows if r["id"] == "p1")
    assert row["source_type"] == "official_principle"
    assert row["_mode"] == "flat"
    assert row["_rrf"] == pytest.approx(0.8)
    assert row["topic_title"] == "المبدأ رقم 12 — الوعد"
    assert row["doc_ref"] == "18329"
    assert row["doc_id"] == "doc-p1"
    assert row["entity_ref"] == "18329"
    assert row["sectors"] == ["CIV"]
    # The content columns ride along untouched.
    assert row["chapter_title"] == "الوعد"
    assert row["issuing_body_names"] == ["المحكمة العليا"]
    assert row["citations"][0]["decision_no"] == "123"


def test_no_principle_ids_means_no_principles_fetch(mock_embedding_fn, flag_on):
    supa = MockSupabase(
        _split_handler([rpc_row("regulation", "c1", 0.9)], []),
        tables={"chunks_v2": [chunk_content("c1")]},
    )

    _run(supa, mock_embedding_fn)

    assert "official_principles" not in {e["table"] for e in supa.table_log}


# -- Reranker block (D4) ---------------------------------------------------------


def test_principle_block_is_exactly_chapter_and_text():
    row = principle_content("p1", text="الوعد ملزم للواعد قضاءً.")

    assert _format_principle_block(row, "C3") == (
        "### [C3] مبدأ قضائي رسمي\n"
        "**الباب:** الوعد\n"
        "\n"
        "الوعد ملزم للواعد قضاءً."
    )


@pytest.mark.parametrize("chapter", [None, "", "   "])
def test_principle_block_omits_an_empty_chapter(chapter):
    row = principle_content("p1", chapter_title=chapter, text="نص القاعدة.")

    assert _format_principle_block(row, "C1") == (
        "### [C1] مبدأ قضائي رسمي\n\nنص القاعدة."
    )


def test_principle_block_shows_no_number_decision_or_score():
    row = {**principle_content("p1", text="نص القاعدة."), "_rrf": 0.8765, "topic_title": "المبدأ رقم 12 — الوعد"}

    block = _format_principle_block(row, "C1")

    for hidden in ("12", "123", "1435", "المحكمة العليا", "ك ع", "درجة الصلة", "0.8765", "المبدأ رقم"):
        assert hidden not in block


def test_principle_block_truncates_a_runaway_text():
    row = principle_content("p1", text="ق" * (MAX_PRINCIPLE_TEXT_CHARS + 50))

    block = _format_principle_block(row, "C1")

    body = block.split("\n\n", 1)[1]
    assert body == "ق" * MAX_PRINCIPLE_TEXT_CHARS + "..."


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"topic_title": "المبدأ رقم 7 — الوقف"}, "المبدأ رقم 7 — الوقف"),
        ({"topic_title": "المبدأ رقم 7 — "}, "المبدأ رقم 7"),       # NULL chapter in the RPC
        ({"principle_no": 7, "chapter_title": "الوقف"}, "المبدأ رقم 7 — الوقف"),
        ({"principle_no": 7}, "المبدأ رقم 7"),
        ({}, "مبدأ قضائي"),
    ],
)
def test_principle_title(row, expected):
    assert principle_title(row) == expected


# -- Reranker dispatch -----------------------------------------------------------


def _principle_row(**over) -> dict:
    return {
        **principle_content("p1"),
        "source_type": "official_principle",
        "_mode": "flat",
        "_rrf": 0.81,
        "topic_title": "المبدأ رقم 12 — الوعد",
        "doc_ref": "18329",
        "doc_id": "doc-p1",
        "sectors": ["CIV"],
        **over,
    }


def test_make_block_dispatches_a_principle_flat_with_no_db_hop():
    supa = MockSupabase()
    row = _principle_row()

    block = _make_block(supa, row, "C4", "precise", 0.81)

    assert block["source_type"] == "official_principle"
    assert block["mode"] == "flat"             # never precise, whatever it is asked
    assert block["unfolded"] is None
    assert block["rrf"] == pytest.approx(0.81)
    assert block["chunk"] is row
    assert block["markdown"] == _format_principle_block(row, "C4")
    assert supa.table_log == [] and supa.rpc_calls == []


@pytest.mark.parametrize(("given", "kept"), [("high", "high"), ("medium", "medium"), (None, "medium")])
def test_assemble_result_for_a_principle(given, kept):
    row = _principle_row()
    block = _make_block(MockSupabase(), row, "C1", "flat", 0.81)

    result = _assemble_result(block, {"relevance": given, "reasoning": "موضوع القاعدة هو الوعد"})

    assert isinstance(result, RerankedResult)
    assert result.source_type == "official_principle"
    assert result.title == "المبدأ رقم 12 — الوعد"
    assert result.content == row["text"]
    assert result.relevance == kept
    assert result.reasoning == "موضوع القاعدة هو الوعد"
    assert result.db_id == "p1"
    assert result.rrf == pytest.approx(0.81)
    assert result.regulation_title == ""
    assert result.source_row == row and result.source_row is not row


def test_assemble_result_title_falls_back_without_the_rpc_title():
    row = _principle_row(topic_title="")
    block = _make_block(MockSupabase(), row, "C1", "flat", 0.5)

    assert _assemble_result(block, {"relevance": "medium"}).title == "المبدأ رقم 12 — الوعد"


def test_dropped_row_for_a_principle():
    block = _make_block(MockSupabase(), _principle_row(), "C1", "flat", 0.5)

    assert _dropped_row(block, "", "llm") == {
        "db_id": "p1",
        "title": "المبدأ رقم 12 — الوعد",
        "reasoning": "",
        "drop_reason": "llm",
        "source_type": "official_principle",
    }


# -- Through the reranker, on either engine ---------------------------------------


class _Usage:
    requests = 1
    input_tokens = 10
    output_tokens = 5
    total_tokens = 15
    details: dict = {}


class _Run:
    def __init__(self, output) -> None:
        self.output = output

    def usage(self) -> _Usage:
        return _Usage()


class _Agent:
    def __init__(self, output) -> None:
        self._output = output

    async def run(self, _msg, **_kw) -> _Run:
        return _Run(self._output)


def _chunk_row(chunk_id: str, rrf: float) -> dict:
    return {
        **chunk_content(chunk_id, regulation_id="reg-1"),
        "source_type": "regulation",
        "_mode": "simple",
        "_rrf": rrf,
    }


@pytest.mark.parametrize("engine", ["llm", "decider"])
def test_a_principle_is_kept_through_the_reranker(monkeypatch, engine):
    seen_questions: dict = {}
    settings = SimpleNamespace(
        REG_RERANKER_ENGINE=engine,
        REG_RERANKER_DECIDERS="liquid/d1-ultra",
        OPENROUTER_RERANKER_API_KEY="sk-or-test",
    )

    async def _fake_decide(_state, questions, *, required, models):
        seen_questions.update(questions)
        return DeciderReply(
            model=models[0], model_served=models[0],
            answers={label: {"score": 3.0} for label in questions},
            usage_entry={"agent": "reranker", "model": models[0]},
            fallback_from=[],
        )

    def _fake_agent(**_kw) -> _Agent:
        return _Agent(RegRerankerClassification(
            sufficient=True,
            keeps=[
                RegKeep(label="C1", relevance="medium", reasoning="نطاق النظام عام"),
                RegKeep(label="C2", relevance="high", reasoning="موضوع القاعدة هو الوعد"),
            ],
            summary_note="",
        ))

    async def _no_http(*_a, **_kw):
        raise AssertionError("unexpected HTTP call")

    monkeypatch.setattr(reranker_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(reranker_mod, "load_supersessions", lambda _s: SupersessionMap())
    monkeypatch.setattr(decider_mod, "decide", _fake_decide)
    monkeypatch.setattr(reranker_mod, "create_reranker_agent", _fake_agent)
    monkeypatch.setattr(httpx.AsyncClient, "send", _no_http)

    supa = MockSupabase(tables={"regulations_v2": [regulation_meta_row("reg-1")]})
    trace: list[dict] = []
    result, _usage, _log = asyncio.run(run_reranker_for_query(
        query="هل الوعد ملزم قضاءً؟", rationale="ت",
        chunks=[_chunk_row("c1", 0.7), _principle_row()],
        supabase=supa, max_keep=8, engine=engine, round_trace=trace,
    ))

    by_id = {r.db_id: r for r in result.results}
    assert set(by_id) == {"c1", "p1"}
    principle = by_id["p1"]
    assert principle.source_type == "official_principle"
    assert principle.title == "المبدأ رقم 12 — الوعد"
    assert principle.content == _principle_row()["text"]
    # The principle reached the model as the D4 block, labelled in render order.
    assert _format_principle_block(_principle_row(), "C2") in trace[0]["user_msg"]

    if engine == "decider":
        clause = decider_rules.PRINCIPLE_CLAUSE
        assert clause in seen_questions["C2"]["instructions"]
        assert clause not in seen_questions["C1"]["instructions"]


# -- Decider clause ----------------------------------------------------------------


def test_decider_questions_without_principles_are_the_tested_shape():
    questions, required = decider_rules.build_questions(["C1", "C2"])
    explicit, _ = decider_rules.build_questions(["C1", "C2"], frozenset())

    assert questions == explicit
    assert required == ["C1", "C2"]
    for lab in ("C1", "C2"):
        assert questions[lab]["instructions"] == (
            f"{decider_rules.RUBRIC5}\n\nRate ONLY the candidate whose header is `### [{lab}]`."
        )


def test_decider_principle_question_carries_the_clause():
    questions, required = decider_rules.build_questions(["C1", "C2", "C3"], {"C2"})

    assert required == ["C1", "C2", "C3"]
    assert questions["C2"]["instructions"] == (
        f"{decider_rules.RUBRIC5}\n\n{decider_rules.PRINCIPLE_CLAUSE}"
        "\n\nRate ONLY the candidate whose header is `### [C2]`."
    )
    assert questions["C2"]["criteria"] == decider_rules.LEVELS5
    assert decider_rules.PRINCIPLE_CLAUSE not in questions["C1"]["instructions"]
    assert decider_rules.PRINCIPLE_CLAUSE not in questions["C3"]["instructions"]
    assert "SUBJECT" in decider_rules.PRINCIPLE_CLAUSE
    assert "مبدأ قضائي رسمي" in decider_rules.PRINCIPLE_CLAUSE


# -- Prompts -------------------------------------------------------------------------


def test_reranker_prompt_teaches_kind_4():
    prompt = get_reranker_prompt("prompt_1")
    header = _format_principle_block(principle_content("p1"), "Cn").split("\n", 1)[0]

    assert "**four candidate kinds**" in prompt
    assert "### Kind 4 — مبدأ قضائي رسمي (an official judicial principle)" in prompt
    # The header the prompt describes is the one the code renders.
    assert f"`{header}`" in prompt
    assert "official judicial principles (مبادئ قضائية رسمية" in prompt
    assert "### For a مبدأ قضائي: the subject check" in prompt
    assert "or from the rule's **subject** (for a principle) — never from a sector tag." in prompt
    assert "For a **مبدأ قضائي**: state the **subject verdict**" in prompt
    # Section order: Kind 4 after Kind 3, the subject check right after the entity check.
    assert prompt.index("### Kind 3") < prompt.index("### Kind 4") < prompt.index(
        "**No candidate block carries a sector list.**"
    )
    assert prompt.index("the entity check") < prompt.index("the subject check") < prompt.index(
        "## Your task: KEEP-ONLY"
    )


def test_expander_prompt_lists_the_principles():
    prompt = get_expander_prompt("prompt_1")

    assert "five kinds of source" in prompt
    assert "- **المبادئ القضائية الرسمية** — official judicial principles" in prompt
    assert prompt.count("نظام / لائحة / تعميم / خدمة / مبدأ") == 2
    assert prompt.count("«مبدأ قضائي في كذا»") == 2


# -- Loop digest ----------------------------------------------------------------------


def test_loop_digest_renders_a_principle_row():
    md = _render_chunks_summary([_principle_row()])

    assert "### 1. [official_principle] المبدأ رقم 12 — الوعد" in md
    assert "- ref: `18329_p_p1`" in md
    assert "- mode: `flat` | rrf: 0.8100" in md
    assert _principle_row()["text"] in md
