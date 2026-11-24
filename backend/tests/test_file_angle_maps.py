"""«الأوجه» — GET /api/v1/files/{item_id}/angle-maps + the status flag
(.claude/plans/angle_map_ui.md §4).

No live DB: the shared in-memory PostgREST fake for the item / file / conversation
reads; the mapper's store and the segmenter's loader are replaced by stubs (they
have their own suites) so these tests pin what THIS module adds — which items a
file resolves to, which maps are shown, the labels, the trimming to the sections
in use, ownership and the switches. All text is invented sample text.
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
from shared.files.tests.user_files_fake import FakeSupabase

USER = "11111111-1111-1111-1111-111111111111"
OTHER_USER = "22222222-2222-2222-2222-222222222222"
CONV_A = "aaaaaaaa-0000-0000-0000-000000000001"
CONV_B = "aaaaaaaa-0000-0000-0000-000000000002"
FILE = "ffffffff-0000-0000-0000-0000000000f1"
ITEM_A = "bbbbbbbb-0000-0000-0000-00000000000a"   # the file in conversation A (D1 there)
ITEM_A2 = "bbbbbbbb-0000-0000-0000-0000000000a2"  # another file sent with it (D2)
ITEM_B = "bbbbbbbb-0000-0000-0000-00000000000b"   # the same file in conversation B
ITEM_THEIRS = "bbbbbbbb-0000-0000-0000-0000000000ff"
MAP_A = "cccccccc-0000-0000-0000-00000000000a"
MAP_B = "cccccccc-0000-0000-0000-00000000000b"


def _item(item_id: str, conv: str, *, user: str = USER, title: str = "لائحة دعوى", wi: int = 1,
          file_id: Optional[str] = None, created: str = "2026-11-10T10:00:00+00:00") -> dict:
    return {
        "item_id": item_id, "user_id": user, "conversation_id": conv, "wi_seq": wi, "kind": "attachment",
        "title": title, "content_md": "نص تجريبي", "summary": None,
        "metadata": {"doc_type": "claim", "filename": f"{title}.pdf"},
        "storage_path": None, "document_id": None, "created_at": created, "updated_at": created,
        "deleted_at": None, "file_id": file_id,
    }


def _world(*, read: bool) -> FakeSupabase:
    items = [
        _item(ITEM_A, CONV_A, file_id=FILE if read else None),
        _item(ITEM_A2, CONV_A, title="مذكرة رد", wi=2, file_id=ITEM_A2 if read else None),
        _item(ITEM_B, CONV_B, wi=4, file_id=FILE if read else None, created="2026-11-12T10:00:00+00:00"),
        _item(ITEM_THEIRS, CONV_A, user=OTHER_USER, file_id=ITEM_THEIRS if read else None),
    ]
    convs = [
        {"conversation_id": CONV_A, "user_id": USER, "title_ar": "استئناف حكم", "deleted_at": None},
        {"conversation_id": CONV_B, "user_id": USER, "title_ar": "سؤال لاحق", "deleted_at": None},
    ]
    sb = FakeSupabase(workspace_items=items, conversations=convs)
    if read:
        for wi in sb.tables["workspace_items"]:
            sb.tables["user_files"].append({
                "file_id": wi["file_id"], "user_id": wi["user_id"], "title": wi["title"],
                "content_md": wi["content_md"], "summary": None, "metadata": copy.deepcopy(wi["metadata"]),
                "storage_path": None, "document_id": None, "origin": "chat", "library_hidden_at": None,
                "created_at": wi["created_at"], "updated_at": wi["created_at"], "deleted_at": None,
            }) if not any(f["file_id"] == wi["file_id"] for f in sb.tables["user_files"]) else None
    return sb


def _seg(seq: int, owner_class: str, what: str, text: str) -> dict:
    return {
        "seq": seq, "from_p": seq, "to_p": seq, "page_from": seq, "page_to": seq, "char_start": 0,
        "char_end": 1, "label": "x", "value": "core", "what": what, "content_md": text,
        "owner_class": owner_class, "owner": {"claimant": "المدعي", "respondent": "المدعى عليه"}.get(owner_class),
        "brought_by": None,
    }


SEGMENTS = {
    ITEM_A: {"segments": [_seg(1, "claimant", "طلبات المدعي", "نص الطلبات"),
                          _seg(2, "court", "منطوق الحكم", "نص المنطوق"),
                          _seg(3, "claimant", "قسم لا يستند إليه وجه", "لا يُرسل")]},
    ITEM_A2: {"segments": [_seg(1, "respondent", "دفع بعدم القبول", "نص الدفع")]},
    ITEM_B: {"segments": [_seg(1, "claimant", "طلبات المدعي", "نص الطلبات"),
                          _seg(2, "court", "منطوق الحكم", "نص المنطوق")]},
}


def _stored(map_id: str, conv: str, members: list[str], created: str, user: str = USER) -> dict:
    return {
        "map": {
            "map_id": map_id, "user_id": user, "conversation_id": conv, "message_id": None,
            "prompt_version": "angles-v5", "created_at": created,
            "client": {"side": "claimant", "party": "المدعي", "basis": "«أنا المدعي»", "confidence": "clear",
                       "by_document": [{"document": "D2", "side": "claimant", "party": "المدعي"}]},
            "gaps": [{"kind": "contradiction", "sections": ["D1.S2", "D9.S9"], "issue": "تاريخان مختلفان",
                      "ocr_suspect": True}],
            "left_out": [{"item_id": ITEM_B, "reason": "skipped_no_map"}] if map_id == MAP_A else [],
        },
        "items": [{"map_id": map_id, "item_id": i, "doc_index": n} for n, i in enumerate(members, start=1)],
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
def stubs(monkeypatch):
    maps: dict[str, Any] = {
        ITEM_A: _stored(MAP_A, CONV_A, [ITEM_A, ITEM_A2], "2026-11-10T11:00:00+00:00"),
        ITEM_A2: _stored(MAP_A, CONV_A, [ITEM_A, ITEM_A2], "2026-11-10T11:00:00+00:00"),
        ITEM_B: _stored(MAP_B, CONV_B, [ITEM_B], "2026-11-12T11:00:00+00:00"),
    }
    calls: list[str] = []

    def fresh(_sb, item_id):
        calls.append(item_id)
        return copy.deepcopy(maps.get(item_id))

    monkeypatch.setattr(ams, "load_fresh_angle_map_for_item", fresh)
    monkeypatch.setattr(
        ams, "load_segment_maps",
        lambda _sb, ids, user_id=None: {i: SEGMENTS[i] for i in ids if i in SEGMENTS},
    )
    return SimpleNamespace(maps=maps, calls=calls)


@pytest.fixture(params=["items", "files-read"])
def path(request):
    files_flags.reset_probe()
    with files_flags.override(read=request.param == "files-read", write=False):
        yield request.param
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
# The service
# ---------------------------------------------------------------------------

def test_one_map_labels_and_only_the_sections_in_use(path, stubs):
    sb = _world(read=path == "files-read")
    maps = ams.get_file_angle_maps(sb, USER, ITEM_A2)
    [m] = maps   # ITEM_A2 is in one conversation only
    assert m["map_id"] == MAP_A and m["conversation_title"] == "استئناف حكم"
    assert [d["index"] for d in m["documents"]] == [1, 2]
    assert [d["title"] for d in m["documents"]] == ["لائحة دعوى", "مذكرة رد"]
    assert m["documents"][0]["doc_type"] == "claim" and m["documents"][1]["wi_seq"] == 2
    # Angle 1: repeats and unknown refs dropped; labels from the client's side.
    a1 = m["angles"][0]
    assert [(s["ref"], s["side"]) for s in a1["sections"]] == [("D1.S1", "own"), ("D2.S1", "opp")]
    # Angle 2: a bad kind / strength fall back; no side named → the client's side.
    a2 = m["angles"][1]
    assert (a2["kind"], a2["strength"], a2["ocr_suspect"]) == ("strengthen", "medium", True)
    assert a2["sections"] == [{"ref": "D1.S2", "side": "neutral"}]
    # Gap: unknown ref dropped.
    assert m["gaps"] == [{"kind": "contradiction", "issue": "تاريخان مختلفان", "ocr_suspect": True,
                          "sections": [{"ref": "D1.S2", "side": "neutral"}]}]
    # Only the sections an angle or a gap rests on, with their text; D1.S3 is not sent.
    assert [s["ref"] for s in m["sections"]] == ["D1.S1", "D2.S1", "D1.S2"]
    assert m["sections"][0]["content_md"] == "نص الطلبات" and m["sections"][0]["owner"] == "المدعي"
    assert all("لا يُرسل" not in s["content_md"] for s in m["sections"])
    assert m["client"]["by_document"] == [{"document": 2, "side": "claimant", "party": "المدعي"}]
    assert m["left_out"] == [{"item_id": ITEM_B, "title": "لائحة دعوى", "reason": "skipped_no_map"}]


def test_a_file_sent_in_two_conversations_has_two_maps_newest_first(stubs):
    files_flags.reset_probe()
    with files_flags.override(read=True, write=False):
        sb = _world(read=True)
        maps = ams.get_file_angle_maps(sb, USER, ITEM_A)
        assert [m["map_id"] for m in maps] == [MAP_B, MAP_A]
        assert [m["conversation_title"] for m in maps] == ["سؤال لاحق", "استئناف حكم"]
        # The file id works too (a library row whose file has no live item answers with it).
        assert [m["map_id"] for m in ams.get_file_angle_maps(sb, USER, FILE)] == [MAP_B, MAP_A]
    files_flags.reset_probe()


def test_switch_off_a_file_is_only_its_own_item(stubs):
    files_flags.reset_probe()
    with files_flags.override(read=False, write=False):
        sb = _world(read=False)
        assert [m["map_id"] for m in ams.get_file_angle_maps(sb, USER, ITEM_A)] == [MAP_A]
    files_flags.reset_probe()


def test_not_mine_or_unknown_is_none_and_a_foreign_map_is_never_shown(path, stubs):
    sb = _world(read=path == "files-read")
    assert ams.get_file_angle_maps(sb, USER, ITEM_THEIRS) is None
    assert ams.get_file_angle_maps(sb, USER, "dddddddd-0000-0000-0000-000000000000") is None
    stubs.maps[ITEM_A2]["map"]["user_id"] = OTHER_USER
    assert ams.get_file_angle_maps(sb, USER, ITEM_A2) == []


def test_no_fresh_map_is_an_empty_list(path, stubs):
    sb = _world(read=path == "files-read")
    stubs.maps.pop(ITEM_A2)
    assert ams.get_file_angle_maps(sb, USER, ITEM_A2) == []


def test_a_map_that_fails_to_build_is_left_out(path, stubs, monkeypatch):
    sb = _world(read=path == "files-read")
    monkeypatch.setattr(ams, "load_segment_maps", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    assert ams.get_file_angle_maps(sb, USER, ITEM_A2) == []


def test_a_failed_file_read_is_a_500(path, stubs):
    sb = _world(read=path == "files-read")
    sb.fail_tables.add("workspace_items")
    with pytest.raises(LunaHTTPException) as e:
        ams.get_file_angle_maps(sb, USER, ITEM_A2)
    assert e.value.status_code == 500


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
    sb = _world(read=False)
    for settings, want in (
        ({}, {"enabled": True, "angle_maps": False}),
        ({"ANGLE_MAP_UI_ENABLED": True}, {"enabled": True, "angle_maps": True}),
        ({"ANGLE_MAP_UI_ENABLED": True, "ANGLE_MAP_UI_USERS": OTHER_USER}, {"enabled": True, "angle_maps": False}),
        ({"ANGLE_MAP_UI_ENABLED": True, "MY_FILES_ENABLED": False}, {"enabled": False, "angle_maps": False}),
    ):
        res = client_for(sb, **settings).get("/api/v1/files/status")
        assert res.status_code == 200 and res.json() == want


def test_route_is_feature_off_without_the_ui_switch(client_for, stubs):
    sb = _world(read=False)
    res = client_for(sb).get(f"/api/v1/files/{ITEM_A}/angle-maps")
    assert res.status_code == 404 and res.json()["error"]["code"] == "FEATURE_OFF"
    assert stubs.calls == []


def test_route_returns_the_maps_and_404s_a_foreign_file(client_for, stubs):
    sb = _world(read=False)
    c = client_for(sb, ANGLE_MAP_UI_ENABLED=True)
    res = c.get(f"/api/v1/files/{ITEM_A}/angle-maps")
    assert res.status_code == 200 and res.headers["Cache-Control"] == "private, no-store"
    [m] = res.json()["maps"]
    assert m["map_id"] == MAP_A and m["angles"][0]["sections"][1] == {"ref": "D2.S1", "side": "opp"}
    assert c.get(f"/api/v1/files/{ITEM_THEIRS}/angle-maps").status_code == 404
    assert c.get("/api/v1/files/not-a-uuid/angle-maps").status_code == 404   # validate_uuid
