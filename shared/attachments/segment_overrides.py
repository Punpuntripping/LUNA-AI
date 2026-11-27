"""Section promotions on an uploaded file — the user's «this section is core» (D7).

Plan: ``.claude/plans/attachment_agent.md`` §3.1. Table:
``attachment_segment_overrides`` (migration 234). A promotion is stored as a
CHARACTER RANGE on the FILE (``user_files.file_id``), like the citation tables of
migration 231 — never on ``attachment_segments``, whose rows a re-segmentation
deletes and re-inserts. So it survives a re-segment and follows the file into every
conversation it is attached to (D15).

Applied inside ``agents/memory/attachment_segmenter/store.load_segment_maps``: any
section that is not ``core`` and overlaps an active range loads as ``core``
(promote only — never demote). Every reader of a section's ``value`` (deep-search
delivery, the writer, the angle mapper, the extended view «جوهري», the «الخيوط» /
«الملفات» UI) sees it with no other change.

Nothing here raises: loads return ``{}``, writes ``None`` / ``False``; failures are
logged with ids and the exception CLASS NAME only.
"""
from __future__ import annotations

import dataclasses
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ._backoff import Backoff

logger = logging.getLogger(__name__)

TABLE = "attachment_segment_overrides"

#: The only value a promotion sets (the table's CHECK).
PROMOTED_VALUE = "core"
#: Marker set on a promoted section when its type has the field / is a dict.
PROMOTED_FLAG = "promoted_by_user"

#: Ids per ``in.(…)`` filter (the ``shared.files.store`` batch size).
ID_BATCH = 100

#: After a failed read, skip reads for this long: ``load_segment_maps`` runs on
#: every delivery, so a missing table (code deployed before migration 234) must not
#: cost a failing request and a warning on each call.
RETRY_AFTER_S = 60.0

_COLUMNS = "id, file_id, char_start, char_end, value, created_at"

_backoff = Backoff(RETRY_AFTER_S)


@dataclass(frozen=True)
class SegmentOverride:
    """One active promotion: ``[char_start, char_end)`` of the file's text is core."""

    id: str
    file_id: str
    char_start: int
    char_end: int
    value: str = PROMOTED_VALUE
    created_at: str | None = None


def _data(resp: Any) -> list[dict]:
    data = getattr(resp, "data", None)
    if isinstance(data, dict):
        return [data]
    return [r for r in (data or []) if isinstance(r, dict)]


def _unique(ids: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(i) for i in ids or [] if i))


def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reset_backoff() -> None:
    """Forget an earlier failed read (tests; a manual retry)."""
    _backoff.reset()


# ---------------------------------------------------------------------------
# Pure: apply
# ---------------------------------------------------------------------------


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _range(obj: Any) -> tuple[int, int] | None:
    start, end = _int(_get(obj, "char_start")), _int(_get(obj, "char_end"))
    if start is None or end is None or start < 0 or end <= start:
        return None
    return start, end


def _promoted(section: Any) -> Any:
    """A copy of ``section`` with ``value = "core"`` (+ the marker when it fits)."""
    if isinstance(section, Mapping):
        return {**section, "value": PROMOTED_VALUE, PROMOTED_FLAG: True}
    fields = getattr(type(section), "model_fields", None)
    if fields is not None and hasattr(section, "model_copy"):
        update: dict[str, Any] = {"value": PROMOTED_VALUE}
        if PROMOTED_FLAG in fields:
            update[PROMOTED_FLAG] = True
        return section.model_copy(update=update)
    if dataclasses.is_dataclass(section) and not isinstance(section, type):
        names = {f.name for f in dataclasses.fields(section)}
        if "value" not in names:
            return section
        update = {"value": PROMOTED_VALUE}
        if PROMOTED_FLAG in names:
            update[PROMOTED_FLAG] = True
        return dataclasses.replace(section, **update)
    return section


def apply_segment_overrides(sections: Iterable[Any], overrides: Iterable[Any] | None) -> list[Any]:
    """``sections`` with every non-core section that overlaps an override range
    turned ``core``. Promote only: a ``core`` section, or one with no usable
    position, is returned as is (the same object); nothing is ever demoted.

    Overlap is on half-open ranges — section ``[s, e)`` and override ``[a, b)``
    overlap when ``s < b and a < e`` — so a neighbour that only touches the range
    is not promoted. Sections may be ``StoredSegment`` (copied with
    ``model_copy``, ``promoted_by_user=True``), dicts (copied, the marker key
    added) or dataclasses (``dataclasses.replace``; the marker only if declared).
    Order and every other field are kept. Pure."""
    ranges = [r for r in (_range(o) for o in overrides or []) if r is not None]
    sections = list(sections or [])
    if not ranges:
        return sections
    out: list[Any] = []
    for s in sections:
        span = _range(s)
        if (
            span is None
            or str(_get(s, "value") or "") == PROMOTED_VALUE
            or not any(span[0] < b and a < span[1] for a, b in ranges)
        ):
            out.append(s)
        else:
            out.append(_promoted(s))
    return out


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _override(row: Mapping[str, Any]) -> SegmentOverride | None:
    span = _range(row)
    if span is None or not row.get("file_id"):
        return None
    return SegmentOverride(
        id=str(row.get("id") or ""),
        file_id=str(row["file_id"]),
        char_start=span[0],
        char_end=span[1],
        value=str(row.get("value") or PROMOTED_VALUE),
        created_at=str(row.get("created_at") or "") or None,
    )


def load_segment_overrides(
    supabase: Any, file_ids: Iterable[Any], *, user_id: Any = None
) -> dict[str, list[SegmentOverride]]:
    """``{file_id: [active promotions, oldest first]}`` — files without one are left
    out. One request per :data:`ID_BATCH` files; ``user_id`` also filters by owner.
    Never raises: a failed read returns ``{}`` (sections keep their stored value),
    logs once and skips reads for :data:`RETRY_AFTER_S`."""
    ids = _unique(file_ids)
    if not ids or supabase is None or _backoff.down():
        return {}
    out: dict[str, list[SegmentOverride]] = {}
    try:
        for i in range(0, len(ids), ID_BATCH):
            q = (
                supabase.table(TABLE)
                .select(_COLUMNS)
                .in_("file_id", ids[i:i + ID_BATCH])
                .is_("reverted_at", "null")
            )
            if user_id:
                q = q.eq("user_id", str(user_id))
            for row in _data(q.order("created_at").execute()):
                o = _override(row)
                if o is not None:
                    out.setdefault(o.file_id, []).append(o)
    except Exception as exc:  # noqa: BLE001
        _backoff.failed()
        logger.warning(
            "attachments: segment overrides not read for %d file(s) (%s) — stored values kept",
            len(ids), type(exc).__name__,
        )
        return {}
    return out


# ---------------------------------------------------------------------------
# Writes (service-role client)
# ---------------------------------------------------------------------------


def save_segment_override(
    supabase: Any,
    *,
    file_id: str,
    user_id: str,
    char_start: int,
    char_end: int,
    evidence: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> str | None:
    """Store one promotion of ``[char_start, char_end)`` of the file's text (the
    section's own range — ``StoredSegment.char_start`` / ``char_end``); its id, or
    ``None`` (bad range, missing ids, failed write — logged). ``evidence`` — the
    user's words that asked for it (decoded by the caller). Never raises."""
    start, end = _int(char_start), _int(char_end)
    if not file_id or not user_id or start is None or end is None or start < 0 or end <= start:
        logger.warning("attachments: segment override for file %s not storable (range / ids)", file_id or "-")
        return None
    row = {
        "id": str(uuid.uuid4()),
        "file_id": str(file_id),
        "user_id": str(user_id),
        "char_start": start,
        "char_end": end,
        "value": PROMOTED_VALUE,
        "evidence": str(evidence or ""),
        "conversation_id": str(conversation_id) if conversation_id else None,
        "message_id": str(message_id) if message_id else None,
    }
    try:
        supabase.table(TABLE).insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachments: segment override not stored for file %s (%s)", file_id, type(exc).__name__)
        return None
    return row["id"]


def revert_segment_override(supabase: Any, override_id: str, user_id: str) -> bool:
    """Mark one of the user's active promotions reverted (the row stays). ``True``
    when a row changed; ``False`` when none matched or the write failed. Never
    raises."""
    if not override_id or not user_id:
        return False
    try:
        resp = (
            supabase.table(TABLE)
            .update({"reverted_at": _now()})
            .eq("id", str(override_id))
            .eq("user_id", str(user_id))
            .is_("reverted_at", "null")
            .execute()
        )
        return bool(_data(resp))
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachments: segment override %s not reverted (%s)", override_id, type(exc).__name__)
        return False


__all__ = [
    "PROMOTED_FLAG",
    "PROMOTED_VALUE",
    "SegmentOverride",
    "TABLE",
    "apply_segment_overrides",
    "load_segment_overrides",
    "reset_backoff",
    "revert_segment_override",
    "save_segment_override",
]
