"""The helper frame's ANGLE_MAP row (angles-v4: started per item, maps the item's
message group), the on/off gate and its settings."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import agents.helpers.registry as registry
from agents.helpers.spec import CONSUMERS, HelperContext
from agents.memory import angle_mapper as pkg
from agents.memory.angle_mapper.prompts import PROMPT_VERSION
from agents.utils import angle_map_flags, segmenter_flags
from shared.config import Settings


def ctx(item_id: str = "item-1") -> HelperContext:
    return HelperContext(helper="angle_map", supabase=object(), user_id="u1", item_id=item_id)


# ── the registry row ─────────────────────────────────────────────────────────


def test_angle_map_is_a_stored_item_helper_after_the_segmenter():
    spec = registry.REGISTRY["angle_map"]
    assert spec is registry.ANGLE_MAP
    assert spec.scope == "item" and spec.stored and spec.auto_start
    assert spec.depends_on == ("segmenter",)
    assert spec.own_ledger_scope  # the run lands its cost in its own llm_calls scope
    assert spec.max_attempts == 2 and spec.concurrency == registry.ANGLE_MAP_CONCURRENCY
    # the version is the map's prompt AND its input's (the segmenter's prompt + paragraph rule)
    assert spec.version == f"{PROMPT_VERSION}/{registry.SEGMENTER.version}"
    assert spec.version.startswith("angles-v5/seg-v") and registry.SEGMENTER.version != "unavailable"
    assert spec.attempts_count_failures_only is True  # only failures in a row use the attempts up
    assert spec.fallback == "the planner plans without angles"


def test_lease_and_concurrency_fit_a_group_call():
    from agents.memory.angle_mapper.runner import CALL_TIMEOUT_S, MAX_ATTEMPTS

    spec = registry.ANGLE_MAP
    # one call (480 s) must fit inside a lease renewed every third of it
    assert spec.lease_s >= CALL_TIMEOUT_S and MAX_ATTEMPTS == 2
    # members that join a group's run each hold a slot while they wait on it
    assert registry.ANGLE_MAP_CONCURRENCY >= 4


def test_topo_order_puts_it_after_the_segmenter_and_its_inputs():
    order = [s.name for s in registry.topo_order()]
    for before in ("ocr", "summary", "segmenter"):
        assert order.index(before) < order.index("angle_map")
    assert registry.ANGLE_MAP in registry.dependents("segmenter")
    assert registry.dependents("angle_map") == []


def test_critical_for_no_consumer():
    spec = registry.ANGLE_MAP
    assert spec.critical_for == frozenset() and spec.covered_upstream == frozenset()
    assert spec.ceiling_s is None
    assert [c for c in CONSUMERS if spec.waits(c)] == []


def test_load_is_the_fresh_group_loader(monkeypatch):
    seen: list = []
    monkeypatch.setattr(pkg, "load_fresh_angle_map_for_item",
                        lambda sb, item_id: seen.append((sb, item_id)) or {"map": 1})
    sb = object()
    assert registry.ANGLE_MAP.load is registry.load_angle_map
    assert registry.ANGLE_MAP.load(sb, "item-9") == {"map": 1}
    assert seen == [(sb, "item-9")]


def test_enabled_is_the_angle_map_gate(monkeypatch):
    monkeypatch.setattr(angle_map_flags, "angle_map_enabled", lambda uid: uid == "u1")
    assert registry.ANGLE_MAP.enabled("u1") is True
    assert registry.ANGLE_MAP.enabled("u2") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, expected", [(True, True), (False, False), (None, False)])
async def test_applies_to_asks_the_runner_guards(monkeypatch, answer, expected):
    seen: list = []
    monkeypatch.setattr(pkg, "angle_mapper_applies_to_item", lambda sb, item_id: seen.append(item_id) or answer)
    assert await registry.ANGLE_MAP.applies_to(ctx("item-3")) is expected
    assert seen == ["item-3"]


@pytest.mark.asyncio
async def test_applies_to_never_raises(monkeypatch):
    def boom(sb, item_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(pkg, "angle_mapper_applies_to_item", boom)
    assert await registry.ANGLE_MAP.applies_to(ctx()) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [
    "stored", "preview", "joined_group", "skipped_group_pending", "skipped_fresh", "skipped_too_large",
    "skipped_stale", "skipped_no_map", "skipped_disabled", "skipped_not_found",
])
async def test_run_returns_what_did_not_fail(monkeypatch, outcome):
    """A member that joined another member's run, or waits for a pending one, is
    done — never a failure the frame would retry."""
    result = SimpleNamespace(outcome=outcome, error="")

    async def fake(sb, item_id, **kw):
        assert item_id == "item-4" and kw == {}
        return result

    monkeypatch.setattr(pkg, "run_angle_mapper", fake)
    assert await registry.ANGLE_MAP.run(ctx("item-4")) is result


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome, error", [
    ("failed_llm", "TimeoutError"), ("failed_invalid", "UnexpectedModelBehavior"), ("failed_store", ""),
    ("error", "KeyError"),
])
async def test_run_raises_on_a_failed_map_so_the_frame_retries(monkeypatch, outcome, error):
    async def fake(sb, item_id, **kw):
        return SimpleNamespace(outcome=outcome, error=error)

    monkeypatch.setattr(pkg, "run_angle_mapper", fake)
    with pytest.raises(registry.AngleMapFailed, match=f"^{outcome}:{error}$"):
        await registry.ANGLE_MAP.run(ctx())


@pytest.mark.asyncio
async def test_a_joined_member_of_a_failed_group_run_is_not_retried(monkeypatch):
    """Only the member that started the group run carries its failure (and its
    retries); a member that joined it is done."""
    result = SimpleNamespace(outcome="joined_group", error="group:failed_llm")

    async def fake(sb, item_id, **kw):
        return result

    monkeypatch.setattr(pkg, "run_angle_mapper", fake)
    assert await registry.ANGLE_MAP.run(ctx()) is result


def test_registry_table_documents_the_group_rule():
    doc = registry.__doc__ or ""
    assert "| angle_map           | item    | segmenter  |" in doc
    assert "maps the item's whole MESSAGE group in one" in " ".join(doc.split())


def test_version_survives_a_broken_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "agents.memory.angle_mapper", None)
    assert registry._angle_map_version() == "unavailable"


def test_the_failed_group_comment_says_what_really_retries_it():
    """No sweep job: a failed group waits for a member's segmenter to end again, or
    for the planner's ensure_ready (phase 2) — never "the next start retries"."""
    import inspect

    src = inspect.getsource(registry)
    assert "the next start retries within max_attempts" not in src
    flat = " ".join(src.replace("#", " ").split())
    assert "A FAILED group run is not retried by itself (no sweep job)" in flat
    assert "ensure_ready`` (phase 2, not built yet)" in flat


# ── attempts: only failures in a row count (the real ANGLE_MAP row) ──────────


class _Runs:
    """``helper_runs`` in memory, ``claim`` a compare-and-set on (attempt, status)."""

    def __init__(self) -> None:
        self.row = None

    def get(self, item_id, helper):
        return self.row

    def claim(self, *, item_id, helper, user_id, prev, attempt, lease_until, version):
        from agents.helpers.status import RunRow

        cur = self.row
        if (prev is None) != (cur is None):
            return None
        if prev is not None and (cur.attempt, cur.status) != (prev.attempt, prev.status):
            return None
        self.row = RunRow(item_id, helper, "running", attempt, lease_until, version)
        return self.row

    def renew(self, row, lease_until):
        return True

    def finish(self, row, *, status, error):
        import dataclasses

        if self.row is None or self.row.attempt != row.attempt:
            return False
        self.row = dataclasses.replace(self.row, status=status, error=error, lease_until=None)
        return True


@pytest.mark.asyncio
async def test_skips_joins_and_remaps_never_use_the_attempts_up(monkeypatch):
    """Each run below ends done (skipped / joined / stored): ten in a row never reach
    max_attempts (2) — the count starts over after every done run. Two failures in a
    row do; a new version starts over."""
    import dataclasses

    from agents.helpers.status import auto_run

    outcomes = ["skipped_group_pending", "joined_group", "stored", "skipped_fresh", "skipped_no_map",
                "stored", "joined_group", "skipped_too_large", "stored", "skipped_group_pending"]
    seen: list[int] = []

    async def fake_runner(sb, item_id, **kw):
        seen.append(len(seen))
        outcome = outcomes[len(seen) - 1] if len(seen) <= len(outcomes) else "failed_llm"
        return SimpleNamespace(outcome=outcome, error="")

    monkeypatch.setattr(pkg, "run_angle_mapper", fake_runner)
    attempts: list[int] = []

    async def run(c: HelperContext):
        attempts.append(c.attempt)
        return await registry.run_angle_map(c)  # the real outcome → done / failed mapping

    spec = dataclasses.replace(registry.ANGLE_MAP, run=run, load=lambda sb, item_id: None, applies_to=None,
                               enabled=None, own_ledger_scope=False)
    store = _Runs()
    for _ in outcomes:
        out = await auto_run(spec, ctx("item-5"), spec.run, store=store)
        assert out.status == "done"
    assert attempts == [1] * len(outcomes)  # every claim started the count over
    assert store.row.status == "done"

    first = await auto_run(spec, ctx("item-5"), spec.run, store=store)
    second = await auto_run(spec, ctx("item-5"), spec.run, store=store)
    assert (first.status, second.status) == ("failed", "failed") and attempts[-2:] == [1, 2]
    spent = await auto_run(spec, ctx("item-5"), spec.run, store=store)
    assert (spent.status, spent.reason) == ("failed", "attempts_exhausted")
    assert len(attempts) == len(outcomes) + 2  # the spent one never ran

    # a new segmenter (or prompt) version is new input: the attempts start over
    newer = dataclasses.replace(spec, version=spec.version + "-next")
    again = await auto_run(newer, ctx("item-5"), newer.run, store=store)
    assert again.status == "failed" and attempts[-1] == 1


# ── the gate ─────────────────────────────────────────────────────────────────


def _settings(monkeypatch, **values) -> None:
    settings = SimpleNamespace(**values)
    monkeypatch.setattr(angle_map_flags, "get_settings", lambda: settings)
    monkeypatch.setattr(segmenter_flags, "get_settings", lambda: settings)


@pytest.mark.parametrize("on, users, seg_on, seg_users, uid, expected", [
    (False, None, True, None, "u1", False),
    (True, None, True, None, "u1", True),
    (True, "", True, None, "u1", True),            # an empty allowlist = everyone
    (True, " , ", True, None, "u1", True),
    (True, "u1, u2", True, None, "u1", True),
    (True, "u2,u3", True, None, "u1", False),
    (True, "u2", True, None, None, False),
    (True, None, False, None, "u1", False),        # the segmenter off → off
    (True, None, True, "u2", "u1", False),         # the segmenter not on for this user
    (True, "u1", True, "u1,u5", "u1", True),
])
def test_angle_map_enabled(monkeypatch, on, users, seg_on, seg_users, uid, expected):
    _settings(monkeypatch, ANGLE_MAP_ENABLED=on, ANGLE_MAP_USERS=users,
              ATTACHMENT_SEGMENTER_ENABLED=seg_on, ATTACHMENT_SEGMENTER_USERS=seg_users)
    assert angle_map_flags.angle_map_enabled(uid) is expected


def test_settings_without_the_fields_are_off(monkeypatch):
    _settings(monkeypatch, ATTACHMENT_SEGMENTER_ENABLED=True)
    assert angle_map_flags.angle_map_enabled("u1") is False


def test_disabled_by_default():
    for name, default in (("ANGLE_MAP_ENABLED", False), ("ANGLE_MAP_USERS", None)):
        field = Settings.model_fields[name]
        assert field.default is default
        assert field.validation_alias is None and field.alias is None  # env var name == field name


def test_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("no settings")

    monkeypatch.setattr(angle_map_flags, "get_settings", boom)
    assert angle_map_flags.angle_map_enabled("u1") is False

    _settings(monkeypatch, ANGLE_MAP_ENABLED=True, ANGLE_MAP_USERS=None)

    def seg_boom(uid):
        raise RuntimeError("segmenter flag bug")

    monkeypatch.setattr(angle_map_flags, "segmenter_enabled", seg_boom)
    assert angle_map_flags.angle_map_enabled("u1") is False
