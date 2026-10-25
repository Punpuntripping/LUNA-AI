"""The responder never sees the workspace-items list (case_documents_forwarding.md D1).

The router keeps it: same ``RouterContext``, same shared renderer — only the
responder stopped calling it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.responder import render_context_block, render_router_context, run_responder
from agents.responder.models import DeepSearchResponse
from agents.router.router import inject_workspace_summaries

from ._fakes import make_input, output_model, router_ctx

_ITEMS = [
    {"item_id": "a", "wi_seq": 2, "kind": "agent_search",
     "title": "بحث التعويض عن التأخير", "summary": "خلاصة البحث السابق"},
    {"item_id": "b", "wi_seq": 3, "kind": "attachment",
     "title": "صك الحكم الابتدائي", "summary": "ملخص الحكم المرفق", "doc_type": "judgment"},
    {"item_id": "c", "wi_seq": 4, "kind": "attachment",
     "title": "عقد المقاولة", "content": "نص العقد بلا ملخص", "doc_type": "contract"},
]


def _assert_no_workspace_list(out: str) -> None:
    assert "<workspace_items>" not in out
    assert "Workspace items in this conversation" not in out
    assert "doc_type" not in out
    for item in _ITEMS:
        assert item["title"] not in out
        assert f'wi="WI-{item["wi_seq"]}"' not in out
    assert "ملخص الحكم المرفق" not in out
    assert "نص العقد بلا ملخص" not in out


def test_rendered_context_has_no_workspace_list():
    out = render_router_context(router_ctx(workspace_item_summaries=_ITEMS))
    _assert_no_workspace_list(out)
    # the rest of the router context still reaches the responder
    assert "<user_name>خالد</user_name>" in out
    assert "نزاع مع مطور عقاري" in out
    assert "Reply tone: formal" in out
    assert "ملخص المحادثة السابقة" in out


def test_context_block_entry_point_has_no_workspace_list():
    inp = make_input("deep_search", router_context=router_ctx(workspace_item_summaries=_ITEMS))
    _assert_no_workspace_list(render_context_block(inp))


def test_router_still_renders_the_list():
    out = inject_workspace_summaries(SimpleNamespace(deps=SimpleNamespace(workspace_item_summaries=_ITEMS)))
    assert "<workspace_items>" in out
    assert '<item wi="WI-3" kind="attachment" doc_type="judgment">' in out


@pytest.mark.asyncio
async def test_agent_instructions_carry_no_workspace_list(patch_responder_model):
    scripted = output_model(DeepSearchResponse(chat_summary_md="الجواب."))
    patch_responder_model(scripted)
    out = await run_responder(
        make_input("deep_search", router_context=router_ctx(workspace_item_summaries=_ITEMS))
    )
    assert out is not None
    instructions = scripted.infos[0].instructions or ""
    assert "## Conversation context for this turn" in instructions
    _assert_no_workspace_list(instructions)
