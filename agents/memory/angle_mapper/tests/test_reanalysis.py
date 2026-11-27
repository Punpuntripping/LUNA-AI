"""Re-analysis of an angle map (attachment_agent.md §2.3, D8 / D16 / D20):
``start_reanalysis``'s checks, the scheduled run (lease + in flight through the
frame, snapshot, the user's critique + earlier requests as guidance, the map
replaced, the ledger keyed to the CURRENT turn), the carried map of a library
file, the guidance block, and the text-corrections overlay on what the mapper
reads. The REAL agent runs on a ``FunctionModel``; the store, the edit log and the
lease store are the real ones on the fake client."""
from __future__ import annotations

import asyncio
import random
from datetime import timedelta
from typing import Any, Callable

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.helpers import _runtime
from agents.memory.angle_mapper import agent as agent_mod
from agents.memory.angle_mapper import edits as E
from agents.memory.angle_mapper import reanalysis as R
from agents.memory.angle_mapper import runner as runner_mod
from agents.memory.angle_mapper.prompts import (
    GUIDANCE_CLOSE,
    GUIDANCE_OPEN,
    PromptDocument,
    build_user_message,
    format_guidance,
    guidance_block,
)
from agents.memory.angle_mapper.store import (
    ITEMS_TABLE,
    MAPS_TABLE,
    load_angle_map_for_item,
    load_fresh_angle_map_for_item,
    write_angle_map,
)
from agents.memory.attachment_segmenter.paragraphs import PARAGRAPH_RULE_VERSION
from agents.utils import usage_sink
from shared.attachments import text_corrections as TC
from shared.privacy import PrivacyCodec

from . import fixtures as F
from .fake_supabase import FakeSupabase

A, B, C = F.ITEM_A, F.ITEM_B, F.ITEM_C
J, O, L = F.JUDGMENT, F.OBJECTION, F.LEASE
USER = F.USER
NOW_CONVO = "convo-now"
NOW_MSG = "msg-now"
CRITIQUE = "ماعجبني التحليل، الموكل هو المعترض وليس المدعى عليه، اعيد تحليله"
LOG = E.EDITS_TABLE


# ── fixtures ─────────────────────────────────────────────────────────────────


async def _paid(supabase, user_id) -> bool:
    return True


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(runner_mod, "angle_map_enabled", lambda uid: True)
    monkeypatch.setattr(runner_mod, "_plan_allows", _paid)
    monkeypatch.setattr(runner_mod, "_codec_for", lambda supabase, uid: None)
    monkeypatch.setattr(runner_mod, "_GROUP_TASKS", {})
    monkeypatch.setattr(_runtime, "_INFLIGHT", {})
    monkeypatch.setattr(_runtime, "_CONVO_LATEST", {})
    monkeypatch.setattr(R, "_JOBS", set())
    TC.reset_backoff()


@pytest.fixture(autouse=True)
def ledger(monkeypatch) -> list[list[dict]]:
    flushed: list[list[dict]] = []
    monkeypatch.setattr(usage_sink, "_flush", lambda supabase, buf: flushed.append([dict(r) for r in buf or []]))
    return flushed


class Model:
    """The model behind the real agent: records each fresh prompt; ``gate`` holds
    the answer until set; ``fail`` raises instead."""

    def __init__(self, answer: Callable[[], dict] = F.good_two, *, gate: asyncio.Event | None = None,
                 fail: bool = False) -> None:
        self.answer, self.gate, self.fail = answer, gate, fail
        self.prompts: list[str] = []

    async def fn(self, messages: list, info: AgentInfo) -> ModelResponse:
        parts = list(getattr(messages[-1], "parts", []))
        prompts = [p.content for p in parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)]
        if prompts:
            self.prompts.append(prompts[-1])
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("provider down")
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, self.answer())])


def use(monkeypatch, model: Model) -> Model:
    monkeypatch.setattr(agent_mod, "get_agent_model", lambda slot: FunctionModel(model.fn))
    return model


def _items(*docs: F.Doc) -> list[dict]:
    return [
        {"item_id": d.item_id, "doc_index": n, "content_chars": len(F.content(d)),
         "segment_prompt_version": F.SEG_VERSION, "paragraph_rule_version": PARAGRAPH_RULE_VERSION,
         "segmented_at": F.SEG_CREATED_AT}
        for n, d in enumerate(docs, start=1)
    ]


def _store(db: FakeSupabase, *docs: F.Doc, message: str | None = F.MSG, conversation: str = F.CONVO) -> str:
    answer = F.good_two() if len(docs) == 2 else F.good_one()
    row = {"prompt_version": "angles-v5", "model": "m", "client": answer["client"], "context": answer["context"],
           "gaps": answer["gaps"], "no_angle": answer["no_angle"], "unrelated_documents": answer["unrelated_documents"],
           "input_tokens": 1, "output_tokens": 1, "reasoning_tokens": 0, "cost_usd": 0.01, "duration_ms": 10}
    angles = [{**a, "seq": i} for i, a in enumerate(answer["angles"], start=1)]
    map_id = write_angle_map(db, message_id=message, conversation_id=conversation, user_id=USER,
                             items=_items(*docs), map_row=row, angles=angles)
    assert map_id
    return map_id


def mapped(*docs: F.Doc, **kw: Any) -> tuple[FakeSupabase, str]:
    """The judgment (D1) + the objection (D2) of one message, stored as one map."""
    docs = docs or (J, O)
    db = FakeSupabase(F.tables(*docs, **kw))
    return db, _store(db, *docs)


async def start(db: FakeSupabase, item_ids=(A,), critique: str = CRITIQUE, **kw: Any) -> R.ReanalysisStart:
    args = {"item_ids": list(item_ids), "user_id": USER, "conversation_id": NOW_CONVO, "message_id": NOW_MSG,
            "critique": critique, **kw}
    return await R.start_reanalysis(db, **args)


async def drain() -> list:
    tasks = list(R._JOBS)
    if tasks:
        await asyncio.wait(tasks, timeout=10)
    return [t.result() for t in tasks]


def runs(db: FakeSupabase) -> dict[str, dict]:
    return {r["item_id"]: r for r in db.tables["helper_runs"] if r["helper"] == "angle_map"}


def maps(db: FakeSupabase) -> list[tuple[str, str | None, list[str]]]:
    out = []
    for m in db.tables[MAPS_TABLE]:
        rows = sorted((r for r in db.tables[ITEMS_TABLE] if r["map_id"] == m["map_id"]), key=lambda r: r["doc_index"])
        out.append((m["map_id"], m["message_id"], [r["item_id"] for r in rows]))
    return out


def base_prompt(*docs: F.Doc, words: str = F.WORDS) -> str:
    """The user message of a NORMAL run of ``docs`` as D1… (no guidance)."""
    pds = []
    for i, d in enumerate(docs, start=1):
        text = F.content(d)
        smap = F.seg_map_row(d, text)
        pds.append(PromptDocument(
            index=i, title=d.title, doc_type=d.doc_type, content_md=text, summary=d.summary,
            documents_in_file=smap["documents_in_file"], sections=F.segment_rows(d, text),
            key_paragraphs=smap["key_paragraphs"],
        ))
    return build_user_message(documents=pds, user_words=F.user_words(words))


def log_row(map_id: str, kind: str, evidence: str, at: str, *, undone: bool = False, items=(A, B), **kw) -> dict:
    return {"id": f"e-{at}", "map_id": map_id, "conversation_id": F.CONVO, "user_id": USER, "item_ids": list(items),
            "kind": kind, "target": "", "before": None, "after": None, "evidence": evidence, "message_id": None,
            "created_at": at, "undone_at": "2026-11-27T00:00:00+00:00" if undone else None, **kw}


# ── start_reanalysis: the checks ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_map_is_refused(monkeypatch):
    model = use(monkeypatch, Model())
    db = FakeSupabase(F.tables(J, O))
    got = await start(db)
    assert got == R.ReanalysisStart(False, R.R_NO_MAP, None)
    assert db.writes() == [] and R._JOBS == set() and model.prompts == []


@pytest.mark.asyncio
async def test_a_free_plan_is_refused_before_anything_is_read(monkeypatch):
    async def free(supabase, user_id) -> bool:
        return False

    monkeypatch.setattr(runner_mod, "_plan_allows", free)
    db, _ = mapped()
    n = len(db.log)
    got = await start(db)
    assert (got.ok, got.reason) == (False, R.R_FREE)
    assert db.log[n:] == [] and R._JOBS == set()


@pytest.mark.asyncio
async def test_the_flag_off_is_refused(monkeypatch):
    monkeypatch.setattr(runner_mod, "angle_map_enabled", lambda uid: False)
    db, _ = mapped()
    got = await start(db)
    assert (got.ok, got.reason) == (False, R.R_DISABLED) and R._JOBS == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("critique", ["", "   \n "])
async def test_no_words_from_the_user_is_refused(critique):
    db, _ = mapped()
    got = await start(db, critique=critique)
    assert (got.ok, got.reason) == (False, R.R_NO_REQUEST)


@pytest.mark.asyncio
async def test_files_of_two_maps_ask_which_file():
    db = FakeSupabase(F.tables(J, O))
    for k, v in F.tables(L, message="msg-other").items():
        db.tables[k] += v
    _store(db, J, O)
    _store(db, L, message="msg-other")
    got = await start(db, item_ids=(A, C))
    assert (got.ok, got.reason) == (False, R.R_SEVERAL)


@pytest.mark.asyncio
async def test_a_failed_read_never_raises():
    db, _ = mapped()
    db.fail.add(ITEMS_TABLE)
    got = await start(db)
    assert (got.ok, got.reason) == (False, R.R_ERROR)


@pytest.mark.asyncio
async def test_a_normal_run_going_in_this_process_is_busy():
    db, map_id = mapped()

    async def going():
        await asyncio.sleep(1)

    task = asyncio.get_running_loop().create_task(going())
    _runtime._INFLIGHT[("angle_map", B)] = task
    try:
        got = await start(db)
    finally:
        task.cancel()
    assert got == R.ReanalysisStart(False, R.R_BUSY, map_id)
    assert runs(db) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("version, reason", [("angles-v5/x+reanalysis", R.R_RUNNING), ("angles-v5/x", R.R_BUSY)])
async def test_a_live_lease_in_another_process_is_refused(version, reason):
    db, map_id = mapped()
    until = (_runtime.utcnow() + timedelta(minutes=5)).isoformat()
    db.tables["helper_runs"].append({"item_id": B, "helper": "angle_map", "status": "running", "attempt": 1,
                                     "lease_until": until, "version": version, "error": None})
    got = await start(db)
    assert got == R.ReanalysisStart(False, reason, map_id)
    assert runs(db)[B]["lease_until"] == until and A not in runs(db)  # nothing claimed


# ── the scheduled run ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ok_schedules_and_returns_at_once_then_replaces_the_map(monkeypatch, ledger):
    gate = asyncio.Event()
    model = use(monkeypatch, Model(gate=gate))
    db, old = mapped()

    got = await start(db)
    assert got == R.ReanalysisStart(True, "", old)
    # returned while the run is still going: in flight under every member, leases held
    [task] = list(R._JOBS)
    assert not task.done()
    assert _runtime.inflight("angle_map", A) is task and _runtime.inflight("angle_map", B) is task
    held = runs(db)
    assert set(held) == {A, B}
    assert all(r["status"] == "running" and r["version"].endswith(R.LEASE_SUFFIX) for r in held.values())
    # a second request now: already running
    again = await start(db)
    assert again == R.ReanalysisStart(False, R.R_RUNNING, old)

    await asyncio.sleep(0.05)
    assert [m[0] for m in maps(db)] == [old]  # the old map is still there while the model works
    gate.set()
    [outcome] = await drain()
    res = outcome.result
    assert outcome.status == "done" and res.outcome == "stored"

    # the map replaced, for the same group and message
    assert [(m, ids) for _, m, ids in maps(db)] == [(F.MSG, [A, B])]
    assert res.map_id != old and load_fresh_angle_map_for_item(db, A)["map"]["map_id"] == res.map_id
    # the snapshot of the old map, with the user's words
    [snap] = [r for r in db.tables[LOG] if r["kind"] == "snapshot"]
    assert snap["map_id"] == old and snap["evidence"] == CRITIQUE
    assert (snap["conversation_id"], snap["message_id"]) == (NOW_CONVO, NOW_MSG)
    assert snap["before"]["map"]["map_id"] == old and len(snap["before"]["angles"]) == 3
    # the guidance: the critique (no earlier requests), after the documents
    [prompt] = model.prompts
    assert prompt == base_prompt(J, O) + "\n" + "\n".join(guidance_block(format_guidance(CRITIQUE)))
    # billed to the CURRENT turn
    rows = [r for flush in ledger for r in flush]
    assert [r["agent"] for r in rows] == ["angle_mapper"]
    assert (rows[0]["user_id"], rows[0]["conversation_id"], rows[0]["message_id"]) == (USER, NOW_CONVO, NOW_MSG)
    # leases closed, nothing in flight
    assert all(r["status"] == "done" and r["lease_until"] is None for r in runs(db).values())
    assert _runtime.inflight("angle_map", A) is None and R._JOBS == set()


@pytest.mark.asyncio
async def test_the_guidance_is_the_critique_plus_the_earlier_requests(monkeypatch):
    model = use(monkeypatch, Model())
    db, old = mapped()
    client = E.edit_client(db, old, {"side": "claimant", "party": "المعترض"}, evidence="خل الموكل هو المعترض",
                           user_id=USER, conversation_id=F.CONVO, message_id="msg-5")
    assert client.ok
    gone = E.edit_angle(db, old, 2, {"title": "عنوان مؤقت"}, evidence="غيّر عنوان الخيط الثاني",
                        user_id=USER, conversation_id=F.CONVO, message_id="msg-6")
    assert gone.ok and E.undo_edit(db, gone.edit_id, USER, evidence="تراجع عن تغيير العنوان").ok
    again = E.edit_angle(db, old, 1, {"strength": "weak"}, evidence="  خل الموكل   هو المعترض ",
                         user_id=USER, conversation_id=F.CONVO)
    assert again.ok
    third = E.remove_gap(db, old, 1, evidence="احذف الثغرة الأولى", user_id=USER)
    assert third.ok

    assert (await start(db)).ok
    await drain()
    [prompt] = model.prompts
    earlier = ["خل الموكل هو المعترض", "احذف الثغرة الأولى"]  # undone + undo rows out, the repeat once
    want = format_guidance(CRITIQUE, earlier)
    assert prompt.endswith("\n".join([GUIDANCE_OPEN, want, GUIDANCE_CLOSE]))
    assert prompt.startswith(base_prompt(J, O))
    assert want == "\n".join(["### This request", CRITIQUE,
                              "### Their earlier change requests on that analysis (oldest first)",
                              "- خل الموكل هو المعترض", "- احذف الثغرة الأولى"])


def test_earlier_requests_lineage_order_dedupe_and_cap():
    db = FakeSupabase()
    db.tables[LOG] = [
        log_row("old-map", "edit_client", "الموكل هو المعترض", "2026-11-20T01:00:00+00:00"),
        log_row("old-map", "snapshot", "التحليل الأول ضعيف", "2026-11-21T01:00:00+00:00"),  # makes old-map lineage
        log_row("map-1", "edit_angle", "اجعل الخيط الأول قوياً", "2026-11-22T01:00:00+00:00"),
        log_row("map-1", "edit_angle", "غيّر هذا ثم تراجعت عنه", "2026-11-22T02:00:00+00:00", undone=True),
        log_row("map-1", "undo", "تراجع", "2026-11-22T03:00:00+00:00"),
        log_row("map-1", "add_gap", "الموكل  هو   المعترض", "2026-11-22T04:00:00+00:00"),  # a repeat
        log_row("map-1", "edit_angle", CRITIQUE, "2026-11-22T05:00:00+00:00"),            # the critique itself
        log_row("map-1", "edit_angle", "سطر\nفي سطرين", "2026-11-22T06:00:00+00:00"),
        log_row("map-1", "snapshot", "لقطة هذا التشغيل", "2026-11-22T07:00:00+00:00"),
        log_row("other-map", "edit_angle", "خريطة أخرى", "2026-11-22T08:00:00+00:00", items=(C,)),
        log_row("other-map", "snapshot", "لقطة أخرى", "2026-11-22T09:00:00+00:00", items=(C,)),
        log_row("map-1", "edit_angle", "مستخدم آخر", "2026-11-22T10:00:00+00:00", user_id=F.OTHER_USER),
    ]
    got = R.earlier_requests(db, "map-1", [A, B], USER, exclude_ids=["e-2026-11-22T07:00:00+00:00"], skip=[CRITIQUE])
    assert got == ["الموكل هو المعترض", "التحليل الأول ضعيف", "اجعل الخيط الأول قوياً", "سطر في سطرين"]
    # over the cap the OLDEST go; one long line is cut
    small = R.earlier_requests(db, "map-1", [A, B], USER, exclude_ids=["e-2026-11-22T07:00:00+00:00"],
                               skip=[CRITIQUE], max_chars=40)
    assert small == ["اجعل الخيط الأول قوياً", "سطر في سطرين"]
    db.tables[LOG].append(log_row("map-1", "edit_angle", "ب" * 900, "2026-11-23T01:00:00+00:00"))
    long = R.earlier_requests(db, "map-1", [A, B], USER, skip=[CRITIQUE])
    assert long[-1] == "ب" * (R.EARLIER_LINE_MAX_CHARS - 1) + "…"
    assert sum(len(x) + 3 for x in long) <= R.EARLIER_MAX_CHARS
    db.fail.add(LOG)
    assert R.earlier_requests(db, "map-1", [A, B], USER) == []


@pytest.mark.asyncio
async def test_a_second_reanalysis_keeps_the_requests_made_before_the_first(monkeypatch):
    model = use(monkeypatch, Model())
    db, old = mapped()
    assert E.edit_client(db, old, {"side": "claimant"}, evidence="الموكل هو المعترض", user_id=USER).ok
    assert (await start(db, critique="أعد التحليل")).ok
    await drain()
    assert (await start(db, critique="ما زال غير دقيق")).ok
    await drain()
    second = model.prompts[-1]
    assert second.endswith("\n".join([
        GUIDANCE_OPEN, "### This request", "ما زال غير دقيق",
        "### Their earlier change requests on that analysis (oldest first)",
        "- الموكل هو المعترض", "- أعد التحليل", GUIDANCE_CLOSE,
    ]))


@pytest.mark.asyncio
async def test_a_failed_model_keeps_the_old_map_and_releases_the_lease(monkeypatch, ledger):
    use(monkeypatch, Model(fail=True))
    db, old = mapped()
    assert (await start(db)).ok
    [outcome] = await drain()
    assert outcome.status == "done" and outcome.result.outcome == "failed_llm"
    assert [m[0] for m in maps(db)] == [old]
    assert all(r["status"] == "done" and r["lease_until"] is None for r in runs(db).values())
    assert _runtime.inflight("angle_map", A) is None and R._JOBS == set()
    # a failed attempt still bills what it used, to the current turn
    assert {(r["conversation_id"], r["message_id"]) for f in ledger for r in f} <= {(NOW_CONVO, NOW_MSG)}


@pytest.mark.asyncio
async def test_an_unexpected_error_still_releases_the_lease(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(runner_mod, "run_message_angle_map", boom)
    db, old = mapped()
    assert (await start(db)).ok
    [outcome] = await drain()
    assert outcome.status == "failed"
    assert all(r["status"] == "failed" and r["lease_until"] is None for r in runs(db).values())
    assert [m[0] for m in maps(db)] == [old] and _runtime.inflight("angle_map", B) is None


@pytest.mark.asyncio
async def test_no_snapshot_no_run(monkeypatch, ledger):
    model = use(monkeypatch, Model())
    db, old = mapped()
    db.fail.add((LOG, "insert"))
    assert (await start(db)).ok
    [outcome] = await drain()
    assert outcome.result.outcome == "skipped_no_snapshot"
    assert model.prompts == [] and [m[0] for m in maps(db)] == [old]
    assert [r for f in ledger for r in f] == []
    assert all(r["status"] == "done" and r["lease_until"] is None for r in runs(db).values())


@pytest.mark.asyncio
async def test_the_critique_is_decoded_then_masked_with_the_documents(monkeypatch):
    from backend.app.services import masking_service as ms

    codec = PrivacyCodec(enabled=True, rng=random.Random(5))
    monkeypatch.setattr(runner_mod, "_codec_for", lambda supabase, uid: codec)
    model = use(monkeypatch, Model())
    db, old = mapped()
    masked = codec.encode(f"الرقم {F.IQAMA} في التحليل خطأ")  # what the agent read
    fake = codec.real_to_fake[F.IQAMA]
    assert fake in masked and F.IQAMA not in masked
    token = ms.set_active_codec(codec)
    try:
        assert (await start(db, critique=masked)).ok
    finally:
        ms.reset_active_codec(token)
    await drain()
    [snap] = [r for r in db.tables[LOG] if r["kind"] == "snapshot"]
    assert snap["evidence"] == f"الرقم {F.IQAMA} في التحليل خطأ"  # stored real
    [prompt] = model.prompts
    assert f"الرقم {fake} في التحليل خطأ" in prompt and F.IQAMA not in prompt  # masked once, not twice


# ── a carried map (a «الملفات» file attached to a new conversation) ──────────


def _carried_db() -> tuple[FakeSupabase, str]:
    """The judgment + the objection mapped in ``CONVO`` (each item its file's first
    item: ``file_id`` = its id); the judgment's file attached again in ``CONVO_2``
    as item G (message ``MSG_2``) — no map of its own."""
    db = FakeSupabase(F.tables(J, O))
    for r in db.tables["workspace_items"]:
        r["file_id"] = r["item_id"]
    db.tables["workspace_items"].append(F.item_row(J, item_id=F.ITEM_G, conversation_id=F.CONVO_2, file_id=A,
                                                   wi_seq=1, created_at="2026-11-21T08:51:00+00:00"))
    db.tables["message_attachments"].append({"message_id": F.MSG_2, "document_id": F.ITEM_G,
                                             "created_at": F.MSG_2_AT})
    return db, _store(db, J, O)


@pytest.mark.asyncio
async def test_a_carried_map_is_rerun_for_its_own_whole_group(monkeypatch, ledger):
    model = use(monkeypatch, Model())
    db, old = _carried_db()
    got = await start(db, item_ids=(F.ITEM_G,), conversation_id=F.CONVO_2)
    assert got == R.ReanalysisStart(True, "", old)
    [task] = list(R._JOBS)
    assert _runtime.inflight("angle_map", F.ITEM_G) is task  # the carrying item sees it in flight too
    [outcome] = await drain()
    assert outcome.result.outcome == "stored"
    # the original group (both files, its message, its conversation) — not G alone
    [(new, message, members)] = maps(db)
    assert (message, members) == (F.MSG, [A, B]) and new != old
    assert db.tables[MAPS_TABLE][0]["conversation_id"] == F.CONVO
    assert len(model.prompts) == 1 and model.prompts[0].startswith(base_prompt(J, O))
    rows = [r for f in ledger for r in f]
    assert {(r["conversation_id"], r["message_id"]) for r in rows} == {(F.CONVO_2, NOW_MSG)}
    assert load_angle_map_for_item(db, F.ITEM_G) is None  # G stays a carrier


@pytest.mark.asyncio
async def test_a_carried_file_takes_the_newest_map_of_its_file():
    db, old = _carried_db()
    # the same file mapped later in a third conversation
    db.tables["workspace_items"].append(F.item_row(J, item_id=F.ITEM_K, conversation_id="convo-3", file_id=A))
    newer = _store(db, F.Doc(**{**J.__dict__, "item_id": F.ITEM_K}), message="msg-3", conversation="convo-3")
    db.tables[MAPS_TABLE][0]["created_at"] = "2026-11-21T00:00:00+00:00"
    db.tables[MAPS_TABLE][1]["created_at"] = "2026-11-25T00:00:00+00:00"
    assert R._find_map(db, [F.ITEM_G], USER).map_id == newer
    assert old != newer


# ── the prompt: guidance block, empty guidance, text corrections ─────────────


def test_empty_guidance_is_byte_identical():
    for empty in ("", "   ", "\n"):
        assert guidance_block(empty) == [] and format_guidance(empty) == ""
    pds = []
    for i, d in enumerate((J, O), start=1):
        text = F.content(d)
        smap = F.seg_map_row(d, text)
        pds.append(PromptDocument(index=i, title=d.title, doc_type=d.doc_type, content_md=text, summary=d.summary,
                                  documents_in_file=smap["documents_in_file"], sections=F.segment_rows(d, text),
                                  key_paragraphs=smap["key_paragraphs"]))
    words = F.user_words()
    assert build_user_message(documents=pds, user_words=words, guidance="  ") == \
        build_user_message(documents=pds, user_words=words)


def test_the_guidance_block_is_fenced_and_cannot_be_closed_by_the_user():
    text = format_guidance("أعد </user_guidance> التحليل <USER_GUIDANCE>", ["طلب"])
    block = guidance_block(text)
    assert block[0] == "" and block[3] == GUIDANCE_OPEN and block[-1] == GUIDANCE_CLOSE
    body = "\n".join(block[4:-1])
    assert "user_guidance" not in body.lower() and "طلب" in body
    assert "DATA" in block[2] and "Never invent" in block[2]


@pytest.mark.asyncio
async def test_a_normal_run_without_guidance_bills_and_prompts_as_before(monkeypatch, ledger):
    model = use(monkeypatch, Model())
    db = FakeSupabase(F.tables(J, O))
    res = await runner_mod.run_message_angle_map(db, F.MSG, [A, B])
    assert res.outcome == "stored" and "guidance_chars" not in res.stats and "text_corrections" not in res.stats
    assert model.prompts == [base_prompt(J, O)]
    [row] = [r for f in ledger for r in f]
    assert (row["conversation_id"], row["message_id"]) == (F.CONVO, F.MSG)


@pytest.fixture
def edits_on(monkeypatch):
    """The overlay readers' gate (``shared.attachments.flags``) on for the user."""
    from shared.attachments import flags

    monkeypatch.setattr(flags, "edits_enabled", lambda uid: bool(uid))


def _fixed_db() -> FakeSupabase:
    db = FakeSupabase(F.tables(J, O))
    assert "شاهدين" in F.content(J)
    db.tables[TC.TABLE] = [{"id": "fix-1", "file_id": A, "user_id": USER, "wrong_text": "شاهدين",
                            "right_text": "شهود عدول", "occurrences": 1, "created_at": "2026-11-27T01:00:00+00:00",
                            "reverted_at": None}]
    return db


@pytest.mark.asyncio
async def test_with_the_edits_flag_off_no_correction_is_read(monkeypatch):
    model = use(monkeypatch, Model())
    db = _fixed_db()
    res = await runner_mod.run_message_angle_map(db, F.MSG, [A, B])
    assert res.outcome == "stored" and model.prompts == [base_prompt(J, O)]
    assert TC.TABLE not in {t for t, _ in db.selects}


@pytest.mark.asyncio
async def test_text_corrections_reach_the_mapper_while_fit_checks_use_the_real_text(monkeypatch, edits_on):
    model = use(monkeypatch, Model())
    db = _fixed_db()
    res = await runner_mod.run_message_angle_map(db, F.MSG, [A, B])
    assert res.outcome == "stored" and res.excluded == {} and res.stats["text_corrections"] == 1
    [prompt] = model.prompts
    assert "شهود عدول" in prompt and "شاهدين" not in prompt
    # only the paragraph text changed: the header counts and every other byte are the real text's
    assert prompt == base_prompt(J, O).replace("شاهدين", "شهود عدول")
    assert f"{len(F.content(J)):,} characters" in prompt
    # the members were judged on the real text: the map stores the real lengths and stays fresh
    stored = load_fresh_angle_map_for_item(db, A)
    assert stored and [i["content_chars"] for i in stored["items"]] == [len(F.content(J)), len(F.content(O))]


@pytest.mark.asyncio
async def test_a_reverted_correction_is_not_applied(monkeypatch, edits_on):
    model = use(monkeypatch, Model())
    db = FakeSupabase(F.tables(J, O))
    db.tables[TC.TABLE] = [{"id": "fix-1", "file_id": A, "user_id": USER, "wrong_text": "شاهدين",
                            "right_text": "شهود", "occurrences": 1, "created_at": "2026-11-27T01:00:00+00:00",
                            "reverted_at": "2026-11-27T02:00:00+00:00"}]
    res = await runner_mod.run_message_angle_map(db, F.MSG, [A, B])
    assert res.outcome == "stored" and model.prompts == [base_prompt(J, O)]


# ── review fixes: no orphaned lease (9), snapshot undo (10), undo consistency (15) ──


@pytest.mark.asyncio
async def test_a_claim_failing_after_the_first_file_releases_it(monkeypatch):
    from agents.helpers import status

    real = status.SupabaseHelperRunStore.claim
    calls: list[str] = []

    def flaky(self, **kw):
        calls.append(kw["item_id"])
        if len(calls) == 2:
            raise RuntimeError("network")
        return real(self, **kw)

    monkeypatch.setattr(status.SupabaseHelperRunStore, "claim", flaky)
    db, old = mapped()
    got = await start(db)
    assert got == R.ReanalysisStart(False, R.R_ERROR, old)
    assert calls == [A, B] and set(runs(db)) == {A}
    assert runs(db)[A]["status"] == "done" and runs(db)[A]["lease_until"] is None  # released, not leaked
    assert R._JOBS == set() and _runtime.inflight("angle_map", A) is None


@pytest.mark.asyncio
async def test_a_caller_timeout_during_the_claim_leaves_no_orphan_lease(monkeypatch):
    import time

    real = R._claim

    def slow(*a, **k):
        time.sleep(0.2)
        return real(*a, **k)

    monkeypatch.setattr(R, "_claim", slow)
    use(monkeypatch, Model())
    db, old = mapped()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(start(db), 0.05)
    # the shielded step goes on: the run is scheduled, runs, and closes its leases
    await asyncio.wait(list(R._STARTING), timeout=5)
    [outcome] = await drain()
    assert outcome.result.outcome == "stored" and [m[0] for m in maps(db)] != [old]
    assert set(runs(db)) == {A, B}
    assert all(r["status"] == "done" and r["lease_until"] is None for r in runs(db).values())


def logrow(db: FakeSupabase, row_id: str) -> dict:
    """A log row as stored NOW (the fake copies a table's rows on every insert)."""
    return next(r for r in db.tables[LOG] if r["id"] == row_id)


async def _reanalysed(monkeypatch, **model: Any) -> tuple[FakeSupabase, str, dict]:
    use(monkeypatch, Model(**model))
    db, old = mapped()
    assert (await start(db)).ok
    await drain()
    [snap] = [r for r in db.tables[LOG] if r["kind"] == "snapshot"]
    return db, old, snap


@pytest.mark.asyncio
async def test_undo_of_a_snapshot_is_refused_while_a_run_is_going(monkeypatch):
    db, old, snap = await _reanalysed(monkeypatch)
    [(current, _, _)] = maps(db)

    async def going():
        await asyncio.sleep(1)

    task = asyncio.get_running_loop().create_task(going())
    _runtime._INFLIGHT[("angle_map", B)] = task
    try:
        u = E.undo_edit(db, snap["id"], USER)
    finally:
        task.cancel()
        _runtime._INFLIGHT.pop(("angle_map", B), None)
    assert (u.ok, u.reason) == (False, E.R_UNDO_BUSY)
    # another process: a live lease on a member file
    until = (_runtime.utcnow() + timedelta(minutes=5)).isoformat()
    runs(db)[A].update(status="running", lease_until=until)
    assert E.undo_edit(db, snap["id"], USER).reason == E.R_UNDO_BUSY
    assert [m[0] for m in maps(db)] == [current] and logrow(db, snap["id"]).get("undone_at") is None
    # once nothing runs: restored, recorded, the snapshot marked
    runs(db)[A].update(status="done", lease_until=None)
    u = E.undo_edit(db, snap["id"], USER, evidence="رجّع الخيوط القديمة")
    assert u.ok and u.map_id not in (old, current)
    assert [m[0] for m in maps(db)] == [u.map_id]
    undo = next(r for r in db.tables[LOG] if r["kind"] == "undo")
    assert undo["id"] == u.edit_id and undo["map_id"] == u.map_id and undo["target"] == snap["id"]
    assert logrow(db, snap["id"])["undone_at"] and E.undo_edit(db, snap["id"], USER).reason == E.R_UNDO_DONE


@pytest.mark.asyncio
async def test_undo_of_a_snapshot_whose_reanalysis_failed_is_refused(monkeypatch):
    db, old, snap = await _reanalysed(monkeypatch, fail=True)
    u = E.undo_edit(db, snap["id"], USER)
    assert (u.ok, u.reason) == (False, E.R_UNDO_NOT_REPLACED)
    assert [m[0] for m in maps(db)] == [old] and logrow(db, snap["id"]).get("undone_at") is None
    assert [r["kind"] for r in db.tables[LOG]] == ["snapshot"]


@pytest.mark.asyncio
async def test_a_restore_whose_undo_row_cannot_be_written_restores_nothing(monkeypatch):
    db, old, snap = await _reanalysed(monkeypatch)
    [(current, _, _)] = maps(db)
    db.fail.add((LOG, "insert"))
    u = E.undo_edit(db, snap["id"], USER)
    assert (u.ok, u.reason) == (False, E.R_WRITE)
    assert [m[0] for m in maps(db)] == [current] and logrow(db, snap["id"]).get("undone_at") is None


@pytest.mark.asyncio
async def test_a_failed_restore_drops_its_undo_row_and_unmarks_the_snapshot(monkeypatch):
    db, old, snap = await _reanalysed(monkeypatch)
    [(current, _, _)] = maps(db)
    db.fail.add((MAPS_TABLE, "insert"))
    u = E.undo_edit(db, snap["id"], USER)
    assert not u.ok and u.reason == E.R_WRITE
    assert [m[0] for m in maps(db)] == [current] and logrow(db, snap["id"]).get("undone_at") is None
    assert [r["kind"] for r in db.tables[LOG]] == ["snapshot"]


def test_a_second_undo_racing_the_first_cannot_reinsert_a_removed_thread(monkeypatch):
    db, map_id = mapped()
    rem = E.remove_angle(db, map_id, 2, evidence="احذف الخيط الثاني", user_id=USER)
    assert rem.ok
    real = E._inverse

    def raced(supabase, ctx, row):
        out = real(supabase, ctx, row)
        next(r for r in db.tables[LOG] if r["id"] == rem.edit_id)["undone_at"] = "2026-11-27T00:00:00+00:00"
        return out  # the other undo marked it between this one's read and its mark

    monkeypatch.setattr(E, "_inverse", raced)
    u = E.undo_edit(db, rem.edit_id, USER)
    assert (u.ok, u.reason) == (False, E.R_UNDO_DONE)
    assert [a["seq"] for a in db.tables["attachment_angles"]] == [1, 3]  # not re-inserted


def test_a_failed_reapply_rolls_the_mark_back_and_a_failed_mark_changes_nothing():
    db, map_id = mapped()
    rem = E.remove_angle(db, map_id, 2, evidence="احذف الخيط الثاني", user_id=USER)

    db.fail.add((LOG, "update"))  # the mark cannot be written
    assert E.undo_edit(db, rem.edit_id, USER).reason == E.R_WRITE
    assert logrow(db, rem.edit_id).get("undone_at") is None and [a["seq"] for a in db.tables["attachment_angles"]] == [1, 3]
    db.fail.discard((LOG, "update"))

    db.fail.add(("attachment_angles", "insert"))  # the thread cannot be put back
    assert E.undo_edit(db, rem.edit_id, USER).reason == E.R_WRITE
    assert logrow(db, rem.edit_id).get("undone_at") is None and [a["seq"] for a in db.tables["attachment_angles"]] == [1, 3]
    db.fail.discard(("attachment_angles", "insert"))

    u = E.undo_edit(db, rem.edit_id, USER)
    assert u.ok and logrow(db, rem.edit_id)["undone_at"] and sorted(a["seq"] for a in db.tables["attachment_angles"]) == [1, 2, 3]
    assert E.undo_edit(db, rem.edit_id, USER).reason == E.R_UNDO_DONE
