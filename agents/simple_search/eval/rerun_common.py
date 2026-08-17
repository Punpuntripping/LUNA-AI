"""Shared bootstrap for the RE-RUN lane (2026-08-16, after the §13e fix round).

Everything this lane writes lands in the single ``[RERUN-RESOLUTION]``
conversation, which ``rerun_teardown.py`` hard-deletes. The account's real
conversations are never touched.

Logfire is dark (401), so every assertion in this lane reads a returned object
or a Supabase row — never a trace.
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

USER_ID = "c5f4cff0-0517-43f0-af59-a9905deab22c"
SCRATCH_TITLE = "[RERUN-RESOLUTION]"


def service_client():
    """A FRESH sync service-role client (anon would hit RLS)."""
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
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)


__all__ = ["USER_ID", "SCRATCH_TITLE", "service_client",
           "ensure_scratch_conversation", "hr"]
