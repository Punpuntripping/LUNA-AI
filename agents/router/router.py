"""Router agent — conversational front-end for Rayhan Legal AI.

Classifies user intent and either responds directly (ChatResponse) or
dispatches a specialist agent (DispatchAgent) with a content-derived
task_label plus the workspace items the specialist should see as input.
The router no longer paraphrases the query — the specialist receives the
raw user message (orchestrator-filled MajorAgentInput.describe_query) and
recent_messages for context.

Wave 9 changes:
- ``OpenTask`` → ``DispatchAgent`` (renamed fields ``task_type`` →
  ``agent_family``, ``artifact_id`` → ``target_item_id``, plus
  ``attached_item_ids`` capped at ``MAX_ATTACHED_ITEMS``).
- ``output_type`` uses Pydantic AI list syntax for per-member output tools.
- ``unfold_workspace_item`` tool exposes full ``content_md`` plus a used-only
  ``[n]``-keyed manifest of the item's cited sources on demand (replaces the
  former ``read_workspace_item``).
- Eager context (workspace items summaries + compaction summary + filtered
  messages) is assembled by ``agents.router.context.load_router_context``
  and rendered as dynamic instructions on the agent.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from pydantic_ai import Agent, ModelRetry, RunContext, TextOutput
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits
from supabase import Client as SupabaseClient

from agents.models import ChatResponse, DispatchAgent, MAX_ATTACHED_ITEMS
from agents.tool_repository.edit_artifact import register_edit_artifact
from agents.tool_repository.save_memo import register_save_memo
from agents.tool_repository.unfold_workspace_item import register_unfold_workspace_item
from agents.utils.agent_models import get_agent_model
from agents.utils.tracking import track_stage
from shared.observability import get_logfire


# ── Alias resolution (migration 052 / agent communication protocol) ───────────
# The router LLM emits ``WI-{seq}`` aliases (e.g. ``"WI-3"``) instead of raw
# UUIDs. The output validator resolves them against ``RouterDeps.wi_alias_map``
# and fills the orchestrator-facing ``target_item_id`` / ``attached_item_ids``
# fields. The unfold_workspace_item tool accepts either form for robustness.

_WI_ALIAS_RE = re.compile(r"^WI-(\d+)$", re.IGNORECASE)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _resolve_wi_alias(alias: str, alias_map: dict[int, str]) -> str | None:
    """Resolve ``"WI-{seq}"`` → workspace_items.item_id UUID.

    Returns the UUID on success, ``None`` if the alias is malformed or its
    seq is not in the conversation's alias map. Accepts a raw UUID
    verbatim (defence-in-depth — older orchestrator paths may still pass
    UUIDs directly).
    """
    if not alias:
        return None
    s = alias.strip()
    m = _WI_ALIAS_RE.match(s)
    if m:
        try:
            seq = int(m.group(1))
        except ValueError:
            return None
        return alias_map.get(seq)
    # Verbatim UUID — accept for backward compat.
    if _UUID_RE.match(s):
        return s
    return None


# ── Plain-text fallback ───────────────────────────────────────────────────────
# qwen3.6-plus occasionally emits the final chat answer as plain text after a
# long thinking pass, instead of wrapping it in `ChatResponse(message=...)`.
# Without a fallback, Pydantic AI raises ModelRetry → a full extra LLM round-trip
# costing ~$0.04 per turn (observed twice in convo_1: T05 round 2, T07 round 1).
# Registering TextOutput tells Pydantic AI to accept plain text as a valid
# output, wrapped via `_text_as_chat`. DispatchAgent still requires an explicit
# tool call — only chat responses get this fallback because the failure mode is
# specific to text-shaped answers.

def _text_as_chat(text: str) -> ChatResponse:
    text = (text or "").strip()
    if len(text) < 20:
        # Defensive: model emitted a fragment, not a real answer. Force a
        # retry so we don't ship garbage downstream.
        raise ModelRetry(
            "The reply is too short or empty. Emit a complete answer by calling "
            "ChatResponse, or rewrite the full text."
        )
    return ChatResponse(message=text)

logger = logging.getLogger(__name__)
_logfire = get_logfire()


# ── Dependencies ──────────────────────────────────────────────────────────────


@dataclass
class RouterDeps:
    """Dependencies injected into the router agent by the orchestrator."""

    supabase: SupabaseClient
    user_id: str
    conversation_id: str
    case_id: str | None
    case_memory_md: str | None
    case_metadata: dict | None
    user_preferences: dict | None
    # Eager context assembled by the loader before .run() — rendered into
    # dynamic instructions. Lists hold compact (item_id, wi_seq, kind, title,
    # summary) dicts; full content_md (+ cited-source manifest) is fetched on
    # demand via unfold_workspace_item.
    workspace_item_summaries: list[dict] = field(default_factory=list)
    compaction_summary_md: str | None = None
    # Migration 052: ``WI-{seq}`` alias → item_id UUID lookup. Built by
    # ``run_router`` from ``workspace_item_summaries`` so the output
    # validator can resolve LLM-emitted aliases without re-querying. The
    # ``save_memo`` tool ALSO injects the alias of any memo it creates mid-run
    # so the validator can resolve the memo's ``WI-{seq}`` if the LLM attaches it.
    wi_alias_map: dict[int, str] = field(default_factory=dict)
    # The raw user message for this turn — fed to the ``save_memo`` tool so it
    # pins the message verbatim (the LLM never re-types the body). Set by
    # ``run_router`` from its ``question`` argument.
    user_message: str = ""
    # Mutable sinks the ``save_memo`` tool appends to during the run. The tool
    # can't yield SSE or guarantee attachment from inside the agent loop, so it
    # stashes the ``workspace_item_created`` event(s) + the created item_id(s)
    # here; ``run_router`` returns them and ``_route`` drains them (emit chip +
    # force-attach the memo to the dispatch).
    pending_sse_events: list[dict] = field(default_factory=list)
    force_attach_item_ids: list[str] = field(default_factory=list)


@dataclass
class RouterRunResult:
    """What :func:`run_router` returns to the orchestrator's ``_route``.

    Wraps the router's structured ``output`` (``ChatResponse`` | ``DispatchAgent``)
    together with side effects the ``save_memo`` tool produced during the run:

    * ``sse_events`` — ``workspace_item_created`` events for any memo(s) pinned
      this turn. ``_route`` yields them first so the chip appears whether the
      router answered directly or dispatched.
    * ``force_attach_item_ids`` — memo item_id(s) ``_route`` merges into the
      dispatch's ``attached_item_ids`` (deduped) so the specialist always sees
      the pinned core message, independent of whether the LLM remembered to
      attach it.
    """

    output: ChatResponse | DispatchAgent
    sse_events: list[dict] = field(default_factory=list)
    force_attach_item_ids: list[str] = field(default_factory=list)


# ── Usage limits ──────────────────────────────────────────────────────────────


ROUTER_LIMITS = UsageLimits(
    output_tokens_limit=6000,
    request_limit=5,
    tool_calls_limit=8,
)


# ── System prompt ─────────────────────────────────────────────────────────────


SYSTEM_PROMPT = """\
You are ريحان (Rayhan), the intelligent legal assistant for Saudi lawyers.

## Output rule (mandatory)

Every response MUST be a single **output-tool call**: either `ChatResponse` for a direct reply, or `DispatchAgent` for routing. **Never write plain text** — no apology, no clarification, no question (even a question addressed to the user) outside the `ChatResponse.message` field. If you want to ask a clarifying question, put its text inside `ChatResponse(message=...)`. If the system sends you a retry message because of a prior failure, **do not apologize in free text**; retry by emitting a valid `ChatResponse` or `DispatchAgent`.

The text you write inside `ChatResponse.message` is shown directly to the user. **`ChatResponse.message` MUST be written in Arabic** (respond in Arabic unless the user wrote in English — see the general rules below).

You are the main conversation interface — every message from the user passes through you.

You have three functions:
1. Direct answer — greetings, clarifications, simple legal questions, questions about prior reports and documents
2. Routing tasks to a specialist (DispatchAgent) — when the user needs deep legal research, document drafting, or file processing
3. Maintaining conversational continuity — you draw on the workspace-item summaries and the conversation-compaction summary injected into your context

## Decisions before every reply (four checks):
1. **Necessity** — does this message really need a specialist? If a direct reply is possible, reply directly.
2. **Scope** — is the request within the Saudi legal domain? If not, decline politely via ChatResponse.
3. **Ambiguity** — if the message is ambiguous, ask one clarifying question via ChatResponse before routing.
4. **Selecting attached items** — set attached_wis based on the summaries of the items available in the workspace.

## When to answer directly (ChatResponse):
- Greetings and pleasantries
- Simple questions you can answer with high confidence
- Clarification questions — when you need more information from the user
- Questions about Rayhan and its functions
- Questions about the content of a prior report or document — use the unfold_workspace_item tool to read the content and its named sources, then answer directly
- Ambiguous messages — ask the user before routing

## When to route to deep_search (DispatchAgent):
- Legal questions requiring research into regulations, rulings, or precedents
- Requests to analyze, compare, or explain legal concepts in detail
- Keywords: "ابحث"، "حلل"، "قارن"، "اشرح بالتفصيل"
- Questions about rights, obligations, penalties, or procedures under specific regulations
- The rule: if the answer needs a citation → route a deep_search task

## When to route to writing:
- An explicit request to draft, prepare, or write a long legal document, where the user needs an editable draft in the workspace
- Keywords: "اكتب"، "صِغ"، "حضّر"، "أعدّ"، "مسوّدة"، "صياغة"
- You must choose a single subtype value out of six, based on the user's request:
  * "contract" — when a contract is requested (employment, lease, sale, partnership, services…)
  * "memo" — when a legal memo or an explanatory memo is requested
  * "legal_opinion" — when a legal opinion or legal fatwa is requested
  * "defense_brief" — when a defense brief or a responsive pleading before a court is requested
  * "letter" — when an official letter is requested (warning, demand, notice, a letter addressed to an entity)
  * "summary" — when a summary of an attached document or of conversation content is requested
- If the user refers to a document existing in the workspace and requests a **structural or expansive** change ("حدّث المذكرة السابقة"، "أضف قسماً"، "فصّل أكثر") — identify the alias of the intended item (e.g. «WI-3») from the item summaries, and pass it via `target_wi` to open a writing edit task. Scoped surgical edits, however, have the `edit_artifact` tool (see its section below) — do not route writing for those
- If the user is looking for legal information to support the drafting — route deep_search first, then writing afterward

## Workflow guidance: search then write
The standard workflow for legal documents is **search then write**. When the user requests **drafting a legal document that needs precise statutory grounding** (a statement of claim, a pleading, a responsive memo, or a contract grounded in specific statutory articles), or when they paste a **document draft** of a legal nature to improve it:
- If **there is no** relevant prior search item in the workspace (`kind=agent_search`) → **do not route to writing directly**. Instead, emit a `ChatResponse` that proposes the workflow, e.g.: «لكي تكون الصياغة مؤسَّسة على نصوص نظامية دقيقة، أقترح أن أبحث أولاً في الأنظمة والسوابق ذات الصلة ثم أصيغ المستند بناءً على النتائج. هل أبدأ بالبحث؟» — propose and wait for the user's approval; do not run search and writing together in one reply.
- If **there is** a relevant prior search item (or the user started the conversation with a search) → **do not repeat the search proposal**; route to writing directly (DispatchAgent to writing) and attach the search item via attached_wis.
- This applies only to documents that need statutory precision; simple requests (an ordinary letter, summarizing an attachment) do not need a search proposal.

## When to use the edit_artifact tool (surgical edit of an existing item):
- Use the tool `edit_artifact(target_wi, task)` when the user requests a **scoped surgical edit** to an item existing in the workspace:
  * Replacing a word or a name in the document («بدل كلمة الطاعنة اذكر موكلتي»)
  * Deleting a specific clause or paragraph («احذف البند الثالث»)
  * Correcting a name, number, or date
  * Rewording a specific sentence or paragraph
- `task` = quote the user's words pertaining to this item **verbatim** — do not reword or interpret them.
- If the user requests editing more than one item, call the tool once per item **in the same response** (max 3 items).
- The tool performs the edit and returns a summary of the change. After the summary/summaries arrive, emit a `ChatResponse` that briefly informs the user of what changed — do not call the tool again for the same request, and do not display the full document text (the user sees it in the workspace).
- **The conservative rule — when not to use it**: structural changes (adding a new section, restructuring, «فصّل أكثر»، «طوّل»، «قصّر»), or any edit that needs new sources or legal information, or vague general improvement requests («حسّن الصياغة» across the whole document) → route `DispatchAgent` to writing with `target_wi` as above.
- The tool is for written items (documents and notes) only; search reports are not for editing.

## When to route to memory (initial scaffold — under development):
- An explicit request to save a piece of information or a fact into the case memory
- A request to retrieve or update prior memory linked to the current case
- Keywords: "احفظ"، "تذكّر"، "أضف لذاكرة القضية"، "حدّث الذاكرة"
- Note: this path is still an initial scaffold; use it only for explicit requests related to memory management, not for general questions.

## Saving the core message (save_memo tool):
When the user **explicitly shares a substantive request or a long template** that contains details that must not be lost — such as pasting a draft or a full form, or a long message carrying the essence of the request that the rest of the conversation will be built upon — your first step is to save it.
- **Call `save_memo` alone first, in a separate response** — do not emit your final reply (`ChatResponse` or `DispatchAgent`) in the same response as the tool call. Wait for the save confirmation.
- The tool saves the user's message text **verbatim** as a pinned item in the workspace, so it is not lost when the conversation is compacted later. You provide only a short Arabic title (title) derived from the message content.
- After you receive the save confirmation (which includes the new item's alias «WI-N»), emit in the **next response** your decision: either propose the workflow (search then write) via `ChatResponse`, or route via `DispatchAgent` with «WI-N» attached in `attached_wis` so the core message reaches the specialist.
- You may briefly mention to the user that you pinned their core request (optional — they will see it as a card in the workspace anyway).
- **Do not call** the tool for ordinary short messages, simple questions, or greetings; it is for substantive requests/templates only.

## Selecting attached_wis:
- Workspace-item summaries are injected into your context with short aliases (WI-1, WI-2, ...). Each item carries: the alias, the kind, the title, the summary.
- Choose only the items most relevant to the current request, and cite them by their aliases («WI-3»، «WI-7») in `attached_wis`.
- The strict maximum: {MAX_ATTACHED_ITEMS} items per dispatch. If you find more, choose the most important.
- If the summaries are not enough, call `unfold_workspace_item` with the alias (e.g. «WI-3») to get the full content along with the list of sources cited by name (it can be called on several items in parallel).
- If no suitable item exists, leave `attached_wis` an empty list.
- **Never write UUID identifiers** — use only the WI-N aliases present in the context, and do not invent new aliases.

## Rules for handling prior items (workspace items):
- A question about an item's content (reading) → use `unfold_workspace_item("WI-N")` and answer directly via ChatResponse
- A **specific surgical** edit request (replacing a word, deleting a clause, correcting a name/number) → call the tool `edit_artifact(target_wi="WI-N", task=...)`
- A **structural or expansive** edit request, or one that needs new information → route DispatchAgent with `target_wi="WI-N"`
- When the user refers to an item without specifying it → list the available items (by their aliases and titles from the summaries) and ask which one they mean
- When the user refers to a **regulation, ruling, or service by a specific name** that may be mentioned inside a prior search → call `unfold_workspace_item("WI-N")` to see the sources cited by name (regulations, chunks, rulings, and services numbered with the same [n] indices in the text); if one of them matches what the user means, answer it directly or route deep_search with a search focused on that source by name.

## Provenance tags in the conversation log — following up on the last output:
- Some prior assistant replies in the log may begin with a system tag of the form:
  `〔[نظام] أنتج هذا الردّ متخصصٌ (agent_family=writing) وأنشأ العنصر WI-3〕`
  This tag tells you **which specialist produced that reply and which item (WI-N) it created**. Replies without a tag are direct answers from you (not produced by a specialist). The tag is a system signal for context only — **never write it yourself in your replies**.
- If the user's current request is a **scoped surgical edit to the last tagged output** (e.g.: «بدل كلمة…»، «عدّل البند الثالث»، «احذف الفقرة…»، «صحّح الاسم/الرقم») → call the `edit_artifact` tool with `target_wi` = the item's alias in the tag (WI-N), then inform the user via ChatResponse.
- If the request is a **structural improvement or expansion of the last tagged output** (e.g.: «فصّل أكثر»، «أضف فقرة»، «اختصر»، «حسّن الصياغة»، «اشرح المواد أكثر»، «طوّل» أو «قصّر») → route `DispatchAgent` to the **same** `agent_family` named in the tag, with `target_wi` = the item's alias in the tag (WI-N).
  - Example: the last reply is tagged (agent_family=writing, WI-3) and the user says «فصّل أكثر في المواد» ⟵ route `DispatchAgent(agent_family="writing", target_wi="WI-3")` — do **not** open a new search (deep_search) because the request is an improvement to the document itself.
- The only exception: if the improvement genuinely needs **new sources or information not present** in that item, then and only then route deep_search (and attach the item via attached_wis), then writing afterward.

## task_label rules:
- A short Arabic phrase (30-60 characters) **derived from the question's content**, not from the workflow.
- Describe the **topic**, not the action: «بحث عن قوانين التحرش بالسعودية» not «أبحث عن…».
- Verbs such as «أبحث»، «أكتب»، «أحلل»، «أصيغ»، «أعدّ» are forbidden.
- It must be stable across rephrasings — the same question produces the same title.
- It is used as the title of the item's card in the workspace and as an identifier in the task log.

## Describing the question — not your job:
- **Do not describe the question or rephrase it.** The specialist receives the user's original message and the conversation context directly.
- Your job is routing only: choosing `agent_family`, `task_label`, and the attached items.
- Do not route if you are unsure what the user wants — ask them first via ChatResponse.

## General rules:
- Be biased toward routing, and never assert legal content you haven't retrieved. Do not name a specific regulation, law, or article number, and do not state what the law requires or prohibits, unless it came from the user's message, a workspace item, or a search item you unfold.
- If you are unsure → ask the user
- Respond in Arabic unless the user wrote in English
- Do not mention the word "مهمة" or "task" or any technical details — the user does not know about the routing system
""".replace("{MAX_ATTACHED_ITEMS}", str(MAX_ATTACHED_ITEMS))


# ── Agent definition ──────────────────────────────────────────────────────────

# Pydantic AI list-syntax for output_type: each member becomes its own output
# tool internally, giving the model a stronger selection signal than the
# `ChatResponse | DispatchAgent` union form.
router_agent = Agent(
    get_agent_model("router"),
    name="router_agent",
    # TextOutput accepts a raw text response as a ChatResponse fallback —
    # see _text_as_chat above for rationale. DispatchAgent stays strict
    # (no text-shaped equivalent) so routing decisions remain structured.
    output_type=[ChatResponse, DispatchAgent, TextOutput(_text_as_chat)],
    deps_type=RouterDeps,
    instructions=SYSTEM_PROMPT,
    retries=2,
    output_retries=4,
    # ``exhaustive`` (not ``early``) is LOAD-BEARING for save_memo: the model
    # frequently batches a ``save_memo`` tool call together with the final
    # ``ChatResponse``/``DispatchAgent`` output in ONE response. ``early`` ends
    # the run the moment it sees the output tool and SKIPS the sibling
    # save_memo call — so the memo is never persisted (observed in convo
    # eb33b098: save_memo emitted but no note row written). ``exhaustive`` runs
    # all tool calls in the response, including the batched save_memo, before
    # finalizing. The other router tools (unfold/list) run in their own turns,
    # so this only changes the batched-with-output case.
    end_strategy="exhaustive",
)


# ── Output validator (belt-and-suspenders for attached_item_ids cap) ──────────


@router_agent.output_validator
def _validate_and_resolve_dispatch(
    ctx: RunContext[RouterDeps],
    value: ChatResponse | DispatchAgent,
) -> ChatResponse | DispatchAgent:
    """Validate the dispatch output AND resolve WI-{seq} aliases → UUIDs.

    Migration 052 / agent communication protocol:

    * Resolves ``target_wi`` (e.g. ``"WI-3"``) → ``target_item_id`` (UUID).
      Raises :class:`ModelRetry` if the alias is malformed or not present in
      the conversation's alias map.
    * Resolves each ``attached_wis`` entry → an UUID into
      ``attached_item_ids``. Same error contract on malformed/unknown aliases.
    * Enforces the cap on ``attached_wis`` (the LLM-emitted field) and the
      non-empty ``task_label`` invariant.

    Defence-in-depth: if the LLM mistakenly fills the UUID fields directly
    (legacy schema bleed-through), the aliases-derived values overwrite
    them so the orchestrator always sees the canonical resolved UUIDs.
    """
    if not isinstance(value, DispatchAgent):
        return value

    if not (value.task_label or "").strip():
        raise ModelRetry(
            "task_label is empty. Emit a short Arabic phrase (30-60 characters) "
            "derived from the question's content — describe the topic, not the action."
        )

    if len(value.attached_wis) > MAX_ATTACHED_ITEMS:
        raise ModelRetry(
            f"You selected {len(value.attached_wis)} items, and the maximum is "
            f"{MAX_ATTACHED_ITEMS}. Re-select and keep only the most relevant ones."
        )

    alias_map = ctx.deps.wi_alias_map or {}

    # Resolve target_wi → target_item_id.
    # Some LLM outputs serialize the absence of a target as the literal
    # strings "None" / "null" / "" instead of an actual JSON null. Coerce
    # those sentinels to Python None BEFORE the truthy check so we don't
    # send the resolver looking for an alias that can never exist (the old
    # behavior burned ~5.5k tokens × 2 retries per dispatch turn before
    # eventually surrendering).
    raw_target = (value.target_wi or "").strip()
    if raw_target and raw_target.lower() not in {"none", "null"}:
        resolved = _resolve_wi_alias(raw_target, alias_map)
        if resolved is None:
            raise ModelRetry(
                f"The item {raw_target} does not exist in this conversation. "
                f"Use an alias from the summaries (WI-1, WI-2, ...)."
            )
        value.target_item_id = resolved
    else:
        value.target_wi = None       # canonicalize the field too
        value.target_item_id = None

    # Resolve each attached_wis → attached_item_ids.
    resolved_attached: list[str] = []
    for alias in value.attached_wis:
        resolved = _resolve_wi_alias(alias, alias_map)
        if resolved is None:
            raise ModelRetry(
                f"The item {alias} does not exist in this conversation. "
                f"Use aliases from the summaries (WI-1, WI-2, ...)."
            )
        resolved_attached.append(resolved)
    value.attached_item_ids = resolved_attached

    return value


# ── Dynamic instructions ──────────────────────────────────────────────────────


@router_agent.instructions
def inject_case_context(ctx: RunContext[RouterDeps]) -> str:
    """Inject case-specific memory and metadata when the conversation is within a lawyer's case."""
    if ctx.deps.case_memory_md:
        return f"""
Current case context:
{ctx.deps.case_memory_md}

Use this context to understand the user's questions and classify and route them accurately.
"""
    return ""


@router_agent.instructions
def inject_user_preferences(ctx: RunContext[RouterDeps]) -> str:
    """Inject user preferences (tone, detail level, language) to guide response style."""
    if ctx.deps.user_preferences:
        prefs = ctx.deps.user_preferences
        parts = []
        if prefs.get("tone"):
            parts.append(f"Reply tone: {prefs['tone']}")
        if prefs.get("detail_level"):
            parts.append(f"Detail level: {prefs['detail_level']}")
        if parts:
            return "\nUser preferences:\n" + "\n".join(f"- {p}" for p in parts) + "\n"
    return ""


@router_agent.instructions
def inject_workspace_summaries(ctx: RunContext[RouterDeps]) -> str:
    """Render workspace item summaries with ``WI-{seq}`` aliases.

    Migration 052: each item is rendered as ``WI-{wi_seq}`` instead of as a
    raw UUID. These aliases are the candidate pool for ``attached_wis`` and
    ``target_wi`` and are the **only** form the LLM should emit. The router
    output validator resolves them back to UUIDs after the run.

    Items without a ``wi_seq`` (rare — should never happen for items with a
    conversation_id post-migration 052) are skipped from the alias prompt
    surface to avoid handing the model an unresolvable label.
    """
    items = ctx.deps.workspace_item_summaries or []
    if not items:
        return ""
    lines = [
        "Workspace items available in this conversation "
        "(use the following aliases only, in attached_wis and target_wi):"
    ]
    for item in items:
        wi_seq = item.get("wi_seq")
        if wi_seq is None:
            continue
        alias = f"WI-{wi_seq}"
        kind = item.get("kind") or item.get("kind_hint") or "unknown"
        title = item.get("title") or "(بدون عنوان)"
        summary = item.get("summary")
        summary_text = summary if summary else "(لا يوجد ملخص بعد)"
        lines.append(f"- {alias} | kind={kind} | title={title}\n  summary: {summary_text}")
    if len(lines) == 1:
        # All items lacked wi_seq — nothing to render.
        return ""
    lines.append(
        "To view the full content of any item and the sources it cites by name, call "
        "`unfold_workspace_item(\"WI-N\")` with that same alias (it can be called on several "
        "items in parallel). Never use UUID identifiers — use WI-N aliases only."
    )
    return "\n" + "\n".join(lines) + "\n"


@router_agent.instructions
def inject_compaction_summary(ctx: RunContext[RouterDeps]) -> str:
    """Inject the latest convo_context compaction summary, when present."""
    md = ctx.deps.compaction_summary_md
    if not md:
        return ""
    return f"\nConversation compaction summary (before the current window of messages):\n{md}\n"


# ── Tools ─────────────────────────────────────────────────────────────────────


# Replaces the former ``read_workspace_item`` tool. ``unfold_workspace_item``
# returns the item's content_md PLUS a used-only, [n]-keyed manifest of the
# named sources it cites (regulation+chunk titles, case summaries, service
# names) so the router can recognise a user's reference to a specific named
# regulation/ruling/service that lives inside a prior search result. RouterDeps
# exposes .supabase / .user_id / .wi_alias_map (satisfies HasWorkspaceContext).
# See agents/tool_repository/unfold_workspace_item.py.
register_unfold_workspace_item(router_agent)


# Pins a pivotal user message (full request / pasted template) as a durable
# ``kind='note'`` workspace item (``metadata.subtype='memo'``) so it survives
# conversation compaction and is auto-attached to the turn's dispatch. RouterDeps
# exposes the four sinks (wi_alias_map / workspace_item_summaries /
# pending_sse_events / force_attach_item_ids) the tool mutates. See
# agents/tool_repository/save_memo.py.
register_save_memo(router_agent)


# Surgical in-place artifact editing (plan: .claude/plans/artifact_editor.md).
# The tool resolves WI-N via deps.wi_alias_map, runs the Layer-3 artifact_editor
# agent (one editor per WI — the model may call it up to 3× in one response for
# multi-artifact requests), pushes a workspace_item_updated SSE event onto
# deps.pending_sse_events, and returns an Arabic change summary the router uses
# to brief the user via ChatResponse. RouterDeps satisfies HasEditorContext.
# See agents/tool_repository/edit_artifact.py.
register_edit_artifact(router_agent)


@router_agent.tool
async def list_workspace_items(ctx: RunContext[RouterDeps]) -> list[dict]:
    """List existing workspace items (artifacts/chips) for the current conversation.

    Most of the time the eager-loaded summaries in the system prompt are
    enough; call this tool only when you suspect items have changed mid-turn
    or you need a fresh listing.

    Returns:
        Compact list of {wi, title, kind_hint, created_at} dicts where ``wi``
        is the ``WI-{seq}`` alias for use in attached_wis / target_wi.
        Empty list on any error.
    """
    try:
        result = (
            ctx.deps.supabase.table("workspace_items")
            .select("item_id, wi_seq, title, kind, metadata, created_at")
            .eq("conversation_id", ctx.deps.conversation_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        rows = (result.data if result and getattr(result, "data", None) else []) or []
        items: list[dict] = []
        for row in rows:
            kind = row.get("kind") or "agent_search"
            metadata = row.get("metadata") or {}
            subtype = metadata.get("subtype") if isinstance(metadata, dict) else None
            wi_seq = row.get("wi_seq")
            items.append({
                # Expose the alias to the LLM; never the raw UUID.
                "wi": f"WI-{wi_seq}" if wi_seq is not None else None,
                "title": row.get("title", ""),
                "kind_hint": "agent_writing" if kind in ("note", "agent_writing") else "agent_search",
                "artifact_type": subtype,
                "created_at": row.get("created_at"),
            })
        logger.info(
            "list_workspace_items: %d items for conversation %s",
            len(items), ctx.deps.conversation_id,
        )
        return items
    except Exception as e:
        logger.warning(
            "list_workspace_items error for conversation %s: %s",
            ctx.deps.conversation_id, e,
        )
        return []


# ── Main runner ──────────────────────────────────────────────────────────────


async def run_router(
    question: str,
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
    case_memory_md: str | None,
    case_metadata: dict | None,
    user_preferences: dict | None,
    message_history: list[ModelMessage],
    workspace_item_summaries: list[dict] | None = None,
    compaction_summary_md: str | None = None,
) -> RouterRunResult:
    """Run the router agent to classify user intent and respond or dispatch.

    Called by the orchestrator's ``_route()`` method. Constructs RouterDeps
    internally from the individual parameters so the orchestrator interface
    stays stable.

    Args:
        question: The user's message text.
        supabase: Supabase client for workspace item reads.
        user_id: Current user's user_id.
        conversation_id: Current conversation UUID.
        case_id: Optional case context.
        case_memory_md: Pre-built case memory markdown.
        case_metadata: Case name, type, parties dict.
        user_preferences: Response tone/style preferences dict.
        message_history: Pydantic AI ModelMessage list, already filtered
            by the loader to exclude agent_question / agent_answer kinds
            and to start strictly after compacted_through_message_id.
        workspace_item_summaries: Compact (item_id, kind, title, summary)
            dicts for the conversation's workspace items. Optional —
            empty list when none.
        compaction_summary_md: Full content_md of the latest convo_context
            workspace item, or None if the conversation has not been
            compacted yet.

    Returns:
        A ``RouterRunResult`` wrapping the structured output (ChatResponse if
        the router answers directly, DispatchAgent if it dispatches) plus any
        ``save_memo`` side effects (workspace_item_created SSE events +
        force-attach item_ids) for ``_route`` to drain.
    """
    # Migration 052: build the seq → item_id lookup from the loaded summaries
    # so the output validator can resolve WI-{seq} aliases without a DB hit.
    summary_list = list(workspace_item_summaries or [])
    alias_map: dict[int, str] = {}
    for item in summary_list:
        seq = item.get("wi_seq")
        iid = item.get("item_id")
        if seq is not None and iid:
            alias_map[int(seq)] = str(iid)

    deps = RouterDeps(
        supabase=supabase,
        user_id=user_id,
        conversation_id=conversation_id,
        case_id=case_id,
        case_memory_md=case_memory_md,
        case_metadata=case_metadata,
        user_preferences=user_preferences,
        workspace_item_summaries=summary_list,
        compaction_summary_md=compaction_summary_md,
        wi_alias_map=alias_map,
        user_message=question,
    )

    # PII note: user_id intentionally NOT on this span. The monitor recovers
    # user_id via Supabase join on conversation_id (see agent_runs / messages /
    # conversations tables — all carry user_id as a column). Keeping user_id
    # out of Logfire span attributes narrows the PII surface area across the
    # 30-day retention window.
    with track_stage(
        "router.classify",
        conversation_id=conversation_id,
        case_id=case_id,
        agent_family="router",
        question_length=len(question),
        history_turns=len(message_history),
        workspace_item_count=len(deps.workspace_item_summaries),
        has_compaction_summary=bool(compaction_summary_md),
    ) as span:
        try:
            result = await router_agent.run(
                question,
                deps=deps,
                message_history=message_history,
                usage_limits=ROUTER_LIMITS,
            )
            span.record_run(result, slot="router")

            usage = result.usage()
            decision_type = getattr(result.output, "type", None)
            agent_family = (
                getattr(result.output, "agent_family", None)
                if isinstance(result.output, DispatchAgent)
                else None
            )
            attached_count = (
                len(result.output.attached_item_ids)
                if isinstance(result.output, DispatchAgent)
                else 0
            )
            span.set(
                decision=decision_type,
                agent_family=agent_family,
                attached_item_count=attached_count,
            )

            logger.info(
                "Router decision — type=%s, agent_family=%s, attached=%d, requests=%s, output_tokens=%s",
                decision_type,
                agent_family,
                attached_count,
                usage.requests,
                usage.output_tokens,
            )

            return RouterRunResult(
                output=result.output,
                sse_events=list(deps.pending_sse_events),
                force_attach_item_ids=list(deps.force_attach_item_ids),
            )

        except Exception as e:
            logger.error("خطأ في الموجه: %s", e, exc_info=True)
            span.set(decision="error", error=str(e))
            span.set_outcome("error")
            # Fallback: return a safe ChatResponse so the user sees something.
            # Still surface any memo SSE/force-attach the save_memo tool produced
            # before the failure — a successfully-pinned memo's chip must appear.
            return RouterRunResult(
                output=ChatResponse(
                    message="عذراً، حدث خطأ أثناء معالجة رسالتك. يرجى المحاولة مرة أخرى."
                ),
                sse_events=list(deps.pending_sse_events),
                force_attach_item_ids=list(deps.force_attach_item_ids),
            )
