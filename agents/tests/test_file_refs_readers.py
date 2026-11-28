"""Agents that read a report as text never see a raw [F1·S7] tag
(aggregator_file_refs.md §4.7): unfold and artifact_analyzer turn it into words
from the report's rows, the chat summarizer drops it. No network, no
``async def`` tests. All text invented."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from shared.workspace import file_refs as fr

REPORT = "wi-report"
BODY = "## الخلاصة\n\n«لم يقدّم ما يثبت السداد» [F1·S7]، وهذا يخالف المادة 77 [1]."
LABELLED = "## الخلاصة\n\n«لم يقدّم ما يثبت السداد» (القسم 7 من الحكم المرفق)، وهذا يخالف المادة 77 [1]."
ROWS = [{"ref_id": "F1.S7", "n": 1, "wi_seq": 1, "section_seq": 7, "doc_title": "الحكم المرفق"}]


@pytest.fixture
def rows(monkeypatch):
    reads: list = []

    def fake(sb, wi_id):
        reads.append(wi_id)
        return list(ROWS)

    monkeypatch.setattr(fr, "load_file_ref_rows", fake)
    return reads


# ---------------------------------------------------------------------------
# unfold_workspace_item
# ---------------------------------------------------------------------------


def _unfold(monkeypatch, item):
    from agents.tool_repository import unfold_workspace_item as uw

    monkeypatch.setattr(uw, "_fetch_item", lambda *a, **kw: dict(item))
    monkeypatch.setattr(uw, "resolve_used_sources", lambda *a, **kw: [])
    return uw.unfold_parts(object(), REPORT, "user-1").joined()


def test_unfold_turns_a_report_tag_into_words(monkeypatch, rows):
    out = _unfold(monkeypatch, {"title": "t", "content_md": BODY, "kind": "agent_search"})
    assert out == LABELLED
    assert rows == [REPORT]


def test_unfold_without_tags_reads_nothing(monkeypatch, rows):
    body = "## الخلاصة\n\nنص [1]."
    assert _unfold(monkeypatch, {"title": "t", "content_md": body, "kind": "agent_search"}) == body
    assert rows == []


def test_unfold_never_touches_an_attachment(monkeypatch, rows):
    from agents.tool_repository import unfold_workspace_item as uw

    monkeypatch.setattr(uw, "correct_text", lambda sb, text, **kw: text)
    out = _unfold(monkeypatch, {"title": "t", "content_md": "نص [F1]", "kind": "attachment"})
    assert out == "نص [F1]" and rows == []


# ---------------------------------------------------------------------------
# artifact_analyzer
# ---------------------------------------------------------------------------


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def __getattr__(self, name):
        return lambda *a, **kw: self

    def execute(self):
        return SimpleNamespace(data=self.rows)


def test_analyzer_reads_the_labelled_body(monkeypatch, rows):
    from agents.memory.artifact_analyzer import context as ctx
    from agents.memory.artifact_analyzer.models import AnalyzerCall

    report = {"item_id": REPORT, "wi_seq": 4, "title": "تقرير", "content_md": BODY, "summary": "",
              "describe_query": "", "metadata": {}}
    sb = SimpleNamespace(table=lambda name: _Q([report]))
    monkeypatch.setattr(ctx, "_load_uras", lambda *a, **kw: {})
    monkeypatch.setattr(ctx, "encode_active", lambda text: text)
    [row] = ctx.load_reports(sb, AnalyzerCall(question="س", user_id="u", conversation_id="c"))
    assert row.content_md == LABELLED
    assert rows == [REPORT]


# ---------------------------------------------------------------------------
# artifact_summarizer — the chat summary reads the body without tags
# ---------------------------------------------------------------------------


def _summarize(monkeypatch, body):
    from agents.memory.artifact_summarizer import runner as sr
    from agents.memory.artifact_summarizer.deps import ArtifactSummaryDeps
    from agents.memory.artifact_summarizer.models import ArtifactSummaryInput

    seen: dict = {}

    def build(**kw):
        seen.update(kw)
        return "msg"

    class Agent:
        async def run(self, *a, **kw):
            raise RuntimeError("model down")

    monkeypatch.setattr(sr, "build_user_message", build)
    monkeypatch.setattr(sr, "create_artifact_summarizer", lambda: Agent())
    inp = ArtifactSummaryInput(describe_query="", content_md=body, title="t")
    out = asyncio.run(sr.handle_artifact_summary_turn(inp, ArtifactSummaryDeps()))
    return seen, out


def test_summarizer_input_and_fallback_have_no_tags(monkeypatch):
    seen, out = _summarize(monkeypatch, BODY)
    assert "[F1·S7]" not in seen["content_md"]
    assert seen["content_md"] == "## الخلاصة\n\n«لم يقدّم ما يثبت السداد»، وهذا يخالف المادة 77 [1]."
    assert "[F1·S7]" not in out.summary_md and out.fallback_used


def test_summarizer_body_without_tags_is_unchanged(monkeypatch):
    body = "## الخلاصة\n\nنص [1]."
    seen, _ = _summarize(monkeypatch, body)
    assert seen["content_md"] == body


# ---------------------------------------------------------------------------
# the chat reply (planner responder) is written from the body without tags
# ---------------------------------------------------------------------------


def test_responder_fallback_has_no_tags():
    from agents.deep_search_v4.planner import runner as pr

    out = pr._response_from_artifact(SimpleNamespace(synthesis_md=BODY))
    assert "[F1·S7]" not in out.chat_summary_md and "[1]" in out.chat_summary_md


def test_responder_inputs_strip_tags():
    import inspect

    from agents.deep_search_v4.planner import prompts as pp
    from agents.deep_search_v4.planner import runner as pr

    assert 'synthesis_md=strip_file_tags((getattr(agg_output, "synthesis_md", "") or "").strip())' in inspect.getsource(pr)
    assert 'synthesis = strip_file_tags((getattr(agg, "synthesis_md", "") or "").strip())' in inspect.getsource(
        pp.build_responder_instructions
    )


# ---------------------------------------------------------------------------
# Share / «حفظ كمدونة» snapshot (blog_service.insert_post) — the frozen public
# copy never carries file tags (added in review, 2026-11-27).
# ---------------------------------------------------------------------------


def test_share_snapshot_strips_file_tags():
    from backend.app.services import blog_service

    captured: dict = {}

    class _Q:
        def insert(self, payload):
            captured.update(payload)
            return self

        def execute(self):
            class R:
                data = [{"token": "t" * 32}]
            return R()

    class _DB:
        def table(self, _name):
            return _Q()

    blog_service.insert_post(
        _DB(),
        owner_user_id="u",
        source_item_id="i",
        subtype=None,
        question_text="q",
        title="t",
        content_md="«لم يسدد» [F1·S7]، والمادة 77 [3] ونص [F2].",
        references_json=[],
    )
    assert captured["content_md"] == "«لم يسدد»، والمادة 77 [3] ونص."


# ---------------------------------------------------------------------------
# The writer reading a report as a source (writer_planner/walkers.py) — the
# body it gets is the labelled one, never raw tags (rework 2026-11-27).
# ---------------------------------------------------------------------------


def test_writer_source_report_is_labelled(monkeypatch, rows):
    import asyncio

    from agents.writer_planner import walkers

    items = {
        REPORT: {"item_id": REPORT, "kind": "agent_search", "title": "تقرير", "content_md": BODY, "wi_seq": 2},
        "att": {"item_id": "att", "kind": "attachment", "title": "مرفق", "content_md": "نص [F1·S7]", "wi_seq": 1},
    }

    async def fetch(deps, ids):
        return {i: items[i] for i in ids}

    async def refs(sb, ids):
        return {}

    async def bodies(ids, index, deps, roles):
        return {}

    monkeypatch.setattr(walkers, "_fetch_workspace_items", fetch)
    monkeypatch.setattr(walkers, "_fetch_used_refs_for_items", refs)
    monkeypatch.setattr(walkers, "_attachment_bodies", bodies)
    deps = type("D", (), {"supabase": object()})()
    out = asyncio.run(walkers.build_analyzed_items_direct([REPORT, "att"], {}, deps))
    by_id = {a.item_id: a.body_md for a in out}
    assert by_id[REPORT] == LABELLED
    assert by_id["att"] == "نص [F1·S7]"  # only a report's tags are labelled
    assert rows == [REPORT]


# ---------------------------------------------------------------------------
# A prior report attached to a later turn (orchestrator._load_attached_items):
# the planner / simple search / writer context read words, not raw tags
# (added in review, 2026-11-27).
# ---------------------------------------------------------------------------


def test_attached_report_reads_labels_not_tags(monkeypatch):
    import agents.orchestrator as orch
    import shared.workspace.file_refs as fr

    report = {
        "item_id": "r1", "wi_seq": 2, "kind": "agent_search", "title": "تقرير",
        "content_md": "«لم يسدد» [F1·S7] والمادة 77 [3].", "summary": "", "metadata": {},
    }
    attachment = {
        "item_id": "a1", "wi_seq": 1, "kind": "attachment", "title": "الحكم",
        "content_md": "نص [F1·S7] كما هو", "summary": "", "metadata": {},
    }
    rows_by_id = {"r1": report, "a1": attachment}

    class _Q:
        def __init__(self):
            self._id = None

        def select(self, *_a, **_k):
            return self

        def eq(self, col, val):
            if col == "item_id":
                self._id = val
            return self

        def is_(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            class R:
                pass
            r = R()
            r.data = dict(rows_by_id[self._id])
            return r

    class _DB:
        def table(self, _name):
            return _Q()

    monkeypatch.setattr(orch.files_store, "overlay_files", lambda _s, rows, **_k: rows)
    monkeypatch.setattr(orch, "correct_rows", lambda _s, rows, **_k: rows)
    monkeypatch.setattr(
        fr, "load_file_ref_rows",
        lambda _s, wi_id: [{"ref_id": "F1.S7", "doc_title": "الحكم", "section_seq": 7}] if wi_id == "r1" else [],
    )
    snaps = {s.item_id: s for s in orch._load_attached_items(_DB(), ["r1", "a1"], "u", "c")}
    assert snaps["r1"].content_md == "«لم يسدد» (القسم 7 من الحكم) والمادة 77 [3]."
    # Only reports are relabelled; an attachment's own text is never touched.
    assert snaps["a1"].content_md == "نص [F1·S7] كما هو"
