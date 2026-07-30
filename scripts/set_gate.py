"""Operator toggle for the SEO public library gate (migration 095 sidecar).

Flips a single item's gate by upserting one row of ``public.seo_item_meta``
(the SIDECAR — SEO state lives here, NEVER on the corpus tables/views, which are
pipeline-owned ``regulation_v2`` views that get re-ingested). ``resolve_gate()``
in ``library_service`` reads this row, so the flip is authoritative the instant
it lands; passing ``--path`` also pokes the frontend's on-demand ISR revalidate
route so the cached public page refreshes in seconds instead of at the next
24h/1h ISR window.

The sidecar upsert is a MERGE keyed on ``(content_type, content_id)`` — only the
columns you pass are touched; ``slug`` and any untouched ``seo_tier`` survive.

Run from the repo root.

Usage:
  # gate a long-tail regulation (fall back to the section default is 'clear')
  python scripts/set_gate.py --type regulation --id res_1234 --gate gated \
      --path /regulations/nizam-al-amal

  # open a curated top regulation AND mark it open-tier (so its مواد read free)
  python scripts/set_gate.py --type regulation --id res_0007 --gate open --tier open \
      --path /regulations/nizam-al-amal

  # remove a per-item override (revert to seo_tier / section default)
  python scripts/set_gate.py --type article --id res_0007#m80 --gate clear

Env (service-role Supabase key + revalidate wiring):
  SUPABASE_URL / SUPABASE_SERVICE_KEY  — via shared.config / shared.db.client.
  PUBLIC_WEB_URL                       — frontend origin for the revalidate POST.
  REVALIDATE_SECRET                    — shared secret for /api/revalidate;
                                         missing → revalidation is SKIPPED (warned),
                                         the DB flip still happens.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the repo root importable when run directly (`python scripts/set_gate.py`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252, which can't encode the Arabic content or
# the em dashes in the summary lines — force UTF-8 so prints never crash.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001 — older streams / redirected output
    pass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001 — dotenv is optional; env may already be set
    pass

import httpx

from shared.config import get_settings
from shared.db.client import get_supabase_client

# Mirrors the seo_item_meta CHECK constraint (migration 095).
CONTENT_TYPES = ("regulation", "article", "judgment", "circular", "service", "form")

# Columns projected when we print the before/after row state.
_ROW_COLS = "content_type, content_id, slug, seo_tier, gate_override, updated_at"


def _fetch_row(client, content_type: str, content_id: str) -> dict | None:
    """Return the current seo_item_meta row, or None if it does not exist yet."""
    res = (
        client.table("seo_item_meta")
        .select(_ROW_COLS)
        .eq("content_type", content_type)
        .eq("content_id", content_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def _print_row(label: str, row: dict | None) -> None:
    if row is None:
        print(f"  {label}: (no row)")
        return
    print(
        f"  {label}: gate_override={row.get('gate_override')!r} "
        f"seo_tier={row.get('seo_tier')!r} slug={row.get('slug')!r} "
        f"updated_at={row.get('updated_at')!r}"
    )


def _revalidate(path: str, settings) -> None:
    """POST to the frontend's on-demand ISR revalidate route (best-effort).

    Contract (a parallel agent builds the route to THIS exact shape):
      POST {PUBLIC_WEB_URL}/api/revalidate
        header  x-revalidate-secret: {REVALIDATE_SECRET}
        json    {"path": "<path>"}

    Missing REVALIDATE_SECRET → warn and skip (never fail the DB flip over a
    cache poke). Network / non-2xx errors are warned, not raised.
    """
    secret = os.environ.get("REVALIDATE_SECRET")
    if not secret:
        print(
            "\nWARNING: REVALIDATE_SECRET not set — skipping ISR revalidation. "
            "The DB gate flip is already live; the cached page will refresh at the "
            "next ISR window. Set REVALIDATE_SECRET to revalidate instantly."
        )
        return

    url = f"{settings.PUBLIC_WEB_URL}/api/revalidate"
    try:
        resp = httpx.post(
            url,
            headers={"x-revalidate-secret": secret},
            json={"path": path},
            timeout=15.0,
        )
    except Exception as e:  # noqa: BLE001
        print(f"\nWARNING: revalidate POST to {url} failed: {e} (DB flip is still live)")
        return

    if 200 <= resp.status_code < 300:
        print(f"\nRevalidated {path} via {url} (HTTP {resp.status_code}).")
    else:
        body = (resp.text or "")[:300]
        print(
            f"\nWARNING: revalidate POST to {url} returned HTTP {resp.status_code}: "
            f"{body} (DB flip is still live)"
        )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Toggle one SEO library item's gate via the seo_item_meta sidecar."
    )
    ap.add_argument(
        "--type",
        required=True,
        choices=CONTENT_TYPES,
        help="content_type of the item (one of: %(choices)s)",
    )
    ap.add_argument("--id", required=True, help="content_id (TEXT key in the sidecar)")
    ap.add_argument(
        "--gate",
        required=True,
        choices=("open", "gated", "clear"),
        help="gate_override to set; 'clear' sets it to NULL (revert to tier/default)",
    )
    ap.add_argument(
        "--path",
        default=None,
        help="public route (e.g. /regulations/<slug>) to on-demand ISR-revalidate",
    )
    ap.add_argument(
        "--tier",
        default=None,
        choices=("open", "gated"),
        help="set seo_tier (REGULATIONS ONLY): 'open' = curated top reg, مواد read free",
    )
    args = ap.parse_args()

    content_id = str(args.id).strip()
    if not content_id:
        ap.error("--id must be a non-empty content_id")

    # seo_tier is only meaningful for regulations (article gating inherits it from
    # the parent regulation; other types ignore it). Reject a misplaced --tier
    # loudly rather than writing a dead column.
    if args.tier is not None and args.type != "regulation":
        ap.error(
            f"--tier is only valid for --type regulation (got --type {args.type}); "
            "seo_tier has no effect on other content types"
        )

    settings = get_settings()
    client = get_supabase_client()

    before = _fetch_row(client, args.type, content_id)

    payload: dict = {
        "content_type": args.type,
        "content_id": content_id,
        # 'clear' → NULL (revert to seo_tier / section default).
        "gate_override": None if args.gate == "clear" else args.gate,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if args.tier is not None:
        payload["seo_tier"] = args.tier

    # Merge-upsert on the composite PK: untouched columns (slug, and seo_tier when
    # --tier is omitted) are preserved.
    client.table("seo_item_meta").upsert(
        payload, on_conflict="content_type,content_id"
    ).execute()

    after = _fetch_row(client, args.type, content_id)

    tier_txt = f", seo_tier -> {args.tier!r}" if args.tier is not None else ""
    gate_txt = "NULL (cleared)" if args.gate == "clear" else f"{args.gate!r}"
    print(
        f"\nseo_item_meta ({args.type}/{content_id}): "
        f"gate_override -> {gate_txt}{tier_txt}\n"
    )
    _print_row("before", before)
    _print_row("after ", after)

    if args.path:
        _revalidate(args.path, settings)

    print()


if __name__ == "__main__":
    main()
