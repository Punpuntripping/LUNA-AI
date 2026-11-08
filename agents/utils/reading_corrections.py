"""Reading corrections: a report sentence the full text showed to be wrong.

The artifact_analyzer finds them with ``read_full_text`` (plan
artifact_analyzer_scope_full_text.md §4.4). Each one is saved as a note on the
report, in ``workspace_items.metadata.reading_corrections``: no migration and no
new table. The card the user sees never changes (D13). Every agent that later
reads the report reads the note with it, so the same error is not made again.

Stored real, like every other stored text: the caller decodes what the model
read under وضع السرية before it saves.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

logger = logging.getLogger(__name__)

KEY = "reading_corrections"
#: A report rarely has more than one or two. The cap keeps a runaway loop from
#: growing one row without bound.
MAX_ENTRIES = 20

_HEADER = "تصحيح بعد قراءة النص الكامل:"
_LINE = "- ورد في التقرير: «{report}». والصحيح وفق «{source}»: «{corrected}»."
_DEFAULT_SOURCE = "النص الكامل"


def entries_of(value: Any) -> list[Mapping[str, Any]]:
    """The saved corrections, from a report's metadata or from the list itself."""
    if isinstance(value, Mapping):
        value = value.get(KEY)
    if not isinstance(value, list):
        return []
    return [
        e for e in value
        if isinstance(e, Mapping) and str(e.get("report_text") or "").strip()
        and str(e.get("corrected_text") or "").strip()
    ]


def render_block(value: Any) -> str:
    """The note in Arabic, or "" when the report has none."""
    entries = entries_of(value)
    if not entries:
        return ""
    lines = [_HEADER] + [
        _LINE.format(
            report=str(e["report_text"]).strip(),
            source=str(e.get("source_name") or "").strip() or _DEFAULT_SOURCE,
            corrected=str(e["corrected_text"]).strip(),
        )
        for e in entries
    ]
    return "\n".join(lines)


def with_corrections(text: str, value: Any) -> str:
    """``text`` with the note after it. Unchanged when there is none."""
    block = render_block(value)
    if not block:
        return text
    body = (text or "").rstrip()
    return f"{body}\n\n{block}" if body else block


def _key(entry: Mapping[str, Any]) -> tuple[str, str]:
    return (str(entry.get("report_text") or "").strip(), str(entry.get("open_key") or ""))


def merge(
    existing: Iterable[Mapping[str, Any]], new: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Existing first; a new entry is added once per (report_text, open_key)."""
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in [*existing, *new]:
        key = _key(entry)
        if key[0] and key not in seen:
            seen.add(key)
            merged.append(dict(entry))
    return merged[:MAX_ENTRIES]


def save(
    supabase: Any,
    *,
    user_id: str,
    conversation_id: str,
    corrections: Iterable[Any],
    message_id: str | None = None,
    decode: Callable[[str], str] | None = None,
) -> int:
    """Write each correction onto its report. Returns how many were new.

    Never raises: a correction that is not saved costs only the note, never the
    reply. ``user_id`` and ``conversation_id`` scope every read and write (the
    client is service-role).
    """
    at = datetime.now(timezone.utc).isoformat()
    by_report: dict[str, list[dict[str, Any]]] = {}
    for c in corrections:
        report_text = c.report_text
        if decode is not None:
            try:
                report_text = decode(report_text)
            except Exception:  # noqa: BLE001 — skip one rather than save a fake
                continue
        by_report.setdefault(c.report_id, []).append({
            "report_text": report_text,
            "corrected_text": c.corrected_text,
            "source_name": c.source_name,
            "domain": c.domain,
            "open_key": c.open_key,
            "message_id": message_id,
            "at": at,
        })

    added = 0
    for report_id, entries in by_report.items():
        try:
            rows = (
                supabase.table("workspace_items")
                .select("metadata")
                .eq("item_id", report_id)
                .eq("user_id", user_id)
                .eq("conversation_id", conversation_id)
                .is_("deleted_at", "null")
                .limit(1)
                .execute()
                .data
            ) or []
            if not rows:
                continue
            metadata = rows[0].get("metadata")
            metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
            before = entries_of(metadata)
            merged = merge(before, entries)
            if len(merged) == len(before):
                continue
            metadata[KEY] = merged
            (
                supabase.table("workspace_items")
                .update({"metadata": metadata})
                .eq("item_id", report_id)
                .eq("user_id", user_id)
                .eq("conversation_id", conversation_id)
                .execute()
            )
            added += len(merged) - len(before)
        except Exception as exc:  # noqa: BLE001 — see the docstring
            logger.warning("reading_corrections: save on %s failed: %s", report_id, exc)
    return added
