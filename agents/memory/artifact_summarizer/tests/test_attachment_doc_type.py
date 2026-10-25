"""Attachment flow — the ``doc_type`` label: produced, persisted, never invented."""
from __future__ import annotations

import pytest
from pydantic_ai.models.test import TestModel

from agents.memory.artifact_summarizer import (
    AttachmentSummaryInput,
    AttachmentSummaryLLMOutput,
    AttachmentSummaryOutput,
    build_artifact_summary_deps,
    run_attachment_summary,
)
from agents.memory.artifact_summarizer import runner as runner_mod
from agents.memory.artifact_summarizer.agent import create_attachment_summarizer
from agents.memory.artifact_summarizer.models import DOC_TYPES
from agents.memory.artifact_summarizer.prompts import SYSTEM_PROMPT_ATTACHMENT_AR
from agents.memory.summarize import _persist_attachment_summary

OCR_TEXT = "صك حكم صادر من المحكمة العامة بمكة المكرمة في دعوى مطالبة بأجرة. " * 10


# ── the label set ────────────────────────────────────────────────────────────


def test_every_label_is_defined_in_the_prompt():
    for label in DOC_TYPES:
        assert f"`{label}`" in SYSTEM_PROMPT_ATTACHMENT_AR, label


def test_llm_output_accepts_a_label_and_defaults_to_other():
    assert AttachmentSummaryLLMOutput(title="t", summary_md="s", doc_type="contract").doc_type == "contract"
    assert AttachmentSummaryLLMOutput(title="t", summary_md="s").doc_type == "other"


def test_llm_output_maps_an_unknown_label_to_other():
    """A stray label must not fail validation (retries=1, no output fallback —
    two misses would lose the whole summary): unknown → ``other``, case and
    whitespace normalised."""
    assert AttachmentSummaryLLMOutput(title="t", summary_md="s", doc_type="invoice").doc_type == "other"
    assert AttachmentSummaryLLMOutput(title="t", summary_md="s", doc_type="court_judgment").doc_type == "other"
    assert AttachmentSummaryLLMOutput(title="t", summary_md="s", doc_type=" Judgment ").doc_type == "judgment"
    assert AttachmentSummaryLLMOutput(title="t", summary_md="s", doc_type=None).doc_type == "other"


def test_llm_output_schema_still_lists_the_labels():
    schema = AttachmentSummaryLLMOutput.model_json_schema()
    assert schema["properties"]["doc_type"]["enum"] == list(DOC_TYPES)


# ── runner ───────────────────────────────────────────────────────────────────


@pytest.fixture
def pinned_agent(monkeypatch):
    """The real attachment agent, with the runner's factory pointed at it."""
    agent = create_attachment_summarizer()
    monkeypatch.setattr(runner_mod, "create_attachment_summarizer", lambda: agent)
    return agent


@pytest.mark.asyncio
async def test_runner_carries_the_label(pinned_agent):
    model = TestModel(
        custom_output_args={"title": "حكم ابتدائي", "summary_md": "ملخص " * 20, "doc_type": "judgment"}
    )
    with pinned_agent.override(model=model):
        out = await run_attachment_summary(
            AttachmentSummaryInput(filename="scan.pdf", content_md=OCR_TEXT),
            build_artifact_summary_deps(),
        )
    assert out.doc_type == "judgment"
    assert not out.fallback_used


@pytest.mark.asyncio
async def test_runner_maps_an_unknown_label_to_other(pinned_agent):
    model = TestModel(
        custom_output_args={"title": "فاتورة", "summary_md": "ملخص " * 20, "doc_type": "invoice"}
    )
    with pinned_agent.override(model=model):
        out = await run_attachment_summary(
            AttachmentSummaryInput(filename="scan.pdf", content_md=OCR_TEXT),
            build_artifact_summary_deps(),
        )
    assert out.doc_type == "other"
    assert not out.fallback_used


@pytest.mark.asyncio
async def test_failed_call_leaves_the_label_empty(pinned_agent):
    class Boom(TestModel):
        async def request(self, *a, **k):
            raise RuntimeError("provider down")

    with pinned_agent.override(model=Boom()):
        out = await run_attachment_summary(
            AttachmentSummaryInput(filename="scan.pdf", content_md=OCR_TEXT),
            build_artifact_summary_deps(),
        )
    assert out.fallback_used
    assert out.doc_type == ""


# ── persist ──────────────────────────────────────────────────────────────────


class _Recorder:
    """Just enough of supabase-py to capture one ``update``."""

    def __init__(self):
        self.updates: list[dict] = []

    def table(self, _name):
        return self

    def update(self, payload):
        self.updates.append(payload)
        return self

    def eq(self, *_a):
        return self

    def execute(self):
        return None


def _summary(doc_type: str) -> AttachmentSummaryOutput:
    return AttachmentSummaryOutput(title="عقد إيجار", summary_md="ملخص", doc_type=doc_type)


def test_persist_writes_the_label_into_metadata():
    db = _Recorder()
    _persist_attachment_summary(db, "item-1", _summary("contract"), 900, {"filename": "a.pdf"})
    md = db.updates[0]["metadata"]
    assert md["doc_type"] == "contract"
    assert md["filename"] == "a.pdf"


def test_persist_without_a_label_leaves_it_unset():
    db = _Recorder()
    _persist_attachment_summary(db, "item-1", _summary(""), 900, {"filename": "a.pdf"})
    assert "doc_type" not in db.updates[0]["metadata"]
