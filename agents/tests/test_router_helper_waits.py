"""The router waits for the file helpers and chooses «بحث شامل» (extended_search.md D34).

Owner 2026-11-27: the segmenter, the citation pins and the angle map are critical
for the router on THIS message's files (before the pause check), the client sees
one phrase per step that is still running, a file the router waited on is never
waited on again later in the turn, and the router — not a silent code check —
starts «بحث شامل» with ``DispatchAgent(extended=True)``.

Offline; ``asyncio.run`` (CI has no pytest-asyncio).
"""
from __future__ import annotations

import asyncio
import dataclasses
import time
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import ModelRetry

import agents.helpers as helper_frame
import agents.helpers._runtime as runtime
import agents.orchestrator as orch
import agents.router.router as rr
from agents.deep_search_v4.extended import eligibility as elig_mod
from agents.deep_search_v4.extended import router_view as rv
from agents.helpers import Readiness, TurnHelpers, bind_turn, ensure_ready, unbind_turn
from agents.helpers.tests.test_status import FakeStore, Segmenter, _registry
from agents.models import ChatResponse, DispatchAgent
from agents.tests.test_conversation_memory_router import (  # noqa: F401 - fixtures
    USER,
    CONVO,
    _empty_turn,
    _response,
    _Script,
    _turn,
    _runs_done,
    flag_off,
    seen_route,
)
from pydantic_ai.messages import ToolCallPart


@pytest.fixture(autouse=True)
def fresh_runtime(monkeypatch):
    monkeypatch.setattr(runtime, "_INFLIGHT", {})
    monkeypatch.setattr(runtime, "_CONVO_LATEST", {})


@pytest.fixture
def quick_stages(monkeypatch):
    """The waits ON for every user (ROUTER_HELPER_WAITS_*), stages after 10 ms."""
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "ROUTER_HELPER_WAITS_ENABLED", True)
    monkeypatch.setattr(get_settings(), "ROUTER_HELPER_WAITS_USERS", None)
    monkeypatch.setattr(orch, "_STAGE_AFTER_S", 0.01)


# --------------------------------------------------------------------------- #
# The frame: a file waited on once is not waited on again
# --------------------------------------------------------------------------- #


def test_a_file_the_router_waited_on_is_not_waited_on_again():
    """The router missed its ceiling on «a»: the aggregator gets the fallback at
    once and never starts a second run; «b», which the router never waited on,
    is still handled by the aggregator as before."""
    seg, store = Segmenter(delay=0.3), FakeStore()
    reg = _registry(seg.spec(critical_for=frozenset({"router", "aggregator"}), ceiling_s=0.05))

    async def go() -> tuple[Any, Any, float]:
        turn = TurnHelpers(
            supabase=None, user_id=USER, conversation_id=CONVO, message_id="m-1",
            registry=reg, store=store,
        )
        bind_turn(turn)
        try:
            first = await ensure_ready(
                "segmenter", ["a"], consumer="router", supabase=None, user_id=USER,
                store=store, registry=reg,
            )
            turn.mark_waited("segmenter", ["a"])
            started = time.monotonic()
            again = await ensure_ready(
                "segmenter", ["a", "b"], consumer="aggregator", supabase=None, user_id=USER,
                store=store, registry=reg,
            )
            took = time.monotonic() - started
            await asyncio.sleep(0.4)  # let both runs end inside this loop
            return first, again, took
        finally:
            unbind_turn()

    first, again, took = asyncio.run(go())
    assert first == {"a": Readiness.fallback("timeout")}
    assert again["a"] == Readiness.fallback("not_critical")
    assert [c for c in seg.calls if c[0] == "a"] == [("a", 1)]
    assert ("b", 1) in seg.calls
    assert took < 0.25


def test_mark_waited_is_per_helper_and_per_file():
    turn = TurnHelpers(supabase=None, user_id=USER, conversation_id=CONVO)
    turn.mark_waited("segmenter", ["a", None, ""])
    assert turn.waited_for("segmenter", "a")
    assert not turn.waited_for("segmenter", "b")
    assert not turn.waited_for("citation_pins", "a")
    assert not turn.waited_for("segmenter", None)


# --------------------------------------------------------------------------- #
# The pre-router step: waits, stages, one "done"
# --------------------------------------------------------------------------- #


def test_a_quick_wait_shows_no_stage(quick_stages):
    async def quick() -> str:
        return "ok"

    async def go() -> list[dict]:
        return [ev async for ev in orch._wait_with_stage(quick(), "arranging")]

    assert asyncio.run(go()) == []


def test_a_slow_wait_shows_its_stage_once(quick_stages):
    async def slow() -> str:
        await asyncio.sleep(0.05)
        return "ok"

    async def go() -> list[dict]:
        return [ev async for ev in orch._wait_with_stage(slow(), "threads")]

    assert asyncio.run(go()) == [
        {"type": "ocr_progress", "phase": "extracting", "stage": "threads"}
    ]


class _Waits:
    """``ensure_ready`` stand-in: records each call and takes ``delays[helper]``."""

    def __init__(self, **delays: float) -> None:
        self.delays = delays
        self.calls: list[tuple[str, str, tuple[str, ...], float]] = []
        self.ended: dict[str, float] = {}

    async def __call__(self, helper: str, item_ids: Any, *, consumer: str, **_k: Any) -> dict:
        self.calls.append((helper, consumer, tuple(item_ids), time.monotonic()))
        await asyncio.sleep(self.delays.get(helper, 0.0))
        self.ended[helper] = time.monotonic()
        return {i: Readiness.ready("x") for i in item_ids}

    async def in_flight(self, helper: str, item_ids: Any) -> None:
        """``orch._wait_in_flight`` stand-in: the angle map is waited, never started."""
        self.calls.append((helper, "in_flight", tuple(item_ids), time.monotonic()))
        await asyncio.sleep(self.delays.get(helper, 0.0))
        self.ended[helper] = time.monotonic()


def _pre_router(
    monkeypatch, waits: _Waits, *, attachments: list[str], unread: bool, to_map: tuple[str, ...] = (),
) -> tuple:
    """``to_map`` — the «الملفات» copies ``library_items_to_map`` picks for a new map."""
    from agents.memory.angle_mapper import library

    async def fake_ocr(_ctx: Any) -> SimpleNamespace:
        return SimpleNamespace(filled_item_ids=[], failed=0)

    async def pick(_sb: Any, item_ids: Any, *, user_id: str) -> list[str]:
        return [i for i in item_ids if i in to_map]

    monkeypatch.setattr(library, "library_items_to_map", pick)
    monkeypatch.setattr(helper_frame, "ensure_ready", waits)
    monkeypatch.setattr(orch, "_wait_in_flight", waits.in_flight)
    monkeypatch.setattr(orch, "has_unread_attachments", lambda *_a: unread)
    monkeypatch.setattr(orch, "_load_reply_attachment_ids", lambda *_a: list(attachments))

    async def go() -> tuple[list[dict], TurnHelpers]:
        turn = TurnHelpers(
            supabase=_empty_turn(), user_id=USER, conversation_id=CONVO, message_id="m-1",
            runs={"ocr": fake_ocr},
        )
        events = [ev async for ev in orch._run_pre_router_helpers(turn)]
        await asyncio.sleep(0)
        while runtime._BACKGROUND:
            await asyncio.wait(list(runtime._BACKGROUND))
        return events, turn

    return asyncio.run(go())


def test_the_router_waits_for_the_file_helpers_in_order(monkeypatch, quick_stages):
    waits = _Waits(segmenter=0.05, citation_pins=0.1, angle_map=0.05)
    events, turn = _pre_router(monkeypatch, waits, attachments=["f1", "f2"], unread=True)

    helpers = [(h, c, ids) for h, c, ids, _ in waits.calls]
    assert helpers == [
        ("segmenter", "router", ("f1", "f2")),
        ("citation_pins", "router", ("f1", "f2")),
        # Waited for, never started: a «الملفات» file gets no new mapper call.
        ("angle_map", "in_flight", ("f1", "f2")),
    ]
    # The segmenter and the pins side by side; the angles after both.
    starts = {h: t for h, _, _, t in waits.calls}
    assert starts["citation_pins"] < waits.ended["segmenter"]
    assert starts["angle_map"] >= max(waits.ended["segmenter"], waits.ended["citation_pins"])

    stages = [e.get("stage") for e in events if e["type"] == "ocr_progress" and e["phase"] == "extracting"]
    assert stages == ["extracting", "arranging", "organizing", "threads"]
    done = [e for e in events if e["type"] == "ocr_progress" and e["phase"] == "done"]
    assert len(done) == 1 and events[-1] is done[0]

    for helper in ("segmenter", "citation_pins", "angle_map"):
        assert turn.waited_for(helper, "f1") and turn.waited_for(helper, "f2")
    assert turn.message_item_ids == ["f1", "f2"]


def test_a_library_file_with_no_map_is_mapped_and_waited_for(monkeypatch, quick_stages):
    """Owner 2026-11-27: a «الملفات» copy whose file has no map anywhere, on Pro /
    Pro Plus / Max, is mapped — started here through the frame and waited for; the
    message's other files are only waited for."""
    waits = _Waits(angle_map=0.05)
    events, turn = _pre_router(monkeypatch, waits, attachments=["f1", "lib"], unread=False, to_map=("lib",))

    angle = sorted((c, ids) for h, c, ids, _ in waits.calls if h == "angle_map")
    assert angle == [("in_flight", ("f1",)), ("router", ("lib",))]
    stages = [e.get("stage") for e in events if e["type"] == "ocr_progress" and e["phase"] == "extracting"]
    assert stages == ["threads"]
    assert turn.waited_for("angle_map", "f1") and turn.waited_for("angle_map", "lib")


def test_no_library_file_to_map_starts_nothing(monkeypatch, quick_stages):
    waits = _Waits()
    _pre_router(monkeypatch, waits, attachments=["f1", "lib"], unread=False, to_map=())
    assert [(c, ids) for h, c, ids, _ in waits.calls if h == "angle_map"] == [("in_flight", ("f1", "lib"))]


def test_a_failing_library_check_falls_back_to_waiting(monkeypatch):
    """``library_items_to_map`` never raises: a failed read inside it = nothing
    started, the files still waited for."""
    from agents.memory.angle_mapper import library

    def boom(*_a: Any) -> list[str]:
        raise RuntimeError("db down")

    monkeypatch.setattr(library, "_enabled", lambda _user: True)
    monkeypatch.setattr(library, "items_to_map", boom)
    assert asyncio.run(library.library_items_to_map(object(), ["lib"], user_id=USER)) == []


def test_a_message_without_files_waits_for_nothing_and_shows_nothing(monkeypatch, quick_stages):
    waits = _Waits()
    events, turn = _pre_router(monkeypatch, waits, attachments=[], unread=False)
    assert waits.calls == []
    assert events == []
    assert turn.message_item_ids == []


def test_the_angle_map_wait_starts_nothing():
    """No run going for the files (a «الملفات» file with stored sections): the
    wait returns at once and no mapper run is started."""
    async def go() -> float:
        started = time.monotonic()
        await orch._wait_in_flight("angle_map", ["lib-1"])
        return time.monotonic() - started

    assert asyncio.run(go()) < 0.05
    assert runtime._INFLIGHT == {}


def test_the_angle_map_wait_follows_a_running_map_and_never_cancels_it(monkeypatch):
    import agents.helpers.registry as registry

    ended: list[str] = []

    async def mapping(delay: float, name: str) -> None:
        await asyncio.sleep(delay)
        ended.append(name)

    async def go() -> tuple[float, float]:
        runtime.spawn("angle_map", "f1", mapping(0.05, "quick"))
        started = time.monotonic()
        await orch._wait_in_flight("angle_map", ["f1", "f2"])
        quick = time.monotonic() - started
        # Past the ceiling: the wait gives up, the run goes on.
        spec = dataclasses.replace(registry.ANGLE_MAP, ceiling_s=0.05)
        monkeypatch.setattr(registry, "get_spec", lambda name, *a, **k: spec)
        runtime.spawn("angle_map", "f2", mapping(0.3, "slow"))
        started = time.monotonic()
        await orch._wait_in_flight("angle_map", ["f2"])
        slow = time.monotonic() - started
        await asyncio.sleep(0.35)
        return quick, slow

    quick, slow = asyncio.run(go())
    assert 0.04 <= quick < 0.25
    assert slow < 0.25
    assert ended == ["quick", "slow"]  # the slow run was not cancelled


@pytest.mark.parametrize("users", [None, "someone-else"])
def test_flag_off_keeps_todays_step(monkeypatch, quick_stages, users):
    """Off (or on for other users only): no file-helper wait, nothing marked, and
    today's events — «extracting» with no stage, «done» right after OCR."""
    from shared.config import get_settings

    if users is None:
        monkeypatch.setattr(get_settings(), "ROUTER_HELPER_WAITS_ENABLED", False)
    else:
        monkeypatch.setattr(get_settings(), "ROUTER_HELPER_WAITS_USERS", users)
    waits = _Waits(segmenter=0.05)
    events, turn = _pre_router(monkeypatch, waits, attachments=["f1"], unread=True)
    assert waits.calls == []
    assert events == [
        {"type": "ocr_progress", "phase": "extracting"},
        {"type": "ocr_progress", "phase": "done", "notices": []},
    ]
    assert not turn.waited_for("segmenter", "f1")


def test_helpers_that_end_at_once_show_only_the_ocr_line(monkeypatch, quick_stages):
    """A small file (not applicable) or a stored result: no «يرتب»… flicker."""
    waits = _Waits()
    events, _ = _pre_router(monkeypatch, waits, attachments=["f1"], unread=True)
    assert [h for h, *_ in waits.calls] == ["segmenter", "citation_pins", "angle_map"]
    assert [(e["phase"], e.get("stage")) for e in events] == [
        ("extracting", "extracting"), ("done", None),
    ]


# --------------------------------------------------------------------------- #
# The router's «بحث شامل» section and its validator
# --------------------------------------------------------------------------- #


def _view(available: bool = True, reason: str = "eligible") -> rv.RouterExtendedView:
    if not available:
        return rv.RouterExtendedView(available=False, reason=reason)
    return rv.RouterExtendedView(
        available=True,
        reason="eligible",
        files=[("WI-3", "item-3", "لائحة الدعوى"), ("WI-4", "item-4", "محضر الجلسة")],
        angle_titles=["بطلان الإجراءات", "التعويض عن الضرر"],
    )


def _ctx(view: Any, *, retry: int = 0) -> SimpleNamespace:
    deps = SimpleNamespace(
        planner_handback=None,
        wi_alias_map={3: "item-3", 4: "item-4", 7: "item-7"},
        extended_view=view,
    )
    return SimpleNamespace(deps=deps, retry=retry)


def _extended(**over: Any) -> DispatchAgent:
    fields: dict[str, Any] = dict(
        agent_family="deep_search", task_label="دراسة القضية من كل جوانبها", extended=True,
    )
    fields.update(over)
    return DispatchAgent(**fields)


def test_extended_is_dropped_when_the_flag_is_off():
    out = rr._validate_and_resolve_dispatch(_ctx(None), _extended())
    assert out.extended is False


def test_extended_when_not_available_is_refused_once_then_dropped():
    with pytest.raises(ModelRetry, match="not available"):
        rr._validate_and_resolve_dispatch(_ctx(_view(False, "low_points")), _extended())
    out = rr._validate_and_resolve_dispatch(_ctx(_view(False, "low_points"), retry=1), _extended())
    assert out.extended is False


def test_extended_with_no_file_attaches_the_mapped_files():
    out = rr._validate_and_resolve_dispatch(_ctx(_view()), _extended())
    assert out.extended is True
    assert out.attached_wis == ["WI-3", "WI-4"]
    assert out.attached_item_ids == ["item-3", "item-4"]


def test_extended_keeps_the_files_the_router_chose():
    out = rr._validate_and_resolve_dispatch(_ctx(_view()), _extended(attached_wis=["WI-7"]))
    assert out.extended is True and out.attached_item_ids == ["item-7"]


def test_extended_is_deep_search_only():
    out = rr._validate_and_resolve_dispatch(
        _ctx(_view()), _extended(agent_family="writing", task_label="مذكرة دفاع في القضية"),
    )
    assert out.extended is False


def test_an_ordinary_dispatch_is_untouched():
    out = rr._validate_and_resolve_dispatch(_ctx(_view()), _extended(extended=False))
    assert out.extended is False and out.attached_item_ids == []


def test_the_section_is_empty_while_the_flag_is_off():
    assert rv.render_router_section(None) == ""
    assert rr.inject_extended_search(SimpleNamespace(deps=SimpleNamespace(extended_view=None))) == ""


def test_the_section_lists_the_files_and_angles_when_available():
    text = rv.render_router_section(_view())
    assert "available this turn" in text
    assert "WI-3 «لائحة الدعوى»" in text and "WI-4 «محضر الجلسة»" in text
    assert "1. بطلان الإجراءات" in text and "2. التعويض عن الضرر" in text
    assert 'extended=true, attached_wis=["WI-3", "WI-4"]' in text
    assert "never `CheckPriorReports`" in text
    # D32 under D34: a typed change to a plan card is re-planned as a new card.
    assert "«بحث شامل — الخطة المقترحة»" in text


@pytest.mark.parametrize(
    "reason, sentence",
    [
        ("plan_not_eligible:free", rv.UNAVAILABLE_AR["plan_not_eligible"]),
        ("low_points", rv.UNAVAILABLE_AR["low_points"]),
        ("no_fresh_map", rv.UNAVAILABLE_AR["no_fresh_map"]),
        ("error:KeyError", rv.UNAVAILABLE_DEFAULT_AR),
    ],
)
def test_the_section_gives_the_reason_when_not_available(reason, sentence):
    text = rv.render_router_section(_view(False, reason))
    assert "Never set `extended=true`" in text
    assert f"«{sentence}»" in text


# --------------------------------------------------------------------------- #
# The view: same checks as the planning turn, over the conversation's files
# --------------------------------------------------------------------------- #


_SUMMARIES = [
    {"item_id": "item-3", "wi_seq": 3, "kind": "attachment", "title": "لائحة الدعوى"},
    {"item_id": "item-4", "wi_seq": 4, "kind": "attachment", "title": "محضر الجلسة"},
    {"item_id": "rep-1", "wi_seq": 5, "kind": "agent_search", "title": "تقرير سابق"},
]


def _flag(monkeypatch, on: bool) -> None:
    import agents.utils.extended_search_flags as flags

    monkeypatch.setattr(flags, "extended_search_enabled", lambda _u: on)


def test_the_view_is_none_while_the_flag_is_off(monkeypatch):
    _flag(monkeypatch, False)
    called: list = []
    monkeypatch.setattr(elig_mod, "check_eligibility", lambda *a, **k: called.append(1))
    assert rv.router_extended_view(object(), user_id=USER, workspace_item_summaries=_SUMMARIES) is None
    assert called == []


def test_the_view_maps_the_map_members_to_their_aliases(monkeypatch):
    _flag(monkeypatch, True)
    seen: dict = {}

    def fake_check(_db: Any, **kw: Any) -> Any:
        seen.update(kw)
        return elig_mod.Eligibility(
            eligible=True, reason="eligible",
            angle_map={
                "map": {"map_id": "m1"},
                "items": [{"item_id": "item-4"}, {"item_id": "elsewhere"}, {"item_id": "item-3"}],
                "angles": [{"seq": 1, "title": "بطلان الإجراءات"}, {"seq": 2, "title": " "}],
            },
        )

    monkeypatch.setattr(elig_mod, "check_eligibility", fake_check)
    view = rv.router_extended_view(object(), user_id=USER, workspace_item_summaries=_SUMMARIES)
    assert seen["attached_item_ids"] == ["item-3", "item-4"]  # attachments only
    assert view.available
    assert view.files == [("WI-4", "item-4", "محضر الجلسة"), ("WI-3", "item-3", "لائحة الدعوى")]
    assert view.angle_titles == ["بطلان الإجراءات"]


def test_the_view_passes_the_reason_on_when_not_eligible(monkeypatch):
    _flag(monkeypatch, True)
    monkeypatch.setattr(
        elig_mod, "check_eligibility",
        lambda *_a, **_k: elig_mod.Eligibility(eligible=False, reason="too_few_angles"),
    )
    view = rv.router_extended_view(object(), user_id=USER, workspace_item_summaries=_SUMMARIES)
    assert not view.available and view.reason == "too_few_angles"
    assert view.unavailable_ar == rv.UNAVAILABLE_AR["too_few_angles"]


def test_a_failing_view_offers_nothing(monkeypatch):
    _flag(monkeypatch, True)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise KeyError("x")

    monkeypatch.setattr(elig_mod, "check_eligibility", boom)
    view = rv.router_extended_view(object(), user_id=USER, workspace_item_summaries=_SUMMARIES)
    assert not view.available and view.reason.startswith("error:")


# --------------------------------------------------------------------------- #
# The turn: the router's choice reaches the dispatch
# --------------------------------------------------------------------------- #


def _deep_search(extended: bool) -> _Script:
    return _Script(_response(ToolCallPart(
        tool_name="final_result_DispatchAgent",
        args={
            "type": "dispatch", "agent_family": "deep_search",
            "task_label": "دراسة القضية من كل جوانبها",
            "target_wi": None, "attached_wis": [], "subtype": None, "extended": extended,
        },
    )))


@pytest.mark.parametrize("chosen", [True, False])
def test_the_routers_choice_reaches_the_dispatch(chosen, flag_off, seen_route, monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_dispatch(**kwargs: Any) -> Any:
        captured.update(kwargs)
        yield {"type": "done"}

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)
    monkeypatch.setattr(orch, "_router_extended_view", lambda *_a: _view())

    async def go() -> list[dict]:
        events = await _turn(_empty_turn(), _deep_search(chosen))
        await _runs_done()
        return events

    asyncio.run(go())
    assert captured["agent_family"] == "deep_search"
    assert captured["extended_allowed"] is chosen
    if chosen:
        assert captured["attached_item_ids"] == ["item-3", "item-4"]
