"""Step S2 — every reader that takes an attachment's content (my_files_phase2.md §A1/§A4/§C4).

Each test builds a fake whose FILE row differs from its POINTER row (title, text,
summary, type, metadata) and whose pointer has ``item_id != file_id``, then runs
the real loader with the read switch off and on. Off: the pointer's values and
no ``user_files`` read at all. On: the file's values. No live DB; the 222 sync
triggers are off here so the two rows stay different. All text is invented.

``files_service`` (the «الملفات» routes) is covered by
``backend/tests/test_files_service.py``, parametrised over both paths.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from shared.files import flags
from shared.files.tests.user_files_fake import FakeSupabase

USER = "11111111-1111-1111-1111-111111111111"
CONV = "cccccccc-0000-0000-0000-000000000001"
CASE = "cccccccc-0000-0000-0000-0000000000ca"
ITEM = "aaaaaaaa-0000-0000-0000-000000000001"
FILE = "ffffffff-0000-0000-0000-000000000001"      # != ITEM on purpose
NOTE = "aaaaaaaa-0000-0000-0000-0000000000ff"
MSG = "eeeeeeee-0000-0000-0000-000000000001"

P = {
    "title": "pointer title",
    "content_md": "pointer text " * 30,            # 390 chars
    "summary": "pointer summary",
    "doc_type": "memo",
    "word_count": 60,
}
F = {
    "title": "file title",
    "content_md": "file body words " * 40,         # 640 chars
    "summary": "file summary",
    "doc_type": "contract",
    "word_count": 120,
}


def _pointer(**kw) -> dict:
    row = {
        "item_id": ITEM, "file_id": FILE, "user_id": USER, "conversation_id": CONV, "case_id": None,
        "message_id": None, "kind": "attachment", "created_by": "user", "wi_seq": 3,
        "title": P["title"], "content_md": P["content_md"], "summary": P["summary"],
        "summary_source_length": len(P["content_md"]), "summary_updated_at": None,
        "metadata": {"doc_type": P["doc_type"], "filename": "doc.pdf", "mime_type": "application/pdf",
                     "upload_status": "ready", "side": "pointer"},
        "storage_path": "general/u/convos/c/doc.pdf", "document_id": None, "is_visible": True,
        "word_count": P["word_count"], "created_at": "2026-11-10T10:00:00+00:00",
        "updated_at": "2026-11-10T10:00:00+00:00", "deleted_at": None, "created_while_paid": True,
    }
    row.update(kw)
    return row


def _file(**kw) -> dict:
    row = {
        "file_id": FILE, "user_id": USER, "title": F["title"], "content_md": F["content_md"],
        "summary": F["summary"], "summary_source_length": len(F["content_md"]), "summary_updated_at": None,
        "metadata": {"doc_type": F["doc_type"], "filename": "doc.pdf", "mime_type": "application/pdf",
                     "upload_status": "ready", "side": "file"},
        "storage_path": "general/u/convos/c/doc.pdf", "document_id": None, "origin": "chat",
        "library_hidden_at": None, "created_at": "2026-11-10T10:00:00+00:00",
        "updated_at": "2026-11-10T10:00:00+00:00", "deleted_at": None,
    }
    row.update(kw)
    return row


def _note() -> dict:
    return {
        "item_id": NOTE, "user_id": USER, "conversation_id": CONV, "case_id": None, "kind": "note",
        "created_by": "user", "wi_seq": 1, "title": "a note", "content_md": "note body", "summary": "note sum",
        "summary_source_length": 9, "metadata": {}, "word_count": 2, "storage_path": None, "document_id": None,
        "created_at": "2026-11-09T10:00:00+00:00", "updated_at": "2026-11-09T10:00:00+00:00",
        "deleted_at": None, "is_visible": True,
    }


def db(pointer: dict | None = None, file: dict | None = None, **tables) -> FakeSupabase:
    return FakeSupabase(
        triggers=False,
        workspace_items=[pointer or _pointer(), _note()],
        user_files=[file or _file()],
        conversations=[{"conversation_id": CONV, "user_id": USER, "deleted_at": None}],
        **tables,
    )


@pytest.fixture(params=[False, True], ids=["switch-off", "switch-on"])
def read_on(request, monkeypatch):
    flags.reset_probe()
    with flags.override(read=request.param, write=False):
        yield request.param
    flags.reset_probe()


def want(read_on: bool, key: str):
    return F[key] if read_on else P[key]


def assert_source(sb: FakeSupabase, read_on: bool) -> None:
    """Off: not one ``user_files`` read and no ``file_id`` added to a select."""
    file_reads = [c for t, c in sb.selects if t == "user_files" and c != "file_id"]
    if read_on:
        assert file_reads, "the switch is on: the file must be read"
    else:
        assert [t for t, _c in sb.selects if t == "user_files"] == []
        for table, cols in sb.selects:
            if table == "workspace_items" and cols.strip() != "*":
                assert "file_id" not in [c.strip() for c in cols.split(",")], cols


# ===========================================================================
# Router, orchestrator, unfold
# ===========================================================================


def test_router_workspace_item_summaries(read_on):
    import agents.router.context as rctx

    sb = db()
    summaries, _ = rctx._load_workspace_item_summaries(sb, CONV, USER)
    att = next(s for s in summaries if s["item_id"] == ITEM)
    note = next(s for s in summaries if s["item_id"] == NOTE)
    assert (att["title"], att["summary"], att["doc_type"]) == (
        want(read_on, "title"), want(read_on, "summary"), want(read_on, "doc_type"),
    )
    assert set(att) == {"item_id", "wi_seq", "kind", "title", "summary", "doc_type"}
    assert (note["title"], note["summary"]) == ("a note", "note sum")
    assert_source(sb, read_on)


def test_router_summary_fallback_reads_the_files_text(read_on):
    import agents.router.context as rctx

    sb = db(_pointer(summary=None), _file(summary=None))
    summaries, _ = rctx._load_workspace_item_summaries(sb, CONV, USER)
    att = next(s for s in summaries if s["item_id"] == ITEM)
    assert want(read_on, "content_md").split()[0] in att["summary"]
    assert (F if not read_on else P)["content_md"].split()[0] not in att["summary"]


def test_orchestrator_attached_items(read_on):
    import agents.orchestrator as orch

    sb = db()
    snaps = orch._load_attached_items(sb, [NOTE, ITEM], USER, CONV)
    assert [s.item_id for s in snaps] == [NOTE, ITEM]           # order kept
    att = snaps[1]
    assert att.title == want(read_on, "title")
    assert att.content_md == want(read_on, "content_md")
    assert att.summary == want(read_on, "summary")
    assert att.metadata["side"] == ("file" if read_on else "pointer")
    assert snaps[0].content_md == "note body"
    assert_source(sb, read_on)


def test_unfold_fetch_item_and_unfold_item(read_on):
    import agents.tool_repository.unfold_workspace_item as uw

    sb = db()
    row = uw._fetch_item(sb, ITEM, USER)
    assert set(row) == {"title", "content_md", "reading_corrections"}     # file_id never leaks
    assert (row["title"], row["content_md"]) == (want(read_on, "title"), want(read_on, "content_md"))
    locked = uw._fetch_item(sb, ITEM, USER, with_lock_columns=True)
    assert "file_id" not in locked and locked["content_md"] == want(read_on, "content_md")
    text = uw.unfold_item(sb, ITEM, USER)
    assert want(read_on, "content_md").strip() in text
    assert_source(sb, read_on)


# ===========================================================================
# Writer planner
# ===========================================================================


def test_walkers_fetch_workspace_items(read_on):
    from agents.writer_planner.walkers import _fetch_workspace_items_sync

    sb = db()
    out = _fetch_workspace_items_sync(sb, [ITEM], USER, CONV)
    row = out[ITEM]
    assert row["title"] == want(read_on, "title")
    assert row["content_md"] == want(read_on, "content_md")
    assert row["summary"] == want(read_on, "summary")
    assert row["doc_type"] == want(read_on, "doc_type")
    assert row["word_count"] == (len(F["content_md"].split()) if read_on else P["word_count"])
    assert_source(sb, read_on)


def test_writer_planner_context(read_on):
    from backend.app.services.writer_planner_context import load_writer_planner_context

    sb = db()
    views = asyncio.run(load_writer_planner_context(sb, USER, CONV))
    att = next(v for v in views if v.item_id == ITEM)
    assert (att.title, att.summary, att.doc_type) == (
        want(read_on, "title"), want(read_on, "summary"), want(read_on, "doc_type"),
    )
    assert att.word_count == (len(F["content_md"].split()) if read_on else P["word_count"])
    assert_source(sb, read_on)


# ===========================================================================
# Memory
# ===========================================================================


def test_item_analyzer_load_workspace_items(read_on):
    from agents.memory.item_analyzer.runner import _load_workspace_items

    sb = db()
    [row] = asyncio.run(_load_workspace_items(sb, [ITEM], user_id=USER))
    assert (row.title, row.content_md) == (want(read_on, "title"), want(read_on, "content_md"))
    assert_source(sb, read_on)


def test_compaction_context(read_on):
    import agents.memory.agent as mem

    sb = db()
    _prior, items = mem._load_compaction_context(sb, CONV, USER)
    att = next(i for i in items if i["wi_seq"] == 3)
    assert (att["title"], att["summary"], att["doc_type"]) == (
        want(read_on, "title"), want(read_on, "summary"), want(read_on, "doc_type"),
    )
    assert_source(sb, read_on)


def test_resummarize_dirty_items_judges_the_files_text(read_on, monkeypatch):
    import agents.memory.agent as mem

    called: list[str] = []

    async def fake_summarize(_sb, item_id, *, force=False):
        called.append(item_id)
        return True

    monkeypatch.setattr(mem, "summarize_workspace_item", fake_summarize)
    # pointer: too short to summarize; file: long enough, no summary yet
    sb = db(_pointer(content_md="short", summary=None), _file(summary=None))
    asyncio.run(mem.resummarize_dirty_items(sb, CONV))
    assert called == ([ITEM] if read_on else [])
    assert_source(sb, read_on)


def test_summarize_workspace_item_reads_the_files_text(read_on, monkeypatch):
    import agents.memory.summarize as summ
    from agents.memory.artifact_summarizer import AttachmentSummaryOutput

    seen: dict = {}

    async def fake_run(inp, _deps):
        seen["content_md"] = inp.content_md
        seen["filename"] = inp.filename
        return AttachmentSummaryOutput(title="new title", summary_md="new summary", doc_type="claim")

    monkeypatch.setattr(summ, "run_attachment_summary", fake_run)
    monkeypatch.setattr(summ, "build_artifact_summary_deps", lambda: None)
    monkeypatch.setattr(summ, "_summarize_codec", lambda *_a: None)
    monkeypatch.setattr(summ, "_load_conversation_context", lambda *_a: "")
    monkeypatch.setattr(summ, "_maybe_scope", lambda *_a: nullcontext())
    sb = db(_pointer(summary=None), _file(summary=None))
    assert asyncio.run(summ.summarize_workspace_item(sb, ITEM)) is True
    assert seen == {"content_md": want(read_on, "content_md").strip(), "filename": want(read_on, "title")}
    # write switch off: today's two item updates (marker, then the summary)
    assert [s[1] for s in sb.statements] == ["workspace_items", "workspace_items"]
    assert sb.rpcs == []


def test_segmenter_load_item_and_items(read_on):
    from agents.memory.attachment_segmenter import runner as seg

    sb = db()
    row = seg._load_item(sb, ITEM)
    rows = seg._load_items(sb, [ITEM, NOTE])
    for r in (row, rows[ITEM]):
        assert r["title"] == want(read_on, "title")
        assert r["content_md"] == want(read_on, "content_md")
        assert r["summary"] == want(read_on, "summary")
        assert r["metadata"]["doc_type"] == want(read_on, "doc_type")
    assert rows[NOTE]["content_md"] == "note body"
    assert_source(sb, read_on)


def test_segmenter_bundle_members_and_item_row(read_on):
    from agents.memory.attachment_segmenter import bundles

    links = [{"message_id": MSG, "document_id": ITEM, "created_at": "2026-11-10T10:00:01+00:00"}]
    sb = db(message_attachments=links)
    [member] = bundles.message_members(sb, MSG, USER)
    assert member["chars"] == len(want(read_on, "content_md"))
    assert len(bundles._item_row(sb, ITEM)["content_md"]) == len(want(read_on, "content_md"))
    assert_source(sb, read_on)


def test_segment_maps_staleness_uses_the_files_length(read_on):
    from agents.memory.attachment_segmenter import store as seg_store

    smap = {
        "item_id": ITEM, "user_id": USER, "content_chars": len(F["content_md"]),
        "paragraph_rule_version": "p1", "prompt_version": "v1", "model": None, "split": False,
        "documents_in_file": [], "key_paragraphs": [], "created_at": "2026-11-10T11:00:00+00:00",
    }
    segs = [{
        "item_id": ITEM, "seq": 1, "from_p": 1, "to_p": 1, "page_from": None, "page_to": None,
        "char_start": 0, "char_end": 4, "label": "parties", "value": "core", "what": "", "content_md": "file",
    }]
    sb = db(attachment_segment_maps=[smap], attachment_segments=segs)
    lengths = seg_store._content_lengths(sb, [ITEM, NOTE], USER)
    assert lengths == {ITEM: len(want(read_on, "content_md")), NOTE: len("note body")}
    maps = seg_store.load_segment_maps(sb, [ITEM], user_id=USER)
    assert (ITEM in maps) is read_on            # the map was made from the FILE's text
    if not read_on:
        assert [t for t, _c in sb.selects if t == "user_files"] == []


def test_template_ingester_load_content(read_on):
    from agents.memory.template_ingester.runner import _load_content_md

    sb = db()
    out = _load_content_md(sb, ITEM, user_id=USER, log=logging.getLogger("t"))
    assert out == (want(read_on, "title"), want(read_on, "content_md").strip())
    assert_source(sb, read_on)


# ===========================================================================
# Backend: the pane (workspace_service), OCR and the page gate
# ===========================================================================


def test_workspace_service_lists_and_get(read_on, monkeypatch):
    from backend.app.services import workspace_service as ws

    monkeypatch.setattr(ws, "get_user_id", lambda _sb, _auth: USER)
    sb = db(_pointer(case_id=CASE))
    items, total = ws.list_workspace_items_by_conversation(sb, "auth-1", CONV)
    case_items, _ = ws.list_workspace_items_by_case(sb, "auth-1", CASE)
    one = ws.get_workspace_item(sb, "auth-1", ITEM)
    for row in (next(i for i in items if i["item_id"] == ITEM), case_items[0], one):
        assert row["title"] == want(read_on, "title")
        assert row["content_md"] == want(read_on, "content_md")
        assert row["summary"] == want(read_on, "summary")
        assert row["metadata"]["side"] == ("file" if read_on else "pointer")
        assert row["updated_at"] == "2026-11-10T10:00:00+00:00"     # the item's own
    assert total == 2
    assert next(i for i in items if i["item_id"] == NOTE)["content_md"] == "note body"
    assert_source(sb, read_on)


def test_ocr_unread_check_and_candidates_read_the_files_marker(read_on):
    from agents.memory.ocr_extractor import runner as ocr
    from agents.memory.ocr_extractor.models import OcrExtractionStats

    # pointer: never attempted; file: already done (the marker lives on the file)
    p_meta = {"mime_type": "text/plain", "upload_status": "ready"}
    sb = db(_pointer(metadata=dict(p_meta)), _file(metadata={**p_meta, "ocr_status": "done"}))
    assert ocr.has_unread_attachments(sb, CONV) is False                     # text/plain: never OCR'd
    sb = db(_pointer(metadata={**p_meta, "mime_type": "application/pdf"}),
            _file(metadata={**p_meta, "mime_type": "application/pdf", "ocr_status": "done"}))
    assert ocr.has_unread_attachments(sb, CONV) is (not read_on)

    sb = db(_pointer(metadata=dict(p_meta)), _file(metadata={**p_meta, "ocr_status": "done"}))
    stats = OcrExtractionStats()
    asyncio.run(ocr._run_inner(sb, CONV, USER, stats, SimpleNamespace(STORAGE_BUCKET_DOCUMENTS="documents")))
    if read_on:
        assert stats.candidates == 0 and sb.statements == []
    else:
        assert stats.candidates == 1 and stats.skipped_unsupported == 1
        assert [s[1] for s in sb.statements] == ["workspace_items"]
    assert_source(sb, read_on)


def test_ocr_page_gate_reads_the_files_marker(read_on):
    from backend.app.services import message_service as ms

    sb = db(_pointer(metadata={"page_count": 5}), _file(metadata={"page_count": 5, "ocr_status": "done"}))
    assert ms._attachment_page_counts(sb, [ITEM, NOTE], user_id=USER) == ([] if read_on else [5])
    assert ms._attachment_page_counts(sb, [ITEM]) == ([] if read_on else [5])   # override: no user needed
    assert_source(sb, read_on)


# ===========================================================================
# Allow-list rollout: a reader with no user reads the item; one with a user
# on the list reads the file.
# ===========================================================================


def test_allow_list_user_less_readers_stay_on_the_item(monkeypatch):
    import agents.router.context as rctx
    from agents.memory.attachment_segmenter import runner as seg

    flags.reset_probe()
    monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(
        USER_FILES_READ_ENABLED=True, USER_FILES_READ_USERS=USER,
        USER_FILES_WRITE_ENABLED=False, USER_FILES_WRITE_USERS=None,
    ))
    try:
        sb = db()
        summaries, _ = rctx._load_workspace_item_summaries(sb, CONV, USER)
        assert next(s for s in summaries if s["item_id"] == ITEM)["title"] == F["title"]
        assert seg._load_item(sb, ITEM)["title"] == P["title"]
    finally:
        flags.reset_probe()


def test_schema_not_ready_every_reader_stays_on_the_item():
    import agents.orchestrator as orch

    flags.reset_probe()
    try:
        with flags.override(read=True):
            sb = db()
            sb.schema_ready = False
            [snap] = orch._load_attached_items(sb, [ITEM], USER, CONV)
            assert snap.title == P["title"]
            assert [c for t, c in sb.selects if t == "workspace_items"] == [
                "item_id, wi_seq, kind, title, content_md, summary, metadata",
            ]
    finally:
        flags.reset_probe()
