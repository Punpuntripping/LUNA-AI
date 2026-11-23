"""The OCR side of the send gate under the page budget (plan ocr_page_budget.md D9).

A file bigger than what is left is no longer refused — the OCR runner reads the
pages the budget allows. The gate refuses only when NOT ONE page can be read.
The counts it gets leave out what OCR will never read (blog chips, notes, files
already extracted).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.services import message_service as ms
from shared import quota

USER = "aaaaaaaa-0000-0000-0000-000000000001"


def _state(limit, used=0):
    return {
        "plan_id": "pro", "effective_plan_id": "pro", "locked": False,
        "ocr_pages_monthly": limit, "ocr_pages": used, "ocr_oldest": None,
    }


@pytest.fixture
def gate(monkeypatch):
    holder: dict[str, Any] = {}

    async def state(_sb, _uid):
        return holder["st"]

    async def block(_sb, plan, meter, period, used, limit, resets, reason="limit"):
        return quota.QuotaExceeded(meter, period, used, limit, resets, plan_id=plan)

    async def ds_gate(_sb, _st):
        return None

    monkeypatch.setattr(quota, "_quota_state", state)
    monkeypatch.setattr(quota, "_quota_block", block)
    monkeypatch.setattr(quota, "_deep_search_gate", ds_gate)

    async def run(st, **kw):
        holder["st"] = st
        return await quota.check(None, None, USER, needs_ord=False, **kw)

    return run


@pytest.mark.asyncio
async def test_big_file_with_pages_left_passes(gate):
    await gate(_state(150), ocr_page_counts=[606])  # Khalid's file on Pro: no longer refused
    await gate(_state(50), ocr_page_counts=[606])   # nor on the 50-page trial


@pytest.mark.asyncio
async def test_no_page_left_blocks(gate):
    with pytest.raises(quota.QuotaExceeded) as e:
        await gate(_state(150, used=150), ocr_page_counts=[3])
    assert e.value.meter == "ocr" and e.value.used == 150 and e.value.limit == 150


@pytest.mark.asyncio
async def test_plan_without_ocr_blocks_a_file(gate):
    with pytest.raises(quota.QuotaExceeded):
        await gate(_state(0), ocr_page_counts=[1])


@pytest.mark.asyncio
async def test_nothing_to_ocr_never_blocks(gate):
    await gate(_state(0), ocr_page_counts=[])
    await gate(_state(150, used=150), ocr_page_counts=[])


@pytest.mark.asyncio
async def test_unlimited_plan_passes(gate):
    await gate(_state(None, used=10_000), ocr_page_counts=[606])


@pytest.mark.asyncio
async def test_legacy_projection_unchanged(gate):
    with pytest.raises(quota.QuotaExceeded):
        await gate(_state(150), needs_ocr=True, est_ocr_pages=606)


# ── what the gate is told to count ───────────────────────────────────────────

class _DB:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail = rows or [], fail

    def table(self, _name):
        return self

    def select(self, *_a):
        return self

    def in_(self, _col, ids):
        self.ids = set(ids)
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("db down")
        return SimpleNamespace(data=[r for r in self.rows if r["item_id"] in self.ids])


def test_page_counts_skip_what_ocr_never_reads():
    db = _DB([
        {"item_id": "big", "kind": "attachment", "metadata": {"page_count": 606}},
        {"item_id": "done", "kind": "attachment", "metadata": {"page_count": 40, "ocr_status": "done"}},
        {"item_id": "blog", "kind": "agent_search", "metadata": {}},
        {"item_id": "unknown", "kind": "attachment", "metadata": {}},
    ])
    counts = ms._attachment_page_counts(db, ["big", "done", "blog", "unknown", "missing"])
    assert counts == [606, 1, 1]
    assert ms._estimate_ocr_pages(db, ["big", "unknown"]) == 607


# ── a PDF is never refused: past the budget it is read from its text layer ───

_PDF_ROWS = [
    {"item_id": "pdf", "kind": "attachment",
     "metadata": {"page_count": 606, "mime_type": "application/pdf"}},
    {"item_id": "img", "kind": "attachment", "metadata": {"mime_type": "image/png"}},
    {"item_id": "old", "kind": "attachment",
     "metadata": {"page_count": 9, "mime_type": "application/pdf", "ocr_status": "done"}},
]


def _overflow(monkeypatch, on):
    from agents.memory.ocr_extractor import runner as ocr_runner

    monkeypatch.setattr(
        ocr_runner, "get_settings",
        lambda: SimpleNamespace(OCR_TEXT_LAYER_OVERFLOW_ENABLED=on),
    )


def test_gate_counts_nothing_for_a_send_with_a_new_pdf(monkeypatch):
    _overflow(monkeypatch, True)
    db = _DB(_PDF_ROWS)
    assert ms._attachment_page_counts(db, ["pdf", "img"], for_gate=True) == []
    # images alone are still counted — no text layer to fall back on
    assert ms._attachment_page_counts(db, ["img"], for_gate=True) == [1]
    # a PDF extracted before is not read again: it cannot open the gate
    assert ms._attachment_page_counts(db, ["old", "img"], for_gate=True) == [1]
    # nor can a scan already found to have no text layer
    scan = {"item_id": "scan", "kind": "attachment",
            "metadata": {"page_count": 4, "mime_type": "application/pdf", "ocr_no_text_layer": True}}
    assert ms._attachment_page_counts(_DB([scan]), ["scan"], for_gate=True) == [4]
    # the plain count (eval scripts) is unchanged
    assert ms._attachment_page_counts(db, ["pdf", "img"]) == [606, 1]


def test_gate_counts_pdfs_as_before_with_the_flag_off(monkeypatch):
    _overflow(monkeypatch, False)
    assert ms._attachment_page_counts(_DB(_PDF_ROWS), ["pdf", "img"], for_gate=True) == [606, 1]


@pytest.mark.asyncio
async def test_no_page_left_a_pdf_passes_images_alone_are_refused(gate, monkeypatch):
    _overflow(monkeypatch, True)
    db = _DB(_PDF_ROWS)
    await gate(_state(150, used=150),
               ocr_page_counts=ms._attachment_page_counts(db, ["pdf"], for_gate=True))
    with pytest.raises(quota.QuotaExceeded):
        await gate(_state(150, used=150),
                   ocr_page_counts=ms._attachment_page_counts(db, ["img"], for_gate=True))


MB = 1024 * 1024


def _init(size, **kw):
    from backend.app.services import upload_session_service as us

    return us.init_upload(
        None, user_id="u", case_id=None, conversation_id="c",
        filename="f.pdf", mime_type="application/pdf", size_bytes=size, **kw,
    )


def test_attachments_go_to_250_mb_case_documents_stay_at_50():
    from backend.app.errors import LunaHTTPException
    from backend.app.services import upload_session_service as us

    assert _init(202 * MB, max_size=us.ATTACHMENT_MAX_FILE_SIZE)["size_bytes"] == 202 * MB
    with pytest.raises(LunaHTTPException) as e:
        _init(251 * MB, max_size=us.ATTACHMENT_MAX_FILE_SIZE)
    assert "250" in str(e.value.detail)
    with pytest.raises(LunaHTTPException) as e:
        _init(60 * MB)  # the default — case documents
    assert "50" in str(e.value.detail)


def test_init_request_outer_bound_is_250_mb():
    from pydantic import ValidationError

    from backend.app.models.requests import UploadInitRequest

    UploadInitRequest(filename="f.pdf", mime_type="application/pdf", size_bytes=250 * MB)
    with pytest.raises(ValidationError):
        UploadInitRequest(filename="f.pdf", mime_type="application/pdf", size_bytes=250 * MB + 1)


def test_ocr_progress_reaches_the_client_and_the_notices_are_saved(monkeypatch):
    """`status` lines never reach the client; the OCR step's indicator and its
    notices ride `ocr_progress` and the notices land in metadata.ocr_notices."""
    import json

    from backend.tests.test_deep_search_gate import CONV, USER as GATE_USER, _MsSupabase, run
    from shared.privacy import PrivacyCodec

    notice = "استخرجنا 100 صفحة من أصل 606 من «ملف.pdf» حسب رصيدك: 1–60، 314–333، 587–606. بقية الصفحات لم تُستخرج."

    async def _check(*_a, **_k):
        return None

    async def _handle(**_kw):
        yield {"type": "ocr_progress", "phase": "extracting"}
        yield {"type": "status", "text": "internal line — never relayed"}
        yield {"type": "ocr_progress", "phase": "done", "notices": [notice]}
        yield {"type": "token", "text": "الجواب"}
        yield {"type": "done", "usage": {}}

    monkeypatch.setattr(quota, "check", _check)
    monkeypatch.setattr(ms, "handle_message", _handle)
    monkeypatch.setattr(ms, "_insert_turn_rows", lambda *a, **k: None)
    monkeypatch.setattr(ms, "write_audit_log", lambda *a, **k: None)
    monkeypatch.setattr(ms, "_merged_message_metadata", lambda _sb, _mid, patch: dict(patch))
    monkeypatch.setattr(ms, "build_turn_codec",
                        lambda *a, **k: PrivacyCodec(real_to_fake={}, fake_to_real={}, enabled=False))

    async def _connected() -> bool:
        return False

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis=None)), is_disconnected=_connected,
    )
    fake = _MsSupabase()

    async def _drain() -> list[str]:
        return [chunk async for chunk in ms.send_message_stream(
            fake, user_id=GATE_USER, conversation_id=CONV,
            conv={"conversation_id": CONV, "case_id": None, "message_count": 2},
            content="اش تقول الدعوى 410654", request=request, attachment_ids=None,
        )]

    events = []
    for chunk in run(_drain()):
        lines = dict(ln.split(": ", 1) for ln in chunk.strip().splitlines() if ": " in ln)
        events.append((lines.get("event", ""), json.loads(lines.get("data", "{}"))))

    ocr = [d for n, d in events if n == "ocr_progress"]
    assert ocr == [{"phase": "extracting", "notices": []}, {"phase": "done", "notices": [notice]}]
    assert "status" not in [n for n, _ in events]
    metas = [u["metadata"] for t, u in fake.updates if t == "messages" and "metadata" in u]
    assert metas and metas[-1]["ocr_notices"] == [notice]


def test_page_counts_on_db_failure():
    assert ms._attachment_page_counts(_DB(fail=True), ["a", "b"]) == [1, 1]
    assert ms._attachment_page_counts(_DB(), []) == []
