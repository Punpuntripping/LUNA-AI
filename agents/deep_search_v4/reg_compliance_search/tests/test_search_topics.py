"""Search-layer tests for the unified ``search_topics`` pipeline (§2).

Covers: the merge/top-15 one-pool cut (D1/D8), the sector RPC param contract
(None vs non-empty list vs the ``[]``-guard vs the future path), the 0-rows
retry-unfiltered, the precise/simple/flat banding (D12), the concurrent
per-type ``_fetch_by_ids`` dispatch, and the merged RPC provenance carried
forward onto each row.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from agents.deep_search_v4.reg_compliance_search import search as search_mod
from agents.deep_search_v4.reg_compliance_search.search import (
    CHUNK_SELECT_TOPICS,
    CIRCULAR_SELECT,
    PRECISE_BAND,
    SERVICE_SELECT,
    TOP_K,
    search_regulations_pipeline,
)

from .conftest import (
    MockSupabase,
    chunk_content,
    circular_content,
    make_deps,
    rpc_row,
    service_content,
)

EMB = [0.0] * 1024


# -- Constants / regression guards --------------------------------------------


def test_legacy_symbols_removed():
    """The legacy title-search knobs + helpers are gone (§2 DELETE list)."""
    assert not hasattr(search_mod, "_search_chunk_titles")
    assert not hasattr(search_mod, "_filter_by_sectors")
    assert not hasattr(search_mod, "MATCH_COUNT")
    assert not hasattr(search_mod, "EF_SEARCH")
    assert not hasattr(search_mod, "PER_DOC")
    assert search_mod.PER_TYPE == 25
    assert search_mod.TOP_K == 25
    assert search_mod.TOPICS_RPC == "search_topics_per_chunk"


def test_selects_never_star():
    """Payload trims — never ``select("*")`` (would drag embeddings / fts)."""
    for sel in (CHUNK_SELECT_TOPICS, CIRCULAR_SELECT, SERVICE_SELECT):
        assert "*" not in sel
    assert "corpus" in CHUNK_SELECT_TOPICS  # D13
    assert "embedding" not in SERVICE_SELECT
    assert "original_markdown" not in SERVICE_SELECT
    assert "entities!circulars_entity_id_fkey(entity_name)" in CIRCULAR_SELECT


# -- Merge / top-15 -----------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_top15_one_pool(mock_embedding_fn):
    """18 rows across types → sorted by score DESC, global top-15 kept (D1)."""
    # 10 chunks + 4 circulars + 4 services, strictly descending scores.
    rpc_rows = []
    chunks = []
    circs = []
    svcs = []
    score = 0.99
    for i in range(18):
        sid = f"chunk-{i}"
        rpc_rows.append(rpc_row("regulation", sid, score))
        chunks.append(chunk_content(sid))
        score -= 0.03
    for i in range(6):
        sid = f"circ-{i}"
        rpc_rows.append(rpc_row("circular", sid, score))
        circs.append(circular_content(sid))
        score -= 0.03
    for i in range(4):
        sid = f"svc-{i}"
        rpc_rows.append(rpc_row("service", sid, score))
        svcs.append(service_content(sid))
        score -= 0.03

    supa = MockSupabase(
        rpc_handler=lambda n, p: list(rpc_rows),
        tables={"chunks_v2": chunks, "circulars": circs, "services": svcs},
    )
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
    )
    rows, count = outcome.rows, outcome.count

    assert count == TOP_K == 25
    assert len(rows) == 25
    # Sorted by _rrf (= RPC score) descending.
    rrfs = [r["_rrf"] for r in rows]
    assert rrfs == sorted(rrfs, reverse=True)
    # The 3 lowest-scoring (last services) were cut.
    kept_ids = {r["id"] for r in rows}
    assert "svc-3" not in kept_ids  # lowest score dropped


@pytest.mark.asyncio
async def test_source_id_dedup_guard(mock_embedding_fn):
    """A duplicate source_id in the RPC output is collapsed to one row."""
    rpc_rows = [
        rpc_row("regulation", "dup", 0.9),
        rpc_row("regulation", "dup", 0.8),  # same source_id
        rpc_row("regulation", "other", 0.7),
    ]
    supa = MockSupabase(
        rpc_handler=lambda n, p: list(rpc_rows),
        tables={"chunks_v2": [chunk_content("dup"), chunk_content("other")]},
    )
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
    )
    rows, count = outcome.rows, outcome.count
    ids = [r["id"] for r in rows]
    assert ids.count("dup") == 1
    assert count == 2


# -- Precise / simple / flat banding (D12) ------------------------------------


@pytest.mark.asyncio
async def test_banding_precise_top5_chunks_only(mock_embedding_fn):
    """Precise for the top-5 CHUNK rows only; rest simple; circular/service flat."""
    rpc_rows = []
    chunks = []
    score = 0.99
    for i in range(6):  # 6 chunks → ranks 1-6
        sid = f"chunk-{i}"
        rpc_rows.append(rpc_row("regulation", sid, score))
        chunks.append(chunk_content(sid))
        score -= 0.03
    rpc_rows.append(rpc_row("circular", "circ-0", score)); score -= 0.03
    rpc_rows.append(rpc_row("service", "svc-0", score))

    supa = MockSupabase(
        rpc_handler=lambda n, p: list(rpc_rows),
        tables={
            "chunks_v2": chunks,
            "circulars": [circular_content("circ-0")],
            "services": [service_content("svc-0")],
        },
    )
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
    )
    rows, count = outcome.rows, outcome.count
    by_id = {r["id"]: r for r in rows}
    # First 5 chunks precise, 6th chunk simple.
    for i in range(PRECISE_BAND):
        assert by_id[f"chunk-{i}"]["_mode"] == "precise"
    assert by_id["chunk-5"]["_mode"] == "simple"
    # Circular + service are flat regardless of rank.
    assert by_id["circ-0"]["_mode"] == "flat"
    assert by_id["svc-0"]["_mode"] == "flat"


# -- Sector RPC param contract ------------------------------------------------


@pytest.mark.asyncio
async def test_sector_param_non_empty_list(mock_embedding_fn):
    """A non-empty sector list is forwarded as ``p_sectors``."""
    supa = MockSupabase(
        rpc_handler=lambda n, p: [rpc_row("regulation", "c1", 0.9)],
        tables={"chunks_v2": [chunk_content("c1")]},
    )
    deps = make_deps(supa, mock_embedding_fn)

    await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
        filter_sectors=["الصحة", "الدواء"],
    )
    assert len(supa.rpc_calls) == 1
    _name, params = supa.rpc_calls[0]
    assert params["p_sectors"] == ["الصحة", "الدواء"]
    assert _name == "search_topics_per_chunk"
    assert params["p_per_type"] == 25
    assert "p_per_doc" not in params
    assert "p_types" not in params


@pytest.mark.asyncio
async def test_sector_param_none_omits_p_sectors(mock_embedding_fn):
    """No sector filter → ``p_sectors`` is omitted entirely."""
    supa = MockSupabase(
        rpc_handler=lambda n, p: [rpc_row("regulation", "c1", 0.9)],
        tables={"chunks_v2": [chunk_content("c1")]},
    )
    deps = make_deps(supa, mock_embedding_fn)

    await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB, filter_sectors=None,
    )
    _name, params = supa.rpc_calls[0]
    assert "p_sectors" not in params


@pytest.mark.asyncio
async def test_sector_empty_list_guard(mock_embedding_fn):
    """``[]`` must NEVER reach the RPC (``sectors && '{}'`` is always false)."""
    supa = MockSupabase(
        rpc_handler=lambda n, p: [rpc_row("regulation", "c1", 0.9)],
        tables={"chunks_v2": [chunk_content("c1")]},
    )
    deps = make_deps(supa, mock_embedding_fn)

    await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB, filter_sectors=[],
    )
    assert len(supa.rpc_calls) == 1  # no retry triggered by the empty guard
    _name, params = supa.rpc_calls[0]
    assert "p_sectors" not in params


@pytest.mark.asyncio
async def test_future_sector_resolution(mock_embedding_fn):
    """A resolved ``sectors_future`` is applied at the RPC (compliance-style)."""
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    fut.set_result(["العمل"])

    supa = MockSupabase(
        rpc_handler=lambda n, p: [rpc_row("regulation", "c1", 0.9)],
        tables={"chunks_v2": [chunk_content("c1")]},
    )
    deps = make_deps(supa, mock_embedding_fn)

    await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
        filter_sectors=None, filter_sectors_future=fut,
    )
    _name, params = supa.rpc_calls[0]
    assert params["p_sectors"] == ["العمل"]


# -- Retry unfiltered on 0 rows + sectors -------------------------------------


@pytest.mark.asyncio
async def test_retry_unfiltered_on_zero_with_sectors(mock_embedding_fn):
    """0 rows WITH a sector filter → one unfiltered retry + Arabic SSE notice."""
    def handler(name, params):
        # Filtered call returns nothing; unfiltered call returns a row.
        if "p_sectors" in params:
            return []
        return [rpc_row("regulation", "c1", 0.9)]

    supa = MockSupabase(handler, tables={"chunks_v2": [chunk_content("c1")]})
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
        filter_sectors=["الصحة"],
    )
    rows, count = outcome.rows, outcome.count
    assert count == 1
    assert len(supa.rpc_calls) == 2
    assert "p_sectors" in supa.rpc_calls[0][1]
    assert "p_sectors" not in supa.rpc_calls[1][1]
    # The Arabic "بدون تصفية" notice was emitted (cli.py keys on it).
    texts = [e.get("text", "") for e in deps._events]
    assert any("بدون تصفية" in t for t in texts)


@pytest.mark.asyncio
async def test_no_retry_when_unfiltered_zero(mock_embedding_fn):
    """0 rows WITHOUT a sector filter → no retry, empty result."""
    supa = MockSupabase(lambda n, p: [], tables={})
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB, filter_sectors=None,
    )
    rows, count = outcome.rows, outcome.count
    assert (rows, count) == ([], 0)
    assert len(supa.rpc_calls) == 1


# -- Per-type content fetch dispatch ------------------------------------------


@pytest.mark.asyncio
async def test_fetch_by_ids_dispatch(mock_embedding_fn):
    """Each source type is fetched from its own table with the right columns."""
    rpc_rows = [
        rpc_row("regulation", "chunk-1", 0.9),
        rpc_row("appendix", "chunk-2", 0.85),
        rpc_row("circular", "circ-1", 0.8),
        rpc_row("service", "svc-1", 0.7),
    ]
    supa = MockSupabase(
        rpc_handler=lambda n, p: list(rpc_rows),
        tables={
            "chunks_v2": [chunk_content("chunk-1"), chunk_content("chunk-2", corpus="appendix")],
            "circulars": [circular_content("circ-1")],
            "services": [service_content("svc-1")],
        },
    )
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
    )
    rows, count = outcome.rows, outcome.count
    assert count == 4

    log_by_table = {entry["table"]: entry for entry in supa.table_log}
    # chunks_v2 fetched with the corpus-bearing select; both chunk ids.
    assert log_by_table["chunks_v2"]["cols"] == CHUNK_SELECT_TOPICS
    assert set(log_by_table["chunks_v2"]["in"][1]) == {"chunk-1", "chunk-2"}
    # circulars fetched with the entity-embed select.
    assert log_by_table["circulars"]["cols"] == CIRCULAR_SELECT
    assert log_by_table["circulars"]["in"][1] == ["circ-1"]
    # services fetched with the rich column trim.
    assert log_by_table["services"]["cols"] == SERVICE_SELECT
    assert log_by_table["services"]["in"][1] == ["svc-1"]


@pytest.mark.asyncio
async def test_provenance_and_types_attached(mock_embedding_fn):
    """Every row carries source_type, _rrf (=score), _mode + merged RPC fields."""
    rpc_rows = [
        rpc_row("regulation", "chunk-1", 0.9, sectors=["الصحة"], doc_ref="R-1"),
        rpc_row("circular", "circ-1", 0.8, entity_ref="17405", sectors=["الدواء"]),
        rpc_row("service", "svc-1", 0.7, sectors=[]),
    ]
    supa = MockSupabase(
        rpc_handler=lambda n, p: list(rpc_rows),
        tables={
            "chunks_v2": [chunk_content("chunk-1")],
            "circulars": [circular_content("circ-1")],
            "services": [service_content("svc-1")],
        },
    )
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
    )
    rows, count = outcome.rows, outcome.count
    by_id = {r["id"]: r for r in rows}

    chunk = by_id["chunk-1"]
    assert chunk["source_type"] == "regulation"
    assert chunk["_rrf"] == pytest.approx(0.9)
    assert chunk["sectors"] == ["الصحة"]   # from the RPC row, not re-fetched
    assert chunk["doc_ref"] == "R-1"
    assert chunk["topic_title"] == "عنوان chunk-1"

    circ = by_id["circ-1"]
    assert circ["source_type"] == "circular"
    assert circ["_mode"] == "flat"
    assert circ["entity_ref"] == "17405"
    assert circ["sectors"] == ["الدواء"]

    svc = by_id["svc-1"]
    assert svc["source_type"] == "service"
    assert svc["_rrf"] == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_missing_content_row_skipped(mock_embedding_fn):
    """An RPC hit whose content row is absent is skipped (never crashes)."""
    rpc_rows = [
        rpc_row("regulation", "present", 0.9),
        rpc_row("regulation", "missing", 0.8),  # no content row
    ]
    supa = MockSupabase(
        rpc_handler=lambda n, p: list(rpc_rows),
        tables={"chunks_v2": [chunk_content("present")]},
    )
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
    )
    rows, count = outcome.rows, outcome.count
    assert count == 1
    assert rows[0]["id"] == "present"


@pytest.mark.asyncio
async def test_rpc_exception_fails_soft(mock_embedding_fn):
    """An RPC exception degrades to empty rows — and SAYS it failed.

    Fail-soft is still the contract (``loop.py``'s ``asyncio.gather`` has no
    ``return_exceptions``, so raising would cost the other sub-queries their
    results). What changed on 2026-08-22 is that the failure is now typed:
    ``error`` names the exception class instead of the call being
    byte-identical to an honest empty.
    """
    def boom(name, params):
        raise RuntimeError("db down")

    supa = MockSupabase(boom, tables={})
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
    )
    assert (outcome.rows, outcome.count) == ([], 0)
    assert outcome.error == "RuntimeError"
    assert outcome.max_score == 0.0


@pytest.mark.asyncio
async def test_gate_timeout_is_a_typed_failure(mock_embedding_fn, monkeypatch):
    """A ``SearchGateTimeout`` is a retrieval failure, never a corpus absence.

    The gate is the one failure mode this change INTRODUCES; if it were
    laundered into ``([], 0)`` it would recreate the exact bug being fixed.
    """
    from agents.deep_search_v4.shared.db_gate import SearchGateTimeout

    @asynccontextmanager
    async def _wedged() -> AsyncIterator[float]:
        raise SearchGateTimeout("no slot")
        yield 0.0  # pragma: no cover - unreachable, keeps this a generator

    monkeypatch.setattr(search_mod, "search_gate", _wedged)

    supa = MockSupabase(lambda n, p: [rpc_row("regulation", "c1", 0.9)], tables={})
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
    )
    assert (outcome.rows, outcome.count) == ([], 0)
    assert outcome.error == "SearchGateTimeout"
    # Never reached the database.
    assert supa.rpc_calls == []


@pytest.mark.asyncio
async def test_honest_empty_carries_no_error(mock_embedding_fn):
    """0 rows from a HEALTHY RPC: empty, but ``error is None``.

    The other half of the distinction — this is the shape that genuinely means
    "the corpus has nothing", and it must stay separable from the failure
    shape above.
    """
    supa = MockSupabase(lambda n, p: [], tables={})
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
    )
    assert (outcome.rows, outcome.count) == ([], 0)
    assert outcome.error is None
    assert outcome.max_score == 0.0
    assert outcome.top_scores == []


# -- Score capture (the only mechanical relevance signal) ----------------------


@pytest.mark.asyncio
async def test_score_head_captured_before_topk_cut(mock_embedding_fn):
    """``max_score``/``top_scores`` come from the WHOLE pool, pre-cut.

    ``search_topics`` is k-NN with no threshold — it returns ``p_per_type``
    rows per type ranked by score whether or not anything is relevant, so row
    COUNT carries no relevance information and SCORE carries all of it.
    Measured against live prod 2026-08-22: random unrelated pairs p50 0.244 /
    p95 0.331 / max 0.378; on-topic material 0.55-0.79.
    """
    # 30 rows > TOP_K=25, so the tail is cut before the content fetch.
    rpc_rows = [
        rpc_row("regulation", f"chunk-{i}", 0.79 - i * 0.01) for i in range(30)
    ]
    supa = MockSupabase(
        rpc_handler=lambda n, p: list(rpc_rows),
        tables={"chunks_v2": [chunk_content(f"chunk-{i}") for i in range(30)]},
    )
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
    )
    assert outcome.count == TOP_K
    assert outcome.max_score == pytest.approx(0.79)
    assert outcome.top_scores == pytest.approx([0.79, 0.78, 0.77, 0.76, 0.75])
    assert outcome.error is None


@pytest.mark.asyncio
async def test_score_survives_when_every_content_row_is_missing(mock_embedding_fn):
    """RPC found strong candidates; Step 5 lost all of them → score persists.

    This is the case the old ``([], 0)`` tuple erased most damagingly: rows
    existed and scored 0.9, and the caller could not tell that from a corpus
    that has nothing. The fetched content rows never carry ``score`` (no
    SELECT lists it — it is computed by the RPC, not stored), so the capture
    has to happen at the sort or not at all.
    """
    rpc_rows = [rpc_row("regulation", "missing-1", 0.9), rpc_row("regulation", "missing-2", 0.8)]
    supa = MockSupabase(rpc_handler=lambda n, p: list(rpc_rows), tables={"chunks_v2": []})
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
    )
    assert (outcome.rows, outcome.count) == ([], 0)
    assert outcome.max_score == pytest.approx(0.9)
    assert outcome.error is None   # nothing failed — the join just missed


@pytest.mark.asyncio
async def test_rpc_runs_inside_the_search_gate(mock_embedding_fn, monkeypatch):
    """Every ``search_topics`` call queues on the process-wide gate.

    The per-phase ``asyncio.Semaphore`` in ``loop.py`` bounds this executor
    only; three executors fan out at once on one Postgres instance, which is
    why the gate has to sit here, around the RPC itself.
    """
    entered: list[str] = []

    @asynccontextmanager
    async def _spy() -> AsyncIterator[float]:
        entered.append("acquired")
        yield 12.5

    monkeypatch.setattr(search_mod, "search_gate", _spy)

    supa = MockSupabase(
        rpc_handler=lambda n, p: [rpc_row("regulation", "c1", 0.9)],
        tables={"chunks_v2": [chunk_content("c1")]},
    )
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
    )
    assert entered == ["acquired"]
    assert outcome.gate_wait_ms == pytest.approx(12.5)
    assert outcome.count == 1


@pytest.mark.asyncio
async def test_several_chunks_of_one_regulation_all_reach_the_reranker(mock_embedding_fn):
    """Same doc_id, different chunks: every one survives the merge.

    Two chunks of one law were the case the per-law rule dropped, so the merge
    must dedup by chunk and never by document.
    """
    rpc_rows = [
        {**rpc_row("regulation", f"chunk-{i}", 0.7 - i * 0.01), "doc_id": "same-law"}
        for i in range(4)
    ]
    supa = MockSupabase(
        rpc_handler=lambda n, p: list(rpc_rows),
        tables={"chunks_v2": [chunk_content(f"chunk-{i}") for i in range(4)]},
    )
    deps = make_deps(supa, mock_embedding_fn)

    outcome = await search_regulations_pipeline(
        query="q", deps=deps, precomputed_embedding=EMB,
    )

    assert outcome.count == 4
    assert {r["id"] for r in outcome.rows} == {f"chunk-{i}" for i in range(4)}
    assert {name for name, _ in supa.rpc_calls} == {"search_topics_per_chunk"}
