"""Router A/B re-run — HARD-delete the ``[AB-RERUN]`` scratch conversation.

FK-safe order: workspace_item_references → workspace_items → message_attachments
→ messages → paused_runs → the conversation row.

Scoped by ``user_id`` AND the exact scratch title, so it can never reach one of
the account's 136 real conversations. Prints a post-delete verification count.

    .venv/Scripts/python.exe agents/simple_search/eval/ab_teardown.py
"""
from __future__ import annotations

from ab_common import SCRATCH_TITLE, USER_ID, hr, service_client  # noqa: E402


def main() -> int:
    sb = service_client()
    hr(f"TEARDOWN — «{SCRATCH_TITLE}»")

    convos = (
        sb.table("conversations").select("conversation_id, title_ar")
        .eq("user_id", USER_ID).eq("title_ar", SCRATCH_TITLE).execute()
    ).data or []
    if not convos:
        print(f"nothing to delete — no «{SCRATCH_TITLE}» conversation")
        return 0

    for conv in convos:
        cid = str(conv["conversation_id"])
        print(f"deleting conversation {cid}")

        items = (
            sb.table("workspace_items").select("item_id")
            .eq("conversation_id", cid).execute()
        ).data or []
        item_ids = [i["item_id"] for i in items]
        print(f"  workspace_items: {len(item_ids)}")
        for i in range(0, len(item_ids), 50):
            sb.table("workspace_item_references").delete().in_(
                "wi_id", item_ids[i:i + 50]
            ).execute()
        if item_ids:
            sb.table("workspace_items").delete().eq("conversation_id", cid).execute()

        # message_attachments keys on message_id only — no conversation_id column.
        msgs = (
            sb.table("messages").select("message_id")
            .eq("conversation_id", cid).execute()
        ).data or []
        msg_ids = [m["message_id"] for m in msgs]
        print(f"  messages: {len(msg_ids)}")
        for i in range(0, len(msg_ids), 50):
            sb.table("message_attachments").delete().in_(
                "message_id", msg_ids[i:i + 50]
            ).execute()
        if msg_ids:
            sb.table("messages").delete().eq("conversation_id", cid).execute()
        sb.table("paused_runs").delete().eq("conversation_id", cid).execute()
        sb.table("conversations").delete().eq("conversation_id", cid).execute()

    left = (
        sb.table("conversations").select("conversation_id", count="exact")
        .eq("user_id", USER_ID).eq("title_ar", SCRATCH_TITLE).execute()
    ).count
    total = (
        sb.table("conversations").select("conversation_id", count="exact")
        .eq("user_id", USER_ID).is_("deleted_at", "null").execute()
    ).count
    print(f"\nscratch conversations remaining: {left}")
    print(f"user's live conversations now:   {total}")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
