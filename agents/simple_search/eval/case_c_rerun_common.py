"""Shared bootstrap for the Case-C **re-run** (eval lane 3 of 3, 2026-08-16).

Same corpus, same account, same fixtures as ``case_c_common`` — the only thing
this adds is a *named*, teardown-able scratch conversation, because the one the
original run used (``3101cee8…``) was hard-deleted at the end of that run.

Nothing here writes to the read-only corpus. Everything the re-run creates lands
in the single ``[RERUN-CASE-C]`` conversation, which ``case_c_rerun_teardown.py``
hard-deletes and verifies.

    .venv/Scripts/python.exe agents/simple_search/eval/case_c_rerun_routing.py
"""
from __future__ import annotations

from case_c_common import USER_ID, hr, service_client, short  # noqa: F401,E402

SCRATCH_TITLE = "[RERUN-CASE-C]"


def ensure_scratch_conversation(sb) -> str:
    """Create (or find) the one scratch conversation this lane may write to."""
    rows = (
        sb.table("conversations").select("conversation_id")
        .eq("user_id", USER_ID).eq("title_ar", SCRATCH_TITLE)
        .is_("deleted_at", "null").limit(1).execute()
    ).data or []
    if rows:
        return str(rows[0]["conversation_id"])
    new = (
        sb.table("conversations")
        .insert({"user_id": USER_ID, "title_ar": SCRATCH_TITLE})
        .execute()
    ).data
    return str(new[0]["conversation_id"])


def judgment_ledger(sb, user_id: str = USER_ID) -> list[dict]:
    """Every ``judgment`` row of this user's unlock ledger, newest last."""
    return list((
        sb.table("library_unlocks")
        .select("unlock_id, content_type, content_id, surface, cost, unlocked_at")
        .eq("user_id", user_id).eq("content_type", "judgment")
        .order("unlocked_at").execute()
    ).data or [])


__all__ = [
    "USER_ID", "SCRATCH_TITLE", "service_client", "ensure_scratch_conversation",
    "judgment_ledger", "hr", "short",
]
