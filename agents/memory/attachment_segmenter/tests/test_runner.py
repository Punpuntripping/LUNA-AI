"""Runner — guards, masking, repair + re-attempt, split, repeats, slicing, store, ledger."""
from __future__ import annotations

import asyncio
import random
import re
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from agents.memory.attachment_segmenter import runner as runner_mod
from agents.memory.attachment_segmenter import load_segment_maps, run_attachment_segmenter
from agents.memory.attachment_segmenter.agent import create_attachment_segmenter
from agents.memory.attachment_segmenter.deps import SegmenterDeps
from agents.memory.attachment_segmenter.models import SegmentMap
from agents.memory.attachment_segmenter.paragraphs import PARAGRAPH_RULE_VERSION, paragraphize
from agents.memory.attachment_segmenter.prompts import GENERIC_LABELS, PROMPT_VERSION
from agents.memory.ocr_extractor.mistral_ocr import join_pages
from agents.utils import agent_models
from agents.utils import segmenter_flags
from shared.privacy import PrivacyCodec

from .fake_supabase import FakeSupabase

ITEM = "11111111-2222-3333-4444-555555555555"
USER = "user-7"
CONVO = "convo-9"
NATIONAL_ID = "1098765432"

_rng = random.Random(3)
_VOCAB = ["وحيث", "إن", "المدعي", "طلب", "إلزام", "المدعى", "عليه", "بسداد", "المبلغ", "المتبقي",
          "من", "قيمة", "العقد", "وقد", "أنكر", "ذلك", "وقدم", "مستندات", "تثبت", "السداد"]


def _line(words: int = 30) -> str:
    return " ".join(_rng.choice(_VOCAB) for _ in range(words)) + f" {_rng.randint(100, 999)}"


def _doc(n: int) -> str:
    """n paragraphs of ~190 chars; paragraph 3 carries a national id."""
    lines = [_line() for _ in range(n)]
    # neutral words on both sides: the codec leaves a number within 16 chars of a
    # money word («المبلغ» is in _VOCAB) unmasked on purpose
    lines[2] = f"بيانات الطرف الأول هوية {NATIONAL_ID} وهو مواطن سعودي الجنسية " + lines[2]
    return "\n".join(lines)


def _row(content: str, **kw: Any) -> dict:
    return {
        "item_id": ITEM, "user_id": USER, "conversation_id": CONVO, "case_id": None,
        "kind": "attachment", "title": "حكم ابتدائي", "summary": "حكم في دعوى مطالبة مالية",
        "content_md": content, "metadata": {"doc_type": "judgment"}, "deleted_at": None, **kw,
    }


def _db(content: str, pages: list[dict] | None = None, **kw: Any) -> FakeSupabase:
    return FakeSupabase({"workspace_items": [_row(content, **kw)], "attachment_pages": pages or []})


# ── a scripted model ─────────────────────────────────────────────────────────

_RANGE_RE = re.compile(r"you map P(\d+)–P(\d+)")
_COUNT_RE = re.compile(r"\| (\d+) paragraphs")


def prompt_range(prompt: str) -> tuple[int, int]:
    m = _RANGE_RE.search(prompt)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1, int(_COUNT_RE.search(prompt).group(1))


def full(segments: list[dict], docs: list[dict] | None = None, keys: list[dict] | None = None) -> dict:
    """A complete model output: v4.4's schema requires all three fields."""
    return {"documents_in_file": docs or [], "segments": segments, "key_paragraphs": keys or []}


def good_map(a: int, b: int, what: str = "قسم") -> dict:
    """metadata · core · boilerplate over a..b."""
    if b - a < 4:
        return full([{"from_p": a, "to_p": b, "label": "grounds", "value": "core", "what": what}])
    return {
        "documents_in_file": [{"from_p": a, "to_p": b, "name": "الحكم"}],
        "segments": [
            {"from_p": a, "to_p": a + 1, "label": "header", "value": "metadata", "what": "بيانات الحكم"},
            {"from_p": a + 2, "to_p": b - 1, "label": "grounds", "value": "core", "what": what},
            {"from_p": b, "to_p": b, "label": "signatures", "value": "boilerplate", "what": "التوقيع"},
        ],
        "key_paragraphs": [{"p": a + 2, "why": "سبب"}],
    }


def _user_text(messages: list) -> str:
    for msg in messages:
        for part in getattr(msg, "parts", []):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content
    return ""


class Script:
    """Collects every call; ``handler(prompt, instructions, n) -> dict`` (or raises)."""

    def __init__(self, handler: Callable[[str, str, int], Any]) -> None:
        self.handler = handler
        self.calls: list[dict] = []

    def factory(self, instructions: str) -> Agent:
        async def fn(messages: list, info: AgentInfo) -> ModelResponse:
            prompt = _user_text(messages)
            self.calls.append({"instructions": instructions, "prompt": prompt})
            out = self.handler(prompt, instructions, len(self.calls))
            if asyncio.iscoroutine(out):
                out = await out
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, out)])

        return Agent(
            FunctionModel(fn), name="attachment_segmenter", deps_type=SegmenterDeps,
            output_type=SegmentMap, instructions=instructions, retries=1,
        )


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(runner_mod, "segmenter_enabled", lambda uid: True)
    monkeypatch.setattr(runner_mod, "_codec_for", lambda supabase, uid: None)


def use(monkeypatch, handler) -> Script:
    script = Script(handler)
    monkeypatch.setattr(runner_mod, "create_attachment_segmenter", script.factory)
    return script


def whole(prompt: str, instructions: str, n: int) -> dict:
    return good_map(*prompt_range(prompt))


def assert_sections_cover(db: FakeSupabase, content: str) -> list[dict]:
    rows = sorted(db.tables["attachment_segments"], key=lambda r: r["seq"])
    covered = [False] * len(content)
    for r in rows:
        assert r["content_md"] == content[r["char_start"]:r["char_end"]]
        for i in range(r["char_start"], r["char_end"]):
            assert not covered[i]
            covered[i] = True
    for i, ch in enumerate(content):
        if not covered[i]:
            assert ch.isspace(), f"char {i} ({ch!r}) is in no section"
    for prev, nxt in zip(rows, rows[1:]):
        assert nxt["from_p"] == prev["to_p"] + 1
        assert nxt["char_start"] >= prev["char_end"]
    return rows


# ── happy path ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_segments_and_stores(monkeypatch):
    content = _doc(40)
    db = _db(content)
    script = use(monkeypatch, whole)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "ok", res
    assert (res.split, res.attempts, res.segments, res.paragraphs) == (False, 1, 3, 40)
    assert len(script.calls) == 1
    # the model saw numbered paragraphs, the title, the summary and the judgment labels
    prompt, instr = script.calls[0]["prompt"], script.calls[0]["instructions"]
    assert "[P001] " in prompt and "[P040] " in prompt
    assert "حكم ابتدائي" in prompt and "حكم في دعوى مطالبة مالية" in prompt
    assert "the speaker rule" in instr and "`claim_requests`" in instr and "start_quote" not in instr
    assert "## Summary of the document (context only — never map it)" in prompt and "## Paragraphs" in prompt

    [m] = db.tables["attachment_segment_maps"]
    assert m["content_chars"] == len(content) and m["user_id"] == USER
    assert m["paragraph_rule_version"] == PARAGRAPH_RULE_VERSION and m["prompt_version"] == PROMPT_VERSION
    assert m["split"] is False and m["documents_in_file"][0]["name"] == "الحكم"
    rows = assert_sections_cover(db, content)
    assert [r["value"] for r in rows] == ["metadata", "core", "boilerplate"]
    assert all(r["page_from"] is None for r in rows)
    assert res.core_chars == len(rows[1]["content_md"])

    stored = load_segment_maps(db, [ITEM])[ITEM]
    assert [s.value for s in stored.segments] == ["metadata", "core", "boilerplate"]


@pytest.mark.asyncio
@pytest.mark.parametrize("doc_type, per_line", [("judgment", 2), ("hearing_minutes", 2), ("contract", 1)])
async def test_paragraph_rule_follows_the_doc_type(monkeypatch, doc_type, per_line):
    lines = [f"{_line(50)}. {_line(50)}" for _ in range(12)]  # ~620 chars each, a sentence end mid-line
    content = "\n".join(lines)
    db = _db(content, metadata={"doc_type": doc_type})
    script = use(monkeypatch, whole)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "ok" and res.paragraphs == 12 * per_line
    assert f"| {12 * per_line} paragraphs" in script.calls[0]["prompt"]
    assert_sections_cover(db, content)


@pytest.mark.asyncio
async def test_cost_lands_in_the_ledger(monkeypatch):
    db = _db(_doc(40))
    use(monkeypatch, whole)
    await run_attachment_segmenter(db, ITEM)
    rows = db.tables["llm_calls"]
    assert rows and all(r["agent"] == "attachment_segmenter.run" for r in rows)
    assert rows[0]["user_id"] == USER and rows[0]["conversation_id"] == CONVO
    assert rows[0]["tokens_in"] > 0


@pytest.mark.asyncio
async def test_an_open_ledger_scope_is_used(monkeypatch):
    """Under the helper frame's per-run scope the rows carry the turn's message."""
    from agents.utils.usage_sink import collect_llm_calls

    db = _db(_doc(40))
    use(monkeypatch, whole)
    with collect_llm_calls(db, conversation_id=CONVO, user_id=USER, message_id="msg-1"):
        res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "ok"
    assert [r["message_id"] for r in db.tables["llm_calls"]] == ["msg-1"]


@pytest.mark.asyncio
async def test_page_numbers_reach_the_sections(monkeypatch):
    md = ["\n".join(_line() for _ in range(10)) for _ in range(4)]
    content, offsets = join_pages(md)
    pages = [{"item_id": ITEM, "page_no": i + 1, "char_start": off, "content_md": m}
             for i, (off, m) in enumerate(zip(offsets, md))]
    db = _db(content, pages=pages)
    use(monkeypatch, whole)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "ok"
    rows = sorted(db.tables["attachment_segments"], key=lambda r: r["seq"])
    assert (rows[0]["page_from"], rows[-1]["page_to"]) == (1, 4)


# ── guards ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("db_kw, outcome", [
    ({"kind": "agent_search"}, "skipped_not_attachment"),
    ({"deleted_at": "2026-11-11T00:00:00Z"}, "not_found"),
])
async def test_row_guards(monkeypatch, db_kw, outcome):
    script = use(monkeypatch, whole)
    res = await run_attachment_segmenter(_db(_doc(40), **db_kw), ITEM)
    assert res.outcome == outcome and not script.calls


@pytest.mark.asyncio
async def test_missing_row(monkeypatch):
    script = use(monkeypatch, whole)
    res = await run_attachment_segmenter(FakeSupabase(), ITEM)
    assert res.outcome == "not_found" and not script.calls


@pytest.mark.asyncio
async def test_short_attachment_is_skipped(monkeypatch):
    script = use(monkeypatch, whole)
    content = ("ن" * 99 + "\n") * 50  # exactly 5,000 chars
    assert len(content) == 5000
    res = await run_attachment_segmenter(_db(content), ITEM)
    assert res.outcome == "skipped_short" and not script.calls


@pytest.mark.asyncio
async def test_flag_off_is_skipped(monkeypatch):
    monkeypatch.setattr(runner_mod, "segmenter_enabled", lambda uid: uid == "someone-else")
    script = use(monkeypatch, whole)
    res = await run_attachment_segmenter(_db(_doc(40)), ITEM)
    assert res.outcome == "skipped_disabled" and not script.calls


@pytest.mark.asyncio
async def test_fresh_map_is_kept_stale_map_is_redone(monkeypatch):
    content = _doc(40)
    db = _db(content)
    script = use(monkeypatch, whole)
    assert (await run_attachment_segmenter(db, ITEM)).outcome == "ok"
    assert (await run_attachment_segmenter(db, ITEM)).outcome == "skipped_fresh"
    assert len(script.calls) == 1
    assert (await run_attachment_segmenter(db, ITEM, force=True)).outcome == "ok"
    assert len(script.calls) == 2
    # content changed (re-OCR / edit) → the stored map is stale → re-run
    db.tables["workspace_items"][0]["content_md"] = content + "\nسطر جديد"
    assert (await run_attachment_segmenter(db, ITEM)).outcome == "ok"
    assert len(script.calls) == 3
    assert db.tables["attachment_segment_maps"][0]["content_chars"] == len(content) + len("\nسطر جديد")
    assert_sections_cover(db, content + "\nسطر جديد")


@pytest.mark.asyncio
async def test_a_map_without_segments_is_redone(monkeypatch):
    content = _doc(40)
    db = _db(content)
    db.tables["attachment_segment_maps"].append({"item_id": ITEM, "content_chars": len(content)})
    use(monkeypatch, whole)
    assert (await run_attachment_segmenter(db, ITEM)).outcome == "ok"


# ── validator / repair / re-attempt ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_small_defects_are_repaired(monkeypatch):
    def handler(prompt, instr, n):
        a, b = prompt_range(prompt)
        return full([
            {"from_p": "P001", "to_p": "P010", "label": "header", "value": "metadata", "what": ""},
            {"from_p": 12, "to_p": b + 5, "label": "grounds", "value": "Core", "what": ""},  # gap + out of range
        ])

    db = _db(_doc(40))
    use(monkeypatch, handler)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "ok" and res.attempts == 1
    assert res.repairs == {"gaps": 1, "gap_paragraphs": 1, "gap_chars": res.repairs["gap_chars"], "out_of_range": 1}
    assert_sections_cover(db, db.tables["workspace_items"][0]["content_md"])


@pytest.mark.asyncio
async def test_invalid_then_valid_is_one_reattempt(monkeypatch):
    def handler(prompt, instr, n):
        a, b = prompt_range(prompt)
        if n == 1:
            return full([{"from_p": 1, "to_p": b // 2, "label": "x", "value": "core", "what": ""}])
        return good_map(a, b)

    db = _db(_doc(40))
    script = use(monkeypatch, handler)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "ok" and res.attempts == 2 and len(script.calls) == 2
    ledger = db.tables["llm_calls"]
    assert len(ledger) == 2  # both attempts billed
    assert res.tokens_in == sum(r["tokens_in"] for r in ledger) > 0
    assert res.tokens_out == sum(r["tokens_out"] for r in ledger) > 0


@pytest.mark.asyncio
async def test_invalid_twice_fails_and_stores_nothing(monkeypatch):
    def handler(prompt, instr, n):
        return full([{"from_p": 1, "to_p": 5, "label": "x", "value": "core", "what": ""}])

    db = _db(_doc(40))
    use(monkeypatch, handler)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "failed_invalid" and res.attempts == 2
    assert db.tables["attachment_segment_maps"] == [] and db.tables["attachment_segments"] == []


@pytest.mark.asyncio
async def test_model_errors_twice_fails(monkeypatch):
    def handler(prompt, instr, n):
        raise RuntimeError("provider down")

    db = _db(_doc(40))
    use(monkeypatch, handler)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "failed_llm" and res.attempts == 2
    assert db.tables["attachment_segment_maps"] == []


@pytest.mark.asyncio
async def test_a_failed_run_still_bills_its_requests(monkeypatch):
    """Output that never validates: each attempt spends 2 requests (retries=1)
    and raises — what those requests cost still reaches llm_calls."""
    def handler(prompt, instr, n):
        return {"not_segments": []}

    db = _db(_doc(40))
    script = use(monkeypatch, handler)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "failed_llm" and len(script.calls) == 4
    ledger = db.tables["llm_calls"]
    assert len(ledger) == 2 and all(r["requests"] == 2 and r["tokens_in"] > 0 for r in ledger)
    assert res.tokens_in == sum(r["tokens_in"] for r in ledger)


@pytest.mark.asyncio
async def test_a_hung_call_times_out_and_is_retried(monkeypatch):
    monkeypatch.setattr(runner_mod, "CALL_TIMEOUT_S", 0.2)

    async def handler(prompt, instr, n):
        if n == 1:
            await asyncio.sleep(5)
        return good_map(*prompt_range(prompt))

    use(monkeypatch, handler)
    res = await run_attachment_segmenter(_db(_doc(40)), ITEM)
    assert res.outcome == "ok" and res.attempts == 2


# ── split mode ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_long_document_is_split_in_two_and_stitched(monkeypatch):
    md = ["\n".join(_line() for _ in range(10)) for _ in range(20)]  # ~38k chars, 200 paragraphs
    content, offsets = join_pages(md)
    assert len(content) > 30_000
    pages = [{"item_id": ITEM, "page_no": i + 1, "char_start": off, "content_md": m}
             for i, (off, m) in enumerate(zip(offsets, md))]
    db = _db(content, pages=pages)

    def handler(prompt, instr, n):
        a, b = prompt_range(prompt)
        # both parts end / start with the same grounds section → merged at the seam
        return full([
            {"from_p": a, "to_p": a + 1, "label": "header", "value": "metadata" if a == 1 else "boilerplate", "what": ""},
            {"from_p": a + 2, "to_p": b, "label": "grounds", "value": "core", "what": f"تسبيب {a}"},
        ])

    script = use(monkeypatch, handler)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "ok" and res.split is True and len(script.calls) == 2
    p1, p2 = sorted(script.calls, key=lambda c: "part 2 of 2" in c["prompt"])
    assert "part 1 of 2" in p1["prompt"] and "Context after your part" in p1["prompt"]
    assert "First page of the document" in p2["prompt"] and "Context before your part" in p2["prompt"]
    assert "ONE PART of a longer document (2 of 2)" in p2["instructions"]
    a2, _ = prompt_range(p2["prompt"])
    paras = paragraphize(content)
    assert a2 == 101 and paras[a2 - 1].char_start == offsets[10]  # page 11 opens part 2
    rows = assert_sections_cover(db, content)
    assert [r["label"] for r in rows] == ["header", "grounds", "header", "grounds"]
    assert db.tables["attachment_segment_maps"][0]["split"] is True


# ── repeats ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_repeated_section_is_stored_as_repeat(monkeypatch):
    block = [_line() for _ in range(6)]
    lines = [_line() for _ in range(4)] + block + [_line() for _ in range(15)] + block + [_line() for _ in range(5)]
    content = "\n".join(lines)
    n = len(lines)

    def handler(prompt, instr, k):
        return full([
            {"from_p": 1, "to_p": 4, "label": "header", "value": "metadata", "what": ""},
            {"from_p": 5, "to_p": 10, "label": "evidence", "value": "core", "what": "التحقيق"},
            {"from_p": 11, "to_p": 25, "label": "defense", "value": "supporting", "what": ""},
            {"from_p": 26, "to_p": 31, "label": "evidence", "value": "core", "what": "التحقيق مكرر"},
            {"from_p": 32, "to_p": n, "label": "ruling", "value": "core", "what": ""},
        ])

    db = _db(content)
    use(monkeypatch, handler)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "ok" and res.repeats == 1
    values = [r["value"] for r in sorted(db.tables["attachment_segments"], key=lambda r: r["seq"])]
    assert values == ["metadata", "core", "supporting", "repeat", "core"]


# ── masking (وضع السرية) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_sees_masked_text_and_store_is_real(monkeypatch):
    codec = PrivacyCodec(enabled=True, rng=random.Random(11))
    monkeypatch.setattr(runner_mod, "_codec_for", lambda supabase, uid: codec)
    content = _doc(40)
    db = _db(content, title=f"صك رقم {NATIONAL_ID}")
    seen: dict = {}

    def handler(prompt, instr, n):
        seen["prompt"] = prompt
        seen["persisted_first"] = [dict(r) for r in db.tables["pii_mappings"]]
        fake = codec.real_to_fake[NATIONAL_ID]
        out = good_map(*prompt_range(prompt), what=f"هوية {fake}")
        out["documents_in_file"][0]["name"] = f"صك {fake}"
        out["key_paragraphs"][0]["why"] = f"رقم {fake}"
        return out

    use(monkeypatch, handler)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "ok"
    fake = codec.real_to_fake[NATIONAL_ID]
    assert fake != NATIONAL_ID
    assert NATIONAL_ID not in seen["prompt"] and fake in seen["prompt"]
    # the new fake reached pii_mappings BEFORE the model read it
    assert any(r["real_value"] == NATIONAL_ID and r["fake_value"] == fake for r in seen["persisted_first"])
    # stored real: descriptions decoded, section text sliced from the real content
    core = next(r for r in db.tables["attachment_segments"] if r["value"] == "core")
    assert core["what"] == f"هوية {NATIONAL_ID}"
    [m] = db.tables["attachment_segment_maps"]
    assert m["documents_in_file"][0]["name"] == f"صك {NATIONAL_ID}"
    assert m["key_paragraphs"][0]["why"] == f"رقم {NATIONAL_ID}"
    assert NATIONAL_ID in core["content_md"]  # paragraph 3 is inside the core section
    assert all(fake not in r["content_md"] for r in db.tables["attachment_segments"])


# ── labels by doc_type ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_doc_type_gets_the_generic_labels(monkeypatch):
    db = _db(_doc(40), metadata={})
    script = use(monkeypatch, whole)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "ok" and res.doc_type == ""
    instr = script.calls[0]["instructions"]
    assert GENERIC_LABELS in instr and "## Labels for this document type (unknown)" in instr
    assert "the speaker rule" not in instr


# ── never raises ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_failure_is_an_outcome(monkeypatch):
    db = _db(_doc(40))
    db.fail.add(("attachment_segments", "insert"))
    use(monkeypatch, whole)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "failed_store"
    assert db.tables["attachment_segment_maps"] == []


@pytest.mark.asyncio
async def test_db_failure_is_an_outcome(monkeypatch):
    db = _db(_doc(40))
    db.fail.add("workspace_items")
    use(monkeypatch, whole)
    res = await run_attachment_segmenter(db, ITEM)
    assert res.outcome == "error" and res.error == "RuntimeError"


# ── the real agent + slot ────────────────────────────────────────────────────


def test_slot_is_on_the_flash_policy():
    assert agent_models.AGENT_MODELS["attachment_segmenter"] is agent_models.AGENT_MODELS["artifact_summarizer"]


@pytest.mark.asyncio
async def test_real_factory_with_test_model():
    agent = create_attachment_segmenter("instructions")
    out = full([{"from_p": 1, "to_p": 3, "label": "grounds", "value": "core", "what": "x"}])
    with agent.override(model=TestModel(custom_output_args=out)):
        res = await agent.run("[P001] a\n[P002] b\n[P003] c", deps=SegmenterDeps(item_id=ITEM))
    assert isinstance(res.output, SegmentMap)
    assert res.output.segments[0].to_p == 3
    assert res.output.tracking_output() == {"segments": 1, "documents_in_file": 0, "key_paragraphs": 0}


# ── the gate ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("enabled, users, uid, expected", [
    (False, None, "u1", False),
    (True, None, "u1", True),
    (True, "", "u1", True),
    (True, "u1, u2", "u1", True),
    (True, "u2,u3", "u1", False),
    (True, "u2", None, False),
])
def test_segmenter_enabled(monkeypatch, enabled, users, uid, expected):
    settings = SimpleNamespace(ATTACHMENT_SEGMENTER_ENABLED=enabled, ATTACHMENT_SEGMENTER_USERS=users)
    monkeypatch.setattr(segmenter_flags, "get_settings", lambda: settings)
    assert segmenter_flags.segmenter_enabled(uid) is expected


def test_segmenter_enabled_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("no settings")

    monkeypatch.setattr(segmenter_flags, "get_settings", boom)
    assert segmenter_flags.segmenter_enabled("u1") is False
