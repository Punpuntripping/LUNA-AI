"""Pages past the OCR page budget come from the PDF's own text layer (owner,
2026-11-23): free, never billed, and the text warns every later agent not to
trust their dates and numbers. A PDF with no budget left is read from its text
layer alone; a scan with no text layer still waits for pages."""
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
from agents.memory.ocr_extractor.models import (
    OcrDocumentResult,
    OcrExtractionStats,
    OcrPage,
    OverflowRead,
)
from agents.memory.ocr_extractor.partial import TEXT_LAYER_CAUTION, assemble_marked
from agents.memory.ocr_extractor.text_layer import TextLayerPage
from shared.quota.ocr_budget import OcrBudget, page_ranges, pick_pages


def _pages(numbers, prefix="نص"):
    return [OcrPage(page_no=n, markdown=f"{prefix} الصفحة {n}", char_start=0) for n in numbers]


def _fits(text, pages):
    rows = [PageRow(page_no=p.page_no, char_start=p.char_start, content_md=p.markdown) for p in pages]
    return _valid_pages(text, rows) == sorted(rows, key=lambda r: r.page_no)


def _rest(total, chosen, blank=()):
    return tuple(p for p in range(1, total + 1) if p not in set(chosen) and p not in set(blank))


# ── which pages ──────────────────────────────────────────────────────────────

def test_overflow_pages_fill_the_gaps_in_page_order():
    assert big_file.overflow_pages(10, (1, 2, 9, 10)) == (3, 4, 5, 6, 7, 8)
    assert big_file.overflow_pages(5, ()) == (1, 2, 3, 4, 5)
    assert big_file.overflow_pages(5, (1, 2, 3, 4, 5)) == ()


def test_overflow_pages_stop_at_the_ceiling():
    chosen = pick_pages(1183, 100)
    extra = big_file.overflow_pages(1183, chosen)
    assert len(chosen) + len(extra) == big_file.TEXT_LAYER_MAX_PAGES == 1000
    assert extra[0] == 61 and not set(extra) & set(chosen)
    assert big_file.overflow_pages(2000, tuple(range(1, 1001))) == ()


# ── the cutting path ─────────────────────────────────────────────────────────

def _blank_pdf(n):
    w = PdfWriter()
    for _ in range(n):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _env(monkeypatch, tmp_path, n_pages, blank=()):
    src = tmp_path / "doc.pdf"
    src.write_bytes(_blank_pdf(n_pages))
    monkeypatch.setattr(big_file, "CHUNK_MAX_PAGES", 10)
    calls = {"mistral": [], "layer": []}

    async def fake_download(url, dest):
        with open(dest, "wb") as fh:
            fh.write(src.read_bytes())

    async def fake_ocr(url, mime, pages=None, *, expected_pages=None, timeout_s=None):
        calls["mistral"].append(expected_pages)
        return OcrDocumentResult(
            text="", page_count=expected_pages, model="mistral-ocr-x",
            pages=[OcrPage(i + 1, f"m{i}", 0) for i in range(expected_pages)],
        )

    def fake_read(reader, pages, *, reverse_digits=None):
        calls["layer"].append(tuple(pages))
        return [
            TextLayerPage(p, "" if p in blank else f"طبقة نص الصفحة {p} " * 3, 0 if p in blank else 30)
            for p in pages
        ]

    monkeypatch.setattr(big_file, "_download", fake_download)
    monkeypatch.setattr(big_file, "ocr_document", fake_ocr)
    monkeypatch.setattr(big_file, "read_pages", fake_read)
    monkeypatch.setattr(big_file, "sample_digits_reversed", lambda reader, pages: False)
    return calls


@pytest.mark.asyncio
async def test_pages_past_the_budget_come_from_the_text_layer(monkeypatch, tmp_path):
    calls = _env(monkeypatch, tmp_path, 30, blank={25})
    chosen = pick_pages(30, 10)  # 1–6, 17–18, 29–30
    res = await big_file.ocr_big_pdf("https://x", chosen, 30, overflow=True)
    assert sum(calls["mistral"]) == 10  # Mistral read only the budget's pages
    assert res.overflow_pages == _rest(30, chosen, blank={25})
    assert res.text_layer_pages == res.overflow_pages
    assert res.overflow_blank == (25,) and res.unread_pages == (25,)
    assert res.lost_pages == ()  # a blank page past the budget is not "lost"
    assert res.page_count == 29 and res.billed_pages == 10
    assert res.model == "mistral-ocr-x"
    assert _fits(res.text, res.pages)


@pytest.mark.asyncio
async def test_no_budget_reads_the_text_layer_alone(monkeypatch, tmp_path):
    calls = _env(monkeypatch, tmp_path, 12)
    res = await big_file.ocr_big_pdf("https://x", (), 12, overflow=True)
    assert calls["mistral"] == []
    assert res.overflow_pages == tuple(range(1, 13)) and res.unread_pages == ()
    assert res.billed_pages == 0 and res.model == big_file.TEXT_LAYER_MODEL


@pytest.mark.asyncio
async def test_without_overflow_the_rest_stays_unread(monkeypatch, tmp_path):
    calls = _env(monkeypatch, tmp_path, 30)
    chosen = pick_pages(30, 10)
    res = await big_file.ocr_big_pdf("https://x", chosen, 30)
    assert calls["layer"] == [] and res.overflow_pages == ()
    assert res.unread_pages == _rest(30, chosen)


@pytest.mark.asyncio
async def test_the_ceiling_caps_a_huge_file(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, 30)
    monkeypatch.setattr(big_file, "TEXT_LAYER_MAX_PAGES", 15)
    res = await big_file.ocr_big_pdf("https://x", pick_pages(30, 10), 30, overflow=True)
    assert len(res.overflow_pages) == 5 and len(res.unread_pages) == 15
    assert res.overflow_blank == ()


@pytest.mark.asyncio
async def test_a_mistral_outage_and_the_overflow_are_told_apart(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, 30)

    async def down(*_a, **_k):
        raise TimeoutError()

    monkeypatch.setattr(big_file, "ocr_document", down)
    chosen = pick_pages(30, 10)
    res = await big_file.ocr_big_pdf("https://x", chosen, 30, overflow=True)
    assert res.text_layer_pages == tuple(range(1, 31))
    assert res.overflow_pages == _rest(30, chosen)  # the budget's pages are a fallback
    assert res.billed_pages == 0


@pytest.mark.asyncio
async def test_url_fallback_reads_pages_the_client_did_not_count(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, 12)
    # The client counted 10 pages; the file has 12. Mistral failed on the URL path.
    res = await big_file.text_layer_document("https://x", tuple(range(1, 11)), 10, overflow=True)
    chosen = pick_pages(12, 10)
    assert res.overflow_pages == _rest(12, chosen) and res.unread_pages == ()
    assert res.billed_pages == 0


# ── the marked text ──────────────────────────────────────────────────────────

def test_overflow_and_a_mistral_failure_get_their_own_notice():
    pages = _pages([1, 2, 3]) + _pages([4, 5, 6, 7], "طبقة")
    text, anchored = assemble_marked(
        pages, 7, budget_partial=False, text_layer_pages=[4, 5, 6, 7], overflow_pages=[6, 7],
    )
    assert "〔[نظام] تعذّر OCR على الصفحات 4–5 فاستُخرج نصها من طبقة النص" in text
    assert "〔[نظام] الصفحات 6–7 تجاوزت حدّ رصيد الاستخراج (OCR) لدى المستخدم" in text
    assert text.count(TEXT_LAYER_CAUTION) == 2
    assert "لم تُستخرج" not in text
    assert _fits(text, anchored)


def test_the_caution_names_dates_numbers_and_the_order_of_events():
    for word in ("التواريخ", "الأرقام", "المبالغ", "أرقام المواد", "ترتيب الأحداث"):
        assert word in TEXT_LAYER_CAUTION


def test_blank_pages_past_the_budget_are_a_budget_gap():
    pages = _pages([1, 2]) + _pages([3, 5], "طبقة")
    text, anchored = assemble_marked(
        pages, 5, budget_partial=True, text_layer_pages=[3, 5], overflow_pages=[3, 5],
    )
    assert text.startswith(
        "〔[نظام] استُخرجت 4 صفحة فقط من أصل 5 صفحة في هذا الملف. الصفحات المستخرجة: 1–3، 5."
    )
    assert "〔[نظام] الصفحة 4 لم تُستخرج.〕" in text
    assert _fits(text, anchored)


# ── the runner ───────────────────────────────────────────────────────────────

USER = "user-1"


def _row(item, pages, mime="application/pdf"):
    return {
        "item_id": item, "conversation_id": "c1", "kind": "attachment", "deleted_at": None,
        "title": "ملف.pdf", "storage_path": f"u/{item}.pdf",
        "metadata": {"mime_type": mime, "file_size_bytes": 10, "page_count": pages,
                     "filename": "ملف.pdf"},
    }


def _overflow_result(chosen, total, blank=()):
    """What ``ocr_big_pdf`` returns with overflow on: the chosen pages by
    "Mistral", every other page from the text layer, ``blank`` pages empty."""
    chosen = tuple(chosen)
    rest = _rest(total, chosen, blank)
    numbers = sorted({*chosen, *rest})
    md = [f"m{p}" if p in chosen else f"طبقة {p}" for p in numbers]
    text, offs = join_pages(md)
    return OcrDocumentResult(
        text=text, page_count=len(md),
        model="mistral-ocr-x" if chosen else big_file.TEXT_LAYER_MODEL,
        pages=[OcrPage(p, m, o) for p, m, o in zip(numbers, md, offs)],
        total_pages=total, text_layer_pages=rest, overflow_pages=rest,
        overflow_blank=tuple(blank), unread_pages=tuple(blank),
    )


@pytest.fixture
def env(monkeypatch):
    calls = {"url": [], "big": [], "ledger": [], "blank": ()}

    async def fake_ocr(url, mime, pages=None, **_k):
        calls["url"].append(tuple(pages) if pages is not None else None)
        md = [f"p{n}" for n in pages]
        text, offs = join_pages(md)
        return OcrDocumentResult(text=text, page_count=len(md), model="m",
                                 pages=[OcrPage(n, m, o) for n, m, o in zip(pages, md, offs)])

    async def fake_big(url, pages, planned_total, *, overflow=False):
        calls["big"].append((tuple(pages), planned_total, overflow))
        return _overflow_result(pages, planned_total, calls["blank"])

    monkeypatch.setattr(ocr_runner, "ocr_document", fake_ocr)
    monkeypatch.setattr(ocr_runner, "ocr_big_pdf", fake_big)
    monkeypatch.setattr(ocr_runner, "get_signed_url", lambda *a, **k: "https://signed")
    monkeypatch.setattr(ocr_runner, "segmenter_enabled", lambda uid: True)
    monkeypatch.setattr(ocr_runner, "record_call", lambda **kw: calls["ledger"].append(kw["pages_used"]))
    monkeypatch.setattr(
        ocr_runner, "get_settings",
        lambda: SimpleNamespace(MISTRAL_API_KEY="k", STORAGE_BUCKET_DOCUMENTS="docs"),
    )
    return calls


def _budget(monkeypatch, budget):
    async def read(_db, _user):
        return budget

    monkeypatch.setattr(ocr_runner, "_read_budget", read)


def _page_rows_fit(db, row):
    rows = [PageRow(p["page_no"], p["char_start"], p["content_md"]) for p in db.tables["attachment_pages"]]
    return len(_valid_pages(row["content_md"], rows))


@pytest.mark.asyncio
async def test_pro_606_pages_ocr_100_and_the_rest_from_the_text_layer(monkeypatch, env):
    _budget(monkeypatch, OcrBudget(total=150, used=0, per_file_cap=100))
    db = FakeSupabase({"workspace_items": [_row("big", 606)]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)

    chosen = pick_pages(606, 100)  # 1–60, 314–333, 587–606
    assert env["big"] == [(chosen, 606, True)]
    assert env["ledger"] == [100]  # only the OCR pages are billed
    row = db.tables["workspace_items"][0]
    meta = row["metadata"]
    assert meta["ocr_status"] == "done" and meta["ocr_pages"] == 606
    assert "ocr_partial" not in meta  # every page was extracted
    assert meta["ocr_overflow_ranges"] == [[61, 313], [334, 586]]
    assert meta["ocr_text_layer_ranges"] == [[61, 313], [334, 586]]
    assert row["content_md"].startswith(
        "〔[نظام] الصفحات 61–313، 334–586 تجاوزت حدّ رصيد الاستخراج (OCR) لدى المستخدم"
    )
    assert TEXT_LAYER_CAUTION in row["content_md"]
    assert _page_rows_fit(db, row) == 606
    assert stats.partial == [] and stats.fallback == [] and stats.no_budget == []
    assert stats.overflow == [OverflowRead(
        "big", "ملف.pdf", chosen, _rest(606, chosen), (), (), 606,
    )]
    assert ocr_runner.budget_notices(stats) == [
        "استخرجنا 100 صفحة من «ملف.pdf» حسب رصيدك (1–60، 314–333، 587–606)، واستخرجنا "
        "الصفحات 61–313، 334–586 من طبقة النص المخزّنة في الملف دون أن تُحسب من رصيدك. "
        "قد تكون الأرقام والتواريخ فيها غير دقيقة، فراجعها في الملف الأصلي."
    ]


@pytest.mark.asyncio
async def test_no_pages_left_a_pdf_is_read_from_its_text_layer_free(monkeypatch, env):
    _budget(monkeypatch, OcrBudget(total=150, used=150, per_file_cap=100))
    db = FakeSupabase({"workspace_items": [_row("doc", 12)]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)

    assert env["big"] == [((), 12, True)] and env["url"] == []
    assert env["ledger"] == []  # nothing billed
    row = db.tables["workspace_items"][0]
    assert row["metadata"]["ocr_status"] == "done"
    assert row["metadata"]["ocr_overflow_ranges"] == [[1, 12]]
    assert row["content_md"].startswith("〔[نظام] الصفحات 1–12 تجاوزت حدّ رصيد الاستخراج")
    assert _page_rows_fit(db, row) == 12
    assert stats.no_budget == [] and stats.extracted == 1
    assert ocr_runner.budget_notices(stats) == [
        "نفد رصيد استخراج الملفات، فاستخرجنا نص «ملف.pdf» من طبقة النص المخزّنة في الملف "
        "دون أن يُحسب من رصيدك. قد تكون الأرقام والتواريخ فيه غير دقيقة، فراجعها في الملف الأصلي."
    ]


@pytest.mark.asyncio
async def test_no_pages_left_a_scan_waits_for_pages(monkeypatch, env):
    _budget(monkeypatch, OcrBudget(total=150, used=150, per_file_cap=100))
    env["blank"] = tuple(range(1, 5))
    db = FakeSupabase({"workspace_items": [_row("scan", 4)]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    meta = db.tables["workspace_items"][0]["metadata"]
    assert "ocr_status" not in meta  # read once pages return
    assert meta["ocr_no_text_layer"] is True
    assert stats.no_budget == ["ملف.pdf"] and stats.empty == 0
    assert ocr_runner.budget_notices(stats) == ["لم نستخرج «ملف.pdf» لأن رصيد استخراج الملفات نفد."]

    # the next turn does not download it again for the text layer…
    again = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert len(env["big"]) == 1 and again.no_budget == ["ملف.pdf"]
    # …and OCR reads it once pages are back
    _budget(monkeypatch, OcrBudget(total=150, used=0, per_file_cap=100))
    env["blank"] = ()
    await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert env["url"] == [tuple(range(1, 5))]  # a small file goes to Mistral whole
    assert db.tables["workspace_items"][0]["metadata"]["ocr_status"] == "done"


@pytest.mark.asyncio
async def test_no_pages_left_an_unreadable_pdf_waits_and_never_reaches_mistral(monkeypatch, env):
    _budget(monkeypatch, OcrBudget(total=150, used=150, per_file_cap=100))

    async def broken(*_a, **_k):
        raise ValueError("encrypted")

    monkeypatch.setattr(ocr_runner, "ocr_big_pdf", broken)
    db = FakeSupabase({"workspace_items": [_row("enc", 12)]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert env["url"] == []  # no whole-file Mistral call without budget
    assert "ocr_status" not in db.tables["workspace_items"][0]["metadata"]
    assert db.tables["workspace_items"][0]["metadata"]["ocr_no_text_layer"] is True
    assert stats.no_budget == ["ملف.pdf"] and stats.failed == 0


@pytest.mark.asyncio
async def test_no_pages_left_an_image_still_waits(monkeypatch, env):
    _budget(monkeypatch, OcrBudget(total=150, used=150, per_file_cap=100))
    db = FakeSupabase({"workspace_items": [_row("img", 1, mime="image/png")]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert env["big"] == env["url"] == []
    assert stats.no_budget == ["ملف.pdf"]


@pytest.mark.asyncio
async def test_overflow_off_passes_no_overflow(monkeypatch, env):
    _budget(monkeypatch, OcrBudget(total=150, used=0, per_file_cap=100))
    monkeypatch.setattr(
        ocr_runner, "get_settings",
        lambda: SimpleNamespace(MISTRAL_API_KEY="k", STORAGE_BUCKET_DOCUMENTS="docs",
                                OCR_TEXT_LAYER_OVERFLOW_ENABLED=False),
    )
    db = FakeSupabase({"workspace_items": [_row("big", 606)]})
    await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert env["big"] == [(pick_pages(606, 100), 606, False)]


@pytest.mark.asyncio
async def test_blank_pages_past_the_budget_are_reported(monkeypatch, env):
    _budget(monkeypatch, OcrBudget(total=150, used=0, per_file_cap=100))
    env["blank"] = tuple(range(400, 410))
    db = FakeSupabase({"workspace_items": [_row("big", 606)]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    meta = db.tables["workspace_items"][0]["metadata"]
    assert meta["ocr_partial"] is True and meta["ocr_page_total"] == 606
    assert stats.overflow[0].blank_pages == tuple(range(400, 410))
    assert stats.overflow[0].beyond_pages == ()
    notices = ocr_runner.budget_notices(stats)
    assert len(notices) == 2
    assert notices[1] == (
        "لم تُستخرج الصفحات 400–409 من «ملف.pdf»: تجاوزت رصيدك وليس فيها نص مخزّن، "
        "والغالب أنها صور."
    )


def test_notice_when_the_budgets_own_pages_were_lost():
    # pages were left, but Mistral and the text layer both failed on them: never «نفد»
    ov = OverflowRead("x", "ملف.pdf", ocr_pages=(), text_layer_pages=(4, 5), total_pages=5)
    assert ocr_runner.budget_notices(OcrExtractionStats(overflow=[ov])) == [
        "استخرجنا الصفحات 4–5 من «ملف.pdf» من طبقة النص المخزّنة في الملف دون أن تُحسب من "
        "رصيدك. قد تكون الأرقام والتواريخ فيها غير دقيقة، فراجعها في الملف الأصلي."
    ]


def test_notice_for_pages_past_the_ceiling():
    ov = OverflowRead(
        "x", "ملف.pdf", ocr_pages=(), text_layer_pages=tuple(range(1, 1001)),
        beyond_pages=tuple(range(1001, 1184)), total_pages=1183, had_budget=False,
    )
    assert ocr_runner.budget_notices(OcrExtractionStats(overflow=[ov])) == [
        "نفد رصيد استخراج الملفات، فاستخرجنا الصفحات 1–1000 من «ملف.pdf» من طبقة النص المخزّنة "
        "في الملف دون أن تُحسب من رصيدك. قد تكون الأرقام والتواريخ فيها غير دقيقة، فراجعها في "
        "الملف الأصلي.",
        "لم تُستخرج الصفحات 1001–1183 من «ملف.pdf»: نستخرج من الملف الواحد 1000 صفحة على الأكثر.",
    ]


@pytest.mark.asyncio
async def test_end_to_end_through_the_real_cutting_path(monkeypatch, tmp_path, env):
    """Runner + the real ``ocr_big_pdf`` (download, Mistral and the text layer faked)."""
    _env(monkeypatch, tmp_path, 30)
    monkeypatch.setattr(ocr_runner, "ocr_big_pdf", big_file.ocr_big_pdf)
    _budget(monkeypatch, OcrBudget(total=150, used=145, per_file_cap=100))  # 5 pages left
    db = FakeSupabase({"workspace_items": [_row("doc", 30)]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)

    row = db.tables["workspace_items"][0]
    chosen = pick_pages(30, 5)
    assert env["ledger"] == [5]
    assert row["metadata"]["ocr_pages"] == 30 and "ocr_partial" not in row["metadata"]
    assert row["metadata"]["ocr_overflow_ranges"] == [list(r) for r in page_ranges(_rest(30, chosen))]
    assert _page_rows_fit(db, row) == 30
    assert stats.overflow and stats.overflow[0].ocr_pages == chosen
