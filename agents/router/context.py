"""Router context loader — assembles eager input context for ``run_router``.

The router needs five pieces of context every turn:

0. The user's call name — what to address them by (migration 122), so a
   direct reply can be personal rather than anonymous.


1. Case metadata + case memories (when ``case_id`` is set)
2. Workspace item summaries — compact ``(item_id, kind, title, summary)``
   dicts used to populate ``DispatchAgent.attached_item_ids``. The full
   ``content_md`` is fetched on demand via the ``unfold_workspace_item`` tool.
3. Compaction summary — full ``content_md`` of the latest ``convo_context``
   workspace item, when one exists.
4. Recent messages — strictly after ``conversations.compacted_through_message_id``
   (or all messages if the cutoff is NULL), with ``agent_question`` and
   ``agent_answer`` metadata kinds excluded (they are reserved for Tier-2
   pause/resume Q&A audit trail; they are not router prompt material).

This module is the single source of truth for what the router sees per
turn. The orchestrator imports ``load_router_context`` and forwards the
resulting fields to ``run_router``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from supabase import Client as SupabaseClient

from agents.utils.history import messages_to_history
from pydantic_ai.messages import ModelMessage
from shared.identity import resolve_call_name

logger = logging.getLogger(__name__)


_EXCLUDED_MESSAGE_KINDS = {"agent_question", "agent_answer"}

# --- Summary-less items: fall back to the item's own content -----------------
#
# The router's ONLY window onto a workspace item is (title, summary) — the full
# ``content_md`` is behind the ``unfold_workspace_item`` tool. So an item whose
# ``summary`` is NULL reached the model as a bare filename plus the literal
# "(لا يوجد ملخص بعد)".
#
# That is exactly the state a freshly-OCR'd SHORT attachment lands in:
# ``summarize_workspace_item`` skips the LLM below ``MIN_CONTENT_LENGTH_CHARS``
# (300) on the stated grounds that "short blurbs don't need an agent-facing
# summary — downstream agents read content_md directly". True of every
# downstream agent except the router, which never reads it. On 2026-08-27 a
# user attached a screenshot carrying their whole question (262 OCR chars),
# typed «ابحث», and the router — which had correctly waited for OCR — answered
# "what would you like me to search for?" because all it could see was
# "Screenshot ....png" (conversation 12afc227).
#
# Fix: when an item has no summary, hand the router the item's own content,
# clipped. Also covers the rarer case of a summarizer that errored out.
_SUMMARY_FALLBACK_ITEM_CHARS = 1500
# Total across all summary-less items in one conversation, so a workspace full
# of them cannot inflate the router prompt without bound (``content_md`` is
# user-supplied and runs to 200k chars). Spent newest-first: the item the
# current turn is about is the one just uploaded.
_SUMMARY_FALLBACK_TOTAL_CHARS = 6000


@dataclass
class RouterContext:
    """Bundle returned by ``load_router_context``."""

    case_memory_md: str | None = None
    case_metadata: dict | None = None
    user_preferences: dict | None = None
    # What to call the user — their «بماذا تحب أن نناديك؟» answer, else a first
    # name derived from the registration / Google name (migration 122). None
    # when we have no usable name, in which case the router is told nothing.
    user_call_name: str | None = None
    workspace_item_summaries: list[dict] = field(default_factory=list)
    compaction_summary_md: str | None = None
    message_history: list[ModelMessage] = field(default_factory=list)


def _load_case_block(
    supabase: SupabaseClient, case_id: str, user_id: str
) -> tuple[dict | None, str | None]:
    """Return (case_metadata, case_memory_md) for ``case_id`` owned by ``user_id``.

    Both the ``lawyer_cases`` lookup and the ``case_memories`` lookup are
    scoped explicitly:

    - ``lawyer_cases`` filters on ``case_id`` AND ``lawyer_user_id = user_id``
      (the column is ``lawyer_user_id`` per the schema, not ``user_id``).
    - ``case_memories`` joins via ``case_id`` (already user-scoped via the
      ``lawyer_cases`` foreign key).

    These filters are **load-bearing** (§6.4 of the redesign spec): the
    backend's Supabase client runs as ``service_role`` and bypasses RLS, so
    the explicit ``.eq("lawyer_user_id", user_id)`` is the actual scope
    enforcement — not a defense-in-depth supplement to RLS.

    ``case_memories`` stores text in ``content_ar`` / ``content_en`` (NOT a
    single ``content`` column — Luna's Arabic-first policy keeps the Arabic
    text as the primary memory body). We select both and prefer ``content_ar``
    when rendering, falling back to ``content_en`` only when ``content_ar``
    is empty.
    """
    case_metadata: dict | None = None
    try:
        case_row = (
            supabase.table("lawyer_cases")
            .select("case_name, case_type, status, parties, description")
            .eq("case_id", case_id)
            .eq("lawyer_user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
        if case_row and getattr(case_row, "data", None):
            case_metadata = case_row.data
    except Exception as e:
        logger.warning("load_router_context: lawyer_cases load failed: %s", e)

    # Ownership gate — fail closed. The ``lawyer_cases`` lookup above is the
    # ONLY owner check in this function: ``case_memories`` has no user column,
    # it is user-scoped solely through its ``case_id`` foreign key. An empty
    # ``case_metadata`` means the case is not this user's, is deleted, or the
    # lookup errored — in every one of those cases the memories below must not
    # load, because they are rendered verbatim into the router's instructions
    # and into ``case_brief`` (planner → executors → aggregator).
    #
    # This is not hypothetical: ``conversations.case_id`` is writable by hand
    # (Studio), and on 2026-08-17 a production conversation carried a case
    # belonging to a different account. Without this gate that turn would have
    # rendered the other account's case memories into the prompt.
    if case_metadata is None:
        return None, None

    memories: list[dict] = []
    try:
        mem_resp = (
            supabase.table("case_memories")
            .select("content_ar, content_en")
            .eq("case_id", case_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=False)
            .execute()
        )
        memories = (mem_resp.data if mem_resp and getattr(mem_resp, "data", None) else []) or []
    except Exception as e:
        logger.warning("load_router_context: case_memories load failed: %s", e)

    case_memory_md: str | None = None
    parts: list[str] = []
    if case_metadata:
        parts.append(
            "### معلومات القضية\n\n"
            f"**اسم القضية:** {case_metadata.get('case_name', '')}\n"
            f"**نوع القضية:** {case_metadata.get('case_type', '')}"
        )
    if memories:
        rendered_lines: list[str] = []
        for m in memories:
            content_ar = (m.get("content_ar") or "").strip()
            content_en = (m.get("content_en") or "").strip()
            text = content_ar or content_en
            if text:
                rendered_lines.append(f"- {text}")
        if rendered_lines:
            parts.append(
                "### الوقائع والمعلومات المحفوظة\n\n"
                + "\n".join(rendered_lines)
            )
    if parts:
        case_memory_md = "\n\n".join(parts)
    return case_metadata, case_memory_md


def _load_user_preferences(
    supabase: SupabaseClient, user_id: str
) -> dict | None:
    try:
        prefs_row = (
            supabase.table("user_preferences")
            .select("preferences")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        if prefs_row and getattr(prefs_row, "data", None):
            return prefs_row.data.get("preferences")
    except Exception as e:
        logger.warning("load_router_context: user_preferences load failed: %s", e)
    return None


def _load_user_call_name(
    supabase: SupabaseClient, user_id: str
) -> str | None:
    """Return the name the router should address this user by, or None.

    Resolution lives in :func:`shared.identity.resolve_call_name` so the
    settings dialog (via ``GET /auth/me``) and the router can never disagree
    about what the user is called.
    """
    try:
        row = (
            supabase.table("users")
            .select("preferred_name, full_name_ar")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        if row and getattr(row, "data", None):
            return resolve_call_name(
                row.data.get("preferred_name"), row.data.get("full_name_ar")
            )
    except Exception as e:
        logger.warning("load_router_context: user name load failed: %s", e)
    return None


def _load_workspace_item_summaries(
    supabase: SupabaseClient, conversation_id: str, user_id: str
) -> tuple[list[dict], str | None]:
    """Return (summaries, compaction_summary_md).

    Workspace items are filtered to exclude ``convo_context`` from the
    summaries list — that one's full ``content_md`` is returned separately
    as the compaction summary.

    The ``.eq("user_id", user_id)`` filter is **load-bearing**, exactly as in
    ``_load_case_block``: this client runs as ``service_role`` and bypasses
    RLS, so the explicit owner filter is the scope enforcement. Titles and
    summaries loaded here are rendered verbatim into the router's dynamic
    instructions, so a row belonging to another user reaching this list is a
    prompt-injection surface — not merely a data leak.
    """
    try:
        resp = (
            supabase.table("workspace_items")
            .select("item_id, wi_seq, kind, title, summary, content_md, created_at")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=False)
            .execute()
        )
        rows = (resp.data if resp and getattr(resp, "data", None) else []) or []
    except Exception as e:
        logger.warning(
            "load_router_context: workspace_items load failed for %s: %s",
            conversation_id, e,
        )
        return [], None

    summaries: list[dict] = []
    compaction_summary_md: str | None = None
    latest_compaction_at: str = ""

    for row in rows:
        kind = row.get("kind") or ""
        if kind == "convo_context":
            created = row.get("created_at") or ""
            # Pick the most recent convo_context item by created_at.
            if created >= latest_compaction_at:
                latest_compaction_at = created
                compaction_summary_md = row.get("content_md") or None
            continue
        summaries.append({
            "item_id": row.get("item_id"),
            # Migration 052: wi_seq is the per-conversation integer alias
            # ("WI-{wi_seq}") the router LLM emits in target_wi/attached_wis.
            "wi_seq": row.get("wi_seq"),
            "kind": kind or "agent_search",
            "title": row.get("title") or "",
            "summary": row.get("summary"),  # may be NULL — filled below
            # Carried only so the fallback pass below can read it; dropped
            # again before the dict leaves this function.
            "_content_md": row.get("content_md") or "",
        })

    _apply_summary_fallback(summaries)
    for s in summaries:
        s.pop("_content_md", None)
    return summaries, compaction_summary_md


def _apply_summary_fallback(summaries: list[dict]) -> None:
    """Fill a NULL ``summary`` with the item's own content, clipped. In place.

    See ``_SUMMARY_FALLBACK_ITEM_CHARS`` for why this exists. Items that DO
    have a summary are untouched; so are items with neither summary nor content
    (a failed OCR, say) — those keep the renderer's "(لا يوجد ملخص بعد)".

    ``summary_is_content`` tells the renderer to label the text as the item's
    body rather than a digest, and ``summary_truncated`` says the router must
    unfold to see the rest. Budget is spent newest-first (``summaries`` arrives
    ordered created_at ASC) because the item the current turn is about is the
    one just added.
    """
    budget = _SUMMARY_FALLBACK_TOTAL_CHARS
    for item in reversed(summaries):
        if (item.get("summary") or "").strip():
            continue
        body = (item.get("_content_md") or "").strip()
        if not body or budget <= 0:
            continue
        allowance = min(_SUMMARY_FALLBACK_ITEM_CHARS, budget)
        truncated = len(body) > allowance
        if truncated:
            body = body[:allowance].rstrip()
        budget -= len(body)
        item["summary"] = body
        item["summary_is_content"] = True
        item["summary_truncated"] = truncated


def _load_filtered_messages(
    supabase: SupabaseClient, conversation_id: str
) -> list[dict]:
    """Load conversation messages strictly after the compaction cutoff,
    excluding agent_question / agent_answer kinds."""
    cutoff_message_id: str | None = None
    try:
        conv = (
            supabase.table("conversations")
            .select("compacted_through_message_id")
            .eq("conversation_id", conversation_id)
            .maybe_single()
            .execute()
        )
        if conv and getattr(conv, "data", None):
            cutoff_message_id = conv.data.get("compacted_through_message_id")
    except Exception as e:
        logger.warning(
            "load_router_context: conversations.compacted_through_message_id "
            "lookup failed: %s",
            e,
        )

    cutoff_created_at: str | None = None
    if cutoff_message_id:
        try:
            cutoff_row = (
                supabase.table("messages")
                .select("created_at")
                .eq("message_id", cutoff_message_id)
                .maybe_single()
                .execute()
            )
            if cutoff_row and getattr(cutoff_row, "data", None):
                cutoff_created_at = cutoff_row.data.get("created_at")
        except Exception as e:
            logger.warning(
                "load_router_context: cutoff message lookup failed: %s", e
            )

    try:
        q = (
            supabase.table("messages")
            .select("message_id, role, content, metadata, artifact_ids, created_at")
            .eq("conversation_id", conversation_id)
        )
        if cutoff_created_at:
            q = q.gt("created_at", cutoff_created_at)
        msg_rows = (q.order("created_at", desc=False).execute()).data or []
    except Exception as e:
        logger.warning("load_router_context: messages load failed: %s", e)
        return []

    # Python-side filter on metadata->>kind. Done here (rather than via
    # PostgREST) to remain robust whether or not those rows exist yet
    # (Task 13 introduces the kinds; the filter is forward-compatible).
    filtered: list[dict] = []
    for row in msg_rows:
        metadata = row.get("metadata") or {}
        kind = None
        if isinstance(metadata, dict):
            kind = metadata.get("kind")
        if kind in _EXCLUDED_MESSAGE_KINDS:
            continue
        filtered.append(row)

    _attach_message_attachments(supabase, filtered)
    return filtered


def _attach_message_attachments(
    supabase: SupabaseClient, msg_rows: list[dict]
) -> None:
    """Annotate USER message rows with ``attached_wi_ids`` (in place).

    One batched ``message_attachments`` query for the whole window. The ids
    feed :func:`agents.utils.history.build_user_attachment_tag` inside
    ``messages_to_history`` so the router's history marks which workspace
    items (uploaded files / attached blogs) rode which user message — the
    user-turn twin of the assistant provenance tag. Best-effort: any failure
    leaves the rows untagged rather than dropping history.
    """
    user_msg_ids = [
        str(r["message_id"])
        for r in msg_rows
        if r.get("message_id") and (r.get("role") or "") == "user"
    ]
    if not user_msg_ids:
        return
    try:
        att_resp = (
            supabase.table("message_attachments")
            .select("message_id, document_id")
            .in_("message_id", user_msg_ids)
            .execute()
        )
        att_rows = (getattr(att_resp, "data", None) or [])
    except Exception as e:
        logger.warning(
            "load_router_context: message_attachments load failed: %s", e
        )
        return
    by_message: dict[str, list[str]] = {}
    for att in att_rows:
        mid, did = att.get("message_id"), att.get("document_id")
        if mid and did:
            by_message.setdefault(str(mid), []).append(str(did))
    if not by_message:
        return
    for row in msg_rows:
        ids = by_message.get(str(row.get("message_id") or ""))
        if ids:
            row["attached_wi_ids"] = ids


def load_router_context(
    supabase: SupabaseClient,
    user_id: str,
    conversation_id: str,
    case_id: str | None,
) -> RouterContext:
    """Eagerly load everything the router needs for one turn.

    Pure data assembly — no LLM calls. Safe to call from the orchestrator
    just before ``run_router``.
    """
    case_metadata: dict | None = None
    case_memory_md: str | None = None
    if case_id:
        case_metadata, case_memory_md = _load_case_block(supabase, case_id, user_id)

    user_preferences = _load_user_preferences(supabase, user_id)
    user_call_name = _load_user_call_name(supabase, user_id)

    workspace_item_summaries, compaction_summary_md = _load_workspace_item_summaries(
        supabase, conversation_id, user_id
    )

    msg_rows = _load_filtered_messages(supabase, conversation_id)
    # Provenance map (item_id → (wi_seq, kind)) so messages_to_history can tag
    # each assistant turn with which agent produced it + which WI it created.
    # Built from the already-loaded summaries — no extra DB hit. Lets the router
    # route a follow-up ("elaborate that letter") back to the SAME family with
    # target_wi instead of mis-firing a fresh deep_search.
    #
    # Built from the REAL titles BEFORE the masking encode below — its title
    # rides the assistant-turn provenance tag through messages_to_history, which
    # applies its own encode (double-encode would be idempotent, but keeping the
    # source real means a single, clean encode of the assembled tag).
    wi_provenance: dict[str, tuple[int | None, str, str]] = {
        str(s["item_id"]): (s.get("wi_seq"), s.get("kind") or "", s.get("title") or "")
        for s in workspace_item_summaries
        if s.get("item_id")
    }
    message_history = messages_to_history(msg_rows, wi_provenance)

    # وضع السرية: the eager surfaces the router LLM reads DIRECTLY in its dynamic
    # instructions — workspace-item summaries + titles (inject_workspace_summaries),
    # the case memory block (inject_case_context), and the conversation-compaction
    # summary (inject_compaction_summary) — are stored REAL (store-real invariant)
    # and would otherwise reach the model raw. Encode them here at assembly; the
    # item_id / wi_seq handles are untouched so the alias resolver still works.
    # New fakes minted here are persisted by the caller (``_route``) BEFORE the
    # router LLM consumes them (same pattern as messages_to_history). Passthrough
    # when masking is disabled / no turn codec is active.
    from backend.app.services.masking_service import active_codec, encode_active

    if active_codec() is not None:
        workspace_item_summaries = [
            {
                **s,
                "title": encode_active(s.get("title") or ""),
                "summary": encode_active(s.get("summary")),
            }
            for s in workspace_item_summaries
        ]
        case_memory_md = encode_active(case_memory_md)
        compaction_summary_md = encode_active(compaction_summary_md)

    return RouterContext(
        case_memory_md=case_memory_md,
        case_metadata=case_metadata,
        user_preferences=user_preferences,
        # NOT encoded by the masking codec above: وضع السرية masks identifiers
        # found INSIDE conversation content, and this is the user asking to be
        # addressed by their own name. Masking it would replace the name with a
        # fake one and the router would greet the user as somebody else.
        user_call_name=user_call_name,
        workspace_item_summaries=workspace_item_summaries,
        compaction_summary_md=compaction_summary_md,
        message_history=message_history,
    )


__all__ = ["RouterContext", "load_router_context"]
