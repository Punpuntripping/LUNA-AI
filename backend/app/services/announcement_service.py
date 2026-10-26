"""«رسائل ريحان» — product announcements (.claude/plans/announcements.md).

Sync functions, run through ``run_db`` from ``api/announcements.py``.

Visibility has ONE definition: the ``get_user_announcements(p_user_id)`` RPC
(migration 189) — published, and either for everyone or for a user on an
unexpired purchasable plan. ``mark_read`` re-reads that same list before it
writes, so a receipt can only ever be stored for a message the caller can see.

The service-role client bypasses RLS (both tables have no client policies), so
every read and write here is scoped to the caller's ``user_id``.
"""
from __future__ import annotations

from typing import Any

from supabase import Client as SupabaseClient


def _visible_rows(supabase: SupabaseClient, user_id: str) -> list[dict[str, Any]]:
    result = supabase.rpc("get_user_announcements", {"p_user_id": user_id}).execute()
    return list(result.data or [])


def list_for_user(supabase: SupabaseClient, user_id: str) -> dict[str, Any]:
    """Newest first (the RPC orders and caps at 50), in the contract shape of
    ``frontend/lib/contracts/announcements.ts``."""
    items = [
        {
            "announcement_id": row["announcement_id"],
            "title": row["title"],
            "body_md": row["body_md"],
            "link_label": row.get("link_label"),
            "link_url": row.get("link_url"),
            "published_at": row["published_at"],
            "read": bool(row.get("is_read")),
        }
        for row in _visible_rows(supabase, user_id)
    ]
    return {
        "items": items,
        "unread_count": sum(1 for item in items if not item["read"]),
    }


def mark_read(supabase: SupabaseClient, user_id: str, ids: list[str]) -> int:
    """Store a read receipt for each of ``ids`` the caller can see and has not
    read yet. Ids outside the caller's list are dropped silently (no oracle for
    which messages exist). Returns how many receipts were written."""
    wanted = {i.lower() for i in ids}
    fresh = [
        row["announcement_id"]
        for row in _visible_rows(supabase, user_id)
        if str(row["announcement_id"]).lower() in wanted and not row.get("is_read")
    ]
    if not fresh:
        return 0
    supabase.table("announcement_reads").upsert(
        [{"user_id": user_id, "announcement_id": a_id} for a_id in fresh],
        on_conflict="user_id,announcement_id",
        ignore_duplicates=True,
    ).execute()
    return len(fresh)
