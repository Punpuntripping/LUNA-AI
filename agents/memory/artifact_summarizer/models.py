"""Input / output contracts for the artifact_summarizer agent.

The agent's audience is **other agents** (router, planner, follow-up turns),
not the end user. Output is a single Arabic markdown blob telling the next
agent what this workspace item covers and what it does NOT cover — so the
downstream decision (re-query, route elsewhere, stop) can be made without
re-reading the full ``content_md``.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


@dataclass
class ArtifactSummaryInput:
    """Everything the summarizer needs.

    These map 1:1 to the three ``workspace_items`` columns the agent
    reads (post-Window-D-v2):

    - ``title``           → ``workspace_items.title``
    - ``describe_query``  → ``workspace_items.describe_query`` — the
      router-written description of the question the artifact answers
      (migration 038). NOT the user's raw chat message.
    - ``content_md``      → ``workspace_items.content_md`` — the full body.

    ``kind`` is passed as context only (lets the prompt set tone for e.g.
    ``compose_document`` vs ``agent_search``), not branched on. The agent
    picks whatever output shape best conveys coverage.
    """

    describe_query: str
    content_md: str
    title: str
    kind: str = "agent_search"


class ArtifactSummaryLLMOutput(BaseModel):
    """The structured output the LLM is asked to produce.

    Single semantic field: ``summary_md``. Wrapping it in a Pydantic model
    (rather than ``output_type=str``) gives pydantic_ai a clean schema to
    enforce, avoids accidental whitespace/quote-wrapping, and leaves room to
    add fields later without breaking the wire format.
    """

    summary_md: str = Field(
        description=(
            "Arabic markdown summary written for downstream AGENTS — describes "
            "what the artifact covers and what it does NOT cover."
        ),
    )


class ArtifactSummaryOutput(BaseModel):
    """Final output returned by the runner to callers (orchestrator)."""

    summary_md: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_reasoning: int = 0
    model_used: str = ""
    fallback_used: bool = False
