"""``build_user_message`` rendering tests (plan §6 rows 6–7, §3.3).

Two levels for the two required assertions:

* unit — the rendered string itself, so a rendering regression is caught at
  the exact place it happens;
* end-to-end — the same content pulled back off the ``UserPromptPart`` the
  model actually received, so a wiring regression in the runner (dropping
  ``prior_summary_md``, passing the WI list positionally into the wrong slot)
  cannot hide behind a green unit test.
"""
from __future__ import annotations

import pytest

from agents.memory.convo_compactor.deps import build_compactor_deps
from agents.memory.convo_compactor.models import CompactionInput
from agents.memory.convo_compactor.prompts import (
    _MAX_MESSAGE_CHARS,
    _MAX_MESSAGES,
    SYSTEM_PROMPT_AR,
    build_user_message,
)
from agents.memory.convo_compactor.runner import handle_compaction_turn

from .conftest import LONG_SUMMARY_AR, arabic_body

PRIOR = (
    "**نية المستخدم:**\nكان المستخدم يبحث عن أثر الشرط الجزائي في عقد المقاولة "
    "المبرم مع شركة البناء الحديث بتاريخ 1444/07/01."
)

MESSAGES = [
    {"role": "user", "content": "ما حكم الشرط الجزائي إذا تجاوز الضرر الفعلي؟"},
    {"role": "assistant", "content": "المحكمة تملك سلطة تعديل الشرط الجزائي."},
]

ITEMS = [
    {"wi_seq": 3, "kind": "agent_search", "title": "الشرط الجزائي", "summary": "ملخّص البحث"},
    {"wi_seq": 11, "kind": "agent_writing", "title": "مذكرة دفاع", "summary": None},
]


# ---------------------------------------------------------------------------
# Prior summary
# ---------------------------------------------------------------------------


def test_prior_summary_in_prompt():
    """A non-empty prior summary is rendered inside ``<prior_summary>``."""
    rendered = build_user_message(
        messages=MESSAGES, workspace_items=ITEMS, prior_summary_md=PRIOR
    )
    assert "<prior_summary>" in rendered
    assert PRIOR in rendered
    assert "first compaction" not in rendered


def test_first_compaction_placeholder():
    """An empty prior summary renders an explicit placeholder, not a blank tag.

    The distinction is load-bearing: "" must read to the model as *first
    compaction*, never as "there was a summary and it was empty".
    """
    rendered = build_user_message(
        messages=MESSAGES, workspace_items=ITEMS, prior_summary_md=""
    )
    assert "<prior_summary>" in rendered
    assert "(none — this is the first compaction of this conversation)" in rendered


@pytest.mark.asyncio
async def test_prior_summary_reaches_the_model(pinned_compactor, tool_model):
    """End-to-end: ``CompactionInput.prior_summary_md`` lands in the user message."""
    model, counter = tool_model(LONG_SUMMARY_AR)

    with pinned_compactor.override(model=model):
        out = await handle_compaction_turn(
            CompactionInput(
                messages=MESSAGES, workspace_items=ITEMS, prior_summary_md=PRIOR
            ),
            build_compactor_deps(),
        )

    assert out.failed is False
    assert PRIOR in counter.last_prompt


# ---------------------------------------------------------------------------
# Workspace-item grounding rows
# ---------------------------------------------------------------------------


def test_wi_list_in_prompt():
    """Each item renders as ``- WI-{seq} (kind) — title`` with its own summary."""
    rendered = build_user_message(
        messages=MESSAGES, workspace_items=ITEMS, prior_summary_md=""
    )
    assert "- WI-3 (agent_search) — الشرط الجزائي" in rendered
    assert "- WI-11 (agent_writing) — مذكرة دفاع" in rendered
    assert "ملخّص البحث" in rendered
    assert "<workspace_items>" in rendered


@pytest.mark.asyncio
async def test_wi_seqs_reach_the_model(pinned_compactor, tool_model):
    """End-to-end: the WI seqs the summary may cite are in the rendered prompt."""
    model, counter = tool_model(LONG_SUMMARY_AR)

    with pinned_compactor.override(model=model):
        await handle_compaction_turn(
            CompactionInput(messages=MESSAGES, workspace_items=ITEMS),
            build_compactor_deps(),
        )

    assert "WI-3" in counter.last_prompt
    assert "WI-11" in counter.last_prompt


def test_empty_wi_list_renders_explicit_none():
    """No items → an explicit marker, so the model does not invent a list."""
    rendered = build_user_message(messages=MESSAGES, workspace_items=[])
    assert "(no workspace items for this span)" in rendered


def test_non_dict_items_are_skipped():
    """Malformed rows are dropped rather than crashing the render."""
    rendered = build_user_message(
        messages=MESSAGES, workspace_items=[None, "junk", ITEMS[0]]  # type: ignore[list-item]
    )
    assert "WI-3" in rendered


def test_item_missing_title_and_kind_get_placeholders():
    rendered = build_user_message(
        messages=MESSAGES, workspace_items=[{"wi_seq": 5}]
    )
    assert "- WI-5 (unknown) — (بلا عنوان)" in rendered


def test_attachment_doc_type_rides_beside_its_kind():
    """An uploaded document's type shows next to its kind; other rows unchanged."""
    rendered = build_user_message(
        messages=MESSAGES,
        workspace_items=[
            {"wi_seq": 7, "kind": "attachment", "title": "صك حكم", "doc_type": "judgment"},
            *ITEMS,
        ],
    )
    assert "- WI-7 (attachment, judgment) — صك حكم" in rendered
    assert "- WI-3 (agent_search) — الشرط الجزائي" in rendered


# ---------------------------------------------------------------------------
# Message rendering, clipping, batch cap
# ---------------------------------------------------------------------------


def test_messages_render_oldest_first_with_roles():
    rendered = build_user_message(messages=MESSAGES, workspace_items=[])
    assert '<msg role="user">' in rendered
    assert '<msg role="assistant">' in rendered
    assert rendered.index(MESSAGES[0]["content"]) < rendered.index(MESSAGES[1]["content"])


def test_long_message_body_is_clipped_with_marker():
    """A pathological body is clipped to ``_MAX_MESSAGE_CHARS`` + an explicit marker."""
    big = arabic_body(_MAX_MESSAGE_CHARS * 3)
    rendered = build_user_message(
        messages=[{"role": "user", "content": big}], workspace_items=[]
    )
    assert "[…]" in rendered
    assert big not in rendered
    assert len(rendered) < len(big)


def test_batch_cap_keeps_most_recent_and_states_the_drop():
    """Over ``_MAX_MESSAGES`` the OLDEST are dropped and the drop is stated."""
    msgs = [
        {"role": "user", "content": f"رسالة رقم {i}"}
        for i in range(_MAX_MESSAGES + 5)
    ]
    rendered = build_user_message(messages=msgs, workspace_items=[])
    assert "[5 earlier message(s) omitted — batch cap]" in rendered
    assert f"رسالة رقم {_MAX_MESSAGES + 4}" in rendered   # newest kept
    assert "رسالة رقم 0\n" not in rendered                 # oldest dropped


def test_blank_messages_render_placeholder():
    rendered = build_user_message(
        messages=[{"role": "user", "content": "  "}], workspace_items=[]
    )
    assert "(no messages)" in rendered


# ---------------------------------------------------------------------------
# user_call_name
# ---------------------------------------------------------------------------


def test_user_call_name_tag_present_only_when_given():
    with_name = build_user_message(
        messages=MESSAGES, workspace_items=[], user_call_name="أبو محمد"
    )
    assert "<user_call_name>أبو محمد</user_call_name>" in with_name

    for empty in (None, "", "   "):
        without = build_user_message(
            messages=MESSAGES, workspace_items=[], user_call_name=empty
        )
        assert "<user_call_name>" not in without


# ---------------------------------------------------------------------------
# System prompt — the load-bearing instructions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fragment",
    [
        "**نية المستخدم:**",          # section 1 heading
        "**ما أُنتج من عناصر:**",      # section 2 heading
        "**خيوط مفتوحة:**",           # section 3 heading
        "REMOVED",                     # the stakes instruction
        "REPLACES it",                 # superseding rule
        "Write the summary in Arabic",  # output-language guard
    ],
)
def test_system_prompt_carries_the_load_bearing_instructions(fragment):
    assert fragment in SYSTEM_PROMPT_AR
