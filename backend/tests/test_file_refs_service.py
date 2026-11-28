"""workspace_item_file_refs — write, list, the section popup, and the two routes
(aggregator_file_refs.md §4.5 / §4.6 / D7, D10–D12: [F1·S7] tags, a table
modelled on workspace_item_references). In-memory fake; no network. All text
invented."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.app.services import file_refs_service as svc
from shared.files import store as files_store

USER = "user-1"
OTHER = "user-2"
REPORT = "wi-report"
ATT = "att-1"
TEXT = "ترويسة المحكمة\nوحيث إن المدعى عليه لم يقدم ما يثبت السداد\nحكمت المحكمة"
# section 2 = the middle line
S2 = (TEXT.index("وحيث"), TEXT.index("\nحكمت"))


class _Q:
    def __init__(self, sb, name):
        self.sb, self.name, self.filters, self.payload, self._limit = sb, name, [], None, None

    def select(self, cols):
        self.sb.log.append((self.name, "select", cols))
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def in_(self, col, vals):
        vals = list(vals)
        self.filters.append(lambda r, c=col, v=vals: r.get(c) in v)
        return self

    def is_(self, col, val):
        self.filters.append(lambda r, c=col: r.get(c) is None)
        return self

    def order(self, col, desc=False):
        self._order = col
        return self

    def limit(self, n):
        self._limit = n
        return self

    def insert(self, rows):
        self.payload = rows
        return self

    def execute(self):
        table = self.sb.tables.setdefault(self.name, [])
        if self.payload is not None:
            if self.sb.fail_insert:
                raise RuntimeError("insert failed")
            table.extend(dict(r) for r in self.payload)
            return SimpleNamespace(data=self.payload)
        rows = [dict(r) for r in table if all(f(r) for f in self.filters)]
        if getattr(self, "_order", None):
            rows.sort(key=lambda r: r.get(self._order) or 0)
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows)


class _Sb:
    def __init__(self, **tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.log: list = []
        self.fail_insert = False

    def table(self, name):
        return _Q(self, name)


def _seg(seq, start, end, text, **kw):
    return SimpleNamespace(
        seq=seq, char_start=start, char_end=end, content_md=text,
        what=kw.get("what", f"قسم {seq}"), owner=kw.get("owner", "المحكمة"),
        brought_by=kw.get("brought_by", ""), page_from=kw.get("page_from", 2), page_to=kw.get("page_to", 3),
    )


@pytest.fixture(autouse=True)
def _plain_reads(monkeypatch):
    """Files read switch off (no overlay), no text fixes, «الملفات» on."""
    monkeypatch.setattr(files_store, "files_read_enabled", lambda *a, **kw: False)
    import shared.attachments.overlay as overlay

    monkeypatch.setattr(overlay, "correct_text", lambda sb, text, **kw: text)
    import backend.app.services.files_service as files_service

    monkeypatch.setattr(files_service, "my_files_enabled", lambda uid: True)


def _maps(monkeypatch, segments_by_item):
    import agents.memory.attachment_segmenter.store as store

    calls = []

    def fake(sb, ids, user_id=None, content_chars=None):
        calls.append((list(ids), user_id, content_chars))
        return {i: SimpleNamespace(segments=segments_by_item[i]) for i in ids if i in segments_by_item}

    monkeypatch.setattr(store, "load_segment_maps", fake)
    return calls


def _items(**extra):
    att = {"item_id": ATT, "user_id": USER, "kind": "attachment", "title": "الحكم الابتدائي",
           "content_md": TEXT, "deleted_at": None, "metadata": {}, "wi_seq": 1}
    att.update(extra)
    return [att, {"item_id": REPORT, "user_id": USER, "kind": "agent_search", "title": "تقرير",
                  "content_md": "", "deleted_at": None, "metadata": {}}]


def _ref(ref, n, seq=None, start=None, end=None, wi_seq=1):
    return SimpleNamespace(ref_id=ref, n=n, item_id=ATT, wi_seq=wi_seq,
                           section_seq=seq, char_start=start, char_end=end)


# ---------------------------------------------------------------------------
# persist
# ---------------------------------------------------------------------------


def test_persist_writes_real_stored_values(monkeypatch):
    _maps(monkeypatch, {ATT: [_seg(2, S2[0], S2[1], "نص القسم", what="أسباب الحكم", owner="المحكمة", brought_by="المدعي")]})
    sb = _Sb(workspace_items=_items())
    n = svc.persist_item_file_refs(
        sb, wi_id=REPORT, user_id=USER,
        file_refs=[_ref("F1.S2", 1, 2, *S2), _ref("F1", 2), _ref("F1.S2", 3, 2, *S2), _ref("bad", 4)],
    )
    assert n == 2
    rows = sb.tables["workspace_item_file_refs"]
    assert rows[0] == {
        "wi_id": REPORT, "n": 1, "ref_id": "F1.S2", "item_id": ATT, "wi_seq": 1, "section_seq": 2,
        "char_start": S2[0], "char_end": S2[1], "page_from": 2, "page_to": 3,
        "doc_title": "الحكم الابتدائي", "what": "أسباب الحكم", "owner": "المحكمة", "brought_by": "المدعي",
    }
    assert rows[1]["ref_id"] == "F1" and rows[1]["n"] == 2
    assert rows[1]["section_seq"] is None and rows[1]["what"] == ""


def test_persist_takes_the_wi_number_from_the_item_when_missing(monkeypatch):
    _maps(monkeypatch, {})
    sb = _Sb(workspace_items=_items())
    svc.persist_item_file_refs(sb, wi_id=REPORT, user_id=USER, file_refs=[_ref("F1", 1, wi_seq=None)])
    assert sb.tables["workspace_item_file_refs"][0]["wi_seq"] == 1


def test_persist_skips_a_ref_with_no_wi_number_and_writes_nothing_empty(monkeypatch):
    _maps(monkeypatch, {})
    items = _items()
    items[0].pop("wi_seq")
    sb = _Sb(workspace_items=items)
    n = svc.persist_item_file_refs(sb, wi_id=REPORT, user_id=USER, file_refs=[_ref("F1", 1, wi_seq=None)])
    assert n == 0
    assert "workspace_item_file_refs" not in sb.tables


def test_persist_keeps_only_positions_when_the_map_moved(monkeypatch):
    _maps(monkeypatch, {ATT: [_seg(2, 0, 5, "غير ذلك")]})
    sb = _Sb(workspace_items=_items())
    svc.persist_item_file_refs(sb, wi_id=REPORT, user_id=USER, file_refs=[_ref("F1.S2", 1, 2, *S2)])
    row = sb.tables["workspace_item_file_refs"][0]
    assert (row["char_start"], row["char_end"]) == S2
    assert row["what"] == "" and row["page_from"] is None


def test_persist_nothing_reads_nothing():
    sb = _Sb()
    assert svc.persist_item_file_refs(sb, wi_id=REPORT, user_id=USER, file_refs=[]) == 0
    assert sb.log == []


# ---------------------------------------------------------------------------
# list + section
# ---------------------------------------------------------------------------


def _stored(**extra):
    row = {"wi_id": REPORT, "n": 1, "ref_id": "F1.S2", "item_id": ATT, "wi_seq": 1, "section_seq": 2,
           "char_start": S2[0], "char_end": S2[1], "page_from": 2, "page_to": 3,
           "doc_title": "الحكم الابتدائي", "what": "أسباب الحكم", "owner": "المحكمة", "brought_by": ""}
    row.update(extra)
    return row


def test_list_rows_in_contract_shape():
    sb = _Sb(workspace_items=_items(), workspace_item_file_refs=[
        _stored(ref_id="F1", n=2, section_seq=None, char_start=None, char_end=None, what=""),
        _stored(),
    ])
    rows = svc.list_file_refs(sb, REPORT, USER)
    assert [r["ref_id"] for r in rows] == ["F1.S2", "F1"]
    assert set(rows[0]) == {"n", "ref_id", "wi_seq", "section_seq", "page_from", "page_to",
                            "doc_title", "what", "owner", "brought_by", "available"}
    assert rows[0]["available"] is True


def test_list_marks_a_deleted_attachment_unavailable():
    sb = _Sb(workspace_items=_items(deleted_at="2026-11-27T00:00:00Z"), workspace_item_file_refs=[_stored()])
    assert svc.list_file_refs(sb, REPORT, USER)[0]["available"] is False


def test_section_same_map_serves_the_row_text(monkeypatch):
    _maps(monkeypatch, {ATT: [_seg(2, S2[0], S2[1], "نص القسم كما خزّن")]})
    sb = _Sb(workspace_items=_items(), workspace_item_file_refs=[_stored()])
    out = svc.file_ref_section(sb, REPORT, "[F1·S2]", owner_user_id=USER, caller_user_id=USER)
    assert out["content_md"] == "نص القسم كما خزّن"
    assert out["files_item_id"] == ATT
    assert out["ref_id"] == "F1.S2" and out["available"] is True


def test_section_after_a_resegment_serves_the_slice(monkeypatch):
    _maps(monkeypatch, {ATT: [_seg(2, 0, 10, "قسم آخر صار رقمه 2")]})
    sb = _Sb(workspace_items=_items(), workspace_item_file_refs=[_stored()])
    out = svc.file_ref_section(sb, REPORT, "F1.S2", owner_user_id=USER, caller_user_id=USER)
    assert out["content_md"] == TEXT[S2[0]:S2[1]]


def test_section_with_no_map_left_serves_the_slice(monkeypatch):
    _maps(monkeypatch, {})
    sb = _Sb(workspace_items=_items(), workspace_item_file_refs=[_stored()])
    out = svc.file_ref_section(sb, REPORT, "f-1-s02", owner_user_id=USER, caller_user_id=USER)
    assert out["content_md"] == TEXT[S2[0]:S2[1]]


def test_whole_document_serves_the_whole_text(monkeypatch):
    _maps(monkeypatch, {})
    sb = _Sb(workspace_items=_items(), workspace_item_file_refs=[
        _stored(ref_id="F1", section_seq=None, char_start=None, char_end=None)
    ])
    assert svc.file_ref_section(sb, REPORT, "F1", owner_user_id=USER, caller_user_id=USER)["content_md"] == TEXT


def test_text_fixes_are_applied(monkeypatch):
    _maps(monkeypatch, {})
    import shared.attachments.overlay as overlay

    monkeypatch.setattr(overlay, "correct_text", lambda sb, text, **kw: text.replace("يقدم", "يقدّم"))
    sb = _Sb(workspace_items=_items(), workspace_item_file_refs=[_stored()])
    out = svc.file_ref_section(sb, REPORT, "F1.S2", owner_user_id=USER, caller_user_id=USER)
    assert "لم يقدّم" in out["content_md"]


def test_deleted_attachment_is_gone(monkeypatch):
    _maps(monkeypatch, {})
    sb = _Sb(workspace_items=_items(deleted_at="2026-11-27T00:00:00Z"), workspace_item_file_refs=[_stored()])
    with pytest.raises(svc.FileRefGone):
        svc.file_ref_section(sb, REPORT, "F1.S2", owner_user_id=USER, caller_user_id=USER)


def test_file_hidden_from_files_is_still_served_without_the_open_button(monkeypatch):
    _maps(monkeypatch, {})
    sb = _Sb(workspace_items=_items(metadata={"library_hidden_at": "2026-11-27"}),
             workspace_item_file_refs=[_stored()])
    out = svc.file_ref_section(sb, REPORT, "F1.S2", owner_user_id=USER, caller_user_id=USER)
    assert out["content_md"] == TEXT[S2[0]:S2[1]]
    assert out["files_item_id"] is None


def test_hidden_file_row_hides_the_open_button(monkeypatch):
    _maps(monkeypatch, {})
    monkeypatch.setattr(files_store, "file_ids_for_items", lambda sb, ids, user_id=None: {ATT: "file-1"})
    sb = _Sb(workspace_items=_items(), workspace_item_file_refs=[_stored()],
             user_files=[{"file_id": "file-1", "user_id": USER, "library_hidden_at": "2026-11-27", "deleted_at": None}])
    out = svc.file_ref_section(sb, REPORT, "F1.S2", owner_user_id=USER, caller_user_id=USER)
    assert out["files_item_id"] is None and out["content_md"]


def test_unknown_ref_or_other_users_attachment_is_not_found(monkeypatch):
    _maps(monkeypatch, {})
    sb = _Sb(workspace_items=_items(), workspace_item_file_refs=[_stored()])
    for ref in ("F1.S9", "nonsense", "[4]"):
        with pytest.raises(svc.FileRefNotFound):
            svc.file_ref_section(sb, REPORT, ref, owner_user_id=USER, caller_user_id=USER)
    with pytest.raises(svc.FileRefNotFound):
        svc.file_ref_section(sb, REPORT, "F1.S2", owner_user_id=OTHER, caller_user_id=OTHER)


def test_another_caller_never_gets_the_open_button(monkeypatch):
    """The shared demo report is readable by everyone: its file is not theirs."""
    _maps(monkeypatch, {})
    sb = _Sb(workspace_items=_items(), workspace_item_file_refs=[_stored()])
    out = svc.file_ref_section(sb, REPORT, "F1.S2", owner_user_id=USER, caller_user_id=OTHER)
    assert out["files_item_id"] is None


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


def _routes(monkeypatch, *, owner=USER, caller=USER):
    from backend.app.api import workspace as ws

    monkeypatch.setattr(ws.workspace_service, "get_workspace_item",
                        lambda sb, auth_id, item_id: {"item_id": item_id, "user_id": owner})
    monkeypatch.setattr(ws, "get_user_id", lambda sb, auth_id: caller)
    return ws


def test_list_route(monkeypatch):
    ws = _routes(monkeypatch)
    sb = _Sb(workspace_items=_items(), workspace_item_file_refs=[_stored()])
    rid = "11111111-1111-1111-1111-111111111111"
    sb.tables["workspace_item_file_refs"][0]["wi_id"] = rid
    out = asyncio.run(ws.list_workspace_item_file_refs(rid, current_user=SimpleNamespace(auth_id="a"), supabase=sb))
    assert [r["ref_id"] for r in out["file_refs"]] == ["F1.S2"]


def test_list_route_refuses_a_report_that_is_not_the_callers(monkeypatch):
    from backend.app.api import workspace as ws
    from backend.app.errors import ErrorCode, LunaHTTPException

    def not_found(sb, auth_id, item_id):
        raise LunaHTTPException(status_code=404, code=ErrorCode.ARTIFACT_NOT_FOUND, detail="العنصر غير موجود")

    monkeypatch.setattr(ws.workspace_service, "get_workspace_item", not_found)
    sb = _Sb(workspace_item_file_refs=[_stored()])
    with pytest.raises(LunaHTTPException) as exc:
        asyncio.run(ws.list_workspace_item_file_refs(
            "11111111-1111-1111-1111-111111111111", current_user=SimpleNamespace(auth_id="a"), supabase=sb,
        ))
    assert exc.value.status_code == 404
    assert not any(t == "workspace_item_file_refs" for t, *_ in sb.log)


def test_section_route_gone_and_not_found(monkeypatch):
    _maps(monkeypatch, {})
    ws = _routes(monkeypatch)
    from backend.app.errors import LunaHTTPException

    rid = "11111111-1111-1111-1111-111111111111"
    sb = _Sb(workspace_items=_items(deleted_at="2026-11-27"), workspace_item_file_refs=[_stored(wi_id=rid)])
    with pytest.raises(LunaHTTPException) as gone:
        asyncio.run(ws.get_workspace_item_file_ref_section(
            rid, "F1.S2", current_user=SimpleNamespace(auth_id="a"), supabase=sb,
        ))
    assert gone.value.status_code == 410 and gone.value.detail == "لم يعد هذا الملف متاحًا"
    with pytest.raises(LunaHTTPException) as missing:
        asyncio.run(ws.get_workspace_item_file_ref_section(
            rid, "F1.S9", current_user=SimpleNamespace(auth_id="a"), supabase=sb,
        ))
    assert missing.value.status_code == 404


def test_section_route_ok(monkeypatch):
    _maps(monkeypatch, {})
    ws = _routes(monkeypatch)
    rid = "11111111-1111-1111-1111-111111111111"
    sb = _Sb(workspace_items=_items(), workspace_item_file_refs=[_stored(wi_id=rid)])
    out = asyncio.run(ws.get_workspace_item_file_ref_section(
        rid, "F1.S2", current_user=SimpleNamespace(auth_id="a"), supabase=sb,
    ))
    assert out["content_md"] == TEXT[S2[0]:S2[1]] and out["files_item_id"] == ATT


# ---------------------------------------------------------------------------
# publisher — a file-ref failure never fails the publish
# ---------------------------------------------------------------------------


def _publish(monkeypatch, *, file_refs, fail=False):
    import agents.agent_search.publisher as pub
    from agents.agent_search.models import SearchPublishInput

    monkeypatch.setattr(pub, "create_workspace_item", lambda *a, **kw: {"item_id": REPORT, "title": "t"})
    monkeypatch.setattr(pub, "persist_item_references", lambda *a, **kw: None)
    calls = []

    def fake_persist(sb, **kw):
        calls.append(kw)
        if fail:
            raise RuntimeError("table missing")
        return len(kw["file_refs"])

    monkeypatch.setattr(svc, "persist_item_file_refs", fake_persist)
    agg = SimpleNamespace(references=[], validation=None, ref_to_sub_queries={}, artifact=None,
                          synthesis_md="x", confidence="high", gaps=[], file_refs=file_refs)
    inp = SearchPublishInput(user_id=USER, conversation_id="c", message_id="m", agg_output=agg,
                             original_query="q", detail_level="medium")
    out = asyncio.run(pub.publish_search_result(inp, SimpleNamespace(supabase=object(), logger=None)))
    return out, calls


def test_publisher_writes_file_refs_beside_the_library_refs(monkeypatch):
    out, calls = _publish(monkeypatch, file_refs=[_ref("F1.S2", 1, 2, *S2)])
    assert out.item_id == REPORT
    assert calls and calls[0]["wi_id"] == REPORT and calls[0]["user_id"] == USER


def test_publisher_survives_a_file_ref_failure(monkeypatch):
    out, calls = _publish(monkeypatch, file_refs=[_ref("F1.S2", 1, 2, *S2)], fail=True)
    assert out.item_id == REPORT and calls


def test_publisher_without_file_refs_calls_nothing(monkeypatch):
    out, calls = _publish(monkeypatch, file_refs=[])
    assert out.item_id == REPORT and calls == []
