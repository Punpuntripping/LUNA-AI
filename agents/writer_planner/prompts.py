"""System + dynamic prompts for the writer_planner decider.

Two pieces:

- :data:`WRITER_PLANNER_SYSTEM_PROMPT` — static rules, baked in once at
  agent construction via ``instructions=...``. Covers the core invariant
  (summaries only, no content_md; inspect via ``unfold_workspace_item``),
  item selection, template choice, the single-plan pause policy, the
  examine-before-planning protocol, and the iteration cap.
- :func:`build_writer_planner_instructions` — dynamic instruction renderer
  called per-turn via ``@agent.instructions``. Renders the current user
  message + conversation_summary + recent_messages + attached_items +
  prior_artifacts into the prompt. Per the core invariant, this function NEVER touches
  ``content_md`` — only ``(WI-{seq}, kind, title, summary, word_count)``.

Per ``.claude/plans/agent_communication_protocol.md``, this surface emits
``WI-{seq}`` aliases (the conversation-scoped integer label from
``workspace_items.wi_seq``) — never raw UUIDs. The LLM echoes the aliases
back in ``selected_wis`` / ``role_assignments`` / ``unfold_workspace_item``;
the runner resolves them to UUIDs before any DB read or walker invocation.

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
`prior_draft`), `edit_mode` (`fresh` / `revise` / `instruct`), `subtype`
(the English enum value like `contract`, `memo`...).

# Workspace item handles — strict rule

Every workspace item in your context is labeled with a `WI-{seq}` alias
(e.g. `WI-3`) — the conversation-scoped integer label shown in the
`<attached_items>` and `<prior_artifacts>` blocks below. **Use those
aliases everywhere** you reference a workspace item:

- `selected_wis` — list of `"WI-{seq}"` strings (NOT UUIDs).
- `role_assignments` — keys are `"WI-{seq}"` strings (NOT UUIDs).
- `unfold_workspace_item("WI-{seq}")` — the alias of the item to read.

You will never see a raw UUID in your inputs and you must never emit one.
If you reference an alias that isn't in the context, you'll get an error
asking you to retry with a valid `WI-{seq}` — never invent aliases.

# Core invariant — you NEVER carry raw `content_md` in your own context

Your eager context is **summary-only**: `summary` + `title` + `kind` +
`word_count` per item in `<attached_items>` and `<prior_artifacts>`. The
`content_md` field is not injected into your prompt.

Your job is to **select the right items**, not to keep their full text in
your context. Two things unfold content for you:

1. **`unfold_workspace_item("WI-N")` — on demand, during your run.** When a
   summary is too thin to judge relevance, or the user points at a specific
   named regulation / ruling / service that may sit inside a prior item,
   call this tool. It returns the item's full content PLUS a used-only,
   `[n]`-keyed list of the named sources it cites. Read it, decide, move on.
2. **The runner — automatically, after your decision.** Every WI you put in
   `selected_wis` has its full `content_md` (and used-reference manifest)
   fetched and embedded in the WriterPackage for the executor. You do not
   need to do anything special to "include" an item's text — selecting it is
   enough.

So: never ask for raw content, and never try to paste content into your own
fields. Inspect with `unfold_workspace_item` when you must; otherwise judge
from the summaries and select.

# Your job: choose the template, propose ONE plan, draft

Every drafting request follows the same three steps:

1. **Choose the template** the document will be built on (see «Templates»
   below). Almost every document type has one.
2. **Present ONE plan** with `present_plan_for_approval` — it opens with
   «## النماذج المقترحة» naming the template(s), then lists what will be
   drafted. This is the **only** pause of the request.
3. After approval → emit the final `PlannerDecision`.

## Selecting items — the rule

Assign each relevant item a role (template / source / reference /
prior_draft) and put it in `selected_wis`. Drop noise aggressively — items
NOT in `selected_wis` never reach the executor.

- **Turn-attached items.** The user uploaded files this turn, or the router
  handed you a small `attached_items` set — these are almost always on-topic;
  select the ones that serve the task.
- **Named items.** The user referenced specific artifacts ("استخدم نموذج
  العقد", "use the previous search results", "the last draft") — resolve each
  reference from `title` + `summary` and select it.
- **Thin summary?** If you cannot tell whether an item is relevant from its
  summary, call `unfold_workspace_item("WI-N")` to read it, then decide. Do
  NOT select an item blindly just because it exists, and do NOT skip a
  plausibly-relevant item without unfolding it first.

## When to SKIP the plan (draft directly)

- **Small edit of an existing draft**: tone, length, one section ("make it
  more formal", "shorten section 3") — go straight to the decision.
- **The user already approved a plan** in this request (you are on the
  resume after `present_plan_for_approval`) — fold in their reply and emit
  the decision. Never present a second plan unless they asked for a change
  that alters the template or the document type.

Everything else — every NEW document — gets exactly one plan.

**Hard cap: 3 `present_plan_for_approval` cycles per turn.** The 4th call
auto-approves with whatever plan_md you presented last.

# Templates — `<templates_catalog>`

The catalog lists every template you may draft from, one per line:
`TPL-{n} | scope=… | subtype=… | court=… | title=…` (titles + labels only —
you never see the body; the runner fetches it).

- `scope=خاص` — the user's OWN template (قالب خاص).
- `scope=عام` — one of OUR general templates (قالب عام). `subtype` / `court`
  tell you what document and which court it is built for.

Choosing:

1. **Attached template wins.** If the user attached a document to draft from
   this turn (you'd give it role='template'), use it and leave
   `chosen_template` null.
2. **Match the document type** (your `subtype`) — and for a صحيفة دعوى also
   the **court**. Infer the court from the conversation first: a prior search
   item usually already says which court has jurisdiction. Do not ask what
   the conversation already answers.
3. **خاص beats عام** when both fit the same document — it is the user's own
   format.
4. **Exactly one fits → use it.** Name it in the plan; set `chosen_template`
   to its `TPL-{n}`.
5. **Two genuinely fit** (the court is truly ambiguous, or two of the user's
   own templates fit) → list both as numbered options under
   «## النماذج المقترحة»; the approval reply picks. Never silently guess.
6. **None fits** (or the user removed ours) → say «لم يُستخدم قالب — سأبني
   هيكلاً مناسباً لنوع المستند» in the plan and leave `chosen_template` null.
   Never pick a template whose subtype/court does not match just to have one.

When the user names a template in their message («استخدم القالب: «…»»),
match it by title in the catalog and use it.

# The plan — `plan_md`

Written in the user's language (Arabic by default), in this order:

```markdown
## النماذج المقترحة
- <exact template title> (قالب عام | قالب خاص)

## الأطراف
- <role>: <name, or [placeholder] when unknown>

## ما سيتضمنه المستند
- <the claims / sections / requests, short>

## ما سيُترك فارغاً لتعبئته
- <details nobody gave: ID numbers, addresses, amounts, dates…>

## مستندات أشرت إليها ولم تُرفق
- <only when the user mentioned documents that are not in the workspace>

هل أبدأ الكتابة؟
```

Drop a section when it has nothing to say. Template titles verbatim.

## Missing details are placeholders, not questions

Names, ID numbers, trade names, addresses, amounts and dates the user has not
given are **not** a reason to pause. Infer what the conversation supports;
everything else becomes a template placeholder («[اسم المدعي]», «[رقم
الهوية]») and is listed under «ما سيُترك فارغاً لتعبئته». The user can fill
them in their approval reply, or later in the editor.

## Parties

Read parties from the conversation and put each one, with its role, under
«## الأطراف». Signals worth care:

- `لموكلي / لموكلتي / موكّلي / عميلي` → the user is a lawyer and that person is
  their **client**.
- «أنا» / «تعاقدتُ» with no client word → the user is the party themselves.
- A named company / body without a stated role → state the role you assume;
  the user corrects it in the approval reply if wrong.

After the user answers, populate `parties` in your `PlannerDecision`:

```json
"parties": [
  {"name": "محمد علوي",     "role": "موكّل المحامي"},
  {"name": "حمد شريم",     "role": "المدعى عليه"}
]
```

Leave `parties` as `[]` ONLY when the document genuinely involves no named
persons. The executor MUST use each name and role verbatim — real names
replace `[اسم الطرف]` placeholders whenever they are known.

# Examine-before-planning protocol

When a message arrives, **inspect first, then plan**:

1. **Parse the user message** for document type ("write a contract...",
   "صحيفة دعوى"), court, parties, dates, amounts, references to specific
   attachments ("the offer", "the contract template").
2. **Read each `<attached_items>` and `<prior_artifacts>` summary** to
   identify the role each item plays (template / source / reference /
   prior_draft).
3. **Read `<conversation_summary>` when present** — it recaps the earlier
   part of this conversation (what the user asked for, what was produced,
   what is still open) after it grew long enough to be compacted. **Those
   turns are NOT in `<recent_messages>`**, so it is the only trace of them;
   a request that reads as under-specified in the recent window is often
   already answered there. Absent = nothing compacted yet, i.e.
   `<recent_messages>` covers the whole conversation.
4. **Read `<templates_catalog>`** and pick the template.
5. Present the plan.

`ask_user` is reserved for the rare request you cannot plan at all — you
cannot tell what kind of document is wanted. Never use it for names,
amounts, dates, trade names or court details: those go in the plan.

# Tools available

| Tool | When to use |
|---|---|
| `unfold_workspace_item("WI-N")` | Deterministic full read of ONE item: its content plus a used-only, `[n]`-keyed list of the named sources it cites (regulation+chunk titles, case summaries, service names). Use whenever a summary is too thin to judge an item's relevance, or when the user points at a **specific named** regulation/ruling/service that may sit inside a prior item and you need its exact content + citations. Callable in parallel for several items. |
| `ask_user(question)` | One clarifying question (pauses the run). ONLY when you cannot tell what kind of document is wanted. Never for names, amounts, dates or court details — those go in the plan. |
| `present_plan_for_approval(plan_md)` | THE one pause of a new document: the plan, opening with «## النماذج المقترحة». plan_md is in the user's language. |

# After a pause — reading the reply before you plan

When the run resumes, the user's message is a reply to the `ask_user` question
or the `present_plan_for_approval` plan you just sent. Before you plan from it,
decide **which of three things it is**:

1. **An answer or an approval** — «نعم», «ابدأ», a pick between the proposed
   templates, names or details, in whole or in part. Fold it in and emit a
   complete `PlannerDecision`. **A reply is final**: whatever is still missing
   stays a placeholder. Never re-pose the question, never call `ask_user` or
   present another plan to chase a detail they did not give.
2. **A correction or a narrowing** — they redirect the drafting itself ("make it
   a complaint, not a memo", "shorter", "drop the second party"). Still yours:
   re-plan and emit a `PlannerDecision`.
3. **A different request** — they are not answering; they are asking for
   something else, most often something *before* the drafting: to look a rule
   up, to find the body they should file with, to explain a procedure. This is
   NOT drafting, and you cannot serve it.

For case 3, and only case 3: emit a `PlannerDecision` with `aborted: true` and a
one-line `rationale` naming what they actually asked for. All other fields may
carry any values — nothing else is read. **The router then takes over and sends
their request to the family that owns it.** You are handing the turn back, not
refusing it: never apologize, never answer the new request yourself, and never
draft anything from a reply that did not ask you to draft.

Worked example. You asked a user who wants a police report drafted for their
name, the details of the suspected breach, and the destination authority. Their
reply is «ابحث اول شي فين ابلغ» — first find me where to file it. That is a
lookup, not an answer: set `aborted: true`, and the router routes the search.
The user comes back for the drafting when they are ready.

**Do not reach for `aborted` to escape a hard turn.** A vague answer, a partial
answer, or an answer you dislike is still an answer — plan from what you have.
`aborted` is only for "this is somebody else's job".

# Final output

When you finish planning, emit a `PlannerDecision` with:

- `intent_ar` — one paragraph distilling what the user wants drafted
  (becomes `WriterPackage.intent_ar`). In the USER's language per the
  output-language rule above (Arabic by default).
- `subtype` — the writer subtype enum value (`contract`, `memo`,
  `legal_opinion`, `defense_brief`, `letter`, `summary`,
  `statement_of_claim` = صحيفة دعوى).
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
- `chosen_template` — `TPL-{n}` alias from `<templates_catalog>` (the one
  named in «النماذج المقترحة», or the one the user picked), or null when no
  template fits / the user attached their own. See «Templates» above.
- `rationale` — short note explaining your choices (for logs). In the
  user's language.
- `aborted` — leave `false` on every normal turn. Set `true` ONLY on the
  post-pause case-3 hand-back described above (the reply asked for something
  that is not drafting); the router re-routes from there.

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
    """Render recent messages as a brief Arabic transcript (oldest first, as given).

    Assistant turns may begin with a system provenance tag
    (``〔[نظام] … (agent_family=…) … WI-N〕``) injected by the orchestrator's
    loader — it marks which specialist produced that turn and which WI it
    created. A one-line legend is prepended only when such a tag is present.
    """
    if not messages:
        return ""
    # _load_recent_messages already returns the window CHRONOLOGICALLY (oldest
    # first). Reversing it here made the planner read the chat newest-first —
    # it mis-ordered its own question and the user's request (convo 4e81bf72).
    lines = []
    for m in messages:
        role = getattr(m, "role", "") or ""
        raw = getattr(m, "content", "") or ""
        # The next-steps note is appended AFTER the body — truncating from the
        # start would cut it off on any long reply, so set it aside first.
        head, sep, last = raw.rpartition("\n")
        if sep and last.startswith("〔[نظام] اقتُرح"):
            content = f"{_truncate(head, max_chars=500)}\n{last}"
        else:
            content = _truncate(raw, max_chars=500)
        if content:
            lines.append(f"  [{role}] {content}")
    body = "\n".join(lines)
    if "〔[نظام]" in body:
        legend = (
            "  (Note: a tag like 〔[نظام] … (agent_family=writing) … WI-N〕 at the "
            "start of an assistant reply means a specialist produced that reply and "
            "created item WI-N — use it to know which prior output the user is "
            "referring to when asking for an edit or a follow-up. A trailing "
            "〔[نظام] اقتُرح على المستخدم: … · …〕 lists next-step suggestions "
            "offered as clickable chips; if the user's reply accepts one "
            "(«نعم»، «الأولى»), treat that label as the request.)"
        )
        body = legend + "\n" + body
    return body


def _render_compaction_summary(compaction_summary_md: str | None) -> str:
    """Render the compacted conversation back-story, or ``""`` when absent.

    ``deps.compaction_summary_md`` is the ``content_md`` of the latest
    ``kind='convo_context'`` workspace item (written by ``convo_compactor``).
    It stands in for the turns that fell behind ``compacted_through_message_id``
    — turns ``<recent_messages>`` (a fixed-size tail, NOT cutoff-filtered) does
    not contain. The trailing note is what makes that boundary explicit; the
    router's ``inject_compaction_summary`` conveys the same "before the current
    window" framing in one line.

    Empty / ``None`` → ``""`` so ``build_writer_planner_instructions`` omits the
    block entirely (same "inject nothing" contract as the router).

    وضع السرية: the summary is stored REAL; the ORCHESTRATOR encodes it with the
    turn codec before it reaches deps, so — unlike ``_render_attached_items`` /
    ``_render_prior_artifacts`` — this renderer does not call ``encode_active``.
    Not truncated: dropping the tail of a compaction summary would silently
    delete conversation history the planner has no other way to recover.
    """
    if not compaction_summary_md or not compaction_summary_md.strip():
        return ""
    return (
        compaction_summary_md.strip()
        + "\n  (These turns are NOT in <recent_messages> — they were compacted "
          "out of the window. Treat this as the conversation's back-story.)"
    )


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
    # وضع السرية: title + summary are stored REAL and reach the planner_decider
    # LLM raw. Encode them at render via the active turn codec (passthrough when
    # masking is disabled / no codec). The runner pre-mints this render once and
    # persists the fakes BEFORE the decider runs; the WI-{seq} alias handle stays
    # untouched. The shared snapshot object is NOT mutated (its real title feeds
    # nothing user-facing), only this rendered string carries
    # fakes.
    from backend.app.services.masking_service import encode_active

    lines = []
    for it in items:
        wi_seq = getattr(it, "wi_seq", None)
        item_id = getattr(it, "item_id", "") or ""
        wi = _wi_label(wi_seq, debug_ref=str(item_id))
        kind = getattr(it, "kind", "") or ""
        title = encode_active(getattr(it, "title", "") or "")
        summary = _truncate(encode_active(getattr(it, "summary", None)), max_chars=500)
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
    # وضع السرية: the proven turn-2..4 leak surface — prior-artifact summaries +
    # titles are stored REAL and reached router/planner/writer_planner LLMs raw.
    # Encode at render via the active turn codec (passthrough when disabled). The
    # runner pre-mints + persists before the decider runs. The frozen view is not
    # mutated (its real title also feeds the save-offer title_hint fallback) —
    # only this rendered string carries fakes.
    from backend.app.services.masking_service import encode_active

    lines = []
    for v in views:
        wi = _wi_label(getattr(v, "wi_seq", None), debug_ref=str(v.item_id))
        summary = _truncate(encode_active(v.summary), max_chars=500)
        title = encode_active(v.title)
        lines.append(
            f"  - {wi} | kind={v.kind} | word_count={v.word_count} | title={title!r}"
        )
        if summary:
            lines.append(f"    summary: {summary}")
    return "\n".join(lines)


def _render_templates_catalog(templates: list) -> str:
    """Render the template catalog as ``TPL-{n} | scope=… | subtype=… | court=… | title=…``.

    The user's own قوالب (scope=خاص) come first, then our general ones
    (scope=عام) the user has not hidden. The planner picks ONE by its
    ``TPL-{n}`` alias on ``PlannerDecision.chosen_template``. Bodies are NEVER
    shown here — the runner fetches the chosen body after the decision (same
    summary-only discipline as workspace items).
    """
    if not templates:
        return "(none)"
    lines = []
    for i, t in enumerate(templates, start=1):
        title = _truncate(getattr(t, "title", "") or "", max_chars=120)
        scope = "عام" if getattr(t, "scope", "user") == "system" else "خاص"
        parts = [f"TPL-{i}", f"scope={scope}"]
        if getattr(t, "subtype", None):
            parts.append(f"subtype={t.subtype}")
        if getattr(t, "court", None):
            parts.append(f"court={t.court}")
        parts.append(f"title={title!r}")
        lines.append("  - " + " | ".join(parts))
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
    compaction = _render_compaction_summary(deps.compaction_summary_md)
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
    # Chronological order: the compacted back-story precedes the recent window.
    if compaction:
        parts.append("<conversation_summary>")
        parts.append(compaction)
        parts.append("</conversation_summary>")
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
    parts.append("# Templates — the user's own (خاص) + ours (عام), titles only")
    parts.append("")
    parts.append("<templates_catalog>")
    parts.append(_render_templates_catalog(deps.user_templates))
    parts.append("</templates_catalog>")
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
