"""Idempotent Arabic-slug population for the SEO public library sidecar.

Reads the corpus (``regulations_v2`` → content_type ``regulation``; ``services``
→ content_type ``service``; ``circulars`` → content_type ``circular``) and writes
a permanent, URL-safe Arabic ``slug`` into the ``seo_item_meta`` SIDECAR
(migration 095) for every item that does not have one yet. Slugs are PERMANENT —
this script NEVER rewrites an existing slug (URLs must not change once
published); it only fills the blanks.

Like ``scripts/set_gate.py`` it MERGE-upserts on the composite PK
``(content_type, content_id)``: only ``slug`` (+ ``updated_at``) is written, so
any existing ``seo_tier`` / ``gate_override`` on the row survives untouched. The
corpus surfaces are pipeline-owned VIEWS/tables — this script only ever writes to
the sidecar, never to them.

Slug algorithm (per item title):
  NFC-normalize → strip tashkeel (U+064B–U+0652) + tatweel (U+0640) → lowercase →
  drop everything that is not an Arabic letter/digit or a Latin alphanumeric
  (punctuation, symbols, whitespace all become separators) → collapse runs to a
  single ``-`` → trim. Empty result (e.g. a title of only punctuation/digits that
  got stripped) falls back to ``reg-{reg_ref}`` / ``service-{service_ref}`` /
  ``circ-{circ_ref}`` (the ref itself run through the same normaliser, so
  underscores become hyphens).

Collision handling (within a content_type — the sidecar's partial unique index is
``(content_type, slug)``): items are processed in stable ``id`` order and a
duplicate base gets a deterministic ``-2``, ``-3``… suffix. Slugs already present
in the sidecar are treated as taken, so re-runs are stable.

``--dry-run`` is the DEFAULT: it prints sample slugs + counts and writes nothing.
Pass ``--apply`` to actually upsert (batched). Optional ``--type`` limits the run
to one content_type.

REVERSIBILITY — ``--unpublish --ids-file``
------------------------------------------
Publishing an item IS writing its slug, so UN-publishing it is writing that slug
back to ``NULL``: the anon endpoints, the hub listers and the sitemap all key off
a slugged sidecar row, and they stop seeing the item the instant the slug goes.

It is an UPDATE, never a DELETE. The sidecar row survives with its ``seo_tier``
and ``gate_override`` intact, so a later re-publish restores the item's GATING
rather than silently resetting it to the default tier. Deleting the row would
throw that away — which is the one thing this path exists to prevent.

The clear is scoped to BOTH ``--type`` and the given ids, so it can never reach
another wing, and it is ``--apply``-gated exactly like publishing: the default
dry-run prints what it *would* clear and writes nothing.

There is deliberately **no ``--unpublish-all``** here. ``build_judgment_slugs.py``
has one because that wing is a reversible sample; regulations / circulars /
services are a published surface with real inbound links, and retiring all of it
should take an explicit id list that somebody had to produce on purpose.

This exists because the one rollback this wing has ever done — 503 regulations
back to 166, 2026-08-06 — was performed with ad-hoc SQL that was never committed
(``.claude/plans/ranking_criteria.md:340,357-362``). Publishing in bulk without a
committed reverse is not a position worth being in.

Run from the repo root:
  python scripts/build_seo_slugs.py                 # dry-run, all types
  python scripts/build_seo_slugs.py --type regulation
  python scripts/build_seo_slugs.py --type circular
  python scripts/build_seo_slugs.py --apply         # write

  # publish a CHOSEN set (e.g. the file build_entity_quota_ids.py emits)
  python scripts/build_seo_slugs.py --type regulation --ids-file ids.txt --apply

  # reverse exactly that set
  python scripts/build_seo_slugs.py --type regulation --unpublish \
      --ids-file ids.txt            # dry-run: prints what it would clear
  python scripts/build_seo_slugs.py --type regulation --unpublish \
      --ids-file ids.txt --apply    # clears the slugs

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY (via shared.config / shared.db.client).
"""
from __future__ import annotations

import argparse
import sys
import unicodedata
import re
from datetime import datetime, timezone
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

# Read-page size (PostgREST caps a single response at 1000 rows by default).
_READ_PAGE = 1000
# Upsert batch size on --apply.
_WRITE_BATCH = 500
# Chunk size for `.in_("content_id", [...])` FILTERS — deliberately not
# _WRITE_BATCH. An upsert body carries its 500 rows in the POST body, but an
# `in.(...)` filter is a query STRING: 500 uuids is ~19 KB of URL, which proxies
# truncate or 414 long before PostgREST sees it. Same value and same reason as
# scripts/build_judgment_slugs.py:_ID_CHUNK.
_ID_CHUNK = 100

# Tashkeel (Arabic diacritics) U+064B..U+0652 + tatweel U+0640 — stripped so
# vowelled and un-vowelled spellings of the same title collapse to one slug.
_TASHKEEL = set(range(0x064B, 0x0653))
_TATWEEL = 0x0640

# Per content_type source config: (corpus table, title columns in priority
# order, ref column for the fallback slug, fallback prefix).
_SOURCES = {
    "regulation": {
        "table": "regulations_v2",
        "title_cols": ("clean_title", "title"),
        "ref_col": "reg_ref",
        "fallback_prefix": "reg",
    },
    "service": {
        "table": "services",
        "title_cols": ("service_name_ar",),
        "ref_col": "service_ref",
        "fallback_prefix": "service",
    },
    "circular": {
        "table": "circulars",
        "title_cols": ("title",),
        "ref_col": "circ_ref",
        "fallback_prefix": "circ",
    },
}


def _is_slug_char(ch: str) -> bool:
    """True for a Latin alphanumeric or an Arabic-block letter/digit."""
    if ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9"):
        return True
    o = ord(ch)
    # Arabic (U+0600–U+06FF) + Arabic Supplement (U+0750–U+077F): keep letters
    # (Lo/Lm) and digits (Nd); drop Arabic punctuation (؟،؛), symbols, marks.
    if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F:
        return unicodedata.category(ch)[0] in ("L", "N")
    return False


def slugify_ar(text: str) -> str:
    """Build a URL-safe Arabic slug from ``text`` (see module docstring)."""
    if not text:
        return ""
    s = unicodedata.normalize("NFC", str(text))
    # strip tashkeel + tatweel
    s = "".join(ch for ch in s if ord(ch) not in _TASHKEEL and ord(ch) != _TATWEEL)
    s = s.lower()
    # every non-slug char becomes a separator (space), then collapse to hyphens
    s = "".join(ch if _is_slug_char(ch) else " " for ch in s)
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _dedupe(base: str, taken: set[str]) -> str:
    """Return ``base`` or the first free ``base-{n}`` (n>=2). Adds nothing to
    ``taken`` — the caller records the winner."""
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _load_existing(client, content_type: str) -> tuple[dict[str, str | None], set[str]]:
    """Return ``(content_id -> slug, {taken slugs})`` for one content_type."""
    existing: dict[str, str | None] = {}
    taken: set[str] = set()
    offset = 0
    while True:
        res = (
            client.table("seo_item_meta")
            .select("content_id, slug")
            .eq("content_type", content_type)
            .order("content_id")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        for r in batch:
            cid = r.get("content_id")
            slug = r.get("slug")
            if cid is None:
                continue
            existing[str(cid)] = slug
            if slug:
                taken.add(slug)
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return existing, taken


def _load_corpus(client, table: str, cols: tuple[str, ...]) -> list[dict]:
    """Fetch every row of ``table`` (paged) in stable ``id`` order."""
    select = ", ".join(("id",) + cols)
    rows: list[dict] = []
    offset = 0
    while True:
        res = (
            client.table(table)
            .select(select)
            .order("id")
            .range(offset, offset + _READ_PAGE - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < _READ_PAGE:
            break
        offset += _READ_PAGE
    return rows


def process_type(
    client, content_type: str, apply: bool, only_ids: set[str] | None = None
) -> dict:
    """Compute (and, when ``apply``, write) slugs for one content_type.

    ``only_ids`` restricts the run to those corpus ids — publishing a CHOSEN set
    rather than the whole corpus. Slugging is what publishes an item, so without
    it the only options are "100 hand-picked rows" and "all 3,952", and the
    second one puts the corpus's untitled scans and standards dumps on public,
    indexable URLs. The selection itself is not this script's business: it reads
    a list of ids and slugs exactly those. `build_usage_rank.py --emit-used-ids`
    writes the list of regulations the pipeline has actually cited.

    Returns a stats dict and prints a per-type summary with sample slugs.
    """
    cfg = _SOURCES[content_type]
    prefix = cfg["fallback_prefix"]
    ref_col = cfg["ref_col"]
    title_cols = cfg["title_cols"]

    existing, taken = _load_existing(client, content_type)
    corpus = _load_corpus(client, cfg["table"], title_cols + (ref_col,))

    now_iso = datetime.now(timezone.utc).isoformat()
    payloads: list[dict] = []
    samples: list[tuple[str, str, str]] = []  # (content_id, source, slug)
    stats = {
        "total": 0,
        "already": 0,
        "new": 0,
        "collision": 0,
        "fallback": 0,
    }

    for row in corpus:
        cid = str(row.get("id"))
        if only_ids is not None and cid not in only_ids:
            continue
        stats["total"] += 1

        # Never rewrite an existing slug — URLs are permanent.
        if existing.get(cid):
            stats["already"] += 1
            continue

        raw_title = ""
        for col in title_cols:
            val = (row.get(col) or "").strip()
            if val:
                raw_title = val
                break

        base = slugify_ar(raw_title)
        source = raw_title
        if not base:
            ref = row.get(ref_col)
            base = slugify_ar(f"{prefix}-{ref}" if ref else f"{prefix}-{cid}")
            # Absolute last resort (should never happen): a non-empty constant.
            base = base or f"{prefix}-{stats['total']}"
            source = f"(fallback) {prefix}-{ref if ref else cid}"
            stats["fallback"] += 1

        final = _dedupe(base, taken)
        if final != base:
            stats["collision"] += 1
        taken.add(final)

        payloads.append(
            {
                "content_type": content_type,
                "content_id": cid,
                "slug": final,
                "updated_at": now_iso,
            }
        )
        stats["new"] += 1
        if len(samples) < 12:
            samples.append((cid, source, final))

    # Summary --------------------------------------------------------------
    print(f"\n=== {content_type} ({cfg['table']}) ===")
    print(
        f"  corpus rows        : {stats['total']}\n"
        f"  already slugged    : {stats['already']}\n"
        f"  new slugs to write : {stats['new']}\n"
        f"    - collisions     : {stats['collision']}\n"
        f"    - title fallbacks: {stats['fallback']}"
    )
    if samples:
        print("  sample new slugs:")
        for cid, source, slug in samples:
            src = (source or "")[:48]
            print(f"    {slug!r:<40}  <-  {src}")

    if apply and payloads:
        written = 0
        for i in range(0, len(payloads), _WRITE_BATCH):
            batch = payloads[i : i + _WRITE_BATCH]
            client.table("seo_item_meta").upsert(
                batch, on_conflict="content_type,content_id"
            ).execute()
            written += len(batch)
        print(f"  APPLIED: upserted {written} slug rows.")
    elif payloads:
        print(f"  DRY-RUN: would upsert {len(payloads)} slug rows (pass --apply to write).")
    else:
        print("  nothing to write (all items already have slugs).")

    return stats


def unpublish_type(client, content_type: str, ids: set[str], apply: bool) -> dict:
    """Clear ``slug`` on the given ids of ONE content_type — the reverse of a
    publish. See the module docstring for why this is an UPDATE and not a DELETE.

    The filter is pinned to ``content_type`` AND ``content_id in (...)``, so the
    blast radius is exactly the intersection of the ``--type`` and the id file —
    no other wing can be caught by it even if the file holds foreign ids.

    Ids are bucketed three ways so the dry-run is honest about what will actually
    happen: ids that carry a slug today (the ones that get cleared), ids whose
    sidecar row exists but is already unpublished, and ids with no sidecar row at
    all (a typo, or an id from the wrong corpus). Only the first bucket is
    written; the other two are reported and skipped, which makes a re-run of the
    same file a no-op rather than an error.
    """
    existing, _taken = _load_existing(client, content_type)

    # Sorted so the run order — and therefore the printed sample — is stable.
    to_clear = sorted(cid for cid in ids if existing.get(cid))
    already = sorted(cid for cid in ids if cid in existing and not existing.get(cid))
    unknown = sorted(cid for cid in ids if cid not in existing)

    print(f"\n=== {content_type} — UNPUBLISH ===")
    print(
        f"  ids requested        : {len(ids)}\n"
        f"  currently published  : {len(to_clear)}   <- slug would be cleared\n"
        f"  already unpublished  : {len(already)}   (sidecar row exists, slug NULL)\n"
        f"  no sidecar row       : {len(unknown)}   (never published / wrong corpus)"
    )
    if unknown:
        print("  sample unknown ids:")
        for cid in unknown[:5]:
            print(f"    {cid}")
    if to_clear:
        print("  sample slugs to clear:")
        for cid in to_clear[:12]:
            print(f"    {existing[cid]!r:<44}  <-  {cid}")

    cleared = 0
    if apply and to_clear:
        now_iso = datetime.now(timezone.utc).isoformat()
        for i in range(0, len(to_clear), _ID_CHUNK):
            chunk = to_clear[i : i + _ID_CHUNK]
            (
                client.table("seo_item_meta")
                .update({"slug": None, "updated_at": now_iso})
                .eq("content_type", content_type)
                .in_("content_id", chunk)
                .execute()
            )
            cleared += len(chunk)
        print(
            f"  APPLIED: cleared {cleared} {content_type} slug(s). "
            f"seo_tier / gate_override untouched."
        )
    elif to_clear:
        print(
            f"  DRY-RUN: would clear {len(to_clear)} slug(s) (pass --apply to write)."
        )
    else:
        print("  nothing to clear (none of these ids is published right now).")

    return {
        "requested": len(ids),
        "to_clear": len(to_clear),
        "already": len(already),
        "unknown": len(unknown),
        "cleared": cleared,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Publish (or un-publish) seo_item_meta slugs for the SEO "
        "public library."
    )
    ap.add_argument(
        "--type",
        choices=("regulation", "service", "circular", "all"),
        default="all",
        help="content_type to process (default: all)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually write (DEFAULT is a dry-run that writes nothing)",
    )
    ap.add_argument(
        "--ids-file",
        metavar="PATH",
        help="publish (or, with --unpublish, un-publish) ONLY these corpus ids "
        "(one per line; blank lines and '#' comments ignored). Requires an "
        "explicit --type.",
    )
    ap.add_argument(
        "--unpublish",
        action="store_true",
        help="REVERSE a publish: clear the slug on the --ids-file ids so their "
        "pages 404 again. Requires --ids-file and an explicit --type. Never a "
        "DELETE — seo_tier / gate_override survive, so a re-publish restores the "
        "item's gating. There is deliberately no --unpublish-all for these types.",
    )
    args = ap.parse_args()

    if args.unpublish and not args.ids_file:
        ap.error(
            "--unpublish requires --ids-file. There is deliberately no "
            "--unpublish-all for regulation/service/circular — retiring a "
            "published surface takes an explicit id list."
        )

    only_ids: set[str] | None = None
    if args.ids_file:
        if args.type == "all":
            ap.error("--ids-file needs an explicit --type (ids are per corpus)")
        raw = Path(args.ids_file).read_text(encoding="utf-8").splitlines()
        only_ids = {
            ln.strip() for ln in raw if ln.strip() and not ln.lstrip().startswith("#")
        }
        if not only_ids:
            ap.error(f"--ids-file {args.ids_file} contained no ids")

    # --- un-publish is its own terminal path (no corpus read, no slug maths) ---
    if args.unpublish:
        # Guaranteed non-empty: --unpublish requires --ids-file, and the parse
        # above errors out on a file that yielded no ids.
        assert only_ids
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(
            f"build_seo_slugs — mode={mode}, action=UNPUBLISH, type={args.type}\n"
            f"  {len(only_ids)} ids from {args.ids_file}"
        )
        unpublish_type(get_supabase_client(), args.type, only_ids, args.apply)
        if not args.apply:
            print("\n(no rows written — re-run with --apply to persist)")
        print()
        return

    types = (
        ("regulation", "service", "circular") if args.type == "all" else (args.type,)
    )
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"build_seo_slugs — mode={mode}, types={', '.join(types)}")
    if only_ids is not None:
        print(f"  restricted to {len(only_ids)} ids from {args.ids_file}")

    client = get_supabase_client()

    grand = {"total": 0, "already": 0, "new": 0, "collision": 0, "fallback": 0}
    for ct in types:
        stats = process_type(client, ct, args.apply, only_ids)
        for k in grand:
            grand[k] += stats[k]

    print(
        f"\n=== TOTAL ({mode}) ===\n"
        f"  corpus rows        : {grand['total']}\n"
        f"  already slugged    : {grand['already']}\n"
        f"  new slugs          : {grand['new']}"
        f"  (collisions {grand['collision']}, fallbacks {grand['fallback']})"
    )
    if not args.apply:
        print("\n(no rows written — re-run with --apply to persist)")
    print()


if __name__ == "__main__":
    main()
