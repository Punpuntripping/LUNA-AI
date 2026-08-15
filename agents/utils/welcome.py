"""رسائل الترحيب — the welcome opening of a user's first answer.

The welcome is **not** a message of its own. It is the first line of the answer
the user was already waiting for, written by whichever agent produces that
turn's user-facing text — the router (a direct ``ChatResponse``), the
deep_search ``planner_responder``, or the ``writer_planner``. Any of the three
can be the first thing a new user ever reads, so all three carry the block.

Two variants, and the first-ever one always wins:

* ``user_first``  — the user has never been welcomed. Gated by a ``welcomed_at``
  key in ``user_preferences.preferences``.
* ``return_gap``  — they have, but their last conversation went quiet for
  :data:`RETURN_GAP_DAYS`. Computed from activity, so it needs no flag.

Everything is resolved ONCE per turn in ``orchestrator._route`` and handed to
whichever agent runs, so the two never disagree and the DB work happens once.
Resolution lives in ``_route`` (not in ``handle_message``) for a second reason:
``_route`` is not on the resume path, so a run that paused for a clarifying
question can never re-greet the user halfway through the conversation.

Failure policy: every lookup here is wrapped and degrades to "no welcome". A
greeting is the least important thing in the turn — it must never be the reason
an answer fails.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from supabase import Client as SupabaseClient

from shared.identity import resolve_call_name

logger = logging.getLogger(__name__)


# ── Gates ─────────────────────────────────────────────────────────────────────

#: Flat key inside ``user_preferences.preferences``. Flat because the merge RPC
#: is a SHALLOW merge — a nested object would clobber its siblings.
WELCOMED_AT_KEY = "welcomed_at"

#: How quiet a user has to go before a new conversation is greeted again.
RETURN_GAP_DAYS = 7

#: Extra characters an agent's chat text may run to when it carries a welcome.
#: The longest opening — «أهلين برائد الأعمال {name}، شكرًا لتجربتك ريحان وإن
#: شاء الله نكون عند حسن ظنك. بالنسبة لسؤالك…» — is ~95 chars plus a name. Any
#: length cap enforced by truncation must be raised by this much on a welcomed
#: turn, or the cut lands on the END of the answer while the greeting survives:
#: exactly backwards.
WELCOME_CHAR_ALLOWANCE = 160


Variant = Literal["user_first", "return_gap"]


@dataclass(frozen=True)
class WelcomeState:
    """What to say, to whom. ``None`` (no state at all) means say nothing."""

    variant: Variant
    call_name: str | None
    profession_group: str | None


# ── The copy ──────────────────────────────────────────────────────────────────
#
# This is the ONLY place welcome text lives. Register is «أهلين» throughout —
# the product's own voice, deliberately warmer than the MSA the rest of the
# prompts use.
#
# The honorifics carry their own «بـ» so the address renders as «أهلين بالمحامي
# أسعد» rather than «أهلين بـالمحامي أسعد». They are MASCULINE and we store no
# gender: a woman in either group sees the masculine form, and `legal` covers
# «محامٍ · طالب قانون · باحث قانوني» so a law student is addressed as «المحامي».
# Raised with the product owner and accepted on 2026-08-14 — do not quietly
# "fix" this into a neutral form.

_HONORIFICS: dict[str, str] = {
    "legal": "بالمحامي",
    "entrepreneur": "برائد الأعمال",
}

# Every line ends on «بالنسبة لسؤالك…» — an OPEN seam. The agent is told to
# continue that sentence into its own answer, which is what keeps the greeting
# from reading like a bolted-on preamble.
_BODY_WITH_ADDRESS: dict[Variant, str] = {
    "user_first": "شكرًا لتجربتك ريحان وإن شاء الله نكون عند حسن ظنك. بالنسبة لسؤالك…",
    "return_gap": "حياك الله من جديد. بالنسبة لسؤالك…",
}

# Standalone forms — no name, so the sentence carries itself.
_BODY_ALONE: dict[Variant, str] = {
    "user_first": "شكرًا لتجربتك ريحان، وإن شاء الله نكون عند حسن ظنك. بالنسبة لسؤالك…",
    "return_gap": "حياك الله من جديد. بالنسبة لسؤالك…",
}


def compose_opening(state: WelcomeState) -> str:
    """The exact Arabic line the agent must open with."""
    honorific = _HONORIFICS.get(state.profession_group or "")
    if state.call_name and honorific:
        address = f"أهلين {honorific} {state.call_name}"
    elif state.call_name:
        address = f"أهلين {state.call_name}"
    else:
        return _BODY_ALONE[state.variant]
    return f"{address}، {_BODY_WITH_ADDRESS[state.variant]}"


def render_welcome_instruction(state: WelcomeState | None) -> str:
    """The prompt block injected into whichever agent answers this turn.

    Returns ``""`` when there is nothing to say, so every call site can inject
    it unconditionally.

    The user's name is interpolated into a line the model is told to copy
    verbatim, so it is NOT html-escaped the way ``inject_user_call_name``
    escapes its fenced ``<user_name>`` payload — escaping here would put
    ``&quot;`` into the user's chat bubble. What makes that safe is
    ``shared.identity.clean_name``, which has already stripped control
    characters and newlines (the only way to fake an instruction break) and
    capped the value at 60 chars.
    """
    if state is None:
        return ""
    opening = compose_opening(state)
    lead = (
        "This is the very first message this user has sent to ريحان."
        if state.variant == "user_first"
        else "This user is starting a new conversation after a long absence."
    )
    # Deliberately phrased around "the chat message you write this turn" rather
    # than a field name: the same block is injected into the router
    # (ChatResponse.message), the deep_search responder (chat_summary_md) and
    # the writer_planner (chat_summary). One wording, three agents, no variants
    # to drift apart.
    return (
        f"\n## The opening line of this turn's chat message (mandatory)\n\n"
        f"{lead} Begin the chat message you write this turn — the text the user "
        f"reads in the chat bubble — with EXACTLY this line, verbatim:\n\n"
        f"{opening}\n\n"
        "- Write it once, as the very first line, with nothing before it.\n"
        "- The trailing «بالنسبة لسؤالك…» is a seam, not a sentence to repeat: "
        "carry it straight into what you were going to say, so the greeting and "
        "the answer read as one message.\n"
        "- **This overrides any rule telling you to open with the essence and "
        "skip preambles.** That rule still governs everything after this line — "
        "the substance starts immediately, with no second preamble.\n"
        "- Add no other greeting, and never mention that you were told to write "
        "it.\n"
    )


# ── Resolution ────────────────────────────────────────────────────────────────


def _load_conversation_message_count(
    supabase: SupabaseClient, conversation_id: str
) -> int | None:
    """``message_count`` for the conversation, or None if unreadable.

    Read BEFORE the turn ends, so the first turn of a conversation still reads
    0 — ``message_service`` only bumps the counter in its final step.
    """
    row = (
        supabase.table("conversations")
        .select("message_count")
        .eq("conversation_id", conversation_id)
        .maybe_single()
        .execute()
    )
    if row and getattr(row, "data", None):
        return int(row.data.get("message_count") or 0)
    return None


def _load_user_row(supabase: SupabaseClient, user_id: str) -> dict | None:
    row = (
        supabase.table("users")
        .select("preferred_name, full_name_ar, profession_group")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    return row.data if row and getattr(row, "data", None) else None


def _load_welcomed_at(supabase: SupabaseClient, user_id: str) -> str | None:
    row = (
        supabase.table("user_preferences")
        .select("preferences")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if row and getattr(row, "data", None):
        prefs = row.data.get("preferences") or {}
        value = prefs.get(WELCOMED_AT_KEY)
        return str(value) if value else None
    return None


def _went_quiet(
    supabase: SupabaseClient, user_id: str, conversation_id: str
) -> bool:
    """True when the user's last conversation has been idle past the gap.

    Excludes the conversation being answered right now (it was created seconds
    ago) and the shared «محادثة تجريبية» — that one row is owned by a real
    account and read by everybody, so for its owner it would otherwise look
    like fresh activity that suppresses the greeting.
    """
    from backend.app.services.demo_service import DEMO_CONVERSATION_ID

    rows = (
        supabase.table("conversations")
        .select("updated_at")
        .eq("user_id", user_id)
        .neq("conversation_id", conversation_id)
        .neq("conversation_id", DEMO_CONVERSATION_ID)
        .is_("deleted_at", "null")
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    data = getattr(rows, "data", None) or []
    if not data:
        # Welcomed before, but nothing else on the account (every other
        # conversation deleted). Not a returning user in any meaningful sense.
        return False
    last = data[0].get("updated_at")
    if not last:
        return False
    when = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when > timedelta(days=RETURN_GAP_DAYS)


def resolve_welcome(
    supabase: SupabaseClient, user_id: str, conversation_id: str
) -> WelcomeState | None:
    """Decide whether this turn opens with a welcome, and which one."""
    try:
        # Only ever the opening turn of a conversation.
        count = _load_conversation_message_count(supabase, conversation_id)
        if count is None or count > 0:
            return None

        user_row = _load_user_row(supabase, user_id) or {}
        call_name = resolve_call_name(
            user_row.get("preferred_name"), user_row.get("full_name_ar")
        )
        profession_group = user_row.get("profession_group")

        if _load_welcomed_at(supabase, user_id) is None:
            return WelcomeState("user_first", call_name, profession_group)

        if _went_quiet(supabase, user_id, conversation_id):
            return WelcomeState("return_gap", call_name, profession_group)

        return None
    except Exception as e:
        logger.warning("resolve_welcome failed (no welcome this turn): %s", e)
        return None


def mark_welcomed(supabase: SupabaseClient, user_id: str, state: WelcomeState | None) -> None:
    """Stamp ``welcomed_at`` — only for ``user_first``, only once.

    Call this when the turn has actually PRODUCED an answer. Stamping earlier
    loses the greeting entirely for anyone whose first turn pauses on a
    clarifying question: the responder never runs, and the resume leg is gated
    out because ``message_count`` is no longer 0 by then. Stamped on completion,
    that user simply gets their welcome in the next conversation instead.
    """
    if state is None or state.variant != "user_first":
        return
    try:
        from backend.app.services.preferences_service import update_preferences

        update_preferences(
            supabase,
            user_id,
            {WELCOMED_AT_KEY: datetime.now(timezone.utc).isoformat()},
        )
    except Exception as e:
        logger.warning("mark_welcomed failed for user %s: %s", user_id, e)


__all__ = [
    "RETURN_GAP_DAYS",
    "WELCOMED_AT_KEY",
    "Variant",
    "WelcomeState",
    "compose_opening",
    "mark_welcomed",
    "render_welcome_instruction",
    "resolve_welcome",
]
