"""Input / output contracts for the item_analyzer (Layer-4 Memory) agent.

This module is the single source of truth for the analyzer's call surface
and verdict shapes. Downstream callers (writer_planner today; router and
deep_search_planner later) import directly from here.

Design notes:

- ``AnalyzerCall`` is the entire caller-facing request. Everything else
  (``user_id``, ``conversation_id``, ``caller_id``) is on ``AnalyzerDeps``.
- Verdicts are 3-state (``full`` / ``partial`` / ``none``) and split across
  two families:

    * REFS family — kinds whose ``content_md`` carries numbered ``[n]``
      reference tokens (``agent_search``, ``agent_writer``).
    * META family — kinds whose ``content_md`` is plain prose / markdown
      with no ``[n]`` refs (``attachment``, ``notes``).

- The merged ``WIVerdict`` union uses a **two-level discriminator**: the
  outer union discriminates by ``kind`` (the family marker on every variant),
  and each family's inner union discriminates by ``need``. Pydantic v2
  handles this natively because each ``kind`` literal value uniquely picks
  exactly one family branch, and each ``need`` literal value uniquely picks
  the variant inside that branch.

- The analyzer never returns full ``content_md`` — partial verdicts carry a
  ``distilled`` slice (analyzer-written, Arabic) and/or structured metadata.
  The caller does the unfolding (raw read for ``full``, distilled + refs for
  ``partial``, skip for ``none``).
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Caller identity — pinned to the three planned callers. Each one owns a
# prompt sub-dir under ``agents/memory/item_analyzer/<caller>/prompts/``.
# Adding a caller = add it here, then register prompts in prompt_registry.
# ---------------------------------------------------------------------------

CallerId = Literal["router", "writer_planner", "deep_search_planner"]


# ---------------------------------------------------------------------------
# Loader row — what ``_load_workspace_items`` returns from the SELECT.
# Mirrors only the columns the analyzer actually renders into the user
# message, so the SQL stays narrow (no over-fetch of ``describe_query``,
# ``summary_md``, etc.).
# ---------------------------------------------------------------------------


class WorkspaceItemRow(BaseModel):
    """A single ``workspace_items`` row, projected to analyzer-relevant columns.

    The loader pulls exactly these columns; anything else stays in the DB.
    The ``word_count`` field (migration 048) is rendered into the user
    message so the LLM can size its verdict ("if the whole WI is small and
    on-topic, return ``full``; otherwise prefer ``partial`` with a focused
    ``distilled``").
    """

    item_id: str
    kind: str
    title: str | None
    content_md: str
    word_count: int
    wi_seq: int | None = None
    """``WI-{seq}`` sequence from ``workspace_items.wi_seq`` (migration 052).

    Used by the runner to build an alias map for the LLM-facing prompts so
    the LLM never sees the raw ``item_id`` UUID. ``None`` only for legacy
    rows / items without a ``conversation_id`` (those don't get a seq from
    the BEFORE INSERT trigger — partial unique index excludes them).
    """


# ---------------------------------------------------------------------------
# Call surface — the entire caller-facing request. Two fields, nothing else.
# ---------------------------------------------------------------------------


class AnalyzerCall(BaseModel):
    """Caller-facing request to ``analyze()``.

    Attributes:
        query: The planner's question, verbatim — "what's THIS WI to me?".
            Rendered into the user message for both family runs.
        targeted_wi: One or more ``workspace_items.item_id`` values. The
            runner loads them with RLS scoping, partitions by kind family,
            and dispatches up to two parallel LLM calls.
    """

    query: str
    targeted_wi: list[str]


# ---------------------------------------------------------------------------
# Domain error — Arabic-message-carrying. Raised only for defense-in-depth
# conditions (e.g. an unsupported ``kind`` reached the runner). LLM failures
# do NOT raise — they degrade to all-``none`` verdicts for the failed family.
# ---------------------------------------------------------------------------


class AnalyzerError(Exception):
    """Domain error raised by the item_analyzer runner.

    The message is Arabic so it can be surfaced to user-facing layers
    unchanged when the caller chooses to do so (the analyzer itself never
    talks to the user).
    """


# ===========================================================================
# REFS family — content carries ``[n]`` reference tokens.
# Kinds: ``agent_search`` (deep-search artifacts) and ``agent_writer``
# (long-form writing artifacts). Refs are unfolded caller-side via the
# existing ``references_service.fetch_item_references``.
# ===========================================================================


class RefsVerdictFull(BaseModel):
    """The entire ``content_md`` matters — caller unfolds the whole WI.

    Use this when the WI is short or every section is on-topic for ``query``.
    The caller will read ``workspace_items.content_md`` directly and embed
    it raw in the WriterPackage / next prompt.
    """

    need: Literal["full"]
    item_id: str
    kind: Literal["agent_search", "agent_writer"]
    rational: str  # Arabic — explains why the whole WI is on-topic.


class RefsVerdictPartial(BaseModel):
    """Only part of the WI matters.

    ``distilled`` is the analyzer-written Arabic slice that captures the
    relevant content for ``query``. ``refs_needed`` lists the ``[n]`` tokens
    the caller must additionally resolve via ``references_service`` to keep
    the slice self-contained. ``refs_needed`` may be empty if ``distilled``
    alone suffices.
    """

    need: Literal["partial"]
    item_id: str
    kind: Literal["agent_search", "agent_writer"]
    distilled: str  # Arabic, analyzer-written slice of the on-topic content.
    refs_needed: list[int] = Field(default_factory=list)
    rational: str


class RefsVerdictNone(BaseModel):
    """Irrelevant — caller drops this WI entirely.

    The ``rational`` should answer "why isn't this on-topic for ``query``"
    in Arabic (e.g. «غير ذي صلة لأن…») so the planner can surface it in
    ``plan_md`` if needed.
    """

    need: Literal["none"]
    item_id: str
    kind: Literal["agent_search", "agent_writer"]
    rational: str


RefsVerdict = Annotated[
    Union[RefsVerdictFull, RefsVerdictPartial, RefsVerdictNone],
    Field(discriminator="need"),
]


# ===========================================================================
# META family — prose / markdown, no ``[n]`` refs.
# Kinds: ``attachment`` (OCR-extracted uploads) and ``notes`` (user notes).
# Partial verdicts can carry both a prose ``distilled`` slice and a
# structured ``extracted_metadata`` dict — use both when both apply.
# ===========================================================================


class MetaVerdictFull(BaseModel):
    """The entire ``content_md`` matters — caller embeds it raw."""

    need: Literal["full"]
    item_id: str
    kind: Literal["attachment", "notes"]
    rational: str


class MetaVerdictPartial(BaseModel):
    """Part of the WI matters.

    Per design decision D1:

    - ``extracted_metadata`` covers structured facts (parties, dates,
      amounts, clause numbers) as a flat ``str → str`` map.
    - ``distilled`` covers the prose slice that matters (Arabic,
      analyzer-written).

    Both may be populated simultaneously when both apply (e.g. an attachment
    that has both a relevant clause AND structured party info). Either may
    be omitted when not applicable: ``distilled`` defaults to ``None``,
    ``extracted_metadata`` defaults to an empty dict.
    """

    need: Literal["partial"]
    item_id: str
    kind: Literal["attachment", "notes"]
    distilled: str | None = None
    extracted_metadata: dict[str, str] = Field(default_factory=dict)
    rational: str


class MetaVerdictNone(BaseModel):
    """Irrelevant — caller drops this WI."""

    need: Literal["none"]
    item_id: str
    kind: Literal["attachment", "notes"]
    rational: str


MetaVerdict = Annotated[
    Union[MetaVerdictFull, MetaVerdictPartial, MetaVerdictNone],
    Field(discriminator="need"),
]


# ===========================================================================
# Per-family structured outputs — what each sub-agent's LLM returns.
# One row in ``agent_runs`` per family run; ``overall_rational`` carries an
# optional cross-item strategic note (e.g. "all three contracts argue the
# same point — pick the cleanest one").
# ===========================================================================


class RefsAnalyzeOutput(BaseModel):
    """Output of the refs-family sub-agent."""

    items: list[RefsVerdict]
    overall_rational: str | None = None


class MetaAnalyzeOutput(BaseModel):
    """Output of the meta-family sub-agent."""

    items: list[MetaVerdict]
    overall_rational: str | None = None


# ===========================================================================
# Caller-facing merged result — what ``analyze()`` returns.
#
# ``WIVerdict`` uses a TWO-LEVEL discriminator:
#
#   * Outer discriminator = ``kind``. Each ``kind`` literal value
#     (``"agent_search"`` / ``"agent_writer"`` vs ``"attachment"`` / ``"notes"``)
#     uniquely identifies the family branch — Pydantic picks ``RefsVerdict``
#     for the first two and ``MetaVerdict`` for the second two.
#   * Inner discriminator (inside each family) = ``need``. Already configured
#     on the family unions above.
#
# Pydantic v2 supports this natively. Sanity-check walks:
#
#   {"need": "partial", "kind": "agent_search", "item_id": "...",
#    "distilled": "...", "refs_needed": [3, 7], "rational": "..."}
#       outer picks ``kind="agent_search"`` → RefsVerdict
#       inner picks ``need="partial"``      → RefsVerdictPartial  OK
#
#   {"need": "none", "kind": "attachment", "item_id": "...",
#    "rational": "..."}
#       outer picks ``kind="attachment"``   → MetaVerdict
#       inner picks ``need="none"``         → MetaVerdictNone     OK
# ===========================================================================


WIVerdict = Annotated[
    Union[RefsVerdict, MetaVerdict],
    Field(discriminator="kind"),
]


class AnalyzeOutput(BaseModel):
    """Final merged result returned by ``analyze()`` to callers.

    Attributes:
        query_echo: The original ``AnalyzerCall.query`` echoed back. Lets the
            caller assert it's looking at the verdict for the right turn (and
            simplifies logging).
        items: One verdict per resolvable WI, in the input order of
            ``AnalyzerCall.targeted_wi``. Out-of-scope / missing ids are
            silently dropped — caller diffs ``items`` against ``targeted_wi``
            if it needs to know which were skipped.
        overall_rational: Optional cross-item note. When both families
            return one, the runner joins them with ``\\n\\n``. ``None`` when
            both sub-runs omit it.
    """

    query_echo: str
    items: list[WIVerdict]
    overall_rational: str | None = None


# ===========================================================================
# Internal LLM-facing verdict shapes (agent communication protocol).
#
# The analyzer's sub-agents see and emit WI aliases (``WI-{seq}``) — never
# raw ``item_id`` UUIDs. These private models bind the LLM's structured
# output to the alias surface; the runner converts each LLM verdict to its
# public counterpart (with ``item_id`` filled in from the alias map) before
# returning to the caller.
#
# Why private (``_``-prefixed): callers MUST keep consuming UUID-bearing
# verdicts. These shapes are an implementation detail of the agent ↔ LLM
# boundary and should never leak outside ``item_analyzer``.
#
# See ``.claude/plans/agent_communication_protocol.md`` for the rationale —
# "Models hallucinate UUIDs. They transpose hex digits, drop dashes, and
# occasionally invent plausible-looking ones from scratch."
# ===========================================================================


# --- REFS family (LLM surface) ---------------------------------------------


class _RefsVerdictFullLLM(BaseModel):
    """LLM-emitted ``full`` refs verdict — carries ``wi`` alias, not UUID."""

    need: Literal["full"]
    wi: str  # "WI-{seq}" — runner resolves to item_id UUID.
    kind: Literal["agent_search", "agent_writer"]
    rational: str


class _RefsVerdictPartialLLM(BaseModel):
    """LLM-emitted ``partial`` refs verdict — alias surface."""

    need: Literal["partial"]
    wi: str
    kind: Literal["agent_search", "agent_writer"]
    distilled: str
    refs_needed: list[int] = Field(default_factory=list)
    rational: str


class _RefsVerdictNoneLLM(BaseModel):
    """LLM-emitted ``none`` refs verdict — alias surface."""

    need: Literal["none"]
    wi: str
    kind: Literal["agent_search", "agent_writer"]
    rational: str


_RefsVerdictLLM = Annotated[
    Union[_RefsVerdictFullLLM, _RefsVerdictPartialLLM, _RefsVerdictNoneLLM],
    Field(discriminator="need"),
]


class _RefsAnalyzeOutputLLM(BaseModel):
    """Refs-family LLM output — verdicts keyed by ``wi`` alias."""

    items: list[_RefsVerdictLLM]
    overall_rational: str | None = None


# --- META family (LLM surface) ---------------------------------------------


class _MetaVerdictFullLLM(BaseModel):
    """LLM-emitted ``full`` meta verdict — alias surface."""

    need: Literal["full"]
    wi: str
    kind: Literal["attachment", "notes"]
    rational: str


class _MetaVerdictPartialLLM(BaseModel):
    """LLM-emitted ``partial`` meta verdict — alias surface."""

    need: Literal["partial"]
    wi: str
    kind: Literal["attachment", "notes"]
    distilled: str | None = None
    extracted_metadata: dict[str, str] = Field(default_factory=dict)
    rational: str


class _MetaVerdictNoneLLM(BaseModel):
    """LLM-emitted ``none`` meta verdict — alias surface."""

    need: Literal["none"]
    wi: str
    kind: Literal["attachment", "notes"]
    rational: str


_MetaVerdictLLM = Annotated[
    Union[_MetaVerdictFullLLM, _MetaVerdictPartialLLM, _MetaVerdictNoneLLM],
    Field(discriminator="need"),
]


class _MetaAnalyzeOutputLLM(BaseModel):
    """Meta-family LLM output — verdicts keyed by ``wi`` alias."""

    items: list[_MetaVerdictLLM]
    overall_rational: str | None = None
