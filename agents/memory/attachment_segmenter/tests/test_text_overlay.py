"""The user's text corrections reach the segmenter's MODEL only (attachment_agent.md §3.3).

The model reads the corrected paragraphs (so a re-segment re-derives «what» / owner
from the fixed name); the stored section rows are still sliced from the REAL
``content_md``, and ``content_chars`` stays the real length — a correction must never
make the map stale. No rows / a failed read → today's message. Invented text only.
"""
from __future__ import annotations

import pytest

from agents.memory.attachment_segmenter import runner as runner_mod
from agents.memory.attachment_segmenter.runner import run_attachment_bundle, run_attachment_segmenter
from shared.attachments import text_corrections as tc
from shared.files import flags

from .fake_supabase import FakeSupabase
from .test_bundles import make_db, per_file, text
from .test_runner import ITEM, USER, _line, _row, assert_sections_cover, use, whole

T = "attachment_text_corrections"
NAME_LINE = "حضر محمود الجلسة وقال محمود إن المبلغ سُدد كاملاً في موعده"


def _fix(file_id: str) -> dict:
    return {"id": "c1", "file_id": file_id, "user_id": USER, "wrong_text": "محمود", "right_text": "محمد",
            "occurrences": 2, "evidence": "صحح الاسم", "created_at": "2026-11-27T10:00:00+00:00",
            "reverted_at": None}


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    from shared.config import get_settings

    # the attachment agent's flag ON (the overlay's gate); the flag-off test turns it off
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", True)
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_USERS", None)
    monkeypatch.setattr(runner_mod, "segmenter_enabled", lambda uid: True)
    monkeypatch.setattr(runner_mod, "_codec_for", lambda supabase, uid: None)
    tc.reset_backoff()
    flags.reset_probe()
    with flags.override(read=False, write=False):
        yield
    tc.reset_backoff()
    flags.reset_probe()


def _content() -> str:
    lines = [_line() for _ in range(40)]
    lines[4] = NAME_LINE + " " + lines[4]
    lines[30] = NAME_LINE + " " + lines[30]
    return "\n".join(lines)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fixed", "none", "down"])
async def test_the_model_reads_the_fixes_the_rows_keep_the_real_text(monkeypatch, mode):
    content = _content()
    db = FakeSupabase(
        {"workspace_items": [_row(content)], "attachment_pages": [],
         T: [_fix(ITEM)] if mode == "fixed" else []},
        fail={T} if mode == "down" else None,
    )
    script = use(monkeypatch, whole)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "ok", res
    prompt = script.calls[0]["prompt"]
    if mode == "fixed":
        assert "حضر محمد الجلسة وقال محمد إن" in prompt and "محمود" not in prompt
    else:
        assert prompt.count("حضر محمود الجلسة") == 2
    # stored: the REAL text, sliced at the real offsets; the real length
    rows = assert_sections_cover(db, content)
    assert sum(r["content_md"].count("محمود") for r in rows) == 4
    [m] = db.tables["attachment_segment_maps"]
    assert m["content_chars"] == len(content) and res.chars == len(content)


@pytest.mark.asyncio
async def test_a_split_run_cuts_where_it_would_without_the_fix(monkeypatch):
    """Offsets are kept on the corrected copies, so the parts are cut identically."""
    content = _content()
    prompts: dict[str, list[str]] = {}
    for mode in ("none", "fixed"):
        db = FakeSupabase({"workspace_items": [_row(content)], "attachment_pages": [],
                           T: [_fix(ITEM)] if mode == "fixed" else []})
        script = use(monkeypatch, whole)
        res = await run_attachment_segmenter(db, ITEM, parts=3)
        assert res.outcome == "ok" and res.parts == 3
        prompts[mode] = [c["prompt"] for c in script.calls]
    for before, after in zip(prompts["none"], prompts["fixed"]):
        assert after == before.replace("محمود", "محمد")


@pytest.mark.asyncio
async def test_a_bundle_fixes_only_its_own_members_text(monkeypatch):
    one = NAME_LINE + "\n" + text(2_500)
    two = NAME_LINE + "\n" + text(2_500)
    db = make_db([("s1", one, "letter"), ("s2", two, "letter")])
    db.tables[T] = [_fix("s2")]
    script = use(monkeypatch, per_file)
    res = await run_attachment_bundle(db, ["s1", "s2"], preview=True)
    assert all(r.outcome == "ok" for r in res.values()), res
    prompt = script.calls[0]["prompt"]
    assert prompt.count("حضر محمود الجلسة") == 1 and prompt.count("حضر محمد الجلسة") == 1
    assert prompt.index("حضر محمود الجلسة") < prompt.index("حضر محمد الجلسة")   # file 1 untouched
    for item_id, content in (("s1", one), ("s2", two)):
        segs = res[item_id].preview["segments"]
        assert all(s["content_md"] == content[s["char_start"]:s["char_end"]] for s in segs)
        assert res[item_id].preview["map_row"]["content_chars"] == len(content)


@pytest.mark.asyncio
async def test_flag_off_the_model_reads_the_stored_text_and_nothing_is_read(monkeypatch):
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", False)
    content = _content()
    db = FakeSupabase({"workspace_items": [_row(content)], "attachment_pages": [], T: [_fix(ITEM)]})
    script = use(monkeypatch, whole)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "ok" and script.calls[0]["prompt"].count("حضر محمود الجلسة") == 2
    assert [x for x in db.log if x[0] == T] == []
