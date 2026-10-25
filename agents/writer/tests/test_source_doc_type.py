"""The writer sees what an uploaded document IS (case_documents_forwarding.md D10, §3.5, §5.6).

- ``walkers._fetch_workspace_items_sync`` selects ``metadata->>doc_type``;
  ``build_analyzed_items_direct`` carries it onto ``AnalyzedItem.doc_type``.
- ``<source kind="attachment" … doc_type="judgment" …>`` — attachments only,
  and only when the label is known.
- The exhibits section of every writer prompt tells it how to use the label.
"""
from __future__ import annotations

from typing import Any

import pytest

from agents.writer.models import AnalyzedItem, WriterPackage, WriterStyle
from agents.writer.prompts import WRITER_PROMPTS, render_package_for_system_prompt
from agents.writer_planner.deps import build_writer_planner_deps
from agents.writer_planner.walkers import build_analyzed_items_direct

DOC_TYPE_RULE = (
    "- A user document may carry `doc_type` — what it is (`judgment`, `contract`, "
    "`claim`, `memo`, …). Use it to describe the exhibit correctly in `label_ar` "
    "(a `judgment` is «صورة من صك الحكم …», not «مستند»); never write the label "
    "itself in the document."
)


def _item(item_id: str, *, kind: str, wi_seq: int, doc_type: str = "") -> AnalyzedItem:
    return AnalyzedItem(
        item_id=item_id,
        wi_seq=wi_seq,
        title=f"Item {item_id}",
        kind=kind,
        doc_type=doc_type,
        role="source",
        need="full",
        body_md="نص",
        word_count_before=1,
        word_count_after=1,
    )


def _render(*items: AnalyzedItem) -> str:
    package = WriterPackage(
        intent_ar="اكتب لائحة اعتراضية.",
        subtype="defense_brief",
        edit_mode="fresh",
        plan_md="",
        analyzed_items=list(items),
        templates=[],
        style=WriterStyle(detail_level="medium", tone="formal"),
    )
    return render_package_for_system_prompt(package)


# ── <source> attributes ──────────────────────────────────────────────────────


def test_analyzed_item_doc_type_defaults_to_empty():
    ai = AnalyzedItem(item_id="x", title="t", kind="attachment", role="source",
                      need="full", body_md="b", word_count_before=0, word_count_after=0)
    assert ai.doc_type == ""


def test_attachment_with_doc_type_carries_the_attribute():
    out = _render(_item("a", kind="attachment", wi_seq=3, doc_type="judgment"))
    assert '<source kind="attachment" wi="WI-3" doc_type="judgment" need="full">' in out


def test_attachment_without_doc_type_renders_as_before():
    out = _render(_item("a", kind="attachment", wi_seq=3))
    assert '<source kind="attachment" wi="WI-3" need="full">' in out
    assert "doc_type=" not in out


def test_non_attachment_never_carries_the_attribute():
    out = _render(_item("s", kind="agent_search", wi_seq=4, doc_type="judgment"))
    assert '<source kind="agent_search" wi="WI-4" need="full">' in out
    assert "doc_type=" not in out


# ── system prompt rule ───────────────────────────────────────────────────────


@pytest.mark.parametrize("subtype", sorted(WRITER_PROMPTS))
def test_every_writer_prompt_carries_the_doc_type_rule(subtype):
    prompt = WRITER_PROMPTS[subtype]
    assert DOC_TYPE_RULE in prompt
    # right after the `label_ar` bullet
    assert prompt.index("`label_ar` is a clean court-facing description") < prompt.index(DOC_TYPE_RULE)
    assert prompt.index(DOC_TYPE_RULE) < prompt.index("- If the same document was uploaded more than once")


# ── walkers: the select + the AnalyzedItem field ───────────────────────────


class _Result:
    def __init__(self, data):
        self.data = data


class _RecordingQuery:
    def __init__(self, rows: list[dict], selects: list[str]):
        self._rows = rows
        self._selects = selects

    def select(self, cols, *_a, **_k):
        self._selects.append(cols)
        return self

    def in_(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def is_(self, *_a):
        return self

    def execute(self):
        return _Result(self._rows)


class _RecordingSupabase:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.selects: list[str] = []

    def table(self, name):
        assert name == "workspace_items", name
        return _RecordingQuery(self._rows, self.selects)


def _row(item_id: str, **over: Any) -> dict:
    base = {"item_id": item_id, "wi_seq": 3, "kind": "attachment", "title": "صك",
            "content_md": "نص الحكم", "word_count": 2, "summary": "ملخص",
            "conversation_id": "c1", "user_id": "u1"}
    base.update(over)
    return base


def _deps(supabase: Any):
    return build_writer_planner_deps(
        supabase=supabase,
        user_id="u1",
        conversation_id="c1",
        style=WriterStyle(detail_level="medium", tone="formal"),
    )


@pytest.mark.asyncio
async def test_walker_selects_and_carries_doc_type():
    sb = _RecordingSupabase([
        _row("wi_j", doc_type="judgment"),
        _row("wi_n", wi_seq=4, doc_type=None),   # metadata without the key → NULL
        _row("wi_m", wi_seq=5),                  # alias absent from the row
    ])
    items = await build_analyzed_items_direct(
        selected_ids=["wi_j", "wi_n", "wi_m"],
        role_assignments={"wi_j": "source", "wi_n": "source", "wi_m": "source"},
        deps=_deps(sb),
    )
    assert len(sb.selects) == 1
    assert "doc_type:metadata->>doc_type" in sb.selects[0]
    by_id = {ai.item_id: ai for ai in items}
    assert by_id["wi_j"].doc_type == "judgment"
    assert by_id["wi_n"].doc_type == ""
    assert by_id["wi_m"].doc_type == ""
