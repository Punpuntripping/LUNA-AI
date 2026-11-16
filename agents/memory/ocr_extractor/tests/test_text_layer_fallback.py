"""The text-layer fallback (owner 2026-11-16): when Mistral fails, the chosen
pages are read from the PDF's own text layer — free, never billed — and the
text says which pages came from where."""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from agents.memory.attachment_segmenter.paragraphs import PageRow, _valid_pages
from agents.memory.attachment_segmenter.tests.fake_supabase import FakeSupabase
from agents.memory.ocr_extractor import big_file
from agents.memory.ocr_extractor import runner as ocr_runner
from agents.memory.ocr_extractor.mistral_ocr import join_pages
from agents.memory.ocr_extractor.models import OcrDocumentResult, OcrPage
from agents.memory.ocr_extractor.partial import assemble_marked
from agents.memory.ocr_extractor.text_layer import TextLayerPage
from shared.quota.ocr_budget import OcrBudget, pick_pages


def _pages(numbers, prefix="نص"):
    return [OcrPage(page_no=n, markdown=f"{prefix} الصفحة {n}", char_start=0) for n in numbers]


def _fits(text, pages):
    rows = [PageRow(page_no=p.page_no, char_start=p.char_start, content_md=p.markdown) for p in pages]
    return _valid_pages(text, rows) == sorted(rows, key=lambda r: r.page_no)


# ── the marked text ──────────────────────────────────────────────────────────

def test_whole_file_from_the_text_layer():
    text, anchored = assemble_marked(_pages(range(1, 6)), 5, budget_partial=False,
                                     text_layer_pages=range(1, 6))
    assert text.startswith("〔[نظام] تعذّر OCR على الصفحات 1–5 فاستُخرج نصها من طبقة النص")
    assert "〔[نظام] من طبقة نص الملف: الصفحات 1–5.〕" in text
    assert "لم تُستخرج" not in text  # nothing skipped
    assert _fits(text, anchored)


def test_mixed_sources_and_lost_pages():
    # pages 1-3 Mistral, 4-5 text layer, 6 lost (an image page), plan covered all 6
    pages = _pages([1, 2, 3]) + _pages([4, 5], "طبقة")
    text, anchored = assemble_marked(pages, 6, budget_partial=False,
                                     text_layer_pages=[4, 5], lost_pages=[6])
    assert "〔[نظام] من طبقة نص الملف: الصفحات 4–5.〕" in text
    assert text.index("نص الصفحة 3") < text.index("من طبقة نص الملف") < text.index("طبقة الصفحة 4")
    assert text.endswith("〔[نظام] تعذّر استخراج الصفحة 6.〕")
    assert "حدّ رصيد" not in text  # a loss is not the budget's doing
    assert _fits(text, anchored)


def test_budget_gaps_and_lost_pages_are_told_apart():
    # plan 1-3 + 8-10 of 10; page 2 lost; 4-7 skipped by the budget
    pages = _pages([1, 3, 8, 9, 10])
    text, _ = assemble_marked(pages, 10, budget_partial=True, lost_pages=[2])
    assert "〔[نظام] تعذّر استخراج الصفحة 2.〕" in text
    assert "〔[نظام] الصفحات 4–7 (4 صفحة) لم تُستخرج.〕" in text
    assert text.startswith("〔[نظام] استُخرجت 5 صفحة فقط من أصل 10")


# ── the cutting path: per-cut fallback ──────────────────────────────────────

def _blank_pdf(n):
    w = PdfWriter()
    for _ in range(n):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _cut_env(monkeypatch, tmp_path, n_pages, fail_on, exc):
    src = tmp_path / "doc.pdf"
    src.write_bytes(_blank_pdf(n_pages))
    monkeypatch.setattr(big_file, "CHUNK_MAX_PAGES", 10)
    # CONCURRENCY 1 makes "the n-th call" deterministic
    monkeypatch.setattr(big_file, "CONCURRENCY", 1)
    calls = {"mistral": 0, "flip": []}

    async def fake_download(url, dest):
        with open(dest, "wb") as fh:
            fh.write(src.read_bytes())

    async def flaky_ocr(url, mime, pages=None, *, expected_pages=None, timeout_s=None):
        calls["mistral"] += 1
        if calls["mistral"] == fail_on:
            raise exc
        return OcrDocumentResult(
            text="", page_count=expected_pages, model="mistral-ocr-x",
            pages=[OcrPage(i + 1, f"m{i}", 0) for i in range(expected_pages)],
        )

    def fake_read(reader, pages, *, reverse_digits=None):
        calls["flip"].append(reverse_digits)
        # page 15 is an image page — no text layer
        return [TextLayerPage(p, "" if p == 15 else f"طبقة نص الصفحة {p} " * 3,
                              0 if p == 15 else 30) for p in pages]

    monkeypatch.setattr(big_file, "_download", fake_download)
    monkeypatch.setattr(big_file, "ocr_document", flaky_ocr)
    monkeypatch.setattr(big_file, "read_pages", fake_read)
    monkeypatch.setattr(big_file, "sample_digits_reversed", lambda reader, pages: True)
    return calls


@pytest.mark.asyncio
async def test_an_outage_sends_the_rest_of_the_file_to_the_text_layer(monkeypatch, tmp_path):
    calls = _cut_env(monkeypatch, tmp_path, 30, fail_on=2, exc=TimeoutError())
    res = await big_file.ocr_big_pdf("https://x", tuple(range(1, 31)), 30)
    # cut 1 by Mistral; cut 2 timed out; cut 3 never tried Mistral
    assert calls["mistral"] == 2
    assert res.text_layer_pages == tuple(p for p in range(11, 31) if p != 15)
    assert res.lost_pages == (15,)
    assert res.page_count == 29 and res.billed_pages == 10
    assert res.model == "mistral-ocr-x"
    assert calls["flip"] == [True, True]  # one per-file digit decision, reused
    assert _fits(res.text, res.pages)


@pytest.mark.asyncio
async def test_a_one_cut_error_falls_back_for_that_cut_only(monkeypatch, tmp_path):
    calls = _cut_env(monkeypatch, tmp_path, 30, fail_on=2, exc=ValueError("bad cut"))
    res = await big_file.ocr_big_pdf("https://x", tuple(range(1, 31)), 30)
    assert calls["mistral"] == 3
    assert res.text_layer_pages == tuple(p for p in range(11, 21) if p != 15)
    assert res.billed_pages == 20


@pytest.mark.asyncio
async def test_past_the_deadline_cuts_skip_mistral(monkeypatch, tmp_path):
    calls = _cut_env(monkeypatch, tmp_path, 20, fail_on=0, exc=None)
    monkeypatch.setattr(big_file, "MISTRAL_DEADLINE_S", 0.0)
    res = await big_file.ocr_big_pdf("https://x", tuple(range(1, 21)), 20)
    assert calls["mistral"] == 0 and res.billed_pages == 0
    assert len(res.text_layer_pages) == 19


def test_outage_errors():
    import httpx

    assert big_file._is_outage(TimeoutError())
    assert big_file._is_outage(httpx.ReadError("dropped"))
    err = RuntimeError("x")
    err.status_code = 503
    assert big_file._is_outage(err)
    err.status_code = 429
    assert big_file._is_outage(err)
    err.status_code = 400
    assert not big_file._is_outage(err)
    assert not big_file._is_outage(ValueError("bad cut"))


@pytest.mark.asyncio
async def test_text_layer_document(monkeypatch, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(_blank_pdf(12))

    async def fake_download(url, dest):
        with open(dest, "wb") as fh:
            fh.write(src.read_bytes())

    monkeypatch.setattr(big_file, "_download", fake_download)
    monkeypatch.setattr(big_file, "read_pages",
                        lambda reader, pages, **_k: [TextLayerPage(p, f"نص {p} " * 10, 20) for p in pages])
    res = await big_file.text_layer_document("https://x", pick_pages(12, 5), 12)
    assert res.text_layer_pages == pick_pages(12, 5)
    assert res.billed_pages == 0 and res.model == big_file.TEXT_LAYER_MODEL


# ── the runner ───────────────────────────────────────────────────────────────

USER = "user-1"


def _row(item, pages, size=10, mime="application/pdf"):
    return {
        "item_id": item, "conversation_id": "c1", "kind": "attachment", "deleted_at": None,
        "title": "ملف.pdf", "storage_path": f"u/{item}.pdf",
        "metadata": {"mime_type": mime, "file_size_bytes": size, "page_count": pages,
                     "filename": "ملف.pdf"},
    }


def _layer_result(pages, total, lost=()):
    md = [f"طبقة {p}" for p in pages]
    text, offs = join_pages(md)
    return OcrDocumentResult(
        text=text, page_count=len(md), model=big_file.TEXT_LAYER_MODEL,
        pages=[OcrPage(p, m, o) for p, m, o in zip(pages, md, offs)],
        total_pages=total, text_layer_pages=tuple(pages), lost_pages=tuple(lost),
    )


@pytest.fixture
def env(monkeypatch):
    calls = {"ledger": [], "layer": []}

    async def mistral_down(*_a, **_k):
        raise TimeoutError()

    async def layer(url, pages, planned_total):
        calls["layer"].append((tuple(pages) if pages is not None else None, planned_total))
        return _layer_result(list(pages), planned_total)

    async def budget(_db, _user):
        return OcrBudget(total=150, used=0, per_file_cap=100)

    monkeypatch.setattr(ocr_runner, "ocr_document", mistral_down)
    monkeypatch.setattr(ocr_runner, "text_layer_document", layer)
    monkeypatch.setattr(ocr_runner, "_read_budget", budget)
    monkeypatch.setattr(ocr_runner, "get_signed_url", lambda *a, **k: "https://signed")
    monkeypatch.setattr(ocr_runner, "segmenter_enabled", lambda uid: True)
    monkeypatch.setattr(ocr_runner, "record_call", lambda **kw: calls["ledger"].append(kw["pages_used"]))
    monkeypatch.setattr(ocr_runner, "get_settings",
                        lambda: SimpleNamespace(MISTRAL_API_KEY="k", STORAGE_BUCKET_DOCUMENTS="docs"))
    return calls


@pytest.mark.asyncio
async def test_whole_file_mistral_failure_reads_the_text_layer_free(env):
    db = FakeSupabase({"workspace_items": [_row("small", 12)]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    row = db.tables["workspace_items"][0]
    meta = row["metadata"]
    assert env["layer"] == [(tuple(range(1, 13)), 12)]
    assert meta["ocr_status"] == "done" and meta["ocr_text_layer_ranges"] == [[1, 12]]
    assert "ocr_partial" not in meta
    assert env["ledger"] == []  # nothing billed
    assert stats.failed == 0 and stats.extracted == 1
    assert row["content_md"].startswith("〔[نظام] تعذّر OCR على الصفحات 1–12")
    notices = ocr_runner.budget_notices(stats)
    assert notices == [
        "تعذّر OCR على «ملف.pdf»، فاستخرجنا نص الصفحات 1–12 من طبقة النص المخزّنة في الملف، "
        "ولم تُحسب من رصيدك. قد تكون الأرقام والتواريخ والجداول فيها غير مرتّبة."
    ]
    rows = [PageRow(p["page_no"], p["char_start"], p["content_md"]) for p in db.tables["attachment_pages"]]
    assert len(_valid_pages(row["content_md"], rows)) == 12


@pytest.mark.asyncio
async def test_mixed_cut_bills_only_mistral_pages(env, monkeypatch):
    async def mixed(url, pages, planned_total):
        md = {p: f"m{p}" for p in pages}
        layer = tuple(p for p in pages if p > 60)
        text, offs = join_pages(list(md.values()))
        return OcrDocumentResult(
            text=text, page_count=len(md), model="mistral-ocr-x",
            pages=[OcrPage(p, m, o) for (p, m), o in zip(md.items(), offs)],
            total_pages=planned_total, text_layer_pages=layer,
        )

    monkeypatch.setattr(ocr_runner, "ocr_big_pdf", mixed)
    db = FakeSupabase({"workspace_items": [_row("big", 606)]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert env["ledger"] == [60]  # pages 1-60 by Mistral; 314-333 + 587-606 free
    meta = db.tables["workspace_items"][0]["metadata"]
    assert meta["ocr_partial"] is True and meta["ocr_text_layer_ranges"] == [[314, 333], [587, 606]]
    assert len(stats.partial) == 1 and len(stats.fallback) == 1


@pytest.mark.asyncio
async def test_scanned_file_with_mistral_down_fails(env, monkeypatch):
    async def nothing(url, pages, planned_total):
        return OcrDocumentResult(text="", page_count=0, model=big_file.TEXT_LAYER_MODEL,
                                 pages=[], total_pages=planned_total, lost_pages=tuple(pages))

    monkeypatch.setattr(ocr_runner, "text_layer_document", nothing)
    db = FakeSupabase({"workspace_items": [_row("scan", 4)]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert stats.failed == 1 and db.tables["workspace_items"][0]["metadata"]["ocr_status"] == "failed"
    assert env["ledger"] == []


@pytest.mark.asyncio
async def test_images_get_no_text_layer_fallback(env):
    db = FakeSupabase({"workspace_items": [_row("img", 1, mime="image/png")]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert stats.failed == 1 and env["layer"] == []


def test_lost_pages_are_not_a_budget_partial():
    plan = SimpleNamespace(pages=tuple(range(1, 11)), total_pages=10)
    res = SimpleNamespace(total_pages=10, page_count=8, lost_pages=(3, 4))
    assert ocr_runner._partial_total(plan, res) is False
