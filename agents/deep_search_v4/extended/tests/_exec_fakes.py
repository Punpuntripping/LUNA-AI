"""Shared fakes for the execution-side tests (runner / merger / publish). No I/O, no LLM."""
from __future__ import annotations

from typing import Any

from agents.deep_search_v4.aggregator.models import AggregatorOutput, Reference
from agents.deep_search_v4.extended.models import (
    AngleCard,
    DocView,
    ExtendedPlan,
    ExtendedRequest,
    ExtendedSearchReport,
    PlannerView,
    SectionView,
)
from agents.models import ChatMessageSnapshot, WorkspaceItemSnapshot

#: A deterministic masking pair (backend/tests/test_masking_wiring.py style).
REAL_ID = "1029384756"
FAKE_ID = "1029111111"


def fixed_codec():
    from shared.privacy import PrivacyCodec

    return PrivacyCodec(real_to_fake={REAL_ID: FAKE_ID}, fake_to_real={FAKE_ID: REAL_ID}, enabled=True)


MARKED_D1 = (
    "〔D1·S1 · claimant · جوهري〕\n\nيطالب المدعي بالدين، هويته" + REAL_ID + ".\n\n"
    "〔D1·S2 · respondent · جوهري〕\n\nيدفع المدعى عليه بعدم الاختصاص."
)


def make_view(*, d2_marked: str = "") -> PlannerView:
    return PlannerView(
        map_id="map-1",
        message_id="msg-1",
        documents=[
            DocView(id="D1", item_id="item-1", title="صحيفة الدعوى", doc_type="claim",
                    summary="ملخص الصحيفة " + REAL_ID, content_chars=200, marked_text=MARKED_D1),
            DocView(id="D2", item_id="item-2", title="مذكرة الخصم", doc_type="memo",
                    summary="ملخص المذكرة", content_chars=100, marked_text=d2_marked),
        ],
        angles=[
            AngleCard(id="A1", kind="strengthen", strength="strong", for_side="claimant", for_party="المدعي",
                      title="ثبوت الدين", argument="الدين ثابت بالسند.", legal_question="حجية السند",
                      weakness="السند غير مؤرخ",
                      sections=[SectionView(ref="D1.S1", label="الموكّل", owner="claimant")]),
            AngleCard(id="A2", kind="answer", strength="medium", for_side="claimant", for_party="المدعي",
                      title="دفع عدم الاختصاص", argument="الخصم يدفع بعدم الاختصاص.",
                      legal_question="الاختصاص المكاني", weakness="",
                      sections=[SectionView(ref="D1.S2", label="الخصم", owner="respondent")]),
        ],
        section_refs=frozenset({"D1.S1", "D1.S2"}),
    )


def make_request(title: str = "بحث أول", *, mode: str = "reg_compliance_led", cap: int | None = 8,
                 angles: list[str] | None = None) -> ExtendedRequest:
    return ExtendedRequest(title=title, angles=angles if angles is not None else ["A1"], sections=[],
                           mode=mode, max_sub_queries=cap, focus="تركيز البحث")


def make_plan(requests: list[ExtendedRequest] | None = None) -> ExtendedPlan:
    return ExtendedPlan(
        understanding="يريد المستخدم تقييم موقفه " + REAL_ID,
        client_side="claimant",
        requests=requests or [make_request()],
    )


def make_ref(n: int, ref_id: str, title: str = "", snippet: str = "", source_type: str = "article") -> Reference:
    return Reference(
        n=n, source_type=source_type, regulation_title="نظام المرافعات", title=title or f"المادة {n}",
        snippet=snippet, relevance="high", ref_id=ref_id,
    )


def make_agg(refs: list[Reference], synthesis: str, *, confidence: str = "medium",
             gaps: list[str] | None = None) -> AggregatorOutput:
    return AggregatorOutput(synthesis_md=synthesis, references=refs, confidence=confidence,
                            gaps=gaps or [], disclaimer_ar="تنبيه قانوني")


def make_report(index: int, agg: Any, *, title: str = "", ok: bool = True,
                angles: list[str] | None = None) -> ExtendedSearchReport:
    return ExtendedSearchReport(index=index, request=make_request(title or f"بحث {index}", angles=angles),
                                ok=ok, agg_output=agg)


async def _embed(_q: str) -> list[float]:  # pragma: no cover - never invoked
    return []


def planner_deps(attached: list[WorkspaceItemSnapshot] | None = None, **kw: Any):
    from agents.deep_search_v4.planner.deps import PlannerDeps

    return PlannerDeps(
        supabase=kw.pop("supabase", object()),  # type: ignore[arg-type]
        embedding_fn=_embed,
        user_id=kw.pop("user_id", "u1"),
        conversation_id=kw.pop("conversation_id", "c1"),
        attached_items=list(attached or []),
        **kw,
    )


def attached_snapshot(item_id: str, seq: int, *, content: str = "نص", summary: str = "ملخص مرمز",
                      doc_type: str = "claim", title: str = "عنوان مرمز") -> WorkspaceItemSnapshot:
    return WorkspaceItemSnapshot(item_id=item_id, kind="attachment", title=title, content_md=content,
                                 summary=summary, metadata={"doc_type": doc_type}, wi_seq=seq)


def recent() -> list[ChatMessageSnapshot]:
    return [
        ChatMessageSnapshot(role="user", content="أرفقت الصحيفة", created_at="2026-11-24T00:00:00Z"),
        ChatMessageSnapshot(role="assistant", content="تم الاستلام", created_at="2026-11-24T00:00:01Z"),
    ]
