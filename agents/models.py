"""Pydantic output models for router, dispatch, and specialist agents.

Wave 9 slim: TaskContinue/TaskEnd are gone (no consumer left after the
specialist refactor). The router now emits ChatResponse | DispatchAgent;
specialists return SpecialistResult; orchestrator passes MajorAgentInput
into Tier-2 agents which never read the DB directly.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any, Literal


# ── Constants ─────────────────────────────────────────────────────────────

# Hard upper bound on how many workspace items the router may attach to a
# single specialist dispatch. Single source of truth — bump as needed.
# Field constraint enforces the bound; an output_validator on the router
# raising ModelRetry gives the LLM a guided retry when it overshoots.
MAX_ATTACHED_ITEMS = 7


# ── Planner outputs (used as agent output_type) ───────────────────────────

class PlannerResult(BaseModel):
    """Structured output from the deep search planner agent."""
    task_done: bool = Field(description="Whether the research task is complete this turn")
    end_reason: Literal["completed", "out_of_scope", "pending"] = Field(
        default="pending",
        description='Why the task ended. "pending" if task is not done yet.',
    )
    answer_ar: str = Field(
        description="Short Arabic summary for chat display. "
        "The full report goes in the artifact, not here.",
    )
    search_summary: str = Field(
        default="",
        description="Internal summary for router context — what was searched and found.",
    )
    artifact_md: str = Field(
        default="",
        description="Full markdown report content. Must be complete, not a diff.",
    )


# ── Router outputs ────────────────────────────────────────────────────────

class ChatResponse(BaseModel):
    """Router responds directly to the user."""
    type: Literal["chat"] = "chat"
    message: str = Field(description="Response text to show the user")


class DispatchAgent(BaseModel):
    """Router dispatches a specialist agent (Tier 2).

    Migration 052 / agent communication protocol: the LLM emits ``target_wi``
    and ``attached_wis`` (alias strings like ``"WI-3"``); the router's output
    validator resolves them against ``RouterDeps.wi_alias_map`` and fills the
    UUID fields (``target_item_id``, ``attached_item_ids``) the orchestrator
    consumes. Both representations live on the model so consumers can keep
    using the UUID fields without changes while telemetry / forensic readers
    can also see the alias the model actually chose.
    """
    type: Literal["dispatch"] = "dispatch"
    agent_family: Literal[
        "deep_search", "simple_search", "writing", "memory"
    ] = Field(
        description="Which specialist family to dispatch."
    )
    task_label: str = Field(
        max_length=80,
        description=(
            "Short Arabic content-derived label (≤80 chars, typical 30–60). "
            "Used as workspace_item.title and agent_runs.task_label. "
            "Describes the SUBJECT of the task, not the action — no verbs "
            "like «أبحث»/«أكتب»/«أحلل». Stable across rephrases of the same "
            "question."
        ),
    )
    # NOTE: the router no longer paraphrases the query into a ``describe_query``
    # field. The specialist receives the raw user message via
    # ``MajorAgentInput.describe_query`` (filled by the orchestrator from the
    # user's actual question) plus ``recent_messages`` for conversation context.
    # -- LLM-emitted alias fields ------------------------------------------
    target_wi: str | None = Field(
        default=None,
        description=(
            'Alias of the workspace item to edit/extend (e.g. "WI-3"). '
            "None for fresh outputs. Use the WI-{n} labels exposed in the "
            "workspace summaries; never emit a raw UUID."
        ),
    )
    attached_wis: list[str] = Field(
        default_factory=list,
        max_length=MAX_ATTACHED_ITEMS,
        description=(
            'Aliases of workspace items to attach (e.g. ["WI-1", "WI-3"]). '
            f"Hard cap of {MAX_ATTACHED_ITEMS}. Use the WI-{{n}} labels from "
            "the workspace summaries; never emit raw UUIDs."
        ),
    )
    # -- Resolver-filled UUID fields ---------------------------------------
    # These are populated by the output validator from the alias fields. The
    # LLM should NOT fill them (the prompt instructs it to use the WI-{n}
    # alias fields instead); if it does, the validator overwrites them.
    target_item_id: str | None = Field(
        default=None,
        description=(
            "Resolved workspace_items.item_id UUID — populated by the router "
            "output validator from target_wi. Do not emit directly; use "
            "target_wi with a WI-{n} alias instead."
        ),
    )
    attached_item_ids: list[str] = Field(
        default_factory=list,
        max_length=MAX_ATTACHED_ITEMS,
        description=(
            "Resolved workspace_items.item_id UUIDs — populated by the "
            "router output validator from attached_wis. Do not emit directly; "
            "use attached_wis with WI-{n} aliases instead."
        ),
    )
    subtype: str | None = Field(
        default=None,
        description=(
            "For agent_family='writing', the document subtype: "
            "contract (عقد), memo (مذكرة), legal_opinion (رأي قانوني), "
            "defense_brief (لائحة دفاع), letter (خطاب), summary (ملخص). "
            "Ignored for non-writing families. Defaults to 'memo' downstream if missing."
        ),
    )


# ── Tier-2 input contract ─────────────────────────────────────────────────

class ChatMessageSnapshot(BaseModel):
    """A single chat message frozen at dispatch time."""
    role: Literal["user", "assistant"]
    content: str
    created_at: str


class WorkspaceItemSnapshot(BaseModel):
    """A workspace item frozen at dispatch time, with full content_md.

    Migration 052: ``wi_seq`` is the conversation-scoped integer alias
    (``WI-{wi_seq}``) the planner / writer LLMs use when referring to this
    item. ``None`` for items without a conversation (case-only). The
    orchestrator builds the alias map from these snapshots.

    Migration 037 + 048: ``summary`` and ``word_count`` are populated by
    the orchestrator's loader. The writer_planner core invariant (planner
    LLM works from summaries only — see .claude/plans/writer_planner.md
    § Core invariant) consumes these fields and never reads ``content_md``
    directly. Default to empty string / 0 for forward-compat with callers
    that haven't migrated yet.
    """
    item_id: str
    kind: str
    title: str
    content_md: str
    summary: str = ""
    word_count: int = 0
    metadata: dict = Field(default_factory=dict)
    wi_seq: int | None = None


class MajorAgentInput(BaseModel):
    """Tier 2 input contract.

    Major agents NEVER read DB messages or workspace items directly — they
    receive only what the orchestrator passes here. This keeps specialists
    deterministic, testable in isolation, and immune to RLS/auth concerns.
    """
    describe_query: str = Field(
        description=(
            "The user's raw query text. The orchestrator fills this from the "
            "user's actual message — the router no longer paraphrases it. "
            "Conversation context comes from ``recent_messages``."
        ),
    )
    task_label: str = Field(
        description=(
            "Router-emitted short Arabic content-derived label (≤80 chars). "
            "Forwarded from DispatchAgent.task_label. Used as the workspace "
            "item title and agent_runs.task_label."
        ),
    )
    attached_items: list[WorkspaceItemSnapshot] = Field(
        default_factory=list,
        description="Router-selected workspace items, full content_md included.",
    )
    recent_messages: list[ChatMessageSnapshot] = Field(
        default_factory=list,
        description="Last N chat messages (default 3, per-agent overridable).",
    )
    compaction_summary_md: str | None = Field(
        default=None,
        description=(
            "content_md of the latest kind='convo_context' workspace item — the "
            "convo_compactor's summary of the turns that fell behind "
            "conversations.compacted_through_message_id. ``recent_messages`` is "
            "a fixed-size tail and is NOT filtered by that cutoff, so without "
            "this the specialists see no trace of a compacted conversation. "
            "وضع السرية: carried ALREADY ENCODED with the turn codec (the "
            "orchestrator encodes at load; masked-mode-off is byte passthrough)."
        ),
    )
    target_item_id: str | None = Field(
        default=None,
        description="Existing workspace item to edit/extend; None for fresh output.",
    )
    welcome_instruction: str | None = Field(
        default=None,
        description=(
            "رسائل الترحيب: the rendered prompt block telling whichever agent "
            "writes this turn's chat reply to open with the welcome line — the "
            "specialist, not the router, is the first responder whenever a new "
            "user's opening message is dispatched. Built once per turn by "
            "``agents.utils.welcome.render_welcome_instruction``; None on every "
            "turn that opens with no greeting, which is all but the first of a "
            "conversation."
        ),
    )
    user_id: str
    conversation_id: str
    case_id: str | None = None
    # ── Unified responder (plan unified_responder.md §4.3 / D4) ─────────────
    # The router's ``RouterContext`` for this turn, handed down VERBATIM from
    # ``_route`` (already encoded with the turn codec — never re-encode, never
    # reload). ``None`` on resume legs and pinned / headless dispatches, where
    # the responder falls back to its family's narrow context. Read only when
    # the family is on ``RESPONDER_UNIFIED_FAMILIES``. ``exclude=True`` +
    # ``repr=False``: it holds the whole message history and must never be
    # dumped into a span, a log line or a serialized payload.
    router_context: Any = Field(default=None, exclude=True, repr=False)
    # The turn's ``WelcomeState`` (``agents/utils/welcome.py``) — the state
    # behind ``welcome_instruction``. Lets a flagged family's failure fallback
    # open with the composed greeting (``compose_opening``) instead of burning
    # it. Same exclusion rules.
    welcome: Any = Field(default=None, exclude=True, repr=False)


# ── Next-step suggestion chips (plan: next_step_suggestions.md) ───────────

# "open" (open a document the turn considered but did not open) is
# simple_search only — deep_search references are already visible in the card.
# "answer" (plan: unified_responder.md §6.3 / D8) is the user's half-written
# reply to the turn's follow-up question, pasted into the composer. Emitted only
# by the unified responder (agents/responder/); it is exempt from the
# one-per-kind rule (up to MAX_ANSWER_STEPS) and every other caller rejects it
# (``allow_answer=False`` is the default).
NextStepKind = Literal["narrow_search", "draft", "apply", "open", "answer"]

MAX_NEXT_STEPS = 3
MAX_ANSWER_STEPS = 2
NEXT_STEP_LABEL_MAX = 40
NEXT_STEP_PROMPT_MAX = 200


class NextStep(BaseModel):
    """One clickable next-step chip. Clicking pastes ``prompt`` into the composer."""
    kind: NextStepKind = Field(
        description=(
            "narrow_search = a narrower search for a gap inside Rayhan's scope; "
            "draft = draft a legal document from the findings; "
            "apply = apply the finding to the user's own facts; "
            "open = open a document considered but not opened (simple_search only); "
            "answer = the user's half-written reply to the follow-up question you "
            "asked, first person, may end with «…» (only with a follow-up "
            "question; up to 2, the one kind that may repeat)."
        ),
    )
    label: str = Field(
        description=f"Arabic chip text, ≤ {NEXT_STEP_LABEL_MAX} chars, no trailing punctuation.",
    )
    prompt: str = Field(
        description=(
            f"Arabic message pasted into the composer, ≤ {NEXT_STEP_PROMPT_MAX} chars, "
            "written as the USER speaking («ابحث لي عن…», «اكتب لي…»)."
        ),
    )


def next_step_errors(
    steps: list[NextStep], *, allow_open: bool, allow_answer: bool = False
) -> list[str]:
    """Validation problems for a responder's ``next_steps`` (empty = valid).

    Used by the responders' output validators to raise ``ModelRetry`` once;
    :func:`salvage_next_steps` is the never-fail fallback after that.

    ``allow_answer`` (unified responder only — plan unified_responder.md §6.3):
    ``answer`` chips are exempt from the one-per-kind rule, capped at
    :data:`MAX_ANSWER_STEPS`; at most one non-answer action may sit beside them
    — the total stays ≤ :data:`MAX_NEXT_STEPS`. Whether a follow-up question
    exists is the caller's check (an answer chip without a question is
    meaningless), not this function's. With ``allow_answer=False`` (every
    pre-existing caller) an ``answer`` chip is an error and the rules for the
    other kinds are exactly as before.
    """
    errors: list[str] = []
    if len(steps) > MAX_NEXT_STEPS:
        errors.append(f"at most {MAX_NEXT_STEPS} next_steps (got {len(steps)})")
    answers = [s for s in steps if s.kind == "answer"]
    kinds = [s.kind for s in steps if s.kind != "answer"]
    if len(set(kinds)) != len(kinds):
        errors.append("each next_step must have a different kind")
    if answers:
        if not allow_answer:
            errors.append("kind 'answer' is not allowed here")
        else:
            if len(answers) > MAX_ANSWER_STEPS:
                errors.append(
                    f"at most {MAX_ANSWER_STEPS} 'answer' next_steps (got {len(answers)})"
                )
            if len(kinds) > 1:
                errors.append(
                    "with 'answer' chips, at most one other (action) next_step"
                )
    for s in steps:
        if s.kind == "open" and not allow_open:
            errors.append("kind 'open' is not allowed here — references are already visible")
        if not s.label.strip() or len(s.label) > NEXT_STEP_LABEL_MAX:
            errors.append(f"label must be 1–{NEXT_STEP_LABEL_MAX} chars: {s.label!r}")
        if not s.prompt.strip() or len(s.prompt) > NEXT_STEP_PROMPT_MAX:
            errors.append(f"prompt must be 1–{NEXT_STEP_PROMPT_MAX} chars")
    return errors


def salvage_next_steps(
    steps: list[NextStep], *, allow_open: bool, allow_answer: bool = False
) -> list[NextStep]:
    """Drop invalid / duplicate-kind chips and cap the count. Never raises.

    Chips are optional decoration — a bad chip must never fail the turn.
    With ``allow_answer`` up to :data:`MAX_ANSWER_STEPS` ``answer`` chips are
    kept (first wins) plus at most one action beside them; otherwise
    ``answer`` chips are dropped and behaviour is exactly as before.
    """
    kept: list[NextStep] = []
    seen: set[str] = set()
    n_answers = 0
    has_answers = allow_answer and any(
        getattr(s, "kind", None) == "answer" for s in steps or []
    )
    for s in steps or []:
        label, prompt = s.label.strip(), s.prompt.strip()
        if s.kind == "answer":
            if not allow_answer or n_answers >= MAX_ANSWER_STEPS:
                continue
        elif s.kind in seen or (s.kind == "open" and not allow_open):
            continue
        elif has_answers and seen - {"answer"}:
            # Beside answer chips only ONE action survives.
            continue
        if not label or not prompt:
            continue
        kept.append(
            NextStep(
                kind=s.kind,
                label=label[:NEXT_STEP_LABEL_MAX],
                prompt=prompt[:NEXT_STEP_PROMPT_MAX],
            )
        )
        if s.kind == "answer":
            n_answers += 1
        seen.add(s.kind)
        if len(kept) == MAX_NEXT_STEPS:
            break
    return kept


# ── Tier-2 output contract ────────────────────────────────────────────────

class SpecialistResult(BaseModel):
    """Standardized specialist return shape, consumed by orchestrator._dispatch.

    The full body_md is no longer streamed to chat — it lives in the workspace
    item identified by output_item_id. Chat receives chat_summary and
    key_findings only.
    """
    output_item_id: str | None = None
    chat_summary: str = Field(
        description="≤ 500 chars, streamed to chat as the assistant body.",
    )
    key_findings: list[str] = Field(
        default_factory=list,
        description="≤ 5 bullets, streamed after chat_summary.",
    )
    sse_events: list[dict] = Field(default_factory=list)
    model_used: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    per_phase_stats: dict = Field(default_factory=dict)
    # Clickable next-step chips — emitted as a ``next_steps`` SSE event after
    # the last token and persisted to messages.metadata.next_steps. Never
    # concatenated into chat_summary.
    next_steps: list[NextStep] = Field(default_factory=list)
    # Unified responder (plan unified_responder.md D6/§6.2): the follow-up
    # question, appended BY CODE as the bubble's last paragraph
    # (``agents.utils.history.compose_bubble``) — never inside chat_summary.
    # ``question_basis`` is internal (which gap it targets): logged, never shown.
    # Both None on every unflagged path.
    follow_up_question: str | None = None
    question_basis: str | None = None
