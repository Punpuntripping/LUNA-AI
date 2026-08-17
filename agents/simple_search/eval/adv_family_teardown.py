"""Teardown + the money reconciliation. Run LAST.

Hard-deletes everything this lane created — the ``[ADV-FAMILY]`` conversation,
its scratch WI, and every card the probes published into it — then re-reads
``library_unlocks`` and diffs it against the baseline captured by
``adv_family_setup.py``.

The reconciliation is a **set** diff on ``unlock_id``, not a count diff. Lane 3
owns the unlock fixtures and writes to the same ledger concurrently; a raw count
went briefly *negative* mid-run (a -3 observed at B1), which is lane 3's rows
appearing and being cleaned, not anything of ours. Only ids absent from the
baseline and present now could be ours.
"""
from __future__ import annotations

import json

from adv_family_common import (  # noqa: E402
    USER_ID, flush, hr, ledger, load, service_client,
)


def main() -> None:
    sb = service_client()
    doc = load()
    convo = doc["scratch_conversation_id"]

    # ── 1. money reconciliation FIRST (before anything is deleted) ───────────
    hr("LEDGER RECONCILIATION")
    base = doc["ledger"]["before"]
    rows = ledger(sb)
    j = [r for r in rows if r["content_type"] == "judgment"]
    now_ids = {str(r["unlock_id"]) for r in j}
    was_ids = set(base["judgment_unlock_ids"])
    added = sorted(now_ids - was_ids)
    removed = sorted(was_ids - now_ids)
    print(f"judgment unlocks: baseline {base['judgment']} → now {len(j)}")
    print(f"  added (could be OURS): {added or 'none'}")
    print(f"  missing vs baseline:   {removed or 'none'}")
    print(f"  all unlocks: baseline {base['total']} → now {len(rows)}")
    doc["ledger"]["after"] = {
        "total": len(rows), "judgment": len(j),
        "regulation": sum(1 for r in rows if r["content_type"] == "regulation"),
        "added_vs_baseline": added, "missing_vs_baseline": removed,
        "newest": max((str(r["unlocked_at"]) for r in j), default=None),
    }
    doc["ledger"]["verdict"] = (
        "CLEAN — zero unlocks caused by this lane" if not added
        else f"ATTENTION — {len(added)} new judgment unlock(s) to account for"
    )
    flush(doc)
    print(f"  → {doc['ledger']['verdict']}")

    # ── 2. what did the lane leave in the scratch conversation? ─────────────
    hr("SCRATCH CONTENTS")
    wis = (sb.table("workspace_items").select("item_id, kind, title, created_at")
           .eq("user_id", USER_ID).eq("conversation_id", convo).execute()).data or []
    msgs = (sb.table("messages").select("message_id")
            .eq("conversation_id", convo).execute()).data or []
    pauses = (sb.table("paused_runs").select("run_id")
              .eq("conversation_id", convo).execute()).data or []
    print(f"conversation {convo}: {len(wis)} WIs · {len(msgs)} messages · "
          f"{len(pauses)} pause rows")
    for w in wis:
        print(f"  {w['item_id']} {w['kind']:14s} {w['title']}")
    doc["teardown"] = {"wis": [{"item_id": str(w["item_id"]), "kind": w["kind"],
                                "title": w["title"]} for w in wis],
                       "messages": len(msgs), "pauses": len(pauses)}
    flush(doc)

    # ── 3. delete, children first ───────────────────────────────────────────
    hr("DELETING")
    n_refs = 0
    for w in wis:
        d = (sb.table("workspace_item_references").delete()
             .eq("wi_id", str(w["item_id"])).execute()).data or []
        n_refs += len(d)
    print(f"refs deleted: {n_refs}")
    for w in wis:
        sb.table("workspace_items").delete().eq("item_id", str(w["item_id"])).execute()
    print(f"WIs deleted: {len(wis)}")
    sb.table("paused_runs").delete().eq("conversation_id", convo).execute()
    sb.table("messages").delete().eq("conversation_id", convo).execute()
    sb.table("conversations").delete().eq("conversation_id", convo).execute()
    print(f"conversation {convo} hard-deleted")

    # ── 4. prove the account is back to baseline ────────────────────────────
    hr("RESTORE CHECK")
    left_c = (sb.table("conversations").select("conversation_id", count="exact")
              .eq("user_id", USER_ID).is_("deleted_at", "null").execute()).count
    left_w = (sb.table("workspace_items").select("item_id", count="exact")
              .eq("user_id", USER_ID).is_("deleted_at", "null").execute()).count
    still = (sb.table("conversations").select("conversation_id")
             .eq("conversation_id", convo).execute()).data or []
    print(f"conversations now: {left_c} (baseline incl. scratch was "
          f"{doc.get('account_before', {}).get('conversations')})")
    print(f"workspace_items now: {left_w} (expected 179)")
    print(f"scratch conversation still present: {bool(still)}")
    doc["teardown"] |= {"refs_deleted": n_refs, "wis_deleted": len(wis),
                        "conversations_after": left_c,
                        "workspace_items_after": left_w,
                        "scratch_still_present": bool(still)}
    flush(doc)
    print("\n" + json.dumps(doc["ledger"], ensure_ascii=False, indent=2,
                            default=str)[:1200])


if __name__ == "__main__":
    main()
