"""The row overlay (``shared/attachments/overlay.py``, attachment_agent.md §3.3): which
file a row's text fixes hang on, the one-read batching, and the byte-identical /
fail-safe contract every reader hook relies on. Synthetic text only."""
from __future__ import annotations

import pytest

from shared.attachments import text_corrections as tc
from shared.attachments.overlay import (
    correct_rows,
    correct_text,
    corrections_for_item_ids,
    corrections_for_rows,
)
from shared.files import flags
from shared.files.tests.user_files_fake import FakeSupabase

USER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
FIRST = "aaaaaaaa-0000-0000-0000-000000000001"     # a file's first item: file_id = item_id
COPY = "aaaaaaaa-0000-0000-0000-000000000002"      # the same file attached elsewhere
FILE = FIRST
NOTE = "aaaaaaaa-0000-0000-0000-0000000000ff"
TEXT = "حضر محمود الجلسة، وقال محمود إن العقد صحيح."
FIXED = "حضر محمد الجلسة، وقال محمد إن العقد صحيح."
T = "attachment_text_corrections"


def fix_row(file_id: str = FILE, *, user: str = USER, wrong: str = "محمود", right: str = "محمد",
            at: str = "2026-11-27T10:00:00+00:00", reverted: str | None = None, i: int = 1) -> dict:
    return {"id": f"c{i}", "file_id": file_id, "user_id": user, "wrong_text": wrong, "right_text": right,
            "occurrences": 2, "evidence": "صحح الاسم", "created_at": at, "reverted_at": reverted}


def pointer(item_id: str, *, file_id: str | None = FILE, kind: str = "attachment", text: str = TEXT) -> dict:
    return {"item_id": item_id, "user_id": USER, "file_id": file_id, "kind": kind, "content_md": text,
            "deleted_at": None}


def db(*corrections: dict, items: list[dict] | None = None) -> FakeSupabase:
    return FakeSupabase(
        triggers=False,
        workspace_items=items if items is not None else [pointer(FIRST), pointer(COPY), pointer(NOTE, kind="note")],
        attachment_text_corrections=list(corrections),
    )


def reads(sb: FakeSupabase, table: str) -> list[str]:
    """Selects on ``table`` — the files switch's one-column schema probe left out."""
    return [cols for t, cols in sb.selects if t == table and cols != "file_id"]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """The attachment agent's flag ON for everyone (the readers' gate); the flag-off
    tests turn it off themselves."""
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", True)
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_USERS", None)
    tc.reset_backoff()
    flags.reset_probe()
    yield
    tc.reset_backoff()
    flags.reset_probe()


@pytest.fixture(params=[False, True], ids=["switch-off", "switch-on"])
def read_on(request):
    with flags.override(read=request.param, write=False):
        yield request.param


# ── no rows: byte-identical, and no read when there is nothing to read ──────────


def test_no_correction_rows_give_back_the_same_list(read_on):
    rows = [{"item_id": FIRST, "file_id": FILE, "kind": "attachment", "content_md": TEXT}]
    sb = db()
    out = correct_rows(sb, rows, user_id=USER)
    assert out is rows and out[0] is rows[0]
    assert len(reads(sb, T)) == 1


def test_nothing_to_read_costs_no_request(read_on):
    sb = db(fix_row())
    for rows in (
        [],
        [{"item_id": NOTE, "kind": "note", "content_md": TEXT}],            # not an attachment
        [{"item_id": FIRST, "kind": "attachment", "content_md": ""}],        # no text
        [{"item_id": FIRST, "file_id": None, "kind": "attachment", "content_md": TEXT}],  # links no file
    ):
        assert correct_rows(sb, rows, user_id=USER) is rows
    assert sb.selects == []
    assert correct_rows(sb, None) == []


# ── which file ─────────────────────────────────────────────────────────────────


def test_a_row_that_carries_file_id_is_read_by_it_with_no_lookup(read_on):
    sb = db(fix_row())
    rows = [{"item_id": COPY, "file_id": FILE, "kind": "attachment", "content_md": TEXT, "title": "t"}]
    [out] = correct_rows(sb, rows, user_id=USER)
    assert out == {**rows[0], "content_md": FIXED} and rows[0]["content_md"] == TEXT   # a copy
    assert reads(sb, "workspace_items") == [] and len(reads(sb, T)) == 1


def test_switch_on_looks_the_file_up_once_for_rows_without_file_id():
    with flags.override(read=True, write=False):
        sb = db(fix_row())
        rows = [{"item_id": i, "kind": "attachment", "content_md": TEXT} for i in (FIRST, COPY)]
        out = correct_rows(sb, rows, user_id=USER)
    assert [r["content_md"] for r in out] == [FIXED, FIXED]          # the copy follows its file (D15)
    assert reads(sb, "workspace_items") == ["item_id, file_id"] and len(reads(sb, T)) == 1


def test_switch_on_an_unlinked_item_gets_nothing():
    with flags.override(read=True, write=False):
        sb = db(fix_row(file_id=COPY), items=[pointer(COPY, file_id=None)])
        rows = [{"item_id": COPY, "kind": "attachment", "content_md": TEXT}]
        assert correct_rows(sb, rows, user_id=USER) is rows
    assert reads(sb, T) == []


def test_switch_off_keys_by_the_item_and_never_selects_file_id():
    with flags.override(read=False, write=False):
        sb = db(fix_row())
        rows = [{"item_id": i, "kind": "attachment", "content_md": TEXT} for i in (FIRST, COPY)]
        out = correct_rows(sb, rows, user_id=USER)
    # the first item IS the file id; a library copy needs the switch (on in production)
    assert [r["content_md"] for r in out] == [FIXED, TEXT]
    assert reads(sb, "workspace_items") == [] and len(reads(sb, T)) == 1


# ── what is read ───────────────────────────────────────────────────────────────


def test_one_read_for_many_rows_and_only_attachments(read_on):
    sb = db(fix_row())
    rows = [
        {"item_id": FIRST, "file_id": FILE, "kind": "attachment", "content_md": TEXT},
        {"item_id": NOTE, "kind": "note", "content_md": TEXT},
        {"item_id": COPY, "file_id": FILE, "kind": "attachment", "content_md": TEXT},
    ]
    out = correct_rows(sb, rows, user_id=USER)
    assert [r["content_md"] for r in out] == [FIXED, TEXT, FIXED]
    assert out[1] is rows[1]
    assert len(reads(sb, T)) == 1


def test_kinds_none_and_where(read_on):
    sb = db(fix_row())
    rows = [{"item_id": FIRST, "file_id": FILE, "content_md": TEXT, "summary": "s"}]
    assert correct_rows(sb, rows, user_id=USER) is rows                         # no kind: skipped
    assert correct_rows(sb, rows, user_id=USER, kinds=None)[0]["content_md"] == FIXED
    assert correct_rows(sb, rows, user_id=USER, kinds=None, where=lambda r: not r.get("summary")) is rows


def test_other_users_and_reverted_fixes_do_not_apply(read_on):
    sb = db(fix_row(user=OTHER), fix_row(reverted="2026-11-27T11:00:00+00:00", i=2))
    rows = [{"item_id": FIRST, "file_id": FILE, "kind": "attachment", "content_md": TEXT}]
    assert correct_rows(sb, rows, user_id=USER) is rows


def test_fixes_apply_oldest_first(read_on):
    sb = db(
        fix_row(wrong="محمد", right="أحمد", at="2026-11-27T10:05:00+00:00", i=2),
        fix_row(wrong="محمود", right="محمد", at="2026-11-27T10:00:00+00:00", i=1),
    )
    rows = [{"item_id": FIRST, "file_id": FILE, "kind": "attachment", "content_md": TEXT}]
    assert correct_rows(sb, rows, user_id=USER)[0]["content_md"] == TEXT.replace("محمود", "أحمد")


def test_text_keys(read_on):
    sb = db(fix_row())
    rows = [{"item_id": FIRST, "file_id": FILE, "kind": "attachment", "content_md": TEXT, "text": TEXT}]
    [out] = correct_rows(sb, rows, user_id=USER, text_keys=("content_md", "text"))
    assert out["content_md"] == out["text"] == FIXED


def test_corrections_for_rows_by_id(read_on):
    sb = db(fix_row())
    rows = [pointer(COPY), pointer(NOTE, kind="note")]
    out = corrections_for_rows(sb, rows, user_id=USER)
    assert list(out) == [COPY] and [c.wrong for c in out[COPY]] == ["محمود"]
    files = [{"file_id": FILE, "content_md": TEXT}]
    assert list(corrections_for_rows(sb, files, user_id=USER, id_key="file_id", kinds=None)) == [FILE]


# ── failures: the stored text, never an error ──────────────────────────────────


def test_a_failed_read_leaves_the_rows_and_backs_off(read_on):
    sb = db(fix_row())
    sb.fail_tables.add(T)
    rows = [{"item_id": FIRST, "file_id": FILE, "kind": "attachment", "content_md": TEXT}]
    assert correct_rows(sb, rows, user_id=USER) is rows
    sb.fail_tables.clear()
    before = len(sb.selects)
    assert correct_rows(sb, rows, user_id=USER) is rows        # 60 s pause: not even tried
    assert len(sb.selects) == before
    tc.reset_backoff()
    assert correct_rows(sb, rows, user_id=USER)[0]["content_md"] == FIXED


def test_a_failed_file_lookup_leaves_the_rows():
    with flags.override(read=True, write=False):
        sb = db(fix_row())
        assert flags.schema_ready(sb)                  # the switch is on; then the lookup fails
        sb.fail_tables.add("workspace_items")
        rows = [{"item_id": FIRST, "kind": "attachment", "content_md": TEXT}]
        assert correct_rows(sb, rows, user_id=USER) is rows


def test_a_broken_client_or_odd_rows_never_raise(read_on):
    rows = [{"item_id": FIRST, "file_id": FILE, "kind": "attachment", "content_md": TEXT}, "x", None]
    assert correct_rows(object(), rows, user_id=USER) is rows
    assert correct_rows(None, rows, user_id=USER) is rows


# ── one text ───────────────────────────────────────────────────────────────────


def test_correct_text(read_on):
    sb = db(fix_row())
    assert correct_text(sb, TEXT, item_id=COPY, user_id=USER, file_id=FILE) == FIXED
    assert correct_text(sb, TEXT, item_id=FIRST, user_id=USER) == FIXED     # either switch: first item
    assert correct_text(sb, TEXT, item_id=COPY, user_id=USER, file_id=None) == TEXT
    assert correct_text(db(), TEXT, item_id=FIRST, user_id=USER) is TEXT
    assert correct_text(sb, "", item_id=FIRST) == ""


# ── the flag gate (only the attachment agent writes corrections) ───────────────


def _flag(monkeypatch, enabled: bool, users: str | None = None) -> None:
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", enabled)
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_USERS", users)


def test_flag_off_or_another_users_allowlist_makes_no_request(monkeypatch, read_on):
    rows = [{"item_id": i, "kind": "attachment", "content_md": TEXT} for i in (FIRST, COPY)]
    for enabled, users in ((False, None), (True, OTHER)):
        _flag(monkeypatch, enabled, users)
        sb = db(fix_row())
        assert correct_rows(sb, rows, user_id=USER) is rows
        assert correct_text(sb, TEXT, item_id=FIRST, user_id=USER) is TEXT
        assert corrections_for_item_ids(sb, [FIRST], user_id=USER) == {}
        assert reads(sb, T) == [] and reads(sb, "workspace_items") == []


def test_no_user_anywhere_reads_nothing_and_a_rows_own_user_decides(read_on):
    sb = db(fix_row())
    rows = [{"item_id": FIRST, "file_id": FILE, "kind": "attachment", "content_md": TEXT}]
    assert correct_rows(sb, rows) is rows and sb.selects == []
    owned = [{**rows[0], "user_id": USER}]
    assert correct_rows(sb, owned)[0]["content_md"] == FIXED


def test_item_ids_reuse_known_file_keys_and_look_up_only_the_rest():
    with flags.override(read=True, write=False):
        sb = db(fix_row())
        out = corrections_for_item_ids(sb, [COPY], user_id=USER, file_keys={COPY: FILE})
        assert list(out) == [COPY] and reads(sb, "workspace_items") == []
        out = corrections_for_item_ids(sb, [COPY, FIRST], user_id=USER, file_keys={COPY: FILE})
    assert set(out) == {COPY, FIRST}
    assert reads(sb, "workspace_items") == ["item_id, file_id"]          # FIRST only


def test_item_ids_backoff_is_checked_before_the_lookup():
    sb = db(fix_row())
    tc._backoff.failed()
    assert corrections_for_item_ids(sb, [COPY], user_id=USER) == {} and sb.selects == []
