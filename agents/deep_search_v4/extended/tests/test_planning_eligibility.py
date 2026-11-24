"""The flag and the eligibility check (D30, D23, D24, D33): every reason, the mode, the
map choice, and never raising."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agents.deep_search_v4.extended import budget as B
from agents.deep_search_v4.extended import eligibility as EL
from agents.deep_search_v4.extended.eligibility import check_eligibility
from agents.memory.angle_mapper.store import write_angle_map
from agents.memory.angle_mapper.tests import fixtures as F
from agents.memory.angle_mapper.tests.fake_supabase import FakeSupabase
from agents.memory.attachment_segmenter.paragraphs import PARAGRAPH_RULE_VERSION
from agents.utils import extended_search_flags as flags
from shared.quota import DeepSearchGate

NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)


def gate(plan: str | None = "pro", *lefts: float) -> DeepSearchGate:
    windows = tuple(("session" if i == 0 else "weekly", float(x), NOW) for i, x in enumerate(lefts))
    return DeepSearchGate(plan_id=plan, min_points=1.01, per_window=None, windows=windows, window_start=NOW)


@pytest.fixture
def flag_on(monkeypatch):
    def _set(enabled: bool = True, users: str | None = None):
        monkeypatch.setattr(flags, "get_settings",
                            lambda: SimpleNamespace(EXTENDED_SEARCH_ENABLED=enabled, EXTENDED_SEARCH_USERS=users))
    _set()
    return _set


def stored_group(*docs: F.Doc, n_angles: int = 3, user_id: str = F.USER) -> FakeSupabase:
    docs = docs or (F.JUDGMENT, F.OBJECTION)
    db = FakeSupabase(F.tables(*docs))
    members = [
        {"item_id": d.item_id, "doc_index": n, "content_chars": len(F.content(d)),
         "segment_prompt_version": F.SEG_VERSION, "paragraph_rule_version": PARAGRAPH_RULE_VERSION,
         "segmented_at": F.SEG_CREATED_AT}
        for n, d in enumerate(docs, start=1)
    ]
    angles = [
        {"seq": i, "for_side": "respondent", "for_party": "المدعى عليه", "kind": "strengthen", "title": f"وجه {i}",
         "argument": "حجة", "sections": ["D1.S3"], "legal_question": "سؤال؟", "strength": "strong", "weakness": ""}
        for i in range(1, n_angles + 1)
    ]
    map_id = write_angle_map(db, message_id=F.MSG, conversation_id=F.CONVO, user_id=user_id, items=members,
                             map_row={"prompt_version": "angles-v5", "client": dict(F.CLIENT_TWO)}, angles=angles)
    assert map_id
    return db


IDS = [F.ITEM_A, F.ITEM_B]


# ── the flag ─────────────────────────────────────────────────────────────────


def test_the_flag_is_off_by_default():
    from shared.config import Settings

    assert Settings.model_fields["EXTENDED_SEARCH_ENABLED"].default is False
    assert Settings.model_fields["EXTENDED_SEARCH_USERS"].default is None


@pytest.mark.parametrize("enabled, users, user, on", [
    (False, None, "u1", False),
    (True, None, "u1", True),
    (True, "", "u1", True),
    (True, "u1, u2", "u2", True),
    (True, "u1,u2", "u3", False),
    (True, "u1", None, False),
])
def test_the_flag_and_its_allowlist(flag_on, enabled, users, user, on):
    flag_on(enabled, users)
    assert flags.extended_search_enabled(user) is on


def test_the_flag_is_off_when_settings_fail(monkeypatch):
    monkeypatch.setattr(flags, "get_settings", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert flags.extended_search_enabled("u1") is False


# ── eligibility ──────────────────────────────────────────────────────────────


def test_eligible_with_the_mode_from_the_points_left(flag_on):
    db = stored_group()
    out = check_eligibility(db, user_id=F.USER, gate=gate("pro", 15.0, 60.0), attached_item_ids=IDS)
    assert out.eligible and out.reason == EL.REASON_OK
    assert out.mode is B.MODES[20] and out.points_left == 15.0 and out.plan_id == "pro"
    assert out.angle_map["map"]["user_id"] == F.USER and len(out.angle_map["angles"]) == 3
    assert out.covered == 2
    assert db.writes()[-1] == ("attachment_angle_map_items", "insert")  # nothing written by the check


def test_unlimited_and_over_fifty_are_no_budget(flag_on):
    db = stored_group()
    assert check_eligibility(db, user_id=F.USER, gate=gate("max"), attached_item_ids=IDS).mode is B.NO_BUDGET
    assert check_eligibility(db, user_id=F.USER, gate=gate("max", 80.0), attached_item_ids=IDS).mode is B.NO_BUDGET


def test_flag_off(flag_on):
    flag_on(False)
    out = check_eligibility(stored_group(), user_id=F.USER, gate=gate("pro", 15.0), attached_item_ids=IDS)
    assert (out.eligible, out.reason) == (False, EL.REASON_FLAG_OFF)


def test_no_gate(flag_on):
    out = check_eligibility(stored_group(), user_id=F.USER, gate=None, attached_item_ids=IDS)
    assert (out.eligible, out.reason) == (False, EL.REASON_NO_GATE)


@pytest.mark.parametrize("plan", ["free", "basic", None])
def test_plans_outside_d24(flag_on, plan):
    out = check_eligibility(stored_group(), user_id=F.USER, gate=gate(plan, 15.0), attached_item_ids=IDS)
    assert not out.eligible and out.reason.startswith(EL.REASON_PLAN)


def test_under_ten_points_left(flag_on):
    out = check_eligibility(stored_group(), user_id=F.USER, gate=gate("max", 9.5, 300.0), attached_item_ids=IDS)
    assert (out.eligible, out.reason, out.points_left) == (False, EL.REASON_LOW_POINTS, 9.5)


def test_no_attachments(flag_on):
    out = check_eligibility(stored_group(), user_id=F.USER, gate=gate("pro", 15.0), attached_item_ids=[])
    assert (out.eligible, out.reason) == (False, EL.REASON_NO_ATTACHMENTS)


def test_no_map(flag_on):
    db = FakeSupabase(F.tables(F.JUDGMENT, F.OBJECTION))
    out = check_eligibility(db, user_id=F.USER, gate=gate("pro", 15.0), attached_item_ids=IDS)
    assert (out.eligible, out.reason) == (False, EL.REASON_NO_MAP)


def test_a_stale_map_is_no_map(flag_on):
    db = stored_group()
    row = next(r for r in db.tables["workspace_items"] if r["item_id"] == F.ITEM_A)
    row["content_md"] += "\nسطر جديد"
    seg = next(r for r in db.tables["attachment_segment_maps"] if r["item_id"] == F.ITEM_A)
    seg["content_chars"] = len(row["content_md"])
    out = check_eligibility(db, user_id=F.USER, gate=gate("pro", 15.0), attached_item_ids=[F.ITEM_A])
    assert (out.eligible, out.reason) == (False, EL.REASON_NO_MAP)


def test_one_angle_is_too_few(flag_on):
    out = check_eligibility(stored_group(n_angles=1), user_id=F.USER, gate=gate("pro", 15.0), attached_item_ids=IDS)
    assert (out.eligible, out.reason) == (False, EL.REASON_FEW_ANGLES)


def test_the_map_covering_most_items_wins(flag_on, monkeypatch):
    from agents.memory.angle_mapper import store

    small = {"map": {"map_id": "m-small", "user_id": F.USER}, "items": [{"item_id": "x1"}], "angles": [{}, {}]}
    big = {"map": {"map_id": "m-big", "user_id": F.USER}, "items": [{"item_id": "x2"}, {"item_id": "x3"}],
           "angles": [{}, {}, {}]}
    by_item = {"x1": small, "x2": big, "x3": big}
    calls: list[str] = []

    def fake(_db, item_id):
        calls.append(item_id)
        return by_item.get(item_id)

    monkeypatch.setattr(store, "load_fresh_angle_map_for_item", fake)
    out = check_eligibility(FakeSupabase(), user_id=F.USER, gate=gate("pro", 15.0), attached_item_ids=["x1", "x2", "x3"])
    assert out.eligible and out.angle_map is big and out.covered == 2
    assert calls == ["x1", "x2"]  # x3 is already inside the map x2 came with


def test_a_map_of_another_user_is_refused(flag_on, monkeypatch):
    from agents.memory.angle_mapper import store

    other = {"map": {"map_id": "m", "user_id": F.OTHER_USER}, "items": [{"item_id": "x1"}], "angles": [{}, {}]}
    monkeypatch.setattr(store, "load_fresh_angle_map_for_item", lambda _db, _i: other)
    out = check_eligibility(FakeSupabase(), user_id=F.USER, gate=gate("pro", 15.0), attached_item_ids=["x1"])
    assert (out.eligible, out.reason) == (False, EL.REASON_OTHER_USER)


def test_never_raises(flag_on, monkeypatch):
    from agents.memory.angle_mapper import store

    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "load_fresh_angle_map_for_item", boom)
    out = check_eligibility(FakeSupabase(), user_id=F.USER, gate=gate("pro", 15.0), attached_item_ids=["x1"])
    assert not out.eligible and out.reason == f"{EL.REASON_ERROR}:RuntimeError"


def test_a_failing_database_is_no_map_not_an_exception(flag_on):
    db = stored_group()
    db.fail = {"attachment_angle_map_items", "attachment_angle_maps"}
    out = check_eligibility(db, user_id=F.USER, gate=gate("pro", 15.0), attached_item_ids=IDS)
    assert (out.eligible, out.reason) == (False, EL.REASON_NO_MAP)
