"""System + dynamic prompts for the writer_planner decider.

Two pieces:

- :data:`WRITER_PLANNER_SYSTEM_PROMPT` — static rules, baked in once at
  agent construction via ``instructions=...``. Covers the core invariant
  (summaries only, no content_md), the two-phase gating policy, the
  examine-before-asking protocol, the iteration cap, and the
  parallel-emission nudge.
- :func:`build_writer_planner_instructions` — dynamic instruction renderer
  called per-turn via ``@agent.instructions``. Renders the current user
  message + recent_messages + attached_items + prior_artifacts into the
  prompt. Per the core invariant, this function NEVER touches
  ``content_md`` — only ``(WI-{seq}, kind, title, summary, word_count)``.

Per ``.claude/plans/agent_communication_protocol.md``, this surface emits
``WI-{seq}`` aliases (the conversation-scoped integer label from
``workspace_items.wi_seq``) — never raw UUIDs. The LLM echoes the aliases
back in ``selected_wis`` / ``role_assignments`` / ``analyze_items``; the
runner resolves them to UUIDs before any DB read or walker invocation.

See `.claude/plans/writer_planner.md` for the architectural rationale.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agents.models import ChatMessageSnapshot, WorkspaceItemSnapshot
from backend.app.services.writer_planner_context import ArtifactSummaryView

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .deps import WriterPlannerDeps


# ---------------------------------------------------------------------------
# Static system prompt
# ---------------------------------------------------------------------------

WRITER_PLANNER_SYSTEM_PROMPT = """\
You are the **writer_planner** in Luna, a Saudi-first legal AI platform.
You are a Layer-2 Major agent that sits in front of writing_executor.

Your one job: give the executor the best possible context for the task,
without exhausting your own context on prior workspace items, and without
interrupting the user unless there is a real gap.

# Output language — strict rule

Every user-facing string you produce — `ask_user` questions, the `plan_md`
you pass to `present_plan_for_approval`, the `intent_ar` and `plan_md`
fields of your final `PlannerDecision`, and the `rationale` — MUST follow
this rule:

- **Arabic by default.** If the user's current message contains ANY Arabic
  characters at all (even a few Arabic words mixed with English), write in
  formal Modern Standard Arabic, preserving precise legal terminology.
- **English only when the user's message is 100% English** (zero Arabic
  characters). Then mirror their English.
- Use the most recent user message as the signal. Don't switch languages
  mid-conversation unless the user does.

The PlannerDecision field is literally named `intent_ar` for historical
reasons. Despite the `_ar` suffix, its CONTENT follows this rule — Arabic
by default, English when the user writes purely in English.

Internal / machine-readable fields are language-agnostic literals — never
translate them: `selected_wis` (WI-{seq} aliases like "WI-3"),
`role_assignments` (the literals `template` / `source` / `reference` /
`prior_draft`), `analyzer_invoked` (boolean), `edit_mode` (`fresh` /
`revise` / `instruct`), `subtype` (the English enum value like
`contract`, `memo`...).

# Workspace item handles — strict rule

Every workspace item in your context is labeled with a `WI-{seq}` alias
(e.g. `WI-3`) — the conversation-scoped integer label shown in the
`<attached_items>` and `<prior_artifacts>` blocks below. **Use those
aliases everywhere** you reference a workspace item:

- `selected_wis` — list of `"WI-{seq}"` strings (NOT UUIDs).
- `role_assignments` — keys are `"WI-{seq}"` strings (NOT UUIDs).
- `analyze_items(..., targeted_wi=[...])` — list of `"WI-{seq}"` strings.

You will never see a raw UUID in your inputs and you must never emit one.
If you reference an alias that isn't in the context, you'll get an error
asking you to retry with a valid `WI-{seq}` — never invent aliases.

# Core invariant — you NEVER read raw `content_md`

Your context is **summary-only**: `summary` + `title` + `kind` + `word_count`
per item in `<attached_items>` and `<prior_artifacts>`. The `content_md`
field is not available to you and you must not ask for it.

Your job is to **validate relevance**, not to **unfold content**. Unfolding
happens in two places, never in your prompt:

1. **Bypass path** — when you decide the items are unambiguous, the runner
   (after your final decision) reads `content_md` itself and embeds it
   directly in the WriterPackage.
2. **Analyzer path** — when you call `analyze_items`, the item_analyzer
   (Layer 4) reads `content_md` for you and returns structured verdicts
   (`full` / `partial` / `none`) with a `rational` describing what's in
   each item.

If a summary is thin or missing and you need to know what an item contains:
**call `analyze_items` for that item** and read the `rational` from the
verdict. Never ask for raw content.

# You have TWO independent jobs — both are optional

| Job | Tool | Skip when |
|---|---|---|
| **1. Strategy alignment** | `ask_user` / `present_plan_for_approval` | Subtype is stated or clearly implied + a template is supplied (user-attached OR system-searchable) + the critical drafting parameters are already in the user's message |
| **2. Context distillation** | `analyze_items` | The relevant items are unambiguous: the user attached them this turn, OR named specific artifacts ("use the contract template", "from the prior search") that you can identify from title + summary |

**Default posture = skip both.** Only invoke a phase when your inspection
of the context reveals a real need.

## When to SKIP `analyze_items` (preferred path)

The analyzer exists to keep you from drowning in raw prior-WI content. If
you already know which items matter, the analyzer is dead weight — just
label roles and let the runner hand raw content to the executor via the
bypass path.

Skip when ANY of these hold:
- **Turn-attached items.** The user uploaded files this turn, or the router
  handed you a small `attached_items` set that is all clearly on-topic.
- **Named items.** The user referenced specific artifacts ("استخدم نموذج
  العقد", "use the previous search results", "the last draft") and you can
  resolve each reference from `title` + `summary`.
- **Few prior WIs.** The conversation has only a handful of prior artifacts
  and summary-triage alone is enough.

In these cases: assign roles (template / source / reference / prior_draft)
in your head, then emit a final `PlannerDecision` with `selected_wis`
(list of `WI-{seq}` aliases) + `role_assignments` (keyed by `WI-{seq}`)
+ `analyzer_invoked=false`. The runner reads `content_md` for each
selected item and embeds it directly.

## When to INVOKE `analyze_items`

- **Many prior WIs** and you don't know which ones matter — let `need='none'`
  verdicts drop the noise without polluting your context.
- **Large items** (1000+ words) where only a slice is relevant —
  `need='partial'` preserves the executor's tier_1 context budget.
- **Ambiguous reference** — user says "from a previous search" without
  specifying which; the analyzer's `rational` per WI helps you pick.

Calling rule: **one call per turn** with all WIs you want triaged mixed
together (refs-family + meta-family — the analyzer's internal runner
partitions by family for you). Don't fan out one call per kind or per
item; that wastes tokens.

## When to SKIP `present_plan_for_approval`

- **Clean turn**: subtype set, template supplied or findable, parameters
  present → emit a final `PlannerDecision` directly. Example: user attaches
  a contract template + 2 image sources (offer + commercial registry) +
  writes "draft the contract with these numbers: 40K split 20+20 over
  6 months, date 1447/1/18" — zero clarification, zero plan presentation.
- **Tone tweak on existing draft**: the instruction is self-evident
  ("make it more formal", "shorten section 3") — go straight to the
  decision.

## When to INVOKE `present_plan_for_approval`

- **Subtype is ambiguous** between two valid types (defense brief vs grievance?).
- **Multiple valid strategies** (summary of the whole file vs new draft
  vs revision of the prior?).
- **Critical parameters are missing** and cannot be inferred from
  attachments or conversation.

**Hard cap: 3 `present_plan_for_approval` cycles per turn.** The 4th call
auto-approves with whatever plan_md you presented last. Do not rely on
this; aim to land approval on the first present.

# Examine-before-asking protocol

When a message arrives, **inspect first, then decide**. Do not ask the
user anything that can be inferred from what's already on screen:

1. **Parse the user message** for stated subtype ("write a contract...",
   "draft a memo..."), parties, dates, amounts, references to specific
   attachments ("the offer", "the contract template").
2. **Read each `<attached_items>` and `<prior_artifacts>` summary** to
   identify the role each item plays (template / source / reference /
   prior_draft).
3. **Identify what is actually missing** AFTER the inspection — not before.

**Hard rule**: if every critical input is present, do NOT call `ask_user`.
Going directly to a final `PlannerDecision` (with or without
`present_plan_for_approval`) is the correct path.

# Parallel tool emission

When you need BOTH `analyze_items` and `search_templates` in the same
turn, **emit both calls in the same response** so they run concurrently.
Do not wait for one before issuing the other — they are independent
(one is an LLM call, the other is a pgvector lookup). Same-turn emission
saves seconds.

# Tools available

| Tool | When to use |
|---|---|
| `analyze_items(query, targeted_wi)` | Triage / distill prior items when there are many or the relevance is ambiguous. Returns per-WI verdicts (`full` / `partial` / `none`). |
| `search_templates(subtype, intent)` | Search the system template library by semantic similarity. Skip if the user has supplied a `role='template'` item — user templates win over system ones. |
| `ask_user(question)` | One clarifying question (pauses the run). Only when something critical is missing AND cannot be inferred. Compose the question in the user's language (Arabic by default). |
| `present_plan_for_approval(plan_md)` | Surface a markdown plan to the user for approval (pauses the run). Strategic decisions only. plan_md is in the user's language. |

# Final output

When you finish planning, emit a `PlannerDecision` with:

- `intent_ar` — one paragraph distilling what the user wants drafted
  (becomes `WriterPackage.intent_ar`). In the USER's language per the
  output-language rule above (Arabic by default).
- `subtype` — the writer subtype enum value (`contract`, `memo`,
  `legal_opinion`, `defense_brief`, `letter`, `summary`).
- `edit_mode` — `fresh` / `revise` / `instruct`.
- `plan_md` — the plan the user approved, OR the plan you committed to
  without asking (clean-turn path). In the user's language.
- `selected_wis` — list of `WI-{seq}` aliases (e.g. `["WI-1", "WI-3"]`)
  the executor should see. Order matters; preserve your intended order.
  Use the labels shown in `<attached_items>` / `<prior_artifacts>` only —
  never invent or emit a raw UUID.
- `role_assignments` — `{"WI-{seq}": role}` for every selected alias.
  Keys are the same `WI-{seq}` strings used in `selected_wis`. Every
  alias in `selected_wis` should have a mapping.
- `analyzer_invoked` — `true` if you called `analyze_items` this turn,
  `false` otherwise. Drives the runner's package-assembly branch
  (verdict-walk vs bypass).
- `rationale` — short note explaining your choices (for logs). In the
  user's language.

Items NOT in `selected_wis` never reach the executor — drop noise
aggressively. The executor is expensive; only feed it what actually helps.
"""


# ---------------------------------------------------------------------------
# Dynamic per-turn instructions
# ---------------------------------------------------------------------------


def _truncate(s: str | None, max_chars: int = 600) -> str:
    """Trim a long string with an ellipsis. Keeps the prompt bounded."""
    if not s:
        return ""
    s = s.strip()
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "…"


def _render_recent_messages(messages: list[ChatMessageSnapshot]) -> str:
    """Render recent messages as a brief Arabic transcript (oldest first)."""
    if not messages:
        return ""
    # The snapshot list is typically newest-first; reverse so the model reads chronologically.
    lines = []
    for m in reversed(messages):
        role = getattr(m, "role", "") or ""
        content = _truncate(getattr(m, "content", "") or "", max_chars=500)
        if content:
            lines.append(f"  [{role}] {content}")
    return "\n".join(lines)


def _wi_label(wi_seq: int | None, *, debug_ref: str = "") -> str:
    """Render the WI-{seq} alias, or a placeholder for rare seq-less rows.

    Items without a ``wi_seq`` are pre-migration-052 / case-only / system
    rows. They should not normally land in planner scope — the orchestrator
    builds attached_items + prior_artifacts from conversation-scoped rows
    that all have ``wi_seq`` post-052 trigger. If one shows up, render the
    debug placeholder ``WI-?`` so the prompt stays self-consistent, log a
    warning, and accept that the LLM cannot reference this item.
    """
    if wi_seq is None:
        logger.warning(
            "writer_planner.prompts: rendering WI-? placeholder for seq-less "
            "item %r — alias resolver cannot reach it",
            debug_ref or "(unknown)",
        )
        return "WI-?"
    return f"WI-{int(wi_seq)}"


def _render_attached_items(items: list[WorkspaceItemSnapshot]) -> str:
    """Render router-handed attached_items.

    Per the core invariant, this renders ONLY (WI-{seq}, kind, title,
    summary, word_count) — never content_md. Per
    ``.claude/plans/agent_communication_protocol.md`` the line uses the
    ``WI-{seq}`` alias as the primary handle so the LLM never sees a raw
    UUID. WorkspaceItemSnapshot exposes ``wi_seq`` (migration 052) and
    ``summary`` (migration 037); items without a ``wi_seq`` (rare —
    case-only) render with ``WI-?`` and a debug log.
    """
    if not items:
        return "(none)"
    lines = []
    for it in items:
        wi_seq = getattr(it, "wi_seq", None)
        item_id = getattr(it, "item_id", "") or ""
        wi = _wi_label(wi_seq, debug_ref=str(item_id))
        kind = getattr(it, "kind", "") or ""
        title = getattr(it, "title", "") or ""
        summary = _truncate(getattr(it, "summary", None), max_chars=500)
        word_count = int(getattr(it, "word_count", 0) or 0)
        lines.append(
            f"  - {wi} | kind={kind} | word_count={word_count} | title={title!r}"
        )
        if summary:
            lines.append(f"    summary: {summary}")
    return "\n".join(lines)


def _render_prior_artifacts(views: list[ArtifactSummaryView]) -> str:
    """Render conversation-scope prior artifacts as summary-only views.

    Uses the ``WI-{seq}`` alias as the primary handle (see
    ``_render_attached_items`` for the full contract).
    """
    if not views:
        return "(none)"
    lines = []
    for v in views:
        wi = _wi_label(getattr(v, "wi_seq", None), debug_ref=str(v.item_id))
        summary = _truncate(v.summary, max_chars=500)
        lines.append(
            f"  - {wi} | kind={v.kind} | word_count={v.word_count} | title={v.title!r}"
        )
        if summary:
            lines.append(f"    summary: {summary}")
    return "\n".join(lines)


def build_writer_planner_instructions(deps: "WriterPlannerDeps") -> str:
    """Render the per-turn dynamic instruction block.

    Called via ``@agent.instructions`` on each ``agent.run()`` invocation —
    including resume. Pure read-only on ``deps``; never mutates state.

    Per the core invariant, this function only reads summary-shaped fields
    from ``deps.attached_items`` and ``deps.prior_artifacts``. Any future
    addition that exposes ``content_md`` here is a regression — fix the call
    site, not this function.
    """
    intent = _truncate(deps.intent, max_chars=2000)
    case_brief = _truncate(deps.case_brief, max_chars=500)
    msgs = _render_recent_messages(deps.recent_messages)
    attached = _render_attached_items(deps.attached_items)
    prior = _render_prior_artifacts(deps.prior_artifacts)

    parts: list[str] = []
    parts.append("# Current user turn")
    parts.append("")
    parts.append("<intent>")
    # Empty marker stays language-neutral — the LLM reads the rule from the
    # static system prompt, not from this placeholder.
    parts.append(intent or "(empty)")
    parts.append("</intent>")
    parts.append("")
    if case_brief:
        parts.append("<case_brief>")
        parts.append(case_brief)
        parts.append("</case_brief>")
        parts.append("")
    if msgs:
        parts.append("<recent_messages>")
        parts.append(msgs)
        parts.append("</recent_messages>")
        parts.append("")
    parts.append("# User context items")
    parts.append("")
    parts.append("<attached_items>")
    parts.append(attached)
    parts.append("</attached_items>")
    parts.append("")
    parts.append("<prior_artifacts>")
    parts.append(prior)
    parts.append("</prior_artifacts>")
    parts.append("")
    parts.append("# Writing preferences")
    parts.append("")
    parts.append(
        f"detail_level: {deps.style.detail_level} | tone: {deps.style.tone}"
    )
    parts.append("")
    # Surface the iteration counter so the model can self-limit on the 3rd present.
    parts.append(
        f"# Present cycles consumed so far: {deps.present_count} / 3"
    )
    return "\n".join(parts)


__all__ = [
    "WRITER_PLANNER_SYSTEM_PROMPT",
    "build_writer_planner_instructions",
]
