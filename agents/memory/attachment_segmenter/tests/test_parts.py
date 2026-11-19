"""N parallel parts (test tool, 2026-11-18) — the 2-part production split is unchanged."""
from __future__ import annotations

from agents.memory.attachment_segmenter import prompts as P
from agents.memory.attachment_segmenter.paragraphs import PageRow, paragraphize
from agents.memory.attachment_segmenter.repair import (
    CHARS_PER_AGENT,
    SPLIT_THRESHOLD_CHARS,
    choose_cut,
    choose_cuts,
    parts_for,
)
from agents.memory.attachment_segmenter.runner import _plans

# 60 paragraphs of ~100 chars on 12 pages of 5 paragraphs
LINES = [f"فقرة رقم {i} " + "نص " * 22 for i in range(1, 61)]
CONTENT = "\n".join(LINES)


def _pages():
    out, pos = [], 0
    starts = []
    for i, line in enumerate(LINES):
        if i % 5 == 0:
            starts.append(pos)
        pos += len(line) + 1
    for k, s in enumerate(starts, start=1):
        out.append(PageRow(page_no=k, char_start=s, content_md=CONTENT[s:s + 40]))
    return out


PARAS = paragraphize(CONTENT, doc_type="memo", pages=_pages())


def test_two_parts_is_the_production_cut():
    assert choose_cuts(PARAS, 2) == ([choose_cut(PARAS)[0]], choose_cut(PARAS)[1])


def test_three_parts_cut_at_page_starts_near_the_thirds():
    cuts, how = choose_cuts(PARAS, 3)
    assert cuts == [21, 41] and how == "page_start,page_start"


def test_three_parts_without_pages_still_cut_in_order():
    paras = paragraphize(CONTENT, doc_type="memo")
    cuts, how = choose_cuts(paras, 3)
    assert len(cuts) == 2 and 1 < cuts[0] < cuts[1] <= len(paras) and how == "paragraph,paragraph"


def test_plans_cover_every_paragraph_once():
    for parts in (2, 3, 4):
        plans, info = _plans(PARAS, content_chars=len(CONTENT), doc_type="memo", title="t", summary="s",
                             encode=lambda x: x, parts=parts)
        assert len(plans) == parts and info["parts"] == parts
        assert plans[0].a == 1 and plans[-1].b == len(PARAS)
        assert all(nxt.a == cur.b + 1 for cur, nxt in zip(plans, plans[1:]))


def test_parts_for_is_one_agent_per_started_150k():
    assert [parts_for(c) for c in (0, 6_000, 40_000, 150_000)] == [1, 1, 1, 1]
    assert [parts_for(c) for c in (150_001, 300_000, 300_001, 450_000, 450_001, 1_300_000)] == [2, 2, 3, 3, 4, 9]
    assert CHARS_PER_AGENT == SPLIT_THRESHOLD_CHARS == 150_000


def test_production_rule_when_parts_is_not_given():
    def plan(chars):
        return _plans(PARAS, content_chars=chars, doc_type="memo", title="t", summary="s", encode=lambda x: x)
    for chars in (len(CONTENT), 40_000, 150_000):   # was split above 30k; now one call up to 150k
        plans, info = plan(chars)
        assert len(plans) == 1 and info == {}
    plans, info = plan(150_001)
    assert len(plans) == 2 and "cut_at" in info
    plans, info = plan(300_001)
    assert len(plans) == 3 and info["parts"] == 3 and len(info["cuts"]) == 2


def test_two_part_text_is_unchanged_and_three_part_text_says_so():
    two = P.build_instructions("memo", part=2, a=31, b=60, first_end=5)
    assert two == P.build_instructions("memo", part=2, a=31, b=60, first_end=5, parts=2)
    assert "(2 of 2)" in two and "Another agent maps the rest" in two and "mapped by the other agent" in two
    three = P.build_instructions("memo", part=2, a=21, b=40, first_end=5, parts=3)
    assert "(2 of 3)" in three and "Other agents map the rest" in three and "mapped by another agent" in three


def test_a_middle_part_sees_context_on_both_sides():
    msg = P.build_part_message(PARAS, part=2, a=21, b=40, first_end=5, overlap=3, doc_type="memo",
                               title="t", summary="s", chars=len(CONTENT), parts=3)
    assert "part 2 of 3" in msg.splitlines()[0]
    assert "## First page of the document" in msg
    assert "## Context before your part" in msg and "[P018] " in msg
    assert "## Context after your part" in msg and "[P043] " in msg and "[P044] " not in msg
    last = P.build_part_message(PARAS, part=3, a=41, b=60, first_end=5, overlap=3, doc_type="memo",
                                title="t", summary="s", chars=len(CONTENT), parts=3)
    assert "## Context after your part" not in last


def test_the_agent_sends_an_output_limit(monkeypatch):
    """Without max_tokens Baseten cuts every response at 16,384 tokens (2026-11-18)."""
    from pydantic_ai.models.test import TestModel

    from agents.memory.attachment_segmenter import agent as A

    monkeypatch.setattr(A, "get_agent_model", lambda slot: TestModel())
    ag = A.create_attachment_segmenter("x")
    assert ag.model_settings == {"max_tokens": A.MAX_OUTPUT_TOKENS} and A.MAX_OUTPUT_TOKENS == 200_000
