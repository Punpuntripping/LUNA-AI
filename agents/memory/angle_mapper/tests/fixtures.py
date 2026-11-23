"""Message groups as ``workspace_items``, the 215 tables, ``message_attachments``
and ``messages`` hold them, plus complete model answers. Synthetic text only.
Shared by the runner / store / prompt / validate / label / preview tests.

Three documents of one user, in one conversation (upload order A, B, C):

JUDGMENT (judgment, ITEM_A) — a first-instance judgment; the client is the respondent.
  S1 P001–P002 header         metadata     court
  S2 P003–P005 claim          core         claimant   «المدعي»
  S3 P006–P008 defense        core         respondent «المدعى عليه»
  S4 P009      expert_report  supporting   expert (قدّمه: المدعى عليه)
  S5 P010–P011 grounds        core         court            (P011: a national id)
  S6 P012      signatures     boilerplate  court

OBJECTION (claim, ITEM_B) — the client's objection to that judgment: in ITS
proceeding the client is the claimant «المعترض» (``client.by_document``).
  S1 P001      header         metadata     claimant   «المعترض»
  S2 P002–P004 grounds        core         claimant   «المعترض»
  S3 P005–P006 requests       core         claimant   «المعترض»  (P006: an iqama number)
  S4 P007      annex          supporting   third_party «البنك» (قدّمه: المعترض)

LEASE (contract, ITEM_C) — a lease of another matter (``unrelated_documents``).
  S1 P001–P002 parties        metadata     both_parties
  S2 P003–P004 rent           core         contract_party «المستأجر»
  S3 P005      term           core         contract_party «المؤجر»

``tables(*docs)`` links the documents to ONE user message (``MSG``) by default.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Iterable

from agents.memory.attachment_segmenter.paragraphs import (
    PARAGRAPH_RULE_VERSION,
    max_paragraph_chars,
    paragraphize,
)

USER = "user-7"
OTHER_USER = "user-8"
CONVO = "convo-9"
MSG = "msg-1"
MSG_AT = "2026-11-20T09:00:00+00:00"
WORDS = "أنا المدعى عليه في هذا الحكم وأبغى أعترض عليه"
NATIONAL_ID = "1098765432"
IQAMA = "2087654321"
SEG_CREATED_AT = "2026-11-20T10:00:00+00:00"
SEG_VERSION = "seg-v5.2"

ITEM_A = "aaaaaaaa-1111-4000-8000-000000000001"
ITEM_B = "bbbbbbbb-2222-4000-8000-000000000002"
ITEM_C = "cccccccc-3333-4000-8000-000000000003"

_FILLER = "تفصيل"


@dataclass(frozen=True)
class Doc:
    item_id: str
    title: str
    doc_type: str
    summary: str
    lines: tuple[str, ...]
    #: (seq, from_p, to_p, label, value, owner_class, owner, brought_by, what)
    sections: tuple[tuple, ...]
    key_paragraphs: tuple[tuple[int, str], ...]
    wi_seq: int
    created_at: str

    @property
    def core(self) -> list[int]:
        return [s[0] for s in self.sections if s[4] == "core"]

    @property
    def seqs(self) -> list[int]:
        return [s[0] for s in self.sections]


JUDGMENT = Doc(
    item_id=ITEM_A,
    title="صك حكم ابتدائي",
    doc_type="judgment",
    summary="حكم برد دعوى مطالبة بقرض",
    lines=(
        "المملكة العربية السعودية وزارة العدل المحكمة العامة بالرياض",
        "صك حكم في الدعوى المقامة من المدعي ضد المدعى عليه",
        "ادعى المدعي بأن المدعى عليه اقترض منه مبلغاً ولم يرده رغم المطالبة المتكررة",
        "وطلب المدعي إلزام المدعى عليه بسداد المتبقي من القرض مع المصاريف",
        "وقدم المدعي سندات تثبت التسليم وشهادة شاهدين على الاتفاق",
        "وبعرض الدعوى على المدعى عليه أنكر الاقتراض وذكر أن المبلغ ثمن بضاعة",
        "وأضاف المدعى عليه أنه سدد الثمن كاملاً بتحويلات بنكية",
        "وطلب المدعى عليه رد الدعوى لعدم صحتها",
        "وورد تقرير الخبير المحاسبي بمطابقة التحويلات على كشف الحساب",
        "وحيث إن المدعي لم يقدم ما يثبت أن ما سلمه قرض فقد حكمت المحكمة برد الدعوى",
        f"بيانات الطرف الأول هوية {NATIONAL_ID} وهو مواطن سعودي الجنسية",
        "القاضي والتوقيع والختم الرسمي للمحكمة",
    ),
    sections=(
        (1, 1, 2, "header", "metadata", "court", "المحكمة العامة بالرياض", None, "بيانات الصك"),
        (2, 3, 5, "claim", "core", "claimant", "المدعي", None, "دعوى القرض وطلباتها"),
        (3, 6, 8, "defense", "core", "respondent", "المدعى عليه", None, "إنكار الاقتراض والدفع بالسداد"),
        (4, 9, 9, "expert_report", "supporting", "expert", "الخبير المحاسبي", "المدعى عليه", "مطابقة التحويلات"),
        (5, 10, 11, "grounds", "core", "court", "المحكمة", None, "الأسباب والمنطوق"),
        (6, 12, 12, "signatures", "boilerplate", "court", "المحكمة", None, "التوقيع"),
    ),
    key_paragraphs=((10, "المنطوق"),),
    wi_seq=3,
    created_at="2026-11-20T08:59:00+00:00",
)

OBJECTION = Doc(
    item_id=ITEM_B,
    title="لائحة اعتراض على الحكم",
    doc_type="claim",
    summary="اعتراض المدعى عليه على حكم رد الدعوى",
    lines=(
        "لائحة اعتراض على الحكم الابتدائي في الدعوى المقامة من المدعي",
        "يعترض المعترض على الحكم لأن المحكمة لم تناقش التحويلات البنكية التي قدمها",
        "ويرى المعترض أن تقرير الخبير المحاسبي أثبت السداد ولم يلتفت إليه الحكم",
        "كما أن المدعي لم يقدم عقد القرض المزعوم",
        "ويطلب المعترض تأييد رد الدعوى وتعديل أسباب الحكم",
        f"بيانات المعترض إقامة {IQAMA} مقيم في الرياض",
        "مرفق كشف حساب صادر من البنك يثبت التحويلات",
    ),
    sections=(
        (1, 1, 1, "header", "metadata", "claimant", "المعترض", None, "بيانات اللائحة"),
        (2, 2, 4, "grounds", "core", "claimant", "المعترض", None, "أسباب الاعتراض"),
        (3, 5, 6, "requests", "core", "claimant", "المعترض", None, "طلبات المعترض"),
        (4, 7, 7, "annex", "supporting", "third_party", "البنك", "المعترض", "كشف الحساب"),
    ),
    key_paragraphs=((2, "السبب الأول"),),
    wi_seq=4,
    created_at="2026-11-20T08:59:30+00:00",
)

LEASE = Doc(
    item_id=ITEM_C,
    title="عقد إيجار محل",
    doc_type="contract",
    summary="عقد إيجار محل تجاري لمدة سنة",
    lines=(
        "عقد إيجار محل تجاري بين المؤجر والمستأجر",
        "الطرف الأول المؤجر والطرف الثاني المستأجر",
        "يلتزم المستأجر بدفع الأجرة السنوية مقدماً",
        "وفي حال التأخر يحق للمؤجر فسخ العقد",
        "مدة العقد سنة واحدة تتجدد تلقائياً",
    ),
    sections=(
        (1, 1, 2, "parties", "metadata", "both_parties", "المؤجر والمستأجر", None, "أطراف العقد"),
        (2, 3, 4, "rent", "core", "contract_party", "المستأجر", None, "الأجرة والفسخ"),
        (3, 5, 5, "term", "core", "contract_party", "المؤجر", None, "مدة العقد"),
    ),
    key_paragraphs=((3, "الأجرة"),),
    wi_seq=5,
    created_at="2026-11-20T09:00:00+00:00",
)

DOCS = (JUDGMENT, OBJECTION, LEASE)


# ── rows ──────────────────────────────────────────────────────────────────────


def content(doc: Doc, *, big: bool = False) -> str:
    """The document's text. ``big``: every line padded so the whole is over 5,000
    characters (a file the segmenter maps on its own), each line still under its
    type's paragraph max (so the paragraphs stay one per line)."""
    if not big:
        return "\n".join(doc.lines)
    base = sum(len(x) for x in doc.lines) + len(doc.lines) - 1
    words = math.ceil((5_100 - base) / (len(doc.lines) * (len(_FILLER) + 1)))
    lines = [f"{x} {' '.join([_FILLER] * words)}" for x in doc.lines]
    assert all(len(x) < max_paragraph_chars(doc.doc_type) for x in lines)
    out = "\n".join(lines)
    assert len(out) > 5_000
    return out


def paragraphs(doc: Doc, text: str | None = None):
    return paragraphize(text if text is not None else content(doc), doc_type=doc.doc_type)


def item_row(doc: Doc, text: str | None = None, **kw: Any) -> dict:
    return {
        "item_id": doc.item_id, "user_id": USER, "conversation_id": CONVO, "case_id": None,
        "kind": "attachment", "title": doc.title, "summary": doc.summary,
        "content_md": text if text is not None else content(doc), "metadata": {"doc_type": doc.doc_type},
        "deleted_at": None, "wi_seq": doc.wi_seq, "created_at": doc.created_at, **kw,
    }


def segment_rows(doc: Doc, text: str | None = None) -> list[dict]:
    text = text if text is not None else content(doc)
    paras = paragraphs(doc, text)
    rows = []
    for seq, a, b, label, value, owner_class, owner, brought_by, what in doc.sections:
        start, end = paras[a - 1].char_start, paras[b - 1].char_end
        rows.append({
            "item_id": doc.item_id, "seq": seq, "from_p": a, "to_p": b, "page_from": None, "page_to": None,
            "char_start": start, "char_end": end, "label": label, "value": value, "what": what,
            "content_md": text[start:end], "owner_class": owner_class, "owner": owner,
            "brought_by": brought_by,
        })
    return rows


def seg_map_row(doc: Doc, text: str | None = None, **kw: Any) -> dict:
    text = text if text is not None else content(doc)
    return {
        "item_id": doc.item_id, "user_id": USER, "paragraph_rule_version": PARAGRAPH_RULE_VERSION,
        "prompt_version": SEG_VERSION, "model": "wafer-deepseek-v4.1-flash-1017", "split": False,
        "documents_in_file": [{"from_p": 1, "to_p": len(doc.lines), "name": doc.title}],
        "key_paragraphs": [{"p": p, "why": why} for p, why in doc.key_paragraphs],
        "content_chars": len(text), "bundle_id": None, "created_at": SEG_CREATED_AT, **kw,
    }


def link(doc: Doc, message_id: str = MSG, created_at: str = MSG_AT) -> dict:
    return {"message_id": message_id, "document_id": doc.item_id, "created_at": created_at}


def message_row(message_id: str = MSG, text: str = WORDS, created_at: str = MSG_AT, role: str = "user") -> dict:
    return {"message_id": message_id, "conversation_id": CONVO, "role": role, "content": text,
            "created_at": created_at}


def map_tables(doc: Doc, text: str | None = None) -> dict[str, list[dict]]:
    """The 215 rows of one document's segment map."""
    return {"attachment_segment_maps": [seg_map_row(doc, text)], "attachment_segments": segment_rows(doc, text)}


def _ids(docs: Iterable[Doc]) -> set[str]:
    return {d.item_id for d in docs}


def tables(
    *docs: Doc,
    message: str | None = MSG,
    words: str = WORDS,
    unmapped: Iterable[Doc] = (),
    big: Iterable[Doc] = (),
    **extra: list[dict],
) -> dict[str, list[dict]]:
    """``docs`` (default: the judgment alone) as stored rows: each one's item, its
    segment map (unless in ``unmapped``), its link to ``message`` (``None`` = no
    message link) and that user message (``words``)."""
    docs = docs or (JUDGMENT,)
    unmapped_ids, big_ids = _ids(unmapped), _ids(big)
    t: dict[str, list[dict]] = {
        "workspace_items": [], "attachment_segment_maps": [], "attachment_segments": [],
        "message_attachments": [], "messages": [],
    }
    for d in docs:
        text = content(d, big=d.item_id in big_ids)
        t["workspace_items"].append(item_row(d, text))
        if d.item_id not in unmapped_ids:
            t["attachment_segment_maps"].append(seg_map_row(d, text))
            t["attachment_segments"] += segment_rows(d, text)
        if message:
            t["message_attachments"].append(link(d, message))
    if message:
        t["messages"].append(message_row(message, words))
    for k, v in extra.items():
        t[k] = t.get(k, []) + list(v)
    return t


# ── files («الملفات»): segment maps hang on the FILE (migrations 223 + 227) ─────

FILE_J = "f1f1f1f1-0000-4000-8000-00000000000a"
FILE_O = "f2f2f2f2-0000-4000-8000-00000000000b"
FILE_L = "f3f3f3f3-0000-4000-8000-00000000000c"
#: Conversation items that point at a file (none of them is its file's first item).
ITEM_G = "99999999-7777-4000-8000-000000000007"
ITEM_H = "88888888-6666-4000-8000-000000000008"
ITEM_K = "77777777-5555-4000-8000-000000000009"
CONVO_2 = "convo-10"
MSG_2 = "msg-2"
MSG_2_AT = "2026-11-21T09:00:00+00:00"


def file_map_tables(doc: Doc, file_id: str, text: str | None = None) -> dict[str, list[dict]]:
    """One document's segment map and segments stored under ``file_id`` (223)."""
    text = text if text is not None else content(doc)
    return {
        "attachment_segment_maps": [seg_map_row(doc, text, item_id=file_id)],
        "attachment_segments": [{**r, "item_id": file_id} for r in segment_rows(doc, text)],
    }


def file_row(doc: Doc, file_id: str, text: str) -> dict:
    """Its ``user_files`` row (221: ``content_chars`` = ``char_length(content_md)``)."""
    return {"file_id": file_id, "user_id": USER, "title": doc.title, "summary": doc.summary, "content_md": text,
            "content_chars": len(text), "metadata": {"doc_type": doc.doc_type}, "deleted_at": None}


def file_tables(
    *pairs: tuple[Doc, str, str],
    message: str | None = MSG_2,
    words: str = WORDS,
    unmapped: Iterable[Doc] = (),
    big: Iterable[Doc] = (),
    **extra: list[dict],
) -> dict[str, list[dict]]:
    """``(doc, item_id, file_id)`` per attachment: items in ``CONVO_2`` that point at
    files (what ``user_file_attach`` creates — a new item id, its file's id), linked
    to ``message``. Pages, maps and segments carry the FILE id only: no segmenter row
    is keyed by any of these item ids."""
    unmapped_ids, big_ids = _ids(unmapped), _ids(big)
    t: dict[str, list[dict]] = {
        "workspace_items": [], "user_files": [], "attachment_segment_maps": [], "attachment_segments": [],
        "message_attachments": [], "messages": [],
    }
    for n, (doc, item_id, file_id) in enumerate(pairs, start=1):
        text = content(doc, big=doc.item_id in big_ids)
        t["workspace_items"].append(item_row(doc, text, item_id=item_id, conversation_id=CONVO_2, file_id=file_id,
                                             wi_seq=n, created_at=f"2026-11-21T08:5{n}:00+00:00"))
        t["user_files"].append(file_row(doc, file_id, text))
        if doc.item_id not in unmapped_ids:
            for k, v in file_map_tables(doc, file_id, text).items():
                t[k] += v
        if message:
            t["message_attachments"].append({"message_id": message, "document_id": item_id, "created_at": MSG_2_AT})
    if message:
        t["messages"].append({**message_row(message, words, MSG_2_AT), "conversation_id": CONVO_2})
    for k, v in extra.items():
        t[k] = t.get(k, []) + list(v)
    return t


def user_words(words: str = WORDS) -> str:
    """What ``_load_user_words`` makes of the message (no earlier messages)."""
    return f"### With the file\n{words}" if words else ""


def ref(doc_index: int, seq: int) -> str:
    return f"D{doc_index}.S{seq}"


# ── model answers ─────────────────────────────────────────────────────────────

CLIENT = {"side": "respondent", "party": "المدعى عليه", "basis": "«أنا المدعى عليه» — D1·S3", "confidence": "clear",
          "by_document": []}
CLIENT_TWO = {**CLIENT, "by_document": [{"document": "D2", "side": "claimant", "party": "المعترض"}]}


def angle(kind: str = "strengthen", sections: list[Any] | None = None, **kw: Any) -> dict:
    return {
        "for_side": "respondent", "for_party": "المدعى عليه", "kind": kind,
        "title": f"وجه {kind}", "argument": "التحويلات البنكية تثبت السداد.",
        "sections": list(sections if sections is not None else ["D1.S3", "D1.S4"]),
        "legal_question": "ما حجية التحويل البنكي في إثبات السداد؟", "strength": "strong",
        "weakness": "", **kw,
    }


def good_one(**kw: Any) -> dict:
    """A group of one (the judgment as D1): every core section placed (D1·S2, D1·S3
    in angles; D1·S5 in context)."""
    out = {
        "client": dict(CLIENT),
        "angles": [angle("strengthen", ["D1.S3", "D1.S4"]),
                   angle("answer", ["D1.S2"], strength="medium", weakness="سندات التسليم")],
        "context": ["D1.S1", "D1.S5"],
        "gaps": [{"kind": "missing", "sections": ["D1.S2"], "issue": "لا يحوي الملف عقد القرض", "ocr_suspect": False}],
        "no_angle": [],
        "unrelated_documents": [],
    }
    out.update(kw)
    return copy.deepcopy(out)


def good_two(**kw: Any) -> dict:
    """The judgment (D1) + the objection (D2): two cross-document angles, every core
    section of both placed, the client's side in D2 given by ``by_document``."""
    out = {
        "client": copy.deepcopy(CLIENT_TWO),
        "angles": [
            angle("strengthen", ["D2.S2", "D1.S3", "D1.S4"], title="الحكم أغفل التحويلات"),
            angle("answer", ["D1.S2"], strength="medium", weakness="سندات التسليم"),
            angle("attack", ["D1.S5", "D2.S3"], title="أسباب الحكم لا تحمل منطوقه"),
        ],
        "context": ["D1.S1", "D2.S1"],
        "gaps": [{"kind": "contradiction", "sections": ["D1.S5", "D2.S2"], "issue": "الحكم والاعتراض يختلفان في السداد",
                  "ocr_suspect": False}],
        "no_angle": [],
        "unrelated_documents": [],
    }
    out.update(kw)
    return copy.deepcopy(out)


def good_three(**kw: Any) -> dict:
    """``good_two`` + the lease (D3), named unrelated, its core sections in no_angle."""
    out = good_two(
        no_angle=[{"section": "D3.S2", "why": "عقد إيجار لا صلة له بالدعوى"},
                  {"section": "D3.S3", "why": "مدة عقد آخر"}],
        unrelated_documents=[{"document": "D3", "why": "عقد إيجار بين أطراف أخرى في نزاع آخر"}],
    )
    out.update(kw)
    return copy.deepcopy(out)
