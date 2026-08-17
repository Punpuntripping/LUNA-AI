"""Shared bootstrap for the ADVERSARIAL FAMILY lane (retest lane 2 of 3).

Measures the SEARCHER / family-level surfaces of
``agents/simple_search/eval/fixtures_adversarial.py``:

* the §13g abort guard, **both directions** (over-fire and under-fire),
* Case-C matching (``casec-*``),
* the corpus-shape traps (``corpus-*``, ``bab-*`` family side).

**Process rule of this lane: FLUSH AFTER EVERY PROBE.** The previous batch died
on a session limit and only the already-written JSON survived, so
:func:`flush` is called at the end of every single probe — never batched.

Everything this lane writes lands in the one ``[ADV-FAMILY]`` scratch
conversation, whose id is recorded in the results file *before any probe runs*.
The account's 136 real conversations are read-only here.

Logfire is dark (401): every assertion reads a returned object or a Supabase
row, never a trace.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

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

USER_ID = "c5f4cff0-0517-43f0-af59-a9905deab22c"
SCRATCH_TITLE = "[ADV-FAMILY]"
RESULTS = _ROOT / "agents" / "simple_search" / "eval" / "adv_family_results.json"


def service_client():
    """A FRESH sync service-role client (§9 trap 11: anon silently hits RLS)."""
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


# --------------------------------------------------------------------------- #
# The results ledger — one file, rewritten after EVERY probe.
# --------------------------------------------------------------------------- #

def load() -> dict[str, Any]:
    if RESULTS.exists():
        try:
            return json.loads(RESULTS.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"lane": "adv_family", "scratch_conversation_id": None, "probes": {},
            "ledger": {}, "notes": []}


def flush(doc: dict[str, Any]) -> None:
    RESULTS.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def record(probe_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Write ONE probe's result and flush immediately. Returns the whole doc."""
    doc = load()
    doc.setdefault("probes", {})[probe_id] = payload
    flush(doc)
    print(f"    [flushed → {probe_id}: {payload.get('verdict', '?')}]")
    return doc


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


def ledger(sb, content_type: str | None = None) -> list[dict]:
    """This user's live ``library_unlocks`` rows — the money ledger."""
    q = (sb.table("library_unlocks")
         .select("unlock_id, content_type, content_id, surface, cost, unlocked_at")
         .eq("user_id", USER_ID))
    if content_type:
        q = q.eq("content_type", content_type)
    return list((q.execute()).data or [])


def hr(title: str) -> None:
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)


def short(text: str, n: int = 200) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


__all__ = ["USER_ID", "SCRATCH_TITLE", "RESULTS", "service_client", "load",
           "flush", "record", "ensure_scratch_conversation", "ledger", "hr",
           "short"]
