"""Shared bootstrap for the router A/B **re-run** (verification lane, 2026-08-16).

Re-measures the exact A/B that `agents_reports/simple_search_eval_case_b.md` §4
and `simple_search_eval_case_c.md` §5 recorded, against the router prompt as
edited on 2026-08-16 (the "answer directly is scoped to what a prior report
CONCLUDED / a `references` page is a SOURCE" fix, plan §13b-eval).

Nothing in this module edits a production file. It calls
``backend.app.services.library_item_service.create_library_item`` and
``agents.router.router.run_router`` exactly as production does, and asserts on
the returned objects — Logfire is dark (401), so traces are not evidence here.

Everything written lands in the single ``[AB-RERUN]`` conversation, which
``ab_teardown.py`` hard-deletes.
"""
from __future__ import annotations

import os
import sys
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
SCRATCH_TITLE = "[AB-RERUN]"

# The carried library page under test — the same one the Case-B eval used.
REG_LABOR_SLUG = "نظام-العمل"
REG_LABOR_TITLE = "نظام العمل"

# Real, read-only source conversations whose router context Case C borrowed.
S_CASES = "2ff014cd-9197-4c15-8991-c1ec045a5902"   # WI-2: 18 rulings + 10 regs
S_HEIRS = "e70cecfa-56ec-4788-b820-69b1d0b0ad1b"   # WI-3: 8 rulings · WI-4: agent_writing


def service_client():
    """A FRESH sync service-role client (§9 trap 11: anon hits RLS).

    Deliberately not a singleton: the runner hands one client per concurrency
    slot so overlapping router runs never share an ``httpx.Client``.
    """
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


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


def hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def short(text: str, n: int = 200) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


__all__ = [
    "USER_ID", "SCRATCH_TITLE", "REG_LABOR_SLUG", "REG_LABOR_TITLE",
    "S_CASES", "S_HEIRS", "service_client", "ensure_scratch_conversation",
    "hr", "short",
]
