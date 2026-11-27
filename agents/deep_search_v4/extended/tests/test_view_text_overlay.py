"""«بحث شامل» marked text carries the user's text corrections (attachment_agent.md §3.3).

Applied AFTER marking: ``marked_text`` compares the paragraphs' offsets with the
sections' stored offsets (``_paragraphs_fit``), so it must run on the REAL text — the
markers fall exactly where they did, ``content_chars`` stays the real length, and only
the words change. No rows / a failed read → today's view. Invented text only.
"""
from __future__ import annotations

import pytest

from agents.deep_search_v4.extended.view import build_planner_view
from agents.memory.angle_mapper.tests import fixtures as F
from shared.attachments import text_corrections as tc
from shared.files import flags

from .test_planning_view import stored

T = "attachment_text_corrections"
WRONG, RIGHT = "بتحويلات", "بحوالات"


def _fix() -> dict:
    return {"id": "c1", "file_id": F.ITEM_A, "user_id": F.USER, "wrong_text": WRONG, "right_text": RIGHT,
            "occurrences": 1, "evidence": "صحح الكلمة", "created_at": "2026-11-27T10:00:00+00:00",
            "reverted_at": None}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from shared.config import get_settings

    # the attachment agent's flag ON (the overlay's gate); the flag-off test turns it off
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", True)
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_USERS", None)
    tc.reset_backoff()
    flags.reset_probe()
    with flags.override(read=False, write=False):
        yield
    tc.reset_backoff()
    flags.reset_probe()


def _markers(text: str) -> list[str]:
    return [b for b in text.split("\n\n") if b.startswith("〔")]


@pytest.mark.parametrize("mode", ["fixed", "none", "down"])
def test_marked_text_after_marking(mode):
    db, amap = stored()
    baseline = build_planner_view(db, amap, user_id=F.USER)
    if mode == "fixed":
        db.tables[T] = [_fix()]
    elif mode == "down":
        db.tables[T] = [_fix()]
        db.fail = {T}
    view = build_planner_view(db, amap, user_id=F.USER)
    before, after = baseline.documents[0].marked_text, view.documents[0].marked_text
    assert WRONG in before
    if mode == "fixed":
        assert after == before.replace(WRONG, RIGHT) and WRONG not in after
    else:
        assert after == before
    # the markers fall where they did (offsets checked on the REAL text) …
    assert _markers(after) == _markers(before) and len(_markers(after)) == 6
    # … the length that drives freshness is the real one, and D2 is untouched
    assert view.documents[0].content_chars == len(F.content(F.JUDGMENT))
    assert view.documents[1].marked_text == baseline.documents[1].marked_text


def test_flag_off_marked_text_is_todays_and_nothing_is_read(monkeypatch):
    from shared.config import get_settings

    db, amap = stored()
    baseline = build_planner_view(db, amap, user_id=F.USER)
    db.tables[T] = [_fix()]
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", False)
    before = len(db.ops(T))
    view = build_planner_view(db, amap, user_id=F.USER)
    assert view.documents[0].marked_text == baseline.documents[0].marked_text
    assert len(db.ops(T)) == before
