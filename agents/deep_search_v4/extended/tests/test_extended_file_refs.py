"""«بحث شامل» with the file refs on (aggregator_file_refs.md, step 2): the markers and the
briefs carry the conversation's file tag (F + the file's WI number) instead of the map's
D number, each search's aggregator numbers what it was actually shown, and the reports
carry the documents for the merger. Off = today's text, byte for byte."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agents.deep_search_v4 import orchestrator as orch
from agents.deep_search_v4.extended import brief, runner
from agents.deep_search_v4.extended.runner import (
    file_wi_of,
    run_extended_searches,
    whole_file_items,
    whole_file_refs,
)
from agents.deep_search_v4.extended.view import file_ref, file_tagged_text, whole_file_label
from agents.deep_search_v4.shared.case_documents import FileRefDoc
from agents.models import WorkspaceItemSnapshot
from backend.app.services import masking_service

from ._exec_fakes import (
    MARKED_D1,
    attached_snapshot,
    make_agg,
    make_plan,
    make_ref,
    make_request,
    make_view,
    planner_deps,
)

SPANS = ((1, 0, 40), (2, 41, 80))


def _view(**kw):
    view = make_view(**kw)
    view.documents[0] = replace(view.documents[0], section_spans=SPANS)
    return view


def _attached():
    return [
        attached_snapshot("item-1", 3),
        attached_snapshot("item-2", 7, content="نص المذكرة", doc_type="memo"),
        attached_snapshot("item-9", 5, content="مرفق آخر", doc_type="official_record"),
    ]


# --------------------------------------------------------------------------- spelling


def test_a_ref_is_written_as_the_file_tag_when_the_document_has_a_number():
    assert file_ref("D1.S7", {"D1": 3}) == "F3·S7"
    assert file_ref("D2.S7", {"D1": 3}) == "D2·S7"  # no number for D2 → the map's ref
    assert file_ref("D1.S7") == "D1·S7" and file_ref("D1.S7", {}) == "D1·S7"
    assert whole_file_label(4) == "〔F4〕"


def test_only_the_markers_of_that_document_are_renamed():
    text = "〔D1·S1 · أ · جوهري〕\n\nنص\n\n〔D10·S2 · ب · جوهري〕\n\nD1·S3 في المتن"
    assert file_tagged_text(text, "D1", 3) == (
        "〔F3·S1 · أ · جوهري〕\n\nنص\n\n〔D10·S2 · ب · جوهري〕\n\nD1·S3 في المتن"
    )


# --------------------------------------------------------------------------- the documents


def test_with_tags_every_document_opens_under_its_file_tag():
    items = whole_file_items(_view(), _attached(), {}, file_tags=True)
    d1, d2, extra = items
    assert d1.content_md == MARKED_D1.replace("〔D1·S", "〔F3·S")
    assert "〔D1·S" not in d1.content_md
    assert d2.content_md == "〔F7〕\n\nنص المذكرة"  # no marked sections → one label over the text
    assert extra.content_md == "〔F5〕\n\nمرفق آخر"  # another document of the turn, shown whole


def test_without_tags_the_documents_are_todays():
    items = whole_file_items(_view(), _attached(), {})
    assert [s.content_md for s in items] == [MARKED_D1, "نص المذكرة", "مرفق آخر"]
    assert whole_file_items(_view(), _attached(), {}, file_tags=False) == items


def test_a_document_with_no_text_gets_no_label():
    items = whole_file_items(_view(), [attached_snapshot("item-1", 3)], {7: "item-2"}, file_tags=True)
    assert items[1].item_id == "item-2" and items[1].content_md == ""


def test_what_each_search_may_cite_and_the_numbers_the_briefs_use():
    view = _view()
    items = whole_file_items(view, _attached(), {}, file_tags=True)
    assert whole_file_refs(view, items) == [
        FileRefDoc(wi_seq=3, item_id="item-1", sections=SPANS),
        FileRefDoc(wi_seq=7, item_id="item-2", sections=()),
        FileRefDoc(wi_seq=5, item_id="item-9", sections=()),
    ]
    assert file_wi_of(view, items) == {"D1": 3, "D2": 7}
    bare = whole_file_items(view, [attached_snapshot("item-1", 3)], {7: "item-2"}, file_tags=True)
    assert [d.item_id for d in whole_file_refs(view, bare)] == ["item-1"]  # D2 has no text


# --------------------------------------------------------------------------- the brief


def test_the_brief_names_sections_by_their_file_tag():
    req = make_request(angles=["A1", "A2"]).model_copy(update={"sections": ["D1.S2"]})
    plan, view = make_plan([req]), _view()
    on = brief.build_planner_brief(req, plan=plan, view=view, file_wi={"D1": 3})
    assert "الأقسام: F3·S1 (الموكّل: claimant)" in on and "الأقسام: F3·S2 (الخصم: respondent)" in on
    assert "- أقسام يقرؤها البحث بتركيز: F3·S2" in on
    assert "D1·S" not in on
    off = brief.build_planner_brief(req, plan=plan, view=view)
    assert off == brief.build_planner_brief(req, plan=plan, view=view, file_wi=None)
    assert off == on.replace("F3·S", "D1·S")


# --------------------------------------------------------------------------- the aggregator's numbering


def _snap(item_id: str, seq: int, content: str) -> WorkspaceItemSnapshot:
    return WorkspaceItemSnapshot(item_id=item_id, kind="attachment", title=f"وثيقة {seq}", content_md=content,
                                 summary="ملخص", metadata={"doc_type": "judgment"}, wi_seq=seq)


_TAGGED = "〔F3·S1 · الدائرة · جوهري〕\n\n" + "ن" * 100 + "\n\n〔F3·S2 · الخصم · جوهري〕\n\nنص"
_NO_SECTIONS = SimpleNamespace(case_full_text_wis=[], reg_full_text_wis=[])


def _whole_deps(items, refs, caps=(200_000, 300_000)):
    return SimpleNamespace(attached_items=items, _case_doc_caps=caps, _whole_file_refs=refs, user_id="u1")


def test_under_extended_the_file_refs_need_the_flag_and_the_runners_tags(monkeypatch):
    monkeypatch.setattr(orch, "file_refs_enabled", lambda uid: uid == "u1")
    refs = [FileRefDoc(wi_seq=3, item_id="item-1", sections=SPANS)]
    assert orch._file_refs_on(_whole_deps([], refs)) is True
    assert orch._file_refs_on(_whole_deps([], [])) is False  # the runner tagged nothing
    assert orch._file_refs_on(SimpleNamespace(user_id="u2", _case_doc_caps=(1, 2), _whole_file_refs=refs)) is False
    assert orch._file_refs_on(SimpleNamespace(user_id="u1")) is True  # the ordinary search, as before


def test_the_aggregator_numbers_only_what_it_was_shown():
    refs = [
        FileRefDoc(wi_seq=3, item_id="item-1", sections=SPANS),
        FileRefDoc(wi_seq=5, item_id="item-9", sections=()),
        FileRefDoc(wi_seq=8, item_id="item-x", sections=()),  # not delivered this turn
    ]
    items = [_snap("item-1", 3, _TAGGED), _snap("item-9", 5, "〔F5〕\n\nمرفق")]
    numbered: list[FileRefDoc] = []
    lists, tel = orch._build_case_document_lists(_NO_SECTIONS, _whole_deps(items, refs), None, file_ref_docs=numbered)
    assert numbered == refs[:2]
    assert tel["case_docs.file_refs.docs"] == 2 and tel["case_docs.file_refs.sections"] == 2
    assert "〔F3·S1 ·" in lists["aggregator"][0].body


def test_a_document_cut_at_its_cap_keeps_the_sections_whose_label_made_it_in():
    refs = [FileRefDoc(wi_seq=3, item_id="item-1", sections=SPANS)]
    numbered: list[FileRefDoc] = []
    orch._build_case_document_lists(
        _NO_SECTIONS, _whole_deps([_snap("item-1", 3, _TAGGED)], refs, caps=(80, 300_000)), None,
        file_ref_docs=numbered,
    )
    assert numbered == [FileRefDoc(wi_seq=3, item_id="item-1", sections=(SPANS[0],))]


def test_a_document_over_the_total_cap_is_not_numbered():
    refs = [FileRefDoc(wi_seq=3, item_id="item-1", sections=SPANS), FileRefDoc(wi_seq=5, item_id="item-9")]
    items = [_snap("item-1", 3, _TAGGED), _snap("item-9", 5, "〔F5〕\n\n" + "م" * 400)]
    numbered: list[FileRefDoc] = []
    orch._build_case_document_lists(
        _NO_SECTIONS, _whole_deps(items, refs, caps=(1_000, 300)), None, file_ref_docs=numbered,
    )
    assert [d.wi_seq for d in numbered] == [3]


# --------------------------------------------------------------------------- the runner


@pytest.fixture
def persisted(monkeypatch):
    monkeypatch.setattr(masking_service, "persist_new_mappings", lambda sb, uid, codec: 0)


def _run_plan(monkeypatch, *, tags: bool):
    calls: list[dict] = []

    async def fake_retrieval(query, config, deps):
        calls.append({"refs": getattr(deps, "_whole_file_refs", None), "items": list(deps.attached_items),
                      "brief": deps._decision.planner_brief})
        return make_agg([make_ref(1, "reg:x")], "تقرير [1] «يطالب المدعي بالدين» [F3·S1]")

    monkeypatch.setattr(orch, "run_retrieval", fake_retrieval)
    monkeypatch.setattr(runner, "_file_tags_on", lambda deps: tags)
    reqs = [make_request("بحث أول"), make_request("بحث ثان", angles=["A2"])]
    reports = asyncio.run(run_extended_searches(
        reqs, plan=make_plan(reqs), view=_view(), query="سؤال",
        deps_factory=lambda i: planner_deps([attached_snapshot("item-1", 3)], wi_alias_map={3: "item-1"}),
    ))
    return calls, reports


def test_with_tags_each_search_reads_tags_and_may_cite_them(monkeypatch, persisted):
    calls, reports = _run_plan(monkeypatch, tags=True)
    want = [FileRefDoc(wi_seq=3, item_id="item-1", sections=SPANS)]
    assert [c["refs"] for c in calls] == [want, want]
    assert all("〔F3·S1 ·" in c["items"][0].content_md for c in calls)
    assert "F3·S1" in calls[0]["brief"] and "D1·S" not in calls[0]["brief"]
    assert all(r.file_ref_docs == want and r.documents == calls[0]["items"] for r in reports)


def test_without_tags_the_searches_are_todays(monkeypatch, persisted):
    calls, reports = _run_plan(monkeypatch, tags=False)
    assert [c["refs"] for c in calls] == [None, None]
    assert calls[0]["items"][0].content_md == MARKED_D1
    assert "D1·S1" in calls[0]["brief"]
    assert all(r.file_ref_docs == [] for r in reports)
