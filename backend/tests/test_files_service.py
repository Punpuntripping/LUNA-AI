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
# ``user_files``, its generated columns, the pointer embed and the 222 sync
# triggers. Every test that reads or writes the library runs on each path
# (my_files_phase2.md §F):
#
#   items             switches off, no user_files yet  — Phase 1, unchanged
#   items-after-222   switches off, files backfilled   — the off path after the migration
#   files-read        read switch on: the library is read from user_files
#   files-read-write  both on: «حذف من الملفات» is one user_files_hide statement

_MODES = ("items", "items-after-222", "files-read", "files-read-write")
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
        page_from: Optional[int] = None, page_to: Optional[int] = None, **owner: Any) -> dict:
    return {
        **owner,
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
        "created_at", "reattach_advised", "original_purged",
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
    furniture = att(filename="4.pdf")          # a fresh map, but nothing the viewer would show
    maps = [smap(fresh["item_id"], len(SAMPLE_TEXT)), smap(stale["item_id"], len(SAMPLE_TEXT) + 7),
            smap(furniture["item_id"], len(SAMPLE_TEXT))]
    segments = [
        seg(fresh["item_id"], 1, "boilerplate"), seg(fresh["item_id"], 2, "core"),
        seg(stale["item_id"], 1, "core"),
        seg(furniture["item_id"], 1, "boilerplate"), seg(furniture["item_id"], 2, "repeat"),
    ]
    sb = FakeSupabase(
        workspace_items=[fresh, stale, none, furniture], conversations=conversations(),
        attachment_segment_maps=maps, attachment_segments=segments,
    )
    out = _list(sb)
    flags = {f["item_id"]: f["has_sections"] for f in out["files"]}
    assert flags == {fresh["item_id"]: True, stale["item_id"]: False, none["item_id"]: False,
                     furniture["item_id"]: False}
    # the card and the opened file agree: the furniture-only file opens as plain text
    assert fs.get_library_file(sb, USER, furniture["item_id"])["sections"] is None
    # the list reads section VALUES only, never their text
    for table, cols in sb.selects:
        if table == "attachment_segments" and "seq" not in cols:
            assert "content_md" not in cols and "what" not in cols


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
        assert set(s) == {"seq", "what", "is_metadata", "page_from", "page_to", "document_index", "content_md",
                          "owner_class", "owner", "brought_by"}
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


# ── the user's edits on an opened file (attachment_agent.md §3.1 / §3.3) ──────────
# Both hang on the FILE: on every data path the key is the attachment's own id (the
# items path reads by item; the files paths read the backfilled file, file_id = item_id).

_TEXT_FIXES = "attachment_text_corrections"
_PROMOTIONS = "attachment_segment_overrides"


@pytest.fixture(autouse=True)
def _overlay_backoff():
    from shared.attachments import segment_overrides, text_corrections

    text_corrections.reset_backoff()
    segment_overrides.reset_backoff()
    yield
    text_corrections.reset_backoff()
    segment_overrides.reset_backoff()


@pytest.fixture
def agent_on(monkeypatch):
    """The attachment agent's flag on — the gate of both reads (only the agent
    writes corrections and promotions)."""
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", True)
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_USERS", None)


def _text_fix(file_id: str, wrong: str = "المؤجر", right: str = "المالك", *, user: str = USER) -> dict:
    return {"id": _id(), "file_id": file_id, "user_id": user, "wrong_text": wrong, "right_text": right,
            "occurrences": 1, "evidence": "صحح الكلمة", "created_at": "2026-11-27T10:00:00+00:00",
            "reverted_at": None}


def _promotion(file_id: str, start: int, end: int, *, user: str = USER) -> dict:
    return {"id": _id(), "file_id": file_id, "user_id": user, "char_start": start, "char_end": end,
            "value": "core", "created_at": "2026-11-27T10:00:00+00:00", "reverted_at": None}


def _ranged(item_id: str, seq: int, value: str, start: int, end: int, **kw: Any) -> dict:
    return {**seg(item_id, seq, value, **kw), "char_start": start, "char_end": end}


def _opened(*, fixes: list[dict] | None = None, promotions: list[dict] | None = None,
            segments: Optional[list] = None) -> tuple[FakeSupabase, str]:
    a = att()
    iid = a["item_id"]
    sb = FakeSupabase(
        workspace_items=[a], conversations=conversations(),
        attachment_segment_maps=[smap(iid, len(SAMPLE_TEXT))],
        attachment_segments=segments(iid) if segments else [
            _ranged(iid, 1, "metadata", 0, 9, text="بين المؤجر والمستأجر"),
            _ranged(iid, 2, "core", 9, 40, text="المؤجر يسلّم المحل"),
        ],
        **{_TEXT_FIXES: [dict(f, file_id=iid) for f in fixes or []],
           _PROMOTIONS: [dict(p, file_id=iid) for p in promotions or []]},
    )
    return sb, iid


def test_detail_text_fixes_reach_the_text_and_the_sections(agent_on):
    sb, iid = _opened(fixes=[_text_fix("-")])
    out = fs.get_library_file(sb, USER, iid)
    assert out["content_md"] == SAMPLE_TEXT.replace("المؤجر", "المالك")        # the viewer + Copy
    assert [s["content_md"] for s in out["sections"]] == ["بين المالك والمستأجر", "المالك يسلّم المحل"]
    # the stored text is untouched; the size and the map's freshness use its REAL length
    assert out["chars"] == len(SAMPLE_TEXT) and out["has_sections"] is True
    assert "المؤجر" in sb.row(iid)["content_md"]


def test_detail_without_text_fixes_or_when_their_read_fails_is_unchanged(agent_on):
    sb, iid = _opened()
    out = fs.get_library_file(sb, USER, iid)
    assert out["content_md"] == SAMPLE_TEXT
    assert [s["content_md"] for s in out["sections"]] == ["بين المؤجر والمستأجر", "المؤجر يسلّم المحل"]
    sb, iid = _opened(fixes=[_text_fix("-")])
    sb.fail_tables.add(_TEXT_FIXES)
    out = fs.get_library_file(sb, USER, iid)
    assert out["content_md"] == SAMPLE_TEXT and out["sections"][1]["content_md"] == "المؤجر يسلّم المحل"


def test_detail_another_users_fix_never_applies(agent_on):
    sb, iid = _opened(fixes=[_text_fix("-", user=OTHER_USER)])
    assert fs.get_library_file(sb, USER, iid)["content_md"] == SAMPLE_TEXT


def test_detail_promoted_sections_show_as_core(agent_on):
    """``_sections`` reads the segment rows itself (not ``load_segment_maps``), so it
    applies the promotions: a promoted letterhead / repeat is shown, a promoted
    metadata section is no longer «بيانات»; an unpromoted repeat stays hidden."""
    def segments(iid: str) -> list[dict]:
        return [
            _ranged(iid, 1, "boilerplate", 0, 10, text="شعار المحكمة"),
            _ranged(iid, 2, "metadata", 10, 20, text="رقم الصك"),
            _ranged(iid, 3, "core", 20, 40, text="الوقائع"),
            _ranged(iid, 4, "repeat", 40, 50, text="تذييل مكرر"),
            _ranged(iid, 5, "repeat", 50, 60, text="تذييل ثان"),
        ]

    sb, iid = _opened(segments=segments, promotions=[_promotion("-", 0, 20), _promotion("-", 40, 45)])
    out = fs.get_library_file(sb, USER, iid)
    assert [s["seq"] for s in out["sections"]] == [1, 2, 3, 4]
    assert [s["is_metadata"] for s in out["sections"]] == [False, False, False, False]
    for s in out["sections"]:      # the response shape is unchanged
        assert set(s) == {"seq", "what", "is_metadata", "page_from", "page_to", "document_index", "content_md",
                          "owner_class", "owner", "brought_by"}


def test_detail_a_promotion_opens_a_file_whose_sections_were_all_hidden(agent_on):
    def segments(iid: str) -> list[dict]:
        return [_ranged(iid, 1, "boilerplate", 0, 10), _ranged(iid, 2, "repeat", 10, 20)]

    sb, iid = _opened(segments=segments)
    assert fs.get_library_file(sb, USER, iid)["sections"] is None
    sb, iid = _opened(segments=segments, promotions=[_promotion("-", 12, 13)])
    out = fs.get_library_file(sb, USER, iid)
    assert [s["seq"] for s in out["sections"]] == [2] and out["has_sections"] is True


def test_detail_failed_promotions_read_keeps_the_stored_values(agent_on):
    def segments(iid: str) -> list[dict]:
        return [_ranged(iid, 1, "metadata", 0, 10), _ranged(iid, 2, "boilerplate", 10, 20),
                _ranged(iid, 3, "core", 20, 40)]

    sb, iid = _opened(segments=segments, promotions=[_promotion("-", 0, 20)])
    sb.fail_tables.add(_PROMOTIONS)
    out = fs.get_library_file(sb, USER, iid)
    assert [(s["seq"], s["is_metadata"]) for s in out["sections"]] == [(1, True), (3, False)]


def test_detail_flag_off_reads_no_fixes_and_no_promotions():
    """Rollback = the attachment agent's flag off: stored rows kept, nothing applied,
    and not one extra request."""
    def segments(iid: str) -> list[dict]:
        return [_ranged(iid, 1, "boilerplate", 0, 10, text="شعار المؤجر"), _ranged(iid, 2, "core", 10, 40)]

    sb, iid = _opened(segments=segments, fixes=[_text_fix("-")], promotions=[_promotion("-", 0, 10)])
    out = fs.get_library_file(sb, USER, iid)
    assert out["content_md"] == SAMPLE_TEXT and [s["seq"] for s in out["sections"]] == [2]
    assert [t for t, _c in sb.selects if t in (_TEXT_FIXES, _PROMOTIONS)] == []


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
            # its own code, so the web can hide the feature instead of showing "not found"
            assert res.json()["error"]["code"] == "FEATURE_OFF"
    assert sb.selects == [] and sb.updates == []


def test_status_route_is_always_200_and_reads_no_file(client_for):
    sb = FakeSupabase(workspace_items=[att()], conversations=conversations())
    for kwargs, want in (
        ({"enabled": False}, False),
        ({"enabled": True}, True),
        ({"enabled": True, "users": OTHER_USER}, False),
        ({"enabled": True, "users": f"{OTHER_USER},{USER}"}, True),
    ):
        res = client_for(sb, **kwargs).get("/api/v1/files/status")
        assert res.status_code == 200, res.text
        # `angle_maps` (angle_map_ui.md) and `pinned_texts`: off here — their
        # *_UI_* switches are not set.
        assert res.json() == {"enabled": want, "angle_maps": False, "pinned_texts": False}
        assert res.headers["Cache-Control"] == "private, no-store"
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


@pytest.mark.parametrize(
    "meta, want",
    [
        ({"ocr_pages": 30, "page_count": 55}, True),                        # the pre-budget 30-page cap
        ({"ocr_pages": 30, "page_count": 30}, False),                       # read in full
        ({"ocr_pages": 12, "page_count": 12}, False),
        ({"ocr_pages": 30}, False),                                         # length unknown: no claim
        ({"page_count": 55}, False),
        ({"ocr_pages": 10, "page_count": 55, "ocr_partial": True}, False),  # budget cut: has its own notice
        ({"ocr_pages": 30, "page_count": 55, "ocr_status": "failed"}, False),
        ({"ocr_pages": "30", "page_count": "303"}, True),                   # numbers stored as text
    ],
)
def test_reattach_advised_rule(meta, want):
    assert fs.reattach_advised({"ocr_status": "done", **meta}) is want


def test_reattach_advised_reaches_the_list_the_detail_and_the_conversation_item():
    from backend.app.api.workspace import _to_response

    cut = att(conv=CONV_A, filename="طويل.pdf", size=9000, ocr_pages=30, page_count=120)
    whole = att(conv=CONV_A, filename="قصير.pdf", size=800, ocr_pages=4, page_count=4)
    sb = FakeSupabase(workspace_items=[cut, whole], conversations=conversations())

    listed = {f["item_id"]: f for f in fs.list_library_files(sb, USER)["files"]}
    assert listed[cut["item_id"]]["reattach_advised"] is True
    assert listed[whole["item_id"]]["reattach_advised"] is False
    assert fs.get_library_file(sb, USER, cut["item_id"])["reattach_advised"] is True

    assert _to_response(cut).model_dump(mode="json")["reattach_advised"] is True
    assert "reattach_advised" not in _to_response(whole).model_dump(mode="json")   # absent, never False
    note = {**att(kind="note"), "metadata": {"ocr_status": "done", "ocr_pages": 30, "page_count": 99}}
    assert "reattach_advised" not in _to_response(note).model_dump(mode="json")    # attachments only

    # …and the cut copy is never offered as "you already have this file" (§3.8)
    assert fs.match_library_file(
        sb, USER, filename="طويل.pdf", size_bytes=9000, mime_type="application/pdf", conversation_id=CONV_A,
    ) is None


def test_sections_carry_the_owner_fields_and_hide_unknown():
    a = att()
    sb = FakeSupabase(
        workspace_items=[a], conversations=conversations(),
        attachment_segment_maps=[smap(a["item_id"], len(SAMPLE_TEXT))],
        attachment_segments=[
            seg(a["item_id"], 1, "core", what="طلبات المدعي", owner_class="claimant", owner=" وكيل  المدعي "),
            seg(a["item_id"], 2, "core", what="شهادة الشاهد", owner_class="witness", owner="الشاهد الأول",
                brought_by="المدعي"),
            seg(a["item_id"], 3, "core", what="نص بلا صاحب", owner_class="unknown", owner="", brought_by=""),
            seg(a["item_id"], 4, "core", what="خريطة قديمة"),                      # before seg-v5.2
        ],
    )
    s = fs.get_library_file(sb, USER, a["item_id"])["sections"]
    assert (s[0]["owner_class"], s[0]["owner"], s[0]["brought_by"]) == ("claimant", "وكيل المدعي", None)
    assert (s[1]["owner_class"], s[1]["owner"], s[1]["brought_by"]) == ("witness", "الشاهد الأول", "المدعي")
    for row in s[2:]:
        assert (row["owner_class"], row["owner"], row["brought_by"]) == (None, None, None)


def test_original_purged_is_true_only_after_the_retention_sweep():
    purged = att(filename="أ.pdf", storage_path=None, original_purged_at="2026-11-01T00:00:00+00:00")
    no_source = att(filename="ب.pdf", storage_path=None)            # never had a stored original
    whole = att(filename="ج.pdf")
    sb = FakeSupabase(workspace_items=[purged, no_source, whole], conversations=conversations())
    by_id = {f["item_id"]: f for f in fs.list_library_files(sb, USER)["files"]}
    assert (by_id[purged["item_id"]]["original_available"], by_id[purged["item_id"]]["original_purged"]) == (False, True)
    assert (by_id[no_source["item_id"]]["original_available"], by_id[no_source["item_id"]]["original_purged"]) == (False, False)
    assert (by_id[whole["item_id"]]["original_available"], by_id[whole["item_id"]]["original_purged"]) == (True, False)


def test_match_request_bounds():
    from pydantic import ValidationError

    from backend.app.models.requests import FileMatchRequest

    ok = FileMatchRequest(filename="عقد.pdf", size_bytes=250 * 1024 * 1024, mime_type="application/pdf",
                          conversation_id=CONV_A)
    assert ok.size_bytes == 250 * 1024 * 1024
    for bad in ({"size_bytes": 10**30}, {"conversation_id": "x" * 37}):
        with pytest.raises(ValidationError):
            FileMatchRequest(**{"filename": "عقد.pdf", "size_bytes": 1, "mime_type": "application/pdf", **bad})


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
        # Write switch off: the stamp lands on the item only, and the 222 mirror
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


# ===========================================================================
# Attach a library file to a conversation (Phase 3, migration 227)
# ===========================================================================


def _library(**tables: list[dict]) -> _UserFilesFake:
    """Files already in their own table (222 backfill), as on prod since 11-21."""
    return _UserFilesFake(backfill=True, **tables)


def test_attach_creates_a_copy_in_the_other_conversation_with_the_files_fields():
    a = att()
    sb = _library(workspace_items=[a], conversations=conversations())
    row = fs.attach_library_file(sb, USER, CONV_B, a["item_id"])
    assert row["item_id"] != a["item_id"] and row["file_id"] == a["item_id"]
    assert row["conversation_id"] == CONV_B and row["kind"] == "attachment"
    assert row["content_md"] == SAMPLE_TEXT and row["summary"] == SAMPLE_SUMMARY
    assert row["metadata"]["ocr_status"] == "done"            # extraction is never re-run
    assert sb.rpcs == [("user_file_attach", {"p_user_id": USER, "p_conversation_id": CONV_B,
                                             "p_file_id": a["item_id"]})]


def test_attach_accepts_a_second_copys_item_id_and_is_idempotent_per_conversation():
    a = att()
    sb = _library(workspace_items=[a], conversations=conversations())
    copy_row = fs.attach_library_file(sb, USER, CONV_B, a["item_id"])
    again = fs.attach_library_file(sb, USER, CONV_B, copy_row["item_id"])   # an item id, not the file id
    assert again["item_id"] == copy_row["item_id"]
    assert sb.rpcs[-1][1]["p_file_id"] == a["item_id"]                     # resolved to the file
    assert sum(1 for w in sb.tables["workspace_items"] if w.get("conversation_id") == CONV_B) == 1


@pytest.mark.parametrize("case", ["other_users_conversation", "deleted_conversation", "hidden", "no_text",
                                  "other_users_file", "unknown"])
def test_attach_refusals_are_a_404_and_write_nothing(case):
    a = att()
    theirs = att(user=OTHER_USER, conv=CONV_OTHER)
    hidden = att(library_hidden_at="2026-11-12T10:00:00+00:00")
    empty = att(text="")
    sb = _library(workspace_items=[a, theirs, hidden, empty], conversations=conversations())
    before = len(sb.tables["workspace_items"])
    conv, target = {
        "other_users_conversation": (CONV_OTHER, a["item_id"]),
        "deleted_conversation": (CONV_DELETED, a["item_id"]),
        "hidden": (CONV_B, hidden["item_id"]),
        "no_text": (CONV_B, empty["item_id"]),
        "other_users_file": (CONV_B, theirs["item_id"]),
        "unknown": (CONV_B, _id()),
    }[case]
    with pytest.raises(LunaHTTPException) as e:
        fs.attach_library_file(sb, USER, conv, target)
    assert e.value.status_code == 404 and e.value.detail == "الملف غير موجود"
    assert len(sb.tables["workspace_items"]) == before


def test_attach_before_migration_227_is_a_503_in_arabic():
    a = att()
    sb = _library(workspace_items=[a], conversations=conversations())
    sb.missing_rpcs.add("user_file_attach")
    with pytest.raises(LunaHTTPException) as e:
        fs.attach_library_file(sb, USER, CONV_B, a["item_id"])
    assert e.value.status_code == 503 and e.value.detail == "هذه الميزة غير متاحة حالياً"


def test_attach_unexpected_failure_is_a_500_in_arabic():
    a = att()
    sb = _library(workspace_items=[a], conversations=conversations())
    sb.fail_rpcs.add("user_file_attach")
    with pytest.raises(LunaHTTPException) as e:
        fs.attach_library_file(sb, USER, CONV_B, a["item_id"])
    assert e.value.status_code == 500 and e.value.detail == "حدث خطأ أثناء إرفاق الملف"


def test_attach_the_library_still_lists_one_card_for_a_file_in_two_conversations():
    a = att()
    sb = _library(workspace_items=[a], conversations=conversations())
    fs.attach_library_file(sb, USER, CONV_B, a["item_id"])
    with files_flags.override(read=True, write=True):
        out = fs.list_library_files(sb, USER)
    assert out["total"] == 1 and [f["file_id"] for f in out["files"]] == [a["item_id"]]


def test_attach_a_shared_file_stays_listed_when_its_first_conversation_is_deleted():
    a = att()
    convs = conversations()
    sb = _library(workspace_items=[a], conversations=convs)
    copy_row = fs.attach_library_file(sb, USER, CONV_B, a["item_id"])
    next(c for c in sb.tables["conversations"] if c["conversation_id"] == CONV_A)["deleted_at"] = "2026-11-20T09:00:00+00:00"
    with files_flags.override(read=True, write=True):
        out = fs.list_library_files(sb, USER)
    assert [f["file_id"] for f in out["files"]] == [a["item_id"]]
    assert out["files"][0]["item_id"] == copy_row["item_id"]     # the card opens the live copy
    assert out["files"][0]["conversation_id"] == CONV_B


# ===========================================================================
# 11. «النصوص المثبّتة» on the file (FILE_PINNED_TEXTS_UI_*)
# ===========================================================================

ART_77 = "a7777777-0000-0000-0000-000000000077"
ART_80 = "a8080808-0000-0000-0000-000000000080"


def _pin(article_id: str, at: int, number: str) -> SimpleNamespace:
    return SimpleNamespace(
        article_id=article_id, char_start=at, char_end=at + 9,
        title=f"المادة {number} من نظام العمل", text=f"النص الرسمي للمادة {number}.",
    )


@pytest.fixture
def pins_world(monkeypatch):
    """Turns the display switch on (with MY_FILES) and replaces ``load_pins`` with a
    recorder. Returns ``(set_pins, calls)``."""
    from agents.memory.citation_pins import store as pins_store

    state: dict = {"pins": [], "calls": []}

    def fake_load_pins(_sb, item_ids, *, user_id=None):
        ids = list(item_ids)
        state["calls"].append((ids, user_id))
        return {ids[0]: list(state["pins"])} if state["pins"] else {}

    monkeypatch.setattr(pins_store, "load_pins", fake_load_pins)

    def on(users: Optional[str] = None):
        monkeypatch.setattr(fs, "get_settings", lambda: SimpleNamespace(
            MY_FILES_ENABLED=True, MY_FILES_USERS=None,
            FILE_PINNED_TEXTS_UI_ENABLED=True, FILE_PINNED_TEXTS_UI_USERS=users,
        ))

    def set_pins(pins: list) -> None:
        state["pins"] = pins

    return on, set_pins, state["calls"]


def _positioned(item_id: str) -> list[dict]:
    rows = [
        seg(item_id, 1, "boilerplate", what="ترويسة"),
        seg(item_id, 2, "core", what="الوقائع"),
        seg(item_id, 3, "core", what="الأسباب"),
        seg(item_id, 4, "core", what="المنطوق"),
    ]
    for row, (start, end) in zip(rows, ((0, 10), (10, 30), (30, 60), (60, 80))):
        row["char_start"], row["char_end"] = start, end
    return rows


def test_detail_pinned_texts_hang_under_the_section_that_cites_them(pins_world, data_path):
    on, set_pins, calls = pins_world
    on()
    a = att()
    iid = a["item_id"]
    sb = FakeSupabase(
        workspace_items=[a], conversations=conversations(),
        attachment_segment_maps=[smap(iid, len(SAMPLE_TEXT))],
        attachment_segments=_positioned(iid),
    )
    set_pins([
        _pin(ART_80, 40, "80"),     # الأسباب
        _pin(ART_77, 12, "77"),     # الوقائع
        _pin(ART_77, 20, "77"),     # الوقائع again → listed once there
        _pin(ART_77, 35, "77"),     # الأسباب too → listed again under it
        _pin(ART_80, 5, "80"),      # inside the hidden letterhead → not shown
        _pin(ART_80, 500, "80"),    # past every section → not shown
    ])
    out = fs.get_library_file(sb, USER, iid)

    by_what = {s["what"]: s for s in out["sections"]}
    assert [p["article_id"] for p in by_what["الوقائع"]["pinned_texts"]] == [ART_77]
    assert [p["article_id"] for p in by_what["الأسباب"]["pinned_texts"]] == [ART_77, ART_80]   # document order
    assert "pinned_texts" not in by_what["المنطوق"]
    assert by_what["الوقائع"]["pinned_texts"][0] == {
        "article_id": ART_77, "title": "المادة 77 من نظام العمل", "text": "النص الرسمي للمادة 77.",
    }
    assert "pinned_texts" not in out                      # with sections they ride on the sections
    for s in out["sections"]:
        assert "_position" not in s
    # read once, under the file's key (the file id on the user_files path), as this user
    [(keys, user_id)] = calls
    assert keys == [str(out.get("file_id") or iid)] and user_id == USER

    # …and through the response model
    from backend.app.models.responses import LibraryFileDetail

    body = LibraryFileDetail.model_validate(out).model_dump(mode="json")
    assert [p["title"] for p in body["sections"][1]["pinned_texts"]] == ["المادة 77 من نظام العمل",
                                                                          "المادة 80 من نظام العمل"]
    assert body["sections"][2]["pinned_texts"] == [] and body["pinned_texts"] == []


def test_detail_pinned_texts_on_a_file_shown_as_full_text(pins_world, data_path):
    on, set_pins, _calls = pins_world
    on()
    a = att()
    sb = FakeSupabase(workspace_items=[a], conversations=conversations())     # no map
    set_pins([_pin(ART_80, 40, "80"), _pin(ART_77, 3, "77"), _pin(ART_80, 9, "80")])
    out = fs.get_library_file(sb, USER, a["item_id"])
    assert out["sections"] is None
    assert [p["article_id"] for p in out["pinned_texts"]] == [ART_77, ART_80]   # once each, document order


def test_detail_pinned_texts_off_reads_nothing_and_adds_nothing(pins_world, monkeypatch, data_path):
    on, set_pins, calls = pins_world
    on(users=OTHER_USER)                                   # on, but not for this user
    a = att()
    iid = a["item_id"]
    sb = FakeSupabase(
        workspace_items=[a], conversations=conversations(),
        attachment_segment_maps=[smap(iid, len(SAMPLE_TEXT))],
        attachment_segments=_positioned(iid),
    )
    set_pins([_pin(ART_77, 12, "77")])
    out = fs.get_library_file(sb, USER, iid)
    assert calls == []
    assert "pinned_texts" not in out
    for s in out["sections"]:
        assert "pinned_texts" not in s and "_position" not in s


def test_detail_pinned_texts_no_pins_leaves_the_file_as_it_was(pins_world, data_path):
    on, _set_pins, calls = pins_world
    on()
    a = att()
    iid = a["item_id"]
    sb = FakeSupabase(
        workspace_items=[a], conversations=conversations(),
        attachment_segment_maps=[smap(iid, len(SAMPLE_TEXT))],
        attachment_segments=_positioned(iid),
    )
    out = fs.get_library_file(sb, USER, iid)
    assert len(calls) == 1 and "pinned_texts" not in out
    assert all("pinned_texts" not in s for s in out["sections"])


def test_pinned_texts_flag_rule_and_env_names(monkeypatch):
    from shared.config import Settings

    def settings(enabled, users=None):
        return lambda: SimpleNamespace(FILE_PINNED_TEXTS_UI_ENABLED=enabled, FILE_PINNED_TEXTS_UI_USERS=users)

    monkeypatch.setattr(fs, "get_settings", settings(False))
    assert fs.file_pinned_texts_ui_enabled(USER) is False
    monkeypatch.setattr(fs, "get_settings", settings(True))
    assert fs.file_pinned_texts_ui_enabled(USER) is True
    monkeypatch.setattr(fs, "get_settings", settings(True, f" {OTHER_USER} , "))
    assert fs.file_pinned_texts_ui_enabled(USER) is False
    assert fs.file_pinned_texts_ui_enabled(OTHER_USER) is True
    for field in ("FILE_PINNED_TEXTS_UI_ENABLED", "FILE_PINNED_TEXTS_UI_USERS"):
        info = Settings.model_fields[field]
        assert info.validation_alias is None and info.alias is None


def test_status_route_pinned_texts_only_with_the_feature(client_for, monkeypatch):
    sb = FakeSupabase(workspace_items=[att()], conversations=conversations())
    for my_files, users, want in ((True, None, True), (True, OTHER_USER, False), (False, None, False)):
        c = client_for(sb, enabled=my_files)
        monkeypatch.setattr(fs, "get_settings", lambda my_files=my_files, users=users: SimpleNamespace(
            MY_FILES_ENABLED=my_files, MY_FILES_USERS=None,
            FILE_PINNED_TEXTS_UI_ENABLED=True, FILE_PINNED_TEXTS_UI_USERS=users,
        ))
        res = c.get("/api/v1/files/status")
        assert res.status_code == 200 and res.json()["pinned_texts"] is want
