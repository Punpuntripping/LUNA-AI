"""Generate plan activation codes (migrations 069 + 081).

Produces N unguessable 5-character codes for a given plan, inserts them into
public.plan_codes, and prints them for distribution. A user redeems a code in-app
(Settings → تفعيل برمز) to get the plan (migration 068 handles the duration +
limits from there).

Two shapes:
  - SINGLE-USE (default, --max-uses 1): each code activates exactly one user. Mint
    a batch (--count N) and hand one to each person.
  - MULTI-USE / GLOBAL (--max-uses N): ONE shared code that the first N distinct
    users to redeem it get activated (one redemption per user). Pair with --code
    to mint a memorable string instead of a random one.

Codes use a 30-symbol no-lookalike alphabet (Crockford base32 minus 0/O/1/I/L/U),
so they are safe to read aloud. Keyspace = 30^5 ≈ 24.3M — combined with the
backend's 5-fails/24h wall, effectively unguessable.

Run from the repo root.

Usage:
  # single-use batches
  python scripts/gen_plan_codes.py --plan marketing_lawyer --count 50 --valid-days 60 --batch-label lawyers_launch_jun26
  python scripts/gen_plan_codes.py --plan marketing_lawyer --count 5 --valid-days 0   # never-expiring

  # one global code the first 100 redeemers share
  python scripts/gen_plan_codes.py --plan marketing_lawyer --max-uses 100 --batch-label launch
  python scripts/gen_plan_codes.py --plan marketing_lawyer --max-uses 100 --code LAWYERS100
"""
from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make the repo root importable when run directly (`python scripts/gen_plan_codes.py`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252, which can't encode the Arabic plan names
# or the em dash in the summary line — force UTF-8 so the final print() (after
# the DB insert) never crashes and swallows the codes.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001 — older streams / redirected output
    pass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001 — dotenv is optional; env may already be set
    pass

from shared.db.client import get_supabase_client

# No-lookalike alphabet: Crockford base32 minus 0 O 1 I L U.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LEN = 5

# The frontend redeem input normalizes to [A-Z0-9] and caps at 12 chars, so a
# custom --code must fit that to be typeable in-app.
CUSTOM_CODE_MAXLEN = 12


def normalize_custom_code(raw: str) -> str:
    """Match the server/frontend normalization: uppercase, strip non-alphanumeric."""
    import re

    return re.sub(r"[^A-Z0-9]", "", raw.upper())


def gen_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def gen_unique_batch(client, count: int) -> list[str]:
    """Generate ``count`` codes unique within the batch AND not already in the
    plan_codes table. Collisions are astronomically rare at 24M keyspace, but
    handled so a rerun never silently produces a duplicate PK insert error."""
    codes: set[str] = set()
    while len(codes) < count:
        candidates = {gen_code() for _ in range(count - len(codes))} - codes
        if not candidates:
            continue
        existing = (
            client.table("plan_codes")
            .select("code")
            .in_("code", list(candidates))
            .execute()
        )
        taken = {r["code"] for r in (existing.data or [])}
        codes |= candidates - taken
    return sorted(codes)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate plan activation codes (single-use or multi-use/global)."
    )
    ap.add_argument("--plan", required=True, help="plan_id to grant (e.g. marketing_lawyer)")
    ap.add_argument("--count", type=int, default=10, help="number of codes to mint (default 10)")
    ap.add_argument(
        "--max-uses",
        type=int,
        default=1,
        help="distinct redeemers PER code; 1 = single-use (default), >1 = global code",
    )
    ap.add_argument(
        "--code",
        default=None,
        help="mint this exact code instead of a random one (implies --count 1; "
        "normalized to [A-Z0-9], max 12 chars). Handy for a memorable global code.",
    )
    ap.add_argument(
        "--valid-days",
        type=int,
        default=60,
        help="days until a code expires; 0 = never (default 60)",
    )
    ap.add_argument("--batch-label", default=None, help="optional label to group this batch")
    args = ap.parse_args()

    if args.count < 1:
        ap.error("--count must be >= 1")
    if args.max_uses < 1:
        ap.error("--max-uses must be >= 1")

    custom_code = None
    if args.code is not None:
        custom_code = normalize_custom_code(args.code)
        if not custom_code:
            ap.error("--code has no usable [A-Z0-9] characters after normalization")
        if len(custom_code) > CUSTOM_CODE_MAXLEN:
            ap.error(
                f"--code '{custom_code}' is {len(custom_code)} chars; the in-app input "
                f"caps at {CUSTOM_CODE_MAXLEN}"
            )
        if args.count != 1:
            ap.error("--code mints a single specific code; omit --count (or set it to 1)")

    client = get_supabase_client()

    # Validate the plan exists so a typo doesn't mint codes for a phantom plan.
    plan = (
        client.table("plans")
        .select("plan_id,name_ar")
        .eq("plan_id", args.plan)
        .limit(1)
        .execute()
    )
    plan_rows = plan.data or []
    if not plan_rows:
        ap.error(f"plan_id '{args.plan}' not found in plans table")
    plan_name = plan_rows[0].get("name_ar") or args.plan

    expires_at = None
    if args.valid_days and args.valid_days > 0:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=args.valid_days)
        ).isoformat()

    if custom_code is not None:
        taken = (
            client.table("plan_codes")
            .select("code")
            .eq("code", custom_code)
            .limit(1)
            .execute()
        )
        if taken.data:
            ap.error(f"code '{custom_code}' already exists in plan_codes")
        codes = [custom_code]
    else:
        codes = gen_unique_batch(client, args.count)

    client.table("plan_codes").insert(
        [
            {
                "code": c,
                "plan_id": args.plan,
                "max_uses": args.max_uses,
                "expires_at": expires_at,
                "batch_label": args.batch_label,
            }
            for c in codes
        ]
    ).execute()

    exp_txt = expires_at.split("T")[0] if expires_at else "never"
    batch_txt = f", batch '{args.batch_label}'" if args.batch_label else ""
    uses_txt = (
        "single-use" if args.max_uses == 1 else f"global, up to {args.max_uses} redeemers each"
    )
    print(
        f"\nGenerated {len(codes)} code(s) for plan '{args.plan}' ({plan_name}) — "
        f"{uses_txt}, expire {exp_txt}{batch_txt}:\n"
    )
    for c in codes:
        print(f"  {c}")
    print()


if __name__ == "__main__":
    main()
