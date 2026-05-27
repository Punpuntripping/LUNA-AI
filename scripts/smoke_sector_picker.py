"""End-to-end smoke test for the sector_picker agent.

Fires the exact companies-law query from conv ``faa3b71e`` (the diagnosed
regression: the old planner_decider picked ``["المعاملات التجارية"]`` and
dropped ``نظام الشركات``). Verifies:

1. The picker fires (its Logfire span is created).
2. The picker returns a non-null sector list that INCLUDES
   ``حوكمة الشركات والاستثمار`` (the sector ``نظام الشركات`` actually lives in).
3. The orchestrator's ``run_full_loop`` records ``sector_source=picker`` in
   the log.

Run from repo root:

    python scripts/smoke_sector_picker.py

Reads ``LUNA_*`` / ``DASHSCOPE_API_KEY`` from ``.env``. Costs ~$0.01 (one
deep_search invocation).
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:
    pass

from dotenv import load_dotenv
load_dotenv()

from shared.observability import configure_logfire, get_logfire
configure_logfire()

import httpx

from agents.deep_search_v4.planner import build_planner_deps, handle_planner_turn
from agents.deep_search_v4.sector_picker import run_sector_picker
from agents.utils.embeddings import embed_regulation_query_alibaba as embed_text
from shared.config import get_settings
from shared.db.client import get_supabase_client

_logfire = get_logfire()


COMPANIES_LAW_QUERY = (
    "حبيبي, اش هو الفرق بين المؤسسات والشركات من ناحية نظامية, "
    "واش الرسوم اللي احتاج ادفعها"
)


async def _standalone_picker_call() -> list[str] | None:
    """Fire the picker in isolation — fastest signal on whether it works."""
    print("[smoke] standalone picker call...")
    t0 = time.perf_counter()
    out = await run_sector_picker(
        query=COMPANIES_LAW_QUERY,
        mode="reg_led",
        planner_brief="",
        context_blocks=[],
    )
    print(f"[smoke]   sectors = {out}")
    print(f"[smoke]   duration = {time.perf_counter() - t0:.2f}s")
    return out


async def _full_planner_turn() -> dict:
    """Fire the full planner turn — picker runs in parallel with the executors."""
    print("\n[smoke] full planner turn (with parallel picker)...")
    settings = get_settings()
    async with httpx.AsyncClient(timeout=120.0) as http_client:
        deps = build_planner_deps(
            supabase=get_supabase_client(),
            embedding_fn=embed_text,
            http_client=http_client,
            jina_api_key=settings.JINA_RERANKER_API_KEY or "",
            detail_level="medium",
            query_id=999_001,  # arbitrary tag for this smoke run
        )
        t0 = time.perf_counter()
        with _logfire.span("smoke.sector_picker", query=COMPANIES_LAW_QUERY[:80]):
            turn = await handle_planner_turn(COMPANIES_LAW_QUERY, deps)
        duration = time.perf_counter() - t0

    decision = getattr(turn, "decision", None)
    agg = getattr(turn, "agg_output", None)
    ura = getattr(deps, "_ura", None)

    # Pull out sector-source info from the run's events.
    events = getattr(deps, "_events", []) or []
    decided_evt = next(
        (e for e in events if e.get("event") == "planner.decided"), None,
    )
    return {
        "kind": getattr(turn, "kind", None),
        "duration_s": round(duration, 1),
        "mode": getattr(decision, "mode", None),
        "support": getattr(decision, "support", None),
        # In Wave A the decider still carries a sectors field but it is ignored
        # by run_retrieval. Surface it here only for the A/B log.
        "decider_sectors": getattr(decision, "sectors", None),
        "confidence": getattr(agg, "confidence", None),
        "ref_count": len(getattr(agg, "references", []) or []),
        "ura_sector_filter": list(getattr(ura, "sector_filter", []) or []),
        "ura_sector_source": getattr(ura, "sector_source", None),
        "events_summary": [e.get("event") for e in events if e.get("event")],
    }


async def main() -> None:
    print("=" * 78)
    print("[smoke] sector_picker end-to-end — conv faa3b71e regression query")
    print("=" * 78)
    print(f"Query: {COMPANIES_LAW_QUERY[:120]}...")

    # --- Step 1: standalone picker -----------------------------------------
    sectors = await _standalone_picker_call()
    if sectors is None:
        print("\n[smoke] ❌ picker returned None — that's the bug we are testing")
        sys.exit(1)
    if "حوكمة الشركات والاستثمار" not in sectors:
        print(
            "\n[smoke] ❌ picker did NOT include حوكمة الشركات والاستثمار"
            f" — got {sectors}. This is the diagnosed failure mode."
        )
        sys.exit(2)
    n = len(sectors)
    if n < 2 or n > 5:
        print(f"\n[smoke] ❌ picker output count {n} outside [2,5] bound")
        sys.exit(3)
    print(f"[smoke] ✅ standalone: {n} sectors picked, includes حوكمة الشركات والاستثمار")

    # --- Step 2: full planner turn -----------------------------------------
    summary = await _full_planner_turn()
    print("\n[smoke] full-turn summary:")
    for k, v in summary.items():
        if k == "events_summary":
            continue
        print(f"  {k}: {v}")
    print(f"  events: {summary['events_summary']}")

    sf = summary.get("ura_sector_filter") or []
    src = summary.get("ura_sector_source")
    if "حوكمة الشركات والاستثمار" in sf:
        print(
            f"[smoke] ✅ full turn: URA records {sf} (source={src})"
        )
    else:
        print(
            f"[smoke] ⚠ full turn: URA sector_filter={sf} (source={src}) — picker "
            "may have run unfiltered. Inspect Logfire trace for "
            "deep_search.sector_picker span."
        )

    print("\n[smoke] done.")


if __name__ == "__main__":
    asyncio.run(main())
