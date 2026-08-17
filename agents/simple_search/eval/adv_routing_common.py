"""Shared bootstrap for the ADVERSARIAL ROUTING lane (retest lane 1 of 3, 2026-08-16).

Measures the **router-level** surfaces of ``fixtures_adversarial`` plus a
re-measurement of the comparison leg, which the §13g gate landed on AFTER the
last measurement (0/9, all ``chat_response``).

Same account, same read-only corpus, same ``run_router`` entry point as
``case_c_rerun_common``. What this adds:

* a **flush-after-every-probe** results file — the previous batch of lanes died
  on a session limit mid-run and only what had reached disk survived;
* a named, teardown-able ``[ADV-ROUTING]`` scratch conversation whose id is
  written to that file **before the first router call**, so a coordinator can
  clean up even if this process never returns;
* one **synthetic prior simple_search card** for ``ctrl-02`` — the account holds
  zero ``agent_family='simple_search'`` workspace items (verified in SQL), so
  the fixture's setup ("a previous simple_search turn published a card holding
  the FULL ruling text") does not exist anywhere and has to be built. It is
  built from a REAL ``cases`` row, lands in the scratch conversation only, and
  is deleted with it.

Nothing here modifies a production module. Read-only against the 136 real
conversations.

    .venv/Scripts/python.exe agents/simple_search/eval/adv_routing.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from case_c_common import USER_ID, hr, service_client, short  # noqa: F401,E402

SCRATCH_TITLE = "[ADV-ROUTING]"

RESULTS_PATH = Path(__file__).with_name("adv_routing_results.json")

# ── Borrowed real contexts (READ-ONLY) ───────────────────────────────────────
# Verified live 2026-08-16 via SQL, not taken from the older eval headers.
#: WI-1 attachment · WI-2 agent_search, 28 refs (18 cases, incl. n=2 regulations)
S_CASES = "2ff014cd-9197-4c15-8991-c1ec045a5902"
#: WI-1/2/3 agent_search (WI-3 = 12 refs, 8 cases) · WI-4 agent_writing
S_HEIRS = "e70cecfa-56ec-4788-b820-69b1d0b0ad1b"
#: the ONLY workspace item in the account citing EXACTLY ONE ruling — the true
#: ctrl-01 setup ("WI-N cites one ruling; the number is printed on its card").
S_ONECASE = "c7bf1e12-dc84-4c52-8e86-9e61d2caff2b"

#: The ruling the synthetic ctrl-02 card holds, whole. Real row, commercial court
#: of الرياض, upheld on appeal — so «هل الحكم نهائي؟» IS answerable from the card,
#: which is the entire point of the control.
CTRL02_CASE_ID = "6dde864a-f288-42e8-9fc3-16fce6125f2e"


def ensure_scratch_conversation(sb) -> str:
    """Create (or find) the one scratch conversation this lane may write to."""
    rows = (
        sb.table("conversations").select("conversation_id")
        .eq("user_id", USER_ID).eq("title_ar", SCRATCH_TITLE)
        .is_("deleted_at", "null").limit(1).execute()
    ).data or []
    if rows:
        return str(rows[0]["conversation_id"])
    new = (
        sb.table("conversations")
        .insert({"user_id": USER_ID, "title_ar": SCRATCH_TITLE})
        .execute()
    ).data
    return str(new[0]["conversation_id"])


# ── The results file — written after EVERY probe ─────────────────────────────


class Results:
    """Append-and-flush results writer. One ``save()`` per router call.

    **Never save before loading.** The first cut of this class flushed the fresh
    empty document in ``__init__`` and only then called ``load_existing()`` —
    which read back the file it had just truncated, so a resume silently
    DESTROYED the completed run instead of continuing it. That is the exact
    failure this lane exists to be resilient against, so the constructor now
    takes ``resume`` and does the read first, in one ordered step.
    """

    def __init__(self, conversation_id: str, resume: bool = True) -> None:
        self.doc: dict = {
            "lane": "adv_routing (retest lane 1 of 3)",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "conversation_id": conversation_id,
            "scratch_title": SCRATCH_TITLE,
            "synthetic_wi": None,
            "baseline": {
                "convos_before": None, "unlocks_before": None,
                "judgment_unlocks_before": None,
            },
            "contexts": {
                "cases": S_CASES, "heirs": S_HEIRS,
                "onecase": S_ONECASE, "empty": None, "ss_card": conversation_id,
            },
            "probes": [],
            "finished_at": None,
            "done": False,
        }
        if resume:
            self._load_existing()   # READ first…
        self.save()                 # …then, and only then, write.

    def _load_existing(self) -> None:
        """Resume: keep probes already on disk for the SAME conversation."""
        if not RESULTS_PATH.exists():
            return
        try:
            old = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return
        if old.get("conversation_id") != self.doc["conversation_id"]:
            return
        self.doc["probes"] = old.get("probes") or []
        self.doc["started_at"] = old.get("started_at") or self.doc["started_at"]
        self.doc["synthetic_wi"] = old.get("synthetic_wi")
        self.doc["baseline"] = old.get("baseline") or self.doc["baseline"]

    def has(self, pid: str, rep: int) -> bool:
        return any(p["id"] == pid and p["rep"] == rep and p.get("got") != "ERROR"
                   for p in self.doc["probes"])

    def add(self, row: dict) -> None:
        self.doc["probes"].append(row)
        self.save()

    def set(self, key: str, value) -> None:
        self.doc[key] = value
        self.save()

    def save(self) -> None:
        RESULTS_PATH.write_text(
            json.dumps(self.doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )


__all__ = [
    "USER_ID", "SCRATCH_TITLE", "RESULTS_PATH", "S_CASES", "S_HEIRS",
    "S_ONECASE", "CTRL02_CASE_ID", "Results", "service_client",
    "ensure_scratch_conversation", "hr", "short",
]
