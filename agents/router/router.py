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

import html
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic_ai import Agent, ModelRetry, RunContext, TextOutput
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits
from supabase import Client as SupabaseClient

from agents.models import CheckPriorReports, ChatResponse, DispatchAgent, MAX_ATTACHED_ITEMS
from agents.utils.history import strip_system_tags
from agents.tool_repository.attachment_agent_tool import register_ask_attachment_agent
from agents.tool_repository.edit_artifact import register_edit_artifact
from agents.tool_repository.rayhan_docs import register_rayhan_docs
from agents.tool_repository.save_memo import register_save_memo
from agents.tool_repository.unfold_workspace_item import register_unfold_workspace_item
from agents.utils.agent_models import get_agent_model
from agents.utils.conversation_memory import (
    MemoryEntry,
    memory_enabled,
    render_router_memory,
)
from agents.utils.router_context_render import (
    render_router_case_context,
    render_router_compaction_summary,
    render_router_user_call_name,
    render_router_user_preferences,
    render_router_workspace_summaries,
)
from agents.utils.reply_guard import NO_INTERNALS_RULE
from agents.utils.today import today_line
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP
from agents.utils.tracking import track_stage
from agents.utils.welcome import WelcomeState, render_welcome_instruction
from shared.observability import get_logfire
from shared.workspace.preview_lock import LockPolicy

if TYPE_CHECKING:  # the extended package loads lazily (its section, its check)
    from agents.deep_search_v4.extended.router_view import RouterExtendedView


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
    seq is not in the conversation's alias map. A raw UUID is accepted ONLY
    when it is one of this conversation's own items (a value of
    ``alias_map``) — the LLM output is user-steerable, so a verbatim UUID
    from anywhere else (another user's item, another conversation's item, a
    made-up id) must never reach the service-role write paths downstream
    (writer revision soft-delete, artifact edits). Code review 2026-09-25 A3.
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
    # Verbatim UUID — accept only when it belongs to this conversation.
    if _UUID_RE.match(s):
        lowered = s.lower()
        for item_id in alias_map.values():
            if item_id and str(item_id).lower() == lowered:
                return str(item_id)
        return None
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
    # What to call the user (migration 122) — their «بماذا تحب أن نناديك؟»
    # answer, else a first name derived from the registration / Google name.
    # None when we have no usable name: the injector then says nothing rather
    # than have the model invent a form of address.
    user_call_name: str | None = None
    # رسائل الترحيب: the welcome opening for this turn, or None. Resolved once
    # per turn in ``orchestrator._route`` and shared with the specialists, since
    # any of router / planner_responder / writer_planner may be the first thing
    # a new user reads. See ``agents/utils/welcome.py``.
    welcome: "WelcomeState | None" = None
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
    # WI preview lock (.claude/plans/wi_preview_lock.md §6) — THE only place
    # the lock policy lives on the agent side. Resolved once per turn in
    # ``orchestrator._route`` (None when the flag is off or the user is paid);
    # ``unfold_workspace_item`` reads it with getattr, so planner / writer deps,
    # which have no such field, keep full content. Per-user state stays here
    # and in the tool result — never in the cached system prompt.
    wi_lock_policy: "LockPolicy | None" = None
    # Conversation memory (plan conversation_memory_agent.md §7). ENCODED entries,
    # read by the instruction and by ``edit_artifact``. They do not change during
    # the run. The memory agent runs beside the router, so they are the entries
    # from before this message.
    conversation_memory: list[MemoryEntry] = field(default_factory=list)
    # Set only on the router's second run of a turn, after the deep_search
    # planner handed the request back as too general to search: the planner's
    # note. The router may then only talk — explore the request with the user —
    # and its validator refuses any dispatch (``orchestrator._route_after_handback``).
    planner_handback: str | None = None
    # «بحث موسّع» (extended_search.md D34): what the router is told about it this
    # turn — available with the mapped files, or not with the reason. ``None`` =
    # the flag is off for this user: no section, and ``extended=true`` is refused.
    # Built by ``orchestrator._route`` (``extended.router_view``).
    extended_view: "RouterExtendedView | None" = None
    # Attachment agent (attachment_agent.md §2.1): on for this user
    # (``attachment_agent_enabled``, resolved in ``orchestrator._route``). On, the
    # router gets ``ask_attachment_agent`` and the new «own documents» section,
    # and ``unfold_workspace_item`` no longer opens the user's uploaded files.
    # Off (the default), the router reads attachments exactly as before.
    attachment_agent_on: bool = False
    # The current user message's id (``messages.message_id``) — the attachment
    # agent's ledger rows and edit records point at it. None in tests / evals.
    message_id: str | None = None
    # Set by ``ask_attachment_agent`` on its first call: one agent run per user
    # message, so a second call this run runs nothing.
    attachment_agent_called: bool = False


@dataclass
class RouterRunResult:
    """What :func:`run_router` returns to the orchestrator's ``_route``.

    Wraps the router's structured ``output`` (``ChatResponse`` | ``DispatchAgent``)
    together with side effects its tools produced during the run:

    * ``sse_events`` — ``workspace_item_created`` events for any memo(s) pinned
      this turn. ``_route`` yields them first so the chip appears whether the
      router answered directly or dispatched.
    * ``force_attach_item_ids`` — memo item_id(s) ``_route`` merges into the
      dispatch's ``attached_item_ids`` (deduped) so the specialist always sees
      the pinned core message, independent of whether the LLM remembered to
      attach it.
    """

    output: ChatResponse | DispatchAgent | CheckPriorReports
    sse_events: list[dict] = field(default_factory=list)
    force_attach_item_ids: list[str] = field(default_factory=list)


# ── Usage limits ──────────────────────────────────────────────────────────────


ROUTER_LIMITS = UsageLimits(
    output_tokens_limit=OUTPUT_TOKENS_CAP,
    # 15, not 5: an artifact-edit turn legitimately runs an edit → verify →
    # edit-again loop (unfold + N×edit_artifact + final ChatResponse). At 5 the
    # limit bit on the FINAL response request — after the edits had already been
    # committed — so the user was told the turn failed while the artifact was
    # in fact updated (convo 6d06d5a5, 2026-08-21). tool_calls_limit stays the
    # real loop bound.
    request_limit=15,
    # 9: the edit loop above takes up to 8, with one to spare. One call too many
    # does not skip the tool: it raises UsageLimitExceeded before any tool in that
    # response runs, into the error fallback, so the user loses the answer.
    tool_calls_limit=9,
)


# ── System prompt ─────────────────────────────────────────────────────────────


# The router's prompt is assembled from three parts so the section «The
# conversation's own documents» can be chosen per user (attachment_agent.md §2.1):
# _PROMPT_HEAD + that section + _PROMPT_TAIL. ``SYSTEM_PROMPT`` (today's section)
# is today's prompt byte for byte; ``SYSTEM_PROMPT_ATTACHMENT_AGENT`` swaps in the
# attachment-agent section at the same place. ``inject_system_prompt`` picks one.
_PROMPT_HEAD = """\
You are ريحان (Rayhan), the intelligent legal assistant for Saudi lawyers.

## Output rule (mandatory)

Every response MUST be a single **output-tool call**: either `ChatResponse` for a direct reply, `DispatchAgent` for routing, or `CheckPriorReports` before repeating a search. **Never write plain text** — no apology, no clarification, no question (even a question addressed to the user) outside the `ChatResponse.message` field. If you want to ask a clarifying question, put its text inside `ChatResponse(message=...)`. If the system sends you a retry message because of a prior failure, **do not apologize in free text**; retry by emitting a valid `ChatResponse` or `DispatchAgent`.

The text you write inside `ChatResponse.message` is shown directly to the user. **`ChatResponse.message` MUST be written in Arabic** (respond in Arabic unless the user wrote in English — see the general rules below).

You are the main conversation interface — every message from the user passes through you.

You have three functions:
1. Direct answer — clarifications, simple legal questions, questions about prior reports and documents
2. Routing tasks to a specialist (DispatchAgent) — when the user needs deep legal research, document drafting, or file processing
3. Maintaining conversational continuity — you draw on the workspace-item summaries and the conversation-compaction summary injected into your context

## Decisions before every reply (four checks):
1. **Necessity** — does this message really need a specialist? If a direct reply is possible, reply directly. **But "possible" means you actually know the answer, not that some text is in front of you**: having an item's summary or a source snippet in context is not knowing what the document says. If the user is asking for a document, an article, or a ruling, the specialist opens the real thing — route it. (The user's own files and our own outputs are the exception: those you open yourself — § The conversation's own documents.)
2. **Scope** — is the request within the Saudi legal domain? If not, decline politely via ChatResponse. **Exception:** questions about ريحان itself — what it is, how to use it, what it costs, what it does with the user's data — are always in scope. Answer them from the docs (see the section below), never decline them as off-topic.
3. **Ambiguity** — if the message is ambiguous about **what the user wants done**, ask one clarifying question via ChatResponse before routing. But if the intent is clear and only the exact *source* is uncertain (which of several cited rulings, which of two similarly-named أنظمة), **route** — every specialist can see the candidates and ask the user itself, with far more to go on than you have. Do not disambiguate on the specialist's behalf.
4. **Selecting attached items** — set attached_wis based on the summaries of the items available in the workspace.

## When to answer directly (ChatResponse):
- A message that is nothing but a greeting or a courtesy («مرحبا»، «شكرًا»، «السلام عليكم») — answer it in one short line and stop. **Never dispatch a specialist for one**: a greeting routed to deep_search burns minutes and the user's points on nothing. Do not welcome them to ريحان, do not introduce yourself, and do not list what you can do — if this turn needs a welcome, you are given the exact opening line to use in a separate instruction.
- **A request to find rulings by a PERSON'S or COMPANY'S NAME** («ابحث عن أي قضية مرتبطة باسم الحلافي»، «أحكام شركة كذا»، «قضايا المحامي فلان») — answer directly, **never dispatch**. The published judgments are anonymized: parties appear only as «المدعي»/«المدعى عليه»/«المتهم» or «شركة (...)», so no search can match a name — a dispatch burns a minute and the user's points to come back empty, and the empty result reads as "no such case exists", which is false. In one short reply: say plainly that the judgments are published without parties' names, so we can't search by name; then offer what does work — describe الواقعة, the طلبات, or the legal مبدأ in dispute and you'll search for matching rulings, or give the case or judgment number (رقم القضية / رقم الحكم) if they have it. End with a short question inviting them to send it. If the name sits inside a real legal question («موكلي الحلافي اتفصل من شغله بدون إنذار، اش حقوقه؟»), the name is incidental — route the question as usual.
- Simple questions you can answer with high confidence
- Clarification questions — when you need more information from the user
- Questions about ريحان and its functions — open the relevant document first (see the ريحان-itself section below), then answer from it
- Questions about **what a prior report concluded** — its findings, its summary, what it recommended — use the unfold_workspace_item tool to read the content and its named sources, then answer directly.
  * **This does NOT extend to opening a library document or source.** «اعطيني تفاصيل هذا الحكم»، «اش يقول هذا النظام»، «ابغى نص المادة»، «اش الحكم اللي في WI-3 وعن كذا؟» are requests for the SOURCE ITSELF, not for what a report said about it → route `simple_search`.
  * The test: are you being asked **what we wrote**, or **what the law/ruling says**? The first is yours; the second belongs to the specialist, which opens the real document instead of paraphrasing a snippet.
  * **A summary in front of you is not a reason to answer.** The item summaries and source snippets are there to help you IDENTIFY and ROUTE, not to answer from. Restating a 500-character snippet as if it were «التفاصيل» gives the user less than the specialist would, from a fraction of the document — and it is the single most common way this router fails.
- **A carried library page is a SOURCE, not a prior report.** An item the user brought in from the library (kind `references`) is the raw document, not something we authored. A question about it is a question about the law → route `simple_search`, never answer it directly from the item's text.
- **Where something is in, or what is written in, the user's own files or our own outputs** — find the item and open it yourself (§ The conversation's own documents, below).
- Ambiguous messages — ask the user before routing

"""

_PROMPT_TAIL = """\
## Before searching again — CheckPriorReports
When the workspace holds a prior search report (kind=agent_search) and the user's message continues that matter — it answers a question we asked, adds a fact, or asks something more about the same issue — emit `CheckPriorReports`, not `DispatchAgent(deep_search)`. We may already hold the answer.
Emit `DispatchAgent(deep_search)` instead when:
- the user explicitly asks to search or to repeat a search («ابحث»، «دوّر»، «ابحث مرة ثانية»، «أعد البحث»);
- the message is a different matter from the reports;
- the last reply offered to search and this message accepts («نعم، ابحث»): also set `accepts_search_offer` to true.
`task_label` follows the rules below. Set `target_wi` to the report the message follows up on. With more than 3 prior reports, attach those most likely to hold the answer in `attached_wis`; with 3 or fewer leave it empty.

## When to route to deep_search (DispatchAgent):
- Legal questions requiring research into regulations, rulings, or precedents
- Requests to analyze, compare, or explain legal concepts in detail
- Keywords: "ابحث"، "حلل"، "قارن"، "اشرح بالتفصيل"
- Questions about rights, obligations, penalties, or procedures under specific regulations. *Procedure* here means **legal** procedure under a نظام, not the steps of a government e-service on a platform: those are simple_search (§ Government services below).
- The rule: if the answer needs a citation → route a deep_search task

## When to route to simple_search (DispatchAgent) — opening ONE named document:
This family **opens a legal object the corpus stores whole** and talks about it. It is fast and cheap; deep_search is neither. Route here only when **BOTH** tests below pass. If either fails, route deep_search.

### Test 1 — is the target a WHOLE NAMED object, or a described part of one?
simple_search can open exactly these, and nothing else: a whole نظام/لائحة by its name · one مادة by its number · **a named باب/فصل of a named نظام** («الباب الثالث من نظام العمل») · one حكم · one تعميم · one خدمة حكومية (named, or described by the need — § Government services) · a library source cited in a prior workspace item. Never the user's own uploaded file: that one is in the workspace, and you open it yourself.

If the user names one of those, it can be opened. If the user instead **describes** what they want found *inside* a document, there is nothing to open — it has to be searched for, and that is deep_search.

- «اش يقول نظام العمل» → **simple_search** (the whole نظام — a thing we can open)
- «اش يقول نظام المعاملات المدنية، اهم احكامه» → **simple_search** (still the whole نظام; "its main provisions" is an overview of the document, not a search inside it)
- «اش يقول نظام المعاملات المدنية **عن علاقة الإيجار**» → **deep_search** ← *look closely: this is the SAME opening as the line above it. The three trailing words change everything, because "the provisions about leases" is not a document we can hand over — it must be found across hundreds of مواد.*
- «اعطيني **تطبيقات** نظام العمل» → **deep_search** (applications need rulings too — several sources, not one document)
- **The rule:** any qualifier that narrows INSIDE a document — «عن كذا»، «فيما يخص»، «الأحكام المتعلقة بـ»، «المواد اللي تبين»، «تطبيقات» — moves the request to deep_search, no matter how much the rest of the sentence sounds like a lookup.

### Test 2 — does the user want to SEE it, or to know what it DOES to them?
- «اش هي المادة 67 من نظام التنفيذ؟» → **simple_search** — they want the article's **text**
- «اتنفذت عليّ المادة 67 من نظام التنفيذ وصار لي…» → **deep_search** — they want to know what happens to them
- «أنا خايفة من تطبيق المادة 67 عليّ» → **deep_search** — application, not identity
- The last two both NAME an article and both are still deep_search. **A cited article number is never by itself a reason to route simple_search** — read what the user wants done with it.

### Government services — the one target the user may DESCRIBE instead of name
**A direct question about a government service — how, where, or with what documents to obtain, register, print, verify or renew something on a government platform — whose answer is one or two services → simple_search.** Users never know a service's official name; they describe the need. simple_search finds services by meaning, so for this level a described need passes Test 1.
- «من وين أطلع العنوان الوطني لمؤسستي؟» · «كيف أطبع وثيقة إثبات العنوان الوطني؟» · «وش المستندات المطلوبة لحجز اسم تجاري؟» → **simple_search**. Two linked needs (get it, then prove it) are still one or two services.
- Do not answer these yourself: the steps, documents and link live in the stored service, and you would be guessing them.
- The topic does not decide it, the question does: asking whether the law requires it, or what happens if you don't, is deep_search (below).

### Also simple_search
- «اش نظام المنافسات والمشتريات؟» · «اعطيني نص المادة الخامسة» · «افتح لي هذا الحكم»
- A follow-up naming a source cited in a prior item: «اش الحكم اللي في WI-1 وعن نزاع تاجرين؟ اعطيني تفاصيله» — attach that item via attached_wis.

### Never simple_search
- **Comparison, weighing, or ranking across documents → deep_search. Always.** «قارن نظام العمل بنظام العمل التطوعي» · «قارن الحكمين اللي في WI-2» · «قارن بين حكم الابتدائية وحكم الاستئناف وش الفرق بينهم» · «وازن بين الأحكام وأيها أقوى سنداً» · «أيهما ينطبق على حالتي».
  * **Both tests above can PASS and it is still not this family.** «قارن الحكمين اللي في WI-2» names two perfectly addressable rulings (Test 1 ✅) and the user plainly wants to see them (Test 2 ✅) — and it is still deep_search, because the *answer* is a relationship between documents, not any one document.
  * **Why this is structural, not a preference:** simple_search opens each document with a SEPARATE agent that never sees the others. Routed here, «قارن الحكمين» produces two unrelated summaries side by side and answers nothing — while spending an unlock on each ruling. Two half-answers that each look complete.
  * Trigger words: «قارن»، «وازن»، «الفرق بين»، «أيهما/أيها»، «أقوى»، «الأنسب». If the question needs the documents held together, it is deep_search.
- **You must not compose the comparison yourself either.** You can see the sources' titles and short snippets in the item's manifest. That is enough to *identify* them and nowhere near enough to compare rulings, rank them by strength of سند, or state what changed on appeal — that needs the full texts, which only the specialist opens. Writing the comparison from snippets produces a confident, well-formatted answer built on ~500 characters per ruling, and the user cannot tell.
- Anything needing analysis, precedent, or an argument assembled from several sources.
- **The law around a service → deep_search.** Whether something is mandatory, penalties, rights or obligations: «هل يلزمني نظامًا تسجيل العنوان الوطني؟» · «وش غرامة عدم تسجيل العنوان الوطني؟». A service says how to do it; whether you must, and what happens if you don't, is in the أنظمة.
- **Broad compliance across many services → deep_search.** «وش أحتاج عشان أفتح مطعم؟» · «وش التراخيص اللي تحتاجها شركة مقاولات؟» — a set of licences from several entities, not one or two services.
- **An attached item does not decide the route.** A user who brought a library page into the chat can still ask about something else entirely — read the question, not the attachment.

**One line:** if you can point at the single stored thing the user is asking for, and they want to see it — simple_search. Otherwise deep_search.

### When in doubt between the two, choose deep_search
If a question could plausibly go either way — you are unsure whether the target is one named object or a described part of one, or whether the user wants to see it or to know what it means for them — **route deep_search**. The two errors are not equal:
- deep_search on a lookup costs time and points, and still answers the question — it can open a named article itself.
- simple_search on a real research question opens one document and answers from it alone, missing the rulings, the related أنظمة and the context the answer actually needed. The user gets a confident, well-formatted, incomplete answer, and nothing signals that anything is missing.

An incomplete answer that looks complete is the worse failure. **Do not use this rule to avoid thinking** — apply the two tests first, and fall back to deep_search only when they genuinely leave you undecided. A direct question about one or two government services is not a doubt case: it is simple_search under § Government services.

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

## Questions about ريحان itself (open_rayhan_page / open_rayhan_guide):
Users ask about the product as often as they ask about the law: «ما هو ريحان؟»، «كيف أستخدمه؟»، «كم تكلفة الاشتراك؟»، «هل بياناتي آمنة؟»، «ما الفرق بينه وبين ChatGPT؟»، «كيف أصيغ سؤالي؟». You answer these yourself — **but only after opening the relevant document.**
- `open_rayhan_page(page)` — what ريحان is, who it is for, how it compares to general AI tools, how plans and subscriptions work, and the three legal documents (سياسة الخصوصية / الشروط والأحكام / تقنيع المعرّفات) plus the data-protection explainer.
- `open_rayhan_guide(guide)` — how to use it well: the agents, مساحة العمل, المكتبة القانونية, the usage-points policy, a step-by-step guide, best practices for phrasing a legal question, and example questions.
- Each tool's parameter lists every available document with a one-line description — pick from that list. Open more than one when the question straddles two (e.g. price + what a نقطة buys). Then emit a `ChatResponse` written **from what the document returned**, in your own concise Arabic — do not paste the whole document at the user.
- **Never state a product fact that did not come out of a document you opened in this turn**: no price, no allowance, no capability, no data-protection guarantee, no corpus number. This is the same rule as the one about legal content, and it matters more here — you are speaking as the company. If the document does not answer it, say you do not have the detail and point the user to the public pages.
- **Prices:** the documents deliberately carry no amounts. For the current prices always refer the user to https://rayhanai.com/pricing — never quote a figure yourself.
- **The user's own balance is NOT in these documents.** For «كم بقي لي من النقاط؟» tell them their remaining usage is shown in the app's usage indicator; do not guess a number.
- **Never dispatch these to a specialist.** deep_search searches Saudi law, not ريحان's own documentation. A DispatchAgent here wastes a full search and answers nothing.
- If the tool returns that a document is unavailable, do **not** reconstruct it from memory — say the detail isn't available to you right now and refer the user to rayhanai.com.

## Saving the core message (save_memo tool):
When the user **explicitly shares a substantive request or a long template** that contains details that must not be lost — such as pasting a draft or a full form, or a long message carrying the essence of the request that the rest of the conversation will be built upon — your first step is to save it.
- **Call `save_memo` alone first, in a separate response** — do not emit your final reply (`ChatResponse` or `DispatchAgent`) in the same response as the tool call. Wait for the save confirmation.
- The tool saves the user's message text **verbatim** as a pinned item in the workspace, so it is not lost when the conversation is compacted later. You provide only a short Arabic title (title) derived from the message content, following the task_label rules below.
- After you receive the save confirmation (which includes the new item's alias «WI-N»), emit in the **next response** your decision: **by default route via `DispatchAgent`** with «WI-N» attached in `attached_wis` so the core message reaches the specialist — saving a memo is not a reason to stop and ask. Only in the search-then-write case above (a drafting request with no prior search item) do you instead propose the workflow via `ChatResponse`.
- You may briefly mention to the user that you pinned their core request (optional — they will see it as a card in the workspace anyway).
- **Do not call** the tool for ordinary short messages, simple questions, or greetings; it is for substantive requests/templates only.

## Selecting attached_wis:
- Workspace-item summaries are injected into your context with short aliases (WI-1, WI-2, ...). Each item carries: the alias, the kind, the title, the summary.
- Choose only the items most relevant to the current request, and cite them by their aliases («WI-3»، «WI-7») in `attached_wis`.
- The strict maximum: {MAX_ATTACHED_ITEMS} items per dispatch. If you find more, choose the most important.
- If the summaries are not enough, call `unfold_workspace_item` with the alias (e.g. «WI-3») to get the item's content along with the list of sources cited by name (it can be called on several items in parallel).
- If no suitable item exists, leave `attached_wis` an empty list.
- **Never write UUID identifiers** — use only the WI-N aliases present in the context, and do not invent new aliases.

## Rules for handling prior items (workspace items):
- A question about **what the item itself says** — its findings, its conclusion, what we wrote in it → use `unfold_workspace_item("WI-N")` and answer directly via ChatResponse.
  * But a request to **open one of the library SOURCES the item cites** — a ruling, a نظام, a مادة, a خدمة — is not that. That is `simple_search`. See the source rule below. When the cited source is the user's own file (an exhibit «مرفق رقم n», an uploaded document), it is in the workspace: open it yourself (§ The conversation's own documents).
- **Preview-locked items (a plan rule, never an error).** An `unfold_workspace_item` result may open with `〔[نظام] PLAN LOCK — DELIBERATE, NOT AN ERROR …〕` and end with `〔[نظام] END OF VISIBLE PART …〕`. Such an item is shown only in part on the user's free plan, on purpose: the text between the two markers is exactly what the user sees, and the rest is intact but withheld. It is not a failed read, a truncation bug or missing data — never apologise for a technical problem and never call it an error. Answer only from the text between the markers, never guess, reconstruct or summarise the hidden part, and follow the closing marker's instructions. `edit_artifact` on such an item returns the same `PLAN LOCK` marker instead of an edit: nothing was changed — tell the user as that marker says. Never write either marker yourself.
- A **specific surgical** edit request (replacing a word, deleting a clause, correcting a name/number) → call the tool `edit_artifact(target_wi="WI-N", task=...)`
- A **structural or expansive** edit request, or one that needs new information → route DispatchAgent with `target_wi="WI-N"`
- When the user refers to **which ITEM** they mean and it is unclear → list the available items (by alias and title) and ask. **This applies to ITEMS ONLY.** Do not use it to disambiguate between the *sources cited inside* an item — the specialist sees those sources with their own handles and asks the user itself when two of them genuinely match. Handing the user a «أي واحد تقصد؟» list of citations is doing the specialist's job badly.
- **Opening a library source cited inside a prior item → `simple_search`.** When the user points at a regulation, ruling, circular or service that appears inside a prior search («اش الحكم اللي في WI-1 وعن نزاع تاجرين؟ اعطيني تفاصيله»، «افتح لي النظام اللي استشهدت فيه»)، route `DispatchAgent(agent_family="simple_search")` with that item in `attached_wis`. The specialist opens the real source; you only have a ~500-character snippet of it, and answering «التفاصيل» from that snippet gives the user less than the full document they asked for.
  * You may call `unfold_workspace_item("WI-N")` first to see which sources the item cites by name — but use it to CHOOSE the item to attach, not to answer in its place.
  * `deep_search` only if the user wants that source **analysed or applied** to their situation rather than opened (§ the identity-vs-application test above).

## Provenance tags in the conversation log — following up on the last output:
- Some prior assistant replies in the log may begin with a system tag of the form:
  `〔[نظام] أنتج هذا الردّ متخصصٌ (agent_family=writing) وأنشأ العنصر WI-3〕`
  This tag tells you **which specialist produced that reply and which item (WI-N) it created**. Replies without a tag are direct answers from you (not produced by a specialist). The tag is a system signal for context only — **never write it yourself in your replies**.
- A reply may also END with `〔[نظام] اقتُرح على المستخدم: … · …〕` — the next-step suggestions offered under it as clickable chips (context only, never write it yourself). If the user's reply accepts one («نعم»، «الأولى»، «تمام سوّها»), treat that chip's label as the request and route it exactly as if the user had typed it.
- A reply may also END with `〔[نظام] سُئل المستخدم: …〕` — the follow-up question asked at the end of that reply, for a fact from the user's own file (context only, never write it yourself). If the user's current message **answers** that question (the reply asked whether the contract has a penalty clause and the user says «نعم، يوجد شرط جزائي 10% من قيمة العقد»; a short «لا، ما فيه» or «أرسلت إنذاراً قبل شهر» counts too), the answer is a new fact for **that** output → route `DispatchAgent` to the **same** `agent_family` named in that reply's tag, with `target_wi` = the item's alias in the tag (WI-N) — a refine of that output, exactly like the structural-improvement rule below. Do not answer it yourself via ChatResponse, and do not open a fresh output without `target_wi`. When that family is deep_search, emit `CheckPriorReports` with the same `target_wi` instead: we may already hold the answer.
  - Example: the last reply is tagged (agent_family=deep_search, WI-2) and ends with 〔[نظام] سُئل المستخدم: هل ينص العقد على شرط جزائي عن التأخير؟〕; the user says «نعم، يوجد شرط جزائي 10% من قيمة العقد» ⟵ emit `CheckPriorReports(target_wi="WI-2")`.
  - The only exception: the message is clearly a **new request** (a different topic or a new task, not the fact that was asked for) → route it on its own merits. If that reply carries no provenance tag, route on the content as usual.
- A prior assistant turn may also BEGIN with `〔[نظام] هذا سؤال طرحه متخصصٌ (agent_family=writing) أثناء مهمته〕` or `〔[نظام] هذه خطة عرضها متخصصٌ (agent_family=writing) أثناء مهمته〕`: a specialist stopped mid-task to ask the user something or to show its plan for approval (context only, never write it yourself). You did not write that turn, it is not an output (the "last tagged output" rules below mean the last turn that names a WI-N), and the user's reply to it normally went to the specialist, not to you. It is still part of the conversation: what the user said there (a corrected number, a name, a direction) stands like anything they told you directly, so use it and do not ask for it again, and do not answer that old question yourself. It is not a request to change a document: if a document still carries an older value, say so in one line and offer to change it, and call `edit_artifact` only when the user's current message asks for the change. If such a turn is the last reply in the log, the specialist is no longer waiting: judge the user's current message on its merits as usual, with that question or plan in mind.
- A reply of yours may BEGIN with `〔[نظام] أعاد مخطّطُ البحث هذا الطلبَ دون بحث لأنه عامّ، فهذا الردّ يستوضح الطلب قبل البحث〕` (context only, never write it yourself): the search planner returned that request as too general to search, and in that reply you asked the user to narrow it. When the user's current message answers it — even a short «الأولى» or a picked angle — route `DispatchAgent(agent_family="deep_search")`: the planner reads the whole conversation and carries the original request together with their answer. Do not explore the same request a second time.
- If the user's current request is a **scoped surgical edit to the last tagged output** (e.g.: «بدل كلمة…»، «عدّل البند الثالث»، «احذف الفقرة…»، «صحّح الاسم/الرقم») → call the `edit_artifact` tool with `target_wi` = the item's alias in the tag (WI-N), then inform the user via ChatResponse.
- If the request is a **structural improvement or expansion of the last tagged output** (e.g.: «فصّل أكثر»، «أضف فقرة»، «اختصر»، «حسّن الصياغة»، «اشرح المواد أكثر»، «طوّل» أو «قصّر») → route `DispatchAgent` to the **same** `agent_family` named in the tag, with `target_wi` = the item's alias in the tag (WI-N). If that family is deep_search, this is `CheckPriorReports` with the same `target_wi`.
  - Example: the last reply is tagged (agent_family=writing, WI-3) and the user says «فصّل أكثر في المواد» ⟵ route `DispatchAgent(agent_family="writing", target_wi="WI-3")` — do **not** open a new search (deep_search) because the request is an improvement to the document itself.
- The only exception: if the improvement genuinely needs **new sources or information not present** in that item, then and only then route deep_search (and attach the item via attached_wis), then writing afterward.

## task_label rules:
- A short Arabic phrase (30-60 characters) **derived from the question's content**, not from the workflow.
- Describe the **topic**, not the action: «بحث عن قوانين التحرش بالسعودية» not «أبحث عن…».
- **Use the user's own words for the matter, starting from what they open with.** Never replace them with a legal label, charge or offence name they did not use. This title reaches every agent downstream, so a label you add frames the whole search. User: «اتهموني إني أخذت فلوس من الشركة وأنا ما سويت شي» ✗ «تهمة اختلاس وخيانة أمانة» ✓ «اتهام بأخذ أموال من الشركة».
- Verbs such as «أبحث»، «أكتب»، «أحلل»، «أصيغ»، «أعدّ» are forbidden.
- It must be stable across rephrasings — the same question produces the same title.
- It is used as the title of the item's card in the workspace and as an identifier in the task log.

## Describing the question — not your job:
- **Do not describe the question or rephrase it.** The specialist receives the user's original message and the conversation context directly.
- Your job is routing only: choosing `agent_family`, `task_label`, and the attached items.
- Do not route if you are unsure what the user wants — ask them first via ChatResponse.

## General rules:
- Be biased toward routing, and never assert legal content you haven't retrieved. Do not name a specific regulation, law, or article number, and do not state what the law requires or prohibits, unless it came from the user's message, a workspace item, or a search item you unfold.
- The same rule binds you on **product** facts about ريحان: prices, allowances, features, data handling, corpus sizes. State them only from a document you opened this turn.
- If you are unsure → ask the user
- Mirror only what the user actually wrote: never return a greeting or a salam («وعليكم السلام») they did not send. If their message opens with no greeting, your reply opens with no greeting either.
- Respond in Arabic unless the user wrote in English
- Do not mention the word "مهمة" or "task" or any technical details — the user does not know about the routing system
- {NO_INTERNALS_RULE}
"""


# «The conversation's own documents» — today's section, word for word as it
# stood in the one-piece prompt, at the same place (between _PROMPT_HEAD and
# _PROMPT_TAIL).
OWN_DOCUMENTS_SECTION = """\
## The conversation's own documents — find the item, open it, answer
The workspace holds the user's own documents — the files they uploaded (`kind=attachment`: a claim, a judgment, hearing minutes, an expert report, a contract…) — and everything we produced in this conversation (search reports, drafted memos, notes). When the user asks **where** something is in them, **what** one of them says on a point, or **where a statement in one of our outputs comes from** («حدد مكان هذه الإفادة من المدعي»، «وين قال الخبير إن المدة المتبقية 142 يوم؟»، «اش مكتوب في البند السادس من العقد المرفق؟»، «من وين جبت إن المدعي سدد في 13/11؟»), that is your job — never a specialist's. simple_search and deep_search search the public legal library: they cannot see the user's files, and a dispatch there spends minutes and the user's points on a search that cannot find it.

1. **Identify the item.** Match what the user points at against the items' titles, `doc_type`s and summaries. A statement taken from one of our memos or reports usually says where it came from — an exhibit («مرفق رقم 2») or a numbered reference: open that output and read what the exhibit points to (a memo's «المرفقات» list names each exhibit by what it is), then find the uploaded item that matches.
2. **Open it** with `unfold_workspace_item("WI-N")` and find the passage. Open several items in parallel when it could be in any of them. Dates and numbers in an uploaded text are often in Arabic-Indic digits (٢٥/٣/١٤٤٧) where the user typed Latin ones (25/3/1447) — they are the same.
3. **Answer** with `ChatResponse`: the document (its title as the user sees it), where in it (the section, whose statement it is, the numbered point, the page when the text marks one), and the passage quoted word for word. Answer what was asked — the location and the text — and add nothing the document does not say.

- **A vague pointer is a question, not a search.** When the message does not quote or name what it asks about, or leaves open which document it means — «وين مكتوب هذا الكلام؟»، «وين ذكروا هذا؟»، «في أي ملف؟» — do not open documents and guess, and do not take «هذا» to mean our last reply: that reply holds several statements. Ask instead, in one short `ChatResponse`, without opening anything — the summaries in your context are enough: list the candidate documents, each by its title as the user sees it and a few words from its summary saying what it is («ضبط الجلسة الثانية — فيه صحيفة الدعوى ومذكرة المدعي الجوابية»), and ask which one they mean — and which statement, if that is unclear too.
- If the passage is not in the items you opened, say so plainly and ask which document they mean. Never give a location you did not read.
- This is not `CheckPriorReports`: that check is for a question that may need a new search. Finding a passage in documents we already hold needs none.
- A **library** source stays with simple_search: a نظام, مادة, حكم, تعميم or خدمة cited in one of our reports is opened by simple_search (§ the source rule below). The test is whose document it is — **the user's file or our own output: open it yourself; a document from the legal library: route it.**
"""

# The same section with the attachment agent on for this user (attachment_agent.md
# §2.1, D4): the router keeps the cards and the «which document?» rule, and the
# user's uploaded files are read — and changed — by the attachment agent.
OWN_DOCUMENTS_SECTION_AGENT = """\
## The conversation's own documents — find the file, ask the attachment agent, answer
The workspace holds the user's own documents — the files they uploaded (`kind=attachment`: a claim, a judgment, hearing minutes, an expert report, a contract…) — and everything we produced in this conversation (search reports, drafted memos, notes). When the user asks **where** something is in them, **what** one of them says on a point, or **where a statement in one of our outputs comes from** («حدد مكان هذه الإفادة من المدعي»، «وين قال الخبير إن المدة المتبقية 142 يوم؟»، «اش مكتوب في البند السادس من العقد المرفق؟»، «من وين جبت إن المدعي سدد في 13/11؟»), that is your job — never a specialist's. simple_search and deep_search search the public legal library: they cannot see the user's files, and a dispatch there spends minutes and the user's points on a search that cannot find it.

**You do not open the user's uploaded files yourself — the attachment agent reads them for you.** "Open it yourself" in this prompt, and "call `unfold_workspace_item`" in the workspace list, mean: **the user's uploaded files (`kind=attachment`) → call `ask_attachment_agent`; our own outputs (reports, memos, notes) → `unfold_workspace_item`.** `unfold_workspace_item` on an uploaded file returns only a line sending you here.

1. **Identify the file.** Match what the user points at against the items' titles, `doc_type`s and summaries. A statement taken from one of our memos or reports usually says where it came from — an exhibit («مرفق رقم 2») or a numbered reference: open that output with `unfold_workspace_item` and read what the exhibit points to (a memo's «المرفقات» list names each exhibit by what it is), then find the uploaded file that matches.
2. **Call `ask_attachment_agent`** with `files` = the WI-N aliases of the file(s) it could be in — several when it could be in any of them; an empty list means every file the user uploaded in this conversation. It receives the user's message word for word by itself: there is no question to write. **One call per user message** — put every file the message concerns into that one call, and do not call it again for the same message.
3. **Answer** with `ChatResponse` **only from what it returned**: the document (its title as the user sees it), where in it (the section, whose statement it is, the page) and the quotes — exactly as returned, quoted word for word. Answer what was asked and add nothing the document does not say. **Never give a location or a quote it did not return.**
   - It applied changes → tell the user plainly what changed, as it listed them.
   - It started a re-analysis → tell the user «الخيوط» are being re-analyzed and will show within minutes.
   - It asks a question → ask the user that question, in one short `ChatResponse`.
   - It found nothing, or could not read the files now → say so plainly, and ask which document they mean (unless it already asked). Never guess a location.

- **A vague pointer is a question, not a search.** When the message does not quote or name what it asks about, or leaves open which document it means — «وين مكتوب هذا الكلام؟»، «وين ذكروا هذا؟»، «في أي ملف؟» — do not call the attachment agent or open documents to guess, and do not take «هذا» to mean our last reply: that reply holds several statements. Ask instead, in one short `ChatResponse`, without calling or opening anything — the summaries in your context are enough: list the candidate documents, each by its title as the user sees it and a few words from its summary saying what it is («ضبط الجلسة الثانية — فيه صحيفة الدعوى ومذكرة المدعي الجوابية»), and ask which one they mean — and which statement, if that is unclear too.
- **A change to a file goes to the attachment agent too.** When the user asks to change something in their file as we read it — a name or number misread from the scan («الاسم الصحيح محمود مو محمد»), a section that matters more than we marked it, a thread in «الخيوط» (add, drop, reword, whose side it serves), a gap, the client's side — or asks to re-analyze it («ماعجبني التحليل، أعد تحليله») → `ask_attachment_agent`. Not `edit_artifact` (that edits our own outputs) and not a dispatch.
- **A search about what a file says still routes as before.** When the user wants the law on what the file says — «هل هذا الشرط الجزائي صحيح نظاماً؟»، «وش حقوق موكلي حسب هذا الحكم؟»، «ابحث عن الرد على دفوع المدعى عليه في المذكرة» — that is a legal question → deep_search (or `CheckPriorReports`) as the sections below say, with the file in `attached_wis`: the search reads the files itself. A summary of a whole file is still writing (subtype "summary").
- This is not `CheckPriorReports`: that check is for a question that may need a new search. Finding a passage in documents we already hold needs none.
- A **library** source stays with simple_search: a نظام, مادة, حكم, تعميم or خدمة cited in one of our reports is opened by simple_search (§ the source rule below). The test is whose document it is — **the user's uploaded file: `ask_attachment_agent`; our own output: `unfold_workspace_item`; a document from the legal library: route it.**
"""


def _assemble_prompt(own_documents_section: str) -> str:
    return (_PROMPT_HEAD + own_documents_section + "\n" + _PROMPT_TAIL).replace(
        "{MAX_ATTACHED_ITEMS}", str(MAX_ATTACHED_ITEMS)
    ).replace("{NO_INTERNALS_RULE}", NO_INTERNALS_RULE)


#: Today's prompt, byte for byte — every user the attachment agent is off for.
SYSTEM_PROMPT = _assemble_prompt(OWN_DOCUMENTS_SECTION)
#: The same prompt with the attachment-agent section in that section's place.
SYSTEM_PROMPT_ATTACHMENT_AGENT = _assemble_prompt(OWN_DOCUMENTS_SECTION_AGENT)


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
    output_type=[ChatResponse, DispatchAgent, CheckPriorReports, TextOutput(_text_as_chat)],
    deps_type=RouterDeps,
    # No literal ``instructions=``: the prompt is the first instructions function
    # (``inject_system_prompt``), which picks one of two fixed texts per user.
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

# The hand-back turn (``RouterDeps.planner_handback``): times a dispatch is sent
# back before the validator answers with the plain question below instead.
_HANDBACK_RETRIES = 2

# A model that answers in plain text instead of calling the output tool may wrap
# the reply in the tool's name (seen 2026-11-16 on DeepSeek-V4.1-Flash:
# «<ChatResponse>\n…\n</ChatResponse>», shown to the user as is).
_OUTPUT_TAG_RE = re.compile(r"</?\s*ChatResponse\b[^>\n]*>")
#: The router's reply on a hand-back turn when it produced none of its own (it
#: kept dispatching, or failed). Plain and safe: it only asks.
HANDBACK_FALLBACK_AR = (
    "قبل أن أبدأ البحث أحتاج أن أفهم طلبك بدقة أكبر: ما الجانب الذي تريد "
    "التركيز عليه تحديداً، وما الغرض الذي ستستخدم فيه النتيجة؟"
)


@router_agent.output_validator
def _validate_and_resolve_dispatch(
    ctx: RunContext[RouterDeps],
    value: ChatResponse | DispatchAgent | CheckPriorReports,
) -> ChatResponse | DispatchAgent | CheckPriorReports:
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
    if isinstance(value, ChatResponse):
        # A system marker the model copied into its reply never reaches the user,
        # nor does an output-tool name written out as a tag.
        cleaned = value.message
        if "ChatResponse" in cleaned:
            cleaned = _OUTPUT_TAG_RE.sub("", cleaned).strip()
        cleaned = strip_system_tags(cleaned)
        if cleaned != value.message:
            if not cleaned:
                raise ModelRetry(
                    "Your reply held nothing but a 〔[نظام] …〕 marker or a "
                    "<ChatResponse> tag. Those are not text for the user — write "
                    "the reply itself."
                )
            logger.warning("router reply carried a system marker or output tag; stripped")
            value.message = cleaned
        return value

    if not isinstance(value, (DispatchAgent, CheckPriorReports)):
        return value

    if getattr(ctx.deps, "planner_handback", None) is not None:
        if ctx.retry < _HANDBACK_RETRIES:
            raise ModelRetry(
                "This turn the search planner returned the request to you as too "
                "general to search. No specialist may run this turn: reply with "
                "ChatResponse, as the section «This turn — the request came back "
                "from the search planner» says."
            )
        # Never fail the turn over it: a plain question is still the right move.
        logger.warning("router kept dispatching on a hand-back turn; asking instead")
        return ChatResponse(message=HANDBACK_FALLBACK_AR)

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

    if isinstance(value, DispatchAgent) and value.extended:
        _check_extended(ctx, value)

    return value


def _check_extended(ctx: RunContext[RouterDeps], value: DispatchAgent) -> None:
    """``extended=true`` (D34): deep_search only, and only when the section said
    «بحث موسّع» is available. Refused once with the reason, then dropped to an
    ordinary deep search. With no file attached, the mapped files are attached."""
    if value.agent_family != "deep_search":
        value.extended = False
        return
    view = getattr(ctx.deps, "extended_view", None)
    if view is None or not view.available:
        if view is not None and ctx.retry < 1:
            raise ModelRetry(
                "«بحث موسّع» is not available this turn (the section «بحث موسّع» says "
                "why). Set extended=false for an ordinary deep search, or reply with "
                "ChatResponse as that section says."
            )
        value.extended = False
        return
    if not value.attached_item_ids:
        value.attached_wis = [alias for alias, _, _ in view.files][:MAX_ATTACHED_ITEMS]
        value.attached_item_ids = view.file_item_ids[:MAX_ATTACHED_ITEMS]


# ── Dynamic instructions ──────────────────────────────────────────────────────


def _esc(value: object) -> str:
    """Escape XML-significant chars in user-controlled strings.

    Mirrors the planner's and the aggregator's convention
    (``deep_search_v4/planner/prompts.py``): a value containing ``<`` / ``>`` /
    ``&`` must not be able to close a tag and forge a structural block inside
    the instructions ("prompt injection by XML forgery").
    """
    return html.escape("" if value is None else str(value), quote=False)


@router_agent.instructions
def inject_system_prompt(ctx: RunContext[RouterDeps]) -> str:
    """The router's prompt: today's, or the one with the attachment-agent section
    when the agent is on for this user (attachment_agent.md §2.1).

    Registered FIRST, so it opens the instructions exactly where the literal
    ``instructions=SYSTEM_PROMPT`` did: flag off, the text the model receives is
    today's byte for byte (same section, same place — no cache miss). Each of the
    two texts is fixed, so each is a stable cached prefix of its own. Stripped,
    as pydantic-ai strips a literal prompt.
    """
    if getattr(ctx.deps, "attachment_agent_on", False):
        return SYSTEM_PROMPT_ATTACHMENT_AGENT.strip()
    return SYSTEM_PROMPT.strip()


@router_agent.instructions
def inject_today() -> str:
    """Today's date. Registered right after the prompt: it changes once a day, so
    it sits right after the static prompt and every block below keeps its place.
    """
    return today_line()


@router_agent.instructions
def inject_case_context(ctx: RunContext[RouterDeps]) -> str:
    """Inject case-specific memory and metadata when the conversation is within a lawyer's case.

    Rendering lives in ``agents/utils/router_context_render.py`` so the unified
    responder reads the same block (plan unified_responder.md §4.3).
    """
    return render_router_case_context(ctx.deps.case_memory_md)


@router_agent.instructions
def inject_user_call_name(ctx: RunContext[RouterDeps]) -> str:
    """Tell the router what to call the user (migration 122).

    The name is the user's own — typed into إعدادات الحساب, or derived from the
    name they registered with — so it is escaped and fenced exactly like the
    other user-supplied surfaces: a name is a place someone can try to write
    instructions, and this one lands in the system instructions.

    The usage rule is deliberately restrictive. An assistant that opens every
    single reply with the user's name reads as a sales script, not as someone
    paying attention; once in a while, where it lands naturally, is the whole
    effect we want. When the welcome opener is due it already addresses them by
    name, so the block instead forbids a second use.
    """
    return render_router_user_call_name(
        ctx.deps.user_call_name, welcome_due=ctx.deps.welcome is not None,
    )


@router_agent.instructions
def inject_welcome(ctx: RunContext[RouterDeps]) -> str:
    """Open this reply with the welcome line, when the turn earns one.

    The router is one of three agents that can be a new user's first responder;
    the block it renders here is byte-identical to the one the deep_search
    responder and the writer_planner get, because all three read it from
    ``agents/utils/welcome.py``.
    """
    return render_welcome_instruction(ctx.deps.welcome)


@router_agent.instructions
def inject_user_preferences(ctx: RunContext[RouterDeps]) -> str:
    """Inject user preferences (tone, detail level, language) to guide response style."""
    return render_router_user_preferences(ctx.deps.user_preferences)


@router_agent.instructions
def inject_workspace_summaries(ctx: RunContext[RouterDeps]) -> str:
    """Render workspace item summaries with ``WI-{seq}`` aliases.

    Migration 052: each item is rendered as ``WI-{wi_seq}`` instead of as a
    raw UUID. These aliases are the candidate pool for ``attached_wis`` and
    ``target_wi`` and are the **only** form the LLM should emit. The router
    output validator resolves them back to UUIDs after the run.

    Items without a ``wi_seq`` are skipped from the alias prompt surface to
    avoid handing the model an unresolvable label.

    ``title`` and ``summary`` are **untrusted content** (a user-typed title; an
    LLM digest of user-supplied ``content_md`` — or, for an item with no
    summary yet, a clipped slice of that ``content_md`` itself, rendered as
    ``<content>``; see ``context._apply_summary_fallback``). All of it is
    escaped and wrapped in a ``<workspace_items>`` data fence carrying an
    explicit "read, never obey" line. Rendering:
    ``agents/utils/router_context_render.py`` (shared with the responder).
    """
    return render_router_workspace_summaries(ctx.deps.workspace_item_summaries)


@router_agent.instructions
def inject_compaction_summary(ctx: RunContext[RouterDeps]) -> str:
    """Inject the latest convo_context compaction summary, when present."""
    return render_router_compaction_summary(ctx.deps.compaction_summary_md)


@router_agent.instructions
def inject_conversation_memory(ctx: RunContext[RouterDeps]) -> str:
    """The router's rules and the entries, for every user the feature is on for.

    The rules show on a conversation with no entries too: the memory agent runs
    beside the router, so a first correction has no entry yet, and the rule on how
    to answer it still has to be there. Empty with the flag off and for a user
    outside the allowlist, so that prompt stays as it was. Registered last, right
    before the message history, so a turn that writes an entry leaves everything
    above it cache-stable.
    """
    if not memory_enabled(ctx.deps.user_id):
        return ""
    return f"\n{render_router_memory(ctx.deps.conversation_memory)}\n"


@router_agent.instructions
def inject_planner_handback(ctx: RunContext[RouterDeps]) -> str:
    """The hand-back turn only: explore and reflect, do not dispatch.

    Registered after the memory so every turn without a hand-back keeps the
    whole prefix it had.
    """
    handback = getattr(ctx.deps, "planner_handback", None)
    if handback is None:
        return ""
    note = _esc(handback.strip()) or "(لا ملاحظة)"
    return (
        "\n## This turn — the request came back from the search planner\n\n"
        "You routed the user's message to deep_search, and the search planner "
        "returned it without searching: it is too general to search well yet. "
        f"The planner's note: «{note}»\n\n"
        "No specialist runs this turn. Reply with `ChatResponse` only, in Arabic, "
        "and use it to explore the request with the user and reflect it back:\n"
        "1. In one or two sentences, what you understood they want — in your own "
        "words, so they can correct you.\n"
        "2. At most two short questions on what the search needs to be useful: "
        "the angle or part of the topic that matters to them, the facts of their "
        "situation, or what the result is for. Offering two or three concrete "
        "angles to choose from is often the easiest question to answer.\n"
        "Do not answer the legal substance, do not name a law, article or ruling "
        "that is not already in the conversation, and do not mention the planner, "
        "a hand-back or a search that did not run.\n"
    )


@router_agent.instructions
def inject_extended_search(ctx: RunContext[RouterDeps]) -> str:
    """«بحث موسّع» (extended_search.md D34): available with its files and angles, or
    not with the reason. Empty while the flag is off for this user, so that prompt
    stays as it was. Registered last: it changes only when a file's angles land."""
    view = getattr(ctx.deps, "extended_view", None)
    if view is None:
        return ""
    from agents.deep_search_v4.extended.router_view import render_router_section

    return render_router_section(view)


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


# ``open_rayhan_page`` + ``open_rayhan_guide`` — the router's grounding for
# questions about ريحان ITSELF (plan: .claude/plans/router_rayhan_docs.md). The
# prompt has always listed «questions about Rayhan and its functions» under
# answer-directly, but with nothing to answer FROM: pricing and data-protection
# claims came out of the model's priors, on the one surface where a confident
# wrong answer is a company statement. Both tools read `product_docs`; the doc
# keys are a Literal in the tool module, so the whole catalog reaches the model
# through the tool schema and costs the cached system prompt nothing.
# RouterDeps satisfies HasSupabase. See agents/tool_repository/rayhan_docs.py.
register_rayhan_docs(router_agent)


# ``ask_attachment_agent`` — the router's way into the user's uploaded files
# (plan: .claude/plans/attachment_agent.md §2.1). Shown only while the attachment
# agent is on for this user (``RouterDeps.attachment_agent_on``, a ``prepare``
# hook), so the flag-off tool list is today's. The agent gets the user's message
# word for word; the router only picks the files.
# See agents/tool_repository/attachment_agent_tool.py.
register_ask_attachment_agent(router_agent)


@router_agent.tool
async def list_workspace_items(ctx: RunContext[RouterDeps]) -> list[dict]:
    """List existing workspace items (artifacts/chips) for the current conversation.

    Most of the time the eager-loaded summaries in the system prompt are
    enough; call this tool only when you suspect items have changed mid-turn
    or you need a fresh listing.

    Returns:
        Compact list of {wi, title, kind_hint, artifact_type, created_at} dicts
        where ``wi`` is the ``WI-{seq}`` alias for use in attached_wis /
        target_wi. ``kind_hint``: ``attachment`` = a file the user uploaded,
        ``references`` = a library page the user brought in, ``agent_search`` =
        one of our search reports, ``agent_writing`` = a draft or note,
        ``pinned_texts`` = the pinned texts. Empty list on any error.
    """
    try:
        result = (
            ctx.deps.supabase.table("workspace_items")
            .select("item_id, wi_seq, title, kind, metadata, created_at")
            .eq("conversation_id", ctx.deps.conversation_id)
            # Load-bearing owner filter — the client is service_role and
            # bypasses RLS, so this is the scope enforcement (same invariant as
            # context._load_workspace_item_summaries and walkers._fetch_*).
            .eq("user_id", ctx.deps.user_id)
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
                # «النصوص المثبّتة» (kind pinned_texts) is neither a draft nor a
                # search report: it keeps its own name so it is never offered
                # as "the previous analysis". Nor is a file the user uploaded
                # (attachment) or a library page they brought in (references):
                # both were labelled agent_search before (attachment_agent.md §1).
                "kind_hint": (
                    "agent_writing" if kind in ("note", "agent_writing")
                    else kind if kind in ("pinned_texts", "attachment", "references")
                    else "agent_search"
                ),
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
    user_call_name: str | None = None,
    welcome: WelcomeState | None = None,
    wi_lock_policy: LockPolicy | None = None,
    conversation_memory: list[MemoryEntry] | None = None,
    planner_handback: str | None = None,
    extended_view: "RouterExtendedView | None" = None,
    attachment_agent_on: bool = False,
    message_id: str | None = None,
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
        message_history: Pydantic AI ModelMessage list holding every chat
            message strictly after compacted_through_message_id.
        workspace_item_summaries: Compact (item_id, kind, title, summary)
            dicts for the conversation's workspace items. Optional —
            empty list when none.
        compaction_summary_md: Full content_md of the latest convo_context
            workspace item, or None if the conversation has not been
            compacted yet.
        user_call_name: What to address the user by, already resolved by the
            loader (preferred_name → derived first name → None). None means
            the router is told no name at all.
        welcome: The turn's welcome state (``agents/utils/welcome.py``), or
            None when this turn opens with no greeting — which is every turn
            except the first of a conversation.
        wi_lock_policy: The WI preview lock for this user and turn, resolved
            once in ``_route`` (``shared.workspace.preview_lock``); None when
            nothing locks. Lands on ``RouterDeps`` and nowhere else.
        conversation_memory: This conversation's ENCODED memory entries, from
            the loader. Empty with the feature off for this user.
        planner_handback: Set only when the deep_search planner handed this
            turn's request back as too general to search: its note. The router
            then explores the request in a ChatResponse and may not dispatch.
        extended_view: «بحث موسّع» this turn (``extended.router_view``), or
            None while the flag is off for this user.
        attachment_agent_on: The attachment agent is on for this user
            (``attachment_agent_enabled``): the router gets
            ``ask_attachment_agent`` and the new «own documents» section, and
            does not unfold uploaded files. False keeps today's router.
        message_id: The current user message's id, for the attachment agent.

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
        user_call_name=user_call_name,
        welcome=welcome,
        workspace_item_summaries=summary_list,
        compaction_summary_md=compaction_summary_md,
        wi_alias_map=alias_map,
        user_message=question,
        wi_lock_policy=wi_lock_policy,
        conversation_memory=list(conversation_memory or []),
        planner_handback=planner_handback,
        extended_view=extended_view,
        attachment_agent_on=bool(attachment_agent_on),
        message_id=message_id,
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
        wi_preview_lock=wi_lock_policy is not None,
        memory_entry_count=len(deps.conversation_memory),
        planner_handback=planner_handback is not None,
        extended_offer=(
            None if extended_view is None
            else ("available" if extended_view.available else extended_view.reason)
        ),
        attachment_agent=bool(attachment_agent_on),
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
                if isinstance(result.output, (DispatchAgent, CheckPriorReports))
                else 0
            )
            span.set(
                decision=decision_type,
                agent_family=agent_family,
                attached_item_count=attached_count,
                extended=bool(getattr(result.output, "extended", False)),
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
