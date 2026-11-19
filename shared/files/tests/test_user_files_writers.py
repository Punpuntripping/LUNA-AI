"""Step S3 — every writer of an attachment (my_files_phase2.md §A3/§A4/§C5).

Switch off: each site issues TODAY's exact statement (table, payload with its
key order, filters) and no RPC. Switch on: ``user_file_patch`` with only the
site's own delta (and, for cancels, the item's ``deleted_at`` after it), the
item following by the 221 trigger — simulated by the fake. The pointer has
``item_id != file_id``. No live DB; all text invented.
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from shared.files import flags
from shared.files.tests.user_files_fake import FakeSupabase

USER = "11111111-1111-1111-1111-111111111111"
CONV = "cccccccc-0000-0000-0000-000000000001"
ITEM = "aaaaaaaa-0000-0000-0000-000000000001"
FILE = "ffffffff-0000-0000-0000-000000000001"
REPO = pathlib.Path(__file__).resolve().parents[3]


def _iso(hours_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _pointer(**kw) -> dict:
    row = {
        "item_id": ITEM, "file_id": FILE, "user_id": USER, "conversation_id": CONV, "kind": "attachment",
        "created_by": "user", "wi_seq": 2, "title": "upload.pdf", "content_md": "", "summary": None,
        "summary_source_length": None, "summary_updated_at": None,
        "metadata": {"filename": "upload.pdf", "mime_type": "application/pdf", "file_size_bytes": 10,
                     "upload_status": "ready", "page_count": 1},
        "storage_path": "general/u/convos/c/upload.pdf", "document_id": None,
        "created_at": _iso(48), "updated_at": _iso(48), "deleted_at": None,
    }
    row.update(kw)
    return row


def _file_from(pointer: dict, **kw) -> dict:
    row = {k: pointer.get(k) for k in (
        "user_id", "title", "summary", "summary_source_length", "summary_updated_at",
        "storage_path", "document_id", "created_at", "updated_at", "deleted_at",
    )}
    row.update({"file_id": FILE, "content_md": pointer.get("content_md") or "",
                "metadata": dict(pointer["metadata"]), "origin": "chat", "library_hidden_at": None})
    row.update(kw)
    return row


def db(pointer: dict | None = None, **file_kw) -> FakeSupabase:
    p = pointer or _pointer()
    return FakeSupabase(workspace_items=[p], user_files=[_file_from(p, **file_kw)])


@pytest.fixture(params=[False, True], ids=["write-off", "write-on"])
def write_on(request):
    flags.reset_probe()
    with flags.override(read=False, write=request.param):
        yield request.param
    flags.reset_probe()


def item_updates(sb: FakeSupabase) -> list[tuple]:
    return [s for s in sb.statements if s[1] == "workspace_items"]


def patch_calls(sb: FakeSupabase) -> list[dict]:
    return [params for name, params in sb.rpcs if name == "user_file_patch"]


def EQ(col, val):
    return ("eq", col, val)


# ===========================================================================
# OCR
# ===========================================================================


def test_ocr_mark_status(write_on):
    from agents.memory.ocr_extractor import runner as ocr

    sb = db()
    snapshot = {"filename": "upload.pdf", "stale": True}
    ocr._mark_status(sb, ITEM, snapshot, "failed", {"ocr_error": "boom"}, user_id=USER)
    if write_on:
        assert item_updates(sb) == []
        assert patch_calls(sb) == [{
            "p_file_id": FILE, "p_fields": {},
            "p_metadata_patch": {"ocr_status": "failed", "ocr_error": "boom"}, "p_user_id": USER,
        }]
        meta = sb.row(ITEM)["metadata"]
        assert meta["ocr_status"] == "failed" and meta["upload_status"] == "ready"   # merged, not replaced
        assert "stale" not in meta
    else:
        assert sb.rpcs == []
        assert item_updates(sb) == [(
            "update", "workspace_items",
            {"metadata": {"filename": "upload.pdf", "stale": True, "ocr_status": "failed", "ocr_error": "boom"}},
            [EQ("item_id", ITEM)],
        )]


def test_ocr_content_write(write_on, monkeypatch):
    from agents.memory.ocr_extractor import runner as ocr
    from agents.memory.ocr_extractor.models import OcrDocumentResult, OcrExtractionStats
    from shared.quota.ocr_budget import OcrBudget

    async def budget(_sb, _uid):
        return OcrBudget(total=None, used=0, per_file_cap=None)

    async def fake_ocr(*_a, **_k):
        return OcrDocumentResult(text="  extracted words  ", page_count=1, model="ocr-model")

    monkeypatch.setattr(ocr, "_read_budget", budget)
    monkeypatch.setattr(ocr, "_ocr", fake_ocr)
    monkeypatch.setattr(ocr, "get_signed_url", lambda *_a, **_k: "https://signed.example/x")
    monkeypatch.setattr(ocr, "record_call", lambda **_k: None)
    p = _pointer(metadata={"filename": "upload.pdf", "mime_type": "application/pdf",
                           "file_size_bytes": 10, "upload_status": "ready", "page_count": 1})
    sb = db(p)
    stats = OcrExtractionStats()
    asyncio.run(ocr._run_inner(sb, CONV, USER, stats, SimpleNamespace(STORAGE_BUCKET_DOCUMENTS="documents")))
    assert stats.extracted == 1
    expected_meta = {**p["metadata"], "ocr_status": "done", "ocr_pages": 1, "ocr_chars": len("extracted words")}
    if write_on:
        assert item_updates(sb) == []
        assert patch_calls(sb) == [{
            "p_file_id": FILE, "p_fields": {"content_md": "extracted words"},
            "p_metadata_patch": {"ocr_status": "done", "ocr_pages": 1, "ocr_chars": 15},
            "p_user_id": USER,
        }]
        assert sb.row(ITEM)["content_md"] == "extracted words"                 # by trigger
        assert sb.row(ITEM)["metadata"] == expected_meta
    else:
        assert sb.rpcs == []
        assert item_updates(sb) == [(
            "update", "workspace_items", {"content_md": "extracted words", "metadata": expected_meta},
            [EQ("item_id", ITEM)],
        )]
        assert list(item_updates(sb)[0][2]) == ["content_md", "metadata"]


# ===========================================================================
# Summarizer
# ===========================================================================


def test_summary_attempt_marker(write_on):
    from agents.memory import summarize as summ

    sb = db()
    md = summ._mark_attempt(sb, ITEM, {"filename": "upload.pdf"}, attachment=True, owner_id=USER)
    assert set(md) == {"filename", "summary_attempt"}
    if write_on:
        assert item_updates(sb) == []
        assert patch_calls(sb) == [{
            "p_file_id": FILE, "p_fields": {},
            "p_metadata_patch": {"summary_attempt": md["summary_attempt"]}, "p_user_id": USER,
        }]
    else:
        assert sb.rpcs == []
        assert item_updates(sb) == [("update", "workspace_items", {"metadata": md}, [EQ("item_id", ITEM)])]


def test_summary_attempt_marker_of_a_generated_item_never_takes_the_file_path(write_on):
    from agents.memory import summarize as summ

    sb = db()
    md = summ._mark_attempt(sb, ITEM, {}, owner_id=USER)        # attachment=False (generic flow)
    assert sb.rpcs == []
    assert item_updates(sb) == [("update", "workspace_items", {"metadata": md}, [EQ("item_id", ITEM)])]


def test_attachment_summary_persist(write_on):
    from agents.memory import summarize as summ
    from agents.memory.artifact_summarizer import AttachmentSummaryOutput

    sb = db()
    out = AttachmentSummaryOutput(
        title=" Lease contract ", summary_md="the summary", doc_type="contract",
        context_link="link", model_used="m", tokens_in=3, tokens_out=2,
    )
    summ._persist_attachment_summary(sb, ITEM, out, 900, {"filename": "upload.pdf", "summary_attempt": {"at": "t"}},
                                     owner_id=USER)
    art = {"flow": "attachment", "model_used": "m", "tokens_in": 3, "tokens_out": 2, "tokens_reasoning": 0,
           "fallback_used": False, "context_link": "link"}
    if write_on:
        assert item_updates(sb) == []
        [call] = patch_calls(sb)
        assert set(call["p_fields"]) == {"summary", "summary_updated_at", "summary_source_length", "title"}
        assert call["p_fields"]["title"] == "Lease contract" and call["p_fields"]["summary_source_length"] == 900
        assert call["p_metadata_patch"] == {"artifact_summary": art, "doc_type": "contract"}
        assert sb.row(ITEM)["summary"] == "the summary" and sb.row(ITEM)["title"] == "Lease contract"
    else:
        assert sb.rpcs == []
        [(_op, table, payload, filters)] = item_updates(sb)
        assert list(payload) == ["summary", "summary_updated_at", "summary_source_length", "metadata", "title"]
        assert payload["metadata"] == {"filename": "upload.pdf", "summary_attempt": {"at": "t"},
                                       "artifact_summary": art, "doc_type": "contract"}
        assert filters == [EQ("item_id", ITEM)]


def test_attachment_summary_persist_failure_is_swallowed(write_on, caplog):
    from agents.memory import summarize as summ
    from agents.memory.artifact_summarizer import AttachmentSummaryOutput

    sb = db()
    sb.fail_tables.add("workspace_items")
    sb.fail_rpcs.add("user_file_patch")
    summ._persist_attachment_summary(sb, ITEM, AttachmentSummaryOutput(title="t", summary_md="s"), 1, {},
                                     owner_id=USER)
    assert "attachment UPDATE failed" in caplog.text


# ===========================================================================
# Upload lifecycle (workspace_service)
# ===========================================================================


@pytest.fixture
def ws(monkeypatch):
    from backend.app.services import upload_session_service, workspace_service

    monkeypatch.setattr(workspace_service, "get_user_id", lambda _sb, _auth: USER)
    monkeypatch.setattr(workspace_service, "get_settings", lambda: SimpleNamespace(STORAGE_BUCKET_DOCUMENTS="documents"))
    monkeypatch.setattr(upload_session_service, "verify_finalize", lambda *_a, **_k: None)
    removed: list = []
    monkeypatch.setattr(upload_session_service, "cancel_storage_object",
                        lambda _sb, *, bucket, storage_path: removed.append(storage_path))
    workspace_service._removed = removed
    return workspace_service


def test_finalize_upload(write_on, ws):
    p = _pointer(metadata={"filename": "upload.pdf", "upload_status": "uploading",
                           "declared_size_bytes": 10, "declared_mime_type": "application/pdf"})
    sb = db(p)
    out = ws.finalize_attachment_upload(sb, "auth-1", ITEM)
    assert out["item_id"] == ITEM and out["metadata"]["upload_status"] == "ready"
    stamp = out["metadata"]["upload_finalized_at"]
    if write_on:
        assert item_updates(sb) == []
        assert patch_calls(sb) == [{
            "p_file_id": FILE, "p_fields": {},
            "p_metadata_patch": {"upload_status": "ready", "upload_finalized_at": stamp}, "p_user_id": USER,
        }]
        assert out == sb.row(ITEM)                         # today's ``result.data[0]``: the stored item
    else:
        assert sb.rpcs == []
        [(_op, _t, payload, filters)] = item_updates(sb)
        assert payload == {"metadata": {**p["metadata"], "upload_status": "ready", "upload_finalized_at": stamp},
                           "updated_at": stamp}
        assert filters == [EQ("item_id", ITEM), EQ("user_id", USER)]


def test_cancel_upload(write_on, ws):
    p = _pointer(metadata={"filename": "upload.pdf", "upload_status": "uploading"})
    sb = db(p)
    ws.cancel_attachment_upload(sb, "auth-1", ITEM)
    row = sb.row(ITEM)
    stamp = row["deleted_at"]
    assert stamp and row["metadata"]["upload_status"] == "cancelled"
    assert ws._removed == ["general/u/convos/c/upload.pdf"]
    if write_on:
        assert patch_calls(sb) == [{
            "p_file_id": FILE, "p_fields": {"deleted_at": stamp},
            "p_metadata_patch": {"upload_status": "cancelled", "upload_cancelled_at": stamp}, "p_user_id": USER,
        }]
        # then the item's own deleted_at — never its metadata (the file holds the merged one)
        assert item_updates(sb) == [(
            "update", "workspace_items", {"deleted_at": stamp, "updated_at": stamp},
            [EQ("item_id", ITEM), EQ("user_id", USER)],
        )]
        assert sb.file(FILE)["deleted_at"] == stamp
    else:
        assert sb.rpcs == []
        assert item_updates(sb) == [(
            "update", "workspace_items",
            {"deleted_at": stamp, "updated_at": stamp,
             "metadata": {**p["metadata"], "upload_status": "cancelled", "upload_cancelled_at": stamp}},
            [EQ("item_id", ITEM), EQ("user_id", USER)],
        )]


def test_legacy_single_shot_promote(write_on, ws, monkeypatch):
    from backend.app.services import message_service

    monkeypatch.setattr(message_service, "verify_conversation_ownership", lambda *_a: None)
    monkeypatch.setattr(ws, "upload_file", lambda *_a, **_k: None)
    sb = FakeSupabase()

    def create(_sb, user_id, **kw):
        row = _pointer(title=kw["title"], storage_path=kw["storage_path"], metadata=kw["metadata"])
        sb.tables["workspace_items"].append(row)
        sb.tables["user_files"].append(_file_from(row))
        return dict(row)

    monkeypatch.setattr(ws, "create_workspace_item", create)
    out = ws.upload_attachment_bytes(
        sb, "auth-1", CONV, file_bytes=b"%PDF-1.4 body", filename="upload.pdf", content_type="application/pdf",
    )
    stamp = out["metadata"]["upload_finalized_at"]
    assert out["metadata"]["upload_status"] == "ready"
    if write_on:
        assert item_updates(sb) == []
        assert patch_calls(sb) == [{
            "p_file_id": FILE, "p_fields": {},
            "p_metadata_patch": {"upload_status": "ready", "upload_finalized_at": stamp}, "p_user_id": USER,
        }]
        assert out == sb.row(ITEM)
    else:
        assert sb.rpcs == []
        [(_op, _t, payload, filters)] = item_updates(sb)
        assert list(payload) == ["metadata", "updated_at"] and payload["updated_at"] == stamp
        assert filters == [EQ("item_id", ITEM), EQ("user_id", USER)]


# ===========================================================================
# Retention sweep and upload reconciler
# ===========================================================================


class _Bucket:
    def __init__(self, sb: FakeSupabase) -> None:
        self.sb = sb

    def remove(self, paths):
        # the order the sweep must keep: the row (or file) write is already recorded
        self.sb.removed.append((list(paths), len(self.sb.statements) + len(self.sb.rpcs)))
        return {"data": paths}


def _with_storage(sb: FakeSupabase) -> FakeSupabase:
    sb.removed = []
    sb.storage = SimpleNamespace(from_=lambda _bucket: _Bucket(sb))
    return sb


def test_retention_sweep(write_on, monkeypatch):
    from backend.app.services import attachment_cleanup as ac

    monkeypatch.setattr(ac, "get_settings", lambda: SimpleNamespace(STORAGE_BUCKET_DOCUMENTS="documents"))
    p = _pointer(metadata={"mime_type": "application/pdf", "ocr_status": "done"})
    sb = _with_storage(db(p))
    stats = ac.cleanup_old_pdf_attachments(sb)
    assert stats["rows_purged"] == 1 and stats["files_deleted"] == 1
    assert sb.removed == [(["general/u/convos/c/upload.pdf"], 1)]       # after exactly one write
    row = sb.row(ITEM)
    stamp = row["metadata"]["original_purged_at"]
    assert row["storage_path"] is None and row["metadata"]["original_purge_reason"] == "retention_sweep"
    if write_on:
        assert item_updates(sb) == []
        assert patch_calls(sb) == [{
            "p_file_id": FILE, "p_fields": {"storage_path": None},
            "p_metadata_patch": {"original_purged_at": stamp, "original_purge_reason": "retention_sweep"},
            "p_user_id": USER,
        }]
    else:
        assert sb.rpcs == []
        assert item_updates(sb) == [(
            "update", "workspace_items",
            {"storage_path": None, "metadata": {**p["metadata"], "original_purged_at": stamp,
                                                "original_purge_reason": "retention_sweep"}},
            [EQ("item_id", ITEM)],
        )]


def test_retention_sweep_keeps_the_file_when_the_write_fails(write_on, monkeypatch):
    from backend.app.services import attachment_cleanup as ac

    monkeypatch.setattr(ac, "get_settings", lambda: SimpleNamespace(STORAGE_BUCKET_DOCUMENTS="documents"))
    sb = _with_storage(db(_pointer(metadata={"mime_type": "application/pdf", "ocr_status": "done"})))
    sb.fail_update_ids.update({ITEM, FILE})
    stats = ac.cleanup_old_pdf_attachments(sb)
    assert stats["rows_purged"] == 0 and sb.removed == []


def test_reconciler_direct_soft_delete(write_on, monkeypatch):
    from backend.app.services import upload_reconciler as ur
    from backend.app.services import upload_session_service

    removed: list = []
    monkeypatch.setattr(upload_session_service, "cancel_storage_object",
                        lambda _sb, *, bucket, storage_path: removed.append(storage_path))
    monkeypatch.setattr(ur, "get_settings", lambda: SimpleNamespace(STORAGE_BUCKET_DOCUMENTS="documents"))
    p = _pointer(metadata={"filename": "upload.pdf", "upload_status": "uploading"})
    sb = db(p)
    ur._direct_soft_delete_attachment(sb, ITEM, p["storage_path"], USER)
    row = sb.row(ITEM)
    stamp = row["deleted_at"]
    assert row["metadata"]["cancelled_by"] == "reconciler" and removed == [p["storage_path"]]
    if write_on:
        assert patch_calls(sb) == [{
            "p_file_id": FILE, "p_fields": {"deleted_at": stamp},
            "p_metadata_patch": {"upload_status": "cancelled", "upload_cancelled_at": stamp,
                                 "cancelled_by": "reconciler"},
            "p_user_id": USER,
        }]
        assert item_updates(sb) == [(
            "update", "workspace_items", {"deleted_at": stamp, "updated_at": stamp}, [EQ("item_id", ITEM)],
        )]
    else:
        assert sb.rpcs == []
        assert item_updates(sb) == [(
            "update", "workspace_items",
            {"deleted_at": stamp, "updated_at": stamp,
             "metadata": {**p["metadata"], "upload_status": "cancelled", "upload_cancelled_at": stamp,
                          "cancelled_by": "reconciler"}},
            [EQ("item_id", ITEM)],
        )]


def test_reconciler_in_place_promote(write_on, monkeypatch):
    from backend.app.services import upload_reconciler as ur
    from backend.app.services import upload_session_service

    monkeypatch.setattr(upload_session_service, "verify_finalize", lambda *_a, **_k: None)
    monkeypatch.setattr(ur, "_auth_id_for_user", lambda *_a: None)
    monkeypatch.setattr(ur, "get_settings", lambda: SimpleNamespace(STORAGE_BUCKET_DOCUMENTS="documents"))
    p = _pointer(metadata={"filename": "upload.pdf", "upload_status": "uploading"})
    sb = db(p)
    stats = {"scanned": 0, "recovered": 0, "deleted": 0}
    ur._reconcile_attachment(sb, dict(p), stats)
    assert stats["recovered"] == 1
    meta = sb.row(ITEM)["metadata"]
    stamp = meta["upload_finalized_at"]
    assert meta["upload_status"] == "ready" and meta["finalized_by"] == "reconciler"
    if write_on:
        assert item_updates(sb) == []
        assert patch_calls(sb) == [{
            "p_file_id": FILE, "p_fields": {},
            "p_metadata_patch": {"upload_status": "ready", "upload_finalized_at": stamp, "finalized_by": "reconciler"},
            "p_user_id": USER,
        }]
    else:
        assert sb.rpcs == []
        assert item_updates(sb) == [(
            "update", "workspace_items", {"metadata": meta, "updated_at": stamp}, [EQ("item_id", ITEM)],
        )]


# ===========================================================================
# Scripts
# ===========================================================================


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"_test_{name}", REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_type_backfill_apply(write_on):
    bf = _load_script("my_files_type_backfill")
    sb = db()
    typed = FakeSupabase(workspace_items=[_pointer(item_id="typed", file_id=None,
                                                   metadata={"doc_type": "memo"})])
    assert bf.apply(typed, {"typed": "contract"}) == {"already_typed": 1}
    done = bf.apply(sb, {ITEM: "contract"})
    assert done == {"written": 1}
    assert sb.row(ITEM)["metadata"]["doc_type"] == "contract"
    if write_on:
        assert item_updates(sb) == []
        assert patch_calls(sb) == [{
            "p_file_id": FILE, "p_fields": {}, "p_metadata_patch": {"doc_type": "contract"}, "p_user_id": USER,
        }]
    else:
        assert sb.rpcs == []
        [(_op, _t, payload, filters)] = item_updates(sb)
        assert payload == {"metadata": {**_pointer()["metadata"], "doc_type": "contract"}}
        assert filters == [EQ("item_id", ITEM)]


def test_nuke_account_deletes_files_after_their_items_and_tolerates_a_missing_table():
    nuke = _load_script("nuke_account")
    tables = [t for t, _c in nuke.CASCADING_TABLES]
    assert tables.index("user_files") == tables.index("workspace_items") + 1
    assert ("user_files", "user_id") in nuke.CASCADING_TABLES

    class _Missing:
        def table(self, _name):
            raise RuntimeError("PGRST205: Could not find the table 'public.user_files'")

    assert nuke.count_rows(_Missing(), "user_files", "user_id", USER) is None
    assert nuke.delete_rows(_Missing(), "user_files", "user_id", USER) is None
