"""Record that one regulation replaces another, with its transition dates.

The data side of ``shared/library/supersession.py``: adding the next replaced
law is this script plus the library steps, with no code or prompt change. Run it
once per pair, so a law and its لائحة are two runs sharing one note file.

It writes three columns on the OLD row's successor list and dates, and the note
on each NEW row. The new rows are written first and the old row last, because a
pair only exists once ``superseded_by`` is set: a run that dies halfway leaves a
harmless note, never a half-built pair.

Everything is validated before anything is written: both rows exist, each new
row has ``effective_date`` (or ``--superseded-on`` is given), the note is not
empty, the dates ascend, and every chunk ref exists ON THE OLD ROW.

The note must be COPIED from the source, never retyped. A note that isn't a
verbatim part of the new row's ``intro`` draws a warning, not an error, since
the provisions may sit in an instrument the row doesn't hold.

``--dry-run`` is the DEFAULT: it prints what it would write and writes nothing.
Pass ``--apply`` to write. Re-running with the same arguments is a no-op.

Dates default to +3 and +12 months from ``superseded_on``, which itself defaults
to the new row's ``effective_date``. End dates are inclusive.

Run from the repo root:
  python scripts/set_supersession.py --old <ref> --new <ref> --note-file note.txt
  python scripts/set_supersession.py --old <ref> --new <ref> --note-file note.txt \
      --surviving "<part>=<chunk_ref>,<chunk_ref>" \
      --surviving-title "<part>=<law the part moves to>"
  python scripts/set_supersession.py ... --apply

  # a split: repeat --new
  python scripts/set_supersession.py --old <ref> --new <ref> --new <ref> ...

  # a merge: run once per old row, with the same --new

``--surviving`` names a part of the old law that stays in force with no end
date. Removing that entry from ``superseded_by`` by hand later ends the
exception. Without ``--surviving-title`` the entry's title is the part's name.

AFTER: refresh_search_index('regulation') + refresh_bm25_stats('regulation'),
then the library steps in the plan.

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY (via shared.config / shared.db.client).
"""
from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

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

_VIEW = "regulations_v2"
_CHUNKS_VIEW = "chunks_v2"
_ROW_COLUMNS = "id, reg_ref, clean_title, title, effective_date, intro"

_PERIOD_1_MONTHS = 3
_PERIOD_2_MONTHS = 12


def add_months(day: date, months: int) -> date:
    """``day`` plus calendar months, clamped to the end of a shorter month."""
    years, month_index = divmod(day.month - 1 + months, 12)
    year = day.year + years
    month = month_index + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def parse_surviving(values: list[str]) -> dict[str, list[str]]:
    """``["<part>=<ref>,<ref>"]`` into ``{part: [refs]}``. Raises ``ValueError``."""
    parts: dict[str, list[str]] = {}
    for value in values:
        part, separator, refs = value.partition("=")
        part = part.strip()
        chunk_refs = [r.strip() for r in refs.split(",") if r.strip()]
        if not separator or not part or not chunk_refs:
            raise ValueError(f"--surviving needs <part>=<chunk_ref>,...: {value!r}")
        if part in parts:
            raise ValueError(f"--surviving names a part twice: {part!r}")
        parts[part] = chunk_refs
    return parts


def parse_titles(values: list[str]) -> dict[str, str]:
    """``["<part>=<title>"]`` into ``{part: title}``. Raises ``ValueError``."""
    titles: dict[str, str] = {}
    for value in values:
        part, separator, title = value.partition("=")
        if not separator or not part.strip() or not title.strip():
            raise ValueError(f"--surviving-title needs <part>=<title>: {value!r}")
        titles[part.strip()] = title.strip()
    return titles


def _fetch_row(client: Any, ref: str) -> dict | None:
    rows = client.table(_VIEW).select(_ROW_COLUMNS).eq("reg_ref", ref).execute().data
    return rows[0] if rows else None


def _chunks_on_row(client: Any, regulation_id: str, refs: list[str]) -> set[str]:
    if not refs:
        return set()
    rows = (
        client.table(_CHUNKS_VIEW)
        .select("chunk_ref")
        .eq("regulation_id", regulation_id)
        .in_("chunk_ref", refs)
        .execute()
        .data
    )
    return {r["chunk_ref"] for r in rows or []}


def build_plan(
    client: Any,
    old_ref: str,
    new_refs: list[str],
    note: str,
    surviving: dict[str, list[str]],
    part_titles: dict[str, str],
    superseded_on: date | None,
    period_1_end: date | None,
    period_2_end: date | None,
) -> tuple[dict | None, list[str], list[str]]:
    """Validate everything and return ``(plan, errors, warnings)``.

    ``plan`` is ``None`` whenever ``errors`` isn't empty. Reads only.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if old_ref in new_refs:
        errors.append(f"{old_ref} is both --old and --new")
    if not note:
        errors.append("the note file is empty")
    for part in part_titles:
        if part not in surviving:
            errors.append(f"--surviving-title names an unknown part: {part!r}")

    old_row = _fetch_row(client, old_ref)
    if old_row is None:
        errors.append(f"old row not found: {old_ref}")
    new_rows = []
    for ref in new_refs:
        row = _fetch_row(client, ref)
        if row is None:
            errors.append(f"new row not found: {ref}")
        else:
            new_rows.append(row)

    if old_row is not None:
        wanted = [r for refs in surviving.values() for r in refs]
        found = _chunks_on_row(client, old_row["id"], wanted)
        for ref in wanted:
            if ref not in found:
                errors.append(f"chunk {ref} is not on {old_ref}")

    start = superseded_on
    for row in new_rows:
        if start is None and row.get("effective_date"):
            start = date.fromisoformat(row["effective_date"])
        if start is None:
            errors.append(
                f"{row['reg_ref']} has no effective_date: set it at ingestion "
                "or pass --superseded-on"
            )
        if note and note not in (row.get("intro") or ""):
            warnings.append(
                f"the note is not a verbatim part of {row['reg_ref']}'s intro: "
                "check it was copied, not retyped"
            )

    dates: dict[str, str] | None = None
    if start is not None:
        first_end = period_1_end or add_months(start, _PERIOD_1_MONTHS)
        second_end = period_2_end or add_months(start, _PERIOD_2_MONTHS)
        if not start < first_end < second_end:
            errors.append(f"dates must ascend: {start}, {first_end}, {second_end}")
        dates = {
            "superseded_on": start.isoformat(),
            "period_1_end": first_end.isoformat(),
            "period_2_end": second_end.isoformat(),
        }

    if errors:
        return None, errors, warnings

    superseded_by: list[dict] = [
        {"reg_ref": r["reg_ref"], "title": r.get("clean_title") or r.get("title")}
        for r in new_rows
    ]
    for part, chunk_refs in surviving.items():
        superseded_by.append(
            {
                "reg_ref": None,
                "title": part_titles.get(part, part),
                "part": part,
                "pending": True,
                "chunk_refs": chunk_refs,
            }
        )
    plan = {
        "old_ref": old_ref,
        "new_refs": new_refs,
        "old_update": {"superseded_by": superseded_by, "superseded_dates": dates},
        "new_update": {"transition_period_note": note},
    }
    return plan, errors, warnings


def apply_plan(client: Any, plan: dict) -> None:
    """Write the plan: new rows first, the old row last."""
    for ref in plan["new_refs"]:
        client.table(_VIEW).update(plan["new_update"]).eq("reg_ref", ref).execute()
    client.table(_VIEW).update(plan["old_update"]).eq("reg_ref", plan["old_ref"]).execute()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record a regulation supersession. Dry run by default."
    )
    parser.add_argument("--old", required=True, help="reg_ref of the replaced row")
    parser.add_argument(
        "--new", required=True, action="append", help="reg_ref of a successor; repeat for a split"
    )
    parser.add_argument(
        "--note-file", required=True, type=Path, help="UTF-8 file holding the provisions, copied"
    )
    parser.add_argument(
        "--surviving", action="append", default=[], help='"<part>=<chunk_ref>,<chunk_ref>"'
    )
    parser.add_argument(
        "--surviving-title", action="append", default=[], help='"<part>=<law the part moves to>"'
    )
    parser.add_argument("--superseded-on", type=date.fromisoformat, help="default: new row's effective_date")
    parser.add_argument("--period-1-end", type=date.fromisoformat, help="default: +3 months")
    parser.add_argument("--period-2-end", type=date.fromisoformat, help="default: +12 months")
    parser.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, client: Any = None) -> int:
    args = _parse_args(argv)
    try:
        surviving = parse_surviving(args.surviving)
        part_titles = parse_titles(args.surviving_title)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2

    note = args.note_file.read_text(encoding="utf-8").strip()
    client = client or get_supabase_client()
    plan, errors, warnings = build_plan(
        client,
        args.old,
        args.new,
        note,
        surviving,
        part_titles,
        args.superseded_on,
        args.period_1_end,
        args.period_2_end,
    )
    for warning in warnings:
        print(f"WARNING: {warning}")
    if plan is None:
        for error in errors:
            print(f"ERROR: {error}")
        print("Nothing written.")
        return 1

    print(f"Old row {plan['old_ref']}:")
    print(json.dumps(plan["old_update"], ensure_ascii=False, indent=2))
    print(f"New rows {', '.join(plan['new_refs'])}: note of {len(note):,} characters")

    if not args.apply:
        print("DRY RUN: nothing written. Pass --apply to write.")
        return 0

    apply_plan(client, plan)
    print("Written. Takes effect within 5 minutes (the helper's cache).")
    print("AFTER: refresh_search_index('regulation') + refresh_bm25_stats('regulation').")
    return 0


if __name__ == "__main__":
    sys.exit(main())
