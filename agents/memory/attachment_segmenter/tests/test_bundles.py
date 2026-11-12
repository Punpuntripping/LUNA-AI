"""Bundles — the small files of one message in ONE run (owner, 2026-11-12)."""
from __future__ import annotations

import asyncio
import random
import re
from typing import Any

import pytest

from agents.memory.attachment_segmenter import bundles as B
from agents.memory.attachment_segmenter import runner as runner_mod
from agents.memory.attachment_segmenter.bundles import (
    bundle_doc_type,
    bundle_for_item,
    bundle_id_for,
    cut_back,
    number_globally,
    plan_units,
    segmenter_applies_to_item,
    split_at_boundaries,
    split_evenly,
)
from agents.memory.attachment_segmenter.models import EmbeddedDoc, KeyParagraph, Segment, SegmentMap
from agents.memory.attachment_segmenter.paragraphs import paragraphize
from agents.memory.attachment_segmenter.prompts import BUNDLE_NOTE, CHUNK_NOTE, LABELS, TYPE_NOTES
from agents.memory.attachment_segmenter.runner import run_attachment_bundle, run_attachment_segmenter

from .fake_supabase import FakeSupabase
from .test_runner import Script, full, prompt_range

USER, CONVO, MSG = "user-7", "convo-9", "msg-1"
_rng = random.Random(21)
_VOCAB = ["وحيث", "إن", "المدعي", "طلب", "إلزام", "عليه", "بسداد", "قيمة", "العقد", "وقد",
          "أنكر", "ذلك", "وقدم", "مستندات", "تثبت", "السداد", "الدائرة", "قررت", "الجلسة", "التالية"]


def line(words: int = 30) -> str:
    return " ".join(_rng.choice(_VOCAB) for _ in range(words)) + f" {_rng.randint(100, 999)}"


def text(chars: int) -> str:
    """Lines of ~190 chars until about ``chars``."""
    out: list[str] = []
    while sum(len(x) + 1 for x in out) < chars:
        out.append(line())
    return "\n".join(out)[:chars].rstrip()


# ── plan_units (pure) ────────────────────────────────────────────────────────


def it(item_id: str, chars: int, msg: str | None = MSG, order: Any = None) -> dict:
    return {"item_id": item_id, "chars": chars, "message_id": msg, "order": order}


def test_owners_example():
    items = [it("a", 40_000, order=1), it("b", 20_000, order=2), it("c", 4_000, order=3),
             it("d", 2_000, order=4), it("e", 4_000, order=5)]
    assert plan_units(items) == [["a"], ["b"], ["c", "d", "e"]]


def test_upload_order_decides_members_and_units():
    items = [it("e", 4_000, order=5), it("c", 4_000, order=3), it("a", 40_000, order=1), it("d", 2_000, order=4)]
    assert plan_units(items) == [["a"], ["c", "d", "e"]]
    # the bundle sits where its first member was uploaded
    items = [it("c", 3_000, order=1), it("a", 9_000, order=2), it("d", 3_000, order=3)]
    assert plan_units(items) == [["c", "d"], ["a"]]


def test_small_files_totalling_5k_or_less_travel_whole():
    assert plan_units([it("a", 2_500, order=1), it("b", 2_500, order=2)]) == []
    assert plan_units([it("a", 2_500, order=1), it("b", 2_501, order=2)]) == [["a", "b"]]


def test_a_single_small_file_is_never_a_unit():
    assert plan_units([it("a", 4_999)]) == []
    assert plan_units([it("a", 5_000)]) == []      # exactly 5,000 is not over it
    assert plan_units([it("a", 5_001)]) == [["a"]]


def test_no_message_link_follows_rule_1_only():
    items = [it("a", 4_000, msg=None, order=1), it("b", 4_000, msg="", order=2), it("c", 9_000, msg=None, order=3)]
    assert plan_units(items) == [["c"]]


def test_one_bundle_per_message_and_messages_apart():
    items = [it("a", 3_000, "m1", 1), it("b", 3_000, "m2", 2), it("c", 3_000, "m1", 3), it("d", 3_000, "m2", 4)]
    assert plan_units(items) == [["a", "c"], ["b", "d"]]


def test_empty_files_and_threshold():
    assert plan_units([it("a", 0, order=1), it("b", 4_000, order=2), it("c", 4_000, order=3)]) == [["b", "c"]]
    assert plan_units([it("a", 600, order=1), it("b", 900, order=2)], min_chars=1_000) == [["a", "b"]]


def test_doc_type_and_bundle_id():
    assert bundle_doc_type(["judgment", "judgment", "letter"]) == "judgment"
    assert bundle_doc_type(["judgment", "letter"]) == B.MIXED_DOC_TYPE
    assert bundle_doc_type(["", "", "judgment"]) == ""
    assert bundle_id_for("m1") == bundle_id_for("m1") != bundle_id_for("m2")
    assert bundle_id_for(None, ["b", "a"]) == bundle_id_for(None, ["a", "b"])
    assert split_evenly(10, [1, 1, 1]) == [4, 3, 3] and sum(split_evenly(7, [5, 0, 2])) == 7


# ── global numbering, boundary split, cut back (pure) ────────────────────────


def _three_files():
    contents = ["أ١\nأ٢\nأ٣", "ب١\nب٢", "ج١\nج٢\nج٣\nج٤"]
    local = [paragraphize(c) for c in contents]
    paras, spans = number_globally(["a", "b", "c"], local, contents)
    return contents, local, paras, spans


def test_global_numbering():
    _, _, paras, spans = _three_files()
    assert [p.n for p in paras] == list(range(1, 10))
    assert [(s.first_p, s.last_p) for s in spans] == [(1, 3), (4, 5), (6, 9)]
    assert [p.text for p in paras[3:5]] == ["ب١", "ب٢"]
    assert all(a.char_end <= b.char_start for a, b in zip(paras, paras[1:]))  # monotonic
    assert paras[2].page != paras[3].page  # a file start reads as a page start


def seg(f, t, value="core", label="x", what=""):
    return Segment(from_p=f, to_p=t, label=label, what=what, value=value)


def test_a_segment_crossing_files_is_split():
    _, _, _, spans = _three_files()
    segs, cuts = split_at_boundaries([seg(1, 2), seg(3, 7, "supporting", "body", "نص")], spans)
    assert [(s.from_p, s.to_p) for s in segs] == [(1, 2), (3, 3), (4, 5), (6, 7)]
    assert cuts == 2 and {s.what for s in segs[1:]} == {"نص"}


def test_cut_back_renumbers_locally_and_clips():
    _, _, _, spans = _three_files()
    m = SegmentMap(
        documents_in_file=[EmbeddedDoc(from_p=1, to_p=5, name="حكم"), EmbeddedDoc(from_p=6, to_p=9, name="خطاب")],
        segments=[seg(1, 3), seg(4, 4, "metadata"), seg(5, 5), seg(6, 9)],
        key_paragraphs=[KeyParagraph(p=2, why="أ"), KeyParagraph(p=5, why="ب"), KeyParagraph(p=8, why="ج")],
    )
    b = cut_back(m, spans[1])
    assert [(s.from_p, s.to_p, s.value) for s in b.segments] == [(1, 1, "metadata"), (2, 2, "core")]
    assert [(d.from_p, d.to_p, d.name) for d in b.documents_in_file] == [(1, 2, "حكم")]
    assert [(k.p, k.why) for k in b.key_paragraphs] == [(2, "ب")]
    c = cut_back(m, spans[2])
    assert [(s.from_p, s.to_p) for s in c.segments] == [(1, 4)] and c.key_paragraphs[0].p == 3


# ── DB fixtures ─────────────────────────────────────────────────────────────


def _item(item_id: str, content: str, seq: int, doc_type: str = "letter", **kw: Any) -> dict:
    return {
        "item_id": item_id, "user_id": USER, "conversation_id": CONVO, "case_id": None,
        "kind": "attachment", "title": f"ملف {seq}", "summary": f"ملخص الملف {seq}",
        "content_md": content, "metadata": {"doc_type": doc_type}, "deleted_at": None,
        "wi_seq": seq, "created_at": f"2026-11-12T10:00:{seq:02d}", **kw,
    }


def make_db(files: list[tuple[str, str, str]], *, msg: str = MSG, links: list[dict] | None = None) -> FakeSupabase:
    items = [_item(i, c, k, dt) for k, (i, c, dt) in enumerate(files, start=1)]
    if links is None:
        links = [{"message_id": msg, "document_id": i, "created_at": "2026-11-12T10:00:00"} for i, _, _ in files]
    messages = [{"message_id": msg, "conversation_id": CONVO, "role": "user",
                 "content": "راجع الخطابات المرفقة واذكر المبالغ", "created_at": "2026-11-12T10:00:00"}]
    return FakeSupabase({"workspace_items": items, "message_attachments": links, "messages": messages})


def owners_example_db() -> FakeSupabase:
    return make_db([
        ("big1", text(40_000), "contract"), ("big2", text(20_000), "letter"),
        ("s1", text(4_000), "letter"), ("s2", text(2_000), "letter"), ("s3", text(4_000), "judgment"),
    ])


def test_bundle_for_item_and_applies_to():
    db = owners_example_db()
    assert bundle_for_item(db, "s2") == ["s1", "s2", "s3"]
    assert bundle_for_item(db, "s1") == ["s1", "s2", "s3"]
    assert bundle_for_item(db, "big1") is None
    assert segmenter_applies_to_item(db, "big1") and segmenter_applies_to_item(db, "s3")
    assert not segmenter_applies_to_item(db, "missing")


def test_an_item_without_a_link_or_a_non_qualifying_message():
    db = make_db([("s1", text(4_000), "letter"), ("s2", text(500), "letter")])
    assert bundle_for_item(db, "s1") is None          # 4,500 in all: not over 5,000
    assert not segmenter_applies_to_item(db, "s1")
    db = make_db([("s1", text(4_000), "letter"), ("s2", text(3_000), "letter")], links=[])
    assert bundle_for_item(db, "s1") is None


def test_a_file_resent_later_belongs_to_its_first_message():
    files = [("s1", text(3_000), "letter"), ("s2", text(3_000), "letter"), ("s3", text(3_000), "letter")]
    links = [
        {"message_id": "m1", "document_id": "s1", "created_at": "2026-11-12T10:00:00"},
        {"message_id": "m1", "document_id": "s2", "created_at": "2026-11-12T10:00:00"},
        {"message_id": "m0", "document_id": "s3", "created_at": "2026-11-12T09:00:00"},
        {"message_id": "m1", "document_id": "s3", "created_at": "2026-11-12T10:00:00"},
    ]
    db = make_db(files, links=links)
    assert bundle_for_item(db, "s1") == ["s1", "s2"]
    assert bundle_for_item(db, "s3") is None


def test_deleted_members_do_not_count():
    db = make_db([("s1", text(3_000), "letter"), ("s2", text(3_000), "letter")])
    db.tables["workspace_items"][1]["deleted_at"] = "2026-11-12T11:00:00"
    assert bundle_for_item(db, "s1") is None


# ── runs (scripted model) ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(runner_mod, "segmenter_enabled", lambda uid: True)
    monkeypatch.setattr(runner_mod, "_codec_for", lambda supabase, uid: None)
    runner_mod._BUNDLE_TASKS.clear()


def use(monkeypatch, handler) -> Script:
    script = Script(handler)
    monkeypatch.setattr(runner_mod, "create_attachment_segmenter", script.factory)
    return script


_BOUNDARY = re.compile(r"^— الملف (\d+) من (\d+)")


def file_starts(prompt: str) -> list[int]:
    """Global numbers of the paragraphs right after each (non-continued) boundary line."""
    lines = prompt.split("\n")
    out = []
    for k, ln in enumerate(lines):
        if _BOUNDARY.match(ln) and "(تابع)" not in ln:
            m = re.match(r"\[P(\d+)\]", lines[k + 1])
            out.append(int(m.group(1)))
    return out


def per_file(prompt: str, instr: str, n: int, *, cross: bool = False) -> dict:
    """header (metadata) + body (core) per file in the part; ``cross`` runs file 1's
    body into file 2."""
    a, b = prompt_range(prompt)
    starts = [s for s in file_starts(prompt) if a <= s <= b]
    bounds = sorted(set([a] + starts))
    segs = []
    for k, s in enumerate(bounds):
        e = (bounds[k + 1] - 1) if k + 1 < len(bounds) else b
        segs.append({"from_p": s, "to_p": s, "label": "header", "what": "رأس الملف", "value": "metadata"})
        if e > s:
            segs.append({"from_p": s + 1, "to_p": e, "label": "body", "what": f"متن {s}", "value": "core"})
    if cross and len(bounds) > 1:
        # file 1's body runs on over file 2's header line: ONE segment crosses
        s2 = bounds[1]
        next(x for x in segs if x["from_p"] == bounds[0] + 1)["to_p"] = s2
        segs = [x for x in segs if not (x["from_p"] == s2 and x["to_p"] == s2)]
    docs = [{"from_p": a, "to_p": b, "name": "مستند"}]
    return full(segs, docs=docs, keys=[{"p": a, "why": "مفتاح"}])


def covers(db: FakeSupabase, item_id: str, content: str) -> list[dict]:
    rows = sorted((r for r in db.tables["attachment_segments"] if r["item_id"] == item_id), key=lambda r: r["seq"])
    assert rows, item_id
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
    assert rows[0]["from_p"] == 1
    for prev, nxt in zip(rows, rows[1:]):
        assert nxt["from_p"] == prev["to_p"] + 1
    covered = [False] * len(content)
    for r in rows:
        assert r["content_md"] == content[r["char_start"]:r["char_end"]]
        for i in range(r["char_start"], r["char_end"]):
            covered[i] = True
    assert all(c or ch.isspace() for c, ch in zip(covered, content))
    assert rows[-1]["to_p"] == len(paragraphize(content, doc_type=_doc_type(db, item_id)))
    return rows


def _doc_type(db: FakeSupabase, item_id: str) -> str:
    return next(r for r in db.tables["workspace_items"] if r["item_id"] == item_id)["metadata"]["doc_type"]


def content_of(db: FakeSupabase, item_id: str) -> str:
    return next(r for r in db.tables["workspace_items"] if r["item_id"] == item_id)["content_md"]


@pytest.mark.asyncio
async def test_bundle_run_stores_one_map_per_member(monkeypatch):
    db = owners_example_db()
    script = use(monkeypatch, lambda p, i, n: per_file(p, i, n, cross=True))
    res = await run_attachment_bundle(db, ["s3", "s1", "s2"])  # any order in → upload order
    assert set(res) == {"s1", "s2", "s3"} and all(r.outcome == "ok" for r in res.values()), res
    assert len(script.calls) == 1
    bid = res["s1"].bundle_id
    assert bid == B.bundle_id_for(MSG) and all(r.bundle_members == ["s1", "s2", "s3"] for r in res.values())
    maps = {m["item_id"]: m for m in db.tables["attachment_segment_maps"]}
    assert set(maps) == {"s1", "s2", "s3"}
    assert all(m["bundle_id"] == bid and m["bundle_size"] == 3 for m in maps.values())
    for i in ("s1", "s2", "s3"):
        covers(db, i, content_of(db, i))
        assert maps[i]["content_chars"] == len(content_of(db, i))
        assert maps[i]["documents_in_file"] and maps[i]["documents_in_file"][0]["from_p"] == 1
    # the crossing segment was split at the boundary
    assert res["s1"].repairs.get("boundary_splits") == 1
    s2 = sorted((r for r in db.tables["attachment_segments"] if r["item_id"] == "s2"), key=lambda r: r["seq"])
    assert s2[0]["what"].startswith("متن")  # file 1's body continued into file 2


@pytest.mark.asyncio
async def test_the_prompt_carries_the_bundle(monkeypatch):
    db = owners_example_db()
    script = use(monkeypatch, per_file)
    await run_attachment_bundle(db, ["s1", "s2", "s3"])
    call = script.calls[0]
    prompt, instr = call["prompt"], call["instructions"]
    assert BUNDLE_NOTE.replace("{n}", "3") in instr
    assert LABELS["letter"] in instr and TYPE_NOTES["letter"] in instr      # 2 letters, 1 judgment
    assert "3 ملفات أُرسلت معاً" in prompt.split("\n")[0]
    for k in (1, 2, 3):  # s1..s3 are the 3rd..5th uploads of the message: titles «ملف 3..5»
        assert f"— الملف {k} من 3 · doc_type: " in prompt
        assert f"### الملف {k} من 3 · ملف {k + 2}" in prompt and f"ملخص الملف {k + 2}" in prompt
    assert "— الملف 3 من 3 · doc_type: judgment · ملف 5 —" in prompt
    assert "راجع الخطابات المرفقة" in prompt  # the user's words, once
    # boundary lines are not numbered; the count in the first line = numbered lines
    numbered = [ln for ln in prompt.split("\n") if ln.startswith("[P")]
    n_total = prompt_range(prompt)[1]
    assert len(numbered) == n_total
    assert file_starts(prompt)[0] == 1


@pytest.mark.asyncio
async def test_mixed_types_get_each_type_its_own_labels(monkeypatch):
    """Each file is labelled by its own kind (owner review of the bundle preview,
    2026-11-12: a registry extract bundled with contracts got contract labels)."""
    db = make_db([("s1", text(3_000), "judgment"), ("s2", text(3_000), "letter")])
    script = use(monkeypatch, per_file)
    await run_attachment_bundle(db, ["s1", "s2"])
    instr = script.calls[0]["instructions"]
    assert LABELS["judgment"] in instr and LABELS["letter"] in instr
    assert "### `judgment` files" in instr and "### `letter` files" in instr


@pytest.mark.asyncio
async def test_each_file_keeps_its_own_paragraph_rule(monkeypatch):
    long_line = f"{line(50)}. {line(50)}"  # ~620 chars, a sentence end mid-line
    db = make_db([("j", "\n".join([long_line] * 4), "judgment"), ("l", "\n".join([long_line] * 4), "letter")])
    use(monkeypatch, per_file)
    res = await run_attachment_bundle(db, ["j", "l"])
    assert res["j"].paragraphs == 8 and res["l"].paragraphs == 4
    covers(db, "j", content_of(db, "j"))
    covers(db, "l", content_of(db, "l"))


@pytest.mark.asyncio
async def test_a_later_file_repeating_an_earlier_one_is_repeat(monkeypatch):
    block = "\n".join(line() for _ in range(5))   # ~950 chars
    db = make_db([("s1", "رأس الخطاب الأول\n" + block + "\n" + text(1_500), "letter"),
                  ("s2", "رأس الخطاب الثاني\n" + block, "letter")])
    use(monkeypatch, per_file)
    res = await run_attachment_bundle(db, ["s1", "s2"])
    assert res["s2"].repeats == 1 and res["s1"].repeats == 0
    values = [r["value"] for r in sorted((r for r in db.tables["attachment_segments"] if r["item_id"] == "s2"),
                                         key=lambda r: r["seq"])]
    assert values == ["metadata", "repeat"]


@pytest.mark.asyncio
async def test_cost_is_shared_by_the_members(monkeypatch):
    db = owners_example_db()
    use(monkeypatch, per_file)
    res = await run_attachment_bundle(db, ["s1", "s2", "s3"])
    ledger = db.tables["llm_calls"]
    assert len(ledger) == 1 and ledger[0]["user_id"] == USER
    assert sum(r.tokens_in for r in res.values()) == ledger[0]["tokens_in"] > 0
    assert res["s1"].tokens_in > res["s2"].tokens_in  # 4k vs 2k of text


@pytest.mark.asyncio
async def test_fresh_bundle_is_kept_a_changed_member_reruns_it(monkeypatch):
    db = owners_example_db()
    script = use(monkeypatch, per_file)
    await run_attachment_bundle(db, ["s1", "s2", "s3"])
    res = await run_attachment_bundle(db, ["s1", "s2", "s3"])
    assert {r.outcome for r in res.values()} == {"skipped_fresh"} and len(script.calls) == 1
    member = next(r for r in db.tables["workspace_items"] if r["item_id"] == "s2")
    member["content_md"] += "\nسطر أضيف بعد إعادة القراءة"
    res = await run_attachment_bundle(db, ["s1", "s2", "s3"])
    assert {r.outcome for r in res.values()} == {"ok"} and len(script.calls) == 2
    covers(db, "s2", member["content_md"])
    assert len(db.tables["attachment_segment_maps"]) == 3


@pytest.mark.asyncio
async def test_a_member_without_a_map_reruns_the_bundle(monkeypatch):
    db = make_db([("s1", text(3_000), "letter"), ("s2", text(3_000), "letter")])
    script = use(monkeypatch, per_file)
    assert (await run_attachment_segmenter(db, "s1")).outcome == "ok"
    # a third small file of the same message arrives (its OCR finished later)
    db.tables["workspace_items"].append(_item("s3", text(2_000), 3))
    db.tables["message_attachments"].append({"message_id": MSG, "document_id": "s3", "created_at": "2026-11-12T10:00:00"})
    res = await run_attachment_segmenter(db, "s1")
    assert res.outcome == "ok" and res.bundle_members == ["s1", "s2", "s3"] and len(script.calls) == 2
    assert {m["item_id"]: m["bundle_size"] for m in db.tables["attachment_segment_maps"]} == {"s1": 3, "s2": 3, "s3": 3}


@pytest.mark.asyncio
async def test_preview_returns_per_member_rows_and_writes_nothing(monkeypatch):
    db = owners_example_db()
    use(monkeypatch, per_file)
    res = await run_attachment_bundle(db, ["s1", "s2", "s3"], preview=True)
    assert all(r.outcome == "ok" and r.preview for r in res.values())
    p2 = res["s2"].preview
    assert set(p2) >= {"paragraphs", "map", "segments", "map_row", "split_info", "user_words", "bundle"}
    assert p2["bundle"]["members"] == ["s1", "s2", "s3"] and p2["bundle"]["index"] == 2
    assert p2["map_row"]["bundle_size"] == 3 and p2["map_row"]["item_id"] == "s2"
    assert p2["segments"][0]["from_p"] == 1 and all(s["item_id"] == "s2" for s in p2["segments"])
    c2 = content_of(db, "s2")
    assert all(s["content_md"] == c2[s["char_start"]:s["char_end"]] for s in p2["segments"])
    assert db.tables["attachment_segment_maps"] == [] and db.tables["llm_calls"] == []
    assert db.tables["pii_mappings"] == []


@pytest.mark.asyncio
async def test_a_bundle_over_30k_is_split_in_two(monkeypatch):
    files = [(f"s{k}", text(4_900), "letter") for k in range(1, 8)]   # 7 × 4,900 = 34,300
    db = make_db(files)
    script = use(monkeypatch, per_file)
    res = await run_attachment_bundle(db, [f for f, _, _ in files])
    assert all(r.outcome == "ok" and r.split for r in res.values()), res
    assert len(script.calls) == 2
    one, two = sorted(script.calls, key=lambda c: "part 2 of 2" in c["prompt"])
    for c in (one, two):
        assert BUNDLE_NOTE.replace("{n}", "7") in c["instructions"]
        assert "## You are mapping ONE PART" in c["instructions"]
    a2, _ = prompt_range(two["prompt"])
    assert a2 in file_starts(one["prompt"] + "\n" + two["prompt"])  # the cut is at a file start
    for f, _, _ in files:
        covers(db, f, content_of(db, f))


# ── run_attachment_segmenter is bundle-aware ─────────────────────────────────


@pytest.mark.asyncio
async def test_member_call_returns_its_own_result(monkeypatch):
    db = owners_example_db()
    script = use(monkeypatch, per_file)
    res = await run_attachment_segmenter(db, "s2")
    assert res.outcome == "ok" and res.item_id == "s2" and res.bundle_members == ["s1", "s2", "s3"]
    assert res.chars == len(content_of(db, "s2")) and len(script.calls) == 1
    assert {m["item_id"] for m in db.tables["attachment_segment_maps"]} == {"s1", "s2", "s3"}
    # big files keep their own path
    big = await run_attachment_segmenter(db, "big2")
    assert big.outcome == "ok" and big.bundle_id is None


@pytest.mark.asyncio
async def test_concurrent_member_calls_make_one_run(monkeypatch):
    db = owners_example_db()

    async def slow(prompt, instr, n):
        await asyncio.sleep(0.05)
        return per_file(prompt, instr, n)

    script = use(monkeypatch, slow)
    out = await asyncio.gather(*(run_attachment_segmenter(db, i) for i in ("s1", "s2", "s3")))
    assert len(script.calls) == 1
    assert [r.item_id for r in out] == ["s1", "s2", "s3"] and all(r.outcome == "ok" for r in out)
    assert len({r.bundle_id for r in out}) == 1
    assert runner_mod._BUNDLE_TASKS == {}


@pytest.mark.asyncio
async def test_preview_calls_never_share_a_run(monkeypatch):
    db = owners_example_db()

    async def slow(prompt, instr, n):
        await asyncio.sleep(0.05)
        return per_file(prompt, instr, n)

    script = use(monkeypatch, slow)
    out = await asyncio.gather(
        run_attachment_segmenter(db, "s1", preview=True), run_attachment_segmenter(db, "s1", preview=True),
    )
    assert len(script.calls) == 2 and all(r.outcome == "ok" and r.preview["bundle"] for r in out)
    assert db.tables["attachment_segment_maps"] == []


@pytest.mark.asyncio
async def test_a_small_file_outside_any_bundle_is_still_skipped(monkeypatch):
    db = make_db([("s1", text(3_000), "letter")])
    script = use(monkeypatch, per_file)
    res = await run_attachment_segmenter(db, "s1")
    assert res.outcome == "skipped_short" and not script.calls


def test_chunk_note_and_bundle_note_coexist():
    from agents.memory.attachment_segmenter.prompts import build_instructions

    text_ = build_instructions("letter", part=2, a=10, b=20, first_end=3, bundle_files=4)
    assert text_.index(BUNDLE_NOTE.replace("{n}", "4")) < text_.index("## You are mapping ONE PART")
    assert CHUNK_NOTE.split("{part}")[0] in text_
    assert build_instructions("letter") == build_instructions("letter", bundle_files=None)


def test_mixed_bundle_gives_every_type_its_own_labels():
    """A registry extract bundled with two contracts keeps the official-record labels."""
    from agents.memory.attachment_segmenter import prompts as P

    text = P.build_instructions("contract", bundle_files=4, bundle_types=["contract", "contract", "letter", "official_record"])
    for k in ("contract", "letter", "official_record"):
        assert f"### `{k}` files" in text and P.LABELS[k] in text
    assert text.count(P.TYPE_NOTES["official_record"]) == 1
    assert "labels of its OWN doc_type" in text


def test_single_type_bundle_keeps_the_plain_label_list():
    from agents.memory.attachment_segmenter import prompts as P

    text = P.build_instructions("letter", bundle_files=2, bundle_types=["letter", "letter"])
    assert "### `letter` files" not in text and P.LABELS["letter"] in text


def test_single_document_prompt_is_unchanged_by_bundle_types():
    from agents.memory.attachment_segmenter import prompts as P

    assert P.build_instructions("judgment") == P.build_instructions("judgment", bundle_types=None)
