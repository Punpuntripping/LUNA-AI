"""Runner (angles-v4, one map per message group) — group resolution, the trigger
rule (pending / joined in-process / fresh / a late member re-maps), the ref
validator retry, coverage + repair across documents, masking with one codec,
the ledger, preview, and never raising. The REAL agent runs (its output
validator included) on a ``FunctionModel``; the store is the real one on a fake
client."""
from __future__ import annotations

import asyncio
import copy
import random
from datetime import timedelta
from typing import Any, Callable

import pytest
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from agents.helpers import _runtime
from agents.memory.angle_mapper import agent as agent_mod
from agents.memory.angle_mapper import runner as runner_mod
from agents.memory.angle_mapper import store as store_mod
from agents.memory.angle_mapper import (
    angle_mapper_applies_to_item,
    load_angle_map_for_item,
    load_fresh_angle_map_for_item,
    run_angle_mapper,
    run_message_angle_map,
)
from agents.memory.angle_mapper.deps import AngleMapperDeps
from agents.memory.angle_mapper.models import Angle, AngleMap
from agents.memory.angle_mapper.prompts import (
    UNREAD_HEADING,
    PromptDocument,
    build_instructions,
    build_user_message,
    multi_question_message,
    no_sections_message,
    repair_message,
)
from agents.memory.angle_mapper.runner import segment_version_ok
from agents.memory.angle_mapper.store import ANGLE_COLUMNS, ITEM_COLUMNS, MAP_COLUMNS
from agents.memory.attachment_segmenter.paragraphs import PARAGRAPH_RULE_VERSION
from agents.utils import agent_models, usage_sink
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP
from agents.utils.usage_sink import collect_llm_calls
from shared.privacy import PrivacyCodec

from . import fixtures as F
from .fake_supabase import FakeSupabase

A, B, C = F.ITEM_A, F.ITEM_B, F.ITEM_C
J, O, L = F.JUDGMENT, F.OBJECTION, F.LEASE
ANGLE_TABLES = ("attachment_angle_maps", "attachment_angle_map_items", "attachment_angles")


# ── a scripted model behind the real agent ───────────────────────────────────


class Script:
    """Every model request, classified: ``map`` (a fresh run), ``retry`` (the output
    validator / schema sent it back) or ``repair`` (the coverage call)."""

    def __init__(self, handler: Callable[[dict, int], Any]) -> None:
        self.handler = handler
        self.calls: list[dict] = []

    async def fn(self, messages: list, info: AgentInfo) -> ModelResponse:
        last = messages[-1]
        parts = list(getattr(last, "parts", []))
        retries = [p for p in parts if isinstance(p, RetryPromptPart)]
        prompts = [p.content for p in parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)]
        kind = "retry" if retries else ("repair" if prompts and prompts[-1].startswith("Coverage check") else "map")
        every_prompt = [
            p.content for m in messages for p in getattr(m, "parts", [])
            if isinstance(p, UserPromptPart) and isinstance(p.content, str)
        ]
        call = {
            "kind": kind,
            "prompt": prompts[-1] if prompts else "",
            "retry": str(retries[0].content) if retries else "",
            "every_prompt": every_prompt,
            "instructions": getattr(last, "instructions", None),
            "settings": dict(info.model_settings or {}),
        }
        self.calls.append(call)
        out = self.handler(call, len(self.calls))
        if asyncio.iscoroutine(out):
            out = await out
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, out)])

    @property
    def kinds(self) -> list[str]:
        return [c["kind"] for c in self.calls]


def use(monkeypatch, handler: Callable[[dict, int], Any]) -> Script:
    script = Script(handler)
    monkeypatch.setattr(agent_mod, "get_agent_model", lambda slot: FunctionModel(script.fn))
    return script


def one(call: dict, n: int) -> dict:
    return F.good_one()


def two(call: dict, n: int) -> dict:
    return F.good_two()


def place_all(*docs: F.Doc) -> dict:
    """A valid map for ``docs`` as D1… whatever they are: every core section in context."""
    return {"client": dict(F.CLIENT), "angles": [],
            "context": [F.ref(i, s) for i, d in enumerate(docs, start=1) for s in d.core],
            "gaps": [], "no_angle": [], "unrelated_documents": []}


#: The real plan gate — the autouse fixture below stubs ``runner_mod._plan_allows``.
_REAL_PLAN_ALLOWS = runner_mod._plan_allows


async def _paid(supabase, user_id) -> bool:
    return True


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(runner_mod, "angle_map_enabled", lambda uid: True)
    monkeypatch.setattr(runner_mod, "_plan_allows", _paid)
    monkeypatch.setattr(runner_mod, "_codec_for", lambda supabase, uid: None)
    monkeypatch.setattr(runner_mod, "_GROUP_TASKS", {})


@pytest.fixture(autouse=True)
def ledger(monkeypatch) -> list[list[dict]]:
    """Every ``llm_calls`` flush (one list of rows per closed scope)."""
    flushed: list[list[dict]] = []

    def fake_flush(supabase: Any, buf: list[dict] | None) -> int:
        flushed.append([dict(r) for r in buf or []])
        return len(buf or [])

    monkeypatch.setattr(usage_sink, "_flush", fake_flush)
    return flushed


@pytest.fixture
def store_calls(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    real = store_mod.write_angle_map

    def spy(supabase, **kw):
        calls.append(copy.deepcopy(kw))
        return real(supabase, **kw)

    monkeypatch.setattr(store_mod, "write_angle_map", spy)
    return calls


def db_for(*docs: F.Doc, **kw: Any) -> FakeSupabase:
    return FakeSupabase(F.tables(*docs, **kw))


def expected_prompt(*docs: F.Doc, words: str = F.WORDS, big: tuple = (), unread: tuple = ()) -> str:
    """The user message for ``docs`` as D1…; ``unread``: the documents the map left
    out whose titles the model is told (angles-v5)."""
    big_ids = {d.item_id for d in big}
    pds = []
    for i, d in enumerate(docs, start=1):
        text = F.content(d, big=d.item_id in big_ids)
        smap = F.seg_map_row(d, text)
        pds.append(PromptDocument(
            index=i, title=d.title, doc_type=d.doc_type, content_md=text, summary=d.summary,
            documents_in_file=smap["documents_in_file"], sections=F.segment_rows(d, text),
            key_paragraphs=smap["key_paragraphs"],
        ))
    return build_user_message(documents=pds, user_words=F.user_words(words), unread=[d.title for d in unread])


#: Reasons a left-out id is never named to the model (deleted / not an attachment /
#: another user's).
NOT_UNREAD = ("skipped_not_found", "skipped_not_attachment", "skipped_other_user")


def unread_of(reason: str, *docs: F.Doc) -> tuple:
    return () if reason in NOT_UNREAD else docs


def stored_maps(db: FakeSupabase) -> list[tuple[str | None, list[str]]]:
    """``[(message_id, [member item ids by doc_index])]`` of every stored map."""
    out = []
    for m in db.tables["attachment_angle_maps"]:
        rows = sorted((r for r in db.tables["attachment_angle_map_items"] if r["map_id"] == m["map_id"]),
                      key=lambda r: r["doc_index"])
        out.append((m["message_id"], [r["item_id"] for r in rows]))
    return out


def lease(item_id: str, *, minutes: float = 5, status: str = "running") -> dict:
    return {"item_id": item_id, "helper": "segmenter", "status": status, "attempt": 1,
            "lease_until": (_runtime.utcnow() + timedelta(minutes=minutes)).isoformat(), "version": "x",
            "error": None}


def gate_joins(monkeypatch, n: int = 2) -> tuple[list[tuple], asyncio.Event]:
    """Wrap ``_group_task``: record ``(trigger, started_here, item_ids)`` per call and
    set the event once it has been called ``n`` times."""
    calls: list[tuple] = []
    ev = asyncio.Event()
    real = runner_mod._group_task

    def wrapper(supabase, group, trigger):
        task, mine = real(supabase, group, trigger)
        calls.append((trigger, mine, tuple(group.item_ids)))
        if len(calls) >= n:
            ev.set()
        return task, mine

    monkeypatch.setattr(runner_mod, "_group_task", wrapper)
    return calls, ev


# ── a message with one file (a group of one) ─────────────────────────────────


@pytest.mark.asyncio
async def test_one_file_maps_and_stores(monkeypatch, store_calls):
    db = db_for(J)
    script = use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.ok and res.error == "" and res.item_id == A
    assert (res.message_id, res.item_ids, res.excluded) == (F.MSG, [A], {})
    assert res.map_id and res.unplaced == []

    [call] = script.calls
    assert call["kind"] == "map"
    assert call["instructions"] == build_instructions(["judgment"]).strip()  # pydantic-ai trims the tail
    assert call["settings"].get("max_tokens") == agent_mod.MAX_OUTPUT_TOKENS == 65_536
    assert call["prompt"] == expected_prompt(J)

    assert res.output == AngleMap.model_validate(F.good_one()).model_dump()
    st = res.stats
    assert st["prompt_version"] == "angles-v5" and st["attempts"] == 1 and st["repair_used"] is False
    assert st["angle_count"] == 2 and st["kind_mix"] == {"strengthen": 1, "attack": 0, "answer": 1}
    assert st["client_confidence"] == "clear" and st["client_side"] == "respondent"
    assert (st["docs"], st["total_chars"], st["cross_doc_angles"]) == (1, len(F.content(J)), 0)
    assert (st["sections"], st["core_sections"], st["paragraphs"]) == (6, 3, 12)
    assert st["segment_prompt_versions"] == ["seg-v5.2"] and st["doc_types"] == ["judgment"]
    assert st["user_words_chars"] == len(F.user_words())
    assert st["input_tokens"] > 0 and st["output_tokens"] > 0 and st["requests"] == 1
    assert st["invalid_refs_dropped"] == 0 and st["unplaced_before_repair"] == 0 and st["unrelated_documents"] == 0

    assert stored_maps(db) == [(F.MSG, [A])]
    loaded = load_fresh_angle_map_for_item(db, A)
    assert loaded and loaded["map"]["map_id"] == res.map_id
    assert [a["kind"] for a in loaded["angles"]] == ["strengthen", "answer"]
    assert len(store_calls) == 1


@pytest.mark.asyncio
async def test_what_reaches_the_store_is_pinned(monkeypatch, store_calls):
    db = db_for(J, O)
    use(monkeypatch, two)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored"
    [c] = store_calls
    assert (c["message_id"], c["conversation_id"], c["user_id"]) == (F.MSG, F.CONVO, F.USER)
    assert c["items"] == [
        {"item_id": A, "doc_index": 1, "content_chars": len(F.content(J)), "segment_prompt_version": "seg-v5.2",
         "paragraph_rule_version": PARAGRAPH_RULE_VERSION, "segmented_at": F.SEG_CREATED_AT},
        {"item_id": B, "doc_index": 2, "content_chars": len(F.content(O)), "segment_prompt_version": "seg-v5.2",
         "paragraph_rule_version": PARAGRAPH_RULE_VERSION, "segmented_at": F.SEG_CREATED_AT},
    ]
    assert all(set(it) == set(ITEM_COLUMNS) for it in c["items"])

    mr = c["map_row"]
    assert set(mr) == set(MAP_COLUMNS)
    assert mr["prompt_version"] == "angles-v5" and mr["model"] == (res.stats["model"] or None)
    assert mr["client"] == F.CLIENT_TWO
    assert mr["context"] == ["D1.S1", "D2.S1"]
    assert mr["gaps"] == F.good_two()["gaps"] and mr["no_angle"] == [] and mr["unrelated_documents"] == []
    assert mr["input_tokens"] == res.stats["input_tokens"] > 0
    assert mr["output_tokens"] == res.stats["output_tokens"] > 0 and mr["reasoning_tokens"] == 0
    assert isinstance(mr["cost_usd"], float) and mr["cost_usd"] == res.stats["cost_usd"]
    assert isinstance(mr["duration_ms"], int) and mr["duration_ms"] >= 0

    assert [a["seq"] for a in c["angles"]] == [1, 2, 3]
    assert all(set(a) == set(ANGLE_COLUMNS) for a in c["angles"])
    assert c["angles"][0] == {"seq": 1, **Angle.model_validate(F.good_two()["angles"][0]).model_dump()}


# ── several files in one message: one call, one map ──────────────────────────


@pytest.mark.asyncio
async def test_a_message_with_two_files_is_mapped_in_one_call(monkeypatch):
    db = db_for(J, O)
    script = use(monkeypatch, two)
    res = await run_angle_mapper(db, B)  # the trigger is the second file
    assert res.outcome == "stored" and res.item_id == B
    assert res.item_ids == [A, B] and res.message_id == F.MSG  # D-order = upload order
    [call] = script.calls
    assert call["instructions"] == build_instructions(["judgment", "claim"]).strip()
    assert call["prompt"] == expected_prompt(J, O)
    st = res.stats
    assert (st["docs"], st["total_chars"]) == (2, len(F.content(J)) + len(F.content(O)))
    assert (st["sections"], st["core_sections"], st["paragraphs"]) == (10, 5, 19)
    assert st["cross_doc_angles"] == 2 and st["doc_types"] == ["judgment", "claim"]
    assert res.output["client"]["by_document"] == [{"document": "D2", "side": "claimant", "party": "المعترض"}]
    assert stored_maps(db) == [(F.MSG, [A, B])]
    assert load_fresh_angle_map_for_item(db, A)["map"]["map_id"] == res.map_id
    assert load_fresh_angle_map_for_item(db, B)["map"]["map_id"] == res.map_id


@pytest.mark.asyncio
async def test_upload_order_decides_the_d_numbers(monkeypatch):
    t = F.tables(J, O)
    for r in t["workspace_items"]:
        r["wi_seq"] = {A: 9, B: 2}[r["item_id"]]
    db = FakeSupabase(t)
    script = use(monkeypatch, lambda call, n: place_all(O, J))
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.item_ids == [B, A]
    assert script.calls[0]["prompt"] == expected_prompt(O, J)
    assert stored_maps(db) == [(F.MSG, [B, A])]


@pytest.mark.asyncio
async def test_a_document_of_another_matter_is_named_and_still_placed(monkeypatch, store_calls):
    db = db_for(J, O, L)
    script = use(monkeypatch, lambda call, n: F.good_three())
    res = await run_angle_mapper(db, C)
    assert res.outcome == "stored" and res.item_ids == [A, B, C] and script.kinds == ["map"]
    assert res.output["unrelated_documents"] == [{"document": "D3", "why": "عقد إيجار بين أطراف أخرى في نزاع آخر"}]
    assert res.stats["unrelated_documents"] == 1 and res.unplaced == []
    assert store_calls[0]["map_row"]["unrelated_documents"] == res.output["unrelated_documents"]
    assert script.calls[0]["instructions"] == build_instructions(["judgment", "claim", "contract"]).strip()


@pytest.mark.asyncio
async def test_a_file_with_no_message_link_is_a_group_of_one(monkeypatch, ledger):
    db = db_for(J, message=None)
    script = use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.message_id is None and res.item_ids == [A]
    assert script.calls[0]["prompt"] == expected_prompt(J, words="")
    assert stored_maps(db) == [(None, [A])]
    assert ledger[0][0]["message_id"] is None


@pytest.mark.asyncio
async def test_another_message_is_another_group(monkeypatch):
    t = F.tables(J)
    t["workspace_items"].append(F.item_row(O))
    t["attachment_segment_maps"].append(F.seg_map_row(O))
    t["attachment_segments"] += F.segment_rows(O)
    t["message_attachments"].append(F.link(O, "msg-2", created_at="2026-11-20T09:30:00+00:00"))
    db = FakeSupabase(t)
    use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.item_ids == [A] and res.excluded == {}


@pytest.mark.asyncio
async def test_a_file_resent_later_belongs_to_its_first_message(monkeypatch):
    """``message_attachments`` may link a file to a later message too: its group is
    its FIRST message's (the segmenter's bundles rule)."""
    t = F.tables(J, O)
    t["message_attachments"].append(F.link(J, "msg-later", created_at="2026-11-21T09:00:00+00:00"))
    db = FakeSupabase(t)
    use(monkeypatch, two)
    res = await run_angle_mapper(db, A)
    assert res.message_id == F.MSG and res.item_ids == [A, B]


# ── who is in the group ──────────────────────────────────────────────────────


def _rows(table: str, item_id: str, **kw: Any) -> Callable[[dict], None]:
    def change(t: dict) -> None:
        for r in t[table]:
            if r["item_id"] == item_id:
                r.update(kw)
    return change


def _drop(table: str, item_id: str) -> Callable[[dict], None]:
    return lambda t: t.update({table: [r for r in t[table] if r["item_id"] != item_id]})


def _both(*fns: Callable[[dict], None]) -> Callable[[dict], None]:
    def change(t: dict) -> None:
        for fn in fns:
            fn(t)
    return change


def _shift_offsets(item_id: str) -> Callable[[dict], None]:
    def change(t: dict) -> None:
        row = next(r for r in t["attachment_segments"] if r["item_id"] == item_id and r["seq"] == 2)
        row["char_start"] += 1
    return change


def _header_reasons(item_id: str) -> list[tuple[str, Callable[[dict], None]]]:
    """Reasons the HEADERS show (pointer row, length, segment-map header)."""
    return [
        ("skipped_no_map", _both(_drop("attachment_segment_maps", item_id), _drop("attachment_segments", item_id))),
        ("skipped_old_segments", _rows("attachment_segment_maps", item_id, prompt_version="seg-v5.1")),
        ("skipped_old_segments", _rows("attachment_segment_maps", item_id, prompt_version=None)),
        ("skipped_paragraph_rule", _rows("attachment_segment_maps", item_id, paragraph_rule_version="lines-1200-v1")),
        ("skipped_stale", _rows("attachment_segment_maps", item_id, content_chars=1)),
        ("skipped_stale", _rows("workspace_items", item_id, content_md="نص آخر تماماً")),
    ]


def _text_reasons(item_id: str) -> list[tuple[str, Callable[[dict], None]]]:
    """Reasons only the run that reads the text sees (segments, paragraphs)."""
    return [
        ("skipped_paragraph_rule", _shift_offsets(item_id)),  # the paragraphs no longer fit the stored offsets
        ("skipped_no_sections", _drop("attachment_segments", item_id)),
    ]


def _member_reasons(item_id: str) -> list[tuple[str, Callable[[dict], None]]]:
    return _header_reasons(item_id) + _text_reasons(item_id)


EXCLUDED = _member_reasons(B) + [
    ("skipped_not_found", _rows("workspace_items", B, deleted_at="2026-11-21T00:00:00+00:00")),
    ("skipped_not_found", _drop("workspace_items", B)),
    ("skipped_not_attachment", _rows("workspace_items", B, kind="agent_search")),
    ("skipped_other_user", _rows("workspace_items", B, user_id=F.OTHER_USER)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason, change", EXCLUDED)
async def test_a_member_without_a_usable_map_is_left_out(monkeypatch, reason, change):
    t = F.tables(J, O)
    change(t)
    db = FakeSupabase(t)
    script = use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored"
    assert res.item_ids == [A] and res.excluded == {B: reason}
    # the other file's text never reaches the model — only its title, when it is an unread file of the message
    assert script.calls[0]["prompt"] == expected_prompt(J, unread=unread_of(reason, O))
    assert O.lines[0] not in script.calls[0]["prompt"]
    assert stored_maps(db) == [(F.MSG, [A])]


@pytest.mark.asyncio
async def test_the_trigger_left_out_still_maps_the_rest(monkeypatch):
    """The member whose segmenter ends last runs the group — even when its own
    segmentation is unusable."""
    t = F.tables(J, O)
    _rows("attachment_segment_maps", A, prompt_version="seg-v5.1")(t)
    db = FakeSupabase(t)
    script = use(monkeypatch, lambda call, n: place_all(O))
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.item_ids == [B] and res.excluded == {A: "skipped_old_segments"}
    assert script.calls[0]["prompt"] == expected_prompt(O, unread=(J,))
    assert stored_maps(db) == [(F.MSG, [B])]


ALONE = _member_reasons(A) + [
    ("skipped_not_found", _rows("workspace_items", A, deleted_at="2026-11-21T00:00:00+00:00")),
    ("skipped_not_found", _drop("workspace_items", A)),
    ("skipped_not_attachment", _rows("workspace_items", A, kind="agent_search")),
    ("skipped_not_attachment", _rows("workspace_items", A, kind=None)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [F.MSG, None])
@pytest.mark.parametrize("reason, change", ALONE)
async def test_a_group_with_no_member_is_the_items_own_reason(monkeypatch, store_calls, ledger, reason, change,
                                                              message):
    t = F.tables(J, message=message)
    change(t)
    db = FakeSupabase(t)
    script = use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.outcome == reason and not res.ok
    assert script.calls == [] and store_calls == [] and ledger == [] and db.writes() == []
    assert res.output is None and res.stats["prompt_version"] == "angles-v5"


def test_the_size_guard_lets_the_largest_real_group_through():
    assert runner_mod.MAX_GROUP_CHARS == 400_000 > 165_000  # the largest real message group (prod, 11-22)


@pytest.mark.asyncio
async def test_a_group_over_the_size_guard_is_not_mapped(monkeypatch, store_calls, ledger):
    db = db_for(J, O)
    monkeypatch.setattr(runner_mod, "MAX_GROUP_CHARS", len(F.content(J)) + len(F.content(O)) - 1)
    script = use(monkeypatch, two)
    for res in (await run_angle_mapper(db, A), await run_angle_mapper(db, A, preview=True),
                await run_message_angle_map(db, F.MSG, [A, B])):
        assert res.outcome == "skipped_too_large" and res.item_ids == [A, B]
        assert res.stats["total_chars"] > runner_mod.MAX_GROUP_CHARS
    assert script.calls == [] and store_calls == [] and ledger == [] and db.writes() == []


@pytest.mark.asyncio
async def test_flag_off_is_skipped(monkeypatch, store_calls):
    seen: list = []
    monkeypatch.setattr(runner_mod, "angle_map_enabled", lambda uid: seen.append(uid) or False)
    db = db_for(J, O)
    script = use(monkeypatch, two)
    assert (await run_angle_mapper(db, A)).outcome == "skipped_disabled"
    assert (await run_message_angle_map(db, F.MSG, [A, B])).outcome == "skipped_disabled"
    assert seen == [F.USER, F.USER] and script.calls == [] and store_calls == []


@pytest.mark.asyncio
async def test_free_plan_is_skipped(monkeypatch, store_calls, ledger):
    """Owner 2026-11-23: free users get no map — no model call, nothing stored, no
    ledger row, on both entry points; the plan is asked for the group's user."""
    asked: list = []

    async def free(supabase, user_id):
        asked.append(user_id)
        return False

    monkeypatch.setattr(runner_mod, "_plan_allows", free)
    db = db_for(J, O)
    script = use(monkeypatch, two)
    assert (await run_angle_mapper(db, A)).outcome == "skipped_free_plan"
    assert (await run_message_angle_map(db, F.MSG, [A, B])).outcome == "skipped_free_plan"
    assert asked == [F.USER, F.USER]
    assert script.calls == [] and store_calls == [] and ledger == [] and db.writes() == []


@pytest.mark.asyncio
async def test_flag_off_skips_before_the_plan_read(monkeypatch, store_calls):
    """The flag is checked first: with it off the plan is never read."""
    async def boom(supabase, user_id):
        raise AssertionError("plan read with the flag off")

    monkeypatch.setattr(runner_mod, "angle_map_enabled", lambda uid: False)
    monkeypatch.setattr(runner_mod, "_plan_allows", boom)
    db = db_for(J, O)
    use(monkeypatch, two)
    assert (await run_angle_mapper(db, A)).outcome == "skipped_disabled"
    assert (await run_message_angle_map(db, F.MSG, [A, B])).outcome == "skipped_disabled"


@pytest.mark.asyncio
async def test_preview_ignores_the_plan(monkeypatch):
    """A preview skips the plan gate like the flag gate (the preview tool maps any
    user's files and stores / bills nothing)."""
    async def free(supabase, user_id):
        raise AssertionError("plan read in a preview")

    monkeypatch.setattr(runner_mod, "_plan_allows", free)
    db = db_for(J, O)
    use(monkeypatch, two)
    res = await run_message_angle_map(db, F.MSG, [A, B], preview=True)
    assert res.outcome == "preview"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row, allowed",
    [
        ({"effective_plan_id": "pro", "locked": False}, True),
        ({"effective_plan_id": "max", "locked": False}, True),
        ({"effective_plan_id": "marketing_lawyer_v2", "locked": False}, True),  # a trial maps
        ({"effective_plan_id": "dev", "locked": False}, True),
        ({"effective_plan_id": "free", "locked": False}, False),
        ({"plan_id": "pro", "effective_plan_id": "free", "locked": False}, False),  # expired → free
        ({"effective_plan_id": None, "locked": True}, False),
        (None, False),  # no subscription row
    ],
)
async def test_plan_allows_reads_the_effective_plan(monkeypatch, row, allowed):
    import shared.quota as quota

    async def state(supabase, user_id):
        assert user_id == F.USER
        return row

    monkeypatch.setattr(quota, "_quota_state", state)
    assert await _REAL_PLAN_ALLOWS(object(), F.USER) is allowed


@pytest.mark.asyncio
async def test_plan_allows_failed_read_is_no_map(monkeypatch):
    import shared.quota as quota

    async def broken(supabase, user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(quota, "_quota_state", broken)
    assert await _REAL_PLAN_ALLOWS(object(), F.USER) is False


# ── the trigger rule: pending, joined, fresh, late ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("probe", ["lease", "inflight_segmenter", "inflight_summary"])
async def test_a_candidate_still_being_segmented_makes_the_group_pending(monkeypatch, store_calls, ledger, probe):
    extra = {"helper_runs": [lease(B)]} if probe == "lease" else {}
    db = db_for(J, O, unmapped=(O,), big=(O,), **extra)
    if probe != "lease":
        helper = probe.split("_", 1)[1]
        monkeypatch.setitem(_runtime._INFLIGHT, (helper, B), asyncio.get_running_loop().create_future())
    script = use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "skipped_group_pending" and not res.ok and res.error == ""
    assert res.stats["pending"] == [B] and res.item_ids == [A] and res.excluded == {B: "skipped_no_map"}
    assert script.calls == [] and store_calls == [] and ledger == [] and db.writes() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("helper_runs, fail", [
    ([lease(B, minutes=-1)], False),            # the lease ran out: that segmenter is dead
    ([lease(B, status="done")], False),
    ([lease(B, status="failed")], False),
    ([lease(C)], False),                       # another item's lease
    ([lease(B)], True),                        # the probe read failed = not pending
])
async def test_a_candidate_that_is_not_running_is_simply_left_out(monkeypatch, helper_runs, fail):
    db = db_for(J, O, unmapped=(O,), big=(O,), helper_runs=helper_runs)
    if fail:
        db.fail.add("helper_runs")
    use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.item_ids == [A] and res.excluded == {B: "skipped_no_map"}


@pytest.mark.asyncio
async def test_a_small_file_the_segmenter_never_maps_is_not_pending(monkeypatch):
    """Only a segmenter CANDIDATE (over 5,000 chars, or in a qualifying bundle) can
    hold the group back; a small file alone travels whole and never gets a map."""
    db = db_for(J, O, unmapped=(O,), helper_runs=[lease(B)])
    use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.excluded == {B: "skipped_no_map"}


@pytest.mark.asyncio
async def test_a_fresh_map_is_never_redone(monkeypatch, ledger):
    db = db_for(J, O)
    script = use(monkeypatch, two)
    first = await run_angle_mapper(db, A)
    assert first.outcome == "stored"
    for item in (A, B, A):
        again = await run_angle_mapper(db, item)
        assert again.outcome == "skipped_fresh" and not again.ok and again.item_ids == [A, B]
    assert len(script.calls) == 1 and len(ledger) == 1
    assert stored_maps(db) == [(F.MSG, [A, B])]


@pytest.mark.asyncio
async def test_members_starting_together_join_one_run(monkeypatch, store_calls):
    db = db_for(J, O)
    calls, both_in = gate_joins(monkeypatch)

    async def handler(call, n):
        await asyncio.wait_for(both_in.wait(), 5)
        return F.good_two()

    script = use(monkeypatch, handler)
    ra, rb = await asyncio.gather(run_angle_mapper(db, A), run_angle_mapper(db, B))
    assert len(script.calls) == 1 and len(store_calls) == 1
    assert sorted([ra.outcome, rb.outcome]) == ["joined_group", "stored"]
    starter, joiner = (ra, rb) if ra.outcome == "stored" else (rb, ra)
    assert sorted([starter.item_id, joiner.item_id]) == [A, B]  # each keeps its own trigger
    assert joiner.error == "" and joiner.map_id == starter.map_id and joiner.item_ids == [A, B]
    assert joiner.output == starter.output and not joiner.ok
    assert [mine for _, mine, _ in calls] == [True, False]
    assert stored_maps(db) == [(F.MSG, [A, B])]
    assert runner_mod._GROUP_TASKS == {}  # a finished run is forgotten


@pytest.mark.asyncio
async def test_a_joined_member_of_a_failed_run_is_not_the_failure(monkeypatch):
    db = db_for(J, O)
    _calls, both_in = gate_joins(monkeypatch)

    async def handler(call, n):
        await asyncio.wait_for(both_in.wait(), 5)
        raise RuntimeError("provider down")

    script = use(monkeypatch, handler)
    ra, rb = await asyncio.gather(run_angle_mapper(db, A), run_angle_mapper(db, B))
    assert sorted([ra.outcome, rb.outcome]) == ["failed_llm", "joined_group"]
    joiner = ra if ra.outcome == "joined_group" else rb
    assert joiner.error == "group:failed_llm"
    assert len(script.calls) == 2  # the starter's two attempts — nobody else's
    assert stored_maps(db) == []


@pytest.mark.asyncio
async def test_a_failed_group_is_mapped_by_the_next_members_run(monkeypatch, store_calls):
    """No sweep: a failed group run stays unmapped until a member's segmenter ends
    again (its run finds no fresh map) — any member, not only the one that failed."""
    db = db_for(J, O)

    def handler(call, n):
        if n <= 2:  # the first run's two attempts
            raise RuntimeError("provider down")
        return F.good_two()

    script = use(monkeypatch, handler)
    failed = await run_angle_mapper(db, A)
    assert failed.outcome == "failed_llm" and stored_maps(db) == []
    assert load_fresh_angle_map_for_item(db, A) is None
    again = await run_angle_mapper(db, B)  # B's segmenter ended again
    assert again.outcome == "stored" and again.item_ids == [A, B] and len(script.calls) == 3
    assert stored_maps(db) == [(F.MSG, [A, B])]


@pytest.mark.asyncio
async def test_a_late_member_makes_the_map_stale_and_its_run_remaps(monkeypatch, ledger):
    db = db_for(J, O, unmapped=(O,))
    script = use(monkeypatch, lambda call, n: F.good_one() if n == 1 else F.good_two())
    first = await run_angle_mapper(db, A)
    assert first.outcome == "stored" and first.item_ids == [A] and first.excluded == {B: "skipped_no_map"}
    assert load_fresh_angle_map_for_item(db, A) is not None

    for k, v in F.map_tables(O).items():  # B's segment map lands
        db.tables[k] += v
    assert load_fresh_angle_map_for_item(db, A) is None  # the frame's load: stale
    late = await run_angle_mapper(db, B)
    assert late.outcome == "stored" and late.item_ids == [A, B] and late.excluded == {}
    assert len(script.calls) == 2 and script.calls[1]["prompt"] == expected_prompt(J, O)
    assert stored_maps(db) == [(F.MSG, [A, B])]  # one map: the old one replaced
    assert load_fresh_angle_map_for_item(db, A)["map"]["map_id"] == late.map_id
    assert (await run_angle_mapper(db, A)).outcome == "skipped_fresh"


@pytest.mark.asyncio
async def test_a_member_that_joined_a_run_of_an_older_group_runs_once_more(monkeypatch):
    """B's segment map lands while A's run (mapping A alone) is going; B joins it,
    sees it stored another member set, resolves again and maps the whole group."""
    db = db_for(J, O, unmapped=(O,))
    calls, joined = gate_joins(monkeypatch)
    b_may_start = asyncio.Event()

    async def handler(call, n):
        if n == 1:
            for k, v in F.map_tables(O).items():
                db.tables[k] += v
            b_may_start.set()
            await asyncio.wait_for(joined.wait(), 5)
            return F.good_one()
        return F.good_two()

    script = use(monkeypatch, handler)
    ta = asyncio.create_task(run_angle_mapper(db, A))
    await asyncio.wait_for(b_may_start.wait(), 5)
    tb = asyncio.create_task(run_angle_mapper(db, B))
    ra, rb = await asyncio.gather(ta, tb)
    assert ra.outcome == "stored" and ra.item_ids == [A]
    assert rb.outcome == "stored" and rb.item_ids == [A, B] and rb.map_id != ra.map_id
    assert calls == [(A, True, (A,)), (B, False, (A, B)), (B, True, (A, B))]
    assert len(script.calls) == 2
    assert stored_maps(db) == [(F.MSG, [A, B])]


@pytest.mark.asyncio
async def test_a_joiner_with_an_older_view_that_finds_the_group_mapped_stops(monkeypatch):
    """B resolved its group before A's segment map landed (it saw {B}); A's run maps
    {A, B}; B joins it, sees another member set stored, resolves again, finds the
    stored map fresh → ``joined_group``, no second call."""
    import threading

    db = db_for(J, O, unmapped=(J,))
    b_resolved, a_started = threading.Event(), threading.Event()
    real_fresh, real_task = runner_mod._stored_fresh, runner_mod._group_task

    def fresh(supabase, group):
        if group.item_ids == [B] and not b_resolved.is_set():
            b_resolved.set()
            assert a_started.wait(5)  # hold B's first view until A's run is going
        return real_fresh(supabase, group)

    def task(supabase, group, trigger):
        out = real_task(supabase, group, trigger)
        if trigger == A:
            a_started.set()
        return out

    monkeypatch.setattr(runner_mod, "_stored_fresh", fresh)
    monkeypatch.setattr(runner_mod, "_group_task", task)
    calls, both_in = gate_joins(monkeypatch)  # wraps ``task``

    async def handler(call, n):
        await asyncio.wait_for(both_in.wait(), 5)
        return F.good_two()

    script = use(monkeypatch, handler)
    tb = asyncio.create_task(run_angle_mapper(db, B))
    assert await asyncio.to_thread(b_resolved.wait, 5)
    for k, v in F.map_tables(J).items():  # A's segment map lands
        db.tables[k] += v
    ra = await run_angle_mapper(db, A)
    rb = await tb
    assert ra.outcome == "stored" and ra.item_ids == [A, B]
    assert rb.outcome == "joined_group" and rb.error == "" and rb.map_id == ra.map_id
    assert calls == [(A, True, (A, B)), (B, False, (B,))]  # B never started a run of its own
    assert len(script.calls) == 1
    assert stored_maps(db) == [(F.MSG, [A, B])]


# ── one definition of a member: the frame's load and the runner agree ────────


def _retype(item_id: str, doc_type: str) -> Callable[[dict], None]:
    """The item's ``doc_type`` changed after it was segmented (another paragraph max)."""
    return _rows("workspace_items", item_id, metadata={"doc_type": doc_type})


#: Every way B can be left out of the group that its HEADERS show.
AGREE = _header_reasons(B) + EXCLUDED[len(_member_reasons(B)):]
#: Ways only its TEXT shows — the paragraph-offset drift (shifted offsets, and a real
#: one: the big objection retyped as a judgment, whose 500-char paragraph max splits
#: its ~730-char lines) and a map without sections.
TEXT_LEVEL = _text_reasons(B) + [("skipped_paragraph_rule", _retype(B, "judgment"))]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason, change", AGREE)
async def test_a_member_left_out_later_is_left_out_by_the_frame_load_too(monkeypatch, ledger, reason, change):
    """B was mapped with A; then B stops being usable. The frame's ``load`` and the
    runner see the SAME group ({A}): the map is stale for both, ONE run re-maps A
    alone, and from then on the load reports it fresh and the runner answers
    ``skipped_fresh`` — never the loop "load: stale / runner: fresh"."""
    from agents.memory.angle_mapper.group import load_group, member_versions

    db = FakeSupabase(F.tables(J, O, big=(O,)))
    script = use(monkeypatch, lambda call, n: F.good_two() if n == 1 else F.good_one())
    first = await run_angle_mapper(db, A)
    assert first.outcome == "stored" and first.item_ids == [A, B]
    assert load_fresh_angle_map_for_item(db, A)["map"]["map_id"] == first.map_id

    t = db.tables
    change(t)
    group = load_group(db, F.MSG, [A, B], user_id=F.USER)
    assert group.item_ids == [A] and group.excluded == {B: reason}
    # one definition: the freshness check's members ARE the runner's group
    assert member_versions(db, A, user_id=F.USER) == group.versions() == member_versions(db, A)
    assert load_fresh_angle_map_for_item(db, A) is None  # stale for the frame …
    remap = await run_angle_mapper(db, A)  # … and for the runner: one re-map
    assert remap.outcome == "stored" and remap.item_ids == [A] and remap.excluded == {B: reason}
    assert script.calls[1]["prompt"] == expected_prompt(J, unread=unread_of(reason, O))
    assert stored_maps(db) == [(F.MSG, [A])]

    fresh = load_fresh_angle_map_for_item(db, A)
    assert fresh is not None and fresh["map"]["map_id"] == remap.map_id  # the frame: ready
    for _ in range(2):
        again = await run_angle_mapper(db, A)
        assert again.outcome == "skipped_fresh" and again.excluded == {B: reason}
    assert len(script.calls) == 2 and len(ledger) == 2
    if reason not in ("skipped_not_found", "skipped_not_attachment", "skipped_other_user"):
        # B itself is in no map (``None`` for it); its own run finds the group fresh
        assert load_fresh_angle_map_for_item(db, B) is None
        assert (await run_angle_mapper(db, B)).outcome == "skipped_fresh"
        assert len(script.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("reason, change", TEXT_LEVEL)
async def test_what_only_a_members_text_shows_leaves_its_map_fresh(monkeypatch, ledger, reason, change):
    """B was mapped with A; then only B's TEXT shows a problem (its header versions
    hold). Freshness is decided from headers: the map stays fresh for the frame AND
    the runner (``skipped_fresh`` for both members) — no segments read, no call, no
    loop. A run that maps the group anyway (``load_group``) would leave B out."""
    from agents.memory.angle_mapper.group import load_group

    db = FakeSupabase(F.tables(J, O, big=(O,)))
    script = use(monkeypatch, two)
    first = await run_angle_mapper(db, A)
    assert first.outcome == "stored" and first.item_ids == [A, B]
    change(db.tables)
    n = len(db.selects)
    assert load_fresh_angle_map_for_item(db, A)["map"]["map_id"] == first.map_id
    for item in (A, B, A):
        again = await run_angle_mapper(db, item)
        assert again.outcome == "skipped_fresh" and again.item_ids == [A, B]
    assert not any(t == "attachment_segments" for t, _cols in db.selects[n:])
    assert len(script.calls) == 1 and len(ledger) == 1
    group = load_group(db, F.MSG, [A, B], user_id=F.USER)
    assert group.item_ids == [A] and group.excluded == {B: reason}


@pytest.mark.asyncio
async def test_a_member_left_out_from_the_start_never_makes_the_map_stale(monkeypatch):
    db = FakeSupabase(F.tables(J, O))
    _shift_offsets(B)(db.tables)
    script = use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.item_ids == [A] and res.excluded == {B: "skipped_paragraph_rule"}
    # stored WITH the map, at the versions it was judged on — the headers then agree
    [stored] = db.tables["attachment_angle_maps"]
    assert stored["left_out"] == [{
        "item_id": B, "reason": "skipped_paragraph_rule", "content_chars": len(F.content(O)),
        "segment_prompt_version": "seg-v5.2", "paragraph_rule_version": PARAGRAPH_RULE_VERSION,
        "segmented_at": F.SEG_CREATED_AT,
    }]
    assert res.stats["left_out"] == 1
    assert load_fresh_angle_map_for_item(db, A)["map"]["map_id"] == res.map_id
    n = len(db.selects)
    assert (await run_angle_mapper(db, A)).outcome == "skipped_fresh"
    assert (await run_angle_mapper(db, B)).outcome == "skipped_fresh"  # B's own run: fresh, B in no map
    assert load_fresh_angle_map_for_item(db, B) is None
    assert not any(t == "attachment_segments" for t, _cols in db.selects[n:])
    # B is segmented again (offsets right): its arrival makes the map stale — one re-map
    db.tables["attachment_segments"] = [r for r in db.tables["attachment_segments"] if r["item_id"] != B]
    db.tables["attachment_segment_maps"] = [r for r in db.tables["attachment_segment_maps"] if r["item_id"] != B]
    for k, v in F.map_tables(O).items():
        db.tables[k] += [{**r, "created_at": "2026-11-22T10:00:00+00:00"} if k == "attachment_segment_maps" else r
                         for r in v]
    assert load_fresh_angle_map_for_item(db, A) is None
    use(monkeypatch, two)
    late = await run_angle_mapper(db, B)
    assert late.outcome == "stored" and late.item_ids == [A, B]
    assert load_fresh_angle_map_for_item(db, B)["map"]["map_id"] == late.map_id
    assert len(script.calls) == 1  # the first script only ever mapped A


# ── headers first: the text is read once, by the run that calls the model ────


def _spy_text_loads(monkeypatch) -> list[tuple[str, ...]]:
    """Every text load of a group run (``group_from_state``), by its item ids."""
    loads: list[tuple[str, ...]] = []
    real = runner_mod.group_from_state

    def spy(supabase, state):
        loads.append(tuple(state.item_ids))
        return real(supabase, state)

    monkeypatch.setattr(runner_mod, "group_from_state", spy)
    return loads


@pytest.mark.asyncio
async def test_pending_and_fresh_are_decided_from_headers(monkeypatch):
    loads = _spy_text_loads(monkeypatch)
    script = use(monkeypatch, two)
    db = db_for(J, O, unmapped=(O,), big=(O,), helper_runs=[lease(B)])
    assert (await run_angle_mapper(db, A)).outcome == "skipped_group_pending"
    assert loads == [] and not any(t == "attachment_segments" for t, _cols in db.selects)

    db = db_for(J, O)
    assert (await run_angle_mapper(db, A)).outcome == "stored"
    assert loads == [(A, B)]
    n = len(db.selects)
    for item in (A, B):
        assert (await run_angle_mapper(db, item)).outcome == "skipped_fresh"
    assert loads == [(A, B)] and len(script.calls) == 1
    assert not any(t == "attachment_segments" for t, _cols in db.selects[n:])


@pytest.mark.asyncio
async def test_members_that_join_one_run_read_the_text_once(monkeypatch):
    db = db_for(J, O)
    loads = _spy_text_loads(monkeypatch)
    _calls, both_in = gate_joins(monkeypatch)

    async def handler(call, n):
        await asyncio.wait_for(both_in.wait(), 5)
        return F.good_two()

    use(monkeypatch, handler)
    ra, rb = await asyncio.gather(run_angle_mapper(db, A), run_angle_mapper(db, B))
    assert sorted([ra.outcome, rb.outcome]) == ["joined_group", "stored"]
    assert loads == [(A, B)]


@pytest.mark.asyncio
async def test_a_group_over_the_size_guard_is_refused_before_its_text_is_read(monkeypatch):
    loads = _spy_text_loads(monkeypatch)
    db = db_for(J, O)
    monkeypatch.setattr(runner_mod, "MAX_GROUP_CHARS", len(F.content(J)))
    use(monkeypatch, two)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "skipped_too_large" and loads == []


@pytest.fixture
def files_read():
    """Files in their own table (``USER_FILES_READ_ENABLED``): lengths from
    ``user_files.content_chars``, text through the overlay."""
    from shared.files import flags as files_flags

    files_flags.reset_probe()
    with files_flags.override(read=True):
        yield
    files_flags.reset_probe()


def _text_reads(db: FakeSupabase, since: int = 0) -> list[tuple[str, str]]:
    """Selects that read a file's text, or segments (which carry it)."""
    return [
        (t, cols) for t, cols in db.selects[since:]
        if t == "attachment_segments"
        or (t in ("workspace_items", "user_files") and (cols.strip() == "*" or "content_md" in cols))
    ]


@pytest.mark.asyncio
async def test_with_files_in_their_table_pending_and_fresh_read_no_text(monkeypatch, files_read):
    G, H = F.ITEM_G, F.ITEM_H
    db = FakeSupabase(F.file_tables((J, G, F.FILE_J), (O, H, F.FILE_O)))
    script = use(monkeypatch, two)
    res = await run_angle_mapper(db, H)
    assert res.outcome == "stored" and res.item_ids == [G, H]
    assert script.calls[0]["prompt"] == expected_prompt(J, O)
    assert _text_reads(db)  # the run that called the model read the text …
    n = len(db.selects)
    assert load_fresh_angle_map_for_item(db, G)["map"]["map_id"] == res.map_id
    assert (await run_angle_mapper(db, G)).outcome == "skipped_fresh"
    assert _text_reads(db, n) == []  # … the frame's load and the fresh check read none
    assert ("user_files", "file_id, content_chars") in db.selects[n:]  # the length, without the text

    db = FakeSupabase(F.file_tables((J, G, F.FILE_J), (O, H, F.FILE_O), unmapped=(O,), big=(O,),
                                    helper_runs=[lease(F.FILE_O)]))
    assert (await run_angle_mapper(db, G)).outcome == "skipped_group_pending"
    assert _text_reads(db) == []


# ── files («الملفات»): segment maps hang on the FILE, the angle map on the item ──


@pytest.mark.asyncio
async def test_library_files_are_mapped_from_their_files_segment_maps(monkeypatch, store_calls):
    """G and H point at files (a new item id each, migration 227): every segment map,
    segment and lease is under a FILE id. Read by item id they would be
    ``skipped_no_map`` and silently drop out of the group."""
    G, H = F.ITEM_G, F.ITEM_H
    db = FakeSupabase(F.file_tables((J, G, F.FILE_J), (O, H, F.FILE_O)))
    script = use(monkeypatch, two)
    res = await run_angle_mapper(db, H)
    assert res.outcome == "stored" and (res.message_id, res.item_ids, res.excluded) == (F.MSG_2, [G, H], {})
    assert script.calls[0]["prompt"] == expected_prompt(J, O)
    [c] = store_calls
    assert (c["message_id"], c["conversation_id"]) == (F.MSG_2, F.CONVO_2)
    assert c["items"][1] == {  # the conversation's item, the file's segment-map versions
        "item_id": H, "doc_index": 2, "content_chars": len(F.content(O)), "segment_prompt_version": "seg-v5.2",
        "paragraph_rule_version": PARAGRAPH_RULE_VERSION, "segmented_at": F.SEG_CREATED_AT,
    }
    assert stored_maps(db) == [(F.MSG_2, [G, H])]
    assert load_fresh_angle_map_for_item(db, G)["map"]["map_id"] == res.map_id  # the frame's load
    assert (await run_angle_mapper(db, G)).outcome == "skipped_fresh"

    # the FILE is segmented again: stale for its items, one re-map
    seg = next(r for r in db.tables["attachment_segment_maps"] if r["item_id"] == F.FILE_O)
    seg["created_at"] = "2026-11-22T10:00:00+00:00"
    assert load_fresh_angle_map_for_item(db, G) is None
    again = await run_angle_mapper(db, H)
    assert again.outcome == "stored" and len(script.calls) == 2
    assert store_calls[1]["items"][1]["segmented_at"] == "2026-11-22T10:00:00+00:00"
    assert stored_maps(db) == [(F.MSG_2, [G, H])]


@pytest.mark.asyncio
async def test_a_library_candidate_still_segmenting_holds_its_group_through_its_files_lease(monkeypatch):
    G, H = F.ITEM_G, F.ITEM_H
    db = FakeSupabase(F.file_tables((J, G, F.FILE_J), (O, H, F.FILE_O), unmapped=(O,), big=(O,),
                                    helper_runs=[lease(F.FILE_O)]))  # the segmenter's lease: on the file
    script = use(monkeypatch, one)
    res = await run_angle_mapper(db, G)
    assert res.outcome == "skipped_group_pending" and res.stats["pending"] == [H]
    assert res.excluded == {H: "skipped_no_map"} and script.calls == []


@pytest.mark.asyncio
async def test_the_same_file_in_two_conversations_gets_two_maps(monkeypatch):
    """The angle map belongs to a conversation's use of a file: the library copy (G)
    is mapped in its own message, and the first conversation's map stays."""
    G = F.ITEM_G
    t = F.tables(J)
    t["workspace_items"][0]["file_id"] = A
    t["workspace_items"].append(F.item_row(J, item_id=G, conversation_id=F.CONVO_2, file_id=A,
                                           created_at=F.MSG_2_AT))
    t["message_attachments"].append({"message_id": F.MSG_2, "document_id": G, "created_at": F.MSG_2_AT})
    t["messages"].append({**F.message_row(F.MSG_2, "سؤال آخر عن الحكم", F.MSG_2_AT), "conversation_id": F.CONVO_2})
    db = FakeSupabase(t)
    use(monkeypatch, one)
    ra = await run_angle_mapper(db, A)
    rg = await run_angle_mapper(db, G)
    assert ra.outcome == rg.outcome == "stored" and ra.map_id != rg.map_id
    assert sorted(stored_maps(db), key=str) == sorted([(F.MSG, [A]), (F.MSG_2, [G])], key=str)
    assert load_fresh_angle_map_for_item(db, A)["map"]["map_id"] == ra.map_id
    assert load_fresh_angle_map_for_item(db, G)["map"]["map_id"] == rg.map_id
    assert (await run_angle_mapper(db, A)).outcome == "skipped_fresh"


def test_no_segmenter_table_is_read_by_item_id_in_the_package():
    """The angle mapper reads the segmenter's tables only through the segmenter's
    own item → file mapping: its loaders, or ``group.segment_headers``."""
    import pathlib
    import re

    pkg = pathlib.Path(runner_mod.__file__).parent
    for path in pkg.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        for table in ("attachment_segment_maps", "attachment_segments", "attachment_pages", "helper_runs"):
            assert not re.search(rf"""\.table\(\s*["']{table}["']""", src), (path.name, table)
        if "SEGMENT_MAPS_TABLE" in src:
            assert path.name == "group.py"
            calls = re.findall(r"\.table\(SEGMENT_MAPS_TABLE\)", src)
            assert len(calls) == 1  # in segment_headers, after _file_keys
            body = src[src.index("def segment_headers"):src.index("def _header_skip")]
            assert "_file_keys(" in body and ".table(SEGMENT_MAPS_TABLE)" in body


# ── refs: qualified, sent back once, then dropped and counted ────────────────


@pytest.mark.asyncio
async def test_bare_refs_in_a_group_of_one_are_qualified_without_a_retry(monkeypatch, store_calls):
    def handler(call, n):
        return F.good_one(angles=[F.angle(sections=[3, "S4"]), F.angle("answer", ["s2"])], context=["S1", 5],
                          gaps=[{"kind": "missing", "sections": ["S2"], "issue": "x", "ocr_suspect": False}])

    db = db_for(J)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.kinds == ["map"]
    assert [a["sections"] for a in res.output["angles"]] == [["D1.S3", "D1.S4"], ["D1.S2"]]
    assert res.output["context"] == ["D1.S1", "D1.S5"] and res.stats["invalid_refs_dropped"] == 0
    assert store_calls[0]["angles"][0]["sections"] == ["D1.S3", "D1.S4"]


@pytest.mark.asyncio
async def test_unknown_refs_are_sent_back_once(monkeypatch, ledger):
    def handler(call, n):
        if call["kind"] == "map":
            return F.good_two(angles=[F.angle(sections=["D2.S2", "D1.S41"]), F.angle("answer", ["S2"]),
                                      F.angle("attack", ["D1.S5", "D2.S3"])], context=["D1.S1", "D3.S1"])
        return F.good_two()

    db = db_for(J, O)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.kinds == ["map", "retry"]
    retry = script.calls[1]["retry"]
    assert retry.startswith("These refs are not sections (or documents) of this message: "
                            "angles[1].sections: D1.S41; angles[2].sections: S2; context: D3.S1.")
    assert "(D1·S1–D1·S6, D2·S1–D2·S4)" in retry and "only the documents D1–D2" in retry
    assert res.stats["attempts"] == 1 and res.stats["requests"] == 2 and res.stats["invalid_refs_dropped"] == 0
    assert res.output["angles"][0]["sections"] == ["D2.S2", "D1.S3", "D1.S4"]
    [rows] = ledger
    assert len(rows) == 1 and rows[0]["requests"] == 2  # one run, both requests billed


@pytest.mark.asyncio
async def test_refs_still_unknown_after_the_retry_are_dropped_and_counted(monkeypatch, store_calls):
    def handler(call, n):
        return F.good_two(
            angles=[F.angle(sections=["D2.S2", "D1.S41", "D1.S3", "D1.S41"]), F.angle("answer", ["D1.S2", "S5"]),
                    F.angle("attack", ["D1.S5", "D2.S3"])],
            context=["D1.S1", "D2.S1", "D9.S1"],
            gaps=[{"kind": "missing", "sections": ["D1.S2", "D2.S77"], "issue": "x", "ocr_suspect": True}],
            no_angle=[{"section": "D2.S88", "why": "لا شيء"}, {"section": "D1.S6", "why": "توقيع"}],
            client={**F.CLIENT_TWO, "by_document": [*F.CLIENT_TWO["by_document"], {"document": "D4"}]},
            unrelated_documents=[{"document": "D7", "why": "x"}],
        )

    db = db_for(J, O)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored"
    assert script.kinds == ["map", "retry"]  # one retry, never a second
    # D1.S41 ×2, S5, D9.S1, D2.S77, D2.S88, by_document D4, unrelated D7
    assert res.stats["invalid_refs_dropped"] == 8
    assert res.output["angles"][0]["sections"] == ["D2.S2", "D1.S3"]
    mr = store_calls[0]["map_row"]
    assert mr["context"] == ["D1.S1", "D2.S1"] and mr["gaps"][0]["sections"] == ["D1.S2"]
    assert mr["no_angle"] == [{"section": "D1.S6", "why": "توقيع"}]
    assert mr["client"]["by_document"] == F.CLIENT_TWO["by_document"] and mr["unrelated_documents"] == []
    assert store_calls[0]["angles"][1]["sections"] == ["D1.S2"]


# ── every angle names a section: sent back once, then dropped and counted ────


@pytest.mark.asyncio
async def test_an_angle_without_sections_is_sent_back_once(monkeypatch, store_calls):
    def handler(call, n):
        if call["kind"] == "map":
            return F.good_one(angles=[F.angle(sections=[]), F.angle("answer", ["D1.S2"])])
        return F.good_one()

    db = db_for(J)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.kinds == ["map", "retry"]
    assert no_sections_message([1]) in script.calls[1]["retry"]
    assert "These refs are not sections" not in script.calls[1]["retry"]
    assert res.stats["angles_without_sections"] == 0 and len(res.output["angles"]) == 2


@pytest.mark.asyncio
async def test_angles_still_without_sections_after_the_retry_are_dropped_and_counted(monkeypatch, store_calls):
    def handler(call, n):  # the same answer to the map and to the retry
        return F.good_one(angles=[F.angle(sections=[]), F.angle("strengthen", ["D1.S3", "D1.S4"]),
                                  F.angle("answer", ["D1.S41"]), F.angle("answer", ["D1.S2"])])

    db = db_for(J)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.kinds == ["map", "retry"]  # one retry, never a second
    retry = script.calls[1]["retry"]
    assert "angles[3].sections: D1.S41" in retry and no_sections_message([1, 3]) in retry
    st = res.stats
    assert st["angles_without_sections"] == 2 and st["invalid_refs_dropped"] == 1 and st["angle_count"] == 2
    assert [a["sections"] for a in res.output["angles"]] == [["D1.S3", "D1.S4"], ["D1.S2"]]
    assert [(a["seq"], a["sections"]) for a in store_calls[0]["angles"]] == [(1, ["D1.S3", "D1.S4"]), (2, ["D1.S2"])]
    assert res.unplaced == []


@pytest.mark.asyncio
async def test_a_map_whose_every_angle_has_no_section_is_failed_invalid(monkeypatch, store_calls, ledger):
    """The real preview of 2026-11-22: 9 angles, every ``sections`` empty."""
    def handler(call, n):
        return F.good_one(angles=[F.angle(sections=[], title=f"وجه {i}") for i in range(9)], context=["D1.S1"])

    db = db_for(J)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "failed_invalid" and res.error == "angles_without_sections" and not res.ok
    assert script.kinds == ["map", "retry"]  # no repair call, no re-attempt
    assert res.stats["angles_without_sections"] == 9
    assert store_calls == [] and stored_maps(db) == [] and res.map_id is None
    assert [r["agent"] for rows in ledger for r in rows] == ["angle_mapper"]  # the call is still billed


@pytest.mark.asyncio
async def test_angles_that_write_their_refs_in_the_text_are_filled_not_failed(monkeypatch, store_calls):
    """The failure of 2026-11-27 (mapper_ab repro run7): every angle without its
    `sections` key, the refs inside `argument` («… (D1·S3, D1·S4)»). The angles take
    those refs; no retry is spent on them and the map is stored."""
    def handler(call, n):
        bare = F.good_one()
        for a, refs in zip(bare["angles"], ("(D1·S3, D1·S4)", "(D1·S2)")):
            del a["sections"]
            a["argument"] = f"{a['argument']} {refs}"
        return bare

    db = db_for(J)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.kinds == ["map"]
    assert [a["sections"] for a in res.output["angles"]] == [["D1.S3", "D1.S4"], ["D1.S2"]]
    st = res.stats
    assert st["angles_sections_from_text"] == 2 and st["angles_without_sections"] == 0
    assert [(a["seq"], a["sections"]) for a in store_calls[0]["angles"]] == [(1, ["D1.S3", "D1.S4"]), (2, ["D1.S2"])]


@pytest.mark.asyncio
async def test_a_map_that_names_its_sections_fills_none(monkeypatch):
    db = db_for(J)
    use(monkeypatch, lambda call, n: F.good_one())
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.stats["angles_sections_from_text"] == 0


@pytest.mark.asyncio
async def test_the_repairs_answer_gets_the_same_rule(monkeypatch):
    short = F.good_two()["angles"][:2]  # D1·S5 and D2·S3 nowhere → the repair call

    def handler(call, n):
        if call["kind"] == "map":
            return F.good_two(angles=short)
        return F.good_two(angles=[F.angle(sections=[]), *F.good_two()["angles"]])  # the repair and its retry

    db = db_for(J, O)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.kinds == ["map", "repair", "retry"]
    assert no_sections_message([1]) in script.calls[2]["retry"]
    assert res.stats["repair_applied"] is True and res.stats["angles_without_sections"] == 1
    assert len(res.output["angles"]) == 3 and res.unplaced == []


@pytest.mark.asyncio
async def test_a_repair_whose_every_angle_has_no_section_keeps_the_first_map(monkeypatch):
    short = F.good_two()["angles"][:2]

    def handler(call, n):
        if call["kind"] == "map":
            return F.good_two(angles=short)
        return F.good_two(angles=[F.angle(sections=[]), F.angle(sections=["D9.S1"])])

    db = db_for(J, O)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.kinds == ["map", "repair", "retry"]
    assert res.stats["repair_applied"] is False and res.stats["angles_without_sections"] == 2
    assert len(res.output["angles"]) == 2 and res.unplaced == ["D1.S5", "D2.S3"]


# ── angles-v5: one question (sent back once, then kept); gaps fixed in code ───


TWO_QUESTIONS = "ما حجية التحويل البنكي؟ وهل يكفي وحده؟"
SAME_AMOUNT = "المبلغ في الحكم 5,000 ريال وفي الاعتراض ٥٠٠٠ ريال"


@pytest.mark.asyncio
async def test_a_legal_question_with_two_questions_is_sent_back_once(monkeypatch, store_calls):
    def handler(call, n):
        out = F.good_two()
        if call["kind"] == "map":
            out["angles"][1]["legal_question"] = TWO_QUESTIONS
        return out

    db = db_for(J, O)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.kinds == ["map", "retry"]
    assert multi_question_message([2]) in script.calls[1]["retry"]
    assert "These refs are not sections" not in script.calls[1]["retry"]
    assert res.output["angles"][1]["legal_question"] == F.angle()["legal_question"]


CHAINED = "هل يجوز للمؤجر فسخ العقد، وما أثر ذلك على الأجرة المستحقة؟"  # one «؟», two questions


@pytest.mark.asyncio
async def test_a_chained_question_under_one_mark_is_sent_back_once(monkeypatch):
    def handler(call, n):
        out = F.good_two()
        if call["kind"] == "map":
            out["angles"][2]["legal_question"] = CHAINED
        return out

    db = db_for(J, O)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.kinds == ["map", "retry"]
    assert multi_question_message([3]) in script.calls[1]["retry"]
    assert "ONE question per angle: split or drop the second question" in script.calls[1]["retry"]
    assert res.stats["compound_questions_after_retry"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("preview", [False, True])
async def test_a_compound_question_left_after_the_retry_is_kept_and_counted(monkeypatch, store_calls, preview):
    def handler(call, n):  # the same answer to the map and to the retry
        out = F.good_two()
        out["angles"][0]["legal_question"] = CHAINED
        out["angles"][1]["legal_question"] = TWO_QUESTIONS
        return out

    db = db_for(J, O)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A, preview=preview)
    assert res.outcome == ("preview" if preview else "stored") and script.kinds == ["map", "retry"]
    assert multi_question_message([1, 2]) in script.calls[1]["retry"]
    assert len(res.output["angles"]) == 3  # never dropped
    assert [a["legal_question"] for a in res.output["angles"][:2]] == [CHAINED, TWO_QUESTIONS]
    assert res.stats["compound_questions_after_retry"] == 2
    if not preview:
        assert store_calls[0]["angles"][0]["legal_question"] == CHAINED


#: The review's case: equal amounts, plus an invoice number that used to let them through.
EQUAL_WITH_INVOICE = "إجمالي الفاتورة رقم 10234 هو 34,758.75 ريال بينما المطالب به ٣٤٬٧٥٨٫٧٥ ريال"
REAL_CONTRADICTION = {"kind": "contradiction", "sections": ["D1.S5", "D2.S2"],
                      "issue": "المبلغ في الحكم 5000 وفي الاعتراض 6000", "ocr_suspect": False}


def _equal(issue: str) -> dict:
    return {"kind": "contradiction", "sections": ["D1.S5", "D2.S2"], "issue": issue, "ocr_suspect": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("preview", [False, True])
async def test_an_equal_value_contradiction_is_dropped_in_code_without_a_retry(monkeypatch, store_calls, preview):
    def handler(call, n):
        return F.good_two(gaps=[_equal(SAME_AMOUNT), REAL_CONTRADICTION, _equal(EQUAL_WITH_INVOICE)])

    db = db_for(J, O)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A, preview=preview)
    assert res.outcome == ("preview" if preview else "stored") and script.kinds == ["map"]  # never sent back
    assert res.output["gaps"] == [REAL_CONTRADICTION]
    assert res.stats["equal_value_gaps_dropped"] == 2 and res.stats["ocr_gaps_merged"] == 0
    if not preview:
        assert store_calls[0]["map_row"]["gaps"] == [REAL_CONTRADICTION]


@pytest.mark.asyncio
async def test_a_map_with_no_gap_to_fix_counts_zero(monkeypatch):
    db = db_for(J, O)
    use(monkeypatch, two)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.output["gaps"] == F.good_two()["gaps"]
    st = res.stats
    assert st["equal_value_gaps_dropped"] == 0 and st["ocr_gaps_merged"] == 0
    assert st["compound_questions_after_retry"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("preview", [False, True])
async def test_ocr_gaps_are_relabelled_and_merged_per_document(monkeypatch, store_calls, preview):
    def ocr(kind, sections, issue):
        return {"kind": kind, "sections": sections, "issue": issue, "ocr_suspect": True}

    def handler(call, n):
        return F.good_two(gaps=[
            ocr("contradiction", ["D1.S5"], "قد يكون المبلغ ٣٠٠ مقروءاً خطأ"),
            REAL_CONTRADICTION,
            ocr("missing", ["D2.S2"], "نص مشوه في D2·S2"),
            ocr("missing", ["D1.S2", "D1.S3"], "تكرار حلقي في D1·S3"),
        ])

    db = db_for(J, O)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A, preview=preview)
    assert script.kinds == ["map"]  # no retry
    expected = [
        ocr("missing", ["D1.S5", "D1.S2", "D1.S3"], "قد يكون المبلغ ٣٠٠ مقروءاً خطأ؛ تكرار حلقي في D1·S3"),
        REAL_CONTRADICTION,
        ocr("missing", ["D2.S2"], "نص مشوه في D2·S2"),
    ]
    assert res.output["gaps"] == expected
    assert res.stats["ocr_gaps_merged"] == 1
    if not preview:
        assert store_calls[0]["map_row"]["gaps"] == expected


@pytest.mark.asyncio
async def test_the_gap_counts_are_the_kept_maps(monkeypatch):
    short = F.good_two()["angles"][:2]  # D1·S5 and D2·S3 nowhere → the repair call

    def handler(call, n):
        if call["kind"] == "map":
            return F.good_two(angles=short, gaps=[_equal(SAME_AMOUNT), _equal(EQUAL_WITH_INVOICE)])
        return F.good_two(gaps=[_equal(SAME_AMOUNT), REAL_CONTRADICTION])

    db = db_for(J, O)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.kinds == ["map", "repair"]
    assert res.stats["repair_applied"] is True
    assert res.output["gaps"] == [REAL_CONTRADICTION] and res.stats["equal_value_gaps_dropped"] == 1


@pytest.mark.asyncio
async def test_every_v5_problem_goes_back_in_the_one_retry_and_what_is_left_is_kept(monkeypatch, store_calls):
    def handler(call, n):  # the same answer to the map and to the retry
        out = F.good_two(gaps=[_equal(SAME_AMOUNT)], context=["D1.S1", "D2.S1", "D9.S1"])
        out["angles"][0]["legal_question"] = TWO_QUESTIONS
        return out

    db = db_for(J, O)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.kinds == ["map", "retry"]  # one retry, never a second
    retry = script.calls[1]["retry"]
    assert "context: D9.S1" in retry and multi_question_message([1]) in retry
    assert "gaps[" not in retry  # gaps are never sent back
    # code cannot pick the question: kept as the model left it; the equal-value gap is dropped
    assert res.output["angles"][0]["legal_question"] == TWO_QUESTIONS
    assert res.output["gaps"] == []
    assert store_calls[0]["angles"][0]["legal_question"] == TWO_QUESTIONS
    assert res.stats["invalid_refs_dropped"] == 1  # the unknown ref is still dropped
    assert res.stats["compound_questions_after_retry"] == 1 and res.stats["equal_value_gaps_dropped"] == 1


# ── angles-v5: the angle's ocr_suspect ────────────────────────────────────────


@pytest.mark.asyncio
async def test_ocr_suspect_angles_are_counted_and_stored(monkeypatch, store_calls):
    def handler(call, n):
        out = F.good_two()
        out["angles"][0]["ocr_suspect"] = True
        out["angles"][2]["ocr_suspect"] = None
        return out

    db = db_for(J, O)
    use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored"
    assert [a["ocr_suspect"] for a in res.output["angles"]] == [True, False, False]
    assert res.stats["ocr_suspect_angles"] == 1
    assert [a["ocr_suspect"] for a in store_calls[0]["angles"]] == [True, False, False]
    rows = sorted(db.tables["attachment_angles"], key=lambda r: r["seq"])
    assert [r["ocr_suspect"] for r in rows] == [True, False, False]
    assert [a["ocr_suspect"] for a in load_angle_map_for_item(db, A)["angles"]] == [True, False, False]


@pytest.mark.asyncio
async def test_no_ocr_suspect_angle_counts_zero(monkeypatch):
    db = db_for(J)
    use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.stats["ocr_suspect_angles"] == 0


# ── angles-v5: the message's files that could not be read ────────────────────


@pytest.mark.asyncio
async def test_the_messages_unread_files_are_named_by_title(monkeypatch):
    """O has no segment map (a small file, or its segmenter failed) and L was deleted:
    the model is told O's title, never L's, never O's text."""
    t = F.tables(J, O, L, unmapped=(O,))
    _rows("workspace_items", C, deleted_at="2026-11-21T00:00:00+00:00")(t)
    db = FakeSupabase(t)
    script = use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.excluded == {B: "skipped_no_map", C: "skipped_not_found"}
    prompt = script.calls[0]["prompt"]
    assert prompt == expected_prompt(J, unread=(O,))
    assert UNREAD_HEADING in prompt and f"- {O.title}" in prompt
    assert L.title not in prompt and O.lines[1] not in prompt


@pytest.mark.asyncio
async def test_a_file_left_out_after_its_text_was_read_is_named_too(monkeypatch):
    t = F.tables(J, O)
    _shift_offsets(B)(t)
    db = FakeSupabase(t)
    script = use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.excluded == {B: "skipped_paragraph_rule"}
    assert script.calls[0]["prompt"] == expected_prompt(J, unread=(O,))


@pytest.mark.asyncio
async def test_a_failed_title_read_still_maps_without_the_block(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("titles down")

    monkeypatch.setattr(runner_mod, "item_titles", broken)
    db = db_for(J, O, unmapped=(O,))
    script = use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.calls[0]["prompt"] == expected_prompt(J)


@pytest.mark.asyncio
async def test_unread_titles_are_masked_like_the_rest(monkeypatch):
    codec = PrivacyCodec(enabled=True, rng=random.Random(3))
    monkeypatch.setattr(runner_mod, "_codec_for", lambda supabase, uid: codec)
    t = F.tables(J, O, unmapped=(O,))
    _rows("workspace_items", B, title=f"هوية {F.NATIONAL_ID}")(t)
    db = FakeSupabase(t)
    script = use(monkeypatch, one)
    res = await run_angle_mapper(db, A, preview=True)
    assert res.outcome == "preview"
    prompt = script.calls[0]["prompt"]
    assert F.NATIONAL_ID not in prompt and f"- هوية {codec.real_to_fake[F.NATIONAL_ID]}" in prompt


# ── coverage and the repair call, across documents ───────────────────────────


@pytest.mark.asyncio
async def test_an_unplaced_core_section_of_any_document_gets_one_repair_call(monkeypatch, ledger):
    def handler(call, n):
        if call["kind"] == "repair":
            return F.good_two()
        return F.good_two(angles=F.good_two()["angles"][:2])  # D1·S5 and D2·S3 nowhere

    db = db_for(J, O)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.kinds == ["map", "repair"]
    repair = script.calls[1]
    assert repair["prompt"] == repair_message(["D1.S5", "D2.S3"])
    assert "D1·S5, D2·S3" in repair["prompt"]
    # on the first call's history: the model sees its first question again
    assert repair["every_prompt"][0] == script.calls[0]["prompt"] == expected_prompt(J, O)
    assert repair["instructions"] == build_instructions(["judgment", "claim"]).strip()
    st = res.stats
    assert st["repair_used"] is True and st["repair_applied"] is True and st["unplaced_before_repair"] == 2
    assert res.unplaced == [] and len(res.output["angles"]) == 3
    [rows] = ledger
    assert len(rows) == 2 and all(r["agent"] == "angle_mapper" for r in rows)
    assert st["input_tokens"] == sum(r["tokens_in"] for r in rows)


@pytest.mark.asyncio
async def test_still_unplaced_after_the_repair_is_marked_per_document(monkeypatch, store_calls):
    short = F.good_two()["angles"][:2]

    def handler(call, n):
        if call["kind"] == "repair":
            return F.good_two(angles=[{**short[0], "title": "بعد الإصلاح"}, short[1]])
        return F.good_two(angles=short)

    db = db_for(J, O)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.kinds == ["map", "repair"]
    assert res.unplaced == ["D1.S5", "D2.S3"]
    assert res.output["no_angle"] == [{"section": "D1.S5", "why": "لم يُصنَّف"},
                                      {"section": "D2.S3", "why": "لم يُصنَّف"}]
    assert res.output["angles"][0]["title"] == "بعد الإصلاح"  # the repair's full map replaced the first
    assert store_calls[0]["map_row"]["no_angle"] == res.output["no_angle"]


@pytest.mark.asyncio
async def test_a_repair_with_no_angles_is_ignored(monkeypatch, store_calls):
    def handler(call, n):
        if call["kind"] == "repair":
            return F.good_two(angles=[], context=["D1.S1", "D1.S2", "D1.S3", "D1.S5", "D2.S2", "D2.S3"])
        return F.good_two(angles=F.good_two()["angles"][:2])

    db = db_for(J, O)
    use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.stats["repair_applied"] is False
    assert len(res.output["angles"]) == 2 and res.unplaced == ["D1.S5", "D2.S3"]


@pytest.mark.asyncio
async def test_a_failed_repair_keeps_the_first_map(monkeypatch):
    def handler(call, n):
        if call["kind"] == "repair":
            raise RuntimeError("provider down")
        return F.good_two(angles=F.good_two()["angles"][:2])

    db = db_for(J, O)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and script.kinds == ["map", "repair"]  # the repair gets one attempt
    assert res.stats["repair_applied"] is False and res.stats["repair_error"] == "RuntimeError"
    assert res.unplaced == ["D1.S5", "D2.S3"] and len(res.output["angles"]) == 2


@pytest.mark.asyncio
async def test_a_map_with_no_angles_takes_the_repair_even_with_none(monkeypatch):
    def handler(call, n):
        if call["kind"] == "repair":
            return F.good_one(angles=[], context=["D1.S1", "D1.S5"], no_angle=[
                {"section": "D1.S2", "why": "طلبات فقط"}, {"section": "D1.S3", "why": "إنكار فقط"}])
        return F.good_one(angles=[], context=["D1.S1"])

    db = db_for(J)
    use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored"
    assert res.stats["unplaced_before_repair"] == 3 and res.stats["repair_applied"] is True
    assert res.unplaced == [] and res.output["angles"] == []


# ── masking (وضع السرية): one codec for the whole group ──────────────────────


def _masked_db() -> FakeSupabase:
    return db_for(J, O, words=f"أنا المدعى عليه وهويتي {F.NATIONAL_ID} وأبغى أعترض")


def _masked_handler(codec: PrivacyCodec, db: FakeSupabase, seen: dict):
    def handler(call, n):
        seen.setdefault("prompts", []).append(call["prompt"])
        seen.setdefault("persisted_first", [dict(r) for r in db.tables["pii_mappings"]])
        nid, iq = codec.real_to_fake[F.NATIONAL_ID], codec.real_to_fake[F.IQAMA]
        out = F.good_two(
            client={"side": "respondent", "party": f"صاحب الهوية {nid}", "basis": f"قال: هويتي {nid}",
                    "confidence": "clear", "by_document": [{"document": "D2", "side": "claimant",
                                                            "party": f"المعترض صاحب الإقامة {iq}"}]},
            gaps=[{"kind": "contradiction", "sections": ["D1.S5", "D2.S3"], "issue": f"الرقم {nid} يقابله {iq}",
                   "ocr_suspect": True}],
            no_angle=[{"section": "D1.S6", "why": f"توقيع {nid}"}],
            unrelated_documents=[{"document": "D2", "why": f"إقامة {iq}"}],
        )
        out["angles"][0].update(for_party=f"صاحب الهوية {nid}", title=f"هوية {nid} وإقامة {iq}",
                                argument=f"الهوية {nid} ثابتة.", legal_question=f"هل الرقم {iq} حجة؟",
                                weakness=f"لا شيء عن {nid}")
        return out

    return handler


def _assert_real(output: dict, codec: PrivacyCodec) -> None:
    nid, iq = codec.real_to_fake[F.NATIONAL_ID], codec.real_to_fake[F.IQAMA]
    assert nid not in str(output) and iq not in str(output)
    a = output["angles"][0]
    assert a["for_party"] == f"صاحب الهوية {F.NATIONAL_ID}"
    assert a["title"] == f"هوية {F.NATIONAL_ID} وإقامة {F.IQAMA}"
    assert a["argument"] == f"الهوية {F.NATIONAL_ID} ثابتة."
    assert a["legal_question"] == f"هل الرقم {F.IQAMA} حجة؟"
    assert a["weakness"] == f"لا شيء عن {F.NATIONAL_ID}"
    assert output["client"]["party"] == f"صاحب الهوية {F.NATIONAL_ID}"
    assert output["client"]["basis"] == f"قال: هويتي {F.NATIONAL_ID}"
    assert output["client"]["by_document"][0]["party"] == f"المعترض صاحب الإقامة {F.IQAMA}"
    assert output["gaps"][0]["issue"] == f"الرقم {F.NATIONAL_ID} يقابله {F.IQAMA}"
    assert output["no_angle"][0]["why"] == f"توقيع {F.NATIONAL_ID}"
    assert output["unrelated_documents"][0]["why"] == f"إقامة {F.IQAMA}"


@pytest.mark.asyncio
async def test_one_codec_masks_every_document_and_the_map_is_stored_real(monkeypatch, store_calls):
    codec = PrivacyCodec(enabled=True, rng=random.Random(11))
    built: list = []
    monkeypatch.setattr(runner_mod, "_codec_for", lambda supabase, uid: built.append(uid) or codec)
    db = _masked_db()
    seen: dict = {}
    use(monkeypatch, _masked_handler(codec, db, seen))
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored"
    assert built == [F.USER]  # ONE codec for the group
    nid, iq = codec.real_to_fake[F.NATIONAL_ID], codec.real_to_fake[F.IQAMA]
    assert nid != F.NATIONAL_ID and iq != F.IQAMA
    [prompt] = seen["prompts"]
    assert F.NATIONAL_ID not in prompt and F.IQAMA not in prompt
    assert prompt.count(nid) == 2 and prompt.count(iq) == 1  # D1's paragraph + the words; D2's paragraph
    # the new fakes reached pii_mappings BEFORE the model read them
    persisted = {(r["real_value"], r["fake_value"]) for r in seen["persisted_first"]}
    assert {(F.NATIONAL_ID, nid), (F.IQAMA, iq)} <= persisted
    _assert_real(res.output, codec)
    c = store_calls[0]
    assert nid not in str(c) and iq not in str(c)
    assert F.NATIONAL_ID in c["map_row"]["client"]["party"] and F.IQAMA in str(c["map_row"]["client"])
    rows = db.tables["attachment_angles"]
    assert nid not in str(rows) and any(F.NATIONAL_ID in r["title"] for r in rows)


@pytest.mark.asyncio
async def test_preview_masks_too_and_saves_no_fakes(monkeypatch):
    codec = PrivacyCodec(enabled=True, rng=random.Random(11))
    monkeypatch.setattr(runner_mod, "_codec_for", lambda supabase, uid: codec)
    db = _masked_db()
    seen: dict = {}
    use(monkeypatch, _masked_handler(codec, db, seen))
    res = await run_angle_mapper(db, A, preview=True)
    assert res.outcome == "preview"
    assert F.NATIONAL_ID not in seen["prompts"][0] and F.IQAMA not in seen["prompts"][0]
    _assert_real(res.output, codec)
    assert db.tables["pii_mappings"] == [] and db.writes() == []


# ── the ledger ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_bare_run_opens_one_scope_keyed_to_the_group(monkeypatch, ledger):
    db = db_for(J, O)
    use(monkeypatch, two)
    res = await run_angle_mapper(db, B)
    assert res.outcome == "stored"
    [rows] = ledger
    [row] = rows
    assert row["agent"] == "angle_mapper" and row["agent_family"] == "memory"
    assert (row["user_id"], row["conversation_id"], row["case_id"], row["message_id"]) == (
        F.USER, F.CONVO, None, F.MSG)
    assert row["tokens_in"] == res.stats["input_tokens"] > 0 and row["requests"] == 1
    assert row["outcome"] == "ok"


@pytest.mark.asyncio
async def test_an_open_turn_scope_gets_nothing_the_groups_scope_gets_the_rows(monkeypatch, ledger):
    """Under a turn's open scope — maybe ANOTHER message's (an abandoned upload, a
    file OCR'd later) — the group run still opens its own scope, nested, keyed to
    the group: the rows carry the group's message, the turn's scope gets none."""
    db = db_for(J)
    use(monkeypatch, one)
    with collect_llm_calls(db, conversation_id=F.CONVO, user_id=F.USER, message_id="turn-9"):
        res = await run_angle_mapper(db, A)
    assert res.outcome == "stored"
    group_rows, turn_rows = ledger  # the inner (group) scope flushes first
    assert [r["message_id"] for r in group_rows] == [F.MSG] and group_rows[0]["agent"] == "angle_mapper"
    assert (group_rows[0]["user_id"], group_rows[0]["conversation_id"]) == (F.USER, F.CONVO)
    assert turn_rows == []


@pytest.mark.asyncio
async def test_a_joined_run_bills_once_in_the_groups_scope(monkeypatch, ledger):
    db = db_for(J, O)
    _calls, both_in = gate_joins(monkeypatch)

    async def handler(call, n):
        await asyncio.wait_for(both_in.wait(), 5)
        return F.good_two()

    use(monkeypatch, handler)

    async def member(item_id: str, turn: str):
        with collect_llm_calls(db, conversation_id=F.CONVO, user_id=F.USER, message_id=turn):
            return await run_angle_mapper(db, item_id)

    ra, rb = await asyncio.gather(member(A, "run-a"), member(B, "run-b"))
    assert sorted([ra.outcome, rb.outcome]) == ["joined_group", "stored"]
    assert sorted(len(rows) for rows in ledger) == [0, 0, 1]  # the group's scope; both turns' empty
    [row] = next(rows for rows in ledger if rows)
    assert row["message_id"] == F.MSG and row["agent"] == "angle_mapper"


@pytest.mark.asyncio
async def test_rows_carry_the_case(monkeypatch, ledger):
    t = F.tables(J)
    t["workspace_items"][0]["case_id"] = "case-3"
    db = FakeSupabase(t)
    use(monkeypatch, one)
    await run_angle_mapper(db, A)
    assert ledger[0][0]["case_id"] == "case-3"


# ── preview: map now, store nothing, bill nothing ────────────────────────────


@pytest.mark.asyncio
async def test_preview_stores_nothing_and_bills_nothing(monkeypatch, store_calls, ledger):
    codec = PrivacyCodec(enabled=True, rng=random.Random(5))
    monkeypatch.setattr(runner_mod, "_codec_for", lambda supabase, uid: codec)
    monkeypatch.setattr(runner_mod, "angle_map_enabled", lambda uid: False)  # preview skips the gate
    db = db_for(J, O)
    script = use(monkeypatch, two)
    with collect_llm_calls(db, conversation_id=F.CONVO, user_id=F.USER, message_id="turn-1"):
        res = await run_angle_mapper(db, A, preview=True)
    assert res.outcome == "preview" and res.ok and res.map_id is None and res.item_ids == [A, B]
    assert len(script.calls) == 1
    assert res.output == AngleMap.model_validate(F.good_two()).model_dump()
    assert res.stats["angle_count"] == 3 and res.stats["input_tokens"] > 0 and res.stats["cross_doc_angles"] == 2
    assert store_calls == [] and db.writes() == []  # no map, no members, no angles, no masking fakes
    assert all(db.tables[t] == [] for t in ANGLE_TABLES) and db.tables["pii_mappings"] == []
    assert ledger == [[]]  # only the caller's scope closed, and it got no row


@pytest.mark.asyncio
async def test_preview_outside_any_scope_opens_none(monkeypatch, ledger):
    db = db_for(J)
    use(monkeypatch, one)
    assert (await run_angle_mapper(db, A, preview=True)).outcome == "preview"
    assert (await run_message_angle_map(db, F.MSG, [A], preview=True)).outcome == "preview"
    assert ledger == [] and db.writes() == []


@pytest.mark.asyncio
async def test_preview_ignores_pending_members_and_fresh_maps(monkeypatch):
    db = db_for(J, O, unmapped=(O,), big=(O,), helper_runs=[lease(B)])
    script = use(monkeypatch, one)
    assert (await run_angle_mapper(db, A)).outcome == "skipped_group_pending"
    res = await run_angle_mapper(db, A, preview=True)
    assert res.outcome == "preview" and res.item_ids == [A] and res.excluded == {B: "skipped_no_map"}
    assert "pending" not in res.stats

    db = db_for(J)
    assert (await run_angle_mapper(db, A)).outcome == "stored"
    assert (await run_angle_mapper(db, A, preview=True)).outcome == "preview"  # a fresh map does not stop it
    assert len(script.calls) == 3


# ── run_message_angle_map: the group a caller names ──────────────────────────


@pytest.mark.asyncio
async def test_message_run_maps_the_named_items_in_upload_order(monkeypatch):
    db = db_for(J, O, unmapped=(O,), big=(O,), helper_runs=[lease(B)])
    for k, v in F.map_tables(O, F.content(O, big=True)).items():
        db.tables[k] += v  # B is mapped, and its stale lease is ignored: no pending check
    script = use(monkeypatch, two)
    res = await run_message_angle_map(db, F.MSG, [B, A, B, ""])
    assert res.outcome == "stored" and res.item_id == B and res.item_ids == [A, B] and res.message_id == F.MSG
    assert script.calls[0]["prompt"] == expected_prompt(J, O, big=(O,))
    again = await run_message_angle_map(db, F.MSG, [A, B])  # no fresh check either
    assert again.outcome == "stored" and len(script.calls) == 2
    assert stored_maps(db) == [(F.MSG, [A, B])]


@pytest.mark.asyncio
async def test_message_run_preview_and_exclusions(monkeypatch, ledger):
    t = F.tables(J, O, L)
    _rows("attachment_segment_maps", C, prompt_version="seg-v5.1")(t)
    db = FakeSupabase(t)
    monkeypatch.setattr(runner_mod, "angle_map_enabled", lambda uid: False)
    use(monkeypatch, two)
    res = await run_message_angle_map(db, F.MSG, [A, B, C], preview=True)
    assert res.outcome == "preview" and res.item_ids == [A, B] and res.excluded == {C: "skipped_old_segments"}
    assert db.writes() == [] and ledger == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ids, outcome", [
    ([], "skipped_not_found"),
    (["", None], "skipped_not_found"),
    (["dddddddd-0000-4000-8000-000000000009"], "skipped_not_found"),
])
async def test_message_run_without_items(monkeypatch, ids, outcome):
    script = use(monkeypatch, two)
    res = await run_message_angle_map(db_for(J), F.MSG, ids)
    assert res.outcome == outcome and script.calls == []


#: Ways the FIRST id given can be unusable while the rest of the message is fine.
NOT_LIVE_A = [
    ("skipped_not_found", _rows("workspace_items", A, deleted_at="2026-11-21T00:00:00+00:00")),
    ("skipped_not_found", _drop("workspace_items", A)),
    ("skipped_not_attachment", _rows("workspace_items", A, kind="agent_search")),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason, change", NOT_LIVE_A)
async def test_message_run_resolves_the_group_from_the_first_live_attachment(monkeypatch, ledger, reason, change):
    """The first id is deleted / not an attachment: the run is resolved from the
    first LIVE attachment (B) — its user, its conversation — and the first id is
    simply left out, as the docstring says."""
    t = F.tables(J, O)
    change(t)
    db = FakeSupabase(t)
    gate: list = []
    monkeypatch.setattr(runner_mod, "angle_map_enabled", lambda uid: gate.append(uid) or True)
    script = use(monkeypatch, lambda call, n: place_all(O))
    res = await run_message_angle_map(db, F.MSG, [A, B])
    assert res.outcome == "stored" and res.item_id == B
    assert res.item_ids == [B] and res.excluded == {A: reason}
    assert gate == [F.USER]  # the gate asked for B's user
    assert script.calls[0]["prompt"] == expected_prompt(O)
    assert stored_maps(db) == [(F.MSG, [B])]
    [rows] = ledger
    assert (rows[0]["user_id"], rows[0]["conversation_id"], rows[0]["message_id"]) == (F.USER, F.CONVO, F.MSG)


@pytest.mark.asyncio
async def test_message_run_left_out_ids_before_and_between_live_ones(monkeypatch):
    t = F.tables(J, O, L)
    _rows("workspace_items", A, deleted_at="2026-11-21T00:00:00+00:00")(t)
    _rows("workspace_items", C, kind="agent_search")(t)
    ghost = "dddddddd-0000-4000-8000-000000000009"
    use(monkeypatch, lambda call, n: place_all(O))
    res = await run_message_angle_map(FakeSupabase(t), F.MSG, [ghost, A, C, B], preview=True)
    assert res.outcome == "preview" and res.item_id == B and res.item_ids == [B]
    assert res.excluded == {ghost: "skipped_not_found", A: "skipped_not_found", C: "skipped_not_attachment"}


@pytest.mark.asyncio
@pytest.mark.parametrize("first, second, outcome", [
    ("deleted", "not_attachment", "skipped_not_found"),
    ("not_attachment", "deleted", "skipped_not_attachment"),
])
async def test_message_run_with_no_live_attachment_is_the_first_ids_reason(monkeypatch, store_calls, ledger,
                                                                           first, second, outcome):
    how = {"deleted": {"deleted_at": "2026-11-21T00:00:00+00:00"}, "not_attachment": {"kind": "agent_search"}}
    reasons = {"deleted": "skipped_not_found", "not_attachment": "skipped_not_attachment"}
    t = F.tables(J, O)
    _rows("workspace_items", A, **how[first])(t)
    _rows("workspace_items", B, **how[second])(t)
    db = FakeSupabase(t)
    script = use(monkeypatch, two)
    res = await run_message_angle_map(db, F.MSG, [A, B])
    assert res.outcome == outcome and not res.ok and res.item_id == A
    assert res.excluded == {A: reasons[first], B: reasons[second]} and res.item_ids == []
    assert script.calls == [] and store_calls == [] and ledger == [] and db.writes() == []


@pytest.mark.asyncio
async def test_message_run_with_a_live_anchor_but_no_usable_member_is_the_anchors_reason(monkeypatch):
    t = F.tables(J, O, unmapped=(O,))
    _rows("workspace_items", A, deleted_at="2026-11-21T00:00:00+00:00")(t)
    script = use(monkeypatch, two)
    res = await run_message_angle_map(FakeSupabase(t), F.MSG, [A, B])
    assert res.outcome == "skipped_no_map" and res.item_id == B
    assert res.excluded == {A: "skipped_not_found", B: "skipped_no_map"} and script.calls == []


@pytest.mark.asyncio
async def test_message_run_with_no_usable_member_is_the_first_items_reason(monkeypatch):
    t = F.tables(J, O)
    _rows("attachment_segment_maps", A, prompt_version="seg-v5.1")(t)
    _rows("attachment_segment_maps", B, content_chars=3)(t)
    script = use(monkeypatch, two)
    res = await run_message_angle_map(FakeSupabase(t), F.MSG, [A, B])
    assert res.outcome == "skipped_old_segments" and res.excluded == {A: "skipped_old_segments", B: "skipped_stale"}
    assert script.calls == []


# ── never raises ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_exploding_model_is_failed_llm(monkeypatch, store_calls):
    def boom(call, n):
        raise RuntimeError("provider down")

    db = db_for(J, O)
    script = use(monkeypatch, boom)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "failed_llm" and res.error == "RuntimeError" and not res.ok
    assert res.stats["attempts"] == 2 and len(script.calls) == 2
    assert store_calls == [] and db.writes() == []


@pytest.mark.asyncio
async def test_an_unparsable_answer_twice_is_failed_invalid(monkeypatch, store_calls):
    db = db_for(J)
    script = use(monkeypatch, lambda call, n: F.good_one(angles=[F.angle(kind="summary")]))
    res = await run_angle_mapper(db, A)
    assert res.outcome == "failed_invalid" and res.error == "UnexpectedModelBehavior"
    assert script.kinds == ["map", "retry", "map", "retry"]  # two runs, each with its one retry
    assert store_calls == []


@pytest.mark.asyncio
async def test_invalid_then_valid_is_one_reattempt(monkeypatch):
    def handler(call, n):
        return F.good_one(angles=[F.angle(kind="summary")]) if n <= 2 else F.good_one()

    db = db_for(J)
    script = use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.stats["attempts"] == 2
    assert script.kinds == ["map", "retry", "map"]


@pytest.mark.asyncio
async def test_a_hung_call_times_out_and_is_reattempted(monkeypatch):
    monkeypatch.setattr(runner_mod, "CALL_TIMEOUT_S", 0.2)

    async def handler(call, n):
        if n == 1:
            await asyncio.sleep(5)
        return F.good_one()

    db = db_for(J)
    use(monkeypatch, handler)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.stats["attempts"] == 2


@pytest.mark.asyncio
async def test_a_raising_store_is_failed_store(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("store bug")

    monkeypatch.setattr(store_mod, "write_angle_map", broken)
    db = db_for(J)
    use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "failed_store" and res.error == "RuntimeError"
    assert res.output is not None and res.map_id is None  # the map is still returned


@pytest.mark.asyncio
async def test_a_failed_write_is_failed_store_and_leaves_no_map(monkeypatch):
    db = db_for(J, O)
    db.fail.add(("attachment_angles", "insert"))
    use(monkeypatch, two)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "failed_store" and res.error == ""
    assert all(db.tables[t] == [] for t in ANGLE_TABLES)


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["workspace_items", "message_attachments", "attachment_segment_maps"])
async def test_a_failed_read_is_error(monkeypatch, table):
    db = db_for(J, O)
    db.fail.add(table)
    script = use(monkeypatch, two)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "error" and res.error == "RuntimeError" and script.calls == []


@pytest.mark.asyncio
async def test_a_failed_segments_read_is_no_sections(monkeypatch):
    """``load_segment_maps`` never raises: a failed read looks like no sections."""
    db = db_for(J)
    db.fail.add("attachment_segments")
    script = use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "skipped_no_sections" and script.calls == []


@pytest.mark.asyncio
async def test_a_failed_user_words_read_still_maps(monkeypatch):
    db = db_for(J)
    db.fail.add("messages")
    script = use(monkeypatch, one)
    res = await run_angle_mapper(db, A)
    assert res.outcome == "stored" and res.stats["user_words_chars"] == 0
    assert script.calls[0]["prompt"] == expected_prompt(J, words="")


@pytest.mark.asyncio
@pytest.mark.parametrize("preview", [False, True])
async def test_a_bug_before_the_call_is_error(monkeypatch, preview):
    def broken(**kw):
        raise ValueError("bad section row")

    monkeypatch.setattr(runner_mod, "build_user_message", broken)
    db = db_for(J)
    script = use(monkeypatch, one)
    res = await run_angle_mapper(db, A, preview=preview)
    assert res.outcome == "error" and res.error == "ValueError" and script.calls == []
    res = await run_message_angle_map(db, F.MSG, [A], preview=preview)
    assert res.outcome == "error" and res.error == "ValueError"


# ── the helper frame's applies_to ────────────────────────────────────────────


@pytest.mark.parametrize("big, expected", [(True, True), (False, False)])
def test_applies_to_is_the_segmenters_rule(big, expected):
    db = db_for(J, big=(J,) if big else ())
    assert angle_mapper_applies_to_item(db, A) is expected
    assert db.writes() == []


def test_applies_to_never_raises():
    db = db_for(J, big=(J,))
    db.fail.add("workspace_items")
    assert angle_mapper_applies_to_item(db, A) is False
    db = db_for(J, big=(J,))
    db.tables["workspace_items"][0]["deleted_at"] = "2026-11-21T00:00:00+00:00"
    assert angle_mapper_applies_to_item(db, A) is False


@pytest.mark.parametrize("version, ok", [
    ("seg-v5.2", True), ("seg-v5.3", True), ("seg-v5.10", True), ("seg-v6", True), ("seg-v6.0", True),
    (" seg-v5.2 ", True), ("seg-v5.1", False), ("seg-v5", False), ("seg-v4.4", False),
    ("seg-v3.1-p2p4", False), ("seg-v5.2-x", False), ("angles-v4", False), ("", False), (None, False),
])
def test_segment_version_ok(version, ok):
    assert segment_version_ok(version) is ok


# ── the real agent + slot ────────────────────────────────────────────────────


def test_slot_is_v41_on_wafer():
    assert agent_models.AGENT_MODELS["angle_mapper"] is agent_models._WAFER_V41_DEFAULT
    assert agent_models.primary_model_of_slot("angle_mapper") == "wafer-deepseek-v4.1-flash-1017"
    assert agent_mod.MODEL_SLOT == "angle_mapper"


def test_limits_use_the_shared_output_cap():
    assert agent_mod.ANGLE_MAPPER_LIMITS.output_tokens_limit == OUTPUT_TOKENS_CAP
    assert agent_mod.ANGLE_MAPPER_LIMITS.request_limit == 2
    assert agent_mod.MAX_OUTPUT_TOKENS == 65_536


REFS_TWO = frozenset({*(F.ref(1, s) for s in J.seqs), *(F.ref(2, s) for s in O.seqs)})


@pytest.mark.asyncio
async def test_real_factory_with_test_model():
    agent = agent_mod.create_angle_mapper("instructions")
    with agent.override(model=TestModel(custom_output_args=F.good_two())):
        res = await agent.run("[P001] a", deps=AngleMapperDeps(item_id=A, section_refs=REFS_TWO))
    assert isinstance(res.output, AngleMap)
    assert res.output.client.side == "respondent" and len(res.output.angles) == 3
    assert res.output.cross_doc_angles() == 2


@pytest.mark.asyncio
async def test_the_validator_returns_the_qualified_map_of_a_group_of_one():
    agent = agent_mod.create_angle_mapper("instructions")
    refs = frozenset(F.ref(1, s) for s in J.seqs)
    with agent.override(model=TestModel(custom_output_args=F.good_one(context=["S1", 5]))):
        res = await agent.run("[P001] a", deps=AngleMapperDeps(item_id=A, section_refs=refs))
    assert res.output.context == ["D1.S1", "D1.S5"]


def test_deps_keep_the_text_out_of_the_span():
    deps = AngleMapperDeps(item_id=B, message_id=F.MSG, item_ids=(A, B), doc_types=("judgment", "claim"),
                           section_refs=REFS_TWO, call="repair")
    assert deps.tracking_input() == {"item_id": B, "message_id": F.MSG, "docs": 2,
                                     "doc_types": ["judgment", "claim"], "sections": 10, "call": "repair"}
    assert deps.tracking_input_full() == deps.tracking_input()
