"""Derived wording of a fixed file — fixed ONCE, in place (plan §3.3, last paragraph).

A text fix (``text_corrections``) is an overlay on the file's text; the wording
DERIVED from that text before the fix still repeats the wrong string: the file's
summary and title, the conversation items that carry the file, the segment map's
``what`` / ``owner`` / ``brought_by``, and the angle maps' thread text, client line
and gaps. None of them drives freshness, so :func:`fix_derived_wording` rewrites
them once, at the time of the fix (called after a successful
``save_text_correction``).

Same whole-word rule as ``check_text_fix``: an occurrence inside a longer word
(other than after a proclitic — و، ب، ل، ال…) is left alone. REAL text only — the
caller passes the decoded ``wrong`` / ``right``.

Where each write goes:

- ``user_files`` through ``shared.files.store`` (the module that owns the table:
  ``load_files`` / ``patch_file``); the 222 sync trigger copies title / summary to
  the file's conversation items;
- ``workspace_items`` (the items carrying the file — ``file_id`` = the file, or
  the item itself for a file with no ``user_files`` row) updated directly too, for
  pointers the trigger does not reach and for the legacy rows;
- ``attachment_segments`` rows of the file (a later re-segment rewrites them from
  the corrected text — fine);
- angle maps holding those items: thread fields, the client's party / basis and
  gap issues through ``angle_mapper.edits`` (each change logged, no conversation
  id: bookkeeping of the fix, not a change the user made from a conversation's
  list). The codec is switched off around them: the text is already real.

Undo: every change is RECORDED (``changes``: place, row key, field, before, after
— real text — or the angle-map edit id) and stored on the correction
(``attachment_text_corrections.derived``, :func:`save_derived`).
:func:`undo_derived` puts each field back ONLY while it still reads ``after``, and
undoes the angle-map edits through ``edits.undo_edit``; it names what it could not
restore.

Never raises. Logs ids and class names only, never text.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator

from .text_corrections import TABLE as CORRECTIONS_TABLE
from .text_corrections import _inside_word, _occurrences

logger = logging.getLogger(__name__)

PLACES: tuple[str, ...] = (
    "user_files.title",
    "user_files.summary",
    "workspace_items.title",
    "workspace_items.summary",
    "attachment_segments",
    "attachment_angles",
    "angle_map.client",
    "angle_map.gaps",
)

_SEGMENT_FIELDS = ("what", "owner", "brought_by")
_ANGLE_FIELDS = ("title", "argument", "weakness", "legal_question", "for_party")
_CLIENT_FIELDS = ("party", "basis")
_VALID_CONFIDENCE = ("clear", "likely", "unclear")

#: How a place that could not be restored is named to the user (Arabic).
PLACE_AR: dict[str, str] = {
    "user_files.title": "عنوان الملف",
    "user_files.summary": "ملخص الملف",
    "workspace_items.title": "عنوان الملف في محادثة",
    "workspace_items.summary": "ملخص الملف في محادثة",
    "attachment_segments": "وصف قسم من الملف",
    "angle_map": "الخيوط",
}


def replace_whole(text: Any, wrong: str, right: str) -> tuple[Any, int]:
    """``text`` with every whole-word ``wrong`` → ``right``; and how many. A
    non-string or empty ``text`` comes back as it is."""
    if not isinstance(text, str) or not text or not wrong or wrong == right:
        return text, 0
    out: list[str] = []
    last = n = 0
    for s in _occurrences(text, wrong):
        if _inside_word(text, s, s + len(wrong)):
            continue
        out.append(text[last:s])
        out.append(right)
        last = s + len(wrong)
        n += 1
    if not n:
        return text, 0
    out.append(text[last:])
    return "".join(out), n


def _data(resp: Any) -> list[dict]:
    data = getattr(resp, "data", None)
    if isinstance(data, dict):
        return [data]
    return [r for r in (data or []) if isinstance(r, dict)]


@contextlib.contextmanager
def _no_codec() -> Iterator[None]:
    """No active codec inside: the edits module decodes free text with it, and
    the text here is already real (decoding real text could refuse a figure)."""
    try:
        from backend.app.services.masking_service import reset_active_codec, set_active_codec
    except Exception:  # noqa: BLE001 — no backend (a script): nothing to switch off
        yield
        return
    token = set_active_codec(None)
    try:
        yield
    finally:
        reset_active_codec(token)


def _field(place: str, key: dict, field: str, before: Any, after: Any) -> dict:
    return {"place": place, "key": key, "field": field, "before": before, "after": after}


# ---------------------------------------------------------------------------
# Places
# ---------------------------------------------------------------------------


def _fix_file_row(supabase: Any, file_id: str, user_id: str, wrong: str, right: str, counts: dict,
                  log: list) -> None:
    from shared.files import store as files_store

    rows = files_store.load_files(supabase, [file_id], columns="title, summary", user_id=user_id)
    row = rows.get(str(file_id))
    if not row:
        return
    fields: dict[str, Any] = {}
    for col in ("title", "summary"):
        new, n = replace_whole(row.get(col), wrong, right)
        if n:
            fields[col] = new
    if fields and files_store.patch_file(supabase, str(file_id), fields=fields, user_id=user_id):
        for col, new in fields.items():
            counts[f"user_files.{col}"] += 1
            log.append(_field("user_files", {"file_id": str(file_id)}, col, row.get(col), new))


def _items_of_file(supabase: Any, file_id: str, user_id: str) -> list[dict]:
    rows: dict[str, dict] = {}
    for col in ("file_id", "item_id"):
        q = (
            supabase.table("workspace_items")
            .select("item_id, title, summary")
            .eq(col, str(file_id))
            .eq("user_id", str(user_id))
            .eq("kind", "attachment")
        )
        for r in _data(q.execute()):
            if r.get("item_id"):
                rows[str(r["item_id"])] = r
    return list(rows.values())


def _fix_items(supabase: Any, items: list[dict], wrong: str, right: str, counts: dict, log: list) -> None:
    for r in items:
        fields: dict[str, Any] = {}
        for col in ("title", "summary"):
            new, n = replace_whole(r.get(col), wrong, right)
            if n:
                fields[col] = new
        if not fields:
            continue
        supabase.table("workspace_items").update(fields).eq("item_id", str(r["item_id"])).execute()
        for col, new in fields.items():
            counts[f"workspace_items.{col}"] += 1
            log.append(_field("workspace_items", {"item_id": str(r["item_id"])}, col, r.get(col), new))


def _fix_segments(supabase: Any, file_id: str, wrong: str, right: str, counts: dict, log: list) -> None:
    rows = _data(
        supabase.table("attachment_segments")
        .select("item_id, seq, " + ", ".join(_SEGMENT_FIELDS))
        .eq("item_id", str(file_id))
        .execute()
    )
    for r in rows:
        fields = {}
        for col in _SEGMENT_FIELDS:
            new, n = replace_whole(r.get(col), wrong, right)
            if n:
                fields[col] = new
        if fields:
            (supabase.table("attachment_segments").update(fields)
             .eq("item_id", str(file_id)).eq("seq", r["seq"]).execute())
            counts["attachment_segments"] += 1
            for col, new in fields.items():
                log.append(_field("attachment_segments", {"item_id": str(file_id), "seq": r["seq"]},
                                  col, r.get(col), new))


def _fix_maps(
    supabase: Any, item_ids: list[str], user_id: str, wrong: str, right: str, counts: dict, log: list,
    *, evidence: str, message_id: str | None,
) -> None:
    from agents.memory.angle_mapper import edits as E
    from agents.memory.angle_mapper.store import load_angle_map_for_item

    who = {"evidence": evidence, "user_id": user_id, "conversation_id": None, "message_id": message_id}
    done: set[str] = set()
    for item_id in item_ids:
        stored = load_angle_map_for_item(supabase, item_id)
        if not stored:
            continue
        head = stored.get("map") or {}
        map_id = str(head.get("map_id") or "")
        if not map_id or map_id in done or str(head.get("user_id") or "") != str(user_id):
            continue
        done.add(map_id)
        for a in stored.get("angles") or []:
            fields = {}
            for col in _ANGLE_FIELDS:
                new, n = replace_whole(a.get(col), wrong, right)
                if n:
                    fields[col] = new
            if fields:
                r = E.edit_angle(supabase, map_id, int(a.get("seq") or 0), fields, **who)
                _count(r, counts, "attachment_angles", map_id, log)
        client = dict(head.get("client") or {})
        fields = {}
        for col in _CLIENT_FIELDS:
            new, n = replace_whole(client.get(col), wrong, right)
            if n:
                fields[col] = new
        if fields:
            if client.get("confidence") in _VALID_CONFIDENCE:
                fields["confidence"] = client["confidence"]   # a wording fix keeps the confidence
            r = E.edit_client(supabase, map_id, fields, **who)
            _count(r, counts, "angle_map.client", map_id, log)
        for index, gap in enumerate([g for g in head.get("gaps") or [] if isinstance(g, dict)], start=1):
            new, n = replace_whole(gap.get("issue"), wrong, right)
            if n:
                r = E.edit_gap(supabase, map_id, index, {"issue": new}, **who)
                _count(r, counts, "angle_map.gaps", map_id, log)


def _count(result: Any, counts: dict, place: str, map_id: str, log: list) -> None:
    if getattr(result, "ok", False):
        counts[place] += 1
        log.append({"place": "angle_map", "map_id": map_id, "edit_id": result.edit_id,
                    "target": getattr(result, "target", "")})
    else:
        logger.warning("attachments: derived wording not fixed in %s of map %s", place, map_id)


# ---------------------------------------------------------------------------
# Fix
# ---------------------------------------------------------------------------


def fix_derived_wording(
    supabase: Any,
    *,
    file_id: str,
    user_id: str,
    wrong: str,
    right: str,
    evidence: str = "",
    message_id: str | None = None,
    changes: list | None = None,
) -> dict[str, int]:
    """Replace the REAL ``wrong`` with ``right`` (whole words) in the wording
    derived from file ``file_id`` — see the module docstring for the places.
    ``evidence`` — the user's words (the angle-map log rows need one; a default
    naming the fix is used when empty). ``changes`` — when given, every change is
    appended to it (what :func:`save_derived` stores for the undo). Returns
    ``{place: rows changed}`` for every place in :data:`PLACES`. Never raises: a
    place that fails is logged and left."""
    counts = {p: 0 for p in PLACES}
    log: list = changes if changes is not None else []
    if not supabase or not file_id or not user_id or not wrong or not right or wrong == right:
        return counts
    file_id, user_id = str(file_id), str(user_id)
    evidence = (evidence or "").strip() or f"تصحيح نص الملف: «{wrong}» ← «{right}»"

    def step(name: str, fn: Any, *args: Any, **kw: Any) -> Any:
        try:
            return fn(*args, **kw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("attachments: derived wording (%s) failed for file %s (%s)", name, file_id, type(exc).__name__)
            return None

    step("user_files", _fix_file_row, supabase, file_id, user_id, wrong, right, counts, log)
    items = step("items", _items_of_file, supabase, file_id, user_id) or []
    step("workspace_items", _fix_items, supabase, items, wrong, right, counts, log)
    step("segments", _fix_segments, supabase, file_id, wrong, right, counts, log)
    with _no_codec():
        step("angle_maps", _fix_maps, supabase, [str(r["item_id"]) for r in items], user_id, wrong, right,
             counts, log, evidence=evidence, message_id=message_id)
    return counts


def save_derived(supabase: Any, correction_id: str, user_id: str, changes: list) -> bool:
    """Store ``changes`` on the correction (``derived``). ``False`` on a failed
    write (logged; the undo then restores only the text). Never raises."""
    if not correction_id or not user_id:
        return False
    try:
        (supabase.table(CORRECTIONS_TABLE).update({"derived": list(changes or [])})
         .eq("id", str(correction_id)).eq("user_id", str(user_id)).execute())
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachments: derived changes not stored for correction %s (%s)",
                       correction_id, type(exc).__name__)
        return False


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


def _read_field(supabase: Any, place: str, key: dict, field: str, user_id: str) -> tuple[bool, Any]:
    """``(found, current value)``."""
    if place == "user_files":
        from shared.files import store as files_store

        row = files_store.load_files(supabase, [key["file_id"]], columns=field, user_id=user_id).get(
            str(key["file_id"]))
        return (row is not None, (row or {}).get(field))
    q = supabase.table(place).select(field)
    for k, v in key.items():
        q = q.eq(k, v)
    if place == "workspace_items":
        q = q.eq("user_id", user_id)
    rows = _data(q.limit(1).execute())
    return (bool(rows), rows[0].get(field) if rows else None)


def _write_field(supabase: Any, place: str, key: dict, field: str, value: Any, user_id: str) -> bool:
    if place == "user_files":
        from shared.files import store as files_store

        return bool(files_store.patch_file(supabase, str(key["file_id"]), fields={field: value}, user_id=user_id))
    q = supabase.table(place).update({field: value})
    for k, v in key.items():
        q = q.eq(k, v)
    if place == "workspace_items":
        q = q.eq("user_id", user_id)
    return bool(_data(q.execute()))


def undo_derived(
    supabase: Any,
    correction_id: str,
    user_id: str,
    *,
    evidence: str = "",
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> list[str]:
    """Put back the wording :func:`fix_derived_wording` changed for this
    correction, newest first. A field is restored only while it still reads what
    the fix wrote (already back = fine); an angle-map edit through
    ``edits.undo_edit`` (which refuses when that part changed since). Returns the
    Arabic names (:data:`PLACE_AR`) of what could NOT be restored — ``[]`` when
    everything is back or nothing was recorded. Never raises."""
    try:
        rows = _data(
            supabase.table(CORRECTIONS_TABLE).select("derived")
            .eq("id", str(correction_id)).eq("user_id", str(user_id)).limit(1).execute()
        )
    except Exception as exc:  # noqa: BLE001 — e.g. the column not there yet
        logger.warning("attachments: derived changes not read for correction %s (%s)",
                       correction_id, type(exc).__name__)
        return []
    changes = [c for c in ((rows[0].get("derived") if rows else None) or []) if isinstance(c, dict)]
    missed: list[str] = []
    for c in reversed(changes):
        place = str(c.get("place") or "")
        try:
            if place == "angle_map":
                from agents.memory.angle_mapper import edits as E

                with _no_codec():
                    r = E.undo_edit(supabase, str(c.get("edit_id") or ""), str(user_id), evidence=evidence,
                                    conversation_id=conversation_id, message_id=message_id)
                ok = r.ok or r.reason == E.R_UNDO_DONE
            else:
                key, field = dict(c.get("key") or {}), str(c.get("field") or "")
                found, now = _read_field(supabase, place, key, field, str(user_id))
                if not found or now == c.get("before"):
                    ok = found
                elif now == c.get("after"):
                    ok = _write_field(supabase, place, key, field, c.get("before"), str(user_id))
                else:
                    ok = False
        except Exception as exc:  # noqa: BLE001
            logger.warning("attachments: derived change in %s not restored for correction %s (%s)",
                           place, correction_id, type(exc).__name__)
            ok = False
        if not ok:
            name = PLACE_AR.get(f"{place}.{c.get('field')}") or PLACE_AR.get(place) or place
            if name not in missed:
                missed.append(name)
    return missed


__all__ = ["PLACES", "PLACE_AR", "fix_derived_wording", "replace_whole", "save_derived", "undo_derived"]
