"""``run_extended_searches`` — N searches in parallel on their own deps (run_retrieval
mocked), whole-file documents, briefs, caps, distinct query ids, failure isolation,
the combined progress stream and the masking of what the searches read."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agents.deep_search_v4 import orchestrator as orch
from agents.deep_search_v4.extended import runner
from agents.deep_search_v4.extended.runner import (
    ERROR_NO_REFERENCES,
    WHOLE_FILE_PER_DOC_CAP,
    WHOLE_FILE_TOTAL_CAP,
    CombinedProgress,
    run_extended_searches,
    whole_file_items,
)
from backend.app.services import masking_service
from backend.app.services.masking_service import reset_active_codec, set_active_codec

from ._exec_fakes import (
    FAKE_ID,
    MARKED_D1,
    REAL_ID,
    attached_snapshot,
    fixed_codec,
    make_agg,
    make_plan,
    make_ref,
    make_request,
    make_view,
    planner_deps,
)


@pytest.fixture
def codec():
    c = fixed_codec()
    token = set_active_codec(c)
    try:
        yield c
    finally:
        reset_active_codec(token)


@pytest.fixture
def persisted(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(masking_service, "persist_new_mappings",
                        lambda sb, uid, codec: calls.append((sb, uid, codec)) or 0)
    return calls


# --------------------------------------------------------------------------- whole_file_items


def test_whole_file_items_encode_the_view_and_keep_attached_copies(codec):
    view = make_view()
    attached = [
        attached_snapshot("item-1", 3, content="نص مرمز قديم"),
        attached_snapshot("item-9", 5, content="مرفق آخر", doc_type="official_record"),
    ]
    items = whole_file_items(view, attached, {7: "item-2"})
    assert [s.item_id for s in items] == ["item-1", "item-2", "item-9"]
    d1, d2, extra = items
    # D1: the marked text (encoded), the attached copy's encoded summary/title + seq.
    assert d1.content_md == MARKED_D1.replace(REAL_ID, FAKE_ID)
    assert REAL_ID not in d1.content_md and "〔D1·S1" in d1.content_md
    assert (d1.wi_seq, d1.summary, d1.title) == (3, "ملخص مرمز", "عنوان مرمز")
    # D2: not attached this turn → the alias map's seq, the view's summary (encoded), no text.
    assert d2.wi_seq == 7 and d2.content_md == "" and d2.summary == "ملخص المذكرة"
    assert d2.metadata == {"doc_type": "memo"} and d2.kind == "attachment"
    # Another document of the turn travels too (D29), as it was.
    assert extra.content_md == "مرفق آخر" and extra.wi_seq == 5


def test_whole_file_items_without_any_seq_get_free_numbers_and_encode_summaries(codec):
    items = whole_file_items(make_view(), [], None)
    assert [s.wi_seq for s in items] == [1, 2]
    assert items[0].summary == "ملخص الصحيفة " + FAKE_ID


def test_whole_file_items_without_a_codec_are_the_view_text():
    items = whole_file_items(make_view(), [], {})
    assert items[0].content_md == MARKED_D1 and REAL_ID in items[0].content_md


# --------------------------------------------------------------------------- CombinedProgress


def _prog(stage: str, **data) -> dict:
    return {"type": "agent_progress", "stage": stage, "text": "x", "data": data}


def test_combined_progress_follows_the_least_advanced_search():
    out: list[dict] = []
    p = CombinedProgress(2, out.append)
    p.start()
    assert out[-1]["stage"] == "searching" and out[-1]["data"]["searches"] == {"total": 2, "done": 0}
    s1, s2 = p.sink(1), p.sink(2)
    s1(_prog("evaluating"))
    assert p.stage() == "searching"                           # search 2 still searching
    s2(_prog("evaluating"))
    assert out[-1]["stage"] == "evaluating"
    s1(_prog("searching", phase="reg_compliance", sources=4, queries=3))   # late phase-end counts
    s2(_prog("searching", phase="case", sources=6, queries=2))
    assert out[-1]["stage"] == "evaluating" and out[-1]["data"]["sources"] == 10
    s1(_prog("aggregating"))
    p.finish(1)
    assert out[-1]["stage"] == "evaluating" and out[-1]["data"]["searches"]["done"] == 1
    s2(_prog("writing"))                                      # never past aggregating from inside
    assert all(e["stage"] in ("searching", "evaluating", "aggregating") for e in out)
    s2(_prog("aggregating"))
    p.finish(2)
    p.all_done(merging=True)
    assert out[-1]["stage"] == "aggregating"
    assert out[-1]["text"] == runner._ALL_DONE_MERGE_TEXT_AR
    assert out[-1]["data"] == {"sources": 10, "searches": {"total": 2, "done": 2}}


def test_combined_progress_passes_status_lines_and_dedupes_repeats():
    out: list[dict] = []
    p = CombinedProgress(1, out.append)
    sink = p.sink(1)
    sink({"type": "status", "text": "جاري البحث في الأنظمة: سؤال"})
    sink(_prog("evaluating"))
    sink(_prog("evaluating"))                                # nothing changed → no event
    assert [e.get("type") for e in out] == ["status", "agent_progress"]
    sink("not a dict")                                       # ignored, never raises
    CombinedProgress(1, None).sink(1)(_prog("evaluating"))   # no sink → no-op


def test_a_finished_search_ignores_late_events():
    out: list[dict] = []
    p = CombinedProgress(2, out.append)
    p.finish(1)
    p.sink(1)(_prog("searching", phase="case", sources=5))
    assert p.sources == 0


# --------------------------------------------------------------------------- run_extended_searches


class _FakeRetrieval:
    """Stands in for ``orchestrator.run_retrieval``: records each call, emits progress
    on the deps' sink, writes the read-back slots, returns a report."""

    def __init__(self, n: int, *, fail: set[int] = frozenset(), empty: set[int] = frozenset()):
        self.n, self.fail, self.empty = n, fail, empty
        self.calls: list[dict] = []
        self.started = 0
        self._all_started = asyncio.Event()

    async def __call__(self, query, config, deps):
        self.calls.append({"query": query, "config": config, "deps": deps,
                           "decision": deps._decision, "items": list(deps.attached_items)})
        self.started += 1
        if self.started == self.n:
            self._all_started.set()
        # Every search is in flight before any finishes → they run in parallel.
        await asyncio.wait_for(self._all_started.wait(), timeout=5)
        deps.emit_sse({"type": "agent_progress", "stage": "evaluating", "text": "", "data": {}})
        deps.emit_sse({"type": "agent_progress", "stage": "searching", "text": "",
                       "data": {"phase": "reg_compliance", "sources": 3, "queries": 2}})
        deps.emit_sse({"type": "agent_progress", "stage": "aggregating", "text": "", "data": {}})
        deps._ura = SimpleNamespace(high_results=[], medium_results=[])
        deps._reg_rqrs = ["rqr"]
        deps._per_executor_stats = {"reg_search": {"duration_ms": 1}}
        title = deps._decision.planner_brief
        if title in self.fail:
            raise RuntimeError("boom")
        refs = [] if title in self.empty else [make_ref(1, f"reg:{title}")]
        return make_agg(refs, f"تقرير [1] {title}")


def _factory(made: list):
    def deps_factory(i: int):
        d = planner_deps([attached_snapshot("item-1", 3)], wi_alias_map={3: "item-1"})
        made.append((i, d))
        return d

    return deps_factory


def _run(requests, fake, monkeypatch, *, deps_factory=None, emitted=None, made=None):
    monkeypatch.setattr(orch, "run_retrieval", fake)
    monkeypatch.setattr(runner, "_brief", lambda req, plan, view: req.title)
    made = made if made is not None else []
    emitted = emitted if emitted is not None else []
    return asyncio.run(run_extended_searches(
        requests, plan=make_plan(requests), view=make_view(), query="سؤال المستخدم",
        deps_factory=deps_factory or _factory(made), emit_sse=emitted.append,
    ))


def test_searches_run_in_parallel_each_on_its_own_deps(monkeypatch, persisted):
    reqs = [
        make_request("ب1", mode="reg_compliance_led", cap=8),
        make_request("ب2", mode="case_led", cap=5),
        make_request("ب3", mode="full", cap=None),
    ]
    fake, made, emitted = _FakeRetrieval(3), [], []
    reports = _run(reqs, fake, monkeypatch, made=made, emitted=emitted)

    assert [r.index for r in reports] == [1, 2, 3] and all(r.ok for r in reports)
    assert [i for i, _ in made] == [1, 2, 3]                       # 1-based factory index
    deps_ids = {id(c["deps"]) for c in fake.calls}
    assert len(deps_ids) == 3                                      # own deps each
    qids = [r.query_id for r in reports]
    assert len(set(qids)) == 3 and all(q > 0 for q in qids)        # distinct query ids
    by_title = {c["decision"].planner_brief: c for c in fake.calls}
    for req in reqs:
        call = by_title[req.title]
        d, cfg, deps = call["decision"], call["config"], call["deps"]
        assert d.mode == req.mode and d.support is False            # never support (D21)
        assert d.case_full_text_wis == d.reg_full_text_wis == ["WI-3", "WI-4"]  # D2: a free seq
        assert cfg.expander_query_cap == req.max_sub_queries        # per-request cap (None = uncapped)
        assert deps._case_doc_caps == (WHOLE_FILE_PER_DOC_CAP, WHOLE_FILE_TOTAL_CAP)
        assert call["query"] == "سؤال المستخدم"                      # the message, word for word
        assert [s.item_id for s in call["items"]] == ["item-1", "item-2"]
    assert by_title["ب1"]["config"].include_cases is False
    assert by_title["ب2"]["config"].include_reg_compliance is False
    assert by_title["ب3"]["config"].include_cases and by_title["ب3"]["config"].include_reg_compliance
    # read-back copied onto each report
    assert reports[0].reg_rqrs == ["rqr"] and reports[0].ura is not None
    assert reports[0].per_executor_stats == {"reg_search": {"duration_ms": 1}}
    assert [r.sources for r in reports] == [3, 3, 3]
    # one combined stream: never "writing", ends on aggregating with every search done
    stages = [e["stage"] for e in emitted if e.get("type") == "agent_progress"]
    assert "writing" not in stages and "done" not in stages
    assert emitted[-1]["data"] == {"sources": 9, "searches": {"total": 3, "done": 3}}
    assert emitted[-1]["text"] == runner._ALL_DONE_MERGE_TEXT_AR
    assert len(persisted) == 1                                     # fakes flushed once


def test_one_failed_search_never_sinks_the_others(monkeypatch, persisted):
    reqs = [make_request("ب1"), make_request("ب2"), make_request("ب3")]
    fake = _FakeRetrieval(3, fail={"ب2"}, empty={"ب3"})
    emitted: list[dict] = []
    reports = _run(reqs, fake, monkeypatch, emitted=emitted)
    assert [r.ok for r in reports] == [True, False, False]
    assert "RuntimeError: boom" in reports[1].error
    assert reports[2].error == ERROR_NO_REFERENCES and reports[2].agg_output is not None
    assert reports[1].ura is not None                              # read-back even on failure
    assert emitted[-1]["text"] == runner._ALL_DONE_TEXT_AR           # one usable → no merge


def test_a_deps_factory_failure_fails_that_search_only(monkeypatch, persisted):
    made: list = []
    good = _factory(made)

    def deps_factory(i):
        if i == 1:
            raise ValueError("no deps")
        return good(i)

    reqs = [make_request("ب1"), make_request("ب2")]
    fake = _FakeRetrieval(1)
    reports = _run(reqs, fake, monkeypatch, deps_factory=deps_factory)
    assert reports[0].ok is False and "ValueError: no deps" in reports[0].error
    assert reports[1].ok is True and len(fake.calls) == 1


def test_a_preset_query_id_is_kept(monkeypatch, persisted):
    def deps_factory(i):
        return planner_deps([attached_snapshot("item-1", 3)], query_id=500 + i)

    reqs = [make_request("ب1"), make_request("ب2")]
    reports = _run(reqs, _FakeRetrieval(2), monkeypatch, deps_factory=deps_factory)
    assert [r.query_id for r in reports] == [501, 502]


def test_no_requests_no_work(monkeypatch):
    emitted: list[dict] = []
    assert _run([], _FakeRetrieval(0), monkeypatch, emitted=emitted) == []
    assert emitted == []


def test_what_the_searches_read_is_encoded_and_persisted_first(monkeypatch, codec):
    order: list[str] = []
    monkeypatch.setattr(masking_service, "persist_new_mappings",
                        lambda sb, uid, c: order.append("persist") or 0)
    fake = _FakeRetrieval(1)

    async def _retrieval(query, config, deps):
        order.append("search")
        return await fake(query, config, deps)

    monkeypatch.setattr(orch, "run_retrieval", _retrieval)
    monkeypatch.setattr(runner, "_brief", lambda req, plan, view: runner._encode("موجز " + REAL_ID))
    req = make_request("ب1")
    reports = asyncio.run(run_extended_searches(
        [req], plan=make_plan([req]), view=make_view(), query="سؤال",
        deps_factory=lambda i: planner_deps([]),
    ))
    assert reports[0].ok
    call = fake.calls[0]
    assert call["decision"].planner_brief == "موجز " + FAKE_ID
    assert all(REAL_ID not in s.content_md + s.summary for s in call["items"])
    assert order == ["persist", "search"]


def test_the_brief_comes_from_the_planning_side(monkeypatch, persisted):
    """With A's ``brief`` module present, the runner's brief is its §4.9 b text."""
    brief = pytest.importorskip("agents.deep_search_v4.extended.brief")
    fake = _FakeRetrieval(1)
    monkeypatch.setattr(orch, "run_retrieval", fake)
    req = make_request("ب1")
    plan, view = make_plan([req]), make_view()
    asyncio.run(run_extended_searches(
        [req], plan=plan, view=view, query="سؤال", deps_factory=lambda i: planner_deps([]),
    ))
    assert fake.calls[0]["decision"].planner_brief == brief.build_planner_brief(req, plan=plan, view=view)
