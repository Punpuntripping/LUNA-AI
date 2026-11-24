"""The agent step of the attachment flow (plan §4.6.3, owner decision C26).

Code decides what it can prove; the Completion Search agent fetches the rest. These
tests pin: what is sent and what is never sent, that the agent's fetch is delivered
only when code confirms the number and that the law is in force, that a declined or
failed request leaves code's decision untouched, and that the text is masked before
the model reads it. Synthetic text and a scripted model only.
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import agents.memory.citation_pins.agent_step as agent_step
from agents.completion_search.agent import create_completion_agent
from agents.completion_search.tests.fakes import FakeLibrary, answer, scripted_model
from agents.memory.citation_pins.agent_step import (
    MARK_CLOSE,
    MARK_OPEN,
    MAX_AGENT_SEARCHES,
    R_AGENT,
    R_AGENT_CONFIRMED,
    R_AGENT_OTHER,
    build_tasks,
    needs_agent,
    render_request,
    run_agent_step,
)
from agents.memory.citation_pins.resolve import MEDIUM, NOT_IN_LIBRARY, PINNED, UNRESOLVED, Citation
from agents.memory.citation_pins.tests.test_resolve import DRAFT, LABOR, PROC, resolve

LABOR_T, PROC_T, DRAFT_T = "نظام العمل", "نظام المرافعات الشرعية", "نظام التجربة المقترح"

#: The library the AGENT reads — the same laws and ids the code decisions use.
LAWS = {
    LABOR_T: {"id": LABOR, "status": "in_force_amended", "articles": {
        "77": "إذا أنهي العقد لسبب غير مشروع كان للطرف المتضرر الحق في تعويض.",
        "80": "لا يجوز لصاحب العمل فسخ العقد دون مكافأة إلا في حالات محددة.",
    }},
    PROC_T: {"id": PROC, "status": "in_force", "articles": {"76": "الدفع بعدم الاختصاص النوعي تحكم به المحكمة."}},
    DRAFT_T: {"id": DRAFT, "status": "consultation_ended", "articles": {"3": "نص مشروع لم يصدر."}},
}
#: Article ids as ``FakeLibrary`` builds them, so code and agent name the same article.
ARTICLES = {
    (LABOR, "77"): f"{LABOR}-a77", (LABOR, "80"): f"{LABOR}-a80",
    (PROC, "76"): f"{PROC}-a76", (DRAFT, "3"): f"{DRAFT}-a3",
}
STATUS = {LABOR: "in_force_amended", PROC: "in_force", DRAFT: "consultation_ended"}

# «من النظام» after two laws: code cannot say which.
BACK_REF = (
    "ينظم نظام العمل العلاقة بين الطرفين. وتسري أحكام نظام المرافعات الشرعية على الإجراءات. "
    "وقد نصت المادة 76 من النظام على الدفع بعدم الاختصاص."
)
# No law in the phrase; the nearest law before it happens to be the right one.
GUESS_RIGHT = "تسري أحكام نظام المرافعات الشرعية ونظام العمل. وقد خالف المدعى عليه المادة 77 مخالفة صريحة."
# … and here the nearest one is the wrong one.
GUESS_WRONG = "تسري أحكام نظام العمل ونظام المرافعات الشرعية. وقد خالف المدعى عليه المادة 77 مخالفة صريحة."
CLAUSE = "تسري أحكام نظام المرافعات الشرعية ونظام العمل. وقد التزم المستأجر في المادة 77 بسداد الأجرة."
MISSING = "استند المدعي إلى المادة 5 من نظام فضاء خارجي في طلبه."


def decided(text: str):
    return resolve(text, articles=ARTICLES)


def call(search_id: str, law: str, number: str) -> tuple[str, dict]:
    return ("get_article", {"search_id": search_id, "regulation": law, "article_number": number})


def found(search_id: str, handle: str = "T1") -> dict:
    return {"search_id": search_id, "selected": [handle], "status": "found"}


def step(text: str, *model_steps, status=STATUS, laws=LAWS, **kwargs):
    scan, citations = decided(text)
    agent = create_completion_agent(model=scripted_model(*model_steps))

    async def reg_status(ids):
        return {i: status[i] for i in ids if i in status}

    library = FakeLibrary(laws)
    out, stats = asyncio.run(run_agent_step(
        scan, citations, text, library=library, reg_status=reg_status, agent=agent, **kwargs,
    ))
    return citations, out, stats, library


# --------------------------------------------------------------------------- #
# What is sent
# --------------------------------------------------------------------------- #


def _citation(status: str, reason: str = "", basis: str = "nearest") -> Citation:
    return Citation(seq=1, char_start=0, char_end=5, para_n=1, sentence_start=0, sentence_end=9,
                    article_number="5", law_as_written="نظام", basis=basis, status=status, reason=reason)


@pytest.mark.parametrize("status,reason,basis", [
    (MEDIUM, "link_is_a_guess", "nearest"),
    (MEDIUM, "short_form_of_one_title", "same_phrase"),
    (MEDIUM, "name_differs_by_al", "same_phrase"),
    (MEDIUM, "compound_number_fallback", "same_phrase"),
    (UNRESOLVED, "back_reference_several_laws", "back_ref"),
    (UNRESOLVED, "back_reference_without_law", "back_ref"),
    (UNRESOLVED, "no_law_named", "no_law"),
    (UNRESOLVED, "name_may_run_on", "same_phrase"),
    (UNRESOLVED, "name_opens_several_titles", "same_phrase"),
    (UNRESOLVED, "executive_regulation_not_found", "same_phrase"),
    (UNRESOLVED, "article_not_in_law", "nearest"),       # the guessed law may be the wrong one
    (NOT_IN_LIBRARY, "", "same_phrase"),
])
def test_what_reading_can_settle_goes_to_the_agent(status, reason, basis):
    assert needs_agent(_citation(status, reason, basis))


@pytest.mark.parametrize("status,reason,basis", [
    (PINNED, "", "same_phrase"),                          # code proved it
    ("other_target", "العقد", "other_target"),            # a clause of a contract
    (UNRESOLVED, "range_too_wide", "same_phrase"),
    (UNRESOLVED, "number_written_twice_disagrees", "same_phrase"),
    (UNRESOLVED, "law_has_no_article_text", "same_phrase"),
    (UNRESOLVED, "not_in_force", "same_phrase"),
    (UNRESOLVED, "article_not_in_law", "same_phrase"),    # the document named the law itself
    (UNRESOLVED, "article_not_in_law", "single_law"),
])
def test_what_reading_cannot_change_is_never_sent(status, reason, basis):
    assert not needs_agent(_citation(status, reason, basis))


def test_a_file_code_proved_calls_no_model():
    text = "نصت المادة 77 من نظام العمل على التعويض."
    citations, out, stats, library = step(text, lambda returns: pytest.fail("the model was called"))
    assert [c.status for c in out] == [PINNED] and out == citations
    assert (stats.sent, stats.model) == (0, None) and library.calls == []


def test_the_request_shows_the_passage_the_mention_and_the_laws_before_it():
    scan, citations = decided(BACK_REF)
    tasks, over = build_tasks(scan, citations, BACK_REF)
    assert over == 0 and len(tasks) == 1
    task = tasks[0]
    assert task.laws_before == (PROC_T, LABOR_T)                      # nearest first
    assert f"{MARK_OPEN}المادة 76{MARK_CLOSE}" in task.window and task.law_in_phrase == ""
    text = render_request(tasks, scan, "judgment")
    assert "[S1] المادة 76" in text and "judgment" in text
    assert text.index(LABOR_T) < text.index(PROC_T)                   # the document's laws, in its order
    assert "get_article" in text


def test_a_name_written_several_times_is_one_search():
    text = MISSING + " ثم كرر المدعي الاستناد إلى المادة 5 من نظام فضاء خارجي."
    scan, citations = decided(text)
    tasks, _ = build_tasks(scan, citations, text)
    assert [t.seqs for t in tasks] == [(1, 2)] and tasks[0].law_in_phrase == "نظام فضاء خارجي"


def test_a_link_that_must_be_read_is_one_search_per_citation():
    text = GUESS_WRONG + " ثم خالف المادة 77 مرة أخرى في واقعة ثانية."
    scan, citations = decided(text)
    tasks, _ = build_tasks(scan, citations, text)
    assert [t.seqs for t in tasks] == [(1,), (2,)]


def test_a_file_with_too_many_open_citations_is_capped():
    text = "تسري أحكام نظام العمل ونظام المرافعات الشرعية. " + " ".join(
        f"وخالف المدعى عليه المادة {n} في واقعة." for n in range(200, 200 + MAX_AGENT_SEARCHES + 5)
    )
    scan, citations = decided(text)
    tasks, over = build_tasks(scan, citations, text)
    assert (len(tasks), over) == (MAX_AGENT_SEARCHES, 5)


# --------------------------------------------------------------------------- #
# What comes back
# --------------------------------------------------------------------------- #


def test_the_agent_settles_a_back_reference_and_its_fetch_is_pinned():
    citations, out, stats, library = step(
        BACK_REF, lambda returns: [call("S1", PROC_T, "76")], lambda returns: answer(found("S1")),
    )
    assert (citations[0].status, citations[0].reason) == (UNRESOLVED, "back_reference_several_laws")
    c = out[0]
    assert (c.status, c.decided_by, c.reason) == (PINNED, "model", R_AGENT)
    assert (c.regulation_id, c.article_id) == (PROC, f"{PROC}-a76")
    assert (stats.sent, stats.searches, stats.pinned, stats.failed) == (1, 1, 1, 0)
    assert stats.model == "completion_search" and stats.row() == {
        "sent_to_agent": 1, "pinned_by_agent": 1, "agent_failed": 0,
    }
    assert ("article", PROC_T, "76") in library.calls                 # the AGENT fetched it


def test_the_agent_agreeing_with_codes_guess_is_recorded_as_such():
    citations, out, _, _ = step(
        GUESS_RIGHT, lambda returns: [call("S1", LABOR_T, "77")], lambda returns: answer(found("S1")),
    )
    assert (citations[0].status, citations[0].reason) == (MEDIUM, "link_is_a_guess")
    assert (out[0].status, out[0].reason, out[0].article_id) == (PINNED, R_AGENT_CONFIRMED, f"{LABOR}-a77")


def test_the_agent_corrects_a_wrong_guess():
    citations, out, _, _ = step(
        GUESS_WRONG, lambda returns: [call("S1", LABOR_T, "77")], lambda returns: answer(found("S1")),
    )
    assert citations[0].status == UNRESOLVED                          # the nearest law has no article 77
    assert (out[0].status, out[0].regulation_id, out[0].decided_by) == (PINNED, LABOR, "model")


def test_a_clause_the_agent_declines_stays_as_code_left_it():
    citations, out, stats, library = step(
        CLAUSE, lambda returns: answer({"search_id": "S1", "status": "not_found", "note": "ليست مادة نظام"}),
    )
    assert out == citations and out[0].status == MEDIUM and out[0].decided_by == "code"
    assert (stats.sent, stats.pinned) == (1, 0) and library.calls == []


def test_a_different_article_number_is_never_accepted():
    citations, out, stats, _ = step(
        GUESS_RIGHT, lambda returns: [call("S1", LABOR_T, "80")], lambda returns: answer(found("S1")),
    )
    assert out == citations and stats.pinned == 0                     # the document wrote 77


def test_an_article_of_a_law_not_in_force_is_not_delivered():
    text = "تسري أحكام نظام التجربة المقترح ونظام العمل. وقد خالف المدعى عليه المادة 3 منه."
    citations, out, stats, _ = step(
        text, lambda returns: [call("S1", DRAFT_T, "3")], lambda returns: answer(found("S1")),
    )
    assert citations[0].status != PINNED
    assert (out[0].status, out[0].reason, out[0].decided_by, out[0].article_id) == (
        UNRESOLVED, "not_in_force", "model", None,
    )
    assert stats.pinned == 0


def test_a_law_the_library_does_not_hold_ends_not_in_library():
    citations, out, stats, _ = step(
        MISSING,
        lambda returns: [call("S1", "نظام فضاء خارجي", "5")],
        lambda returns: answer({"search_id": "S1", "status": "not_in_library"}),
    )
    assert citations[0].status == NOT_IN_LIBRARY
    assert (out[0].status, out[0].decided_by, out[0].reason) == (NOT_IN_LIBRARY, "model", "")
    assert stats.not_in_library == 1


def test_the_agent_can_say_the_passage_cites_another_instrument():
    # Code tied the article to the nearest law; the passage is about rules the library lacks.
    citations, out, _, _ = step(
        GUESS_RIGHT,
        lambda returns: [call("S1", "قواعد عمل اللجان التجريبية", "77")],
        lambda returns: answer({"search_id": "S1", "status": "not_in_library"}),
    )
    assert citations[0].status == MEDIUM
    assert (out[0].status, out[0].reason, out[0].article_id) == (NOT_IN_LIBRARY, R_AGENT_OTHER, None)


def test_one_answer_settles_every_place_the_name_is_written():
    text = MISSING + " ثم كرر المدعي الاستناد إلى المادة 5 من نظام فضاء خارجي."
    _, out, stats, _ = step(
        text,
        lambda returns: [call("S1", "نظام فضاء خارجي", "5")],
        lambda returns: answer({"search_id": "S1", "status": "not_in_library"}),
    )
    assert [(c.status, c.decided_by) for c in out] == [(NOT_IN_LIBRARY, "model")] * 2
    assert (stats.sent, stats.searches) == (2, 1)


# --------------------------------------------------------------------------- #
# Failure, limits, masking
# --------------------------------------------------------------------------- #


def test_a_request_that_fails_twice_keeps_codes_decisions():
    attempts = {"n": 0}

    def down(returns):
        attempts["n"] += 1
        raise RuntimeError("provider down")

    citations, out, stats, _ = step(BACK_REF, down)
    assert out == citations and attempts["n"] == agent_step.CHUNK_ATTEMPTS
    assert (stats.sent, stats.failed, stats.pinned) == (1, 1, 0)


def test_a_request_that_runs_out_of_time_keeps_codes_decisions(monkeypatch):
    monkeypatch.setattr(agent_step, "CHUNK_TIMEOUT_S", 0.05)

    async def slow(messages: list, info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(1)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer())])

    scan, citations = decided(BACK_REF)

    async def reg_status(ids):
        return {}

    out, stats = asyncio.run(run_agent_step(
        scan, citations, BACK_REF, library=FakeLibrary(LAWS), reg_status=reg_status,
        agent=create_completion_agent(model=FunctionModel(slow)),
    ))
    assert out == citations and stats.failed == 1


def test_a_status_that_cannot_be_read_takes_nothing_from_the_agent():
    scan, citations = decided(BACK_REF)
    agent = create_completion_agent(model=scripted_model(
        lambda returns: [call("S1", PROC_T, "76")], lambda returns: answer(found("S1")),
    ))

    async def reg_status(ids):
        raise RuntimeError("read failed")

    out, stats = asyncio.run(run_agent_step(
        scan, citations, BACK_REF, library=FakeLibrary(LAWS), reg_status=reg_status, agent=agent,
    ))
    assert out == citations and (stats.pinned, stats.failed) == (0, 1)


def test_more_than_ten_searches_are_split_into_requests():
    runs = {"n": 0}

    def decline(returns):
        runs["n"] += 1
        return answer()

    text = "تسري أحكام نظام العمل ونظام المرافعات الشرعية. " + " ".join(
        f"وخالف المدعى عليه المادة {n} في واقعة." for n in range(200, 212)
    )
    citations, out, stats, _ = step(text, decline)
    assert (stats.searches, stats.sent, runs["n"]) == (12, 12, 2)
    assert out == citations


def test_the_text_is_masked_before_the_model_reads_it():
    seen: list[str] = []
    order: list[str] = []

    async def reader(messages: list, info: AgentInfo) -> ModelResponse:
        order.append("model")
        seen.append("\n".join(str(getattr(p, "content", "")) for m in messages for p in m.parts))
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer())])

    text = "ادعى زيد بن خالد أن نظام العمل ونظام المرافعات الشرعية يسريان. وقد خالف المدعى عليه المادة 77."
    scan, citations = decided(text)

    async def reg_status(ids):
        return {}

    async def before_call():
        order.append("fakes stored")

    asyncio.run(run_agent_step(
        scan, citations, text, library=FakeLibrary(LAWS), reg_status=reg_status,
        agent=create_completion_agent(model=FunctionModel(reader)),
        encode=lambda t: t.replace("زيد بن خالد", "سالم بن فهد"), before_call=before_call,
    ))
    assert seen and "زيد بن خالد" not in seen[0] and "سالم بن فهد" in seen[0]
    assert order[:2] == ["fakes stored", "model"]                     # stored first, then the model


def test_the_models_answer_has_no_field_for_legal_text():
    from agents.completion_search.models import HelperDecision, SearchDecision

    fields = set(SearchDecision.model_fields) | set(HelperDecision.model_fields)
    assert fields == {"searches", "search_id", "asked", "selected", "status", "note"}
