"""RE-RUN lane SETUP — create the `[RERUN-RESOLUTION]` scratch conversation.

No workspace items are carried: this lane measures the router's *unattached*
behaviour (the tie-break watch item) and the resolver legs, neither of which
needs a carry.

    python agents/simple_search/eval/rerun_setup.py
"""
from __future__ import annotations

import json
from pathlib import Path

from rerun_common import USER_ID, ensure_scratch_conversation, hr, service_client


def main() -> int:
    sb = service_client()
    hr("SETUP — [RERUN-RESOLUTION] scratch conversation")

    convo_id = ensure_scratch_conversation(sb)
    print(f"conversation_id: {convo_id}")

    # Prove it is empty — the router reads workspace items for the conversation
    # it is handed, so a non-empty scratch would change what is being measured.
    wis = (sb.table("workspace_items").select("item_id")
           .eq("conversation_id", convo_id).execute()).data or []
    msgs = (sb.table("messages").select("message_id")
            .eq("conversation_id", convo_id).execute()).data or []
    live = (sb.table("conversations").select("conversation_id", count="exact")
            .eq("user_id", USER_ID).is_("deleted_at", "null").execute())
    print(f"  workspace_items: {len(wis)}")
    print(f"  messages:        {len(msgs)}")
    print(f"  live conversations on the account (incl. this scratch): {live.count}")
    assert not wis and not msgs

    Path(__file__).with_name("rerun_setup.json").write_text(
        json.dumps({"conversation_id": convo_id,
                    "live_conversations_including_scratch": live.count},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("\nsetup ok → rerun_setup.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
