"""artifact_analyzer reads an official judicial principle as one (§3.6).

LIVE for every user. Before this, a ``principles`` source was filtered out of
<sources> (not in ``DOMAIN_TABLES``), and had it got through it would have
rendered through the SERVICE fall-through («خدمة: …»). Pins: it is offered,
it opens from ``official_principles.text``, its block is «مبدأ قضائي رسمي» with
its body and decision line, it is named the way its card is, and it can be
quoted only after opening — like every other source.

The search row is the persisted ``PrincipleURAResult`` dump of
``official_principles`` no. 2 (entity 18329), built from the live row of
2026-11-07 — never retyped. The FakeDB is the one test_artifact_analyzer uses.
"""
from __future__ import annotations

from typing import Any

from agents.deep_search_v4.ura.principles import principle_from_row
from agents.memory.artifact_analyzer.context import load_reports, load_sources
from agents.memory.artifact_analyzer.deps import AnalyzerDeps
from agents.memory.artifact_analyzer.models import AnalyzerOutputLLM, QuoteLLM
from agents.memory.artifact_analyzer.tools import DOMAIN_TABLES, read_sources
from agents.memory.artifact_analyzer.verify import finalize
from agents.tests.test_artifact_analyzer import CONVO, USER, FakeDB, _call, _report_row

P2_ID = "64052e1b-17d9-511d-8508-ed401e624d1a"
RULE = "الأصل براءة الذمة حتى يثبت ما يوجب الإدانة."

ROW_P2 = {
    "id": P2_ID,
    "principle_ref": "18329_principles_0002",
    "entity_ref": "18329",
    "principle_no": 2,
    "chapter_title": "الأصول والقواعد",
    "text": RULE,
    "citations": [
        {
            "body": "الهيئة القضائية العليا",
            "body_code": "هـ ق ع",
            "date_hijri": "13/10/1391",
            "decision_no": "254",
        }
    ],
    "issuing_bodies": ["هـ ق ع"],
    "issuing_body_names": ["الهيئة القضائية العليا"],
    "source": {"pages": [33], "source_url": "https://drive.google.com/file/d/x/view"},
}
# Exactly what ``retrieval_artifacts.ura_json`` stores for it.
URA_P2 = principle_from_row(ROW_P2, relevance="high").model_dump(mode="json")


def _db(**over: Any) -> FakeDB:
    tables = {
        "workspace_items": [_report_row("wi-1", "2026-11-02T10:00:00")],
        "retrieval_artifacts": [{
            "artifact_id": "wi-1", "user_id": USER, "deleted_at": None,
            "ura_json": {"high_results": [URA_P2], "medium_results": []},
        }],
        "workspace_item_references": [
            {"wi_id": "wi-1", "n": 7, "ref_id": f"principle:{P2_ID}",
             "domain": "principles", "item_id": P2_ID, "used": True},
        ],
        "official_principles": [{"id": P2_ID, "text": RULE + " (النص الكامل)"}],
    }
    tables.update(over)
    return FakeDB(**tables)


def _deps(db: FakeDB) -> AnalyzerDeps:
    reports = load_reports(db, _call())
    sources = load_sources(db, reports, USER)
    return AnalyzerDeps(
        supabase=db, user_id=USER, conversation_id=CONVO,
        reports={r.label: r for r in reports},
        sources={s.label: s for s in sources},
    )


def test_the_tool_can_open_a_principle_by_its_rule_column() -> None:
    assert DOMAIN_TABLES["principles"] == ("official_principles", "id", "text")


def test_a_principle_is_offered_with_its_own_block_and_name() -> None:
    (source,) = load_sources(_db(), load_reports(_db(), _call()), USER)
    assert source.domain == "principles"
    assert source.open_key == P2_ID
    assert source.name == "المبدأ رقم 2 — الأصول والقواعد — الهيئة القضائية العليا"
    assert source.stored_text == RULE
    assert source.cited_in == [("R1", 7)]
    assert source.block.splitlines() == [
        "### [S1] مبدأ قضائي رسمي: المبدأ رقم 2 — الأصول والقواعد  (R1[7])",
        "**الجهة**: الهيئة القضائية العليا",
        "**القرار**: القرار 254 بتاريخ 13/10/1391هـ",
        RULE,
    ]
    # Never the service fall-through.
    assert "خدمة" not in source.block


def test_a_cited_principle_with_no_search_row_still_reads_as_one() -> None:
    db = _db(retrieval_artifacts=[])
    (source,) = load_sources(db, load_reports(db, _call()), USER)
    assert source.name == "مبدأ قضائي"
    assert source.block.splitlines()[0] == "### [S1] مبدأ قضائي رسمي: مبدأ قضائي  (R1[7])"


def test_opening_reads_the_table_and_falls_back_to_the_stored_rule() -> None:
    db = _db()
    deps = _deps(db)
    assert read_sources(deps, [deps.sources["S1"]]) == {"S1": RULE + " (النص الكامل)"}
    db.broken.add("official_principles")
    assert read_sources(deps, [deps.sources["S1"]]) == {"S1": RULE}


def test_a_principle_is_quotable_only_after_it_is_opened() -> None:
    deps = _deps(_db())
    quote = QuoteLLM(source="S1", text=RULE)
    out = AnalyzerOutputLLM(restatement="س", verdict="sufficient", quotes=[quote], missing=[])
    assert finalize(deps, out).quotes == []

    deps.opened["S1"] = read_sources(deps, [deps.sources["S1"]])["S1"]
    (verified,) = finalize(deps, out).quotes
    assert verified.source_name == "المبدأ رقم 2 — الأصول والقواعد — الهيئة القضائية العليا"
    assert verified.ref_id == f"principle:{P2_ID}"
    # A principle has no parent law, so it can trigger no law-change notice.
    assert verified.regulation_ids == []
