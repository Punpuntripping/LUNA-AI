"""Idempotent seed of the 38 sector rows into ``public.topics``.

`library_sectors.md` §6 (Phase 0). The taxonomy tables landed empty in migration
096; this fills ``topics`` from the ONE slug map, ``shared/library/sectors.py``.

Upserts on the ``topics_slug_key`` unique index, so re-runs are stable and a
renamed Arabic label is corrected in place. ``parent_id`` stays NULL — the
taxonomy is flat for now (the column exists for a later grouping pass, §10).

⚠ ``topic_map`` IS LEFT EMPTY ON PURPOSE (D15 / trap T1). Materialising the 30k+
join rows looks tidy and is a trap: ``regulations_v2`` is a VIEW over the
pipeline-owned ``regulation_v2`` schema, so a re-ingest would silently
desynchronise the join table with no error. Sector filtering goes through the
existing ``.contains()`` array filters, which are always current and GIN-indexed
on 3 of the 4 corpora. This script must never write to ``topic_map``.

Note that ``topics`` is NOT on the request path: the backend resolves slugs from
``shared/library/sectors.py`` in memory. These rows exist so the taxonomy has a
queryable home (and a place for ``description`` / ``parent_id`` later).

``--dry-run`` is the DEFAULT: it prints the plan and writes nothing. Pass
``--apply`` to persist.

Run from the repo root:
  python scripts/seed_topics.py            # dry-run
  python scripts/seed_topics.py --apply    # write

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY (via shared.config / shared.db.client).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the repo root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252, which can't encode Arabic — force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass

from shared.db.client import get_supabase_client
from shared.library.sectors import SECTOR_SLUGS


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed public.topics with the 38 sectors.")
    parser.add_argument(
        "--apply", action="store_true", help="actually write (default is a dry run)"
    )
    args = parser.parse_args()
    mode = "APPLY" if args.apply else "DRY-RUN"

    # Volume order from the map; `sort_order` is not a column, but inserting in
    # this order keeps an unordered `select *` readable during debugging.
    rows = [
        {"slug": slug, "name_ar": name, "parent_id": None}
        for name, slug in SECTOR_SLUGS.items()
    ]
    print(f"seed_topics — mode={mode}, {len(rows)} sectors")

    client = get_supabase_client()

    existing_res = client.table("topics").select("slug, name_ar").execute()
    existing = {r["slug"]: r["name_ar"] for r in (existing_res.data or [])}

    new = [r for r in rows if r["slug"] not in existing]
    renamed = [
        r for r in rows if r["slug"] in existing and existing[r["slug"]] != r["name_ar"]
    ]
    unchanged = len(rows) - len(new) - len(renamed)
    orphans = [slug for slug in existing if slug not in SECTOR_SLUGS]

    print(f"  already correct : {unchanged}")
    print(f"  new             : {len(new)}")
    for r in new[:5]:
        print(f"      + {r['slug']:<34} {r['name_ar']}")
    if len(new) > 5:
        print(f"      … and {len(new) - 5} more")
    print(f"  label corrected : {len(renamed)}")
    for r in renamed:
        print(f"      ~ {r['slug']:<34} {existing[r['slug']]} → {r['name_ar']}")

    if orphans:
        # Not deleted automatically: a slug that left the map may still be linked
        # from the wild, and dropping the row is a decision with a 301 attached.
        print(f"  ⚠ orphan rows   : {len(orphans)} — in topics but NOT in the slug map:")
        for slug in orphans:
            print(f"      ? {slug}")
        print("    (left alone — remove by hand once their URLs are redirected)")

    if not args.apply:
        print("\n(no rows written — re-run with --apply to persist)\n")
        return

    if new or renamed:
        client.table("topics").upsert(rows, on_conflict="slug").execute()
        print(f"\n  ✓ upserted {len(rows)} rows on topics_slug_key")
    else:
        print("\n  ✓ nothing to do — already in sync")

    total = client.table("topics").select("slug", count="exact").execute()
    print(f"  topics now holds {total.count} rows")
    print("  topic_map left empty on purpose (D15 / T1)\n")


if __name__ == "__main__":
    main()
