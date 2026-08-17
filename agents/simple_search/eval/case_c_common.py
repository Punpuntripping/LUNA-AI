"""Shared bootstrap for the Case-C evaluation scripts (Eval Agent 3).

Read-only against the live project ``dwgghvxogtwyaxmbgjod``. Nothing here
writes: every script that imports this module either reads Supabase rows or
calls an agent with the service-role client and inspects the returned object.

Run from repo root::

    .venv/Scripts/python.exe agents/simple_search/eval/case_c_parity.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:  # Windows console — the whole corpus is Arabic
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / ".env")

# The eval account. READ-ONLY corpus: 136 conversations, 135 agent_search WIs,
# 1,522 references.
USER_ID = "c5f4cff0-0517-43f0-af59-a9905deab22c"


def service_client():
    """The sync service-role supabase client (§9 trap 11: anon hits RLS)."""
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def short(text: str, n: int = 160) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


__all__ = ["USER_ID", "service_client", "hr", "short"]
