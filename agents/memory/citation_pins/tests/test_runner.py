"""The attachment citation run end to end on an in-memory database.

Plan ``.claude/plans/completion_search.md`` §4.6 and the §7 list: a contract is never
scanned, a file that cites nothing makes no further read, a changed text redoes the
run, rows hang on the file. The agent step is replaced by a stand-in that changes
nothing, except in the tests at the end that run it with a scripted model.
Synthetic text only.
"""
from __future__ import annotations

import asyncio

import pytest

import agents.completion_search.lookups as lookups
import agents.memory.citation_pins.runner as runner
from agents.helpers import registry
from agents.memory.attachment_segmenter.tests import fake_supabase
from agents.memory.attachment_segmenter.tests.fake_supabase import FakeSupabase
from agents.memory.citation_pins import store
from agents.memory.citation_pins.agent_step import AGENT_STEP_VERSION, AgentStats
from agents.tool_repository.citation_scan import SCAN_RULE_VERSION

REAL_AGENT_PASS = runner._agent_pass
NO_AGENT = {"sent_to_agent": 0, "pinned_by_agent": 0, "agent_failed": 0}

fake_supabase.PRIMARY_KEYS.setdefault("attachment_citation_scans", ("item_id",))
fake_supabase.PRIMARY_KEYS.setdefault("attachment_citations", ("item_id", "seq"))
fake_supabase.CASCADES.setdefault("attachment_citation_scans", [("attachment_citations", "item_id")])

USER, CONV, ITEM = "user-1", "conv-1", "item-1"
LABOR, PROC = "reg-labor", "reg-proc"

TEXT = (
    "صحيفة دعوى\n"
    "استناداً إلى المادة (77) من نظام العمل يطالب المدعي بالتعويض.\n"
    "كما نصت المادة 80 منه على حالات الفسخ.\n"
    "ويحكم نظام المرافعات الشرعية الإجراءات.\n"
    "وبناءً على المادة 9 من العقد المبرم بين الطرفين.\n"
    "والمادة 13 من نظام ديوان المظالم.\n"
)


def db(*, content: str = TEXT, doc_type: str | None = "claim", kind: str = "attachment", **extra) -> FakeSupabase:
    item = {
        "item_id": ITEM, "user_id": USER, "conversation_id": CONV, "case_id": None, "kind": kind,
        "title": "صحيفة الدعوى", "summary": "", "content_md": content,
        "metadata": {"doc_type": doc_type} if doc_type else {}, "deleted_at": None, "created_at": "2026-11-22",
        **extra,
    }
    return FakeSupabase({
        "workspace_items": [item],
        "regulations_v2": [
            {"id": LABOR, "title": "نظام العمل", "clean_title": None, "status_class": "in_force_amended",
             "parent_law_id": None, "doc_type_bucket": "law_statute"},
            {"id": PROC, "title": "نظام المرافعات الشرعية", "clean_title": None, "status_class": "in_force",
             "parent_law_id": None, "doc_type_bucket": "law_statute"},
        ],
        "articles_v2": [
            {"id": "a77", "regulation_id": LABOR, "article_number": "77", "content": "نص المادة 77 الرسمي."},
            {"id": "a80", "regulation_id": LABOR, "article_number": "80", "content": "نص المادة 80 الرسمي."},
            {"id": "p1", "regulation_id": PROC, "article_number": "1", "content": "نص."},
        ],
    })


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(lookups, "_title_cache", None)
    monkeypatch.setattr("agents.utils.citation_pin_flags.citation_pins_enabled", lambda uid: True)
    carded: list[dict] = []
    monkeypatch.setattr(runner, "_top_up_card", lambda supabase, row: carded.append(dict(row)) or 2)

    async def no_agent(supabase, row, scan, citations, content, doc_type, **_kwargs):
        return list(citations), AgentStats()

    monkeypatch.setattr(runner, "_agent_pass", no_agent)       # no model in these tests
    return carded


def run(supabase: FakeSupabase):
    return asyncio.run(runner.run_citation_pins(supabase, ITEM))


def citations(supabase: FakeSupabase) -> list[dict]:
    return sorted(supabase.tables["attachment_citations"], key=lambda r: r["seq"])


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


def test_a_claim_is_scanned_decided_and_stored(_defaults):
    supabase = db()
    result = run(supabase)
    assert result.outcome == runner.WRITTEN
    assert dict(result.counts) == {
        "mentions": 4, "headings": 0, "pinned": 2, "medium": 0, "not_in_library": 1, **NO_AGENT,
    }

    (scan,) = supabase.tables["attachment_citation_scans"]
    assert scan["item_id"] == ITEM and scan["user_id"] == USER
    assert scan["content_chars"] == len(TEXT) and scan["doc_type"] == "claim" and scan["model"] is None
    assert scan["pinned"] == 2

    rows = citations(supabase)
    assert [(r["article_number"], r["status"]) for r in rows] == [
        ("77", "pinned"), ("80", "pinned"), ("9", "other_target"), ("13", "not_in_library"),
    ]
    first = rows[0]
    assert (first["regulation_id"], first["article_id"], first["decided_by"]) == (LABOR, "a77", "code")
    assert TEXT[first["char_start"]:first["char_end"]] == "المادة (77)"
    assert "المادة (77)" in TEXT[first["sentence_start"]:first["sentence_end"]]
    assert rows[2]["article_id"] is None and rows[3]["regulation_id"] is None
    # positions only: no column holds the attachment's sentence
    assert not any("يطالب المدعي" in str(v) for r in rows for v in r.values())
    assert len(_defaults) == 1                     # the conversation's card was topped up once


def test_a_contract_is_never_scanned(_defaults):
    supabase = db(doc_type="contract")
    assert run(supabase).outcome == runner.SKIPPED_NOT_APPLICABLE
    assert supabase.tables["attachment_citation_scans"] == [] and _defaults == []
    assert not runner.citation_pins_applies_to_item(supabase, ITEM)


def test_no_type_yet_is_not_a_contract():
    assert runner.citation_pins_applies_to_item(db(doc_type=None), ITEM)


@pytest.mark.parametrize("kwargs", [
    {"kind": "note"}, {"content": "   "}, {"deleted_at": "2026-11-22T00:00:00Z"},
])
def test_only_a_live_attachment_with_text_applies(kwargs):
    supabase = db(**kwargs)
    assert run(supabase).outcome == runner.SKIPPED_NOT_APPLICABLE
    assert not runner.citation_pins_applies_to_item(supabase, ITEM)


def test_the_flag_off_writes_nothing(monkeypatch):
    monkeypatch.setattr("agents.utils.citation_pin_flags.citation_pins_enabled", lambda uid: False)
    supabase = db()
    assert run(supabase).outcome == runner.SKIPPED_DISABLED
    assert supabase.tables["attachment_citation_scans"] == []


def test_a_file_that_cites_nothing_gets_a_zero_row_and_no_library_read(_defaults):
    supabase = db(content="خطاب قصير بلا أي إشارة إلى نص نظامي.")
    result = run(supabase)
    assert result.outcome == runner.NO_CITATIONS
    (scan,) = supabase.tables["attachment_citation_scans"]
    assert (scan["mentions"], scan["pinned"]) == (0, 0)
    assert citations(supabase) == [] and _defaults == []
    assert not [op for op in supabase.log if op[0] == "articles_v2"]


def test_the_documents_own_headings_are_counted_and_never_resolved():
    supabase = db(content="المادة الأولى: تعريفات\nالمادة الثانية: مدة العقد\nتطبق المادة 77 من نظام العمل.",
                  doc_type="reference")
    result = run(supabase)
    assert (result.counts["headings"], result.counts["mentions"], result.counts["pinned"]) == (2, 1, 1)
    assert [r["article_number"] for r in citations(supabase)] == ["77"]


# --------------------------------------------------------------------------- #
# Redo rules
# --------------------------------------------------------------------------- #


def test_a_second_run_on_the_same_text_does_no_work_but_tops_up_the_card(_defaults):
    supabase = db()
    run(supabase)
    before = len(supabase.log)
    again = run(supabase)
    assert again.outcome == runner.ALREADY_DONE and again.counts["pinned"] == 2
    assert len(citations(supabase)) == 4
    assert not [op for op in supabase.log[before:] if op[0] in ("articles_v2", "regulations_v2")]
    assert len(_defaults) == 2                     # the file may be in another conversation now


def test_changed_text_redoes_the_run_and_replaces_the_rows():
    supabase = db()
    run(supabase)
    supabase.tables["workspace_items"][0]["content_md"] = "نص جديد: المادة 80 من نظام العمل فقط."
    assert runner.load_citation_scan(supabase, ITEM) is None          # stale for the helper frame
    assert run(supabase).outcome == runner.WRITTEN
    assert [(r["seq"], r["article_number"]) for r in citations(supabase)] == [(1, "80")]
    assert len(supabase.tables["attachment_citation_scans"]) == 1
    assert runner.load_citation_scan(supabase, ITEM).pinned == 1


def test_a_failed_write_is_a_failed_run():
    supabase = db()
    supabase.fail.add("attachment_citations")
    result = run(supabase)
    assert result.outcome == runner.FAILED_STORE and result.failed
    assert supabase.tables["attachment_citation_scans"] == []          # no half-written scan left


def test_a_card_that_cannot_be_written_does_not_fail_the_run(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("card down")

    monkeypatch.setattr(runner, "_top_up_card", boom)
    supabase = db()
    assert run(supabase).outcome == runner.WRITTEN
    assert len(citations(supabase)) == 4


# --------------------------------------------------------------------------- #
# What delivery reads
# --------------------------------------------------------------------------- #


def test_load_pins_returns_the_official_text_in_document_order():
    supabase = db()
    run(supabase)
    pins = store.load_pins(supabase, [ITEM, "other-item"], user_id=USER)
    assert list(pins) == [ITEM]
    assert [(p.article_number, p.title, p.text) for p in pins[ITEM]] == [
        ("77", "المادة 77 من نظام العمل", "نص المادة 77 الرسمي."),
        ("80", "المادة 80 من نظام العمل", "نص المادة 80 الرسمي."),
    ]
    assert pins[ITEM][0].char_start == TEXT.index("المادة (77)")


def test_the_text_is_read_at_delivery_time():
    supabase = db()
    run(supabase)
    supabase.tables["articles_v2"][0]["content"] = "نص المادة 77 بعد تعديل النظام."
    assert store.load_pins(supabase, [ITEM])[ITEM][0].text == "نص المادة 77 بعد تعديل النظام."


def test_an_article_that_left_the_library_is_no_longer_pinned():
    supabase = db()
    run(supabase)
    supabase.tables["articles_v2"][:] = [a for a in supabase.tables["articles_v2"] if a["id"] != "a77"]
    assert [p.article_number for p in store.load_pins(supabase, [ITEM])[ITEM]] == ["80"]


def test_a_failed_pin_read_means_no_pins_not_an_error():
    supabase = db()
    run(supabase)
    supabase.fail.add("attachment_citations")
    assert store.load_pins(supabase, [ITEM]) == {}


# --------------------------------------------------------------------------- #
# The helper frame
# --------------------------------------------------------------------------- #


def test_the_helper_is_registered_beside_the_segmenter():
    spec = registry.REGISTRY["citation_pins"]
    segmenter = registry.REGISTRY["segmenter"]
    assert spec.stored and spec.scope == "item"
    assert spec.depends_on == ("ocr", "summary")               # the document type comes from the summary
    assert spec.critical_for == segmenter.critical_for
    assert "segmenter" not in spec.depends_on and "citation_pins" not in segmenter.depends_on
    assert spec.own_ledger_scope is True        # the agent's model use lands on the user's ledger
    assert spec.ceiling_s == 300.0
    assert spec.version == f"{SCAN_RULE_VERSION}+{AGENT_STEP_VERSION}"     # a rule or prompt change is a new version


def test_the_helper_wrapper_raises_on_a_failed_run():
    class Ctx:
        item_id = ITEM

    supabase = db()
    supabase.fail.add("attachment_citations")
    Ctx.supabase = supabase
    with pytest.raises(registry.CitationPinsFailed):
        asyncio.run(registry.run_citation_pins(Ctx()))
    Ctx.supabase = db()
    assert asyncio.run(registry.run_citation_pins(Ctx())).outcome == runner.WRITTEN


def test_the_code_decisions_and_the_store_never_touch_a_model():
    import inspect

    import agents.memory.citation_pins.agent_step as agent_step
    import agents.memory.citation_pins.resolve as resolve_mod

    for module in (store, resolve_mod):
        source = inspect.getsource(module)
        for banned in ("pydantic_ai", "get_agent_model", "run_tracked", "run_completion_search", "embed"):
            assert banned not in source, f"{module.__name__} mentions {banned}"
    # no search fee and no similarity search anywhere in the flow (C12, C13)
    for module in (runner, store, resolve_mod, agent_step):
        source = inspect.getsource(module)
        for banned in ("record_search_fee", "embedding"):
            assert banned not in source, f"{module.__name__} mentions {banned}"


# --------------------------------------------------------------------------- #
# The agent step inside the run (owner decision C26)
# --------------------------------------------------------------------------- #

AGENT_TEXT = (
    "ينظم نظام العمل العلاقة بين الطرفين. وتسري أحكام نظام المرافعات الشرعية على الإجراءات. "
    "وقد نصت المادة 80 من النظام على حالات الفسخ.\n"
    "واستناداً إلى المادة (77) من نظام العمل يطالب المدعي بالتعويض.\n"
)


def _agent_db() -> FakeSupabase:
    supabase = db(content=AGENT_TEXT)
    # the article id the fake library gives article 80 of the labour law
    supabase.tables["articles_v2"].append(
        {"id": f"{LABOR}-a80", "regulation_id": LABOR, "article_number": "80", "content": "نص المادة 80 كما في المكتبة."}
    )
    supabase.tables["articles_v2"][:] = [a for a in supabase.tables["articles_v2"] if a["id"] != "a80"]
    return supabase


def _scripted_agent():
    from agents.completion_search.agent import create_completion_agent
    from agents.completion_search.tests.fakes import FakeLibrary, answer, scripted_model

    laws = {
        "نظام العمل": {"id": LABOR, "status": "in_force_amended", "articles": {"80": "نص المادة 80 كما في المكتبة."}},
        "نظام المرافعات الشرعية": {"id": PROC, "status": "in_force", "articles": {"1": "نص."}},
    }
    model = scripted_model(
        lambda returns: [("get_article", {"search_id": "S1", "regulation": "نظام العمل", "article_number": "80"})],
        lambda returns: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
    )
    return create_completion_agent(model=model), FakeLibrary(laws)


def _run_with_agent(monkeypatch, supabase: FakeSupabase):
    monkeypatch.setattr(runner, "_agent_pass", REAL_AGENT_PASS)
    agent, library = _scripted_agent()
    return asyncio.run(runner.run_citation_pins(supabase, ITEM, agent=agent, library=library))


def test_what_code_cannot_prove_is_fetched_by_the_agent_and_stored(monkeypatch):
    supabase = _agent_db()
    result = _run_with_agent(monkeypatch, supabase)
    assert result.outcome == runner.WRITTEN
    assert (result.counts["pinned"], result.counts["sent_to_agent"], result.counts["pinned_by_agent"]) == (2, 1, 1)

    (scan,) = supabase.tables["attachment_citation_scans"]
    assert scan["model"] == "completion_search"
    assert (scan["sent_to_agent"], scan["pinned_by_agent"], scan["agent_failed"]) == (1, 1, 0)

    by_number = {r["article_number"]: r for r in citations(supabase)}
    assert (by_number["80"]["status"], by_number["80"]["decided_by"], by_number["80"]["regulation_id"]) == (
        "pinned", "model", LABOR,
    )
    assert (by_number["77"]["status"], by_number["77"]["decided_by"]) == ("pinned", "code")
    # delivery reads both, in document order
    assert [p.article_number for p in store.load_pins(supabase, [ITEM])[ITEM]] == ["80", "77"]


def test_a_file_code_decided_alone_records_no_model(monkeypatch):
    supabase = db(content="استناداً إلى المادة (77) من نظام العمل يطالب المدعي بالتعويض.")
    result = _run_with_agent(monkeypatch, supabase)
    (scan,) = supabase.tables["attachment_citation_scans"]
    assert result.counts["pinned"] == 1 and scan["model"] is None and scan["sent_to_agent"] == 0


def test_the_agent_switch_off_is_the_code_only_flow(monkeypatch):
    supabase = _agent_db()
    _run_with_agent(monkeypatch, supabase)                    # stored with the agent's pin
    monkeypatch.setattr("agents.utils.citation_pin_flags.citation_agent_enabled", lambda: False)
    # what the agent pinned is no longer delivered …
    assert [p.article_number for p in store.load_pins(supabase, [ITEM])[ITEM]] == ["77"]
    # … and a new run does not call it
    supabase.tables["attachment_citation_scans"].clear()
    supabase.tables["attachment_citations"].clear()

    async def boom(*_a, **_k):
        raise AssertionError("the agent step ran with its switch off")

    monkeypatch.setattr(runner, "_agent_pass", boom)
    result = run(supabase)
    assert result.outcome == runner.WRITTEN and result.counts["sent_to_agent"] == 0
    assert {r["decided_by"] for r in citations(supabase)} == {"code"}


def test_a_failing_agent_step_never_fails_the_run(monkeypatch):
    supabase = _agent_db()

    async def broken(*_a, **_k):
        raise RuntimeError("agent down")

    monkeypatch.setattr("agents.memory.citation_pins.agent_step.run_agent_step", broken)
    result = _run_with_agent(monkeypatch, supabase)
    assert result.outcome == runner.WRITTEN and result.counts["pinned"] == 1      # code's pin is kept
    assert {r["decided_by"] for r in citations(supabase)} == {"code"}
