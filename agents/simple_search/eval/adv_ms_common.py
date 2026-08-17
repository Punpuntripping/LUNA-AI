"""Shared bootstrap for the adversarial MONEY + STATE lane (§13h `unlock-*` / `state-*`).

Retest lane 3 of 3. These are the fixtures with real consequences: `unlock-*`
writes to the live `library_unlocks` ledger and `state-*` writes real
`paused_runs` rows. Everything here exists to make those writes **accountable**.

**The flush discipline.** A previous batch died mid-run on a session limit and
only flushed data survived. So this module's contract is that nothing is held in
memory across a probe:

* :func:`flush_probe` appends one probe verdict and rewrites the file.
* :func:`record_ledger_row` records a `library_unlocks` row id **the moment it
  is observed**, before any assertion runs on it. An unrecorded unlock row from
  a dead agent is the mess this lane must not leave.
* :func:`record_pause_row` does the same for `paused_runs`.

Both recorders write through to disk immediately, so the file is a complete
cleanup worklist at every instant — even if the process is killed between two
statements.

**Protected rows.** The account carries one REAL pre-existing planner pause
(`6e1d3707…`, conversation `fcb965fb…`, 2026-06-04) and 53 real ledger rows.
:data:`PROTECTED_PAUSE_RUN_ID` is named here so no cleanup path can reach it,
and every delete in this lane is scoped by BOTH the row id and a value only
this lane could have produced.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:  # Windows console — the corpus is Arabic
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / ".env")

# The eval account (task brief). 136 REAL conversations — read-only.
USER_ID = "c5f4cff0-0517-43f0-af59-a9905deab22c"
SCRATCH_TITLE = "[ADV-MONEY-STATE]"

RESULTS_PATH = Path(__file__).with_name("adv_money_state_results.json")

# ── Protected live state — NEVER touched by this lane's cleanup ──────────────
#: A real deep_search planner pause on a real conversation, open since
#: 2026-06-04. state-01 reads its SHAPE but never its row.
PROTECTED_PAUSE_RUN_ID = "6e1d3707-cf9c-4f6b-8a2e-7635c9e41113"
PROTECTED_PAUSE_CONVO = "fcb965fb-78fa-408a-ab1c-0cb29007a89c"

# ── Targets, measured live 2026-08-16 (see the report's §Targets) ────────────
#: unlock-01 — two rulings NOT among the 17 already unlocked, from a WI that is
#: NOT unlock-02's, so the two fixtures' ledger deltas can never be confused.
UNLOCK01_X = ("9a390ee2-819b-42a5-8f9d-8ba0a3b78d11", "17642_fi_4471232662")
UNLOCK01_Y = ("1fe98851-1856-4b82-87b5-3b498d61dd8d", "17642_fi_4470045059")

#: unlock-02 / unlock-03 — a real WI citing exactly 3 rulings, none unlocked.
WI_THREE_RULINGS = "b592d479-ce19-4a36-895f-190c8827c188"
WI_THREE_TITLE = "السند النظامي لرفض نقل العامل لشركة أخرى"
WI_THREE_CASES = [
    ("ff8e7de6-9dcb-4eaf-a33f-35a5c3064a1d", "17642_ap_4630228183", "العمالية"),
    ("c77d4a35-c786-4c5e-b67a-1420f707359a", "17642_fi_4470749941", "التجارية"),
    ("a90da1ca-e535-4e78-8c9d-a5f9f6d42582", "17642_fi_5533", "التجارية"),
]

#: Every case id this lane is ever allowed to delete an unlock row for. The
#: cleanup intersects observed-new rows with this set: two other eval lanes are
#: live on this account today and a row of theirs must never be swept up here.
OWNED_CASE_IDS = {
    UNLOCK01_X[0], UNLOCK01_Y[0], *[c[0] for c in WI_THREE_CASES],
}


def service_client():
    """A FRESH sync service-role client (§9 trap 11: anon hits RLS)."""
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


# =========================================================================== #
# The flush machinery — the whole point of this module.
# =========================================================================== #


def _blank() -> dict:
    return {
        "lane": "adv_money_state",
        "fixtures": ["unlock-01", "unlock-02", "unlock-03", "state-01", "state-02"],
        "scratch_conversation_id": None,
        "baseline": {},
        "protected": {
            "pause_run_id": PROTECTED_PAUSE_RUN_ID,
            "pause_conversation_id": PROTECTED_PAUSE_CONVO,
            "note": "pre-existing REAL planner pause — never deleted by this lane",
        },
        "ledger_rows_created": [],
        "pause_rows_created": [],
        "workspace_items_created": [],
        "probes": [],
        "cleanup": {},
    }


def load() -> dict:
    if RESULTS_PATH.exists():
        try:
            return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a corrupt file must not lose the run
            pass
    return _blank()


def save(doc: dict) -> None:
    """Write the whole document. Atomic-ish: temp file then replace."""
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = RESULTS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    tmp.replace(RESULTS_PATH)


def set_top(**fields) -> dict:
    """Set top-level fields (scratch id, baseline) and flush immediately."""
    doc = load()
    doc.update(fields)
    save(doc)
    return doc


def record_ledger_row(unlock_id: str, *, content_id: str, fixture: str,
                      content_type: str = "judgment", **extra) -> None:
    """Record a ledger row THE MOMENT it is observed. Flushes before returning.

    Called before any assertion touches the row, so a crash between the insert
    and the check still leaves a complete cleanup worklist on disk.
    """
    doc = load()
    if not any(r["unlock_id"] == unlock_id for r in doc["ledger_rows_created"]):
        doc["ledger_rows_created"].append({
            "unlock_id": str(unlock_id), "content_id": str(content_id),
            "content_type": content_type, "fixture": fixture,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "deleted": False, **extra,
        })
        save(doc)
        print(f"    [FLUSHED ledger row] {unlock_id} · case {content_id} · {fixture}")


def record_pause_row(run_id: str, *, fixture: str, conversation_id: str,
                     agent_family: str = "", **extra) -> None:
    """Record a paused_runs row the moment it is created. Flushes immediately."""
    doc = load()
    if not any(r["run_id"] == run_id for r in doc["pause_rows_created"]):
        doc["pause_rows_created"].append({
            "run_id": str(run_id), "conversation_id": str(conversation_id),
            "agent_family": agent_family, "fixture": fixture,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "deleted": False, **extra,
        })
        save(doc)
        print(f"    [FLUSHED pause row] {run_id} · {agent_family} · {fixture}")


def record_workspace_items(item_ids: list[str], *, fixture: str) -> None:
    doc = load()
    known = {w["item_id"] for w in doc["workspace_items_created"]}
    added = False
    for iid in item_ids:
        if iid and iid not in known:
            doc["workspace_items_created"].append({
                "item_id": str(iid), "fixture": fixture,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            })
            added = True
    if added:
        save(doc)
        print(f"    [FLUSHED {len(item_ids)} workspace item(s)] {fixture}")


def flush_probe(fixture: str, verdict: str, **payload) -> None:
    """Append one probe verdict and rewrite the file. Call after EVERY probe."""
    doc = load()
    doc["probes"] = [p for p in doc["probes"] if p.get("fixture") != fixture]
    doc["probes"].append({
        "fixture": fixture, "verdict": verdict,
        "ran_at": datetime.now(timezone.utc).isoformat(), **payload,
    })
    save(doc)
    print(f"\n>>> FLUSHED probe {fixture}: {verdict} → {RESULTS_PATH.name}")


# =========================================================================== #
# Live-state helpers.
# =========================================================================== #


def ledger(sb, content_type: str | None = None) -> list[dict]:
    q = (sb.table("library_unlocks")
         .select("unlock_id, content_type, content_id, surface, cost, unlocked_at")
         .eq("user_id", USER_ID))
    if content_type:
        q = q.eq("content_type", content_type)
    return list((q.execute()).data or [])


def ledger_fingerprint(sb) -> dict:
    """Count + newest per content_type — the brief's restoration test."""
    rows = ledger(sb)
    by: dict[str, dict] = {}
    for r in rows:
        ct = r["content_type"]
        slot = by.setdefault(ct, {"count": 0, "newest": ""})
        slot["count"] += 1
        slot["newest"] = max(slot["newest"], str(r["unlocked_at"]))
    return {"total": len(rows), "by_content_type": by,
            "ids": sorted(str(r["unlock_id"]) for r in rows)}


def pause_rows(sb, conversation_id: str | None = None) -> list[dict]:
    q = (sb.table("paused_runs")
         .select("run_id, conversation_id, agent_family, pause_reason, "
                 "question_text, asked_at, expires_at")
         .eq("user_id", USER_ID))
    if conversation_id:
        q = q.eq("conversation_id", conversation_id)
    return list((q.execute()).data or [])


def ensure_scratch_conversation(sb) -> str:
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


def scratch_id() -> str:
    cid = load().get("scratch_conversation_id")
    if not cid:
        raise SystemExit("run adv_setup.py first — no scratch conversation recorded")
    return str(cid)


def hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def short(text: str, n: int = 200) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


__all__ = [
    "USER_ID", "SCRATCH_TITLE", "RESULTS_PATH", "PROTECTED_PAUSE_RUN_ID",
    "PROTECTED_PAUSE_CONVO", "UNLOCK01_X", "UNLOCK01_Y", "WI_THREE_RULINGS",
    "WI_THREE_TITLE", "WI_THREE_CASES", "OWNED_CASE_IDS",
    "service_client", "load", "save", "set_top", "record_ledger_row",
    "record_pause_row", "record_workspace_items", "flush_probe",
    "ledger", "ledger_fingerprint", "pause_rows", "ensure_scratch_conversation",
    "scratch_id", "hr", "short",
]
