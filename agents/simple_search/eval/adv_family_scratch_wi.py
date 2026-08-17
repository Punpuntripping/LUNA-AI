"""Build the ONE scratch WI the abort probes need. Idempotent; torn down at end.

`hair-01` and its mirror («قارن الحكمين») both require a WI citing **exactly
two** rulings — «الحكمين» has to be literal, or an ask_user is the correct
answer and the abort guard is never exercised. **The account has no such WI**:
measured over all 179 WIs, the case-ref counts are {1, 3, 3, 3, 4, 4, 5, 6, 7,
8, 9, 18}. So one is constructed here, in the lane's own scratch conversation.

Two constraints drove the choice of rulings:

* **both are ALREADY unlocked** (`library_unlocks`, surface=library, since
  2026-08-02/14), so a fan-out over them spends nothing — the abort probes can
  fire in both directions without touching the ledger;
* **their subjects are far apart** (a تجارية inheritance dispute over a travel
  agency vs a تأميني vehicle-damage claim), so «الحكم الثاني» / «قارن الحكمين»
  are unambiguous and a mis-selection is visible rather than arguable.

`dac45545` additionally carries `appeal_*` on the same row — which is what
`corpus-01` («الحكم الابتدائي والاستئنافي» = one document wearing two names)
needs, so the same scratch card serves that probe too.
"""
from __future__ import annotations

import json

from adv_family_common import (  # noqa: E402
    USER_ID, flush, hr, load, service_client, short,
)

SCRATCH_WI_TITLE = "[ADV-FAMILY] حكمان للاختبار"

#: (case_id, why it is here). Both are in `library_unlocks` for this user.
RULINGS = [
    ("dac45545-4506-4da7-8bc6-053156b2d3b7",
     "تجارية · ورثة وكالة عامر للسفر والسياحة · carries appeal_* on the same row"),
    ("0cc3a6ae-a084-44d8-afc3-cfda25dfab76",
     "تأميني · تعويض القيمة التأمينية لمركبة · no appeal"),
]


def main() -> None:
    sb = service_client()
    doc = load()
    convo = doc["scratch_conversation_id"]
    assert convo, "run adv_family_setup.py first"

    existing = (sb.table("workspace_items").select("item_id")
                .eq("user_id", USER_ID).eq("conversation_id", convo)
                .eq("title", SCRATCH_WI_TITLE).is_("deleted_at", "null")
                .limit(1).execute()).data or []
    if existing:
        wi_id = str(existing[0]["item_id"])
        print(f"scratch WI already exists: {wi_id}")
    else:
        row = (sb.table("workspace_items").insert({
            "user_id": USER_ID,
            "conversation_id": convo,
            "kind": "agent_search",
            "created_by": "agent",
            "agent_family": "deep_search",
            "title": SCRATCH_WI_TITLE,
            "content_md": "بطاقة اختبار تحمل حكمين فقط.",
            "metadata": {"ref_count": 2, "cited_count": 2, "adv_family_scratch": True},
        }).execute()).data
        wi_id = str(row[0]["item_id"])
        print(f"scratch WI created: {wi_id}")

    have = {int(r["n"]) for r in ((sb.table("workspace_item_references")
            .select("n").eq("wi_id", wi_id).execute()).data or [])}
    meta = []
    for i, (case_id, why) in enumerate(RULINGS, start=1):
        c = (sb.table("cases").select(
            "id, case_ref, case_number, court, court_level, appeal_judgment_number, "
            "appeal_court, appeal_result, summary"
        ).eq("id", case_id).limit(1).execute()).data[0]
        if i not in have:
            sb.table("workspace_item_references").insert({
                "wi_id": wi_id, "domain": "cases", "n": i, "used": True,
                "relevance": "high", "sub_queries": [],
                "ref_id": f"case:{c['case_ref']}", "item_id": case_id,
            }).execute()
        meta.append({"n": i, "case_id": case_id, "case_ref": c["case_ref"],
                     "case_number": c["case_number"], "court": c["court"],
                     "court_level": c["court_level"],
                     "has_appeal": bool(c.get("appeal_judgment_number")),
                     "appeal_court": c.get("appeal_court"),
                     "appeal_result": c.get("appeal_result"),
                     "summary_head": short(c.get("summary") or "", 220),
                     "why": why})

    hr("SCRATCH WI")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    doc["scratch_wi"] = {"wi_id": wi_id, "title": SCRATCH_WI_TITLE, "refs": meta}
    flush(doc)
    print(f"\nflushed scratch_wi {wi_id}")


if __name__ == "__main__":
    main()
