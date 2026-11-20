"""shared/files — flags (switches + schema probe) and store (overlay, lookups, writes).

Plan: .claude/plans/my_files_phase2.md §C1 / §F "Unit tests". No live DB: the
client is ``user_files_fake.FakeSupabase``. All text here is invented.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from shared.files import flags
from shared.files import store
from shared.files.tests.user_files_fake import FakeSupabase

USER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
CONV = "cccccccc-0000-0000-0000-000000000001"
ITEM = "aaaaaaaa-0000-0000-0000-000000000001"
FILE = "ffffffff-0000-0000-0000-000000000001"     # deliberately != ITEM

P_TITLE, F_TITLE = "pointer title", "file title"
P_TEXT, F_TEXT = "pointer text body", "file text body that is longer"
P_SUM, F_SUM = "pointer summary", "file summary"


def pointer(item_id: str = ITEM, file_id: str | None = FILE, *, user: str = USER, **kw) -> dict:
    row = {
        "item_id": item_id, "file_id": file_id, "user_id": user, "conversation_id": CONV,
        "kind": "attachment", "wi_seq": 1, "title": P_TITLE, "content_md": P_TEXT,
        "summary": P_SUM, "summary_source_length": len(P_TEXT), "summary_updated_at": "2026-11-01T00:00:00+00:00",
        "metadata": {"doc_type": "memo", "filename": "a.pdf", "keep": "pointer"},
        "storage_path": "general/u/a.pdf", "document_id": None, "word_count": 3,
        "created_at": "2026-11-01T00:00:00+00:00", "updated_at": "2026-11-01T00:00:00+00:00",
        "deleted_at": None,
    }
    row.update(kw)
    return row


def file_row(file_id: str = FILE, *, user: str = USER, **kw) -> dict:
    row = {
        "file_id": file_id, "user_id": user, "title": F_TITLE, "content_md": F_TEXT,
        "summary": F_SUM, "summary_source_length": len(F_TEXT), "summary_updated_at": "2026-11-02T00:00:00+00:00",
        "metadata": {"doc_type": "contract", "filename": "a.pdf", "keep": "file"},
        "storage_path": "general/u/a.pdf", "document_id": None, "origin": "chat",
        "library_hidden_at": None, "created_at": "2026-11-01T00:00:00+00:00",
        "updated_at": "2026-11-02T00:00:00+00:00", "deleted_at": None,
    }
    row.update(kw)
    return row


def _settings(read=False, read_users=None, write=False, write_users=None):
    return lambda: SimpleNamespace(
        USER_FILES_READ_ENABLED=read, USER_FILES_READ_USERS=read_users,
        USER_FILES_WRITE_ENABLED=write, USER_FILES_WRITE_USERS=write_users,
    )


@pytest.fixture(autouse=True)
def _clean_flags(monkeypatch):
    flags.reset_probe()
    monkeypatch.setattr(flags, "get_settings", _settings())
    yield
    flags.reset_probe()


def _file_selects(sb: FakeSupabase) -> list[str]:
    """``user_files`` selects, the schema probe left out."""
    return [cols for table, cols in sb.selects if table == "user_files" and cols != "file_id"]


# ===========================================================================
# flags
# ===========================================================================


def test_switches_off_by_default_and_never_probe():
    sb = FakeSupabase()
    assert flags.files_read_enabled(USER, supabase=sb) is False
    assert flags.files_write_enabled(USER, supabase=sb) is False
    assert sb.selects == []


def test_switch_on_probes_once_and_caches(monkeypatch):
    monkeypatch.setattr(flags, "get_settings", _settings(read=True, write=True))
    sb = FakeSupabase()
    assert flags.files_read_enabled(USER, supabase=sb) is True
    assert sb.selects == [("user_files", "file_id"), ("workspace_items", "file_id")]
    assert flags.files_write_enabled(OTHER, supabase=sb) is True
    assert flags.files_read_enabled(None, supabase=None) is True      # cached: no client needed
    assert len(sb.selects) == 2


def test_allow_list_and_none_user(monkeypatch):
    monkeypatch.setattr(flags, "get_settings", _settings(read=True, read_users=f" {USER} , "))
    sb = FakeSupabase()
    assert flags.files_read_enabled(USER, supabase=sb) is True
    assert flags.files_read_enabled(OTHER, supabase=sb) is False
    assert flags.files_read_enabled(None, supabase=sb) is False       # None + allow-list = off
    assert flags.files_write_enabled(USER, supabase=sb) is False       # the other switch is its own


def test_schema_not_ready_reads_off_logs_once_and_reprobes_after_a_minute(monkeypatch, caplog):
    monkeypatch.setattr(flags, "get_settings", _settings(read=True))
    clock = [1000.0]
    monkeypatch.setattr(flags.time, "monotonic", lambda: clock[0])
    sb = FakeSupabase(schema_ready=False)
    with caplog.at_level(logging.ERROR, logger="shared.files.flags"):
        assert flags.files_read_enabled(USER, supabase=sb) is False
        assert flags.files_read_enabled(USER, supabase=sb) is False   # within the minute: no probe
    assert len([r for r in caplog.records if "schema not ready" in r.getMessage()]) == 1
    probes = len(sb.selects)
    assert probes == 1                      # the user_files probe failed first
    clock[0] += flags.PROBE_RETRY_S + 1
    sb.schema_ready = True
    assert flags.files_read_enabled(USER, supabase=sb) is True
    assert len(sb.selects) == probes + 2


def test_none_client_cannot_probe(monkeypatch):
    monkeypatch.setattr(flags, "get_settings", _settings(read=True))
    assert flags.files_read_enabled(USER, supabase=None) is False


def test_off_on_error(monkeypatch):
    def boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(flags, "get_settings", boom)
    assert flags.files_read_enabled(USER, supabase=FakeSupabase()) is False
    assert flags.files_write_enabled(USER, supabase=FakeSupabase()) is False


def test_override_forces_and_restores(monkeypatch):
    sb = FakeSupabase()
    with flags.override(read=True):
        assert flags.files_read_enabled(None, supabase=sb) is True
        assert flags.files_write_enabled(USER, supabase=sb) is False
        with flags.override(write=True, read=False):
            assert flags.files_read_enabled(USER, supabase=sb) is False
            assert flags.files_write_enabled(USER, supabase=sb) is True
        assert flags.files_read_enabled(USER, supabase=sb) is True
    assert flags.files_read_enabled(USER, supabase=sb) is False
    # the schema still applies under an override
    flags.reset_probe()
    with flags.override(read=True):
        assert flags.files_read_enabled(USER, supabase=FakeSupabase(schema_ready=False)) is False


def test_flag_env_names_equal_field_names(monkeypatch):
    """The repo's pydantic alias trap: the env var must be the field name."""
    from shared.config import Settings

    names = ("USER_FILES_READ_ENABLED", "USER_FILES_READ_USERS",
             "USER_FILES_WRITE_ENABLED", "USER_FILES_WRITE_USERS")
    for field in names:
        info = Settings.model_fields[field]
        assert info.validation_alias is None and info.alias is None
    monkeypatch.setenv("SUPABASE_URL", "http://localhost:54321")
    for name in ("SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_JWT_SECRET"):
        monkeypatch.setenv(name, "x")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    s = Settings(_env_file=None)
    assert (s.USER_FILES_READ_ENABLED, s.USER_FILES_WRITE_ENABLED) == (False, False)
    assert (s.USER_FILES_READ_USERS, s.USER_FILES_WRITE_USERS) == (None, None)
    monkeypatch.setenv("USER_FILES_READ_ENABLED", "true")
    monkeypatch.setenv("USER_FILES_READ_USERS", USER)
    monkeypatch.setenv("USER_FILES_WRITE_ENABLED", "true")
    monkeypatch.setenv("USER_FILES_WRITE_USERS", OTHER)
    s = Settings(_env_file=None)
    assert s.USER_FILES_READ_ENABLED is True and s.USER_FILES_READ_USERS == USER
    assert s.USER_FILES_WRITE_ENABLED is True and s.USER_FILES_WRITE_USERS == OTHER


# ===========================================================================
# with_file_id / overlay_files
# ===========================================================================


def test_with_file_id():
    sb = FakeSupabase()
    cols = "item_id, title, content_md"
    assert store.with_file_id(cols, user_id=USER, supabase=sb) is cols
    with flags.override(read=True):
        assert store.with_file_id(cols, user_id=USER, supabase=sb) == cols + ", file_id"
        assert store.with_file_id("*", supabase=sb) == "*"
        assert store.with_file_id("item_id, file_id", supabase=sb) == "item_id, file_id"
        embed = "item_id, conversations!fk(file_id)"
        assert store.with_file_id(embed, supabase=sb) == embed + ", file_id"   # an embed's column is not the row's


def test_overlay_off_is_a_no_op():
    sb = FakeSupabase(user_files=[file_row()])
    rows = [pointer()]
    out = store.overlay_files(sb, rows, user_id=USER)
    assert out is rows and out[0] is rows[0]
    assert sb.selects == []


def test_overlay_replaces_only_rows_with_file_id_and_only_keys_they_carry():
    sb = FakeSupabase(user_files=[file_row()])
    linked = {"item_id": ITEM, "file_id": FILE, "title": P_TITLE, "content_md": P_TEXT,
              "kind": "attachment", "updated_at": "2026-11-01"}
    unlinked = {"item_id": "x", "title": "note title", "content_md": "note body", "kind": "note"}
    null_link = {"item_id": "y", "file_id": None, "title": "t"}
    with flags.override(read=True):
        out = store.overlay_files(sb, [linked, unlinked, null_link], user_id=USER)
    assert out[1] is unlinked and out[2] is null_link
    assert out[0] == {**linked, "title": F_TITLE, "content_md": F_TEXT}
    assert list(out[0]) == list(linked)                  # no key added, order kept
    assert linked["title"] == P_TITLE                    # the input row is not mutated
    [cols] = _file_selects(sb)
    assert cols == "file_id, title, content_md"          # only what the rows carry


def test_overlay_doc_type_alias_reads_the_generated_column():
    sb = FakeSupabase(user_files=[file_row()])
    row = {"item_id": ITEM, "file_id": FILE, "doc_type": "memo"}
    with flags.override(read=True):
        [out] = store.overlay_files(sb, [row], user_id=USER)
    assert out["doc_type"] == "contract"


def test_overlay_every_file_column():
    sb = FakeSupabase(user_files=[file_row(storage_path=None, document_id="d-1")])
    row = {k: v for k, v in pointer().items()}
    with flags.override(read=True):
        [out] = store.overlay_files(sb, [row], user_id=USER)
    assert out["title"] == F_TITLE and out["content_md"] == F_TEXT and out["summary"] == F_SUM
    assert out["summary_source_length"] == len(F_TEXT)
    assert out["summary_updated_at"] == "2026-11-02T00:00:00+00:00"
    assert out["metadata"]["keep"] == "file"
    assert out["storage_path"] is None and out["document_id"] == "d-1"
    assert out["word_count"] == len(F_TEXT.split())
    # per-conversation fields stay the pointer's
    for key in ("item_id", "file_id", "conversation_id", "wi_seq", "kind", "created_at", "updated_at", "deleted_at"):
        assert out[key] == row[key]


def test_overlay_pointer_id_differs_from_file_id():
    sb = FakeSupabase(user_files=[file_row(file_id=FILE), file_row(file_id=ITEM, title="wrong file")])
    with flags.override(read=True):
        [out] = store.overlay_files(sb, [pointer(item_id=ITEM, file_id=FILE)], user_id=USER)
    assert out["title"] == F_TITLE


def test_overlay_missing_file_keeps_pointer_values_and_logs_ids_only(caplog):
    sb = FakeSupabase(user_files=[])
    row = pointer()
    with flags.override(read=True), caplog.at_level(logging.WARNING, logger="shared.files.store"):
        [out] = store.overlay_files(sb, [row], user_id=USER)
    assert out is row
    text = " ".join(r.getMessage() for r in caplog.records)
    assert FILE in text
    for secret in (P_TITLE, P_TEXT, P_SUM, "a.pdf"):
        assert secret not in text


def test_overlay_user_scoping():
    sb = FakeSupabase(user_files=[file_row(user=OTHER)])
    with flags.override(read=True):
        [out] = store.overlay_files(sb, [pointer()], user_id=USER)
        assert out["title"] == P_TITLE                      # another user's file is never read
        [unscoped] = store.overlay_files(sb, [pointer()])
    assert unscoped["title"] == F_TITLE                     # no user given: by file id only
    assert any(f"{USER}" == v for v in sb.filter_values)


def test_overlay_chunks_above_100_ids():
    n = 250
    files = [file_row(file_id=f"f-{i:03d}", title=f"file {i}") for i in range(n)]
    rows = [pointer(item_id=f"i-{i:03d}", file_id=f"f-{i:03d}") for i in range(n)]
    sb = FakeSupabase(user_files=files)
    with flags.override(read=True):
        out = store.overlay_files(sb, rows, user_id=USER)
    assert [r["title"] for r in out] == [f"file {i}" for i in range(n)]
    assert len(_file_selects(sb)) == 3                       # 100 + 100 + 50


def test_overlay_strict_raises_and_lenient_keeps_values():
    sb = FakeSupabase(user_files=[])
    with flags.override(read=True):
        with pytest.raises(store.FileOverlayError):
            store.overlay_files(sb, [pointer()], user_id=USER, strict=True)
        sb2 = FakeSupabase(user_files=[file_row()])
        sb2.fail_tables.add("user_files")
        with pytest.raises(store.FileOverlayError):
            store.overlay_files(sb2, [pointer()], user_id=USER, strict=True)
        row = pointer()
        [out] = store.overlay_files(sb2, [row], user_id=USER)
        assert out is row


def test_overlay_keeps_a_pointer_null_that_the_file_coalesced():
    sb = FakeSupabase(user_files=[file_row(content_md="", metadata={})])
    with flags.override(read=True):
        [out] = store.overlay_files(sb, [pointer(content_md=None, metadata=None)], user_id=USER)
    assert out["content_md"] is None and out["metadata"] is None


def test_overlay_values_are_copies():
    sb = FakeSupabase(user_files=[file_row()])
    with flags.override(read=True):
        a, b = store.overlay_files(sb, [pointer(item_id="i-1"), pointer(item_id="i-2")], user_id=USER)
    a["metadata"]["x"] = 1
    assert "x" not in b["metadata"] and "x" not in sb.file(FILE)["metadata"]


# ===========================================================================
# lookups
# ===========================================================================


def test_file_ids_and_content_chars_for_items():
    sb = FakeSupabase(
        workspace_items=[
            pointer(item_id="i-live", file_id="f-live"),
            pointer(item_id="i-gone", file_id="f-gone", deleted_at="2026-11-03T00:00:00+00:00"),
            pointer(item_id="i-dead-file", file_id="f-dead"),
            pointer(item_id="i-other", file_id="f-other", user=OTHER),
            {"item_id": "i-note", "user_id": USER, "kind": "note", "content_md": "x", "deleted_at": None},
        ],
        user_files=[
            file_row(file_id="f-live", content_md="abcde"),
            file_row(file_id="f-gone"),
            file_row(file_id="f-dead", deleted_at="2026-11-03T00:00:00+00:00"),
            file_row(file_id="f-other", user=OTHER),
        ],
    )
    ids = ["i-live", "i-gone", "i-dead-file", "i-other", "i-note"]
    assert store.file_ids_for_items(sb, ids, user_id=USER) == {
        "i-live": "f-live", "i-gone": "f-gone", "i-dead-file": "f-dead",
    }
    assert store.content_chars_for_items(sb, ids, user_id=USER) == {"i-live": 5}
    assert store.content_chars_for_items(sb, ids) == {"i-live": 5, "i-other": len(F_TEXT)}


def test_lookups_chunk_above_100_ids():
    items = [pointer(item_id=f"i-{i:03d}", file_id=f"f-{i:03d}") for i in range(205)]
    sb = FakeSupabase(workspace_items=items, user_files=[file_row(file_id=f"f-{i:03d}") for i in range(205)])
    assert len(store.file_ids_for_items(sb, [r["item_id"] for r in items])) == 205
    assert len([1 for t, _c in sb.selects if t == "workspace_items"]) == 3


# ===========================================================================
# write_attachment / patch_file / hide_files
# ===========================================================================


def test_write_off_issues_todays_exact_statement():
    sb = FakeSupabase(workspace_items=[pointer()], user_files=[file_row()])
    payload = {"deleted_at": "2026-11-05T00:00:00+00:00", "updated_at": "2026-11-05T00:00:00+00:00",
               "metadata": {"upload_status": "cancelled"}}
    store.write_attachment(
        sb, ITEM,
        fields={"deleted_at": "x"}, metadata_patch={"upload_status": "cancelled"},
        legacy_update=payload, pointer_update={"deleted_at": "x"},
        owner_id=USER, filters={"user_id": USER},
    )
    assert sb.rpcs == []
    assert sb.statements == [(
        "update", "workspace_items", payload,
        [("eq", "item_id", ITEM), ("eq", "user_id", USER)],
    )]
    assert list(sb.statements[0][2]) == ["deleted_at", "updated_at", "metadata"]   # key order kept


def test_write_off_without_legacy_dict_is_fields_then_metadata():
    sb = FakeSupabase(workspace_items=[pointer()])
    res = store.write_attachment(sb, ITEM, fields={"content_md": "new"}, metadata={"ocr_status": "done"})
    assert sb.statements == [(
        "update", "workspace_items", {"content_md": "new", "metadata": {"ocr_status": "done"}},
        [("eq", "item_id", ITEM)],
    )]
    assert list(sb.statements[0][2]) == ["content_md", "metadata"]
    assert res.data and res.data[0]["item_id"] == ITEM      # today's response object


def test_write_on_patches_the_file_merges_metadata_and_syncs_the_pointer():
    sb = FakeSupabase(workspace_items=[pointer()], user_files=[file_row()])
    with flags.override(write=True):
        res = store.write_attachment(
            sb, ITEM,
            fields={"content_md": "ocr text"},
            metadata={"stale": "snapshot"},                      # the off path's dict: NOT sent
            metadata_patch={"ocr_status": "done", "ocr_chars": 8},
            owner_id=USER,
        )
    assert sb.rpcs == [("user_file_patch", {
        "p_file_id": FILE, "p_fields": {"content_md": "ocr text"},
        "p_metadata_patch": {"ocr_status": "done", "ocr_chars": 8}, "p_user_id": USER,
    })]
    assert [s for s in sb.statements if s[1] == "workspace_items"] == []
    f = sb.file(FILE)
    assert f["metadata"] == {"doc_type": "contract", "filename": "a.pdf", "keep": "file",
                             "ocr_status": "done", "ocr_chars": 8}           # merged, not replaced
    assert f["summary"] is None                                          # new text clears it
    p = sb.row(ITEM)
    assert p["content_md"] == "ocr text" and p["metadata"] == f["metadata"] and p["summary"] is None
    assert isinstance(res, store.FileWrite) and res.file_id == FILE and res.data == []


def test_write_on_returning_rereads_the_pointer_and_pointer_update_is_written():
    sb = FakeSupabase(workspace_items=[pointer()], user_files=[file_row()])
    with flags.override(write=True):
        res = store.write_attachment(
            sb, ITEM,
            fields={"deleted_at": "2026-11-05T00:00:00+00:00"},
            metadata_patch={"upload_status": "cancelled"},
            pointer_update={"deleted_at": "2026-11-05T00:00:00+00:00"},
            owner_id=USER, filters={"user_id": USER}, returning=True,
        )
    assert sb.statements == [(
        "update", "workspace_items", {"deleted_at": "2026-11-05T00:00:00+00:00"},
        [("eq", "item_id", ITEM), ("eq", "user_id", USER)],
    )]
    assert res.data[0]["item_id"] == ITEM and res.data[0]["deleted_at"] == "2026-11-05T00:00:00+00:00"
    assert res.data[0]["metadata"]["upload_status"] == "cancelled"
    assert sb.file(FILE)["deleted_at"] == "2026-11-05T00:00:00+00:00"


def test_write_on_falls_back_to_the_item_when_there_is_no_file_or_no_row_written(caplog):
    payload = {"metadata": {"m": 1}}
    sb = FakeSupabase(workspace_items=[pointer(file_id=None)], user_files=[file_row()])
    with flags.override(write=True), caplog.at_level(logging.WARNING):
        store.write_attachment(sb, ITEM, metadata={"m": 1}, metadata_patch={"m": 1}, owner_id=USER)
    assert sb.rpcs == [] and sb.statements == [("update", "workspace_items", payload, [("eq", "item_id", ITEM)])]

    sb = FakeSupabase(workspace_items=[pointer()], user_files=[file_row(user=OTHER)])
    with flags.override(write=True):
        store.write_attachment(sb, ITEM, metadata={"m": 1}, metadata_patch={"m": 1})
    assert len(sb.rpcs) == 1 and sb.rpcs[0][1]["p_user_id"] is None
    # owner given but the file is someone else's → no row → today's statement
    sb = FakeSupabase(workspace_items=[pointer(user=USER)], user_files=[file_row(user=OTHER)])
    with flags.override(write=True):
        store.write_attachment(sb, ITEM, metadata={"m": 1}, metadata_patch={"m": 1}, owner_id=OTHER)
    assert sb.rpcs == []          # the item is not OTHER's: no file id resolves
    assert sb.statements == [("update", "workspace_items", payload, [("eq", "item_id", ITEM)])]
    text = " ".join(r.getMessage() for r in caplog.records)
    assert P_TITLE not in text and "a.pdf" not in text


def test_write_on_failure_raises():
    sb = FakeSupabase(workspace_items=[pointer()], user_files=[file_row()])
    sb.fail_rpcs.add("user_file_patch")
    with flags.override(write=True), pytest.raises(RuntimeError):
        store.write_attachment(sb, ITEM, metadata_patch={"m": 1}, owner_id=USER)
    assert sb.statements == []


def test_write_uses_the_per_user_write_switch(monkeypatch):
    monkeypatch.setattr(flags, "get_settings", _settings(write=True, write_users=OTHER))
    sb = FakeSupabase(workspace_items=[pointer()], user_files=[file_row()])
    store.write_attachment(sb, ITEM, metadata={"m": 1}, metadata_patch={"m": 1}, owner_id=USER)
    store.write_attachment(sb, ITEM, metadata={"m": 1}, metadata_patch={"m": 1})            # system job, no owner
    assert sb.rpcs == [] and len(sb.statements) == 2


def test_hide_files_rpc():
    sb = FakeSupabase(
        workspace_items=[pointer()],
        user_files=[file_row(), file_row(file_id="f-2"), file_row(file_id="f-3", user=OTHER)],
    )
    assert store.hide_files(sb, USER, [FILE, "f-2", "f-3", FILE]) == 2
    assert sb.rpcs == [("user_files_hide", {"p_user_id": USER, "p_file_ids": [FILE, "f-2", "f-3"]})]
    first = sb.file(FILE)["library_hidden_at"]
    assert first and sb.file(FILE)["metadata"]["library_hidden_at"] == first
    assert sb.row(ITEM)["metadata"]["library_hidden_at"] == first           # synced to the item
    assert store.hide_files(sb, USER, [FILE]) == 0                           # first stamp kept
    assert sb.file(FILE)["library_hidden_at"] == first
    assert store.hide_files(sb, USER, []) == 0 and len(sb.rpcs) == 2


def test_the_fake_rejects_fields_the_rpc_rejects():
    """Migration 221's user_file_patch raises 22023 on any other p_fields key —
    a writer must never send ``metadata`` or ``updated_at`` as a field."""
    sb = FakeSupabase(user_files=[file_row()])
    for bad in ({"metadata": {}}, {"updated_at": "x"}, {"word_count": 1}):
        with pytest.raises(RuntimeError, match="22023"):
            store.patch_file(sb, FILE, fields=bad)
    assert store.patch_file(sb, FILE, fields={"title": None}) is True
    assert sb.file(FILE)["title"] == F_TITLE


def test_patch_file_reports_whether_a_row_was_written():
    sb = FakeSupabase(user_files=[file_row()])
    assert store.patch_file(sb, FILE, fields={"title": "t"}, user_id=USER) is True
    assert store.patch_file(sb, FILE, fields={"title": "t"}, user_id=OTHER) is False
    assert store.patch_file(sb, "missing") is False
