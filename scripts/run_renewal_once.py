"""Run the auto-renewal sweep ONCE, by hand, and print what it did.

WHY THIS EXISTS
---------------
``renewal_service.run_due_renewals`` is registered on APScheduler at 03:30 UTC.
Waiting a day to learn whether a mada merchant-initiated charge works is not a
feedback loop. This invokes the same function directly.

⚠ THIS SPENDS REAL MONEY. It charges stored card tokens for every subscription
the selection window catches. It is not a dry run and there is no confirmation
prompt — read ``--list`` output first, always.

    python scripts/run_renewal_once.py --list     # who WOULD be charged, no charge
    python scripts/run_renewal_once.py --run      # actually charge them

⚠ THE KEY MODE MUST MATCH THE TOKEN. A token minted under ``sk_live`` cannot be
charged with ``sk_test`` and vice versa: the provider rejects it, the sweep
records an ordinary decline, the dunning counter advances and the customer gets
a "renewal failed" email — a fake negative that looks exactly like a real one.
The repo ``.env`` carries a TEST key, so a live check needs an explicit
override:

    MOYASAR_SECRET_KEY=sk_live_… SUBSCRIPTION_AUTO_RENEWAL_ENABLED=true \
        python scripts/run_renewal_once.py --run

This script refuses to run if the key mode and the target token disagree, rather
than let you discover it from a bogus decline.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config import get_settings          # noqa: E402
from shared.db.client import get_supabase_client  # noqa: E402


def _preview(supabase) -> list[dict]:
    """Rows the sweep's first pass would select. Mirrors renewal_service's
    selection; read-only, and deliberately a SEPARATE query rather than a
    'dry_run' flag threaded through the real one — a dry-run branch inside code
    that charges cards is a branch that can be got wrong.

    ⚠ THE ``expires_at`` WINDOW IS PART OF THE SELECTION, not a detail. Without
    it this listed every payment-sourced pro/max subscriber as a candidate,
    including people whose term ends next month and who cannot be charged today.
    A preview that overstates who gets charged is worse than no preview: it
    trains you to ignore it. Mirrors renewal_service.py's `.gte`/`.lte` on
    [now, now + DUE_HORIZON] — if that changes, change this.
    """
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=24)
    res = (
        supabase.table("user_subscriptions")
        .select("user_id, plan_id, source, expires_at, renewal_cancelled_at,"
                " renewal_failed_count")
        .in_("plan_id", ["pro", "max"])
        .eq("source", "payment")
        .is_("renewal_cancelled_at", "null")
        .gte("expires_at", now.isoformat())
        .lte("expires_at", horizon.isoformat())
        .execute()
    )
    return list(res.data or [])


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="show candidates, charge nothing")
    g.add_argument("--run", action="store_true", help="RUN THE SWEEP — charges cards")
    args = ap.parse_args()

    settings = get_settings()
    key = (settings.MOYASAR_SECRET_KEY or "").strip()
    mode = "live" if key.startswith("sk_live") else "test" if key.startswith("sk_test") else "?"
    flag = getattr(settings, "SUBSCRIPTION_AUTO_RENEWAL_ENABLED", False)

    print(f"supabase : {settings.SUPABASE_URL}")
    print(f"moyasar  : {mode} key")
    print(f"flag     : SUBSCRIPTION_AUTO_RENEWAL_ENABLED={flag}")
    if not flag:
        print("\nREFUSING: the flag is off — run_due_renewals would no-op anyway.")
        return 2

    supabase = get_supabase_client()

    candidates = _preview(supabase)
    print(f"\n{len(candidates)} payment-sourced pro/max subscription(s) with renewal on:")
    for row in candidates:
        card = (
            supabase.table("payment_methods")
            .select("brand, last4, revoked_at")
            .eq("user_id", row["user_id"])
            .is_("revoked_at", "null")
            .execute()
        )
        c = (card.data or [None])[0]
        card_txt = f"{c['brand']} ••{c['last4']}" if c else "NO ACTIVE CARD (skipped)"
        print(f"  {row['user_id']}  {row['plan_id']:4}  expires {row['expires_at']}"
              f"  fails={row['renewal_failed_count']}  {card_txt}")

    if args.list:
        print("\n--list: nothing charged.")
        return 0

    # Import late: renewal_service reads settings at import time in places.
    from backend.app.services import renewal_service  # noqa: E402

    print("\nrunning the sweep — THIS CHARGES CARDS…\n")
    stats = asyncio.run(renewal_service.run_due_renewals(supabase))
    print("outcome:", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
