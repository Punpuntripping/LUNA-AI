"""Memory pre-router hooks — workspace-item re-summarization + conversation compaction.

Two hooks, both invoked once per turn from the orchestrator's best-effort
pre-router block (``agents/orchestrator.py:1463-1468``):

* :func:`resummarize_dirty_items` — finds workspace items whose ``summary`` is
  missing or has drifted away from ``content_md`` and delegates to the REAL
  artifact_summarizer (:func:`agents.memory.summarize.summarize_workspace_item`).
* :func:`compact_conversation` — folds the oldest span of an over-threshold
  conversation into a single ``convo_context`` workspace item produced by the
  ``convo_compactor`` agent, then advances
  ``conversations.compacted_through_message_id``.

Both used to run on hardcoded mock strings (Wave 9). They are real LLM calls
now — see ``.claude/plans/memory_compaction_agent.md``.

**Fail-closed contract** (plan §3.5): compaction that cannot produce a real
summary MUST NOT insert a ``convo_context`` row and MUST NOT advance the
cutoff. Advancing the cutoff behind a bad summary permanently destroys the
compacted span from every downstream agent's view. Failing leaves the
conversation over threshold so the next turn retries — bounded by turn rate,
self-healing, and cheap.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from supabase import Client as SupabaseClient

from agents.memory.convo_compactor import (
    CompactionInput,
    build_compactor_deps,
    run_convo_compaction,
)
# The masking helpers and the min-length floor are imported from the summarizer
# rather than re-implemented: one codec pattern, one threshold, one place to fix.
# The leading underscores are deliberate — these are intra-package helpers of the
# memory family, not a public API.
from agents.memory.summarize import (
    ATTEMPT_RECENT_WINDOW_S,
    MIN_CONTENT_LENGTH_CHARS,
    _dec,
    _enc,
    _summarize_codec,
    summarize_workspace_item,
)
from shared.identity import resolve_call_name
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

DEFAULT_COMPACT_MAX_TOKENS = 10_000
DEFAULT_COMPACT_FRACTION   = 0.60
DRIFT_THRESHOLD            = 0.25   # 25 % change in content length triggers re-summary

# Max re-summarize LLM calls this hook will fire in a single turn. The hook runs
# BEFORE the router, sequentially, so an unbounded dirty set would stall the turn
# behind N model calls — most visibly right after the mock-summary repair
# (plan §5.1) NULLs a whole backlog at once. Anything over the cap heals on the
# next turn, or via the daily sweep (``backend/app/services/summary_sweeper.py``).
RESUMMARIZE_CAP_PER_TURN = 3

# A compaction attempt stamps a marker on the oldest post-cutoff message BEFORE
# the LLM fires. A conversation whose compaction fails persistently would
# otherwise re-fire once per turn forever; within this window we skip instead.
# Mirrors ``summarize.ATTEMPT_RECENT_WINDOW_S`` / ``_mark_attempt``, shorter
# because compaction is genuinely urgent — the context window stays oversized
# until it succeeds.
COMPACTION_ATTEMPT_WINDOW_S = 600

# Pathological-input guard only. ``convo_compactor`` clips each message to 2000
# chars when it renders the prompt, which is what actually shapes the request;
# this outer bound merely stops a multi-megabyte paste from being copied into
# the input dataclass. Deliberately far above the package's clip so the two
# never compound into a destructive double-clip.
MESSAGE_SAFETY_CLIP_CHARS = 8_000

# ---------------------------------------------------------------------------
# Token-counting helper (tiktoken is not in requirements.txt — see note below)
# ---------------------------------------------------------------------------
# NOTE FOR MAINTAINER: `tiktoken` is NOT currently listed in
# backend/requirements.txt.  To enable accurate token counting add:
#
#     tiktoken>=0.7.0
#
# to backend/requirements.txt and re-deploy.  Until then every call falls
# back to the len(text)//4 heuristic, which under-counts Arabic — so the
# threshold below fires later than nominal (plan §8, open decision 2).

try:
    import tiktoken as _tiktoken
    _tok_enc = _tiktoken.get_encoding("cl100k_base")

    def _count_tokens(text: str) -> int:
        return len(_tok_enc.encode(text))

except ImportError:
    _tiktoken = None   # type: ignore[assignment]
    _tok_enc = None    # type: ignore[assignment]
    logger.warning(
        "tiktoken not installed — falling back to len(text)//4 for token counting. "
        "Add tiktoken>=0.7.0 to backend/requirements.txt for accurate counts."
    )

    def _count_tokens(text: str) -> int:  # type: ignore[misc]
        return len(text) // 4


# ---------------------------------------------------------------------------
# Tool-pair boundary helper
# ---------------------------------------------------------------------------

# TODO — tool-pair boundary rule from Pydantic AI docs (10_message_history.md):
#
#   The compaction cutoff MUST NOT split a ToolCallPart from its matching
#   ToolReturnPart.  After computing the naive fraction-based boundary, walk
#   forward through the message list until the current message is NOT a
#   tool-return (i.e. is not ModelResponse immediately following a
#   ToolCallPart).  That position becomes the actual cutoff index.
#   Splitting a ToolCallPart / ToolReturnPart pair causes the model to error
#   on resume because the context becomes structurally invalid.
#
# The ``messages`` table stores plain text content (no tool parts), so this is
# still a no-op passthrough. It becomes load-bearing the moment tool parts land
# in the table (plan §8, open decision 3).

def _walk_to_safe_boundary(messages: list[dict[str, Any]], idx: int) -> int:
    """Return the first safe compaction cutoff at or after `idx`.

    Placeholder — always returns `idx` unchanged while ``messages.content`` is
    plain text. See the TODO above for the rule it must implement once
    ToolCallPart / ToolReturnPart pairs reach the table.
    """
    return idx


# ---------------------------------------------------------------------------
# Attempt marker (retry-storm guard)
# ---------------------------------------------------------------------------
#
# Anchor choice: ``messages.metadata.compaction_attempt`` on the OLDEST
# post-cutoff message.
#
# ``conversations`` has no ``metadata`` column in production (verified live —
# the plan's suggested ``conversations.metadata.compaction_attempt`` does not
# exist; see [[project_migration_drift]]), and adding one is out of scope here.
# The oldest post-cutoff message is the right anchor anyway because it is
# exactly as stable as the thing the marker guards:
#
#   * compaction fails  → the cutoff does not move → next turn recomputes the
#     same ``messages[0]`` → the marker is found → skipped;
#   * compaction succeeds → the cutoff advances past it → next turn's
#     ``messages[0]`` is a different row → a naturally fresh marker slot.
#
# The row is already being SELECTed, so the read is free; only the write is new.


def _compaction_attempted_recently(anchor: dict[str, Any]) -> bool:
    """True when a compaction attempt for this same cutoff window is in flight
    or failed within ``COMPACTION_ATTEMPT_WINDOW_S``.

    An unparseable or future-dated marker is ignored (proceed) — the guard must
    never be able to wedge compaction off permanently.
    """
    metadata = anchor.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False
    at = (metadata.get("compaction_attempt") or {}).get("at")
    if not at:
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(at))).total_seconds()
    except Exception:  # noqa: BLE001
        return False
    return 0 <= age < COMPACTION_ATTEMPT_WINDOW_S


def _mark_compaction_attempt(
    supabase: SupabaseClient, anchor: dict[str, Any]
) -> None:
    """Stamp ``metadata.compaction_attempt.at`` on the anchor message BEFORE the
    LLM fires, so a failure does not re-bill once per turn.

    Read-modify-write on the row's existing metadata — ``metadata.kind`` is what
    the router's message filter reads (``agents/router/context.py:293-303``) and
    must survive. Best-effort: a failed marker write proceeds anyway.
    """
    metadata = anchor.get("metadata")
    md = dict(metadata) if isinstance(metadata, dict) else {}
    md["compaction_attempt"] = {"at": datetime.now(timezone.utc).isoformat()}
    try:
        (
            supabase.table("messages")
            .update({"metadata": md})
            .eq("message_id", anchor["message_id"])
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "compact_conversation: attempt-marker write failed message_id=%s: %s",
            anchor.get("message_id"), exc,
        )


# ---------------------------------------------------------------------------
# Compaction input loading
# ---------------------------------------------------------------------------


def _load_compaction_context(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
) -> tuple[str, list[dict[str, Any]]] | None:
    """Return ``(prior_summary_md, workspace_items)`` for the compactor.

    One SELECT, partitioned the same way ``agents/router/context.py:215-237``
    partitions it:

    * the most recent non-deleted ``convo_context`` ``content_md`` becomes
      ``prior_summary_md`` — **mandatory** input. Without it, compaction #2
      silently orphans compaction #1 and that span becomes unrecoverable from
      context (plan §1.3);
    * every other non-deleted item becomes a ``{wi_seq, kind, title, summary}``
      grounding row so the summary can reference ``WI-{n}`` instead of
      inventing items.

    Returns ``None`` on query failure — the caller must then fail closed. A
    silent ``("", [])`` here would look exactly like "first compaction, no
    items", which is precisely the orphaning bug this input exists to prevent.
    """
    try:
        resp = (
            supabase.table("workspace_items")
            .select("wi_seq, kind, title, summary, content_md, created_at")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=False)
            .execute()
        )
        rows = (getattr(resp, "data", None) or []) if resp else []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "compact_conversation: workspace_items load failed for %s: %s",
            conversation_id, exc,
        )
        return None

    prior_summary_md = ""
    latest_at = ""
    items: list[dict[str, Any]] = []

    for row in rows:
        kind = row.get("kind") or ""
        if kind == "convo_context":
            created = row.get("created_at") or ""
            if created >= latest_at:
                latest_at = created
                prior_summary_md = (row.get("content_md") or "").strip()
            continue
        items.append({
            "wi_seq": row.get("wi_seq"),
            "kind": kind or "agent_search",
            "title": row.get("title") or "",
            "summary": row.get("summary"),   # may be NULL — the prompt handles it
        })

    return prior_summary_md, items


def _load_user_call_name(supabase: SupabaseClient, user_id: str) -> str | None:
    """What the summary should call the user, or ``None``.

    Resolution lives in :func:`shared.identity.resolve_call_name` so this can
    never disagree with the router or the settings dialog. Best-effort — a
    failure just means the compactor writes an unnamed summary.
    """
    try:
        row = (
            supabase.table("users")
            .select("preferred_name, full_name_ar")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        data = (getattr(row, "data", None) or {}) if row else {}
        return resolve_call_name(data.get("preferred_name"), data.get("full_name_ar"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("compact_conversation: call-name lookup failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resummarize_dirty_items(
    supabase: SupabaseClient,
    conversation_id: str,
) -> list[str]:
    """Re-summarize workspace items whose ``summary`` is missing or stale.

    Scans every non-``convo_context``, non-deleted item in the conversation and
    delegates each dirty one to the real artifact_summarizer
    (:func:`agents.memory.summarize.summarize_workspace_item`). Returns the
    item_ids that actually got a fresh summary written.

    Two kinds of dirty, handled differently:

    * **``summary IS NULL``** → a plain call. The summarizer's own
      ``metadata.summary_attempt`` guard (1 h) stops a failing item from
      re-billing every turn.
    * **drift ≥ ``DRIFT_THRESHOLD``** → needs ``force=True``. The summarizer is
      idempotent by design and returns immediately when ``summary`` is already
      set, so without ``force`` an edited item would never be re-summarized at
      all. ``force`` also bypasses the summarizer's attempt guard, so this
      function applies that recency check itself before forcing.

    **NULL is a terminal state, not a to-do.** ``summarize_workspace_item``
    refuses anything under ``MIN_CONTENT_LENGTH_CHARS`` and returns *before*
    stamping an attempt marker, so short items would be re-asked (a wasted
    round-trip each, forever) if they counted as dirty. They are filtered out
    up front on the same threshold the summarizer enforces — the gate the
    daily sweep already uses (``summary_sweeper.py:93``).
    """
    try:
        result = (
            supabase.table("workspace_items")
            .select("item_id, content_md, summary, summary_source_length, kind, metadata")
            .eq("conversation_id", conversation_id)
            .neq("kind", "convo_context")
            .is_("deleted_at", "null")
            .execute()
        )
        items: list[dict[str, Any]] = (getattr(result, "data", None) or []) if result else []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "resummarize_dirty_items: item load failed for %s: %s",
            conversation_id, exc,
        )
        return []

    now = datetime.now(timezone.utc)
    updated_ids: list[str] = []
    attempted = 0
    skipped_capped = 0

    for item in items:
        content_md: str = (item.get("content_md") or "").strip()
        current_len = len(content_md)
        existing_summary: str | None = item.get("summary")
        source_len: int | None = item.get("summary_source_length")
        item_id: str = item["item_id"]

        # Terminal-NULL guard — see the docstring. Short/empty bodies are
        # legitimately unsummarized; asking again every turn only burns a
        # round-trip.
        if current_len < MIN_CONTENT_LENGTH_CHARS:
            continue

        force = False
        if existing_summary is None or source_len is None:
            pass                                  # NULL summary → plain call
        else:
            drift = abs(current_len - source_len) / source_len if source_len > 0 else 1.0
            if drift < DRIFT_THRESHOLD:
                continue                          # clean
            # Drifted: the summarizer no-ops on an already-summarized row, so
            # this path must force. Since force also skips the summarizer's own
            # double-bill guard, honour that window here instead.
            at = ((item.get("metadata") or {}).get("summary_attempt") or {}).get("at")
            if at:
                try:
                    age = (now - datetime.fromisoformat(str(at))).total_seconds()
                    if 0 <= age < ATTEMPT_RECENT_WINDOW_S:
                        continue
                except Exception:  # noqa: BLE001
                    pass                          # unparseable marker → proceed
            force = True

        if attempted >= RESUMMARIZE_CAP_PER_TURN:
            # Bounded per turn — this hook runs sequentially, before the router,
            # so the cap counts ATTEMPTS rather than successes: a backlog that
            # the summarizer's own guards reject still costs one blocking
            # round-trip each, and that latency is the thing being bounded.
            skipped_capped += 1
            continue

        attempted += 1
        try:
            # Never raises by contract; the guard is defensive.
            if await summarize_workspace_item(supabase, item_id, force=force):
                updated_ids.append(item_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "resummarize_dirty_items: failed to summarize item %s: %s",
                item_id,
                exc,
            )

    logger.debug(
        "resummarize_dirty_items: conversation %s — %d/%d items updated "
        "(%d attempted, %d deferred by cap)",
        conversation_id,
        len(updated_ids),
        len(items),
        attempted,
        skipped_capped,
    )
    return updated_ids


async def compact_conversation(
    supabase: SupabaseClient,
    conversation_id: str,
    user_id: str,
    max_tokens: int = DEFAULT_COMPACT_MAX_TOKENS,
    fraction: float = DEFAULT_COMPACT_FRACTION,
) -> str | None:
    """Compact a conversation when its post-cutoff messages exceed max_tokens.

    Algorithm:
    1. Load ``conversations.compacted_through_message_id`` (cutoff pointer).
    2. Fetch all messages with ``created_at`` strictly after the cutoff
       message's ``created_at`` (or all messages if no cutoff exists yet).
    3. Count tokens across all post-cutoff messages.
    4. If total <= max_tokens → return None (no compaction needed).
    5. Take the oldest ``fraction`` of those messages as the batch to compact.
    6. Apply tool-pair boundary safety (see ``_walk_to_safe_boundary``).
    7. Run the ``convo_compactor`` agent over the batch, the prior compaction
       summary, and the conversation's workspace-item list. One LLM call; its
       cost lands in ``llm_calls`` via the package's ``record_run`` (the
       orchestrator's pre-router hook runs inside ``handle_message``'s
       ``collect_llm_calls`` scope).
    8. Insert a ``convo_context`` workspace_item with the summary.
    9. Update ``conversations.compacted_through_message_id`` to the last
       summarized message's id.
    10. Return the new ``convo_context`` item_id.

    Returns None if compaction was not triggered **or failed for any reason**.
    Fail-closed (plan §3.5): a failed or empty summary writes nothing and leaves
    the cutoff exactly where it was, so the messages stay in every downstream
    agent's window and the next turn retries. Never advance the cutoff behind a
    summary you do not have.

    Fixed-window: one compaction per threshold breach (does not loop).
    """
    # Steps 1-3 are the read phase. Every one of them is guarded: the docstring
    # promises None "if compaction was not triggered or failed for any reason",
    # and an unguarded read here would raise straight through that contract into
    # the caller. Both live callers happen to wrap this function today, so a
    # raise is contained — but the contract is what a third caller will read.
    try:
        # --------------------------------------------------------------
        # 1. Load conversation to get current compaction pointer
        # --------------------------------------------------------------
        conv_result = (
            supabase.table("conversations")
            .select("conversation_id, compacted_through_message_id")
            .eq("conversation_id", conversation_id)
            .is_("deleted_at", "null")
            .single()
            .execute()
        )
        conversation: dict[str, Any] = conv_result.data or {}
        cutoff_message_id: str | None = conversation.get(
            "compacted_through_message_id"
        )

        # --------------------------------------------------------------
        # 2. Resolve cutoff created_at (needed for strict-after filter)
        # --------------------------------------------------------------
        cutoff_created_at: str | None = None
        if cutoff_message_id is not None:
            cutoff_msg_result = (
                supabase.table("messages")
                .select("created_at")
                .eq("message_id", cutoff_message_id)
                .single()
                .execute()
            )
            cutoff_created_at = (cutoff_msg_result.data or {}).get("created_at")

        # --------------------------------------------------------------
        # 3. Fetch post-cutoff messages ordered oldest-first
        # --------------------------------------------------------------
        # ``metadata`` rides along for the attempt marker on messages[0] — free
        # here, and it saves a second round-trip for the retry-storm guard.
        msg_query = (
            supabase.table("messages")
            .select("message_id, role, content, metadata, created_at")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=False)
        )
        if cutoff_created_at is not None:
            msg_query = msg_query.gt("created_at", cutoff_created_at)

        msg_result = msg_query.execute()
        messages: list[dict[str, Any]] = msg_result.data or []
    except Exception as exc:  # noqa: BLE001
        # FAIL CLOSED, same as every later failure path: no summary, no cutoff
        # advance. A read failure means we cannot even establish what the span
        # IS, so compacting would be guesswork.
        logger.warning(
            "compact_conversation: %s — read phase failed (%s); not compacting",
            conversation_id, exc, exc_info=True,
        )
        return None

    if not messages:
        return None

    # ------------------------------------------------------------------
    # 4. Count total tokens
    # ------------------------------------------------------------------
    full_text = " ".join(m.get("content") or "" for m in messages)
    total_tokens = _count_tokens(full_text)

    if total_tokens <= max_tokens:
        logger.debug(
            "compact_conversation: %s has %d tokens (<= %d threshold), skipping",
            conversation_id,
            total_tokens,
            max_tokens,
        )
        return None

    # ------------------------------------------------------------------
    # 4b. Retry-storm guard — a compaction for THIS cutoff window that is in
    #     flight or failed inside the cooldown must not re-fire the model.
    # ------------------------------------------------------------------
    anchor = messages[0]
    if _compaction_attempted_recently(anchor):
        logger.debug(
            "compact_conversation: %s attempted within %ds, skipping",
            conversation_id,
            COMPACTION_ATTEMPT_WINDOW_S,
        )
        return None

    # ------------------------------------------------------------------
    # 5. Identify the batch to compact (oldest `fraction`)
    # ------------------------------------------------------------------
    naive_boundary = max(1, int(len(messages) * fraction))

    # ------------------------------------------------------------------
    # 6. Tool-pair boundary safety
    # ------------------------------------------------------------------
    safe_boundary = _walk_to_safe_boundary(messages, naive_boundary)
    batch = messages[:safe_boundary]

    if not batch:
        logger.warning(
            "compact_conversation: %s — empty batch after boundary walk, aborting",
            conversation_id,
        )
        return None

    last_summarized_message = batch[-1]
    last_summarized_id: str = last_summarized_message["message_id"]

    # ------------------------------------------------------------------
    # 7. Real compaction — one convo_compactor call, fail-closed
    # ------------------------------------------------------------------
    # 7a. Mandatory inputs. A load failure is NOT "no prior summary" — treat it
    #     as a failure and retry next turn (plan §1.3).
    loaded = _load_compaction_context(supabase, conversation_id, user_id)
    if loaded is None:
        return None
    prior_summary_md, wi_rows = loaded

    # 7b. وضع السرية — the compactor reads real message bodies and its output is
    #     STORED, so every LLM-bound surface is encoded and the produced summary
    #     is decoded before persist (store-real invariant, summarize.py:60-77).
    #     ``user_call_name`` is deliberately NOT encoded, matching the router's
    #     rationale at context.py:423-426: it is the user asking to be addressed
    #     by their own name, and a fake would address someone else.
    codec = _summarize_codec(supabase, user_id)

    enc_messages: list[dict[str, str]] = []
    for m in batch:
        body = (m.get("content") or "").strip()
        if not body:
            continue
        enc_messages.append({
            "role": str(m.get("role") or "user"),
            "content": _enc(codec, body[:MESSAGE_SAFETY_CLIP_CHARS]),
        })

    if not enc_messages:
        logger.warning(
            "compact_conversation: %s — batch has no non-empty message bodies, aborting",
            conversation_id,
        )
        return None

    enc_items = [
        {
            "wi_seq": r.get("wi_seq"),
            "kind": r.get("kind"),
            "title": _enc(codec, r.get("title") or ""),
            "summary": _enc(codec, r["summary"]) if r.get("summary") else None,
        }
        for r in wi_rows
    ]
    enc_prior = _enc(codec, prior_summary_md)

    if codec is not None and user_id:
        # Persist the freshly-minted fakes BEFORE the call, so anything that
        # reads this summary in a later process can still decode it.
        #
        # Non-fatal on failure: the summary produced by this call is decoded
        # in-process, with this same codec object, before it is stored — so the
        # store-real invariant holds regardless. A failed flush only costs the
        # ability to decode these particular fakes from a *different* process,
        # and compaction has no pause/resume path that would need that.
        try:
            from backend.app.services.masking_service import persist_new_mappings

            persist_new_mappings(supabase, user_id, codec)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "compact_conversation: %s — mapping persist failed: %s",
                conversation_id, exc,
            )

    # 7c. Stamp the attempt marker BEFORE the LLM fires.
    _mark_compaction_attempt(supabase, anchor)

    # 7d. The one call. ``run_convo_compaction`` never raises by contract —
    #     it signals failure via ``output.failed``. The guard is defensive.
    try:
        output = await run_convo_compaction(
            CompactionInput(
                messages=enc_messages,
                workspace_items=enc_items,
                prior_summary_md=enc_prior,
                user_call_name=_load_user_call_name(supabase, user_id),
                # Telemetry only — never rendered into the prompt. Without it
                # the compaction span carries conversation_id=None and cannot
                # be joined to its conversation in Logfire (/convo-monitor).
                conversation_id=conversation_id,
            ),
            build_compactor_deps(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "compact_conversation: %s — compactor raised: %s",
            conversation_id, exc, exc_info=True,
        )
        output = None

    summary_md = (getattr(output, "summary_md", "") or "").strip() if output else ""

    if output is None or output.failed or not summary_md:
        # FAIL CLOSED. No convo_context row, no cutoff advance. The conversation
        # stays over threshold and retries on the next turn (throttled by the
        # attempt marker stamped above).
        logger.warning(
            "compact_conversation: %s — compaction failed, cutoff left at %s",
            conversation_id,
            cutoff_message_id,
        )
        try:
            _logfire.warning(
                "convo_compactor.fail_closed",
                conversation_id=conversation_id,
                message_count=len(enc_messages),
                had_prior_summary=bool(prior_summary_md),
                empty_output=bool(output is not None and not summary_md),
                raised=output is None,
            )
        except Exception:  # noqa: BLE001
            pass
        return None

    # 7e. Decode before store — the summary re-encodes at the next assembly
    #     point (router injection, writer context, attachment summarizer).
    summary_text = _dec(codec, summary_md)

    # ------------------------------------------------------------------
    # 8. Insert convo_context workspace_item
    # ------------------------------------------------------------------
    # ``summary`` is written at insert time on purpose: the AFTER-INSERT trigger
    # POSTs to /internal/summarize-workspace-item, and the summarizer's
    # idempotency guard skips any row that already has one. A convo_context IS a
    # summary; summarizing it again would be a wasted call.
    new_item_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    (
        supabase.table("workspace_items")
        .insert(
            {
                "item_id": new_item_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "kind": "convo_context",
                "created_by": "agent",
                "title": f"ملخص المحادثة — {now_iso[:10]}",
                "content_md": summary_text,
                "is_visible": False,  # convo_context is internal; hidden from chip bar
                "summary": summary_text,
                "summary_source_length": len(summary_text),
                "summary_updated_at": now_iso,
                "created_at": now_iso,
                "updated_at": now_iso,
            }
        )
        .execute()
    )

    # ------------------------------------------------------------------
    # 9. Update compacted_through_message_id
    # ------------------------------------------------------------------
    (
        supabase.table("conversations")
        .update({"compacted_through_message_id": last_summarized_id})
        .eq("conversation_id", conversation_id)
        .execute()
    )

    logger.info(
        "compact_conversation: %s compacted %d messages into item %s "
        "(model=%s, tokens_in=%d, tokens_out=%d, prior_summary=%s)",
        conversation_id,
        len(enc_messages),
        new_item_id,
        output.model_used,
        output.tokens_in,
        output.tokens_out,
        bool(prior_summary_md),
    )

    # ------------------------------------------------------------------
    # 10. Return new item id
    # ------------------------------------------------------------------
    return new_item_id
