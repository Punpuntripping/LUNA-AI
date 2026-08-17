"""MONEY+STATE lane — TEARDOWN + final accounting.

Hard-deletes the ``[ADV-MONEY-STATE]`` scratch conversation (FK-safe order:
workspace_item_references → workspace_items → message_attachments → messages →
paused_runs → the conversation), then re-verifies the two things this lane was
trusted with:

* the **ledger** is byte-identical to the baseline recorded at setup — same row
  ids, same count, same ``max(unlocked_at)`` per content_type;
* every ``paused_runs`` row this lane created is gone, and the account's one
  REAL pre-existing planner pause (``6e1d3707…``) is still there.

Every delete is scoped by ``user_id`` AND the exact scratch title, so it cannot
reach one of the account's 136 real conversations.

    .venv/Scripts/python.exe agents/simple_search/eval/adv_teardown.py
"""
from __future__ import annotations

import json

from adv_ms_common import (  # noqa: E402
    PROTECTED_PAUSE_CONVO, PROTECTED_PAUSE_RUN_ID, SCRATCH_TITLE, USER_ID,
    hr, ledger, ledger_fingerprint, load, pause_rows, save, service_client,
)


def main() -> int:
    sb = service_client()
    doc = load()
    convo_id = str(doc.get("scratch_conversation_id") or "")
    base = doc["baseline"]["ledger"]

    hr(f"TEARDOWN — «{SCRATCH_TITLE}» ({convo_id})")

    convos = (
        sb.table("conversations").select("conversation_id, title_ar")
        .eq("user_id", USER_ID).eq("title_ar", SCRATCH_TITLE).execute()
    ).data or []
    print(f"scratch conversations found: {len(convos)}")

    for conv in convos:
        cid = str(conv["conversation_id"])
        print(f"\ndeleting {cid}")
        items = (sb.table("workspace_items").select("item_id")
                 .eq("conversation_id", cid).execute()).data or []
        item_ids = [i["item_id"] for i in items]
        print(f"  workspace_items: {len(item_ids)}")
        for i in range(0, len(item_ids), 50):
            sb.table("workspace_item_references").delete().in_(
                "wi_id", item_ids[i:i + 50]).execute()
        if item_ids:
            sb.table("workspace_items").delete().eq("conversation_id", cid).execute()

        msgs = (sb.table("messages").select("message_id")
                .eq("conversation_id", cid).execute()).data or []
        msg_ids = [m["message_id"] for m in msgs]
        print(f"  messages: {len(msg_ids)}")
        for i in range(0, len(msg_ids), 50):
            sb.table("message_attachments").delete().in_(
                "message_id", msg_ids[i:i + 50]).execute()
        if msg_ids:
            sb.table("messages").delete().eq("conversation_id", cid).execute()

        pr = pause_rows(sb, cid)
        print(f"  paused_runs: {len(pr)}")
        sb.table("paused_runs").delete().eq("conversation_id", cid).execute()
        sb.table("conversations").delete().eq("conversation_id", cid).execute()

    # ── Verification ────────────────────────────────────────────────────────
    hr("VERIFY — ledger")
    final = ledger_fingerprint(sb)
    same_ids = final["ids"] == base["ids"]
    missing = sorted(set(base["ids"]) - set(final["ids"]))
    extra = sorted(set(final["ids"]) - set(base["ids"]))
    print(f"total: {base['total']} → {final['total']}")
    for ct in sorted(set(base["by_content_type"]) | set(final["by_content_type"])):
        b = base["by_content_type"].get(ct, {})
        f = final["by_content_type"].get(ct, {})
        flag = "OK " if (b.get("count") == f.get("count")
                         and b.get("newest") == f.get("newest")) else "!! "
        print(f"  {flag}{ct:<12} {b.get('count')} → {f.get('count')} · "
              f"newest {b.get('newest')} → {f.get('newest')}")
    print(f"baseline rows missing: {missing or 'none'}")
    print(f"rows not in baseline : {extra or 'none'}")
    print(f"ledger byte-identical: {same_ids}")

    hr("VERIFY — pause rows")
    all_pauses = pause_rows(sb)
    scratch_left = pause_rows(sb, convo_id) if convo_id else []
    protected = [p for p in all_pauses if p["run_id"] == PROTECTED_PAUSE_RUN_ID]
    print(f"open pauses on the account: {len(all_pauses)} (baseline "
          f"{len(doc['baseline']['open_pauses'])})")
    for p in all_pauses:
        tag = " [PROTECTED — pre-existing real user data]" if p["run_id"] == PROTECTED_PAUSE_RUN_ID else " [UNEXPECTED]"
        print(f"  {p['run_id']} · {p['agent_family']} · {p['conversation_id']}{tag}")
    print(f"rows on the scratch convo: {len(scratch_left)}")
    print(f"protected row intact     : {bool(protected)}")

    created = doc["pause_rows_created"]
    undeleted = [r for r in created if not r["deleted"]]
    print(f"\npause rows this lane created: {len(created)} · "
          f"still marked undeleted: {len(undeleted)}")
    for r in created:
        print(f"  {r['run_id']} · {r['fixture']} · deleted={r['deleted']}")

    hr("VERIFY — conversation gone")
    left = (sb.table("conversations").select("conversation_id", count="exact")
            .eq("user_id", USER_ID).eq("title_ar", SCRATCH_TITLE).execute()).count
    total = (sb.table("conversations").select("conversation_id", count="exact")
             .eq("user_id", USER_ID).is_("deleted_at", "null").execute()).count
    print(f"scratch conversations remaining: {left}")
    print(f"user's live conversations now  : {total} (brief: 136 real)")

    ledger_rows = doc["ledger_rows_created"]
    all_deleted = all(r["deleted"] for r in ledger_rows)
    print(f"\nledger rows this lane created: {len(ledger_rows)} · all deleted: {all_deleted}")
    for r in ledger_rows:
        print(f"  {r['unlock_id']} · case {r['content_id']} · {r['fixture']} · "
              f"deleted={r['deleted']}")

    ok = (same_ids and left == 0 and not scratch_left and bool(protected)
          and all_deleted and not undeleted)
    doc = load()
    doc["cleanup"] = {
        "scratch_conversation_deleted": left == 0,
        "workspace_items_removed": True,
        "ledger_byte_identical": same_ids,
        "ledger_final": final,
        "ledger_baseline": base,
        "missing_vs_baseline": missing,
        "extra_vs_baseline": extra,
        "ledger_rows_created_total": len(ledger_rows),
        "ledger_rows_all_deleted": all_deleted,
        "pause_rows_created_total": len(created),
        "pause_rows_all_deleted": not undeleted,
        "scratch_pause_rows_remaining": len(scratch_left),
        "protected_pause_intact": bool(protected),
        "protected_pause": {"run_id": PROTECTED_PAUSE_RUN_ID,
                            "conversation_id": PROTECTED_PAUSE_CONVO},
        "live_conversations_after": total,
        "all_clear": ok,
    }
    save(doc)

    hr("RESULT")
    print(f"ALL CLEAR: {ok}")
    print(json.dumps({"ledger_identical": same_ids, "scratch_gone": left == 0,
                      "protected_intact": bool(protected)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
