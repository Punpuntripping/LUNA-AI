"""``scripts/angle_map_preview.py`` — group selection (``--message`` / ``--item`` /
``--conversation``), a preview run that writes nothing and bills nothing, the
page (per group: documents, client per document, angle cards with document refs
and side labels, cross-document badge, unrelated documents, left-out files), the
``--from-json`` re-render and the ``--out`` guard. The real runner on a fake
client and a ``FunctionModel``; no network."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.memory.angle_mapper import agent as agent_mod
from agents.memory.angle_mapper import runner as runner_mod
from agents.utils import usage_sink

from . import fixtures as F
from .fake_supabase import FakeSupabase

REPO = Path(__file__).resolve().parents[4]
A, B, C = F.ITEM_A, F.ITEM_B, F.ITEM_C


def _load_tool():
    spec = importlib.util.spec_from_file_location("angle_map_preview_tool", REPO / "scripts" / "angle_map_preview.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


T = _load_tool()


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(runner_mod, "angle_map_enabled", lambda uid: False)  # preview ignores the gate
    monkeypatch.setattr(runner_mod, "_codec_for", lambda supabase, uid: None)
    monkeypatch.setattr(runner_mod, "_GROUP_TASKS", {})


@pytest.fixture
def ledger(monkeypatch) -> list:
    flushed: list = []
    monkeypatch.setattr(usage_sink, "_flush", lambda sb, buf: flushed.append(list(buf or [])) or 0)
    return flushed


def model(monkeypatch, outputs: dict[int, dict]) -> list[str]:
    """Answer by the number of documents in the prompt (``- N documents``)."""
    prompts: list[str] = []

    async def fn(messages: list, info: AgentInfo) -> ModelResponse:
        prompt = next(p.content for p in messages[-1].parts if isinstance(getattr(p, "content", None), str))
        prompts.append(prompt)
        n = int(prompt.split("\n")[1].split()[1])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, outputs[n])])

    monkeypatch.setattr(agent_mod, "get_agent_model", lambda slot: FunctionModel(fn))
    return prompts


def conversation_db() -> FakeSupabase:
    """MSG: the judgment + the objection + the lease (its segments too old → left
    out); a fourth file with no message link (a group of one)."""
    t = F.tables(F.JUDGMENT, F.OBJECTION, F.LEASE)
    for r in t["attachment_segment_maps"]:
        if r["item_id"] == C:
            r["prompt_version"] = "seg-v5.1"
    lone = F.Doc(**{**F.JUDGMENT.__dict__, "item_id": "eeeeeeee-5555-4000-8000-000000000005", "wi_seq": 9,
                    "title": "صك آخر بلا رسالة"})
    t["workspace_items"].append(F.item_row(lone))
    t["attachment_segment_maps"].append(F.seg_map_row(lone))
    t["attachment_segments"] += F.segment_rows(lone)
    return FakeSupabase(t)


LONE = "eeeeeeee-5555-4000-8000-000000000005"
MSG_UUID = "abcdef12-3456-4000-8000-0000000000aa"


# ── selection ────────────────────────────────────────────────────────────────


def test_select_by_item_resolves_its_message_group():
    db = conversation_db()
    [g] = T.select_groups(db, items=[B])
    assert g == {"key": f"message:{F.MSG}", "message_id": F.MSG, "item_ids": [A, B, C]}
    [g] = T.select_groups(db, items=[LONE[:8]])  # a prefix; no message link → a group of one
    assert g == {"key": f"item:{LONE}", "message_id": None, "item_ids": [LONE]}
    assert db.writes() == []


def test_select_by_conversation_gives_every_group_once_in_upload_order():
    db = conversation_db()
    groups = T.select_groups(db, conversations=[F.CONVO], items=[A, C], messages=[])
    assert [g["key"] for g in groups] == [f"message:{F.MSG}", f"item:{LONE}"]


def test_select_by_message_prefix_prefers_the_user_message():
    t = F.tables(F.JUDGMENT, message=MSG_UUID)
    t["messages"].append(F.message_row("abcdef12-9999-4000-8000-0000000000bb", "رد", role="assistant"))
    db = FakeSupabase(t)
    [g] = T.select_groups(db, messages=["abcdef12"])
    assert g["message_id"] == MSG_UUID and g["item_ids"] == [A]
    assert T.select_groups(db, messages=[MSG_UUID, "abcdef12"]) == [g]  # selected twice, runs once


@pytest.mark.parametrize("token, error", [
    ("abc", "at least 6 hex"), ("zzzzzzzz", "not a uuid"), ("ffffff", "no message"),
])
def test_bad_message_tokens_are_refused(token, error):
    with pytest.raises(SystemExit, match=error):
        T.select_groups(FakeSupabase(F.tables(F.JUDGMENT)), messages=[token])


def test_a_message_without_attachments_is_skipped(capsys):
    db = FakeSupabase(F.tables(F.JUDGMENT))
    assert T.select_groups(db, messages=["99999999-0000-4000-8000-000000000000"]) == []
    assert "has no attachment" in capsys.readouterr().out


# ── the run: preview only ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_previews_each_group_and_writes_nothing(monkeypatch, ledger):
    db = conversation_db()
    prompts = model(monkeypatch, {2: F.good_two(), 1: F.good_one()})
    groups = T.select_groups(db, conversations=[F.CONVO])
    db.log.clear()
    data = await T.collect(db, groups, concurrency=2)
    assert db.writes() == [] and ledger == [] and db.tables["attachment_angle_maps"] == []
    assert len(prompts) == 2
    g1, g2 = data["groups"]
    assert g1["result"]["outcome"] == "preview" and g1["result"]["item_ids"] == [A, B]
    assert g1["result"]["excluded"] == {C: "skipped_old_segments"} and g1["result"]["map_id"] is None
    assert [(d["item_id"], d["doc_index"], d["excluded"]) for d in g1["documents"]] == [
        (A, 1, None), (B, 2, None), (C, None, "skipped_old_segments")]
    assert g1["user_words"] == F.user_words()
    assert g2["result"]["outcome"] == "preview" and g2["message_id"] is None and g2["user_words"] == ""
    assert data["prompt_version"] == "angles-v5"
    assert T._coverage(g1)["unplaced"] == [] and len(T._coverage(g1)["core"]) == 5
    assert sum(1 for a in T._angles(g1) if T._cross(a)) == 2


@pytest.mark.asyncio
async def test_a_deleted_first_file_does_not_hide_the_group(monkeypatch, ledger):
    t = F.tables(F.JUDGMENT, F.OBJECTION)
    t["workspace_items"][0]["deleted_at"] = "2026-11-21T00:00:00+00:00"
    db = FakeSupabase(t)
    model(monkeypatch, {1: objection_alone()})
    g = T._group_of_message(db, F.MSG)
    assert g["item_ids"] == [A, B]  # the deleted file is still linked to the message
    data = await T.collect(db, [g])
    [page] = data["groups"]
    assert page["result"]["outcome"] == "preview" and page["result"]["item_ids"] == [B]
    assert [(d["item_id"], d["excluded"]) for d in page["documents"]] == [(B, None), (A, "skipped_not_found")]


def objection_alone() -> dict:
    """A valid map for the objection as D1: its core sections in context."""
    return {"client": dict(F.CLIENT), "angles": [], "context": ["D1.S2", "D1.S3"], "gaps": [], "no_angle": [],
            "unrelated_documents": []}


# ── the page ─────────────────────────────────────────────────────────────────


@pytest.fixture
def page_data(monkeypatch) -> dict:
    db = FakeSupabase(F.tables(F.JUDGMENT, F.OBJECTION, F.LEASE))
    model(monkeypatch, {3: F.good_three()})
    import asyncio

    return asyncio.run(T.collect(db, [T._group(F.MSG, [A, B, C])]))


def test_the_page_shows_the_group(page_data):
    html = T.render(page_data)
    assert html.startswith("<!doctype html>") and '<html lang="ar" dir="rtl">' in html
    assert "prefers-color-scheme: dark" in html and "max-width:640px" in html
    for title in (F.JUDGMENT.title, F.OBJECTION.title, F.LEASE.title):
        assert title in html
    # refs as the prompt shows them, with their document's title
    assert "<span class='sid'>D2·S2</span> <span class='dt'>— لائحة اعتراض على الحكم</span>" in html
    assert "يربط بين المستندات" in html
    assert "مستندات من قضية أخرى (1)" in html and "عقد إيجار بين أطراف أخرى في نزاع آخر" in html
    assert "صفته في المستندات الأخرى" in html and "المعترض" in html
    assert F.WORDS in html
    assert "المجموع" in html and "1 مجموعة (1 ناجحة)" in html


def test_side_labels_follow_the_clients_key_in_each_document(page_data):
    g = page_data["groups"][0]
    view = T._GroupView(g)
    serves_client = T._angles(g)[0]  # for_side respondent; D2 sections are the client's as claimant
    assert "الموكّل" in view.label("D2.S2", serves_client)
    assert "الموكّل" in view.label("D1.S3", serves_client)
    assert "الخصم" in view.label("D1.S2", serves_client)
    assert "محايد" in view.label("D1.S5", serves_client)
    assert view.label("D9.S1", serves_client) == ""
    # the section text opens per document, in that document's own paragraph numbers
    assert "P002" in view.text("D2.S2") and F.OBJECTION.lines[1] in view.text("D2.S2")
    assert "P010" in view.text("D1.S5") and F.JUDGMENT.lines[9] in view.text("D1.S5")


def test_an_ocr_suspect_angle_card_carries_the_badge(monkeypatch):
    out = F.good_three()
    out["angles"][1]["ocr_suspect"] = True
    db = FakeSupabase(F.tables(F.JUDGMENT, F.OBJECTION, F.LEASE))
    model(monkeypatch, {3: out})
    import asyncio

    data = asyncio.run(T.collect(db, [T._group(F.MSG, [A, B, C])]))
    g = data["groups"][0]
    assert [a["ocr_suspect"] for a in T._angles(g)] == [False, True, False]
    assert T.OCR_SUSPECT_AR == "قد يكون خطأ قراءة"
    view = T._GroupView(g)
    cards = [T._angle_card(view, i, a) for i, a in enumerate(T._angles(g), 1)]
    badge = "<span class='flag'>قد يكون خطأ قراءة</span>"
    assert [badge in c for c in cards] == [False, True, False]
    assert T.render(data).count(badge) == 1
    assert g["result"]["stats"]["ocr_suspect_angles"] == 1


def test_overview_row_counts(page_data):
    g = page_data["groups"][0]
    row = T._overview_row(g, 0)
    total = sum(len(F.content(d)) for d in (F.JUDGMENT, F.OBJECTION, F.LEASE))
    assert f"{total:,}" in row and "preview" in row and "class='warn'" not in row
    assert "<td class='num'>2</td>" in row  # two cross-document angles
    assert "5/0/2/0" in row and "من 7" in row  # 7 core sections over three documents: 5 in angles, 2 no_angle
    assert T._line(g).startswith(F.MSG[:8])


def test_from_json_re_renders_the_same_page(page_data, tmp_path):
    saved = tmp_path / T.JSON_NAME
    saved.write_text(json.dumps(page_data, ensure_ascii=False, default=str), encoding="utf-8")
    out = tmp_path / "out"
    T.main(["--from-json", str(saved), "--out", str(out)])
    assert (out / T.HTML_NAME).read_text(encoding="utf-8") == T.render(page_data)


def test_a_failed_group_still_renders():
    g = {"key": "message:m", "message_id": "m", "user_words": "", "documents": [],
         "result": {"outcome": "error", "error": "preview: RuntimeError", "output": None, "stats": {}}}
    html = T.render({"groups": [g]})
    assert "preview: RuntimeError" in html and "class='warn'" in html


# ── the --out guard ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("inside", [REPO, REPO / "scripts", REPO / "nope" / "deeper"])
def test_out_inside_a_repo_is_refused_before_anything_runs(inside):
    with pytest.raises(SystemExit, match="OUTSIDE"):
        T.main(["--out", str(inside), "--message", "abcdef12"])


def test_the_tool_never_writes_to_the_database():
    src = (REPO / "scripts" / "angle_map_preview.py").read_text(encoding="utf-8")
    src = src.replace("sys.path.insert(0, str(ROOT))", "")
    for call in (".insert(", ".upsert(", ".update(", ".delete(", "write_angle_map", "persist_new_mappings",
                 "collect_llm_calls"):
        assert call not in src, call
    assert "preview=True" in src and "run_angle_mapper(" not in src
