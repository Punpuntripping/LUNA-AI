"""OCR page rows (migration 215): content_md unchanged, exact page offsets, rows
written only with the segmenter flag on, and a failed write never fails OCR."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.memory.attachment_segmenter.tests.fake_supabase import FakeSupabase
from agents.memory.ocr_extractor import mistral_ocr
from agents.memory.ocr_extractor import runner as ocr_runner
from agents.memory.ocr_extractor.mistral_ocr import join_pages
from agents.memory.ocr_extractor.models import OcrDocumentResult, OcrPage


def _legacy(markdowns: list[str]) -> str:
    """content_md exactly as OCR built it before page rows existed."""
    return "\n\n".join(markdowns).strip()


CASES = [
    ["# صفحة ١", "نص الصفحة ٢", "خاتمة"],
    ["  \n# بداية بفراغ", "وسط", "نهاية بفراغ \n\n  "],
    ["", "", "أول نص بعد صفحتين فارغتين", "", "بعد صفحة فارغة", ""],
    ["\n\n\n", "x"],
    ["only page"],
    ["", ""],
    [],
]


@pytest.mark.parametrize("pages", CASES)
def test_text_is_byte_identical(pages):
    text, offsets = join_pages(pages)
    assert text == _legacy(pages)
    assert len(offsets) == len(pages)


@pytest.mark.parametrize("pages", CASES)
def test_offsets_point_at_each_page(pages):
    text, offsets = join_pages(pages)
    for md, off in zip(pages, offsets):
        assert 0 <= off <= len(text)
        body = md.strip()
        if body:
            # the page's own text starts at its offset (after any leading blank
            # the strip of the whole document removed, for the first page)
            assert text[off:].lstrip().startswith(body)
            assert text.find(body, off) == off + (len(text[off:]) - len(text[off:].lstrip()))
    assert offsets == sorted(offsets)


def test_inner_pages_slice_exactly():
    pages = ["a1\na2", "b1\n\nb2  ", "  c1"]
    text, offsets = join_pages(pages)
    assert text[offsets[1]:offsets[1] + len(pages[1])] == pages[1]
    assert text[offsets[2]:] == pages[2]  # last page, no trailing blank to strip
    assert offsets[0] == 0


def test_leading_whitespace_of_the_first_page():
    text, offsets = join_pages(["\n  عنوان", "ثانية"])
    assert offsets == [0, len("عنوان") + 2]
    assert text[offsets[1]:] == "ثانية"


def test_empty_pages_clamp():
    text, offsets = join_pages(["", "", "نص", ""])
    assert text == "نص"
    assert offsets == [0, 0, 0, 2]


def test_page_numbers_follow_mistral_index():
    pages = [SimpleNamespace(index=i, markdown=f"p{i}") for i in (0, 1, 2)]
    assert mistral_ocr._page_numbers(pages) == [1, 2, 3]
    odd = [SimpleNamespace(index=None, markdown="a"), SimpleNamespace(index=None, markdown="b")]
    assert mistral_ocr._page_numbers(odd) == [1, 2]
    dup = [SimpleNamespace(index=0, markdown="a"), SimpleNamespace(index=0, markdown="b")]
    assert mistral_ocr._page_numbers(dup) == [1, 2]


@pytest.mark.asyncio
async def test_ocr_document_returns_pages(monkeypatch):
    md = ["  رأس", "متن", ""]
    response = SimpleNamespace(
        pages=[SimpleNamespace(index=i, markdown=m) for i, m in enumerate(md)], model="mistral-ocr-x"
    )

    class _OCR:
        async def process_async(self, **_k):
            return response

    class _Client:
        def __init__(self, **_k):
            self.ocr = _OCR()
            self.sdk_configuration = SimpleNamespace(async_client=None)

    monkeypatch.setattr(mistral_ocr, "Mistral", _Client)
    monkeypatch.setattr(mistral_ocr, "get_settings", lambda: SimpleNamespace(MISTRAL_OCR_MODEL="m", MISTRAL_API_KEY="k"))
    res = await mistral_ocr.ocr_document("https://x", "application/pdf")
    assert res.text == _legacy(md)
    assert [(p.page_no, p.markdown, p.char_start) for p in res.pages] == [(1, "  رأس", 0), (2, "متن", 5), (3, "", 8)]
    assert res.page_count == 3


# ── runner ───────────────────────────────────────────────────────────────────

ITEM = "item-1"
USER = "user-1"


def _db() -> FakeSupabase:
    return FakeSupabase({"workspace_items": [{
        "item_id": ITEM, "conversation_id": "c1", "kind": "attachment", "deleted_at": None,
        "storage_path": "u/f.pdf", "metadata": {"mime_type": "application/pdf", "file_size_bytes": 10},
    }]})


@pytest.fixture
def ocr_env(monkeypatch):
    md = ["  صفحة أولى", "صفحة ثانية"]
    text, offsets = join_pages(md)
    result = OcrDocumentResult(
        text=text, page_count=2, model="mistral-ocr-x",
        pages=[OcrPage(i + 1, m, o) for i, (m, o) in enumerate(zip(md, offsets))],
    )

    async def fake_ocr(url, mime):
        return result

    monkeypatch.setattr(ocr_runner, "ocr_document", fake_ocr)
    monkeypatch.setattr(ocr_runner, "get_signed_url", lambda *a, **k: "https://signed")
    monkeypatch.setattr(
        ocr_runner, "get_settings",
        lambda: SimpleNamespace(MISTRAL_API_KEY="k", STORAGE_BUCKET_DOCUMENTS="docs"),
    )
    return text


@pytest.mark.asyncio
async def test_flag_on_writes_one_row_per_page(monkeypatch, ocr_env):
    monkeypatch.setattr(ocr_runner, "segmenter_enabled", lambda uid: uid == USER)
    db = _db()
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert stats.extracted == 1 and stats.pages_stored == 2
    row = db.tables["workspace_items"][0]
    assert row["content_md"] == ocr_env
    pages = sorted(db.tables["attachment_pages"], key=lambda r: r["page_no"])
    assert [(p["page_no"], p["char_start"], p["user_id"]) for p in pages] == [(1, 0, USER), (2, len("صفحة أولى") + 2, USER)]
    assert row["content_md"][pages[1]["char_start"]:] == "صفحة ثانية"


@pytest.mark.asyncio
async def test_flag_off_writes_nothing(monkeypatch, ocr_env):
    monkeypatch.setattr(ocr_runner, "segmenter_enabled", lambda uid: False)
    db = _db()
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert stats.extracted == 1 and stats.pages_stored == 0
    assert db.tables["attachment_pages"] == []
    assert ("attachment_pages", "upsert") not in db.log


@pytest.mark.asyncio
async def test_failed_page_write_never_fails_ocr(monkeypatch, ocr_env):
    monkeypatch.setattr(ocr_runner, "segmenter_enabled", lambda uid: True)
    db = _db()
    db.fail.add("attachment_pages")
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert stats.extracted == 1 and stats.failed == 0 and stats.pages_stored == 0
    row = db.tables["workspace_items"][0]
    assert row["content_md"] == ocr_env and row["metadata"]["ocr_status"] == "done"
