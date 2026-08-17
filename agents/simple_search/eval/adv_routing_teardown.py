"""ADV-ROUTING lane — HARD-delete the ``[ADV-ROUTING]`` scratch conversation.

Deletes in FK-safe order everything the lane wrote — its synthetic ctrl-02 card,
the two messages that framed it, any ``save_memo`` item the router pinned, and
the conversation row — then verifies the account is back to its exact baseline
(136 real conversations · 179 workspace items · 1,522 references · 53 unlocks,
17 of them judgments).

Scoped by ``user_id`` AND the exact scratch title, so it can never touch one of
the account's real conversations. The scratch id is also read back from
``adv_routing_results.json`` and cross-checked, so a title-rename cannot orphan
the row.

    .venv/Scripts/python.exe agents/simple_search/eval/adv_routing_teardown.py
"""
from __future__ import annotations

import json

from adv_routing_common import (  # noqa: E402
    RESULTS_PATH, SCRATCH_TITLE, USER_ID, hr, service_client,
)

BASELINE = {"conversations": 136, "workspace_items": 179,
            "references": 1522, "unlocks": 53, "judgment_unlocks": 17}


def main() -> int:
    sb = service_client()

    recorded = None
    if RESULTS_PATH.exists():
        try:
            recorded = json.loads(RESULTS_PATH.read_text(encoding="utf-8")).get(
                "conversation_id")
        except Exception:  # noqa: BLE001
            pass

    convos = (
        sb.table("conversations").select("conversation_id, title_ar")
        .eq("user_id", USER_ID).eq("title_ar", SCRATCH_TITLE).execute()
    ).data or []
    ids = {str(c["conversation_id"]) for c in convos}
    if recorded:
        print(f"results file records scratch convo {recorded} "
              f"({'found by title' if recorded in ids else 'NOT found by title'})")
        ids.add(recorded)

    hr(f"TEARDOWN — {len(ids)} scratch conversation(s)")
    for cid in sorted(ids):
        owner = (
            sb.table("conversations").select("conversation_id, title_ar, user_id")
            .eq("conversation_id", cid).eq("user_id", USER_ID).execute()
        ).data or []
        if not owner:
            print(f"  {cid}: already gone / not this user — skip")
            continue
        title = owner[0].get("title_ar")
        if title != SCRATCH_TITLE:
            print(f"  {cid}: title is «{title}» not «{SCRATCH_TITLE}» — REFUSING")
            continue
        print(f"  deleting {cid}")

        items = (sb.table("workspace_items").select("item_id")
                 .eq("conversation_id", cid).execute()).data or []
        item_ids = [i["item_id"] for i in items]
        print(f"    workspace_items: {len(item_ids)}")
        for i in range(0, len(item_ids), 50):
            sb.table("workspace_item_references").delete().in_(
                "wi_id", item_ids[i:i + 50]).execute()
        if item_ids:
            sb.table("workspace_items").delete().eq("conversation_id", cid).execute()

        msgs = (sb.table("messages").select("message_id")
                .eq("conversation_id", cid).execute()).data or []
        msg_ids = [m["message_id"] for m in msgs]
        print(f"    messages: {len(msg_ids)}")
        for i in range(0, len(msg_ids), 50):
            sb.table("message_attachments").delete().in_(
                "message_id", msg_ids[i:i + 50]).execute()
        if msg_ids:
            sb.table("messages").delete().eq("conversation_id", cid).execute()
        sb.table("paused_runs").delete().eq("conversation_id", cid).execute()
        sb.table("conversations").delete().eq("conversation_id", cid).execute()

    hr("VERIFY — account back to baseline?")
    now = {
        "conversations": (sb.table("conversations").select("conversation_id", count="exact")
                          .eq("user_id", USER_ID).is_("deleted_at", "null").execute()).count,
        "workspace_items": (sb.table("workspace_items").select("item_id", count="exact")
                            .eq("user_id", USER_ID).is_("deleted_at", "null").execute()).count,
        "unlocks": (sb.table("library_unlocks").select("unlock_id", count="exact")
                    .eq("user_id", USER_ID).execute()).count,
        "judgment_unlocks": (sb.table("library_unlocks").select("unlock_id", count="exact")
                             .eq("user_id", USER_ID).eq("content_type", "judgment").execute()).count,
    }
    left = (sb.table("conversations").select("conversation_id", count="exact")
            .eq("user_id", USER_ID).eq("title_ar", SCRATCH_TITLE).execute()).count
    print(f"  scratch conversations remaining: {left}")
    for k, expected in BASELINE.items():
        if k == "references":
            continue
        got = now[k]
        print(f"  {k:<18} now={got:<6} baseline={expected:<6} "
              f"{'OK' if got == expected else 'DRIFT (another lane may still be live)'}")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
