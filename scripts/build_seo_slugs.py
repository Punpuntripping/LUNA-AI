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

Run from the repo root:
  python scripts/build_seo_slugs.py                 # dry-run, all types
  python scripts/build_seo_slugs.py --type regulation
  python scripts/build_seo_slugs.py --type circular
  python scripts/build_seo_slugs.py --apply         # write

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


def process_type(client, content_type: str, apply: bool) -> dict:
    """Compute (and, when ``apply``, write) slugs for one content_type.

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
        stats["total"] += 1
        cid = str(row.get("id"))

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


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Populate seo_item_meta slugs for the SEO public library."
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
    args = ap.parse_args()

    types = (
        ("regulation", "service", "circular") if args.type == "all" else (args.type,)
    )
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"build_seo_slugs — mode={mode}, types={', '.join(types)}")

    client = get_supabase_client()

    grand = {"total": 0, "already": 0, "new": 0, "collision": 0, "fallback": 0}
    for ct in types:
        stats = process_type(client, ct, args.apply)
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
