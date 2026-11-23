"""The text-layer warning reaches every segmenter call and every stored section
(owner, 2026-11-23): pages read from the PDF's text layer, not by OCR, must not
have their dates and numbers trusted by any agent downstream."""
from __future__ import annotations

import pytest

from agents.memory.attachment_segmenter import load_segment_maps
from agents.memory.attachment_segmenter import runner as runner_mod
from agents.memory.attachment_segmenter.paragraphs import Paragraph
from agents.memory.attachment_segmenter.runner import run_attachment_bundle, run_attachment_segmenter
from agents.memory.attachment_segmenter.text_layer import (
    call_notice,
    marks_for,
    stamp_rows,
    text_layer_pages,
)
from agents.memory.ocr_extractor.mistral_ocr import join_pages
from agents.memory.ocr_extractor.partial import TEXT_LAYER_CAUTION
from agents.utils.attachment_delivery import (
    DEEP_SEARCH_VALUES,
    WRITER_VALUES,
    render_sections,
    sections_from_rows,
)

from .test_bundles import owners_example_db, per_file
from .test_runner import ITEM, Script, _db, _line, full, prompt_range, whole

HEADING = "## Pages taken from the PDF's text layer, not by OCR"
STAMP = "〔[نظام] نص"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(runner_mod, "segmenter_enabled", lambda uid: True)
    monkeypatch.setattr(runner_mod, "_codec_for", lambda supabase, uid: None)
    runner_mod._BUNDLE_TASKS.clear()


def use(monkeypatch, handler) -> Script:
    script = Script(handler)
    monkeypatch.setattr(runner_mod, "create_attachment_segmenter", script.factory)
    return script


def _paras(pages):
    return [Paragraph(n=i + 1, char_start=0, char_end=1, text="x", page=pg) for i, pg in enumerate(pages)]


# ── the pieces ───────────────────────────────────────────────────────────────

def test_text_layer_pages_reads_the_ranges():
    assert text_layer_pages({"ocr_text_layer_ranges": [[3, 5], [9, 9]]}) == {3, 4, 5, 9}
    assert text_layer_pages({}) == frozenset() and text_layer_pages(None) == frozenset()
    assert text_layer_pages({"ocr_text_layer_ranges": [["x"], [2]]}) == frozenset()


def test_the_notice_names_only_the_calls_own_paragraphs():
    marks = marks_for(_paras([1, 1, 2, 2, 3, 3]), frozenset({2}))
    note = call_notice(1, 6, marks)
    assert note.startswith(HEADING)
    assert "Paragraphs P003–P004 come from pages" in note
    assert "never state a date, number or amount" in note
    assert call_notice(5, 6, marks) == ""  # a part with no such page is not told
    assert call_notice(1, 6, marks_for(_paras([1, 2]), frozenset())) == ""


def test_without_page_numbers_every_paragraph_of_the_file_is_named():
    note = call_notice(1, 4, marks_for(_paras([None] * 4), frozenset({7, 8})))
    assert "Paragraphs P001–P004" in note
    assert "pages 7–8 of it came from the text layer" in note


def test_a_bundle_member_is_numbered_globally():
    marks = marks_for(_paras([1, 2]), frozenset({2}), first_n=11)
    assert set(marks) == {11, 12}
    assert "Paragraphs P012 come from pages" in call_notice(1, 12, marks)


def test_stamp_rows():
    rows = [
        {"seq": 1, "page_from": 1, "page_to": 1, "content_md": "a"},
        {"seq": 2, "page_from": 1, "page_to": 3, "content_md": "b"},
        {"seq": 3, "page_from": None, "page_to": None, "content_md": "c"},
    ]
    out, stamped = stamp_rows(rows, frozenset({2, 3, 9}))
    assert stamped == 2
    assert out[0]["content_md"] == "a"
    assert out[1]["content_md"].startswith(
        "〔[نظام] نص الصفحات 2–3 في هذا المقطع مستخرج من طبقة النص المخزّنة في الملف لا بـOCR"
    )
    assert TEXT_LAYER_CAUTION in out[1]["content_md"] and out[1]["content_md"].endswith("〕\nb")
    assert out[2]["content_md"].startswith("〔[نظام] بعض صفحات هذا الملف (2–3، 9)")
    assert rows[1]["content_md"] == "b"  # the input rows are left as they were
    assert stamp_rows(rows, frozenset()) == (rows, 0)


# ── a whole run ──────────────────────────────────────────────────────────────

def _paged_db(n_pages=3, per_page=12, layer=((2, 3),)):
    md = ["\n".join(_line() for _ in range(per_page)) for _ in range(n_pages)]
    content, offsets = join_pages(md)
    assert len(content) > 5_000
    pages = [{"item_id": ITEM, "page_no": i + 1, "char_start": off, "content_md": m}
             for i, (off, m) in enumerate(zip(offsets, md))]
    meta = {"doc_type": "judgment", "ocr_text_layer_ranges": [list(r) for r in layer]}
    return _db(content, pages=pages, metadata=meta), content


def _rows(db, item_id=ITEM):
    return sorted((r for r in db.tables["attachment_segments"] if r["item_id"] == item_id),
                  key=lambda r: r["seq"])


@pytest.mark.asyncio
async def test_the_call_is_told_and_the_sections_are_stamped(monkeypatch):
    db, _content = _paged_db()  # page 1 = P001–P012, pages 2–3 = P013–P036
    script = use(monkeypatch, whole)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "ok", res

    prompt = script.calls[0]["prompt"]
    assert "Paragraphs P013–P036 come from pages" in prompt
    assert prompt.index(HEADING) < prompt.index("## Paragraphs")

    # good_map: header P1–2 (page 1) · grounds P3–35 (pages 1–3) · signatures P36 (page 3)
    header, grounds, signatures = _rows(db)
    assert not header["content_md"].startswith("〔")
    assert grounds["content_md"].startswith(f"{STAMP} الصفحات 2–3 في هذا المقطع")
    assert signatures["content_md"].startswith(f"{STAMP} الصفحة 3 في هذا المقطع")

    # …and the warning is what every recipient reads first, trimmed or not
    sections = sections_from_rows(load_segment_maps(db, [ITEM])[ITEM].segments)
    for keep in (DEEP_SEARCH_VALUES, WRITER_VALUES):
        for ratio in (1.0, 0.3):
            text, _ = render_sections(sections, keep, ratio=ratio)
            assert f"{STAMP} الصفحات 2–3 في هذا المقطع" in text


@pytest.mark.asyncio
async def test_a_file_without_text_layer_pages_is_unchanged(monkeypatch):
    db, content = _paged_db(layer=())
    script = use(monkeypatch, whole)
    assert (await run_attachment_segmenter(db, ITEM)).outcome == "ok"
    assert HEADING not in script.calls[0]["prompt"]
    for r in _rows(db):
        assert r["content_md"] == content[r["char_start"]:r["char_end"]]


def _split_handler(prompt, instr, n):
    a, b = prompt_range(prompt)
    return full([
        {"from_p": a, "to_p": a + 1, "label": "header",
         "value": "metadata" if a == 1 else "boilerplate", "what": ""},
        {"from_p": a + 2, "to_p": b, "label": "grounds", "value": "core", "what": f"تسبيب {a}"},
    ])


@pytest.mark.asyncio
async def test_in_a_split_run_only_the_part_holding_such_pages_is_told(monkeypatch):
    db, _content = _paged_db(n_pages=4, layer=((4, 4),))
    script = use(monkeypatch, _split_handler)
    res = await run_attachment_segmenter(db, ITEM, parts=2)
    assert res.outcome == "ok" and len(script.calls) == 2
    told = [c["prompt"] for c in script.calls if HEADING in c["prompt"]]
    assert len(told) == 1 and "part 2 of 2" in told[0]
    assert any(r["content_md"].startswith(f"{STAMP} الصفحة 4 في هذا المقطع") for r in _rows(db))


@pytest.mark.asyncio
async def test_a_bundle_member_from_the_text_layer(monkeypatch):
    db = owners_example_db()  # no page rows: the member's pages cannot be placed
    s2 = next(r for r in db.tables["workspace_items"] if r["item_id"] == "s2")
    s2["metadata"] = {**s2["metadata"], "ocr_text_layer_ranges": [[1, 1]]}
    script = use(monkeypatch, per_file)
    res = await run_attachment_bundle(db, ["s1", "s2", "s3"])
    assert all(r.outcome == "ok" for r in res.values()), res

    prompt = script.calls[0]["prompt"]
    assert HEADING in prompt and "pages 1 of it came from the text layer" in prompt
    assert all(r["content_md"].startswith("〔[نظام] بعض صفحات هذا الملف (1)") for r in _rows(db, "s2"))
    assert not any(r["content_md"].startswith("〔") for r in _rows(db, "s1") + _rows(db, "s3"))
