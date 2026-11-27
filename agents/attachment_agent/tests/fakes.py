"""Rows and models for the attachment agent tests.

The database is the angle mapper's in-memory supabase stand-in; these helpers
build the rows the agent reads: attachments (``workspace_items``), segment maps
(215), page rows, angle maps (228) and chat messages.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.memory.angle_mapper.tests.fake_supabase import FakeSupabase

USER = "u1"
CONVO = "c1"

# A three-page lease; pages are joined with "\n\n" as OCR joins them.
PAGE_1 = (
    "عقد إيجار\n"
    "المؤجر: محمد أحمد العتيبي\n"
    "المستأجر: شركة الأفق للتجارة\n"
    "سجل تجاري رقم 1010234567"
)
PAGE_2 = (
    "البند الأول: مدة العقد سنة تبدأ من ٢٥/٣/١٤٤٧هـ.\n"
    "البند الثاني: يَدْفَعُ المستأجرُ الأجرةَ مقدمًا كل ستة أشهر.\n"
    "حقوق الطبع محفوظة للمكتب"
)
PAGE_3 = (
    "البند الثالث: لا يجوز للمستأجر التأجير من الباطن إلا بموافقة كتابية.\n"
    "للتواصل: lessor.office@mail.com\n"
    "التوقيع: المؤجر"
)
LEASE = "\n\n".join([PAGE_1, PAGE_2, PAGE_3])
PAGE_STARTS = [0, len(PAGE_1) + 2, len(PAGE_1) + 2 + len(PAGE_2) + 2]


def span_of(text: str, needle: str) -> tuple[int, int]:
    start = text.index(needle)
    return start, start + len(needle)


def attachment(
    item_id: str, title: str, content: str, *, wi_seq: int | None = None, kind: str = "attachment",
    deleted_at: str | None = None, created_at: str = "2026-11-01T10:00:00", doc_type: str = "contract",
    summary: str = "", conversation_id: str = CONVO, user_id: str = USER,
) -> dict:
    return {
        "item_id": item_id, "conversation_id": conversation_id, "user_id": user_id, "kind": kind,
        "title": title, "summary": summary, "content_md": content, "wi_seq": wi_seq,
        "created_at": created_at, "deleted_at": deleted_at, "doc_type": doc_type,
    }


def segment_map(item_id: str, content: str, sections: Sequence[dict]) -> tuple[dict, list[dict]]:
    """``sections``: dicts with seq, char_start, char_end, label, value and any of
    what / owner / owner_class / brought_by / page_from / page_to / content_md."""
    map_row = {
        "item_id": item_id, "user_id": USER, "paragraph_rule_version": "p1", "prompt_version": "s1",
        "model": "m", "split": False, "documents_in_file": [], "key_paragraphs": [],
        "content_chars": len(content), "created_at": "2026-11-01T10:05:00",
    }
    rows = []
    for s in sections:
        row = {
            "item_id": item_id, "from_p": s["seq"], "to_p": s["seq"], "what": "", "owner": "",
            "owner_class": "unknown", "brought_by": "", "page_from": None, "page_to": None, **s,
        }
        row.setdefault("content_md", content[s["char_start"]:s["char_end"]])
        rows.append(row)
    return map_row, rows


def lease_sections(content: str = LEASE) -> list[dict]:
    """S1 parties (p1) · S2 term (p2) · S3 rent (p2) · S4 footer (boilerplate) · S5 sublet (p3) · S6 contact."""
    def sec(seq: int, needle: str, label: str, value: str, what: str, page: int, owner: str = "") -> dict:
        start, end = span_of(content, needle)
        return {"seq": seq, "char_start": start, "char_end": end, "label": label, "value": value,
                "what": what, "owner": owner, "page_from": page, "page_to": page}
    return [
        sec(1, PAGE_1, "parties", "metadata", "أطراف العقد", 1, "طرفا العقد"),
        sec(2, "البند الأول: مدة العقد سنة تبدأ من ٢٥/٣/١٤٤٧هـ.", "term", "core", "مدة العقد", 2, "المؤجر"),
        sec(3, "البند الثاني: يَدْفَعُ المستأجرُ الأجرةَ مقدمًا كل ستة أشهر.", "rent", "core", "دفع الأجرة", 2, "المؤجر"),
        sec(4, "حقوق الطبع محفوظة للمكتب", "footer", "boilerplate", "تذييل", 2),
        sec(5, "البند الثالث: لا يجوز للمستأجر التأجير من الباطن إلا بموافقة كتابية.", "sublet", "core",
            "منع التأجير من الباطن", 3, "المؤجر"),
        sec(6, "للتواصل: lessor.office@mail.com\nالتوقيع: المؤجر", "contact", "supporting", "التواصل والتوقيع", 3),
    ]


def big_text(n_lines: int = 400) -> str:
    """Past the whole-file limit: numbered lines, one key phrase near the end."""
    lines = [f"سطر رقم {i} من نص المذكرة الطويلة في الدعوى." for i in range(1, n_lines + 1)]
    lines[n_lines - 10] = "وقد أقر المدعى عليه باستلام المبلغ كاملًا."
    return "\n".join(lines)


def make_db(
    attachments: Sequence[dict],
    *,
    maps: Sequence[tuple[dict, list[dict]]] = (),
    pages: dict[str, Sequence[int]] | None = None,
    angle_maps: Sequence[dict] = (),
    messages: Sequence[dict] = (),
    fail: set | None = None,
) -> FakeSupabase:
    tables: dict[str, list[dict]] = {"workspace_items": list(attachments), "messages": list(messages)}
    tables["attachment_segment_maps"] = [m for m, _ in maps]
    tables["attachment_segments"] = [r for _, rows in maps for r in rows]
    page_rows = []
    by_id = {a["item_id"]: a for a in attachments}
    for item_id, starts in (pages or {}).items():
        text = by_id[item_id]["content_md"]
        for n, start in enumerate(starts, start=1):
            end = starts[n] if n < len(starts) else len(text)
            page_rows.append({"item_id": item_id, "page_no": n, "char_start": start, "content_md": text[start:end]})
    tables["attachment_pages"] = page_rows
    amaps, aitems, angles = [], [], []
    for am in angle_maps:
        amaps.append(am["map"])
        aitems.extend(am["items"])
        angles.extend(am["angles"])
    tables["attachment_angle_maps"] = amaps
    tables["attachment_angle_map_items"] = aitems
    tables["attachment_angles"] = angles
    return FakeSupabase(tables, fail=fail)


def angle_map(map_id: str, members: Sequence[str], angles: Sequence[dict], *, client: dict | None = None,
              gaps: Sequence[dict] = (), message_id: str = "m0") -> dict:
    return {
        "map": {"map_id": map_id, "message_id": message_id, "conversation_id": CONVO, "user_id": USER,
                "prompt_version": "a5", "client": client or {}, "context": [], "gaps": list(gaps),
                "no_angle": [], "unrelated_documents": [], "left_out": []},
        "items": [{"map_id": map_id, "item_id": item, "doc_index": i} for i, item in enumerate(members, start=1)],
        "angles": [{"map_id": map_id, "seq": i, **a} for i, a in enumerate(angles, start=1)],
    }


def edit_deps(db: FakeSupabase, message: str, *, item_ids: Sequence[str] = (), message_id: str = "m9"):
    """Deps as the runner builds them (files, maps, the masked current message)."""
    from agents.attachment_agent.context import _enc, load_context, turn_encoder
    from agents.attachment_agent.deps import AttachmentDeps
    from agents.attachment_agent.models import AttachmentAgentCall

    c = AttachmentAgentCall(user_id=USER, conversation_id=CONVO, message_id=message_id,
                            user_message=message, item_ids=list(item_ids))
    loaded = load_context(db, c)
    return AttachmentDeps(
        supabase=db, user_id=USER, conversation_id=CONVO, message_id=message_id,
        files={f.label: f for f in loaded.files}, maps=loaded.maps,
        user_message=_enc(turn_encoder(), message), source_text="\n".join(loaded.threads),
    )


def memo_sections(content: str) -> list[dict]:
    """A two-section memo: S1 facts (supporting) · S2 the request (core)."""
    cut = content.index("الطلبات")
    return [
        {"seq": 1, "char_start": 0, "char_end": cut, "label": "facts", "value": "supporting", "what": "الوقائع"},
        {"seq": 2, "char_start": cut, "char_end": len(content), "label": "requests", "value": "core",
         "what": "الطلبات", "owner": "المدعي"},
    ]


MEMO = "الوقائع: استأجر المدعي المحل من محمد أحمد العتيبي.\nالطلبات: إلزام المدعى عليه بالإخلاء."


def two_map_db(*, lease_map_members: Sequence[str] = ("i1",), **kw: Any) -> FakeSupabase:
    """F1 = the lease (i1) in map1, F2 = a memo (i2) in map2; each map has A1."""
    return make_db(
        [attachment("i1", "عقد الإيجار", LEASE, wi_seq=1, created_at="2026-11-01T10:00:00",
                    summary="عقد بين محمد أحمد العتيبي وشركة الأفق"),
         attachment("i2", "مذكرة الدعوى", MEMO, wi_seq=2, created_at="2026-11-01T11:00:00", doc_type="memo")],
        maps=[segment_map("i1", LEASE, lease_sections()), segment_map("i2", MEMO, memo_sections(MEMO))],
        pages={"i1": PAGE_STARTS},
        angle_maps=[
            angle_map("map1", list(lease_map_members), [
                {"title": "منع التأجير من الباطن", "kind": "attack", "strength": "medium",
                 "argument": "البند الثالث يمنع التأجير", "sections": [f"D{list(lease_map_members).index('i1') + 1}.S5"]},
                {"title": "الأجرة مقدمًا", "kind": "strengthen", "strength": "weak",
                 "sections": [f"D{list(lease_map_members).index('i1') + 1}.S3"]},
            ], client={"side": "claimant", "party": "محمد أحمد العتيبي", "basis": "المؤجر في العقد",
                       "confidence": "likely"},
               gaps=[{"kind": "missing", "issue": "لا يوجد إشعار الإخلاء", "sections": []},
                     {"kind": "contradiction", "issue": "تاريخ البداية غير واضح", "sections": []}],
               message_id="m-up-1"),
            angle_map("map2", ["i2"], [
                {"title": "طلب الإخلاء", "kind": "strengthen", "strength": "strong", "sections": ["D1.S2"]},
            ], message_id="m-up-2"),
        ],
        **kw,
    )


# ---------------------------------------------------------------------------
# Scripted models
# ---------------------------------------------------------------------------


def final(outcome: str = "found", points: Sequence[dict] = (), **extra: Any) -> TextPart:
    """The model's answer as plain JSON text (the salvager parses it). ``request``
    is required by the schema: "read" unless given."""
    body = {"request": "read", "outcome": outcome, "points": list(points), **extra}
    return TextPart(json.dumps(body, ensure_ascii=False))


def call(tool: str, **args: Any) -> ToolCallPart:
    return ToolCallPart(tool_name=tool, args=args)


def point(statement: str, *quotes: tuple[str, str]) -> dict:
    return {"statement": statement, "quotes": [{"file": f, "text": t} for f, t in quotes]}


def scripted(steps: Sequence[Any], seen: list | None = None) -> FunctionModel:
    """One response per model request, in order. A step is a part, a list of
    parts, or a callable ``(messages) -> part(s)``. ``seen`` collects the
    messages of every request."""
    queue = list(steps)

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if seen is not None:
            seen.append(messages)
        step = queue.pop(0)
        if callable(step):
            step = step(messages)
        parts = step if isinstance(step, list) else [step]
        return ModelResponse(parts=parts)

    return FunctionModel(fn)


def last_request_parts(messages: list[ModelMessage]) -> list[Any]:
    req = [m for m in messages if isinstance(m, ModelRequest)][-1]
    return list(req.parts)


def tool_returns(messages: list[ModelMessage]) -> list[str]:
    return [str(p.content) for m in messages if isinstance(m, ModelRequest)
            for p in m.parts if isinstance(p, ToolReturnPart)]


def retry_prompts(messages: list[ModelMessage]) -> list[str]:
    return [str(p.content) for m in messages if isinstance(m, ModelRequest)
            for p in m.parts if isinstance(p, RetryPromptPart)]


def user_prompt(messages: list[ModelMessage]) -> str:
    first = [m for m in messages if isinstance(m, ModelRequest)][0]
    return "\n".join(str(getattr(p, "content", "")) for p in first.parts)


__all__ = [name for name in dir() if not name.startswith("_")] + ["Callable"]
