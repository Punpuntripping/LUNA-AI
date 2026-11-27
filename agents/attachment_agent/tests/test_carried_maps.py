"""A «الملفات» copy reads and edits the map its file CARRIES from another
conversation (``angle_mapper/library.py``, owner 2026-11-27): shown whole, its
members pointed at this conversation's copy, edits made in place on that map
(plan §7 option a), refs into its other documents kept, and nothing at all with
``LIBRARY_FILE_MAPS_ENABLED`` off. The mapper's freshness check is stubbed (its
own tests cover it), like ``angle_mapper/tests/test_library.py``."""
from __future__ import annotations

import asyncio

import pytest

from agents.attachment_agent import apply as apply_mod
from agents.attachment_agent import edit_tools as T
from agents.attachment_agent.apply import apply_staged
from agents.attachment_agent.edit_tools import NO_THREADS, ThreadFields
from agents.memory.angle_mapper import store as store_mod
from agents.memory.angle_mapper.edits import EDITS_TABLE
from shared.attachments import segment_overrides, text_corrections
from shared.files import flags as files_flags

from .fakes import (
    LEASE,
    MEMO,
    PAGE_STARTS,
    USER,
    angle_map,
    edit_deps,
    lease_sections,
    make_db,
    memo_sections,
    segment_map,
)

OTHER_CONVO = "c0"


def _item(item_id: str, file_id: str, convo: str, title: str, content: str, at: str, **kw) -> dict:
    return {"item_id": item_id, "file_id": file_id, "conversation_id": convo, "user_id": USER,
            "kind": "attachment", "title": title, "summary": "", "content_md": content, "created_at": at,
            "deleted_at": None, "doc_type": "contract", "wi_seq": kw.get("wi_seq")}


def _file(file_id: str, title: str, content: str) -> dict:
    return {"file_id": file_id, "user_id": USER, "title": title, "summary": "", "content_md": content,
            "content_chars": len(content), "deleted_at": None, "metadata": {}}


def _db():
    """The lease (file f1) and a memo (file f2) were uploaded together in c0 and
    mapped (map C: D1 = the lease, D2 = the memo). The lease was attached from
    «الملفات» to this conversation (c1) as item i1; the memo was not."""
    carried = angle_map("mapC", ["o1", "o2"], [
        {"title": "منع التأجير من الباطن", "kind": "attack", "strength": "medium", "sections": ["D1.S5"]},
        {"title": "طلب الإخلاء", "kind": "strengthen", "strength": "strong", "sections": ["D2.S2", "D1.S3"]},
    ], client={"side": "claimant", "party": "محمد أحمد العتيبي"}, message_id="m-c0")
    carried["map"]["conversation_id"] = OTHER_CONVO
    db = make_db(
        [_item("o1", "f1", OTHER_CONVO, "عقد الإيجار", LEASE, "2026-11-20T10:00:00"),
         _item("o2", "f2", OTHER_CONVO, "مذكرة الدعوى", MEMO, "2026-11-20T10:00:01"),
         _item("i1", "f1", "c1", "عقد الإيجار", LEASE, "2026-11-27T09:00:00", wi_seq=1)],
        maps=[segment_map("f1", LEASE, lease_sections()), segment_map("f2", MEMO, memo_sections(MEMO))],
        angle_maps=[carried],
    )
    db.tables["user_files"] = [_file("f1", "عقد الإيجار", LEASE), _file("f2", "مذكرة الدعوى", MEMO)]
    db.tables["attachment_pages"] = [
        {"item_id": "f1", "page_no": n, "char_start": s, "content_md": ""} for n, s in enumerate(PAGE_STARTS, 1)
    ]
    return db


@pytest.fixture(autouse=True)
def _library(monkeypatch):
    """Files read switch on (``file_id``), LIBRARY_FILE_MAPS on, freshness stubbed."""
    from shared.config import get_settings

    text_corrections.reset_backoff()
    segment_overrides.reset_backoff()
    settings = get_settings()
    monkeypatch.setattr(settings, "LIBRARY_FILE_MAPS_ENABLED", True)
    monkeypatch.setattr(settings, "LIBRARY_FILE_MAPS_USERS", "")
    monkeypatch.setattr(store_mod, "load_fresh_angle_map_for_item", store_mod.load_angle_map_for_item)
    monkeypatch.setattr(apply_mod, "persist_new_mappings", lambda *a: 0)
    with files_flags.override(read=True, write=False):
        yield


def test_a_copy_shows_the_map_it_carries_whole():
    deps = edit_deps(_db(), "x")
    (tmap,) = deps.maps
    assert tmap.map_id == "mapC" and tmap.carried and tmap.label == ""
    # the lease member points at THIS conversation's copy; the memo stays the map's own
    assert tmap.doc_of_item == {"i1": 1, "o2": 2}
    assert deps.files["F1"].has_angle_map
    from agents.attachment_agent.context import load_context
    from agents.attachment_agent.models import AttachmentAgentCall

    (block,) = load_context(_db(), AttachmentAgentCall(user_id=USER, conversation_id="c1", user_message="x")).threads
    head = block.splitlines()[0]
    assert "carried from another conversation" in head and "1 document not in this conversation" in head
    assert "A1 · «منع التأجير من الباطن»" in block and "sections: F1·S5" in block
    # the thread resting on the memo says so
    assert "A2 · «طلب الإخلاء»" in block
    assert "sections: F1·S3 (+1 in documents not in this conversation)" in block


def test_a_thread_edit_on_the_carried_map_is_made_in_place():
    db = _db()
    message = "خل خيط الإخلاء قوته متوسطة وأضف له قسم مدة العقد"
    deps = edit_deps(db, message)
    out = T.stage_edit_thread(deps, "A2", ThreadFields(strength="medium", add_sections=["F1·S2"]),
                              "خل خيط الإخلاء قوته متوسطة وأضف له قسم مدة العقد")
    assert out.startswith("Staged")
    # F1·S2 → the map's D1 (the localized copy's document)
    assert deps.staged_edits[0].args["fields"] == {"strength": "medium", "add_sections": ["D1.S2"]}
    report = asyncio.run(apply_staged(deps))
    assert report.failed == []
    assert report.applied == ["عُدّل الخيط «طلب الإخلاء»: القوة → متوسطة؛ أُضيف إليه «مدة العقد»"]
    a2 = next(a for a in db.tables["attachment_angles"] if a["map_id"] == "mapC" and a["seq"] == 2)
    assert a2["strength"] == "medium" and a2["sections"] == ["D2.S2", "D1.S3", "D1.S2"]   # the memo ref kept
    (log,) = db.tables[EDITS_TABLE]
    assert (log["map_id"], log["conversation_id"]) == ("mapC", "c1")
    assert [r["item_id"] for r in db.tables["attachment_angle_map_items"]] == ["o1", "o2"]  # members as stored


def test_a_full_sections_list_keeps_the_refs_into_documents_not_here():
    deps = edit_deps(_db(), "غير أقسام خيط الإخلاء إلى قسم الأجرة")
    T.stage_edit_thread(deps, "A2", ThreadFields(sections=["F1·S3", "F1·S2"]), "غير أقسام خيط الإخلاء")
    assert deps.staged_edits[0].args["fields"] == {"sections": ["D2.S2", "D1.S3", "D1.S2"]}


def test_reanalysis_of_a_carried_map_passes_this_conversations_items():
    deps = edit_deps(_db(), "ماعجبني التحليل اعيد تحليله")
    T.stage_reanalysis(deps, "اعيد تحليله")
    assert deps.staged_edits[0].args["targets"] == [{"map_id": "mapC", "item_ids": ["i1"]}]


def test_flag_off_nothing_is_carried(monkeypatch):
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "LIBRARY_FILE_MAPS_ENABLED", False)
    db = _db()
    deps = edit_deps(db, "خل خيط الإخلاء قوته متوسطة")
    assert deps.maps == [] and not deps.files["F1"].has_angle_map
    assert T.stage_edit_thread(deps, "A2", ThreadFields(strength="medium"), "خل خيط الإخلاء قوته متوسطة") == NO_THREADS
    # no carried-map read at all: the map heads are never listed
    assert ("attachment_angle_maps", "map_id, created_at") not in db.selects


def test_an_own_map_wins_over_a_carried_one():
    db = _db()
    own = angle_map("mapOwn", ["i1"], [{"title": "خيط خاص", "kind": "attack", "sections": ["D1.S2"]}],
                    message_id="m-c1")
    db.tables["attachment_angle_maps"].append(own["map"])
    db.tables["attachment_angle_map_items"].extend(own["items"])
    db.tables["attachment_angles"].extend(own["angles"])
    deps = edit_deps(db, "x")
    (tmap,) = deps.maps
    assert tmap.map_id == "mapOwn" and not tmap.carried
