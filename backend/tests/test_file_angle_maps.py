"""«الأوجه» — GET /api/v1/files/angle-maps[/{map_id}] + the status flag
(.claude/plans/angle_map_ui.md, rev 3).

No live DB: the shared in-memory PostgREST fake holds the map tables, the items,
the files and the conversations; the segmenter's header reader, the files
layer's length reader, the mapper's freshness check and the segment loader are
stubbed (they have their own suites) so these tests pin what THIS module adds —
which maps are listed (fresh, live, the caller's, not in a deleted conversation),
their light rows, the full map (labels, sections trimmed to those in use) and
the switches. All text is invented sample text.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from backend.app.errors import LunaHTTPException
from backend.app.services import angle_maps_service as ams
from backend.app.services import files_service as fs
from shared.files import flags as files_flags
from shared.files import store as files_store
from shared.files.tests.user_files_fake import FakeSupabase

USER = "11111111-1111-1111-1111-111111111111"
OTHER_USER = "22222222-2222-2222-2222-222222222222"
CONV_A = "aaaaaaaa-0000-0000-0000-000000000001"
CONV_B = "aaaaaaaa-0000-0000-0000-000000000002"
CONV_GONE = "aaaaaaaa-0000-0000-0000-000000000003"
ITEM_A1 = "bbbbbbbb-0000-0000-0000-0000000000a1"
ITEM_A2 = "bbbbbbbb-0000-0000-0000-0000000000a2"
ITEM_B1 = "bbbbbbbb-0000-0000-0000-0000000000b1"
ITEM_STALE = "bbbbbbbb-0000-0000-0000-0000000000c1"
ITEM_DELETED = "bbbbbbbb-0000-0000-0000-0000000000d1"
ITEM_GONE_CONV = "bbbbbbbb-0000-0000-0000-0000000000e1"
ITEM_THEIRS = "bbbbbbbb-0000-0000-0000-0000000000ff"
MAP_A = "cccccccc-0000-0000-0000-00000000000a"
MAP_B = "cccccccc-0000-0000-0000-00000000000b"
MAP_STALE = "cccccccc-0000-0000-0000-00000000000c"
MAP_DELETED = "cccccccc-0000-0000-0000-00000000000d"
MAP_GONE_CONV = "cccccccc-0000-0000-0000-00000000000e"
MAP_THEIRS = "cccccccc-0000-0000-0000-0000000000ff"
SEGMENTED_AT = "2026-11-20T10:00:00+00:00"


def _item(item_id: str, conv: str, *, user: str = USER, title: str = "لائحة دعوى", wi: int = 1,
          deleted: Optional[str] = None) -> dict:
    return {
        "item_id": item_id, "user_id": user, "conversation_id": conv, "wi_seq": wi, "kind": "attachment",
        "title": title, "content_md": "نص تجريبي", "summary": None,
        "metadata": {"doc_type": "claim", "filename": f"{title}.pdf"},
        "storage_path": None, "document_id": None, "created_at": "2026-11-10T10:00:00+00:00",
        "updated_at": "2026-11-10T10:00:00+00:00", "deleted_at": deleted, "file_id": None,
    }


def _member(map_id: str, item_id: str, n: int, *, segmented_at: str = SEGMENTED_AT) -> dict:
    return {"map_id": map_id, "item_id": item_id, "doc_index": n, "content_chars": 1000,
            "segment_prompt_version": "seg-v5.2", "paragraph_rule_version": "v2-sentence-500j-1200",
            "segmented_at": segmented_at}


def _world() -> FakeSupabase:
    items = [
        _item(ITEM_A1, CONV_A), _item(ITEM_A2, CONV_A, title="مذكرة رد", wi=2),
        _item(ITEM_B1, CONV_B, title="عقد توريد"),
        _item(ITEM_STALE, CONV_B, title="ملف أعيد تقسيمه"),
        _item(ITEM_DELETED, CONV_B, title="ملف محذوف", deleted="2026-11-21T00:00:00+00:00"),
        _item(ITEM_GONE_CONV, CONV_GONE, title="في محادثة محذوفة"),
        _item(ITEM_THEIRS, CONV_A, user=OTHER_USER, title="ليس لي"),
    ]
    convs = [
        {"conversation_id": CONV_A, "user_id": USER, "title_ar": "استئناف حكم", "deleted_at": None},
        {"conversation_id": CONV_B, "user_id": USER, "title_ar": "عقد توريد", "deleted_at": None},
        {"conversation_id": CONV_GONE, "user_id": USER, "title_ar": "قديمة", "deleted_at": "2026-11-22T00:00:00+00:00"},
    ]
    maps = [
        {"map_id": MAP_A, "user_id": USER, "conversation_id": CONV_A, "message_id": None, "created_at": "2026-11-23T10:00:00+00:00"},
        {"map_id": MAP_B, "user_id": USER, "conversation_id": CONV_B, "message_id": None, "created_at": "2026-11-24T10:00:00+00:00"},
        {"map_id": MAP_STALE, "user_id": USER, "conversation_id": CONV_B, "message_id": None, "created_at": "2026-11-22T10:00:00+00:00"},
        {"map_id": MAP_DELETED, "user_id": USER, "conversation_id": CONV_B, "message_id": None, "created_at": "2026-11-21T10:00:00+00:00"},
        {"map_id": MAP_GONE_CONV, "user_id": USER, "conversation_id": CONV_GONE, "message_id": None, "created_at": "2026-11-20T10:00:00+00:00"},
        {"map_id": MAP_THEIRS, "user_id": OTHER_USER, "conversation_id": CONV_A, "message_id": None, "created_at": "2026-11-25T10:00:00+00:00"},
    ]
    members = [
        _member(MAP_A, ITEM_A1, 1), _member(MAP_A, ITEM_A2, 2),
        _member(MAP_B, ITEM_B1, 1),
        _member(MAP_STALE, ITEM_STALE, 1, segmented_at="2026-11-19T10:00:00+00:00"),
        _member(MAP_DELETED, ITEM_DELETED, 1),
        _member(MAP_GONE_CONV, ITEM_GONE_CONV, 1),
        _member(MAP_THEIRS, ITEM_THEIRS, 1),
    ]
    angles = [
        {"map_id": MAP_A, "seq": 1, "kind": "strengthen"}, {"map_id": MAP_A, "seq": 2, "kind": "answer"},
        {"map_id": MAP_A, "seq": 3, "kind": "answer"}, {"map_id": MAP_B, "seq": 1, "kind": "attack"},
    ]
    return FakeSupabase(
        workspace_items=items, conversations=convs, attachment_angle_maps=maps,
        attachment_angle_map_items=members, attachment_angles=angles,
    )


def _header(item_id: str) -> dict:
    return {"item_id": item_id, "prompt_version": "seg-v5.2", "paragraph_rule_version": "v2-sentence-500j-1200",
            "content_chars": 1000, "created_at": SEGMENTED_AT}


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    files_flags.reset_probe()
    with files_flags.override(read=False, write=False):
        monkeypatch.setattr(ams, "segment_headers",
                            lambda _sb, ids, user_id=None: {i: _header(i) for i in ids})
        monkeypatch.setattr(files_store, "content_chars_for_items", lambda _sb, ids, user_id=None: {})
        yield
    files_flags.reset_probe()


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------

def _settings(**kw):
    base = {"MY_FILES_ENABLED": True, "MY_FILES_USERS": None, "ANGLE_MAP_UI_ENABLED": False, "ANGLE_MAP_UI_USERS": None}
    base.update(kw)
    return lambda: SimpleNamespace(**base)


def test_ui_flag_rule(monkeypatch):
    monkeypatch.setattr(ams, "get_settings", _settings())
    assert ams.angle_maps_ui_enabled(USER) is False
    monkeypatch.setattr(ams, "get_settings", _settings(ANGLE_MAP_UI_ENABLED=True))
    assert ams.angle_maps_ui_enabled(USER) is True
    monkeypatch.setattr(ams, "get_settings", _settings(ANGLE_MAP_UI_ENABLED=True, ANGLE_MAP_UI_USERS=f" {OTHER_USER} ,"))
    assert ams.angle_maps_ui_enabled(USER) is False and ams.angle_maps_ui_enabled(OTHER_USER) is True


def test_ui_flag_env_names_equal_field_names(monkeypatch):
    from shared.config import Settings

    for field in ("ANGLE_MAP_UI_ENABLED", "ANGLE_MAP_UI_USERS"):
        info = Settings.model_fields[field]
        assert info.validation_alias is None and info.alias is None
    monkeypatch.setenv("SUPABASE_URL", "http://localhost:54321")
    for name in ("SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_JWT_SECRET"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("ANGLE_MAP_UI_ENABLED", "true")
    monkeypatch.setenv("ANGLE_MAP_UI_USERS", USER)
    s = Settings(_env_file=None)
    assert s.ANGLE_MAP_UI_ENABLED is True and s.ANGLE_MAP_UI_USERS == USER


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------

def test_list_is_the_callers_fresh_live_maps_newest_first():
    maps = ams.list_angle_maps(_world(), USER)
    # STALE (re-segmented), DELETED (a member gone), GONE_CONV (deleted conversation)
    # and another user's map are not listed.
    assert [m["map_id"] for m in maps] == [MAP_B, MAP_A]
    a = maps[1]
    assert a["conversation_title"] == "استئناف حكم"
    assert [(d["index"], d["title"], d["wi_seq"], d["doc_type"]) for d in a["documents"]] == [
        (1, "لائحة دعوى", 1, "claim"), (2, "مذكرة رد", 2, "claim"),
    ]
    assert a["angle_count"] == 3 and a["kinds"] == {"strengthen": 1, "attack": 0, "answer": 2}
    assert maps[0]["kinds"] == {"strengthen": 0, "attack": 1, "answer": 0}


def test_list_of_one_conversation():
    assert [m["map_id"] for m in ams.list_angle_maps(_world(), USER, CONV_A)] == [MAP_A]
    assert ams.list_angle_maps(_world(), USER, CONV_GONE) == []


CONV_NEW = "aaaaaaaa-0000-0000-0000-000000000009"
COPY_A1 = "bbbbbbbb-0000-0000-0000-0000000001a1"
COPY_GONE = "bbbbbbbb-0000-0000-0000-0000000001e1"


def _with_copies(monkeypatch) -> FakeSupabase:
    """«الملفات» copies in CONV_NEW: of ITEM_A1's file (MAP_A, with ITEM_A2 — another
    file) and of ITEM_GONE_CONV's file (MAP_GONE_CONV, its conversation deleted).
    ``file_id`` selected as with the files read switch on."""
    from agents.utils import angle_map_flags

    monkeypatch.setattr(files_store, "with_file_id", lambda cols, **_k: f"{cols}, file_id")
    monkeypatch.setattr(angle_map_flags, "library_file_maps_enabled", lambda _user: True)
    sb = _world()
    rows = {r["item_id"]: r for r in sb.tables["workspace_items"]}
    rows[ITEM_A1]["file_id"] = "file-a1"
    rows[ITEM_A2]["file_id"] = "file-a2"
    rows[ITEM_GONE_CONV]["file_id"] = "file-gone"
    copy_a1 = _item(COPY_A1, CONV_NEW, title="لائحة دعوى")
    copy_a1["file_id"] = "file-a1"
    copy_gone = _item(COPY_GONE, CONV_NEW, title="في محادثة محذوفة", wi=2)
    copy_gone["file_id"] = "file-gone"
    sb.tables["workspace_items"] += [copy_a1, copy_gone]
    sb.tables["conversations"].append(
        {"conversation_id": CONV_NEW, "user_id": USER, "title_ar": "محادثة جديدة", "deleted_at": None})
    return sb


def test_list_of_a_conversation_shows_the_maps_its_library_copies_carry(monkeypatch):
    """Owner 2026-11-27: a «الملفات» copy brings its file's map whole — even with
    other files, even when the conversation it was made in is deleted."""
    maps = ams.list_angle_maps(_with_copies(monkeypatch), USER, CONV_NEW)
    assert [m["map_id"] for m in maps] == [MAP_A, MAP_GONE_CONV]
    assert [d["item_id"] for d in maps[0]["documents"]] == [ITEM_A1, ITEM_A2]
    assert maps[0]["conversation_title"] == "استئناف حكم"


def test_switch_off_lists_no_carried_map(monkeypatch):
    from agents.utils import angle_map_flags

    sb = _with_copies(monkeypatch)
    monkeypatch.setattr(angle_map_flags, "library_file_maps_enabled", lambda _user: False)
    assert ams.list_angle_maps(sb, USER, CONV_NEW) == []


def test_carried_maps_change_nothing_elsewhere(monkeypatch):
    sb = _with_copies(monkeypatch)
    assert [m["map_id"] for m in ams.list_angle_maps(sb, USER, CONV_A)] == [MAP_A]
    assert [m["map_id"] for m in ams.list_angle_maps(sb, USER)] == [MAP_B, MAP_A]
    assert ams.list_angle_maps(sb, OTHER_USER, CONV_NEW) == []


def test_a_stale_carried_map_is_not_listed_and_a_deleted_original_item_carries_nothing(monkeypatch):
    sb = _with_copies(monkeypatch)
    monkeypatch.setattr(ams, "segment_headers",
                        lambda _sb, ids, user_id=None: {i: _header(i) for i in ids if i != ITEM_A2})
    assert [m["map_id"] for m in ams.list_angle_maps(sb, USER, CONV_NEW)] == [MAP_GONE_CONV]
    for r in sb.tables["workspace_items"]:
        if r["item_id"] == ITEM_GONE_CONV:
            r["deleted_at"] = "2026-11-26T00:00:00+00:00"
    assert ams.list_angle_maps(sb, USER, CONV_NEW) == []


def test_the_newest_fresh_carried_map_per_file(monkeypatch):
    sb = _with_copies(monkeypatch)
    # file-a1 was also sent alone in CONV_B later → a newer map of it alone.
    later = _item("bbbbbbbb-0000-0000-0000-0000000002a1", CONV_B, title="لائحة دعوى", wi=3)
    later["file_id"] = "file-a1"
    sb.tables["workspace_items"].append(later)
    sb.tables["attachment_angle_maps"].append({
        "map_id": "cccccccc-0000-0000-0000-0000000000aa", "user_id": USER, "conversation_id": CONV_B,
        "message_id": None, "created_at": "2026-11-26T10:00:00+00:00"})
    sb.tables["attachment_angle_map_items"].append(
        _member("cccccccc-0000-0000-0000-0000000000aa", later["item_id"], 1))
    maps = ams.list_angle_maps(sb, USER, CONV_NEW)
    assert [m["map_id"] for m in maps] == ["cccccccc-0000-0000-0000-0000000000aa", MAP_GONE_CONV]


def test_list_a_member_with_no_segment_map_now_is_stale(monkeypatch):
    monkeypatch.setattr(ams, "segment_headers",
                        lambda _sb, ids, user_id=None: {i: _header(i) for i in ids if i != ITEM_A2})
    assert [m["map_id"] for m in ams.list_angle_maps(_world(), USER)] == [MAP_B]


def test_list_a_changed_text_length_is_stale(monkeypatch):
    monkeypatch.setattr(files_store, "content_chars_for_items", lambda _sb, ids, user_id=None: {ITEM_B1: 999})
    assert [m["map_id"] for m in ams.list_angle_maps(_world(), USER)] == [MAP_A]


def test_list_failed_read_is_a_500():
    sb = _world()
    sb.fail_tables.add("attachment_angle_map_items")
    with pytest.raises(LunaHTTPException) as e:
        ams.list_angle_maps(sb, USER)
    assert e.value.status_code == 500


# ---------------------------------------------------------------------------
# One map
# ---------------------------------------------------------------------------

def _seg(seq: int, owner_class: str, what: str, text: str) -> dict:
    return {
        "seq": seq, "from_p": seq, "to_p": seq, "page_from": seq, "page_to": seq, "char_start": 0,
        "char_end": 1, "label": "x", "value": "core", "what": what, "content_md": text,
        "owner_class": owner_class, "owner": {"claimant": "المدعي", "respondent": "المدعى عليه"}.get(owner_class),
        "brought_by": None,
    }


SEGMENTS = {
    ITEM_A1: {"segments": [_seg(1, "claimant", "طلبات المدعي", "نص الطلبات"),
                           _seg(2, "court", "منطوق الحكم", "نص المنطوق"),
                           _seg(3, "claimant", "قسم لا يستند إليه وجه", "لا يُرسل")]},
    ITEM_A2: {"segments": [_seg(1, "respondent", "دفع بعدم القبول", "نص الدفع")]},
}


def _stored(user: str = USER) -> dict:
    return {
        "map": {
            "map_id": MAP_A, "user_id": user, "conversation_id": CONV_A, "message_id": None,
            "prompt_version": "angles-v5", "created_at": "2026-11-23T10:00:00+00:00",
            "client": {"side": "claimant", "party": "المدعي", "basis": "«أنا المدعي»", "confidence": "clear",
                       "by_document": [{"document": "D2", "side": "claimant", "party": "المدعي"}]},
            "gaps": [{"kind": "contradiction", "sections": ["D1.S2", "D9.S9"], "issue": "تاريخان مختلفان",
                      "ocr_suspect": True}],
            "left_out": [{"item_id": ITEM_B1, "reason": "skipped_no_map"}],
        },
        "items": [{"map_id": MAP_A, "item_id": ITEM_A1, "doc_index": 1}, {"map_id": MAP_A, "item_id": ITEM_A2, "doc_index": 2}],
        "angles": [
            {"seq": 1, "kind": "strengthen", "strength": "strong", "for_side": "claimant", "for_party": "المدعي",
             "title": "وجه أول", "argument": "حجة", "legal_question": "سؤال؟", "weakness": "",
             "sections": ["D1.S1", "D2.S1", "D1.S1", "D7.S1"], "ocr_suspect": False},
            {"seq": 2, "kind": "bogus", "strength": "??", "for_side": "", "for_party": "",
             "title": "وجه ثان", "argument": "", "legal_question": "", "weakness": "يضعفه",
             "sections": ["D1.S2"], "ocr_suspect": True},
        ],
    }


@pytest.fixture
def one_map(monkeypatch):
    state = {"stored": _stored(), "asked": []}

    def fresh(_sb, item_id):
        state["asked"].append(item_id)
        return copy.deepcopy(state["stored"])

    monkeypatch.setattr(ams, "load_fresh_angle_map_for_item", fresh)
    monkeypatch.setattr(ams, "load_segment_maps",
                        lambda _sb, ids, user_id=None: {i: SEGMENTS[i] for i in ids if i in SEGMENTS})
    return state


def test_get_one_map_labels_and_only_the_sections_in_use(one_map):
    m = ams.get_angle_map(_world(), USER, MAP_A)
    assert one_map["asked"] == [ITEM_A1]    # freshness through its first member
    assert m["map_id"] == MAP_A and m["conversation_title"] == "استئناف حكم"
    assert [d["title"] for d in m["documents"]] == ["لائحة دعوى", "مذكرة رد"]
    a1, a2 = m["angles"]
    assert [(s["ref"], s["side"]) for s in a1["sections"]] == [("D1.S1", "own"), ("D2.S1", "opp")]
    assert (a2["kind"], a2["strength"], a2["ocr_suspect"]) == ("strengthen", "medium", True)
    assert a2["sections"] == [{"ref": "D1.S2", "side": "neutral"}]
    assert m["gaps"][0]["sections"] == [{"ref": "D1.S2", "side": "neutral"}]
    assert [s["ref"] for s in m["sections"]] == ["D1.S1", "D2.S1", "D1.S2"]
    assert all("لا يُرسل" not in s["content_md"] for s in m["sections"])
    assert m["client"]["by_document"] == [{"document": 2, "side": "claimant", "party": "المدعي"}]
    assert m["left_out"] == [{"item_id": ITEM_B1, "title": "عقد توريد", "reason": "skipped_no_map"}]


@pytest.mark.parametrize("mode", ["fixed", "none", "down", "flag-off"])
def test_get_one_map_section_text_carries_the_users_text_fixes(one_map, mode, monkeypatch):
    """The AngleSectionDialog text (attachment_agent.md §3.3): a fix of a document's
    file applies to that document's sections only; none / a failed read / the
    attachment agent off for the user (no request at all) = stored."""
    from shared.attachments import text_corrections as tc
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", mode != "flag-off")
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_USERS", None)
    tc.reset_backoff()
    sb = _world()
    sb.tables["attachment_text_corrections"] = [] if mode == "none" else [{
        "id": "c1", "file_id": ITEM_A1, "user_id": USER, "wrong_text": "الطلبات", "right_text": "المطالبات",
        "occurrences": 1, "evidence": "صحح الكلمة", "created_at": "2026-11-27T10:00:00+00:00",
        "reverted_at": None,
    }]
    if mode == "down":
        sb.fail_tables.add("attachment_text_corrections")
    try:
        m = ams.get_angle_map(sb, USER, MAP_A)
    finally:
        tc.reset_backoff()
    texts = {s["ref"]: s["content_md"] for s in m["sections"]}
    assert texts["D1.S1"] == ("نص المطالبات" if mode == "fixed" else "نص الطلبات")
    if mode == "flag-off":
        assert [t for t, _c in sb.selects if t == "attachment_text_corrections"] == []
        assert [c for t, c in sb.selects if t == "workspace_items" and c == "item_id, file_id"] == []
    assert (texts["D2.S1"], texts["D1.S2"]) == ("نص الدفع", "نص المنطوق")
    assert SEGMENTS[ITEM_A1]["segments"][0]["content_md"] == "نص الطلبات"     # the stored rows untouched


def test_get_is_none_for_another_users_map_a_stale_one_or_an_unknown_id(one_map):
    sb = _world()
    one_map["stored"] = _stored(user=OTHER_USER)
    assert ams.get_angle_map(sb, USER, MAP_A) is None
    one_map["stored"] = None                       # no longer fresh
    assert ams.get_angle_map(sb, USER, MAP_A) is None
    assert ams.get_angle_map(sb, USER, "dddddddd-0000-0000-0000-000000000000") is None


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------

@pytest.fixture
def client_for(monkeypatch):
    def make(sb: FakeSupabase, **settings: Any):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.app.api import files as files_api
        from backend.app.deps import get_current_user, get_supabase
        from backend.app.errors import luna_exception_handler
        from shared.auth.jwt import AuthUser

        monkeypatch.setattr(files_api, "get_user_id", lambda _sb, _auth: USER)
        monkeypatch.setattr(fs, "get_settings", _settings(**settings))
        monkeypatch.setattr(ams, "get_settings", _settings(**settings))
        app = FastAPI()
        app.include_router(files_api.router, prefix="/api/v1")
        app.add_exception_handler(LunaHTTPException, luna_exception_handler)
        app.dependency_overrides[get_current_user] = lambda: AuthUser(
            auth_id="auth-1", email="a@b.com", role="authenticated"
        )
        app.dependency_overrides[get_supabase] = lambda: sb
        return TestClient(app)

    return make


def test_status_reports_angle_maps_only_with_both_switches(client_for):
    sb = _world()
    for settings, want in (
        ({}, {"enabled": True, "angle_maps": False}),
        ({"ANGLE_MAP_UI_ENABLED": True}, {"enabled": True, "angle_maps": True}),
        ({"ANGLE_MAP_UI_ENABLED": True, "ANGLE_MAP_UI_USERS": OTHER_USER}, {"enabled": True, "angle_maps": False}),
        ({"ANGLE_MAP_UI_ENABLED": True, "MY_FILES_ENABLED": False}, {"enabled": False, "angle_maps": False}),
    ):
        res = client_for(sb, **settings).get("/api/v1/files/status")
        assert res.status_code == 200 and res.json() == want


def test_routes_are_feature_off_without_the_ui_switch(client_for):
    c = client_for(_world())
    for path in ("/api/v1/files/angle-maps", f"/api/v1/files/angle-maps/{MAP_A}"):
        res = c.get(path)
        assert res.status_code == 404 and res.json()["error"]["code"] == "FEATURE_OFF"


def test_list_route_and_its_conversation_filter(client_for):
    c = client_for(_world(), ANGLE_MAP_UI_ENABLED=True)
    res = c.get("/api/v1/files/angle-maps")
    # The literal path wins over /files/{item_id}.
    assert res.status_code == 200 and res.headers["Cache-Control"] == "private, no-store"
    assert [m["map_id"] for m in res.json()["maps"]] == [MAP_B, MAP_A]
    res = c.get(f"/api/v1/files/angle-maps?conversation_id={CONV_A}")
    assert [m["map_id"] for m in res.json()["maps"]] == [MAP_A]
    assert c.get("/api/v1/files/angle-maps?conversation_id=nope").status_code == 404   # validate_uuid


def test_one_map_route(client_for, one_map):
    c = client_for(_world(), ANGLE_MAP_UI_ENABLED=True)
    res = c.get(f"/api/v1/files/angle-maps/{MAP_A}")
    assert res.status_code == 200 and res.json()["angles"][0]["sections"][1] == {"ref": "D2.S1", "side": "opp"}
    one_map["stored"] = None
    res = c.get(f"/api/v1/files/angle-maps/{MAP_A}")
    assert res.status_code == 404 and res.json()["detail"] == "الخيوط غير موجودة أو لم تعد محدّثة"
    assert c.get("/api/v1/files/angle-maps/not-a-uuid").status_code == 404
