"""Case-B eval **RE-RUN** (2026-08-16, lane 2 of 3) — shared bootstrap.

Re-measures every leg the original `agents_reports/simple_search_eval_case_b.md`
recorded, plus the router A/B from `simple_search_router_ab_rerun.md`, now that
the carrier fixes and the SECOND router patch have landed.

**Reuse, not rewrite.** Every fixture, cell list and assertion body comes from
the existing `case_b_*` / `ab_*` modules; this lane only re-points them at a new
scratch conversation and adds the scoring the brief asks for (the family split,
the tie-break watch). Three lanes are running in parallel, so nothing here
EDITS an existing eval file — the title is swapped by patching the module
attribute at runtime.

Nothing here touches a production module.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_HERE = Path(__file__).resolve().parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:  # Windows console — the corpus is Arabic
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / ".env")

# The eval account (task brief). 136 REAL conversations — never touched.
USER_ID = "c5f4cff0-0517-43f0-af59-a9905deab22c"
SCRATCH_TITLE = "[RERUN-CASE-B]"


def service_client():
    """A FRESH sync service-role client (§9 trap 11: anon hits RLS).

    Deliberately not a singleton — one client per concurrency slot so
    overlapping router runs never share an ``httpx.Client``.
    """
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def use_rerun_scratch() -> None:
    """Point the reused harness modules at THIS lane's scratch conversation.

    ``case_b_carry`` / ``case_b_edge`` / ``case_b_regression`` all locate their
    conversation through ``case_b_fixtures.SCRATCH_CONVO_TITLE``, and
    ``ab_setup``/``ab_teardown`` through ``ab_common.SCRATCH_TITLE``. Patching
    the attribute (rather than editing the file) keeps the other two lanes'
    copies of those modules untouched.
    """
    from agents.simple_search.eval import case_b_fixtures as FX

    FX.SCRATCH_CONVO_TITLE = SCRATCH_TITLE
    import ab_common  # noqa: E402  (eval dir is on sys.path)

    ab_common.SCRATCH_TITLE = SCRATCH_TITLE


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
    "USER_ID", "SCRATCH_TITLE", "service_client", "use_rerun_scratch",
    "ensure_scratch_conversation", "hr", "short",
]
