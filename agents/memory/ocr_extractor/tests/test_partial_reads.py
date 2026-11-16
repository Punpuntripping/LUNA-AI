"""Partial reads under the OCR page budget (plan ocr_page_budget.md):
the marked text, the cutting path, and the runner's handling of the budget."""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from agents.memory.attachment_segmenter.paragraphs import PageRow, _valid_pages
from agents.memory.attachment_segmenter.tests.fake_supabase import FakeSupabase
from agents.memory.ocr_extractor import big_file
from agents.memory.ocr_extractor import runner as ocr_runner
from agents.memory.ocr_extractor.mistral_ocr import join_pages
from agents.memory.ocr_extractor.models import OcrDocumentResult, OcrExtractionStats, OcrPage, PartialRead
from agents.memory.ocr_extractor.partial import assemble_partial, partial_header
from shared.quota.ocr_budget import OcrBudget, pick_pages


def _pages(numbers):
    return [OcrPage(page_no=n, markdown=f"نص الصفحة {n}", char_start=0) for n in numbers]


def _fits(text, pages):
    rows = [PageRow(page_no=p.page_no, char_start=p.char_start, content_md=p.markdown) for p in pages]
    return _valid_pages(text, rows) == sorted(rows, key=lambda r: r.page_no)


# ── the marked text ──────────────────────────────────────────────────────────

def test_partial_text_marks_every_gap():
    numbers = pick_pages(606, 100)
    text, anchored = assemble_partial(_pages(numbers), 606)
    assert text.startswith("〔[نظام] استُخرجت 100 صفحة فقط من أصل 606 صفحة")
    assert "1–60، 314–333، 587–606" in text.split("\n\n")[0]
    assert "〔[نظام] الصفحات 61–313 (253 صفحة) لم تُستخرج.〕" in text
    assert "〔[نظام] الصفحات 334–586 (253 صفحة) لم تُستخرج.〕" in text
    assert text.endswith("نص الصفحة 606")  # last block reaches the end: no tail marker
    assert _fits(text, anchored)


def test_partial_text_tail_marker_and_single_page_gap():
    text, anchored = assemble_partial(_pages([1, 3]), 5)
    assert "〔[نظام] الصفحة 2 لم تُستخرج.〕" in text
    assert text.endswith("〔[نظام] الصفحات 4–5 (2 صفحة) لم تُستخرج.〕")
    assert _fits(text, anchored)


def test_partial_text_unknown_length():
    text, anchored = assemble_partial(_pages(range(1, 11)), None)
    assert text.startswith("〔[نظام] استُخرجت أول 10 صفحة فقط من هذا الملف")
    assert text.endswith("〔[نظام] ما بعد الصفحة 10 لم يُستخرج.〕")
    assert _fits(text, anchored)


def test_header_never_claims_more_than_read():
    assert "استُخرجت 2 صفحة فقط من أصل 10" in partial_header([1, 2], 10)


# ── cutting ──────────────────────────────────────────────────────────────────

def _pdf(n_pages: int, piece_info_bytes: int = 0) -> bytes:
    """An n-page PDF; with piece_info_bytes, every page carries one shared
    /PieceInfo stream of that size (like an InDesign export)."""
    w = PdfWriter()
    blob = None
    if piece_info_bytes:
        blob = StreamObject()
        blob.set_data(bytes(range(256)) * (piece_info_bytes // 256))
        blob = w._add_object(blob)
    for _ in range(n_pages):
        page = w.add_blank_page(width=200, height=200)
        if blob is not None:
            page[NameObject("/PieceInfo")] = DictionaryObject({NameObject("/X"): blob})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_cut_drops_piece_info():
    reader = PdfReader(io.BytesIO(_pdf(20, piece_info_bytes=2_000_000)))
    [(pages, data)] = big_file.cut_group(reader, (1, 2, 3))
    assert pages == (1, 2, 3)
    assert len(data) < 50_000
    assert len(PdfReader(io.BytesIO(data)).pages) == 3


def test_cut_drops_links_that_drag_their_target_pages():
    from pypdf.annotations import Link, Text

    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=200, height=200)
    w.add_annotation(page_number=0, annotation=Link(rect=(10, 10, 50, 50), target_page_index=2))
    w.add_annotation(page_number=0, annotation=Text(rect=(60, 60, 90, 90), text="ختم"))
    buf = io.BytesIO()
    w.write(buf)
    [(_, data)] = big_file.cut_group(PdfReader(io.BytesIO(buf.getvalue())), (1,))
    cut = PdfReader(io.BytesIO(data))
    assert len(cut.pages) == 1
    subtypes = [a.get_object()["/Subtype"] for a in cut.pages[0].get("/Annots", [])]
    assert subtypes == ["/Text"]  # the note stays, the link goes


def test_cut_halves_a_heavy_group(monkeypatch):
    monkeypatch.setattr(big_file, "CHUNK_MAX_BYTES", 1)  # everything is "too heavy"
    reader = PdfReader(io.BytesIO(_pdf(8)))
    cuts = big_file.cut_group(reader, tuple(range(1, 9)))
    assert [c[0] for c in cuts] == [(i,) for i in range(1, 9)]


def test_choose_pages_repicks_for_the_real_length():
    assert big_file.choose_pages(606, pick_pages(606, 10), 606) == pick_pages(606, 10)  # count right
    assert big_file.choose_pages(50, pick_pages(606, 10), 606) == pick_pages(50, 10)  # count wrong
    assert big_file.choose_pages(40, tuple(range(1, 11)), None) == pick_pages(40, 10)  # count unknown
    assert big_file.choose_pages(7, None, None) == tuple(range(1, 8))


@pytest.mark.asyncio
async def test_ocr_big_pdf_maps_pages_back(monkeypatch, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(_pdf(120, piece_info_bytes=500_000))
    monkeypatch.setattr(big_file, "CHUNK_MAX_PAGES", 7)

    async def fake_download(url, dest):
        with open(dest, "wb") as fh:
            fh.write(src.read_bytes())

    calls = []

    async def fake_ocr(url, mime, pages=None, *, expected_pages=None, timeout_s=None):
        assert url.startswith("data:application/pdf;base64,") and pages is None
        calls.append(expected_pages)
        return OcrDocumentResult(
            text="", page_count=expected_pages, model="m",
            pages=[OcrPage(page_no=i + 1, markdown=f"local {i}", char_start=0) for i in range(expected_pages)],
        )

    monkeypatch.setattr(big_file, "_download", fake_download)
    monkeypatch.setattr(big_file, "ocr_document", fake_ocr)
    # planned against a wrong client count (606): re-picked for the real 120
    res = await big_file.ocr_big_pdf("https://x", pick_pages(606, 30), 606)
    assert res.total_pages == 120
    assert [p.page_no for p in res.pages] == list(pick_pages(120, 30))
    assert sum(calls) == 30 and max(calls) <= 7
    assert res.page_count == 30
    assert _fits(res.text, res.pages)


@pytest.mark.asyncio
async def test_at_most_two_files_cut_at_once(monkeypatch):
    import asyncio

    live, peak = 0, 0

    async def slow(*_a):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.02)
        live -= 1
        return "done"

    monkeypatch.setattr(big_file, "_ocr_big_pdf", slow)
    results = await asyncio.gather(*(big_file.ocr_big_pdf("u", None, None) for _ in range(5)))
    assert results == ["done"] * 5 and peak == big_file.MAX_FILES_AT_ONCE == 2


# ── the runner ───────────────────────────────────────────────────────────────

USER = "user-1"


def _row(item, pages, size=10, name="ملف.pdf"):
    return {
        "item_id": item, "conversation_id": "c1", "kind": "attachment", "deleted_at": None,
        "title": name, "storage_path": f"u/{item}.pdf",
        "metadata": {"mime_type": "application/pdf", "file_size_bytes": size,
                     "page_count": pages, "filename": name},
    }


@pytest.fixture
def env(monkeypatch):
    calls = {"url": [], "big": [], "ledger": []}

    async def fake_ocr(url, mime, pages=None, **_k):
        calls["url"].append(tuple(pages) if pages is not None else None)
        md = [f"p{n}" for n in pages]
        text, offs = join_pages(md)
        return OcrDocumentResult(text=text, page_count=len(md), model="m",
                                 pages=[OcrPage(n, m, o) for n, m, o in zip(pages, md, offs)])

    async def fake_big(url, pages, planned_total):
        calls["big"].append((tuple(pages) if pages is not None else None, planned_total))
        md = [f"p{n}" for n in pages]
        text, offs = join_pages(md)
        return OcrDocumentResult(text=text, page_count=len(md), model="m",
                                 pages=[OcrPage(n, m, o) for n, m, o in zip(pages, md, offs)],
                                 total_pages=planned_total)

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


def _budget(monkeypatch, budget=None, fail=False):
    async def read(_db, _user):
        if fail:
            raise RuntimeError("rpc down")
        return budget

    monkeypatch.setattr(ocr_runner, "_read_budget", read)


@pytest.mark.asyncio
async def test_pro_606_pages_reads_100_and_marks_the_rest(monkeypatch, env):
    _budget(monkeypatch, OcrBudget(total=150, used=0, per_file_cap=100))
    db = FakeSupabase({"workspace_items": [_row("big", 606)]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)

    assert env["big"] == [(pick_pages(606, 100), 606)] and env["url"] == []
    assert env["ledger"] == [100]
    row = db.tables["workspace_items"][0]
    meta = row["metadata"]
    assert meta["ocr_status"] == "done" and meta["ocr_pages"] == 100
    assert meta["ocr_partial"] is True and meta["ocr_page_total"] == 606
    assert meta["ocr_page_ranges"] == [[1, 60], [314, 333], [587, 606]]
    assert row["content_md"].startswith("〔[نظام] استُخرجت 100 صفحة فقط من أصل 606")
    pages = db.tables["attachment_pages"]
    rows = [PageRow(p["page_no"], p["char_start"], p["content_md"]) for p in pages]
    assert len(_valid_pages(row["content_md"], rows)) == 100
    assert stats.partial == [PartialRead("big", "ملف.pdf", pick_pages(606, 100), 606)]
    assert ocr_runner.budget_notices(stats) == [
        "استخرجنا 100 صفحة من أصل 606 من «ملف.pdf» حسب رصيدك: 1–60، 314–333، 587–606. بقية الصفحات لم تُستخرج."
    ]


@pytest.mark.asyncio
async def test_a_file_that_fits_goes_whole_by_url(monkeypatch, env):
    _budget(monkeypatch, OcrBudget(total=150, used=0, per_file_cap=100))
    db = FakeSupabase({"workspace_items": [_row("small", 12)]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert env["url"] == [tuple(range(1, 13))] and env["big"] == []
    meta = db.tables["workspace_items"][0]["metadata"]
    assert "ocr_partial" not in meta and meta["ocr_pages"] == 12
    assert stats.partial == [] and ocr_runner.budget_notices(stats) == []


@pytest.mark.asyncio
async def test_smallest_first_across_files(monkeypatch, env):
    _budget(monkeypatch, OcrBudget(total=150, used=0, per_file_cap=100))
    db = FakeSupabase({"workspace_items": [_row("a", 606), _row("b", 700), _row("c", 20)]})
    await ocr_runner.run_ocr_extraction(db, "c1", USER)
    by_id = {r["item_id"]: r["metadata"]["ocr_pages"] for r in db.tables["workspace_items"]}
    assert by_id == {"c": 20, "a": 100, "b": 30}
    assert env["ledger"] and sum(env["ledger"]) == 150


@pytest.mark.asyncio
async def test_no_pages_left_leaves_the_file_for_later(monkeypatch, env):
    _budget(monkeypatch, OcrBudget(total=150, used=150, per_file_cap=100))
    db = FakeSupabase({"workspace_items": [_row("big", 606)]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert "ocr_status" not in db.tables["workspace_items"][0]["metadata"]
    assert stats.no_budget == ["ملف.pdf"] and env["big"] == env["url"] == []
    assert ocr_runner.budget_notices(stats) == ["لم نستخرج «ملف.pdf» لأن رصيد استخراج الملفات نفد."]


@pytest.mark.asyncio
async def test_quota_read_failure_reads_nothing(monkeypatch, env):
    _budget(monkeypatch, fail=True)
    db = FakeSupabase({"workspace_items": [_row("big", 606)]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert stats.budget_unavailable and stats.failed == 0
    assert "ocr_status" not in db.tables["workspace_items"][0]["metadata"]


@pytest.mark.asyncio
async def test_unknown_page_count_is_cut_to_learn_it(monkeypatch, env):
    _budget(monkeypatch, OcrBudget(total=150, used=0, per_file_cap=100))
    db = FakeSupabase({"workspace_items": [_row("u", None)]})
    await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert env["big"] == [(tuple(range(1, 101)), None)]


@pytest.mark.asyncio
async def test_cutting_failure_falls_back_to_the_whole_file(monkeypatch, env):
    _budget(monkeypatch, OcrBudget(total=150, used=0, per_file_cap=100))

    async def broken(*_a, **_k):
        raise ValueError("encrypted")

    monkeypatch.setattr(ocr_runner, "ocr_big_pdf", broken)
    db = FakeSupabase({"workspace_items": [_row("big", 606)]})
    stats = await ocr_runner.run_ocr_extraction(db, "c1", USER)
    assert env["url"] == [pick_pages(606, 100)]
    assert stats.partial and stats.failed == 0


def test_has_unread_attachments():
    done = _row("done", 5)
    done["metadata"]["ocr_status"] = "done"
    uploading = _row("up", 5)
    uploading["metadata"]["upload_status"] = "uploading"
    word = _row("w", 5)
    word["metadata"]["mime_type"] = "application/msword"
    assert not ocr_runner.has_unread_attachments(
        FakeSupabase({"workspace_items": [done, uploading, word]}), "c1",
    )
    assert ocr_runner.has_unread_attachments(
        FakeSupabase({"workspace_items": [done, _row("new", 606)]}), "c1",
    )
    assert not ocr_runner.has_unread_attachments(
        FakeSupabase({"workspace_items": [_row("x", 1)]}, fail={"workspace_items"}), "c1",
    )


def test_orchestrator_notices():
    import agents.orchestrator as orch

    stats = OcrExtractionStats(partial=[PartialRead("x", "ملف.pdf", pick_pages(606, 100), 606)])
    assert orch._ocr_notices(stats, failed=False)[0].startswith("استخرجنا 100 صفحة من أصل 606")
    assert orch._ocr_notices(None, failed=True) == [ocr_runner.OCR_FAILED_NOTICE]
    still = orch._ocr_notices(None, failed=True, timed_out=True)
    assert still == [orch._OCR_STILL_RUNNING_NOTICE]
    assert orch._ocr_notices(OcrExtractionStats(), failed=False) == []


def test_notice_for_unknown_length():
    stats = OcrExtractionStats(partial=[PartialRead("x", "ملف.pdf", tuple(range(1, 101)), None)])
    assert ocr_runner.budget_notices(stats) == [
        "استخرجنا أول 100 صفحة من «ملف.pdf» حسب رصيدك، وقد تكون فيه صفحات أخرى لم تُستخرج."
    ]
    assert ocr_runner.budget_notices(None) == []
