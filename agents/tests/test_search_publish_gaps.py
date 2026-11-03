"""A published search report keeps the gaps it reported (plan artifact_analyzer.md §4.9).

``AggregatorOutput.gaps`` otherwise exists only for the turn, and the
artifact_analyzer needs each report's own statement of what it lacks.
"""
from __future__ import annotations

from types import SimpleNamespace

from agents.agent_search import publisher
from agents.deep_search_v4.aggregator.runner import _MODEL_FAILURE_GAP_AR


def _input(*, gaps: object, artifact: object = None) -> SimpleNamespace:
    return SimpleNamespace(
        agg_output=SimpleNamespace(
            gaps=gaps, confidence="medium", log_id="log-1", artifact=artifact,
        ),
        detail_level="medium",
    )


def test_gaps_are_saved_in_order() -> None:
    gaps = ["لم تحدد المراجع مدة التظلم", "لم تظهر بيانات عن مقدار الدين"]

    metadata = publisher._build_metadata(_input(gaps=gaps))

    assert metadata["gaps"] == gaps


def test_run_failure_entries_are_not_report_gaps() -> None:
    real_gap = "لم تحدد المراجع مدة التظلم"

    metadata = publisher._build_metadata(_input(gaps=[
        "no_references_after_reranker", _MODEL_FAILURE_GAP_AR, "   ", real_gap,
    ]))

    assert metadata["gaps"] == [real_gap]


def test_no_gaps_is_an_empty_list() -> None:
    assert publisher._build_metadata(_input(gaps=None))["gaps"] == []
    assert publisher._build_metadata(_input(gaps=[]))["gaps"] == []


def test_gaps_are_decoded_for_storage(monkeypatch) -> None:
    """Under وضع السرية the turn holds fakes; DB rows must not."""
    monkeypatch.setattr(publisher, "decode_for_persist", lambda t: t.replace("FAKE", "REAL"))

    metadata = publisher._build_metadata(_input(gaps=["مبلغ FAKE غير محدد"]))

    assert metadata["gaps"] == ["مبلغ REAL غير محدد"]


def test_existing_metadata_is_untouched() -> None:
    artifact = SimpleNamespace(metadata={"ref_count": 3, "prompt_key": "p"})

    metadata = publisher._build_metadata(_input(gaps=["نقص"], artifact=artifact))

    assert metadata["ref_count"] == 3
    assert metadata["prompt_key"] == "p"
    assert metadata["subtype"] == "legal_synthesis"
    assert metadata["confidence"] == "medium"
    assert metadata["ura_log_id"] == "log-1"
    assert metadata["gaps"] == ["نقص"]
