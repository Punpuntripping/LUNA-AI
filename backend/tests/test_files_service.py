"""«ملفاتي» — files_service + /api/v1/files routes (.claude/plans/my_files.md §11).

No live DB: ``FakeSupabase`` is an in-memory PostgREST stand-in that supports
the query shapes the service uses (select with a projection and the embeds,
eq / neq / gt / is_, ``metadata->>key`` paths, order, range / limit, update,
the user_files RPCs). It records every select so a test can prove the library
list never reads ``content_md``. Every library test runs on the Phase-1 path
AND the Phase-2 ``user_files`` paths (see ``data_path``).

All document text below is invented sample text.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from backend.app.errors import LunaHTTPException
from backend.app.services import files_service as fs
from shared.files import flags as files_flags
from shared.files.tests.user_files_fake import FakeSupabase as _UserFilesFake

# ---------------------------------------------------------------------------
# ids
# ---------------------------------------------------------------------------

USER = "11111111-1111-1111-1111-111111111111"
OTHER_USER = "22222222-2222-2222-2222-222222222222"
CONV_A = "aaaaaaaa-0000-0000-0000-000000000001"
CONV_B = "aaaaaaaa-0000-0000-0000-000000000002"
CONV_DELETED = "aaaaaaaa-0000-0000-0000-000000000003"
CONV_OTHER = "aaaaaaaa-0000-0000-0000-000000000004"


def _id() -> str:
    return str(uuid.uuid4())


SAMPLE_TEXT = "عقد إيجار محل تجاري بين المؤجر والمستأجر لمدة سنة واحدة تبدأ من أول الشهر."
SAMPLE_SUMMARY = (
    "**ملخص المستند:**\n"
    "عقد **إيجار** لمحل تجاري بين طرفين لمدة سنة، يحدد `الأجرة` وطريقة السداد.\n\n"
    "**أبرز المعطيات:**\n"
    "- **الأجرة:** خمسون ألف ريال\n\n"
    "**صلة المستند بالمحادثة:**\n"
    "رفعه المستخدم ليسأل عن فسخ العقد."
)


# ---------------------------------------------------------------------------
# The fake, and the three data paths
# ---------------------------------------------------------------------------
#
# ``FakeSupabase`` is the shared in-memory PostgREST stand-in
# (shared/files/tests/user_files_fake.py) — the same query shapes as before plus
# ``user_files``, its generated columns, the pointer embed and the 221 sync
# triggers. Every test that reads or writes the library runs on each path
# (my_files_phase2.md §F):
#
#   items             switches off, no user_files yet  — Phase 1, unchanged
#   items-after-221   switches off, files backfilled   — the off path after the migration
#   files-read        read switch on: the library is read from user_files
#   files-read-write  both on: «حذف من الملفات» is one user_files_hide statement

_MODES = ("items", "items-after-221", "files-read", "files-read-write")
_PATH = {"mode": "items"}
_PATH_AWARE = (
    "test_list_", "test_detail_", "test_hide_", "test_match_", "test_routes_", "test_no_query_",
)


class FakeSupabase(_UserFilesFake):
    def __init__(self, **tables: list[dict]) -> None:
        super().__init__(backfill=_PATH["mode"] != "items", **tables)


def pytest_generate_tests(metafunc):
    if "data_path" not in metafunc.fixturenames:
        return
    if any(m.args and "data_path" in str(m.args[0]) for m in metafunc.definition.iter_markers("parametrize")):
        return   # the test names its own paths
    modes = _MODES if metafunc.function.__name__.startswith(_PATH_AWARE) else ("items",)
    metafunc.parametrize("data_path", modes, indirect=True)


@pytest.fixture(autouse=True)
def data_path(request):
    mode = getattr(request, "param", "items")
    _PATH["mode"] = mode
    files_flags.reset_probe()
    with files_flags.override(read=mode.startswith("files"), write=mode == "files-read-write"):
        yield mode
    files_flags.reset_probe()
    _PATH["mode"] = "items"


def _on_files(mode: str) -> bool:
    return mode.startswith("files")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def conversations() -> list[dict]:
    return [
        {"conversation_id": CONV_A, "user_id": USER, "deleted_at": None},
        {"conversation_id": CONV_B, "user_id": USER, "deleted_at": None},
        {"conversation_id": CONV_DELETED, "user_id": USER, "deleted_at": "2026-11-15T09:00:00+00:00"},
        {"conversation_id": CONV_OTHER, "user_id": OTHER_USER, "deleted_at": None},
    ]


def att(
    item_id: Optional[str] = None,
    *,
    user: str = USER,
    conv: Optional[str] = CONV_A,
    created: str = "2026-11-10T10:00:00+00:00",
    text: Optional[str] = SAMPLE_TEXT,
    summary: Optional[str] = SAMPLE_SUMMARY,
    wi_seq: Optional[int] = 1,
    kind: str = "attachment",
    deleted_at: Optional[str] = None,
    filename: Optional[str] = "عقد الإيجار.pdf",
    size: Optional[int] = 1000,
    mime: Optional[str] = "application/pdf",
    title: str = "عقد إيجار محل تجاري",
    storage_path: Optional[str] = "general/u/convos/c/file.pdf",
    **meta: Any,
) -> dict:
    metadata: dict = {"ocr_status": "done", "upload_status": "ready"}
    if filename is not None:
        metadata["filename"] = filename
    if size is not None:
        metadata["file_size_bytes"] = size
    if mime is not None:
        metadata["mime_type"] = mime
    if text:
        metadata["ocr_chars"] = len(text)
    metadata.update(meta)
    return {
        "item_id": item_id or _id(),
        "user_id": user,
        "conversation_id": conv,
        "wi_seq": wi_seq,
        "kind": kind,
        "title": title,
        "content_md": text,
        "summary": summary,
        "metadata": metadata,
        "storage_path": storage_path,
        "document_id": None,
        "created_at": created,
        "updated_at": created,
        "deleted_at": deleted_at,
    }


def smap(item_id: str, chars: int, docs: Optional[list[dict]] = None, user: str = USER) -> dict:
    return {"item_id": item_id, "user_id": user, "content_chars": chars, "documents_in_file": docs or []}


def seg(item_id: str, seq: int, value: str, *, from_p: int = 1, what: str = "قسم", text: str = "نص القسم",
        page_from: Optional[int] = None, page_to: Optional[int] = None) -> dict:
    return {
        "item_id": item_id, "seq": seq, "from_p": from_p, "to_p": from_p,
        "page_from": page_from, "page_to": page_to, "char_start": 0, "char_end": 1,
        "label": "parties", "value": value, "what": what, "content_md": text,
    }


# ===========================================================================
# 1. summary_preview
# ===========================================================================


def test_preview_drops_the_label_line_and_takes_the_first_paragraph():
    out = fs.summary_preview(SAMPLE_SUMMARY)
    assert out == "عقد إيجار لمحل تجاري بين طرفين لمدة سنة، يحدد الأجرة وطريقة السداد."


def test_preview_drops_an_inline_label_and_stops_at_the_next_label():
    out = fs.summary_preview("**ملخص المستند:** حكم ابتدائي في دعوى مالية.\n**أبرز المعطيات:**\n- المبلغ")
    assert out == "حكم ابتدائي في دعوى مالية."
    assert fs.summary_preview("**ملخص المستند**: محضر جلسة أولى.") == "محضر جلسة أولى."


def test_preview_removes_markdown_marks():
    out = fs.summary_preview("## ملخص\n\n- **بند** أول `مهم`\n- بند *ثان*\n\nفقرة أخرى")
    assert out == "بند أول مهم بند ثان"
    for mark in ("**", "#", "`", "*", "- "):
        assert mark not in out


def test_preview_cuts_on_a_word_boundary_with_an_ellipsis():
    words = " ".join(["المستأجر", "يلتزم", "بسداد", "الأجرة", "في", "موعدها"] * 20)
    out = fs.summary_preview(words)
    assert len(out) <= fs.PREVIEW_MAX_CHARS
    assert out.endswith("…")
    body = out[:-1]
    assert words.startswith(body)
    assert words[len(body)] == " ", "cut inside a word"


def test_preview_at_the_limit_is_not_cut():
    text = "ق" * fs.PREVIEW_MAX_CHARS
    assert fs.summary_preview(text) == text


@pytest.mark.parametrize("value", [None, "", "   \n\n", "**ملخص المستند:**\n\n", 42])
def test_preview_empty(value):
    assert fs.summary_preview(value) == ""


# ===========================================================================
# 2. list_library_files
# ===========================================================================


def _list(sb: FakeSupabase) -> dict:
    return fs.list_library_files(sb, USER)


def test_list_never_selects_content_md_nor_returns_text_or_full_summary(data_path):
    a = att()
    sb = FakeSupabase(workspace_items=[a], conversations=conversations())
    out = _list(sb)
    table = "user_files" if _on_files(data_path) else "workspace_items"
    item_selects = [cols for t, cols in sb.selects if t == table and cols != "file_id"]
    assert item_selects, f"the list must read {table}"
    for cols in item_selects:
        assert "content_md" not in cols
        assert cols.strip() != "*"
    [f] = out["files"]
    assert "content_md" not in f and "summary" not in f
    assert f["summary_preview"] == fs.summary_preview(SAMPLE_SUMMARY)
    assert f["chars"] == len(SAMPLE_TEXT)
    assert set(f) == {
        "item_id", "title", "filename", "mime_type", "doc_type", "summary_preview", "page_count",
        "chars", "has_sections", "original_available", "conversation_id", "wi_seq", "copies",
        "created_at",
    } | ({"file_id"} if _on_files(data_path) else set())
    if _on_files(data_path):
        assert f["file_id"] == a["item_id"] and f["item_id"] == a["item_id"]
    else:
        assert [t for t, _c in sb.selects if t == "user_files"] == []


def test_list_excludes_hidden_deleted_conversation_no_text_and_foreign_rows():
    keep = att(filename="أ.pdf")
    no_conv = att(filename="ب.pdf", conv=None, wi_seq=None)
    rows = [
        keep,
        no_conv,
        att(filename="مخفي.pdf", library_hidden_at="2026-11-18T00:00:00+00:00"),
        att(filename="محادثة محذوفة.pdf", conv=CONV_DELETED),
        att(filename="بلا نص.pdf", text=""),
        att(filename="بلا نص 2.pdf", text=None),
        att(filename="محذوف.pdf", deleted_at="2026-11-12T00:00:00+00:00"),
        att(filename="غيري.pdf", user=OTHER_USER, conv=CONV_OTHER),
        att(filename="ملاحظة.pdf", kind="note"),
    ]
    out = _list(FakeSupabase(workspace_items=rows, conversations=conversations()))
    assert {f["item_id"] for f in out["files"]} == {keep["item_id"], no_conv["item_id"]}
    assert out["total"] == 2


def test_list_folds_identical_files_into_the_newest_copy():
    old = att(conv=CONV_A, created="2026-11-01T10:00:00+00:00", wi_seq=1)
    mid = att(conv=CONV_B, created="2026-11-05T10:00:00+00:00", wi_seq=2)
    new = att(conv=CONV_A, created="2026-11-09T10:00:00+00:00", wi_seq=3, doc_type="contract")
    hidden_copy = att(created="2026-11-11T10:00:00+00:00", library_hidden_at="2026-11-12T00:00:00+00:00")
    other_mime = att(mime="image/png", created="2026-11-02T10:00:00+00:00")
    other_size = att(size=999, created="2026-11-03T10:00:00+00:00", doc_type="judgment")
    no_name_1 = att(filename=None, created="2026-11-04T10:00:00+00:00")
    no_name_2 = att(filename=None, created="2026-11-04T11:00:00+00:00")
    no_size_1 = att(size=None, created="2026-11-06T10:00:00+00:00")
    no_size_2 = att(size=None, created="2026-11-06T11:00:00+00:00")
    rows = [old, mid, new, hidden_copy, other_mime, other_size, no_name_1, no_name_2, no_size_1, no_size_2]
    out = _list(FakeSupabase(workspace_items=rows, conversations=conversations()))

    by_id = {f["item_id"]: f for f in out["files"]}
    assert new["item_id"] in by_id and old["item_id"] not in by_id and mid["item_id"] not in by_id
    assert by_id[new["item_id"]]["copies"] == 3          # the hidden copy is not counted
    assert hidden_copy["item_id"] not in by_id
    for row in (other_mime, other_size, no_name_1, no_name_2, no_size_1, no_size_2):
        assert by_id[row["item_id"]]["copies"] == 1
    assert out["total"] == 7

    # newest first
    stamps = [f["created_at"] for f in out["files"]]
    assert stamps == sorted(stamps, reverse=True)

    # type_counts after folding: the three-copy group counts once
    assert out["type_counts"] == {"contract": 1, "judgment": 1, "untyped": 5}
    assert sum(out["type_counts"].values()) == out["total"]


def test_list_unknown_doc_type_is_untyped():
    out = _list(FakeSupabase(workspace_items=[att(doc_type="not_a_type")], conversations=conversations()))
    assert out["files"][0]["doc_type"] is None
    assert out["type_counts"] == {"untyped": 1}


def test_list_has_sections_only_for_a_fresh_map():
    fresh = att(filename="1.pdf")
    stale = att(filename="2.pdf")
    none = att(filename="3.pdf")
    maps = [smap(fresh["item_id"], len(SAMPLE_TEXT)), smap(stale["item_id"], len(SAMPLE_TEXT) + 7)]
    out = _list(FakeSupabase(
        workspace_items=[fresh, stale, none], conversations=conversations(), attachment_segment_maps=maps,
    ))
    flags = {f["item_id"]: f["has_sections"] for f in out["files"]}
    assert flags == {fresh["item_id"]: True, stale["item_id"]: False, none["item_id"]: False}


def test_list_map_read_failure_lists_without_sections():
    a = att()
    sb = FakeSupabase(workspace_items=[a], conversations=conversations(),
                      attachment_segment_maps=[smap(a["item_id"], len(SAMPLE_TEXT))])
    sb.fail_tables.add("attachment_segment_maps")
    out = _list(sb)
    assert out["files"][0]["has_sections"] is False


def test_list_cap_total_and_pagination(monkeypatch):
    monkeypatch.setattr(fs, "_PAGE_ROWS", 50)   # forces several pages through the loop
    rows = [
        att(filename=f"ملف {i}.pdf", created=f"2026-10-{1 + i % 28:02d}T{i % 24:02d}:{i % 60:02d}:00+00:00")
        for i in range(fs.LIBRARY_CAP + 5)
    ]
    out = _list(FakeSupabase(workspace_items=rows, conversations=conversations()))
    assert len(out["files"]) == fs.LIBRARY_CAP
    assert out["total"] == fs.LIBRARY_CAP + 5


def test_list_original_available_and_page_count():
    purged = att(filename="1.pdf", storage_path=None, original_purged_at="2026-11-01T00:00:00+00:00", page_count=4)
    live = att(filename="2.pdf", page_count=9, ocr_pages=9)
    partial = att(filename="3.pdf", page_count=40, ocr_pages=10, ocr_partial=True, ocr_page_total=42)
    out = _list(FakeSupabase(workspace_items=[purged, live, partial], conversations=conversations()))
    by_id = {f["item_id"]: f for f in out["files"]}
    assert by_id[purged["item_id"]]["original_available"] is False
    assert by_id[live["item_id"]]["original_available"] is True
    assert by_id[purged["item_id"]]["page_count"] == 4
    assert by_id[partial["item_id"]]["page_count"] == 42


def test_list_read_failure_is_a_500():
    sb = FakeSupabase(workspace_items=[att()], conversations=conversations())
    sb.fail_tables.update({"workspace_items", "user_files"})
    with pytest.raises(LunaHTTPException) as exc:
        _list(sb)
    assert exc.value.status_code == 500


# ===========================================================================
# 3. get_library_file
# ===========================================================================


def test_detail_without_map_has_no_sections_and_the_full_text_and_summary():
    a = att()
    out = fs.get_library_file(FakeSupabase(workspace_items=[a], conversations=conversations()), USER, a["item_id"])
    assert out["sections"] is None and out["documents"] == [] and out["has_sections"] is False
    assert out["content_md"] == SAMPLE_TEXT
    assert out["summary"] == SAMPLE_SUMMARY
    assert out["chars"] == len(SAMPLE_TEXT)


def test_detail_stale_map_is_no_sections_judged_on_the_real_text_length():
    a = att(ocr_chars=len(SAMPLE_TEXT) + 3)   # metadata disagrees; the real length decides
    sb = FakeSupabase(
        workspace_items=[a], conversations=conversations(),
        attachment_segment_maps=[smap(a["item_id"], len(SAMPLE_TEXT) + 3)],
        attachment_segments=[seg(a["item_id"], 1, "core")],
    )
    out = fs.get_library_file(sb, USER, a["item_id"])
    assert out["sections"] is None
    assert out["chars"] == len(SAMPLE_TEXT)


def test_detail_sections_drop_boilerplate_and_repeats_and_hide_label_and_value():
    a = att()
    iid = a["item_id"]
    segments = [
        seg(iid, 3, "core", what="الوقائع", text="وقائع الدعوى", page_from=2, page_to=3),
        seg(iid, 1, "boilerplate", what="ترويسة", text="شعار المحكمة"),
        seg(iid, 2, "metadata", what="بيانات الصك", text="رقم الصك وتاريخه", page_from=1, page_to=1),
        seg(iid, 4, "repeat", what="تذييل", text="تذييل مكرر"),
        seg(iid, 5, "supporting", what="المرفقات", text="قائمة المرفقات"),
    ]
    sb = FakeSupabase(
        workspace_items=[a], conversations=conversations(),
        attachment_segment_maps=[smap(iid, len(SAMPLE_TEXT), [{"name": "صك الحكم", "from_p": 1, "to_p": 9}])],
        attachment_segments=segments,
    )
    out = fs.get_library_file(sb, USER, iid)
    assert out["has_sections"] is True
    assert [s["seq"] for s in out["sections"]] == [2, 3, 5]
    texts = {s["content_md"] for s in out["sections"]}
    assert "شعار المحكمة" not in texts and "تذييل مكرر" not in texts
    assert [s["is_metadata"] for s in out["sections"]] == [True, False, False]
    for s in out["sections"]:
        assert set(s) == {"seq", "what", "is_metadata", "page_from", "page_to", "document_index", "content_md"}
        assert s["document_index"] is None            # one document in the file
    assert out["documents"] == []
    assert out["sections"][1]["page_from"] == 2 and out["sections"][1]["page_to"] == 3
    # the English label is never even selected
    seg_selects = [cols for table, cols in sb.selects if table == "attachment_segments"]
    assert seg_selects and all("label" not in cols for cols in seg_selects)


def test_detail_only_hidden_values_or_no_rows_means_no_sections():
    a = att()
    iid = a["item_id"]
    sb = FakeSupabase(
        workspace_items=[a], conversations=conversations(),
        attachment_segment_maps=[smap(iid, len(SAMPLE_TEXT))],
        attachment_segments=[seg(iid, 1, "boilerplate"), seg(iid, 2, "repeat")],
    )
    assert fs.get_library_file(sb, USER, iid)["sections"] is None
    sb.tables["attachment_segments"] = []
    assert fs.get_library_file(sb, USER, iid)["sections"] is None


def test_detail_document_index_only_with_two_or_more_documents():
    a = att()
    iid = a["item_id"]
    docs = [{"name": "صحيفة الدعوى", "from_p": 1, "to_p": 10}, {"name": "صك الحكم", "from_p": 15, "to_p": 30}]
    sb = FakeSupabase(
        workspace_items=[a], conversations=conversations(),
        attachment_segment_maps=[smap(iid, len(SAMPLE_TEXT), docs)],
        attachment_segments=[
            seg(iid, 1, "core", from_p=1),
            seg(iid, 2, "core", from_p=12),      # in the gap → joins the document before it
            seg(iid, 3, "core", from_p=20),
        ],
    )
    out = fs.get_library_file(sb, USER, iid)
    assert out["documents"] == [{"name": "صحيفة الدعوى"}, {"name": "صك الحكم"}]
    assert [s["document_index"] for s in out["sections"]] == [0, 0, 1]


def test_detail_section_read_failure_falls_back_to_full_text():
    a = att()
    sb = FakeSupabase(workspace_items=[a], conversations=conversations(),
                      attachment_segment_maps=[smap(a["item_id"], len(SAMPLE_TEXT))],
                      attachment_segments=[seg(a["item_id"], 1, "core")])
    sb.fail_tables.add("attachment_segments")
    out = fs.get_library_file(sb, USER, a["item_id"])
    assert out["sections"] is None and out["content_md"] == SAMPLE_TEXT


def test_detail_is_owner_only_and_attachment_only():
    mine = att()
    theirs = att(user=OTHER_USER, conv=CONV_OTHER)
    deleted = att(deleted_at="2026-11-12T00:00:00+00:00")
    note = att(kind="note")
    sb = FakeSupabase(workspace_items=[mine, theirs, deleted, note], conversations=conversations())
    assert fs.get_library_file(sb, USER, mine["item_id"]) is not None
    assert fs.get_library_file(sb, USER, theirs["item_id"]) is None
    assert fs.get_library_file(sb, USER, deleted["item_id"]) is None
    assert fs.get_library_file(sb, USER, note["item_id"]) is None
    assert fs.get_library_file(sb, USER, _id()) is None


def test_detail_works_for_files_the_library_does_not_list():
    hidden = att(filename="مخفي.pdf", library_hidden_at="2026-11-18T00:00:00+00:00")
    old = att(created="2026-11-01T10:00:00+00:00")
    new = att(created="2026-11-09T10:00:00+00:00")
    processing = att(filename="قيد المعالجة.pdf", text=None, summary=None, ocr_status=None)
    sb = FakeSupabase(workspace_items=[hidden, old, new, processing], conversations=conversations())
    assert fs.get_library_file(sb, USER, hidden["item_id"])["copies"] == 1
    folded = fs.get_library_file(sb, USER, old["item_id"])
    assert folded["item_id"] == old["item_id"] and folded["copies"] == 2
    pending = fs.get_library_file(sb, USER, processing["item_id"])
    assert pending["content_md"] == "" and pending["sections"] is None and pending["summary_preview"] == ""


# ===========================================================================
# 4. hide_library_file
# ===========================================================================


def test_hide_stamps_the_file_and_every_identical_copy_keeping_metadata():
    target = att(conv=CONV_A, created="2026-11-09T10:00:00+00:00", doc_type="contract")
    copy_b = att(conv=CONV_B, created="2026-11-01T10:00:00+00:00")
    copy_no_text = att(conv=CONV_A, text=None, ocr_status=None, created="2026-11-10T10:00:00+00:00")
    different = att(filename="آخر.pdf")
    theirs = att(user=OTHER_USER, conv=CONV_OTHER)
    rows = [target, copy_b, copy_no_text, different, theirs]
    sb = FakeSupabase(workspace_items=rows, conversations=conversations())
    before = len(sb.tables["workspace_items"])

    assert fs.hide_library_file(sb, USER, target["item_id"]) is True

    for row in (target, copy_b, copy_no_text):
        meta = sb.row(row["item_id"])["metadata"]
        assert meta.get(fs.HIDDEN_KEY)
        for key, value in row["metadata"].items():          # nothing dropped
            assert meta[key] == value
    assert fs.HIDDEN_KEY not in sb.row(different["item_id"])["metadata"]
    assert fs.HIDDEN_KEY not in sb.row(theirs["item_id"])["metadata"]
    # never a delete
    assert len(sb.tables["workspace_items"]) == before
    assert all(r["deleted_at"] is None for r in sb.tables["workspace_items"])
    assert {f["item_id"] for f in _list(sb)["files"]} == {different["item_id"]}


def test_hide_is_idempotent_and_keeps_the_first_stamp():
    a = att()
    sb = FakeSupabase(workspace_items=[a], conversations=conversations())
    assert fs.hide_library_file(sb, USER, a["item_id"]) is True
    first = sb.row(a["item_id"])["metadata"][fs.HIDDEN_KEY]
    writes = len(sb.updates)
    assert fs.hide_library_file(sb, USER, a["item_id"]) is True
    assert sb.row(a["item_id"])["metadata"][fs.HIDDEN_KEY] == first
    assert len(sb.updates) == writes


def test_hide_another_users_file_is_not_found_and_writes_nothing():
    theirs = att(user=OTHER_USER, conv=CONV_OTHER)
    mine_same = att()
    sb = FakeSupabase(workspace_items=[theirs, mine_same], conversations=conversations())
    assert fs.hide_library_file(sb, USER, theirs["item_id"]) is False
    assert fs.hide_library_file(sb, USER, _id()) is False
    assert sb.updates == []


def test_hide_write_failure_is_a_500():
    a = att()
    b = att(conv=CONV_B, created="2026-11-01T10:00:00+00:00")
    sb = FakeSupabase(workspace_items=[a, b], conversations=conversations())
    sb.fail_update_ids.add(b["item_id"])
    with pytest.raises(LunaHTTPException) as exc:
        fs.hide_library_file(sb, USER, a["item_id"])
    assert exc.value.status_code == 500


# ===========================================================================
# 5. match_library_file
# ===========================================================================

_PICK = {"filename": "عقد الإيجار.pdf", "size_bytes": 1000, "mime_type": "application/pdf"}


def _match(sb: FakeSupabase, conversation_id: Optional[str] = CONV_A, **pick: Any) -> Optional[dict]:
    return fs.match_library_file(sb, USER, conversation_id=conversation_id, **{**_PICK, **pick})


def test_match_in_the_same_conversation():
    a = att(conv=CONV_A, wi_seq=4)
    out = _match(FakeSupabase(workspace_items=[a], conversations=conversations()))
    assert out == {
        "item_id": a["item_id"], "title": a["title"], "created_at": a["created_at"],
        "same_conversation": True, "wi_seq": 4,
    }


def test_match_elsewhere_is_not_proposed_in_phase_1b(monkeypatch):
    b = att(conv=CONV_B)
    sb = FakeSupabase(workspace_items=[b], conversations=conversations())
    assert fs.CROSS_CONVERSATION_MATCH is False
    assert _match(sb) is None
    assert _match(sb, conversation_id=None) is None
    monkeypatch.setattr(fs, "CROSS_CONVERSATION_MATCH", True)   # Phase 3
    out = _match(sb)
    assert out["item_id"] == b["item_id"] and out["same_conversation"] is False


def test_match_newest_wins_and_same_conversation_beats_a_newer_copy_elsewhere(monkeypatch):
    old = att(conv=CONV_A, created="2026-11-01T10:00:00+00:00", wi_seq=1)
    new = att(conv=CONV_A, created="2026-11-05T10:00:00+00:00", wi_seq=2)
    newest_elsewhere = att(conv=CONV_B, created="2026-11-09T10:00:00+00:00")
    sb = FakeSupabase(workspace_items=[old, new, newest_elsewhere], conversations=conversations())
    monkeypatch.setattr(fs, "CROSS_CONVERSATION_MATCH", True)
    assert _match(sb)["item_id"] == new["item_id"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"ocr_partial": True, "ocr_page_total": 40, "ocr_page_ranges": [[1, 10]]},  # page budget
        {"ocr_lost_ranges": [[3, 4]]},                                              # pages lost
        {"ocr_text_layer_ranges": [[1, 2]]},                                        # Mistral failed
        {"ocr_pages": 30, "page_count": 55},                                        # pre-budget 30-page cap
        {"ocr_pages": 30},                                                          # cap, length unknown
        {"ocr_status": "failed"},
        {"ocr_status": "empty"},
        {"ocr_status": None},                                                       # not extracted yet
        {"upload_status": "uploading"},
        {"library_hidden_at": "2026-11-18T00:00:00+00:00"},
    ],
)
def test_match_never_proposes_an_incomplete_hidden_or_unfinished_copy(overrides):
    a = att(conv=CONV_A, **overrides)
    assert _match(FakeSupabase(workspace_items=[a], conversations=conversations())) is None


def test_match_complete_read_with_page_counts_is_proposed():
    a = att(conv=CONV_A, ocr_pages=12, page_count=12)
    assert _match(FakeSupabase(workspace_items=[a], conversations=conversations())) is not None


def test_match_needs_text_and_the_same_name_size_and_type():
    sb = FakeSupabase(workspace_items=[att(conv=CONV_A, text="")], conversations=conversations())
    assert _match(sb) is None
    sb = FakeSupabase(workspace_items=[att(conv=CONV_A)], conversations=conversations())
    assert _match(sb, size_bytes=1001) is None
    assert _match(sb, mime_type="image/png") is None
    assert _match(sb, filename="عقد آخر.pdf") is None
    assert _match(sb) is not None


def test_match_never_returns_another_users_file(monkeypatch):
    monkeypatch.setattr(fs, "CROSS_CONVERSATION_MATCH", True)
    theirs = att(user=OTHER_USER, conv=CONV_OTHER)
    sb = FakeSupabase(workspace_items=[theirs], conversations=conversations())
    assert _match(sb) is None
    assert _match(sb, conversation_id=CONV_OTHER) is None


def test_no_query_carries_a_filename_in_its_url():
    """Filter values travel in the PostgREST URL, which the httpx spans record:
    the file name must be compared in Python, never sent as a filter."""
    a = att(conv=CONV_A)
    b = att(conv=CONV_B, created="2026-11-01T10:00:00+00:00")
    sb = FakeSupabase(workspace_items=[a, b], conversations=conversations())
    fs.list_library_files(sb, USER)
    fs.get_library_file(sb, USER, a["item_id"])
    _match(sb)
    fs.hide_library_file(sb, USER, a["item_id"])
    assert sb.filter_values, "the fake must have seen the filters"
    assert all(_PICK["filename"] not in v for v in sb.filter_values)
    assert all("filename" not in cols for _t, cols in sb.selects)


def test_match_skips_a_deleted_conversation_and_survives_a_read_failure(monkeypatch):
    monkeypatch.setattr(fs, "CROSS_CONVERSATION_MATCH", True)
    sb = FakeSupabase(workspace_items=[att(conv=CONV_DELETED)], conversations=conversations())
    assert _match(sb) is None
    sb = FakeSupabase(workspace_items=[att(conv=CONV_A)], conversations=conversations())
    sb.fail_tables.update({"workspace_items", "user_files"})
    assert _match(sb) is None


# ===========================================================================
# 6. The flag
# ===========================================================================


def _settings(enabled: bool, users: Optional[str] = None):
    return lambda: SimpleNamespace(MY_FILES_ENABLED=enabled, MY_FILES_USERS=users)


def test_flag_rule(monkeypatch):
    monkeypatch.setattr(fs, "get_settings", _settings(False))
    assert fs.my_files_enabled(USER) is False
    monkeypatch.setattr(fs, "get_settings", _settings(True))
    assert fs.my_files_enabled(USER) is True
    monkeypatch.setattr(fs, "get_settings", _settings(True, f" {OTHER_USER} , "))
    assert fs.my_files_enabled(USER) is False
    assert fs.my_files_enabled(OTHER_USER) is True


def test_flag_env_names_equal_field_names(monkeypatch):
    from shared.config import Settings

    for field in ("MY_FILES_ENABLED", "MY_FILES_USERS"):
        info = Settings.model_fields[field]
        assert info.validation_alias is None and info.alias is None
    monkeypatch.setenv("SUPABASE_URL", "http://localhost:54321")
    for name in ("SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_JWT_SECRET"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("MY_FILES_ENABLED", "true")
    monkeypatch.setenv("MY_FILES_USERS", USER)
    s = Settings(_env_file=None)
    assert s.MY_FILES_ENABLED is True and s.MY_FILES_USERS == USER


# ===========================================================================
# 7. The routes
# ===========================================================================


@pytest.fixture
def client_for(monkeypatch):
    def make(sb: FakeSupabase, *, enabled: bool = True, users: Optional[str] = None):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.app.api import files as files_api
        from backend.app.deps import get_current_user, get_supabase
        from backend.app.errors import luna_exception_handler
        from shared.auth.jwt import AuthUser

        monkeypatch.setattr(files_api, "get_user_id", lambda _sb, _auth: USER)
        monkeypatch.setattr(fs, "get_settings", _settings(enabled, users))
        app = FastAPI()
        app.include_router(files_api.router, prefix="/api/v1")
        app.add_exception_handler(LunaHTTPException, luna_exception_handler)
        app.dependency_overrides[get_current_user] = lambda: AuthUser(
            auth_id="auth-1", email="a@b.com", role="authenticated"
        )
        app.dependency_overrides[get_supabase] = lambda: sb
        return TestClient(app)

    return make


def test_routes_404_when_the_feature_is_off(client_for):
    a = att()
    sb = FakeSupabase(workspace_items=[a], conversations=conversations())
    for kwargs in ({"enabled": False}, {"enabled": True, "users": OTHER_USER}):
        c = client_for(sb, **kwargs)
        responses = [
            c.get("/api/v1/files"),
            c.get(f"/api/v1/files/{a['item_id']}"),
            c.delete(f"/api/v1/files/{a['item_id']}"),
            c.post("/api/v1/files/match", json={**_PICK, "conversation_id": CONV_A}),
        ]
        for res in responses:
            assert res.status_code == 404, res.text
            assert res.json()["detail"] == "هذه الميزة غير متاحة"
    assert sb.selects == [] and sb.updates == []


def test_routes_list_detail_hide_and_match(client_for):
    mine = att(conv=CONV_A, wi_seq=2)
    theirs = att(user=OTHER_USER, conv=CONV_OTHER, filename="غيري.pdf")
    sb = FakeSupabase(
        workspace_items=[mine, theirs], conversations=conversations(),
        attachment_segment_maps=[smap(mine["item_id"], len(SAMPLE_TEXT))],
        attachment_segments=[seg(mine["item_id"], 1, "core", what="الوقائع")],
    )
    c = client_for(sb)

    res = c.get("/api/v1/files")
    assert res.status_code == 200 and res.headers["Cache-Control"] == "private, no-store"
    body = res.json()
    assert [f["item_id"] for f in body["files"]] == [mine["item_id"]]
    assert body["files"][0]["has_sections"] is True and body["total"] == 1

    res = c.get(f"/api/v1/files/{mine['item_id']}")
    assert res.status_code == 200
    detail = res.json()
    assert detail["sections"][0]["what"] == "الوقائع" and detail["content_md"] == SAMPLE_TEXT

    res = c.get(f"/api/v1/files/{theirs['item_id']}")
    assert res.status_code == 404 and res.json()["detail"] == "الملف غير موجود"
    assert c.get("/api/v1/files/not-a-uuid").status_code == 404

    res = c.post("/api/v1/files/match", json={**_PICK, "conversation_id": CONV_A})
    assert res.status_code == 200
    assert res.json()["match"]["item_id"] == mine["item_id"]
    assert res.json()["match"]["same_conversation"] is True
    res = c.post("/api/v1/files/match", json={**_PICK, "conversation_id": CONV_B})
    assert res.status_code == 200 and res.json() == {"match": None}
    res = c.post("/api/v1/files/match", json=_PICK)
    assert res.status_code == 200 and res.json() == {"match": None}

    assert c.delete(f"/api/v1/files/{theirs['item_id']}").status_code == 404
    res = c.delete(f"/api/v1/files/{mine['item_id']}")
    assert res.status_code == 204 and res.content == b""
    assert c.get("/api/v1/files").json()["files"] == []
    # the conversation pane still opens it
    assert c.get(f"/api/v1/files/{mine['item_id']}").status_code == 200
    assert c.delete(f"/api/v1/files/{mine['item_id']}").status_code == 204   # idempotent


def test_router_is_mounted_in_the_app(monkeypatch):
    # Importing main runs load_dotenv + configure_logfire: an empty token keeps
    # a unit test from exporting spans (load_dotenv never overrides a set var).
    monkeypatch.setenv("LOGFIRE_TOKEN", "")
    from backend.app.main import create_app

    paths = {getattr(r, "path", "") for r in create_app().routes}
    assert {"/api/v1/files", "/api/v1/files/{item_id}", "/api/v1/files/match"} <= paths


# ===========================================================================
# 8. WorkspaceItemResponse.summary_preview
# ===========================================================================


def test_workspace_response_summary_preview_attachments_only_and_never_when_locked():
    from backend.app.api.workspace import _to_response
    from backend.app.services.wi_preview_service import WiPreview
    from shared.workspace.preview_lock import preview_cut

    attachment = att()
    item = _to_response(attachment).model_dump(mode="json")
    assert item["summary_preview"] == fs.summary_preview(SAMPLE_SUMMARY)

    no_summary = _to_response({**att(), "summary": None}).model_dump(mode="json")
    assert no_summary["summary_preview"] == ""

    search = {**att(kind="agent_search"), "summary": "ملخص تقرير البحث"}
    dumped = _to_response(search).model_dump(mode="json")
    assert "summary_preview" not in dumped           # other kinds: byte-identical to before

    locked = WiPreview(cut=preview_cut(SAMPLE_TEXT), visible_ns=frozenset(), hidden_ref_count=0)
    dumped = _to_response(attachment, locked).model_dump(mode="json")
    assert "summary_preview" not in dumped


# ===========================================================================
# 9. Drift guard — the contract's FileDocType == the summarizer's DocType
# ===========================================================================


def test_doc_types_match_the_summarizer():
    from agents.memory.artifact_summarizer.models import DOC_TYPES

    assert tuple(fs.DOC_TYPES) == tuple(DOC_TYPES)


# ===========================================================================
# 10. Phase 2 — the user_files path only (my_files_phase2.md §D)
# ===========================================================================

FILE_ID = "ffffffff-0000-0000-0000-0000000000f1"


def _split_pointer_world() -> FakeSupabase:
    """One file whose conversation item has a DIFFERENT id (never rely on equality)."""
    a = att(conv=CONV_A, wi_seq=7)
    sb = FakeSupabase(workspace_items=[a], conversations=conversations())
    pointer = sb.row(a["item_id"])
    f = next(f for f in sb.tables["user_files"] if f["file_id"] == a["item_id"])
    f["file_id"] = FILE_ID
    pointer["file_id"] = FILE_ID
    return sb


@pytest.mark.parametrize("data_path", ["files-read", "files-read-write"], indirect=True)
def test_files_path_pointer_id_differs_from_file_id(data_path):
    sb = _split_pointer_world()
    item_id = sb.tables["workspace_items"][0]["item_id"]
    [f] = fs.list_library_files(sb, USER)["files"]
    assert (f["item_id"], f["file_id"], f["wi_seq"], f["conversation_id"]) == (item_id, FILE_ID, 7, CONV_A)
    by_item = fs.get_library_file(sb, USER, item_id)
    by_file = fs.get_library_file(sb, USER, FILE_ID)
    for d in (by_item, by_file):
        assert d["item_id"] == item_id and d["file_id"] == FILE_ID and d["content_md"] == SAMPLE_TEXT
    m = _match(sb)
    assert (m["item_id"], m["wi_seq"], m["same_conversation"]) == (item_id, 7, True)
    assert fs.hide_library_file(sb, USER, item_id) is True
    if data_path == "files-read-write":
        assert sb.rpcs[-1] == ("user_files_hide", {"p_user_id": USER, "p_file_ids": [FILE_ID]})
        assert fs.list_library_files(sb, USER)["files"] == []
    else:
        # Write switch off: the stamp lands on the item only, and the 221 mirror
        # copies an item to its file only while file_id = item_id — a split
        # pointer is a Phase-3 shape, which needs the write switch on (§E).
        assert fs.HIDDEN_KEY in sb.row(item_id)["metadata"]
        assert len(fs.list_library_files(sb, USER)["files"]) == 1


@pytest.mark.parametrize("data_path", ["files-read", "files-read-write"], indirect=True)
def test_files_path_hidden_is_the_column(data_path):
    a = att()
    sb = FakeSupabase(workspace_items=[a], conversations=conversations())
    assert len(fs.list_library_files(sb, USER)["files"]) == 1
    sb.file(a["item_id"])["library_hidden_at"] = "2026-11-18T00:00:00+00:00"   # column only
    assert fs.list_library_files(sb, USER)["files"] == []
    assert fs.get_library_file(sb, USER, a["item_id"]) is not None           # the pane still opens it


@pytest.mark.parametrize("data_path", ["files-read-write"], indirect=True)
def test_hide_is_one_statement_and_survives_a_stale_metadata_write(data_path):
    """Plan §12 item 7: a writer finishing with metadata read before the stamp
    can no longer bring the file back into the library."""
    a = att()
    copy_b = att(conv=CONV_B, created="2026-11-01T10:00:00+00:00")
    sb = FakeSupabase(workspace_items=[a, copy_b], conversations=conversations())
    stale = dict(sb.row(a["item_id"])["metadata"])
    assert fs.hide_library_file(sb, USER, a["item_id"]) is True
    assert [name for name, _p in sb.rpcs] == ["user_files_hide"]
    assert sorted(sb.rpcs[0][1]["p_file_ids"]) == sorted([a["item_id"], copy_b["item_id"]])
    assert not [s for s in sb.statements if s[1] == "workspace_items"]       # no read-merge-write
    # OCR / the summarizer writing back an old snapshot on the item (legacy write path)
    sb.table("workspace_items").update({"metadata": stale}).eq("item_id", a["item_id"]).execute()
    assert fs.HIDDEN_KEY not in sb.row(a["item_id"])["metadata"]
    assert fs.list_library_files(sb, USER)["files"] == []                    # the column held


@pytest.mark.parametrize("data_path", ["files-read"], indirect=True)
def test_files_path_unlinked_item_falls_back_to_the_item(data_path):
    a = att()
    sb = FakeSupabase(workspace_items=[a], conversations=conversations())
    sb.row(a["item_id"])["file_id"] = None
    sb.tables["user_files"] = []
    d = fs.get_library_file(sb, USER, a["item_id"])
    assert d is not None and d["content_md"] == SAMPLE_TEXT and "file_id" not in d


def test_file_id_is_absent_from_the_json_unless_set():
    from backend.app.models.responses import LibraryFile, LibraryFileDetail, LibraryFileListResponse

    base = {"item_id": "i", "title": "t", "created_at": "2026-11-10T10:00:00+00:00"}
    assert "file_id" not in LibraryFile.model_validate(base).model_dump(mode="json")
    assert LibraryFile.model_validate({**base, "file_id": "f"}).model_dump(mode="json")["file_id"] == "f"
    detail = LibraryFileDetail.model_validate(base).model_dump(mode="json")
    assert "file_id" not in detail and detail["sections"] is None
    listed = LibraryFileListResponse.model_validate(
        {"files": [base, {**base, "file_id": "f"}], "total": 2, "type_counts": {}}
    ).model_dump(mode="json")
    assert "file_id" not in listed["files"][0] and listed["files"][1]["file_id"] == "f"


@pytest.mark.parametrize("data_path", ["items", "files-read"], indirect=True)
def test_routes_carry_file_id_only_on_the_files_path(client_for, data_path):
    a = att()
    sb = FakeSupabase(workspace_items=[a], conversations=conversations())
    c = client_for(sb)
    listed = c.get("/api/v1/files").json()["files"][0]
    detail = c.get(f"/api/v1/files/{a['item_id']}").json()
    for body in (listed, detail):
        if _on_files(data_path):
            assert body["file_id"] == a["item_id"]
        else:
            assert "file_id" not in body
