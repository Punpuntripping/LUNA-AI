"""MONEY+STATE lane — SETUP. Scratch conversation id first, then the baseline.

The brief's ordering rule: the scratch conversation id is flushed to
``adv_money_state_results.json`` BEFORE anything else happens, so a run that
dies at any later point still leaves a hard-deletable handle behind.

Also snapshots the ledger fingerprint (count + newest per content_type) and the
account's open pause rows, which are the two things every fixture below is
measured against and restored to.

    .venv/Scripts/python.exe agents/simple_search/eval/adv_setup.py
"""
from __future__ import annotations

import json

from adv_ms_common import (  # noqa: E402
    PROTECTED_PAUSE_RUN_ID, SCRATCH_TITLE, USER_ID, ensure_scratch_conversation,
    hr, ledger_fingerprint, pause_rows, save, load, service_client,
)


def main() -> int:
    sb = service_client()
    hr(f"SETUP — «{SCRATCH_TITLE}» scratch conversation")

    # ── 1. The id, FLUSHED FIRST. Nothing else happens before this lands. ────
    convo_id = ensure_scratch_conversation(sb)
    doc = load()
    doc["scratch_conversation_id"] = convo_id
    save(doc)
    print(f"conversation_id: {convo_id}   [FLUSHED FIRST]")

    # ── 2. Baseline ledger + pause state ────────────────────────────────────
    hr("BASELINE — ledger + pause rows")
    fp = ledger_fingerprint(sb)
    print(f"ledger total: {fp['total']}")
    for ct, slot in sorted(fp["by_content_type"].items()):
        print(f"  {ct:<12} {slot['count']:>3}  newest {slot['newest']}")

    pauses = pause_rows(sb)
    print(f"\nopen paused_runs on this account: {len(pauses)}")
    for p in pauses:
        prot = " [PROTECTED — never touched]" if p["run_id"] == PROTECTED_PAUSE_RUN_ID else ""
        print(f"  {p['run_id']} · {p['agent_family']} · convo {p['conversation_id']}{prot}")

    scratch_pauses = pause_rows(sb, convo_id)
    print(f"pauses on the scratch conversation: {len(scratch_pauses)} (must be 0)")

    doc = load()
    doc["baseline"] = {
        "ledger": fp,
        "open_pauses": pauses,
        "scratch_pauses_at_start": len(scratch_pauses),
        "expected_by_brief": {"judgment": 17, "total": 53, "newest": "2026-08-14"},
    }
    save(doc)

    ok = (fp["by_content_type"].get("judgment", {}).get("count") == 17
          and fp["total"] == 53 and not scratch_pauses)
    print(f"\nbaseline matches the brief (17 judgment / 53 total): {ok}")
    print(json.dumps({"conversation_id": convo_id, "ledger_total": fp["total"]},
                     ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
