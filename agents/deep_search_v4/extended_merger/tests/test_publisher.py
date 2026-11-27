"""The merged answer («بحث موسّع») is published as ONE agent_search workspace item
with ``metadata.subtype = "extended_synthesis"`` (plan extended_search.md §4.7, D14).

``create_workspace_item`` / ``persist_item_references`` / ``decode_for_persist``
are monkeypatched on the publisher module — no database, no codec.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agents.agent_search.deps import SearchPublishDeps
from agents.deep_search_v4.aggregator.models import Reference
from agents.deep_search_v4.extended_merger import publisher
from agents.deep_search_v4.extended_merger.models import (
    SUBTYPE,
    AngleCard,
    AngleStatus,
    LeftOutEntry,
    MergedPublishInput,
    MergedReportEntry,
    MergerOutput,
    Resolution,
)

FAKE = "FAKE-9"  # a masked token as the pipeline holds it
REAL = "REAL-1"  # what the codec restores

_METADATA_KEYS = {
    "subtype", "confidence", "detail_level", "client_side", "run_id", "prompt_version", "model_used",
    "ref_count", "cited_count", "children", "reports", "angles", "angle_status",
    "resolved", "left_out", "gaps", "ref_reports", "stats",
}


def _ref(n: int, **kw) -> Reference:
    return Reference(
        n=n, source_type="regulation", regulation_title="نظام", title=f"مرجع {n}",
        relevance="high", ref_id=f"reg:00000000-0000-0000-0000-00000000000{n}", **kw,
    )


def _output(**overrides) -> MergerOutput:
    base = dict(
        synthesis_md=f"الجواب بشأن {FAKE} [1] ثم [2,3] وليس [9].",
        references=[_ref(1), _ref(2), _ref(3), _ref(4)],
        ref_reports={1: [1], 2: [1, 2], 3: [2], 4: [2]},
        confidence="medium",
        gaps=[f"لم يثبت مبلغ {FAKE}", "   ", "لم تحدد مدة التظلم"],
        angle_status=[AngleStatus(angle="A1", status="supported", note=f"العقد {FAKE} يثبته")],
        resolved=[Resolution(topic=f"مدة {FAKE}", reports=[1, 2], decision=f"العبرة بـ {FAKE}", settled=False)],
        angles=[AngleCard(id="A1", kind="strengthen", strength="strong", title=f"وجه {FAKE}", for_party=f"المدعي {FAKE}")],
        left_out=[LeftOutEntry(angle="A2", title=f"وجه {FAKE}", why=f"لا يخص {FAKE}")],
        reports=[
            MergedReportEntry(n=1, title=f"تقرير {FAKE}", mode="full", angles=["A1"], item_id="wi-1", confidence="high"),
            MergedReportEntry(n=2, title="تقرير فشل", mode="case_led", angles=["A3"], item_id=None, ok=False),
            MergedReportEntry(n=3, title="تقرير ثالث", mode="reg_compliance_led", angles=["A4"], item_id="wi-3"),
        ],
        disclaimer_ar="",
        model_used="qwen3.6-plus",
        prompt_version="merger-v1",
        stats={"shared_refs": 2, "dropped_tags": 1, "label": FAKE},
    )
    base.update(overrides)
    return MergerOutput(**base)


def _input(**overrides) -> MergedPublishInput:
    base = dict(
        user_id="user-1",
        conversation_id="conv-1",
        output=_output(),
        original_query=f"سؤال المستخدم عن {FAKE}",
        detail_level="high",
        case_id="case-1",
        message_id="msg-1",
        task_label=f"عنوان {FAKE}",
        describe_query=f"وصف {FAKE}",
    )
    base.update(overrides)
    return MergedPublishInput(**base)


class _Calls:
    def __init__(self) -> None:
        self.create: list[dict] = []
        self.refs: list[dict] = []
        self.decoded: list[str] = []


@pytest.fixture
def calls(monkeypatch) -> _Calls:
    rec = _Calls()

    def fake_create(supabase, user_id, **kwargs):
        rec.create.append({"supabase": supabase, "user_id": user_id, **kwargs})
        return {"item_id": "wi-merged", "title": kwargs["title"]}

    def fake_refs(supabase, **kwargs):
        rec.refs.append(kwargs)
        return len(kwargs["references"])

    def fake_decode(text: str) -> str:
        rec.decoded.append(text)
        return text.replace(FAKE, REAL)

    monkeypatch.setattr(publisher, "create_workspace_item", fake_create)
    monkeypatch.setattr(publisher, "persist_item_references", fake_refs)
    monkeypatch.setattr(publisher, "decode_for_persist", fake_decode)
    return rec


def _publish(inp: MergedPublishInput | None = None):
    deps = SearchPublishDeps(supabase=SimpleNamespace(name="sb"))
    return asyncio.run(publisher.publish_merged_result(inp or _input(), deps))


# --- the row --------------------------------------------------------------


def test_row_is_an_agent_search_item_with_the_extended_subtype(calls) -> None:
    _publish()

    (row,) = calls.create
    assert row["user_id"] == "user-1"
    assert row["kind"] == "agent_search"
    assert row["created_by"] == "agent"
    assert row["agent_family"] == "deep_search"
    assert row["conversation_id"] == "conv-1"
    assert row["case_id"] == "case-1"
    assert row["message_id"] == "msg-1"
    assert row["metadata"]["subtype"] == SUBTYPE == "extended_synthesis"
    assert row["describe_query"] == f"وصف {REAL}"


def test_metadata_has_exactly_the_contract_keys(calls) -> None:
    _publish()

    assert set(calls.create[0]["metadata"]) == _METADATA_KEYS


def test_metadata_values(calls) -> None:
    _publish()

    md = calls.create[0]["metadata"]
    assert md["confidence"] == "medium"
    assert md["detail_level"] == "high"
    assert md["prompt_version"] == "merger-v1"
    assert md["model_used"] == "qwen3.6-plus"
    assert md["ref_count"] == 4
    assert md["cited_count"] == 3  # [1], [2,3]; [9] is not a reference
    assert md["ref_reports"] == {"1": [1], "2": [1, 2], "3": [2], "4": [2]}
    assert md["stats"] == {"shared_refs": 2, "dropped_tags": 1, "label": FAKE}
    assert md["gaps"] == [f"لم يثبت مبلغ {REAL}", "لم تحدد مدة التظلم"]
    assert md["reports"][1] == {
        "n": 2, "title": "تقرير فشل", "mode": "case_led", "angles": ["A3"],
        "item_id": None, "confidence": None, "ok": False,
    }
    assert md["resolved"][0]["reports"] == [1, 2]
    assert md["resolved"][0]["settled"] is False


def test_children_skip_reports_without_an_item(calls) -> None:
    _publish()

    assert calls.create[0]["metadata"]["children"] == ["wi-1", "wi-3"]


# --- store-real invariant -------------------------------------------------


def test_decode_reaches_content_and_every_metadata_text_field(calls) -> None:
    _publish()

    row = calls.create[0]
    md = row["metadata"]
    assert row["title"] == f"عنوان {REAL}"
    assert row["content_md"] == f"الجواب بشأن {REAL} [1] ثم [2,3] وليس [9]."
    assert md["reports"][0]["title"] == f"تقرير {REAL}"
    assert md["angles"][0]["title"] == f"وجه {REAL}"
    assert md["angles"][0]["for_party"] == f"المدعي {REAL}"
    assert md["angle_status"][0]["note"] == f"العقد {REAL} يثبته"
    assert md["resolved"][0]["topic"] == f"مدة {REAL}"
    assert md["resolved"][0]["decision"] == f"العبرة بـ {REAL}"
    assert md["left_out"][0]["title"] == f"وجه {REAL}"
    assert md["left_out"][0]["why"] == f"لا يخص {REAL}"
    assert md["gaps"][0] == f"لم يثبت مبلغ {REAL}"
    # Nothing masked is left anywhere in the stored text (stats aside — counters).
    stored = repr({k: v for k, v in md.items() if k != "stats"}) + row["title"] + row["content_md"]
    assert FAKE not in stored


def test_ids_and_enums_are_never_decoded(calls) -> None:
    out = _output(
        reports=[MergedReportEntry(n=1, title="t", mode=FAKE, angles=[FAKE], item_id=FAKE)],
        angles=[AngleCard(id=FAKE, kind=FAKE, strength=FAKE, title="t")],
        angle_status=[AngleStatus(angle=FAKE, status="open", note="n")],
        left_out=[LeftOutEntry(angle=FAKE)],
    )
    _publish(_input(output=out))

    md = calls.create[0]["metadata"]
    assert md["reports"][0]["mode"] == FAKE
    assert md["reports"][0]["angles"] == [FAKE]
    assert md["reports"][0]["item_id"] == FAKE
    assert md["children"] == [FAKE]
    assert md["angles"][0]["id"] == FAKE
    assert md["angles"][0]["kind"] == FAKE
    assert md["angles"][0]["strength"] == FAKE
    assert md["angle_status"][0]["angle"] == FAKE
    assert md["left_out"][0]["angle"] == FAKE
    assert FAKE not in calls.decoded  # no decode call ever saw a bare id / enum


# --- title ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("task_label", "original_query", "expected"),
    [
        ("عنوان الموجّه", "سؤال", "عنوان الموجّه"),
        (None, "سؤال المستخدم", "سؤال المستخدم"),
        ("   ", "سؤال المستخدم", "سؤال المستخدم"),
        (None, "", "بحث موسّع"),
        ("", "  ", "بحث موسّع"),
        ("ع" * 120, "سؤال", "ع" * 80),
        (None, "س" * 120, "س" * 80),
    ],
)
def test_title_fallbacks(calls, task_label, original_query, expected) -> None:
    _publish(_input(task_label=task_label, original_query=original_query))

    assert calls.create[0]["title"] == expected


def test_title_is_decoded_before_the_cut(calls) -> None:
    label = "ع" * 76 + FAKE  # the fake straddles char 80
    _publish(_input(task_label=label))

    assert calls.create[0]["title"] == ("ع" * 76 + REAL)[:80]
    assert label in calls.decoded


# --- content --------------------------------------------------------------


def test_no_disclaimer_means_the_answer_only(calls) -> None:
    _publish(_input(output=_output(synthesis_md="الجواب [1]\n\n", disclaimer_ar="")))

    assert calls.create[0]["content_md"] == "الجواب [1]"


def test_disclaimer_is_appended_when_set(calls) -> None:
    _publish(_input(output=_output(synthesis_md="الجواب [1]", disclaimer_ar=" تنبيه عام ")))

    assert calls.create[0]["content_md"] == "الجواب [1]\n\n---\n\nتنبيه عام"


# --- references -----------------------------------------------------------


def test_refs_persisted_with_the_cited_numbers_and_no_sub_queries(calls) -> None:
    inp = _input()
    _publish(inp)

    (refs,) = calls.refs
    assert refs["wi_id"] == "wi-merged"
    assert [r.n for r in refs["references"]] == [1, 2, 3, 4]
    assert refs["cited_numbers"] == [1, 2, 3]
    assert refs["ref_to_sub_queries"] == {}
    assert refs["ura_results"] is None  # none given


def test_ura_results_are_flattened(calls) -> None:
    row_a, row_b, row_c = (SimpleNamespace(ref_id=x) for x in ("a", "b", "c"))
    ura = SimpleNamespace(high_results=[row_a], medium_results=[row_b])
    _publish(_input(ura_results=[ura, row_c]))

    assert calls.refs[0]["ura_results"] == [row_a, row_b, row_c]


def test_a_refs_persist_failure_does_not_raise(calls, monkeypatch) -> None:
    def boom(supabase, **kwargs):
        raise RuntimeError("refs down")

    monkeypatch.setattr(publisher, "persist_item_references", boom)

    result = _publish()

    assert result.item_id == "wi-merged"


def test_missing_item_id_raises_and_persists_no_refs(calls, monkeypatch) -> None:
    monkeypatch.setattr(publisher, "create_workspace_item", lambda supabase, user_id, **kw: {"title": "x"})

    with pytest.raises(RuntimeError, match="no item_id"):
        _publish()
    assert calls.refs == []


def test_a_create_failure_propagates(calls, monkeypatch) -> None:
    def boom(supabase, user_id, **kwargs):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(publisher, "create_workspace_item", boom)

    with pytest.raises(RuntimeError, match="insert failed"):
        _publish()


# --- SSE ------------------------------------------------------------------


def test_sse_event_shape(calls) -> None:
    result = _publish()

    assert result.item_id == "wi-merged"
    assert result.sse_events == [
        {
            "type": "workspace_item_created",
            "item_id": "wi-merged",
            "kind": "agent_search",
            "title": f"عنوان {REAL}",
            "subtype": "extended_synthesis",
            "created_by": "agent",
            "primary": True,
        }
    ]
