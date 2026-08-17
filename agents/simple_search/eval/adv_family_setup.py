"""Step 0 of the adversarial-family lane — scratch conversation + ledger baseline.

Runs BEFORE any probe, and writes the results file first, so that a session
death at any later point still leaves the scratch id (for teardown) and the
money baseline (for accounting) on disk.
"""
from __future__ import annotations

import json

from adv_family_common import (  # noqa: E402
    RESULTS, USER_ID, ensure_scratch_conversation, flush, hr, ledger, load,
    service_client,
)


def main() -> None:
    sb = service_client()
    doc = load()

    convo = ensure_scratch_conversation(sb)
    doc["scratch_conversation_id"] = convo
    flush(doc)
    hr("SCRATCH CONVERSATION")
    print(f"[ADV-FAMILY] conversation_id = {convo}   (recorded → {RESULTS.name})")

    rows = ledger(sb)
    judgments = [r for r in rows if r["content_type"] == "judgment"]
    doc["ledger"]["before"] = {
        "total": len(rows),
        "judgment": len(judgments),
        "regulation": sum(1 for r in rows if r["content_type"] == "regulation"),
        "judgment_unlock_ids": sorted(str(r["unlock_id"]) for r in judgments),
        "judgment_content_ids": sorted(str(r["content_id"]) for r in judgments),
        "newest": max((str(r["unlocked_at"]) for r in judgments), default=None),
    }
    flush(doc)
    hr("LEDGER BASELINE")
    print(json.dumps({k: v for k, v in doc["ledger"]["before"].items()
                      if k != "judgment_unlock_ids"}, ensure_ascii=False, indent=2))

    # Account shape, for the same reason: it is the restore target.
    convos = (sb.table("conversations").select("conversation_id", count="exact")
              .eq("user_id", USER_ID).is_("deleted_at", "null").execute())
    doc["account_before"] = {"conversations": convos.count}
    flush(doc)
    print(f"conversations (incl. scratch): {convos.count}")


if __name__ == "__main__":
    main()
