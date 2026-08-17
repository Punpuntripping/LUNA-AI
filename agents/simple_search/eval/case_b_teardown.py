"""Case-B eval — HARD-delete the ``[EVAL-CASE-B]`` scratch conversation.

Deletes, in FK-safe order, everything the harness wrote:

    workspace_item_references (of the conversation's items)
      → workspace_items → messages → the conversation row itself.

Scoped by ``user_id`` AND the exact scratch title — it can never touch one of
the account's 136 real conversations. Prints a post-delete verification count.

    python agents/simple_search/eval/case_b_teardown.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

from dotenv import load_dotenv

load_dotenv()

from agents.simple_search.eval import case_b_fixtures as FX  # noqa: E402
from shared.db.client import get_supabase_client  # noqa: E402


def main() -> int:
    sb = get_supabase_client()
    convos = (
        sb.table("conversations").select("conversation_id, title_ar")
        .eq("user_id", FX.USER_ID).eq("title_ar", FX.SCRATCH_CONVO_TITLE)
        .execute()
    ).data or []
    if not convos:
        print(f"nothing to delete — no «{FX.SCRATCH_CONVO_TITLE}» conversation")
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

    # Verify.
    left_c = (
        sb.table("conversations").select("conversation_id", count="exact")
        .eq("user_id", FX.USER_ID).eq("title_ar", FX.SCRATCH_CONVO_TITLE).execute()
    ).count
    total = (
        sb.table("conversations").select("conversation_id", count="exact")
        .eq("user_id", FX.USER_ID).is_("deleted_at", "null").execute()
    ).count
    print(f"\nscratch conversations remaining: {left_c}")
    print(f"user's live conversations now:   {total}")
    return 0 if left_c == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
