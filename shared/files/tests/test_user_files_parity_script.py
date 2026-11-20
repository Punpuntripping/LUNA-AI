"""scripts/user_files_parity.py — run against the in-memory fake only (never a live DB).

Equal data in both tables → every loader's digest is equal with the switch off
and on; a file that differs from its item → those loaders report DIFFERENT. The
printed output carries ids, function names and lengths — never text.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

from shared.files import flags
from shared.files.tests.user_files_fake import FakeSupabase

REPO = pathlib.Path(__file__).resolve().parents[3]
USER = "11111111-1111-1111-1111-111111111111"
AUTH = "99999999-1111-1111-1111-111111111111"
CONV = "cccccccc-0000-0000-0000-000000000001"
ITEM = "aaaaaaaa-0000-0000-0000-000000000001"
SECRET_TITLE = "عقد سري للاختبار"
SECRET_TEXT = "نص المستند السري " * 40
SECRET_SUMMARY = "**ملخص المستند:**\nملخص سري للمستند."


def _load():
    spec = importlib.util.spec_from_file_location("_parity", REPO / "scripts" / "user_files_parity.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module          # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


def _world() -> FakeSupabase:
    item = {
        "item_id": ITEM, "user_id": USER, "conversation_id": CONV, "case_id": None, "message_id": None,
        "kind": "attachment", "created_by": "user", "wi_seq": 1, "title": SECRET_TITLE,
        "content_md": SECRET_TEXT, "summary": SECRET_SUMMARY, "summary_source_length": len(SECRET_TEXT),
        "summary_updated_at": None, "word_count": len(SECRET_TEXT.split()),
        "metadata": {"filename": "secret.pdf", "mime_type": "application/pdf", "file_size_bytes": 1234,
                     "upload_status": "ready", "ocr_status": "done", "ocr_chars": len(SECRET_TEXT),
                     "doc_type": "contract"},
        "storage_path": "general/u/convos/c/secret.pdf", "document_id": None, "is_visible": True,
        "created_at": "2026-11-10T10:00:00+00:00", "updated_at": "2026-11-10T10:00:00+00:00",
        "deleted_at": None,
    }
    msg = "eeeeeeee-0000-0000-0000-000000000001"
    smap = {
        "item_id": ITEM, "user_id": USER, "content_chars": len(SECRET_TEXT), "paragraph_rule_version": "p",
        "prompt_version": "v", "model": None, "split": False, "documents_in_file": [], "key_paragraphs": [],
        "created_at": "2026-11-10T11:00:00+00:00",
    }
    segs = [
        {"item_id": ITEM, "seq": i, "from_p": i, "to_p": i, "page_from": None, "page_to": None,
         "char_start": 0, "char_end": 10, "label": "parties", "value": value, "what": "قسم",
         "content_md": f"نص القسم {i}"}
        for i, value in ((1, "metadata"), (2, "core"), (3, "supporting"))
    ]
    return FakeSupabase(
        backfill=True,
        workspace_items=[item],
        conversations=[{"conversation_id": CONV, "user_id": USER, "deleted_at": None}],
        users=[{"user_id": USER, "auth_id": AUTH}],
        messages=[{"message_id": msg, "conversation_id": CONV, "role": "user", "content": "اقرأ الملف",
                   "artifact_ids": [], "referenced_item_ids": [], "metadata": {},
                   "created_at": "2026-11-10T10:00:01+00:00"}],
        message_attachments=[{"message_id": msg, "document_id": ITEM, "created_at": "2026-11-10T10:00:01+00:00"}],
        attachment_segment_maps=[smap],
        attachment_segments=segs,
    )


@pytest.fixture(autouse=True)
def _clean():
    flags.reset_probe()
    yield
    flags.reset_probe()


def test_equal_data_every_loader_equal_and_no_text_printed(capsys, tmp_path, monkeypatch):
    parity = _load()
    monkeypatch.setattr(parity, "_inside_git", lambda _p: False)
    out = tmp_path / "digests.json"
    assert parity.run_agents(_world(), out_path=out) == 0
    printed = capsys.readouterr().out
    lines = [ln for ln in printed.splitlines() if ln.startswith(CONV) or ln.startswith("user:")]
    names = {ln.split("  ")[1] for ln in lines}
    for expected in (
        "router._load_workspace_item_summaries",
        "router_context_render.render_workspace_items_fence",
        "orchestrator._load_recent_messages(full=True)",
        "orchestrator._load_attached_items",
        "case_documents.build_case_document_blocks",
        "case_documents.build_case_document_blocks_segmented",
        "walkers.build_analyzed_items_direct",
        "writer_planner_context.load_writer_planner_context",
        f"unfold.unfold_item[{ITEM}]",
        "memory._load_compaction_context",
        "item_analyzer._load_workspace_items",
        "workspace_service.list_workspace_items_by_conversation->_to_response",
        "files_service.list_library_files",
        f"files_service.get_library_file[{ITEM}]",
        f"files_service.match_library_file[{ITEM}]",
    ):
        assert expected in names, expected
    assert all(ln.split("  ")[2] == "equal" for ln in lines), printed
    for secret in (SECRET_TITLE, "ملخص سري", "نص المستند", "secret.pdf"):
        assert secret not in printed
    saved = json.loads(out.read_text(encoding="utf-8"))
    flat = json.dumps(saved, ensure_ascii=False)
    assert SECRET_TITLE not in flat and "secret.pdf" not in flat
    entry = saved[CONV]["orchestrator._load_attached_items"]
    assert entry["off"] == entry["on"] and entry["off_len"] > 0
    assert saved[CONV]["attachment_segmenter.load_segment_maps"]["off_len"] > 2        # a map was loaded
    assert saved[CONV]["orchestrator._load_recent_messages(full=True)"]["off_len"] > 2  # history present
    assert not any(v["off"].startswith("error:") for v in saved[CONV].values()), saved[CONV]


def test_a_differing_file_is_reported(capsys):
    parity = _load()
    sb = _world()
    sb.file(ITEM)["title"] = "a different title"
    assert parity.run_agents(sb) == 1
    printed = capsys.readouterr().out
    different = {ln.split("  ")[1] for ln in printed.splitlines() if "DIFFERENT" in ln}
    assert "orchestrator._load_attached_items" in different
    assert "router._load_workspace_item_summaries" in different
    assert "a different title" not in printed


def test_schema_not_ready_runs_the_off_pass_only(capsys):
    parity = _load()
    sb = _world()
    sb.schema_ready = False
    assert parity.run_agents(sb) == 0
    printed = capsys.readouterr().out
    assert "OFF pass only" in printed and "off-only" in printed
    assert [t for t, c in sb.selects if t == "user_files" and c != "file_id"] == []


def test_baseline_compares_off_digests(tmp_path, monkeypatch, capsys):
    parity = _load()
    monkeypatch.setattr(parity, "_inside_git", lambda _p: False)
    before = tmp_path / "before.json"
    sb = _world()
    sb.schema_ready = False                      # "before 222"
    assert parity.run_agents(sb, out_path=before) == 0
    flags.reset_probe()
    assert parity.run_agents(_world(), baseline_path=before) == 0       # after 222, switch off: same
    flags.reset_probe()
    changed = _world()
    changed.row(ITEM)["summary"] = "changed"
    assert parity.run_agents(changed, baseline_path=before) == 1
    assert "baseline-DIFFERENT" in capsys.readouterr().out


def test_out_path_inside_a_repo_is_refused():
    parity = _load()
    with pytest.raises(SystemExit):
        parity._check_out_path(str(REPO / "digests.json"))


def test_sql_mode_prints_ids_only_and_runs_read_only(capsys, monkeypatch):
    parity = _load()
    executed: list[str] = []

    class _Cur:
        def __init__(self):
            self.last = ""

        def execute(self, sql):
            executed.append(sql.strip().split()[0] if sql.strip() else "")
            self.last = sql

        def fetchall(self):
            return [(USER, 3, 2, 3, 2)] if "FULL JOIN" in self.last else []

    class _Conn:
        closed = False
        rolled_back = False
        read_only = False          # psycopg 3: set BEFORE the first statement

        def cursor(self):
            assert self.read_only is True, "the session must be read-only before any statement"
            return _Cur()

        def rollback(self):
            _Conn.rolled_back = True

        def close(self):
            _Conn.closed = True

    import shared.config as config
    from types import SimpleNamespace

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(SUPABASE_DB_URL="postgresql://x"))
    assert parity.run_sql(connect=lambda _url: _Conn()) == 1
    printed = capsys.readouterr().out
    assert "counts  1 row(s)" in printed and USER in printed and "fields  0 row(s) — ok" in printed
    assert "postgresql://x" not in printed
    # read-only by the driver's own switch (a "BEGIN READ ONLY" sent as SQL is a no-op once
    # the driver has opened its transaction), SELECTs only, rolled back, closed
    assert executed and all(word in ("SELECT", "WITH") for word in executed)
    assert _Conn.rolled_back and _Conn.closed
    assert parity.run_sql(print_only=True) == 0
    assert "user_files" in capsys.readouterr().out
