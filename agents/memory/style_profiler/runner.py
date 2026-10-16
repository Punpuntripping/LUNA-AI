"""Runner for the style_profiler — post-turn, best-effort, never raises.

Public API (plan ``unified_responder.md`` §7):

* ``maybe_refresh_style_profile(supabase, user_id)`` — counts the user's
  messages across conversations and refreshes the profile when there is no
  row yet, or when ``count - source_msg_count >= STYLE_PROFILE_EVERY_N``.
  Swallows every error (log only). Call it fire-and-forget via
  ``schedule_style_profile_refresh`` so it never blocks a turn.
* ``load_style_profile_md(supabase, user_id)`` — the stored ``rendered_md``
  or ``None``. Sync, one PK read; never raises.

وضع السرية: messages are stored REAL. Every LLM-bound message goes through the
codec (the turn's active codec, or one built for this user when detached —
same helper as the summarizer/compactor). The produced profile is decoded and
then RE-validated, and the validators strip digits/emails/name-like entries,
so nothing stored here can carry a real OR fake identifier. That is also why
no ``persist_new_mappings`` flush is needed: no fake minted for this call can
survive into the stored output.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from agents.memory.summarize import _dec, _enc, _summarize_codec
from agents.utils.tracking import track_stage

from .agent import STYLE_PROFILER_LIMITS, create_style_profiler
from .models import StyleProfile, StyleProfilerInput, normalize_ar
from .prompts import render_profile_md

logger = logging.getLogger(__name__)

TABLE = "user_style_profiles"
RECENT_MESSAGES = 30
DEFAULT_EVERY_N = 8
# After a failed refresh, don't retry for this user in this process for a while
# (a persistent provider outage must not cost one LLM call per turn).
FAIL_BACKOFF_S = 600.0

_INFLIGHT: set[str] = set()
_LAST_FAILURE: dict[str, float] = {}
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def get_every_n() -> int:
    """``STYLE_PROFILE_EVERY_N`` from settings (env fallback, default 8).
    ``<= 0`` disables refreshes."""
    try:
        from shared.config import get_settings

        return int(get_settings().STYLE_PROFILE_EVERY_N)
    except Exception:  # noqa: BLE001
        try:
            return int(os.environ.get("STYLE_PROFILE_EVERY_N", DEFAULT_EVERY_N))
        except ValueError:
            return DEFAULT_EVERY_N


def should_refresh(row: dict | None, total_user_msgs: int, every_n: int) -> bool:
    """Pure threshold rule."""
    if every_n <= 0 or total_user_msgs <= 0:
        return False
    if row is None:
        return True
    prev = int(row.get("source_msg_count") or 0)
    return total_user_msgs - prev >= every_n


# ---------------------------------------------------------------------------
# DB helpers (sync — run via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _count_user_messages(supabase: Any, user_id: str) -> int:
    """All user-role messages across the user's conversations (deleted ones
    included, so the count is monotonic and the threshold never stalls)."""
    res = (
        supabase.table("messages")
        # NOT head=True: postgrest-py raises "JSON could not be generated" on
        # the empty HEAD body (verified live 2026-10-15). limit(1) + count is
        # the same single round-trip.
        .select("message_id, conversations!messages_conversation_id_fkey!inner(user_id)", count="exact")
        .eq("conversations.user_id", user_id)
        .eq("role", "user")
        .limit(1)
        .execute()
    )
    return int(getattr(res, "count", None) or 0)


def _load_recent_user_messages(supabase: Any, user_id: str) -> list[dict]:
    """Last RECENT_MESSAGES user messages from non-deleted conversations,
    returned oldest-first."""
    res = (
        supabase.table("messages")
        .select("content, conversation_id, created_at, conversations!messages_conversation_id_fkey!inner(user_id, deleted_at)")
        .eq("conversations.user_id", user_id)
        .is_("conversations.deleted_at", "null")
        .eq("role", "user")
        .order("created_at", desc=True)
        .limit(RECENT_MESSAGES)
        .execute()
    )
    rows = [r for r in (res.data or []) if (r.get("content") or "").strip()]
    rows.reverse()
    return rows


def _load_row(supabase: Any, user_id: str) -> dict | None:
    res = (
        supabase.table(TABLE)
        .select("profile, rendered_md, source_msg_count")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    data = res.data or []
    if isinstance(data, dict):
        return data
    return data[0] if data else None


def _upsert_row(supabase: Any, user_id: str, profile: StyleProfile, rendered: str, count: int) -> None:
    from datetime import datetime, timezone

    supabase.table(TABLE).upsert(
        {
            "user_id": user_id,
            "profile": profile.model_dump(),
            "rendered_md": rendered,
            "source_msg_count": count,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="user_id",
    ).execute()


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


async def run_style_profiler(inp: StyleProfilerInput) -> StyleProfile | None:
    """One LLM call. Returns the validated profile or ``None``. Never raises."""
    from .prompts import build_user_message

    if not any((m or "").strip() for m in inp.messages):
        return None
    try:
        with track_stage(
            "style_profiler.run",
            conversation_id=inp.conversation_id,
            agent_family="memory",
            message_count=len(inp.messages),
            has_previous=bool(inp.previous_profile),
        ) as span:
            agent = create_style_profiler()
            result = await agent.run(
                build_user_message(inp.messages, inp.previous_profile),
                usage_limits=STYLE_PROFILER_LIMITS,
            )
            try:
                span.record_run(result, slot="style_profiler")
            except Exception:  # noqa: BLE001
                pass
            out = result.output
            return out if isinstance(out, StyleProfile) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("style_profiler: LLM call failed: %s", exc)
        return None


def _decode_profile(codec: Any, profile: StyleProfile) -> StyleProfile:
    """Decode text fields (store-real invariant), then RE-validate so the
    identifier strip runs on the decoded values too."""
    data = profile.model_dump()
    data["terms"] = [_dec(codec, t) for t in data.get("terms") or []]
    data["notes"] = _dec(codec, data.get("notes") or "")
    return StyleProfile.model_validate(data)


def _filter_terms_to_source(profile: StyleProfile, sources: list[str]) -> StyleProfile:
    """Keep only terms that actually occur in the user's (real) messages —
    «verbatim» is the contract; an invented term is dropped."""
    haystack = normalize_ar(" ".join(sources))
    kept = [t for t in profile.terms if normalize_ar(t) and normalize_ar(t) in haystack]
    if kept == profile.terms:
        return profile
    return profile.model_copy(update={"terms": kept})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def maybe_refresh_style_profile(
    supabase: Any,
    user_id: str,
    *,
    conversation_id: str | None = None,
    every_n: int | None = None,
) -> None:
    """Refresh the user's style profile when due. Never raises, never blocks
    the caller's turn when scheduled via ``schedule_style_profile_refresh``.

    ``conversation_id`` only attributes the ``llm_calls`` ledger row
    (``llm_calls.conversation_id`` is NOT NULL); defaults to the conversation
    of the most recent loaded message.
    """
    if not user_id:
        return
    uid = str(user_id)
    if uid in _INFLIGHT:
        return
    failed_at = _LAST_FAILURE.get(uid)
    if failed_at is not None and time.monotonic() - failed_at < FAIL_BACKOFF_S:
        return

    _INFLIGHT.add(uid)
    try:
        n = get_every_n() if every_n is None else every_n
        if n <= 0:
            return
        total = await asyncio.to_thread(_count_user_messages, supabase, uid)
        row = await asyncio.to_thread(_load_row, supabase, uid)
        if not should_refresh(row, total, n):
            return

        rows = await asyncio.to_thread(_load_recent_user_messages, supabase, uid)
        if not rows:
            return
        real_texts = [str(r.get("content") or "") for r in rows]

        codec = _summarize_codec(supabase, uid)
        masked = [_enc(codec, t) for t in real_texts]

        conv_id = conversation_id or rows[-1].get("conversation_id")
        prev = row.get("profile") if row else None
        inp = StyleProfilerInput(
            messages=masked,
            previous_profile=prev if isinstance(prev, dict) else None,
            conversation_id=str(conv_id) if conv_id else None,
        )

        profile = await _run_in_ledger_scope(supabase, uid, inp)
        if profile is None:
            _LAST_FAILURE[uid] = time.monotonic()
            return

        profile = _decode_profile(codec, profile)
        profile = _filter_terms_to_source(profile, real_texts)
        rendered = render_profile_md(profile)
        await asyncio.to_thread(_upsert_row, supabase, uid, profile, rendered, total)
        _LAST_FAILURE.pop(uid, None)
    except Exception as exc:  # noqa: BLE001
        _LAST_FAILURE[uid] = time.monotonic()
        logger.warning("style_profiler: refresh failed for user: %s", exc)
    finally:
        _INFLIGHT.discard(uid)


async def _run_in_ledger_scope(supabase: Any, user_id: str, inp: StyleProfilerInput) -> StyleProfile | None:
    """Run inside its OWN ``collect_llm_calls`` scope. Always a fresh scope: a
    post-turn task may inherit the turn's (already flushed) buffer through the
    copied context, and rows appended there would be lost silently."""
    if not inp.conversation_id:
        return await run_style_profiler(inp)
    try:
        from agents.utils.usage_sink import collect_llm_calls

        scope = collect_llm_calls(supabase, conversation_id=inp.conversation_id, user_id=user_id)
    except Exception:  # noqa: BLE001
        return await run_style_profiler(inp)
    with scope:
        return await run_style_profiler(inp)


def schedule_style_profile_refresh(
    supabase: Any,
    user_id: str,
    *,
    conversation_id: str | None = None,
) -> asyncio.Task | None:
    """Fire-and-forget wrapper: schedules ``maybe_refresh_style_profile`` on
    the running loop and keeps a strong reference until it finishes. Returns
    ``None`` (and does nothing) when there is no running loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    try:
        task = loop.create_task(
            maybe_refresh_style_profile(supabase, user_id, conversation_id=conversation_id),
            name="style_profiler.refresh",
        )
    except Exception:  # noqa: BLE001
        return None
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def load_style_profile_md(supabase: Any, user_id: str) -> str | None:
    """The stored rendered profile block, or ``None``. Never raises."""
    if not user_id:
        return None
    try:
        row = _load_row(supabase, str(user_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("style_profiler: load failed: %s", exc)
        return None
    if not row:
        return None
    md = (row.get("rendered_md") or "").strip()
    return md or None


__all__ = [
    "maybe_refresh_style_profile",
    "schedule_style_profile_refresh",
    "load_style_profile_md",
    "run_style_profiler",
    "should_refresh",
    "get_every_n",
]
