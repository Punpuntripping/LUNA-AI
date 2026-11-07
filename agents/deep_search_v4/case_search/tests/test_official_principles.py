"""Official judicial principles on the case side.

Plan: `.claude/plans/official_principles_refs.md` §3.2 (retrieval) and the
cases half of §3.3 (reranker), decisions D2 / D4 / D7.

Guards:

1. **Grouping** — `official_principle` rows (case_id NULL) group by
   `attrs.principle_id`, best topic wins, rule text from `short_summary`, top
   `PRINCIPLE_CANDIDATES`; `group_topic_rows` still drops them.
2. **The side-call** — principle channel only, flag on only, same embedding,
   no sector filter, 40 rows; a dead side-call costs the principles, never the
   rulings; flag off leaves the outcome exactly as it was.
3. **The reranker view** — `### [N] مبدأ قضائي رسمي` / `الباب:` / rule text,
   nothing else (D4); the parser recognises the header.
4. **The caps** — rulings keep `max_keep` untouched, principles get their own
   `PRINCIPLE_EXTRA_KEEP` on top (D2), in reranker.py, the decider rules and
   the loop's decision-log re-cap; `caps_applied` records both.
5. **The kept principle** — the contract fields `ura/case_adapter.py` routes
   on, built from the candidate row with no `cases` fetch.

No live RPC or LLM: the RPC, the agent, `decide()` and settings are faked.
"""
from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.deep_search_v4.case_search import decider_rules as dr
from agents.deep_search_v4.case_search import loop as loop_mod
from agents.deep_search_v4.case_search import reranker as reranker_mod
from agents.deep_search_v4.case_search import search as search_mod
from agents.deep_search_v4.case_search.fusion import wrap_as_fused
from agents.deep_search_v4.case_search.models import (
    CANDIDATE_KIND_PRINCIPLE,
    PRINCIPLE_EXTRA_KEEP,
    CaseKeep,
    CaseRerankerClassification,
    CaseSearchDeps,
    CaseSearchOutcome,
    ChannelCandidate,
    LoopState,
    RerankerQueryResult,
    TypedQuery,
)
from agents.deep_search_v4.case_search.reranker import (
    _parse_case_blocks,
    run_reranker_for_query,
)
from agents.deep_search_v4.case_search.unfold_reranker import (
    format_bucket_for_reranker,
    format_candidate_for_reranker,
)
from agents.deep_search_v4.case_search.unfold_ura import (
    assemble_kept_cases,
    principle_title,
)
from agents.deep_search_v4.shared.decider import DeciderReply

D1 = "liquid/d1-ultra"
BODIES = "المحكمة العليا، الهيئة الدائمة بمجلس القضاء الأعلى"


# -- Row / candidate builders --------------------------------------------------


def _ruling_row(case_id: str, score: float, *, idx: int = 0) -> dict:
    return {
        "case_id": case_id, "topic_ref": f"T-{case_id}-{idx}", "topic_index": idx,
        "topic_text": f"موضوع الحكم {case_id}", "attrs": {"النوع": "موضوعي"},
        "score": score, "kind": "principle", "case_ref": f"REF-{case_id}",
        "court": f"محكمة {case_id}", "city": "الرياض", "court_level": "first_instance",
        "case_number": "1445/1", "date_hijri": "1445/01/01", "short_summary": "ملخص",
    }


def _principle_row(
    pid: str,
    score: float,
    *,
    idx: int = 0,
    no: int | str = 12,
    chapter: str = "السرقة",
    rule: str = "نص المبدأ",
    attrs_as_str: bool = False,
    summary: bool = True,
) -> dict:
    attrs = {"principle_id": pid, "principle_no": no, "chapter": chapter, "sectors": ["C01"]}
    return {
        "case_id": None, "topic_ref": f"P-{pid}-{idx}", "topic_index": idx,
        "topic_text": rule if idx == -1 else f"موضوع المبدأ {pid}",
        "attrs": json.dumps(attrs, ensure_ascii=False) if attrs_as_str else attrs,
        "score": score, "kind": "official_principle", "case_ref": None,
        "court": BODIES, "city": None, "court_level": "supreme",
        "case_number": "12/3 ، 45/6", "date_hijri": "1400/01/01",
        "short_summary": rule if summary else None,
    }


def _ruling(i: int, score: float = 0.5) -> ChannelCandidate:
    return search_mod.group_topic_rows([_ruling_row(f"c{i}", score)], "principle")[0]


def _principle(i: int, score: float = 0.6, *, chapter: str = "السرقة") -> ChannelCandidate:
    cand = search_mod.group_principle_rows(
        [_principle_row(f"p{i}", score, no=100 + i, chapter=chapter, rule=f"نص المبدأ {i}")],
        "principle",
    )[0]
    return cand


# -- (1) Grouping --------------------------------------------------------------


def test_principle_rows_group_by_principle_id_not_case_id():
    rows = [
        _principle_row("p1", 0.50, idx=0),
        _principle_row("p1", 0.70, idx=1),
        _principle_row("p1", 0.60, idx=-1, rule="القاعدة الأولى"),
        _principle_row("p2", 0.90, idx=0, chapter="", rule="القاعدة الثانية"),
        {**_principle_row("p3", 0.99), "attrs": {"chapter": "بلا معرّف"}},   # no principle_id
    ]
    rows[0]["short_summary"] = rows[1]["short_summary"] = "القاعدة الأولى"

    cands = search_mod.group_principle_rows(rows, "principle")

    assert [c.case_id for c in cands] == ["p2", "p1"]
    assert all(c.candidate_kind == CANDIDATE_KIND_PRINCIPLE for c in cands)
    assert [c.rank for c in cands] == [1, 2]
    p1 = cands[1]
    assert p1.score == 0.70                                   # best topic wins
    assert [t["score"] for t in p1.topics] == [0.70, 0.60, 0.50]
    assert p1.row["principle_id"] == "p1"
    assert p1.row["short_summary"] == "القاعدة الأولى"
    assert p1.row["chapter"] == "السرقة" and p1.row["principle_no"] == 12
    assert p1.row["court"] == BODIES and p1.row["court_level"] == "supreme"
    assert cands[0].row["chapter"] == ""
    # The same rows are invisible to the ruling grouper (case_id NULL).
    assert search_mod.group_topic_rows(rows, "principle") == []


def test_rule_text_falls_back_to_the_minus_one_row_and_attrs_may_be_a_string():
    rows = [
        _principle_row("p1", 0.4, idx=0, summary=False, attrs_as_str=True),
        _principle_row("p1", 0.3, idx=-1, rule="نص القاعدة", summary=False, attrs_as_str=True),
    ]
    (cand,) = search_mod.group_principle_rows(rows, "principle")
    assert cand.row["short_summary"] == "نص القاعدة"
    assert cand.row["chapter"] == "السرقة"


def test_top_principle_candidates_cut():
    rows = [_principle_row(f"p{i}", i / 100) for i in range(12)]
    cands = search_mod.group_principle_rows(rows, "principle")
    assert search_mod.PRINCIPLE_CANDIDATES == 8
    assert [c.case_id for c in cands] == [f"p{i}" for i in range(11, 3, -1)]


# -- (2) The side-call ---------------------------------------------------------


_RULING_ROWS = [_ruling_row("c1", 0.8), _ruling_row("c2", 0.7), _ruling_row("c1", 0.6, idx=1)]
_PRINCIPLE_ROWS = [_principle_row("p1", 0.75), _principle_row("p2", 0.65, chapter="الوقف")]


def _deps() -> CaseSearchDeps:
    return CaseSearchDeps(supabase=object(), embedding_fn=AsyncMock(return_value=[0.1]),  # type: ignore[arg-type]
                          _events=[])


@pytest.fixture
def rpc(monkeypatch):
    """Fake `search_case_topics_rpc`; `h.fail[kind]` makes that kind raise."""
    h = SimpleNamespace(calls=[], fail={}, flag=True)

    async def fake_rpc(supabase, *, kind, embedding, sectors, match_count):
        h.calls.append({"kind": kind, "embedding": embedding, "sectors": sectors,
                        "match_count": match_count})
        if kind in h.fail:
            raise h.fail[kind]
        rows = _PRINCIPLE_ROWS if kind == "official_principle" else _RULING_ROWS
        return [dict(r) for r in rows], 3.0

    monkeypatch.setattr(search_mod, "search_case_topics_rpc", fake_rpc)
    monkeypatch.setattr(
        "shared.config.get_settings",
        lambda: SimpleNamespace(OFFICIAL_PRINCIPLES_ENABLED=h.flag),
    )
    return h


async def _search(channel: str = "principle", sectors=None) -> CaseSearchOutcome:
    q = TypedQuery(text="قطع اليد في حد السرقة", channel=channel)
    return await search_mod.search_case_section(q, _deps(), sectors=sectors,
                                                precomputed_embedding=[0.1, 0.2])


@pytest.mark.asyncio
async def test_principle_channel_runs_the_side_call_with_the_same_embedding(rpc):
    out = await _search(sectors=["تجاري"])

    kinds = sorted(c["kind"] for c in rpc.calls)
    assert kinds == ["official_principle", "principle"]
    side = next(c for c in rpc.calls if c["kind"] == "official_principle")
    main = next(c for c in rpc.calls if c["kind"] == "principle")
    assert side["embedding"] is main["embedding"]               # reused, not re-embedded
    assert side["sectors"] is None                               # D7: never filtered
    assert side["match_count"] == search_mod.PRINCIPLE_MATCH_COUNT == 40

    assert out.error is None and out.principle_searched is True
    assert out.principle_error is None and out.principle_rows == 2
    assert [c.case_id for c in out.principles] == ["p1", "p2"]
    assert all(c.candidate_kind == CANDIDATE_KIND_PRINCIPLE for c in out.principles)
    assert [c.case_id for c in out.candidates] == ["c1", "c2"]   # rulings only


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["facts", "basis"])
async def test_other_channels_never_run_the_side_call(rpc, channel):
    out = await _search(channel)
    assert [c["kind"] for c in rpc.calls] == [search_mod.CHANNEL_TO_KIND[channel]]
    assert out.principles == [] and out.principle_searched is False


@pytest.mark.asyncio
async def test_flag_off_is_the_old_outcome_and_rulings_are_untouched_when_on(rpc):
    rpc.flag = False
    off = await _search()
    assert [c["kind"] for c in rpc.calls] == ["principle"]
    rulings = search_mod.group_topic_rows(_RULING_ROWS, "principle")
    assert off == CaseSearchOutcome(
        candidates=rulings, count=2, max_score=0.8, top_scores=[0.8, 0.7, 0.6],
        topic_rows=3, topic_rows_kept=3, gate_wait_ms=3.0,
    )

    rpc.flag = True
    on = await _search()
    assert dataclasses.replace(
        on, principles=[], principle_searched=False, principle_rows=0, principle_error=None,
    ) == off


@pytest.mark.asyncio
async def test_settings_without_the_field_mean_off(rpc, monkeypatch):
    monkeypatch.setattr("shared.config.get_settings", lambda: SimpleNamespace())
    out = await _search()
    assert [c["kind"] for c in rpc.calls] == ["principle"] and out.principles == []

    def boom():
        raise RuntimeError("no env")

    monkeypatch.setattr("shared.config.get_settings", boom)
    assert search_mod.official_principles_enabled() is False


@pytest.mark.asyncio
async def test_a_dead_side_call_costs_the_principles_never_the_rulings(rpc):
    rpc.fail["official_principle"] = TimeoutError("read timeout")
    out = await _search()
    assert out.error is None                                      # the sub-query did not fail
    assert [c.case_id for c in out.candidates] == ["c1", "c2"]
    assert out.principles == [] and out.principle_searched is True
    assert out.principle_error == "TimeoutError"


@pytest.mark.asyncio
async def test_a_dead_ruling_call_is_still_the_typed_failure(rpc):
    rpc.fail["principle"] = TimeoutError("read timeout")
    out = await _search()
    assert out.error == "TimeoutError"
    assert out.candidates == [] and out.principles == []


# -- (3) The reranker view -----------------------------------------------------


def test_principle_block_is_chapter_and_rule_only():
    cand = _principle(3, chapter="السرقة")
    assert format_candidate_for_reranker(cand, 16) == (
        "### [16] مبدأ قضائي رسمي\nالباب: السرقة\nنص المبدأ 3\n"
    )
    block = format_candidate_for_reranker(cand, 16)
    for absent in ("المحكمة", "103", "12/3", "1400", "الموضوعات", "الملخص"):
        assert absent not in block


def test_principle_block_omits_an_empty_chapter_and_survives_wrap_as_fused():
    cand = _principle(4, chapter="")
    assert format_candidate_for_reranker(cand, 2) == "### [2] مبدأ قضائي رسمي\nنص المبدأ 4\n"
    (fused,) = wrap_as_fused([cand])
    assert fused.candidate_kind == CANDIDATE_KIND_PRINCIPLE
    assert format_candidate_for_reranker(fused, 2) == "### [2] مبدأ قضائي رسمي\nنص المبدأ 4\n"


def test_ruling_block_is_unchanged():
    assert format_candidate_for_reranker(_ruling(1), 1) == (
        "### [1]\nالمحكمة: محكمة c1 — الرياض (ابتدائي)\n"
        "الموضوعات المطابقة:\n- [مبدأ · موضوعي] موضوع الحكم c1\nالملخص: ملخص\n"
    )


def test_parser_tags_principle_blocks_and_keeps_legacy_headers():
    md, _n = format_bucket_for_reranker([_ruling(1), _ruling(2), _principle(1)])
    blocks = _parse_case_blocks(md)
    assert [(b["position"], b["kind"]) for b in blocks] == [
        (1, "case"), (2, "case"), (3, "official_principle"),
    ]
    legacy = _parse_case_blocks("### [1] حكم: محكمة — مدينة (ابتدائي)\nنص\n")
    assert legacy[0]["title"] == "محكمة — مدينة (ابتدائي)" and legacy[0]["kind"] == "case"


# -- (4) The caps --------------------------------------------------------------


class _Usage:
    requests = 1
    input_tokens = 10
    output_tokens = 5
    total_tokens = 15
    details = {}


class _Agent:
    def __init__(self, keeps):
        self._keeps = keeps

    async def run(self, msg, **_kw):
        out = CaseRerankerClassification(sufficient=True, keeps=self._keeps, summary_note="ن")
        return SimpleNamespace(output=out, usage=lambda: _Usage())


def _keep(p: int, rel: str) -> CaseKeep:
    return CaseKeep(position=p, relevance=rel, reasoning=f"سبب {p}")


async def _rerank(cands, keeps, monkeypatch, **kw):
    monkeypatch.setattr(reranker_mod, "create_reranker_agent", lambda **_k: _Agent(keeps))
    md, _n = format_bucket_for_reranker(cands, bucket_label="q1_principle")
    return await run_reranker_for_query(
        query="س", rationale="[principle] ر", raw_markdown=md, max_keep=7, engine="llm", **kw,
    )


@pytest.mark.asyncio
async def test_rulings_keep_max_keep_and_principles_get_their_own_five(monkeypatch):
    rulings = [_ruling(i) for i in range(1, 10)]          # positions 1..9
    principles = [_principle(i) for i in range(1, 7)]     # positions 10..15
    keeps = (
        [_keep(1, "high"), _keep(2, "high")] + [_keep(p, "medium") for p in range(3, 10)]
        + [_keep(p, "medium") for p in range(10, 14)] + [_keep(14, "high"), _keep(15, "high")]
    )
    result, _usage, log = await _rerank(rulings + principles, keeps, monkeypatch)

    kinds = [r.candidate_kind for r in result.results]
    assert kinds == ["case"] * 7 + [CANDIDATE_KIND_PRINCIPLE] * PRINCIPLE_EXTRA_KEEP
    # Principles: high first (14, 15), then the first three medium keeps.
    assert [r.content for r in result.results[7:]] == [
        "نص المبدأ 5", "نص المبدأ 6", "نص المبدأ 1", "نص المبدأ 2", "نص المبدأ 3",
    ]
    assert result.caps_applied == {
        "max_keep": 7, "truncated_by_cap": 2,
        "principle_extra_keep": 5, "principle_truncated_by_cap": 1,
    }
    assert result.dropped_count == 3
    assert len([d for d in log if d["action"] == "keep"]) == 15

    # Rulings never displaced: the same 7 rulings as a window with no principles.
    alone, _u, _l = await _rerank(rulings, keeps[:9], monkeypatch)
    assert [r.title for r in result.results[:7]] == [r.title for r in alone.results]


@pytest.mark.asyncio
async def test_no_principle_in_the_window_keeps_the_old_caps_dict(monkeypatch):
    rulings = [_ruling(i) for i in range(1, 10)]
    keeps = [_keep(p, "medium") for p in range(1, 10)]
    result, _u, _l = await _rerank(rulings, keeps, monkeypatch)
    assert result.caps_applied == {"max_keep": 7, "truncated_by_cap": 2}
    assert len(result.results) == 7


def test_decider_classify_splits_rulings_and_principles():
    rulings = {1: (3.0, 0.80), 2: (2.23, 1.0), 3: (2.0, 0.85), 4: (2.0, 0.84)}
    principles = {
        5: (0.4, 1.0),   # unrelated subject → dropped
        6: (0.6, 0.1),   # related → kept (low mech never drops a principle), but the
                         # lowest rank of six keeps → cut by PRINCIPLE_EXTRA_KEEP
        7: (3.0, 0.9),   # decides the mechanism → high
        8: (2.6, 0.4),   # high score but mech < 0.5 → medium
        9: (1.5, 0.9),   # medium
        10: (2.0, 0.9),  # medium
        11: (1.0, 1.0),  # medium
    }
    alone = dr.classify(rulings, model=D1, max_keep=2)
    both = dr.classify(rulings | principles, model=D1, max_keep=2,
                       principle_positions=frozenset(principles))
    pairs = [(k.position, k.relevance) for k in both.keeps]
    assert pairs[:2] == [(k.position, k.relevance) for k in alone.keeps]   # rulings untouched
    assert pairs[2:] == [(7, "high"), (10, "medium"), (9, "medium"), (8, "medium"), (11, "medium")]


def test_decider_questions_use_the_principle_rubric_only_on_principles():
    plain, _ = dr.build_questions([1, 2], "principle")
    questions, required = dr.build_questions([1, 2], "principle", principle_positions={2})
    assert questions["R1"] == plain["R1"] and questions["M1"] == plain["M1"]
    assert questions["R2"]["criteria"] == dr.PRINCIPLE_LEVELS
    assert questions["R2"]["instructions"].startswith(dr.PRINCIPLE_RUBRIC)
    assert questions["R2"]["instructions"].endswith("`### [2] مبدأ قضائي رسمي`.")
    assert questions["M2"]["criteria"] == dr.PRINCIPLE_MECH_CRITERIA
    assert required == ["R1", "M1", "R2", "M2"]


@pytest.mark.asyncio
async def test_decider_path_sends_principle_positions(monkeypatch):
    seen = {}

    async def fake_decide(state, questions, *, required, models):
        seen["questions"] = questions
        answers = {"R1": {"score": 3.0}, "M1": {"noul": 0.9},
                   "R2": {"score": 3.0}, "M2": {"noul": 0.9}}
        return DeciderReply(model=D1, model_served=D1, answers=answers,
                            usage_entry={"agent": "reranker"}, fallback_from=[])

    monkeypatch.setattr(reranker_mod._decider, "decide", fake_decide)
    monkeypatch.setattr(reranker_mod, "_decider_models", lambda *_a: [D1])
    md, _n = format_bucket_for_reranker([_ruling(1), _principle(1)])
    result, _u, _l = await run_reranker_for_query(
        query="س", rationale="[principle] ر", raw_markdown=md, max_keep=7,
    )
    assert seen["questions"]["R2"]["criteria"] == dr.PRINCIPLE_LEVELS
    assert [r.candidate_kind for r in result.results] == ["case", CANDIDATE_KIND_PRINCIPLE]
    assert result.caps_applied["principle_extra_keep"] == PRINCIPLE_EXTRA_KEEP


# -- (5) The kept principle ----------------------------------------------------


class _FakeSupabase:
    """`cases` SELECT stub — records every id list it is asked for."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.asked: list[list[str]] = []

    def table(self, name):
        assert name == "cases"
        sb = self

        class _Q:
            def select(self, *_a):
                return self

            def in_(self, _col, ids):
                sb.asked.append(sorted(ids))
                self._ids = set(ids)
                return self

            def execute(self):
                return SimpleNamespace(data=[r for r in sb.rows if r["id"] in self._ids])

        return _Q()


def _case_row(cid: str) -> dict:
    return {"id": cid, "case_ref": f"REF-{cid}", "court": f"محكمة {cid}",
            "court_level": "appeal", "summary": f"ملخص {cid}"}


def test_principle_title():
    assert principle_title(43, "الوقف") == "المبدأ رقم 43 — الوقف"
    assert principle_title("٤٣", "") == "المبدأ رقم 43"
    assert principle_title(None, "الوقف") == "مبدأ قضائي — الوقف"


@pytest.mark.asyncio
async def test_kept_principle_is_built_from_its_row_without_a_cases_fetch():
    sb = _FakeSupabase([_case_row("c1")])
    bucket = wrap_as_fused([_ruling(1), _principle(7, score=0.71)])

    only_principle = await assemble_kept_cases(
        sb, kept_decisions=[{"position": 2, "relevance": "high", "reasoning": "موضوعه السرقة"}],
        fused_bucket=bucket,
    )
    assert sb.asked == []                                   # no cases fetch at all
    (p,) = only_principle
    assert p.candidate_kind == CANDIDATE_KIND_PRINCIPLE == p.source_type
    assert p.principle_id == p.db_id == p.db_uuid == "p7"
    assert p.title == "المبدأ رقم 107 — السرقة"
    assert p.content == "نص المبدأ 7"
    assert p.court == BODIES and p.court_level == "supreme"
    assert p.score == 0.71 and p.relevance == "high" and p.reasoning == "موضوعه السرقة"

    mixed = await assemble_kept_cases(
        sb, kept_decisions=[{"position": 1, "relevance": "medium"}, {"position": 2}],
        fused_bucket=bucket,
    )
    assert sb.asked == [["c1"]]                             # the principle id never asked
    assert [r.candidate_kind for r in mixed] == ["case", CANDIDATE_KIND_PRINCIPLE]


# -- (6) Through the loop ------------------------------------------------------


@pytest.mark.asyncio
async def test_search_node_appends_principles_after_rulings_and_keeps_fusion_rulings_only(monkeypatch):
    rulings = [_ruling(i, 0.9 - i / 100) for i in range(1, 4)]
    principles = [_principle(1), _principle(2)]

    async def fake_section(query, deps, **_kw):
        return CaseSearchOutcome(candidates=list(rulings), count=3, max_score=0.9,
                                 principles=list(principles), principle_searched=True,
                                 principle_rows=5)

    monkeypatch.setattr(loop_mod, "search_case_section", fake_section)
    monkeypatch.setattr("agents.utils.embeddings.embed_regulation_queries_alibaba",
                        AsyncMock(return_value=[[0.0]]))
    q = TypedQuery(text="س", channel="principle")
    state = LoopState(focus_instruction="س", user_context="",
                      expander_output_v2=SimpleNamespace(queries=[q]))  # type: ignore[arg-type]
    deps = _deps()
    await loop_mod.SectionedSearchNode().run(SimpleNamespace(state=state, deps=deps))

    (_q, payload), = state.per_query_candidates
    assert [c.case_id for c in payload] == ["c1", "c2", "c3", "p1", "p2"]
    assert [c.case_id for c in state.channel_candidates["principle"]] == ["c1", "c2", "c3"]
    assert "### [4] مبدأ قضائي رسمي" in state.search_results_log[0]["raw_markdown"]


@pytest.mark.asyncio
async def test_reranker_node_windows_caps_and_tags_principles(monkeypatch):
    rulings = [_ruling(i, 0.9 - i / 100) for i in range(1, 21)]          # 20 → window 15
    principles = [_principle(i, 0.8 - i / 100) for i in range(1, 9)]     # 8 → positions 16..23
    seen = {}

    async def fake_reranker(**kw):
        seen["md"] = kw["raw_markdown"]
        log = [{"position": p, "action": "keep", "relevance": "medium", "reasoning": f"ر{p}"}
               for p in range(1, 10)]
        log += [{"position": p, "action": "drop"} for p in range(10, 16)]
        log += [{"position": p, "action": "keep",
                 "relevance": "high" if p in (22, 23) else "medium", "reasoning": f"م{p}"}
                for p in range(16, 24)]
        rqr = RerankerQueryResult(query=kw["query"], rationale=kw["rationale"], sufficient=True,
                                  results=[], dropped_count=6, summary_note="ن")
        return rqr, [], log

    sb = _FakeSupabase([_case_row(f"c{i}") for i in range(1, 21)])
    monkeypatch.setattr(loop_mod, "run_reranker_for_query", fake_reranker)
    q = TypedQuery(text="س", channel="principle", rationale="ر")
    state = LoopState(focus_instruction="س", user_context="",
                      per_query_candidates=[(q, rulings + principles)])
    deps = CaseSearchDeps(supabase=sb, embedding_fn=AsyncMock())  # type: ignore[arg-type]
    await loop_mod.SectionedRerankerNode().run(SimpleNamespace(state=state, deps=deps))

    blocks = _parse_case_blocks(seen["md"])
    assert len(blocks) == 23
    assert [b["position"] for b in blocks if b["kind"] == "official_principle"] == list(range(16, 24))

    (rqr,) = state.reranker_results
    kinds = [r.candidate_kind for r in rqr.results]
    assert kinds == ["case"] * 7 + [CANDIDATE_KIND_PRINCIPLE] * 5
    assert sb.asked == [sorted(f"c{i}" for i in range(1, 8))]   # rulings only, capped first
    # High first (p7, p8 at positions 22, 23), then retrieval score desc (p1, p2, p3).
    assert [r.principle_id for r in rqr.results[7:]] == ["p1", "p2", "p3", "p7", "p8"]
    assert {r.principle_id: r.relevance for r in rqr.results[7:]}["p8"] == "high"

    capped = [d for d in rqr.dropped_results if d["drop_reason"] == "cap"]
    assert sorted(d["ref_id"] for d in capped if d["source_type"] == "official_principle") == [
        "p4", "p5", "p6",
    ]
    assert all(d["source_table"] == "official_principles" for d in capped
               if d["source_type"] == "official_principle")
    assert {d["title"] for d in capped if d["ref_id"] == "p4"} == {"المبدأ رقم 104 — السرقة"}
    assert sorted(d["ref_id"] for d in capped if d["source_type"] == "case") == ["c8", "c9"]
