"""The whole-file hook in ``deep_search_v4/orchestrator.py`` («بحث شامل», Q11):
``deps._case_doc_caps`` → every attached document whole (200k / 300k), no ``doc_type``
filter, no segmented section selection, no segment wait. Unset → today's path."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from agents.deep_search_v4 import orchestrator as orch
from agents.deep_search_v4.extended.runner import WHOLE_FILE_PER_DOC_CAP, WHOLE_FILE_TOTAL_CAP
from agents.deep_search_v4.planner.apply import build_retrieval_config
from agents.deep_search_v4.planner.models import PlannerDecision
from agents.models import WorkspaceItemSnapshot
from agents.utils import attachment_sections
from agents.utils.attachment_delivery import Section

from ._exec_fakes import planner_deps

CAPS = (WHOLE_FILE_PER_DOC_CAP, WHOLE_FILE_TOTAL_CAP)


def _doc(seq: int, content: str, doc_type: str = "judgment") -> WorkspaceItemSnapshot:
    return WorkspaceItemSnapshot(
        item_id=f"id-{seq}", kind="attachment", title=f"وثيقة {seq}", content_md=content,
        summary=f"ملخص {seq}", metadata={"doc_type": doc_type}, wi_seq=seq,
    )


def _decision(aliases: list[str]) -> PlannerDecision:
    return PlannerDecision(mode="full", rationale="r", planner_brief="موجز",
                           case_full_text_wis=aliases, reg_full_text_wis=aliases)


# --------------------------------------------------------------------------- _whole_file_caps


def test_caps_are_read_only_when_set_to_two_positive_ints():
    assert orch._whole_file_caps(SimpleNamespace()) is None
    assert orch._whole_file_caps(SimpleNamespace(_case_doc_caps=None)) is None
    assert orch._whole_file_caps(MagicMock()) is None          # a test double never turns it on
    assert orch._whole_file_caps(SimpleNamespace(_case_doc_caps=(0, 5))) is None
    assert orch._whole_file_caps(SimpleNamespace(_case_doc_caps="200000")) is None
    assert orch._whole_file_caps(SimpleNamespace(_case_doc_caps=CAPS)) == CAPS


# --------------------------------------------------------------------------- _build_case_document_lists


def test_without_caps_the_lists_are_todays():
    deps = SimpleNamespace(attached_items=[_doc(1, "س" * 20_000), _doc(2, "ن" * 500, "official_record")])
    lists, tel = orch._build_case_document_lists(_decision(["WI-1", "WI-2"]), deps)
    assert "[انتهى المعروض: 12000" in lists["aggregator"][0].body
    assert len(lists["aggregator"]) == 1                     # official_record is not forwarded today
    assert "case_docs.whole_file_caps" not in tel


def test_with_caps_every_document_travels_whole():
    big = "س" * 150_000
    deps = SimpleNamespace(
        attached_items=[_doc(1, big), _doc(2, "ن" * 500, "official_record")],
        _case_doc_caps=CAPS,
    )
    lists, tel = orch._build_case_document_lists(_decision(["WI-1", "WI-2"]), deps)
    for recipient in ("case", "reg", "aggregator"):
        bodies = [b.body for b in lists[recipient]]
        assert len(bodies) == 2                               # no doc_type filter
        assert big in bodies[0]                               # whole, no cut marker
        assert "[انتهى المعروض" not in bodies[0]
        assert "#### الملخص\nملخص 1" in bodies[0]             # the summary travels (D29)
        assert tel[f"case_docs.full_text.{recipient}"] == ["WI-1", "WI-2"]
    assert tel["case_docs.whole_file_caps"] == list(CAPS)


def test_with_caps_segmented_sections_are_ignored():
    deps = SimpleNamespace(attached_items=[_doc(1, "س" * 20_000)], _case_doc_caps=CAPS)
    sections = {"id-1": [Section(1, "core", "أسباب الحكم", "وحيث إن")]}
    lists, tel = orch._build_case_document_lists(_decision(["WI-1"]), deps, sections)
    assert "〔أسباب الحكم〕" not in lists["case"][0].body
    assert "س" * 20_000 in lists["case"][0].body
    assert not any(k.startswith("case_docs.segmented") for k in tel)


def test_with_caps_the_per_document_and_total_caps_apply():
    deps = SimpleNamespace(
        attached_items=[_doc(1, "س" * 250_000), _doc(2, "ن" * 150_000), _doc(3, "م" * 50_000)],
        _case_doc_caps=CAPS,
    )
    lists, tel = orch._build_case_document_lists(_decision(["WI-1", "WI-2", "WI-3"]), deps)
    agg = [b.body for b in lists["aggregator"]]
    assert "[انتهى المعروض: 200000 حرف من أصل 250000" in agg[0]
    assert "لتجاوز حد السياق" in agg[1]                     # 200k + 150k > 300k → summary + note
    assert "م" * 50_000 in agg[2]                            # a later, shorter one still fits
    assert tel["case_docs.truncated"] == ["WI-1"]
    assert "aggregator:WI-2" in tel["case_docs.over_total"]


# --------------------------------------------------------------------------- run_retrieval


def _capture(monkeypatch) -> dict:
    captured: dict = {}

    async def _picker(**kwargs):
        return None

    async def _full_loop(query, query_id, deps, prompt_key="prompt_1"):
        await asyncio.sleep(0)
        captured["full_deps"] = deps
        captured["query_id"] = query_id
        return SimpleNamespace(references=[], confidence="medium")

    async def _wait(*args, **kwargs):
        captured["waited"] = True
        return {"id-1": [Section(1, "core", "أسباب الحكم", "وحيث إن")]}, {}

    monkeypatch.setattr(orch, "run_sector_picker", _picker)
    monkeypatch.setattr(orch, "run_full_loop", _full_loop)
    monkeypatch.setattr(orch, "delivery_enabled", lambda _uid: True)
    monkeypatch.setattr(attachment_sections, "wait_and_load_sections", _wait)
    return captured


def test_run_retrieval_with_caps_skips_the_segment_wait(monkeypatch):
    captured = _capture(monkeypatch)
    decision = _decision(["WI-1"])
    deps = planner_deps([_doc(1, "س" * 20_000)], _decision=decision, query_id=42)
    deps._case_doc_caps = CAPS
    asyncio.run(orch.run_retrieval("سؤال", build_retrieval_config(decision), deps))
    assert "waited" not in captured
    body = captured["full_deps"].case_document_blocks["aggregator"][0].body
    assert "س" * 20_000 in body and "〔أسباب الحكم〕" not in body
    assert captured["query_id"] == 42


def test_run_retrieval_without_caps_still_waits_and_segments(monkeypatch):
    captured = _capture(monkeypatch)
    decision = _decision(["WI-1"])
    deps = planner_deps([_doc(1, "س" * 20_000)], _decision=decision)
    asyncio.run(orch.run_retrieval("سؤال", build_retrieval_config(decision), deps))
    assert captured["waited"] is True
    assert "〔أسباب الحكم〕" in captured["full_deps"].case_document_blocks["aggregator"][0].body
    assert "case_docs.whole_file_caps" not in captured["full_deps"]._case_docs_telemetry
