"""User text corrections reach every whole-text reader (attachment_agent.md §3.3, D12/D15).

A correction (``attachment_text_corrections``: wrong → right, every occurrence, keyed by
the FILE) is an overlay applied at read time; ``content_md`` is never rewritten. Each
reader hook is run three ways, under both files read switches:

- ``fixed`` — a correction row: the corrected text reaches the consumer;
- ``none``  — no rows: the consumer gets the stored text, byte for byte;
- ``down``  — the corrections read fails: the stored text, no error.

Lengths that drive freshness stay on the REAL text (the summary's
``summary_source_length``). The section loader, the «الملفات» file view, the angle-map
dialog, the extended view and the segmenter have their own tests beside their suites
(``backend/tests/test_files_service.py``, ``backend/tests/test_file_angle_maps.py``,
``agents/deep_search_v4/extended/tests/test_view_text_overlay.py``,
``agents/memory/attachment_segmenter/tests/test_text_overlay.py``). Invented text only.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from shared.attachments import text_corrections as tc
from shared.files import flags
from shared.files.tests.user_files_fake import FakeSupabase

USER = "11111111-1111-1111-1111-111111111111"
CONV = "cccccccc-0000-0000-0000-000000000001"
CASE = "cccccccc-0000-0000-0000-0000000000ca"
ITEM = "aaaaaaaa-0000-0000-0000-000000000001"    # the file's first item: file_id = item_id
COPY = "aaaaaaaa-0000-0000-0000-000000000002"    # the same file attached again («الملفات»)
NOTE = "aaaaaaaa-0000-0000-0000-0000000000ff"
T = "attachment_text_corrections"

SENTENCE = "حضر محمود الجلسة، وقال محمود إن العقد صحيح ولا نزاع في توقيعه. "
TEXT = SENTENCE * 8                                # > 300 chars: the summarizer runs
FIXED = TEXT.replace("محمود", "محمد")
NOTE_TEXT = "ملاحظة: محمود لم يحضر."               # a note is never corrected


def _pointer(item_id: str = ITEM, *, file_id: str = ITEM, **kw) -> dict:
    row = {
        "item_id": item_id, "file_id": file_id, "user_id": USER, "conversation_id": CONV, "case_id": CASE,
        "message_id": None, "kind": "attachment", "created_by": "user", "wi_seq": 3,
        "title": "محضر جلسة", "content_md": TEXT, "summary": "ملخص المحضر",
        "summary_source_length": len(TEXT), "summary_updated_at": None,
        "metadata": {"doc_type": "minutes", "filename": "محضر.pdf", "upload_status": "ready"},
        "storage_path": "general/u/convos/c/m.pdf", "document_id": None, "is_visible": True,
        "word_count": 90, "created_at": "2026-11-10T10:00:00+00:00",
        "updated_at": "2026-11-10T10:00:00+00:00", "deleted_at": None, "created_while_paid": True,
    }
    row.update(kw)
    return row


def _file(**kw) -> dict:
    row = {
        "file_id": ITEM, "user_id": USER, "title": "محضر جلسة", "content_md": TEXT, "summary": "ملخص المحضر",
        "summary_source_length": len(TEXT), "summary_updated_at": None,
        "metadata": {"doc_type": "minutes", "filename": "محضر.pdf", "upload_status": "ready"},
        "storage_path": "general/u/convos/c/m.pdf", "document_id": None, "origin": "chat",
        "library_hidden_at": None, "created_at": "2026-11-10T10:00:00+00:00",
        "updated_at": "2026-11-10T10:00:00+00:00", "deleted_at": None,
    }
    row.update(kw)
    return row


def _note() -> dict:
    return {
        "item_id": NOTE, "user_id": USER, "conversation_id": CONV, "case_id": CASE, "kind": "note",
        "created_by": "user", "wi_seq": 1, "title": "ملاحظة", "content_md": NOTE_TEXT, "summary": None,
        "summary_source_length": None, "metadata": {}, "word_count": 4, "storage_path": None,
        "document_id": None, "created_at": "2026-11-09T10:00:00+00:00",
        "updated_at": "2026-11-09T10:00:00+00:00", "deleted_at": None, "is_visible": True,
    }


def _fix(file_id: str = ITEM) -> dict:
    return {"id": "c1", "file_id": file_id, "user_id": USER, "wrong_text": "محمود", "right_text": "محمد",
            "occurrences": 16, "evidence": "الاسم محمد لا محمود", "created_at": "2026-11-27T10:00:00+00:00",
            "reverted_at": None}


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


@pytest.fixture(params=["fixed", "none", "down"])
def mode(request) -> str:
    return request.param


def db(mode: str, *pointers: dict, **tables) -> FakeSupabase:
    sb = FakeSupabase(
        triggers=False,
        workspace_items=[*(pointers or [_pointer()]), _note()],
        user_files=[_file()],
        conversations=[{"conversation_id": CONV, "user_id": USER, "deleted_at": None}],
        attachment_text_corrections=[_fix()] if mode == "fixed" else [],
        **tables,
    )
    if mode == "down":
        sb.fail_tables.add(T)
    return sb


def want(mode: str) -> str:
    return FIXED if mode == "fixed" else TEXT


def corrections_reads(sb: FakeSupabase) -> int:
    return sum(1 for t, _c in sb.selects if t == T)


# ===========================================================================
# 1. Router — the no-summary fallback, before its 1,500 / 6,000 cut
# ===========================================================================


def test_router_summary_fallback(read_on, mode):
    import agents.router.context as rctx

    sb = db(mode, _pointer(summary=None))
    sb.tables["user_files"][0]["summary"] = None
    summaries, _ = rctx._load_workspace_item_summaries(sb, CONV, USER)
    att = next(s for s in summaries if s["item_id"] == ITEM)
    note = next(s for s in summaries if s["item_id"] == NOTE)
    assert att["summary_is_content"] is True
    assert att["summary"] == want(mode)[:rctx._SUMMARY_FALLBACK_ITEM_CHARS].rstrip().strip()
    assert note["summary"] == NOTE_TEXT                   # only attachments are corrected


def test_router_reads_no_corrections_for_items_with_a_summary(read_on):
    import agents.router.context as rctx

    sb = db("fixed")
    summaries, _ = rctx._load_workspace_item_summaries(sb, CONV, USER)
    assert next(s for s in summaries if s["item_id"] == ITEM)["summary"] == "ملخص المحضر"
    assert corrections_reads(sb) == 0


# ===========================================================================
# 2. unfold_workspace_item — planner / writer planner, REAL text before the codec
# ===========================================================================


def test_unfold_item(read_on, mode):
    import agents.tool_repository.unfold_workspace_item as uw

    sb = db(mode)
    text = uw.unfold_item(sb, ITEM, USER)
    assert want(mode).strip() in text
    assert ("محمود" in text) is (mode != "fixed")
    assert uw.unfold_item(sb, NOTE, USER).strip() == NOTE_TEXT


def test_unfold_encodes_the_corrected_text(read_on, monkeypatch):
    """Masking runs on the corrected REAL text: a codec that masks «محمد» sees it."""
    import agents.tool_repository.unfold_workspace_item as uw
    import backend.app.services.masking_service as ms

    codec = SimpleNamespace(encode=lambda t: t.replace("محمد", "شخص-1"))
    monkeypatch.setattr(ms, "active_codec", lambda: codec)
    monkeypatch.setattr(ms, "persist_new_mappings", lambda *_a, **_k: None)
    deps = SimpleNamespace(supabase=db("fixed"), user_id=USER, wi_alias_map={3: ITEM})
    out = asyncio.run(uw.unfold_for_deps(deps, "WI-3"))
    assert "شخص-1" in out and "محمود" not in out and "محمد" not in out


# ===========================================================================
# 3. _load_attached_items — planner <attached_items>, case documents, simple search…
# ===========================================================================


def test_orchestrator_attached_items(read_on, mode):
    import agents.orchestrator as orch

    sb = db(mode)
    snaps = orch._load_attached_items(sb, [NOTE, ITEM], USER, CONV)
    assert [s.item_id for s in snaps] == [NOTE, ITEM]
    assert snaps[1].content_md == want(mode)
    assert snaps[0].content_md == NOTE_TEXT
    assert corrections_reads(sb) == 1                     # one read for every row


def test_a_library_copy_follows_its_file():
    """D15: the same file attached in another conversation item reads its file's fixes
    (read switch on — as in production)."""
    import agents.orchestrator as orch
    from agents.writer_planner.walkers import _fetch_workspace_items_sync

    with flags.override(read=True, write=False):
        sb = db("fixed", _pointer(COPY, file_id=ITEM))
        [snap] = orch._load_attached_items(sb, [COPY], USER, CONV)
        rows = _fetch_workspace_items_sync(sb, [COPY], USER, CONV)
    assert snap.content_md == FIXED and rows[COPY]["content_md"] == FIXED


# ===========================================================================
# 4. Writer planner walkers — the writer's whole-text bodies
# ===========================================================================


def test_walkers_fetch_workspace_items(read_on, mode):
    from agents.writer_planner.walkers import _fetch_workspace_items_sync

    sb = db(mode)
    out = _fetch_workspace_items_sync(sb, [ITEM, NOTE], USER, CONV)
    assert out[ITEM]["content_md"] == want(mode)
    assert out[NOTE]["content_md"] == NOTE_TEXT


def test_walkers_build_items_carry_the_corrected_body(read_on, monkeypatch):
    from agents.writer_planner import walkers

    monkeypatch.setattr(walkers, "_attachment_bodies", _no_bodies)
    deps = SimpleNamespace(supabase=db("fixed"), user_id=USER, conversation_id=CONV)
    [item] = asyncio.run(walkers.build_analyzed_items_direct([ITEM], {ITEM: "template"}, deps))
    assert item.body_md == FIXED


async def _no_bodies(*_a, **_k) -> dict:
    return {}


# ===========================================================================
# 5. Workspace routes — the web / app pane
# ===========================================================================


def test_workspace_service_lists_and_get(read_on, mode, monkeypatch):
    from backend.app.services import workspace_service as ws

    monkeypatch.setattr(ws, "get_user_id", lambda _sb, _auth: USER)
    sb = db(mode)
    items, _ = ws.list_workspace_items_by_conversation(sb, "auth-1", CONV)
    case_items, _ = ws.list_workspace_items_by_case(sb, "auth-1", CASE)
    one = ws.get_workspace_item(sb, "auth-1", ITEM, text_corrections=True)
    for rows in (items, case_items):
        assert next(i for i in rows if i["item_id"] == ITEM)["content_md"] == want(mode)
        assert next(i for i in rows if i["item_id"] == NOTE)["content_md"] == NOTE_TEXT
    assert one["content_md"] == want(mode)


def test_workspace_ownership_gate_reads_no_corrections(read_on, monkeypatch):
    """``get_workspace_item`` is also the ownership gate of the write routes: unless the
    GET route asks, it returns the stored row and never reads the corrections."""
    from backend.app.services import workspace_service as ws

    monkeypatch.setattr(ws, "get_user_id", lambda _sb, _auth: USER)
    sb = db("fixed")
    assert ws.get_workspace_item(sb, "auth-1", ITEM)["content_md"] == TEXT
    assert corrections_reads(sb) == 0


def test_workspace_get_route_asks_for_the_corrected_text(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import workspace as workspace_api
    from backend.app.deps import get_current_user, get_supabase

    seen: dict = {}

    def fake_get(_sb, auth_id, item_id, **kw):
        seen.update(kw)
        return _pointer(content_md=FIXED)

    async def no_previews(_sb, _rows):
        return {}

    monkeypatch.setattr(workspace_api.workspace_service, "get_workspace_item", fake_get)
    monkeypatch.setattr(workspace_api.wi_preview_service, "previews_for", no_previews)
    monkeypatch.setattr(workspace_api.wi_preview_service, "note_served", lambda *_a, **_k: None)
    app = FastAPI()
    app.include_router(workspace_api.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(auth_id="auth-1")
    app.dependency_overrides[get_supabase] = lambda: object()
    resp = TestClient(app).get(f"/api/v1/workspace/{ITEM}")
    assert resp.status_code == 200, resp.text
    assert seen == {"text_corrections": True}
    assert resp.json()["content_md"] == FIXED


# ===========================================================================
# 7. Template ingester
# ===========================================================================


def test_template_ingester_load_content(read_on, mode):
    from agents.memory.template_ingester.runner import _load_content_md

    sb = db(mode)
    out = _load_content_md(sb, ITEM, user_id=USER, log=logging.getLogger("t"))
    assert out == ("محضر جلسة", want(mode).strip())


# ===========================================================================
# 8. Deep-search + writer sections — before the codec
# ===========================================================================


def _segmented(mode: str) -> FakeSupabase:
    smap = {
        "item_id": ITEM, "user_id": USER, "content_chars": len(TEXT),
        "paragraph_rule_version": "p1", "prompt_version": "v1", "model": None, "split": False,
        "documents_in_file": [], "key_paragraphs": [], "created_at": "2026-11-10T11:00:00+00:00",
    }
    half = len(SENTENCE) * 4
    segs = [
        {"item_id": ITEM, "seq": 1, "from_p": 1, "to_p": 1, "page_from": 1, "page_to": 1,
         "char_start": 0, "char_end": half, "label": "parties", "value": "core", "what": "الحضور",
         "content_md": TEXT[:half]},
        {"item_id": ITEM, "seq": 2, "from_p": 2, "to_p": 2, "page_from": 2, "page_to": 2,
         "char_start": half, "char_end": len(TEXT), "label": "facts", "value": "supporting", "what": "الأقوال",
         "content_md": TEXT[half:]},
    ]
    return db(mode, attachment_segment_maps=[smap], attachment_segments=segs)


def _ready(monkeypatch) -> None:
    import agents.helpers as helpers

    async def ready(helper, item_ids, **_kw):
        return {i: SimpleNamespace(status="ready") for i in item_ids}

    monkeypatch.setattr(helpers, "ensure_ready", ready)


def test_wait_and_load_sections(read_on, mode, monkeypatch):
    from agents.utils import attachment_sections

    _ready(monkeypatch)
    sb = _segmented(mode)
    out, _tel = asyncio.run(attachment_sections.wait_and_load_sections(
        [ITEM], consumer="writer", supabase=sb, user_id=USER,
    ))
    secs = out[ITEM]
    assert "".join(s.text for s in secs) == want(mode)
    # positions stay on the REAL text (citation pins are placed by them)
    assert [(s.char_start, s.char_end) for s in secs] == [(0, len(SENTENCE) * 4), (len(SENTENCE) * 4, len(TEXT))]


def test_sections_are_corrected_before_the_codec(monkeypatch):
    from agents.utils import attachment_sections
    import backend.app.services.masking_service as ms

    _ready(monkeypatch)
    monkeypatch.setattr(ms, "active_codec", lambda: object())
    monkeypatch.setattr(ms, "encode_active", lambda t: t.replace("محمد", "شخص-1"))
    out, _ = asyncio.run(attachment_sections.wait_and_load_sections(
        [ITEM], consumer="aggregator", supabase=_segmented("fixed"), user_id=USER, encode=True,
    ))
    text = "".join(s.text for s in out[ITEM])
    assert text == TEXT.replace("محمود", "شخص-1")


# ===========================================================================
# 13. Summary input — corrected text to the model, REAL length stored
# ===========================================================================


def test_summary_input_is_corrected_and_the_source_length_stays_real(read_on, mode, monkeypatch):
    import agents.memory.summarize as summ
    from agents.memory.artifact_summarizer import AttachmentSummaryOutput

    seen: dict = {}

    async def fake_run(inp, _deps):
        seen["content_md"] = inp.content_md
        return AttachmentSummaryOutput(title="محضر", summary_md="ملخص", doc_type="minutes")

    def fake_persist(*_a, source_length, **_kw):
        seen["source_length"] = source_length

    monkeypatch.setattr(summ, "run_attachment_summary", fake_run)
    monkeypatch.setattr(summ, "build_artifact_summary_deps", lambda: None)
    monkeypatch.setattr(summ, "_summarize_codec", lambda *_a: None)
    monkeypatch.setattr(summ, "_load_conversation_context", lambda *_a: "")
    monkeypatch.setattr(summ, "_maybe_scope", lambda *_a: nullcontext())
    monkeypatch.setattr(summ, "_persist_attachment_summary", fake_persist)
    sb = db(mode, _pointer(summary=None))
    sb.tables["user_files"][0]["summary"] = None
    assert asyncio.run(summ.summarize_workspace_item(sb, ITEM)) is True
    assert seen["content_md"] == want(mode).strip()
    assert seen["source_length"] == len(TEXT.strip())     # resummarize_dirty_items compares this


# ===========================================================================
# The flag gate — only the attachment agent writes corrections: with it off for
# the user, every reader makes ZERO extra requests and returns today's bytes.
# ===========================================================================

OVERRIDES = "attachment_segment_overrides"


def overlay_requests(sb: FakeSupabase) -> list[tuple[str, str]]:
    """Corrections / promotions reads."""
    return [(t, c) for t, c in sb.selects if t in (T, OVERRIDES)]


def store_lookups(read_on: bool) -> int:
    """What ``load_segment_maps`` itself selects as ``item_id, file_id`` (its file
    keys; with the read switch on, also ``content_chars_for_items``) — not the overlay's."""
    return 2 if read_on else 1


def file_lookups(sb: FakeSupabase) -> int:
    """Item → file lookups (``file_ids_for_items``)."""
    return sum(1 for t, c in sb.selects if t == "workspace_items" and c.replace(" ", "") == "item_id,file_id")


def _every_hook(sb: FakeSupabase, monkeypatch) -> list[str]:
    import agents.orchestrator as orch
    import agents.router.context as rctx
    import agents.tool_repository.unfold_workspace_item as uw
    from agents.memory.template_ingester.runner import _load_content_md
    from agents.utils import attachment_sections
    from agents.writer_planner.walkers import _fetch_workspace_items_sync
    from backend.app.services import workspace_service as ws

    monkeypatch.setattr(ws, "get_user_id", lambda _sb, _auth: USER)
    _ready(monkeypatch)
    summaries, _ = rctx._load_workspace_item_summaries(sb, CONV, USER)
    secs, _ = asyncio.run(attachment_sections.wait_and_load_sections(
        [ITEM], consumer="writer", supabase=sb, user_id=USER,
    ))
    return [
        next(s for s in summaries if s["item_id"] == ITEM)["summary"],
        uw.unfold_item(sb, ITEM, USER).strip(),
        orch._load_attached_items(sb, [ITEM], USER, CONV)[0].content_md,
        _fetch_workspace_items_sync(sb, [ITEM], USER, CONV)[ITEM]["content_md"],
        next(i for i in ws.list_workspace_items_by_conversation(sb, "a", CONV)[0] if i["item_id"] == ITEM)["content_md"],
        ws.get_workspace_item(sb, "a", ITEM, text_corrections=True)["content_md"],
        _load_content_md(sb, ITEM, user_id=USER, log=logging.getLogger("t"))[1],
        "".join(s.text for s in secs[ITEM]),
    ]


@pytest.mark.parametrize("users", [None, "someone-else"], ids=["flag-off", "not-on-the-allowlist"])
def test_flag_off_every_reader_makes_no_extra_request(read_on, monkeypatch, users):
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", users is not None)
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_USERS", users)
    sb = _segmented("fixed")
    sb.tables["workspace_items"][0]["summary"] = None
    sb.tables["user_files"][0]["summary"] = None
    sb.tables[OVERRIDES] = [{"id": "o1", "file_id": ITEM, "user_id": USER, "char_start": 0, "char_end": 10,
                             "value": "core", "created_at": "2026-11-27T10:00:00+00:00", "reverted_at": None}]
    texts = _every_hook(sb, monkeypatch)
    assert [t.strip() for t in texts] == [TEXT.strip()] * len(texts)
    assert overlay_requests(sb) == []
    assert file_lookups(sb) == store_lookups(read_on)     # the segment loader's own, as before


def test_flag_on_the_same_readers_do_correct(read_on, monkeypatch):
    sb = _segmented("fixed")
    sb.tables["workspace_items"][0]["summary"] = None
    sb.tables["user_files"][0]["summary"] = None
    texts = _every_hook(sb, monkeypatch)
    assert [t.strip() for t in texts] == [FIXED.strip()] * len(texts)
    # the sections reuse the loader's file keys; only unfold (no file_id in its row,
    # read switch on) looks its file up
    assert file_lookups(sb) == store_lookups(read_on) + (1 if read_on else 0)


def test_unfold_of_a_report_never_looks_for_corrections(read_on):
    import agents.tool_repository.unfold_workspace_item as uw

    report = _pointer("aaaaaaaa-0000-0000-0000-0000000000ee", file_id=None, kind="agent_search")
    sb = db("fixed", _pointer(), report)
    assert "محمود" in uw.unfold_item(sb, report["item_id"], USER)
    assert overlay_requests(sb) == [] and file_lookups(sb) == 0
