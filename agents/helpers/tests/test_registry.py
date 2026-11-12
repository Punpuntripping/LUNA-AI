"""The matrix as code (helper_frame.md §4): its shape, and every wait point's names."""
from __future__ import annotations

import dataclasses
import re
import typing
from pathlib import Path

import pytest

import agents.helpers.registry as registry
from agents.helpers.spec import CONSUMERS, Consumer, HelperSpec

AGENTS_DIR = Path(registry.__file__).resolve().parents[1]

# A consumer named at a wait point: ``consumer="x"``, ``.ready("x"`` or a value
# of the orchestrator's family → consumer map.
_WAIT_POINT = re.compile(r"""(?:\bconsumer\s*=\s*|\.ready\(\s*)["'](\w+)["']""")


def _wait_point_names() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in AGENTS_DIR.rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for name in _WAIT_POINT.findall(text):
            found.setdefault(name, []).append(str(path.relative_to(AGENTS_DIR)))
    return found


def test_the_consumer_list_is_the_literal():
    assert CONSUMERS == typing.get_args(Consumer)
    assert len(set(CONSUMERS)) == len(CONSUMERS)


def test_every_consumer_named_at_a_wait_point_exists():
    found = _wait_point_names()
    # The orchestrator's waits are there: router, the analyzer, the dispatches,
    # the resume legs.
    assert {"router", "analyzer", "writer_planner", "deep_planner", "other"} <= set(found)
    unknown = {name: where for name, where in found.items() if name not in CONSUMERS}
    assert unknown == {}


def test_the_family_map_names_known_consumers():
    import agents.orchestrator as orch

    for family in ("deep_search", "writing", "simple_search", "artifact_editor", None):
        assert orch._memory_consumer(family) in CONSUMERS


def test_depends_on_names_registered_helpers_without_a_cycle():
    order = [spec.name for spec in registry.topo_order()]
    assert sorted(order) == sorted(registry.REGISTRY)
    for spec in registry.REGISTRY.values():
        for dep in spec.depends_on:
            assert dep in registry.REGISTRY
            assert order.index(dep) < order.index(spec.name)


def test_a_cycle_or_an_unknown_dependency_is_refused():
    before = dict(registry.REGISTRY)
    cyclic = HelperSpec(name="ocr", scope="message", depends_on=("summary",))
    with pytest.raises(ValueError, match="cycle"):
        registry.register(cyclic)
    with pytest.raises(ValueError, match="unknown"):
        registry.register(HelperSpec(name="orphan", scope="item", depends_on=("nope",)))
    assert registry.REGISTRY == before


def test_a_spec_with_a_bad_consumer_is_refused():
    with pytest.raises(ValueError, match="unknown consumers"):
        HelperSpec(name="x", scope="item", critical_for=frozenset({"routr"}))
    with pytest.raises(ValueError, match="both critical"):
        HelperSpec(
            name="x", scope="item",
            critical_for=frozenset({"router"}), covered_upstream=frozenset({"router"}),
        )
    with pytest.raises(ValueError, match="only item helpers"):
        HelperSpec(name="x", scope="message", stored=True)


# The owner's matrix (helper_frame.md §3–§4, §7 defaults).


@pytest.mark.parametrize("name", ["ocr", "summary", "pre_router_memory"])
def test_the_router_steps_are_critical_for_the_router_only(name):
    spec = registry.REGISTRY[name]
    assert spec.critical_for == {"router"}
    assert spec.covered_upstream == set(CONSUMERS) - {"router"}


def test_the_memory_agent_is_critical_for_everyone_but_the_router():
    spec = registry.REGISTRY["memory"]
    assert not spec.waits("router")
    for consumer in ("deep_planner", "writer_planner", "analyzer", "writer", "other"):
        assert spec.waits(consumer)
    # Expanders and the aggregator run inside the deep planner's call, which waited.
    assert spec.covered_upstream == {"case_expander", "reg_expander", "aggregator"}
    assert spec.run is None  # started by the orchestrator, adopted by the turn


def test_the_summary_follows_ocr_and_runs_in_parallel():
    spec = registry.REGISTRY["summary"]
    assert spec.scope == "item"
    assert spec.depends_on == ("ocr",)
    assert spec.concurrency and spec.concurrency > 1


def test_ceilings():
    import agents.orchestrator as orch

    assert registry.REGISTRY["ocr"].ceiling_s == 180.0
    assert registry.REGISTRY["memory"].ceiling_s == orch.MEMORY_WRITER_CEILING_S == 60.0
    assert registry.REGISTRY["summary"].ceiling_s is not None
    assert registry.REGISTRY["pre_router_memory"].ceiling_s is not None


def test_ocr_and_the_pre_router_hook_never_start_by_themselves():
    assert registry.REGISTRY["pre_router_memory"].auto_start is False
    assert registry.REGISTRY["memory"].auto_start is False
    # OCR has no dependencies, so nothing could start it; the orchestrator does.
    assert registry.REGISTRY["ocr"].depends_on == ()


def test_existing_helpers_keep_their_metadata_markers():
    """OCR / summary keep ocr_status / summary_attempt; helper_runs is for new helpers."""
    existing = ("ocr", "summary", "pre_router_memory", "memory")
    assert not any(registry.REGISTRY[name].stored for name in existing)


def test_segmenter_is_a_stored_item_helper_critical_for_the_four_readers():
    """attachment_segmentation.md D15/D16 — never the router, planners or analyzer."""
    spec = registry.REGISTRY["segmenter"]
    assert spec.stored and spec.scope == "item"
    assert spec.depends_on == ("ocr", "summary")
    assert spec.critical_for == frozenset(
        {"case_expander", "reg_expander", "aggregator", "writer"}
    )
    assert not spec.critical_for & {"router", "deep_planner", "writer_planner", "analyzer"}
    assert spec.ceiling_s == 300.0


def test_register_and_unregister_round_trip():
    spec = HelperSpec(name="probe", scope="item", depends_on=("summary",))
    registry.register(spec)
    try:
        assert registry.dependents("summary")[-1].name == "probe"
        with pytest.raises(ValueError, match="dependents"):
            registry.unregister("summary")
    finally:
        registry.unregister("probe")
    assert "probe" not in registry.REGISTRY


def test_specs_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        registry.REGISTRY["ocr"].ceiling_s = 1.0  # type: ignore[misc]
