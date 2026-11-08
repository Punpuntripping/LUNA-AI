"""Reading corrections: saved as a note on the report, read by every later agent.

Plan artifact_analyzer_scope_full_text.md §4.4–4.6. The card the user sees never
changes; the note follows the report into the analyzer, the planner, the attached
item, the unfold tool and the router. The reply says each correction as one of
our reading, and carries «اقرأ الحكم كاملًا» for a ruling it quotes.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from agents import orchestrator
from agents.memory.artifact_analyzer.context import load_reports, render_turn_message
from agents.memory.artifact_analyzer.models import (
    AnalyzerResult,
    QuoteLLM,
    ReadingCorrection,
    VerifiedQuote,
)
from agents.memory.artifact_analyzer.verify import finalize
from agents.responder import ruling_links as links_mod
from agents.responder.models import PriorReportArtifact
from agents.responder.prior_report_prompt import PRIOR_REPORT_BLOCK, render_prior_report_package
from agents.router import context as router_context
from agents.tests.test_artifact_analyzer import CONVO, USER, FakeDB, _call, _report_row
from agents.tests.test_artifact_analyzer_full_text import (
    BODY,
    REPORT_SAYS,
    RULING_SAYS,
    _db as full_text_db,
    _deps,
    _out,
)
from agents.tool_repository import unfold_workspace_item as uwi
from agents.utils import reading_corrections as rc

SAVED = {
    "report_text": REPORT_SAYS, "corrected_text": RULING_SAYS, "source_name": "حكم CR1",
    "domain": "cases", "open_key": "CR1", "message_id": "m-1", "at": "2026-11-08T10:00:00",
}
META = {"confidence": "high", rc.KEY: [SAVED]}


def _correction(**over: Any) -> ReadingCorrection:
    base = dict(
        report_id="wi-1", report_text=REPORT_SAYS, corrected_text=RULING_SAYS,
        source_name="حكم CR1", domain="cases", open_key="CR1",
    )
    return ReadingCorrection(**{**base, **over})


class _Writes(FakeDB):
    """FakeDB that records updates and supports what the savers chain."""

    def __init__(self, **tables: list[dict]) -> None:
        super().__init__(**tables)
        self.updates: list[tuple[str, dict, dict]] = []

    def table(self, name: str):
        query = super().table(name)
        db = self

        class _Update:
            def __init__(self, patch: dict) -> None:
                self.patch, self.filters = patch, {}

            def eq(self, col: str, val: Any) -> "_Update":
                self.filters[col] = val
                return self

            def execute(self) -> SimpleNamespace:
                if name in db.broken:
                    raise RuntimeError("db down")
                db.updates.append((name, self.patch, self.filters))
                for row in db.tables.get(name, []):
                    if all(row.get(k) == v for k, v in self.filters.items()):
                        row.update(self.patch)
                return SimpleNamespace(data=[])

        query.update = lambda patch: _Update(patch)  # type: ignore[attr-defined]
        return query


# ── Render and merge ─────────────────────────────────────────────────────────


def test_the_note_names_both_texts_and_the_source() -> None:
    block = rc.render_block(META)
    assert block.startswith("تصحيح بعد قراءة النص الكامل:")
    assert f"«{REPORT_SAYS}»" in block and f"«{RULING_SAYS}»" in block and "«حكم CR1»" in block


def test_no_note_leaves_the_text_untouched() -> None:
    assert rc.render_block({}) == "" and rc.render_block(None) == ""
    assert rc.with_corrections("نص", {"confidence": "high"}) == "نص"
    assert rc.with_corrections("نص", META).startswith("نص\n\n")


def test_malformed_entries_are_ignored() -> None:
    assert rc.entries_of({rc.KEY: [{"report_text": "x"}, "y", None]}) == []
    assert rc.entries_of({rc.KEY: "not a list"}) == []


def test_merge_keeps_one_entry_per_sentence_and_source() -> None:
    again = {**SAVED, "at": "later"}
    other = {**SAVED, "open_key": "CR2"}
    merged = rc.merge([SAVED], [again, other])
    assert [e["open_key"] for e in merged] == ["CR1", "CR2"]
    assert merged[0]["at"] == SAVED["at"]


# ── Saving ───────────────────────────────────────────────────────────────────


def _store(**over: Any) -> _Writes:
    return _Writes(workspace_items=[_report_row("wi-1", "2026-11-02T10:00:00", **over)])


def test_save_adds_the_note_once_and_keeps_the_rest_of_the_metadata() -> None:
    db = _store()
    added = rc.save(db, user_id=USER, conversation_id=CONVO, corrections=[_correction()],
                    message_id="m-9", decode=lambda t: t.replace("ENC", ""))
    assert added == 1
    metadata = db.tables["workspace_items"][0]["metadata"]
    assert metadata["confidence"] == "high" and metadata["gaps"]
    (entry,) = metadata[rc.KEY]
    assert entry["message_id"] == "m-9" and entry["open_key"] == "CR1"
    # The same correction a second time changes nothing.
    assert rc.save(db, user_id=USER, conversation_id=CONVO, corrections=[_correction()]) == 0
    assert len(db.updates) == 1


def test_save_stores_the_reports_words_decoded() -> None:
    db = _store()
    rc.save(db, user_id=USER, conversation_id=CONVO,
            corrections=[_correction(report_text="ENC" + REPORT_SAYS)],
            decode=lambda t: t.replace("ENC", ""))
    assert db.tables["workspace_items"][0]["metadata"][rc.KEY][0]["report_text"] == REPORT_SAYS


def test_save_never_touches_another_users_or_conversations_report() -> None:
    db = _store(user_id="someone-else")
    assert rc.save(db, user_id=USER, conversation_id=CONVO, corrections=[_correction()]) == 0
    db = _store(conversation_id="another")
    assert rc.save(db, user_id=USER, conversation_id=CONVO, corrections=[_correction()]) == 0
    assert db.updates == []


def test_a_failed_save_is_swallowed() -> None:
    db = _store()
    db.broken.add("workspace_items")
    assert rc.save(db, user_id=USER, conversation_id=CONVO, corrections=[_correction()]) == 0


def test_the_orchestrator_save_never_raises(monkeypatch) -> None:
    def boom(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(rc, "save", boom)
    asyncio.run(orchestrator._save_reading_corrections(
        _store(), USER, CONVO, [_correction()], "m-1",
    ))


# ── The five readers ─────────────────────────────────────────────────────────


def _db_with_note() -> FakeDB:
    db = full_text_db()
    db.tables["workspace_items"][0]["metadata"] = META
    return db


def test_the_analyzer_reads_the_note_inside_the_report() -> None:
    db = _db_with_note()
    reports = load_reports(db, _call())
    message = render_turn_message(_call(), reports, [])
    assert "<corrections>" in message and RULING_SAYS in message


def test_the_analyzer_never_quotes_a_sentence_already_corrected() -> None:
    deps = _deps(_db_with_note())
    result = finalize(deps, _out(quotes=[QuoteLLM(source="R1", text=REPORT_SAYS)]))
    assert result.quotes == [] and result.verdict == "insufficient"


def test_the_planner_reads_the_note_with_the_summary() -> None:
    db = _db_with_note()
    (summary,) = orchestrator._load_prior_search_summaries(db, CONVO, USER)
    assert summary.summary.startswith("ملخص") and RULING_SAYS in summary.summary


def test_an_attached_report_carries_the_note() -> None:
    (snapshot,) = orchestrator._load_attached_items(_db_with_note(), ["wi-1"], USER, CONVO)
    assert snapshot.content_md.startswith(BODY) and RULING_SAYS in snapshot.content_md


def test_the_unfold_tool_reads_the_note(monkeypatch) -> None:
    monkeypatch.setattr(uwi, "_fetch_item", lambda *_a, **_k: {
        "title": "t", "content_md": BODY, rc.KEY: [SAVED],
    })
    monkeypatch.setattr(uwi, "resolve_used_sources", lambda *_a, **_k: [])
    parts = uwi.unfold_parts(object(), "wi-1", USER)
    assert parts is not None and parts.body.startswith(BODY) and RULING_SAYS in parts.body


def test_the_router_reads_the_note_with_the_summary() -> None:
    row = _report_row("wi-1", "2026-11-02T10:00:00", metadata=META)
    row[rc.KEY] = [SAVED]  # PostgREST returns the aliased JSON path as its own key
    db = FakeDB(workspace_items=[row])
    summaries, _ = router_context._load_workspace_item_summaries(db, CONVO, USER)
    assert summaries[0]["summary"].startswith("ملخص") and RULING_SAYS in summaries[0]["summary"]
    assert rc.KEY not in summaries[0] and "_corrections" not in summaries[0]


# ── The reply ────────────────────────────────────────────────────────────────


def test_the_responder_package_carries_the_corrections() -> None:
    artifact = PriorReportArtifact(
        question="س", verdict="sufficient", corrections=[_correction()],
    )
    package = render_prior_report_package(artifact)
    assert "## Corrections to our earlier reading" in package
    assert f"<report_said>{REPORT_SAYS}</report_said>" in package
    assert f"<full_text>{RULING_SAYS}</full_text>" in package
    assert "Corrections" not in render_prior_report_package(
        PriorReportArtifact(question="س", verdict="sufficient"),
    )


def test_the_responder_says_a_correction_as_our_reading() -> None:
    assert "عند الرجوع إلى النص الكامل للحكم تبيّن أن" in PRIOR_REPORT_BLOCK


def _quote(domain: str, key: str, name: str = "حكم") -> VerifiedQuote:
    return VerifiedQuote(text="نص", source_name=name, domain=domain, open_key=key)


def test_links_go_to_rulings_whose_text_the_reply_carries() -> None:
    result = AnalyzerResult(
        verdict="sufficient",
        quotes=[
            _quote("cases", "CR2", "حكم 2"), _quote("regulations", "c1"),
            VerifiedQuote(text="جملة التقرير", source_name="حكم 9", item_id="wi-1"),
            _quote("cases", "CR3"), _quote("cases", "CR4"), _quote("cases", "CR5"),
        ],
        reading_errors=[_correction()],
    )
    assert [ref for ref, _ in links_mod.quoted_rulings(result)] == ["CR1", "CR2", "CR3"]


def test_a_ruling_without_a_page_gets_no_button(monkeypatch) -> None:
    import backend.app.services.library_items_service as lis

    async def page(_sb: Any, content_type: str, content_id: str) -> str | None:
        assert content_type == "judgment"
        return "/judgments/hukm-1" if content_id == "id-1" else None

    monkeypatch.setattr(lis, "public_page_url", page)
    db = FakeDB(cases=[{"id": "id-1", "case_ref": "CR1"}, {"id": "id-2", "case_ref": "CR2"}])
    result = AnalyzerResult(verdict="sufficient", quotes=[
        _quote("cases", "CR1", "حكم 1"), _quote("cases", "CR2", "حكم 2"),
    ])
    links = asyncio.run(links_mod.ruling_links(db, result))
    assert links == [{"label": "اقرأ الحكم كاملًا", "url": "/judgments/hukm-1", "title": "حكم 1"}]


def test_no_ruling_no_lookup() -> None:
    assert asyncio.run(links_mod.ruling_links(None, AnalyzerResult(verdict="sufficient"))) == []


def test_a_failed_lookup_costs_only_the_button() -> None:
    db = FakeDB(cases=[])
    db.broken.add("cases")
    result = AnalyzerResult(verdict="sufficient", quotes=[_quote("cases", "CR1")])
    assert asyncio.run(links_mod.ruling_links(db, result)) == []
