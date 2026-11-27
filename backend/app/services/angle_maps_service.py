"""«الأوجه» at the front of «الملفات» — the user's angle maps.

Plan: ``.claude/plans/angle_map_ui.md`` (rev 3, owner 2026-11-24: «الأوجه» first in
the chat pane's «الملفات» tab and on the /files page, «الملفات» second; no tab
inside an opened file). Read-only. An angle map belongs to the MESSAGE the files
were sent in (``attachment_angle_maps``, one per message group, members by
conversation item):

- ``list_angle_maps`` — the caller's maps, newest first, optionally of one
  conversation: light rows (documents' titles, angle counts) for the /files list
  and the pane. A map is listed only while it is still fresh at member level —
  every member live, its segment map the one the map was made from (same length,
  prompt version, paragraph rule, ``segmented_at``) — and its conversation is not
  deleted. Batched: a few reads for the whole list.
- ``get_angle_map`` — one map in full, through the mapper's own freshness check
  (``load_fresh_angle_map_for_item``); sections from the segmenter's own loader
  (by file id), الموكّل / الخصم / محايد from the mapper's own ``labels``; only the
  sections an angle or a gap rests on are sent, with their text.

Behind ``ANGLE_MAP_UI_ENABLED`` / ``ANGLE_MAP_UI_USERS`` (on top of «الملفات»'s
own switch). A map exists only where the mapper's switch built one.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from supabase import Client as SupabaseClient

from agents.memory.angle_mapper.labels import (
    angle_viewpoint,
    relative_label,
    resolve_ref,
    section_index,
)
from agents.memory.angle_mapper.group import _same_version, _ts, segment_headers, stored_versions
from agents.memory.angle_mapper.store import load_fresh_angle_map_for_item
from agents.memory.attachment_segmenter.store import load_segment_maps
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services import files_service
from shared.attachments.overlay import corrections_for_item_ids
from shared.attachments.text_corrections import apply_text_corrections
from shared.config import get_settings
from shared.files import store as files_store

logger = logging.getLogger(__name__)

ITEMS_TABLE = "workspace_items"
CONVERSATIONS_TABLE = "conversations"
MAPS_TABLE = "attachment_angle_maps"
MAP_ITEMS_TABLE = "attachment_angle_map_items"
ANGLES_TABLE = "attachment_angles"

#: Maps listed at most (newest first).
LIST_CAP = 200
#: Ids per ``in.(…)`` filter / rows per page.
_BATCH = 100
_PAGE = 1_000

_REF = re.compile(r"^D(\d+)\.S(\d+)$")
_DOC = re.compile(r"(\d+)")
_KINDS = frozenset({"strengthen", "attack", "answer"})
_STRENGTHS = frozenset({"strong", "medium", "weak"})
_CONFIDENCES = frozenset({"clear", "likely", "unclear"})
_GAP_KINDS = frozenset({"missing", "contradiction", "unanswered"})
_SIDES = frozenset({"own", "opp", "neutral"})


def angle_maps_ui_enabled(user_id: Any) -> bool:
    """``ANGLE_MAP_UI_ENABLED`` and (no ``ANGLE_MAP_UI_USERS`` allowlist, or this
    ``users.user_id`` is on it). Off on error — off is today's app."""
    try:
        settings = get_settings()
        if not getattr(settings, "ANGLE_MAP_UI_ENABLED", False):
            return False
        users = getattr(settings, "ANGLE_MAP_UI_USERS", None)
        allowed = {u.strip() for u in str(users or "").split(",") if u.strip()}
        return not allowed or str(user_id or "") in allowed
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------


def _data(resp: Any) -> list[dict]:
    data = getattr(resp, "data", None)
    return data if isinstance(data, list) else []


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> Optional[str]:
    text = " ".join(str(value or "").split())
    return text or None


def _batches(ids: list[str]) -> list[list[str]]:
    return [ids[i:i + _BATCH] for i in range(0, len(ids), _BATCH)]


def _rows_in(supabase: SupabaseClient, table: str, columns: str, col: str, ids: list[str]) -> list[dict]:
    """Every row of ``table`` whose ``col`` is in ``ids`` — batched ids, paged rows.
    Raises on a failed read."""
    out: list[dict] = []
    for batch in _batches(ids):
        offset = 0
        while True:
            page = _data(
                supabase.table(table).select(columns).in_(col, batch).range(offset, offset + _PAGE - 1).execute()
            )
            out.extend(page)
            if len(page) < _PAGE:
                break
            offset += _PAGE
    return out


def _conversations(supabase: SupabaseClient, user_id: str, ids: list[str]) -> dict[str, dict]:
    """``{conversation_id: {title, deleted}}`` of the caller's conversations. Fail-soft."""
    out: dict[str, dict] = {}
    try:
        for batch in _batches(ids):
            for r in _data(
                supabase.table(CONVERSATIONS_TABLE)
                .select("conversation_id, title_ar, deleted_at")
                .in_("conversation_id", batch)
                .eq("user_id", user_id)
                .execute()
            ):
                out[str(r["conversation_id"])] = {
                    "title": _str_or_none(r.get("title_ar")),
                    "deleted": bool(r.get("deleted_at")),
                }
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_maps: conversation read failed (%s)", type(exc).__name__)
    return out


def _item_rows(supabase: SupabaseClient, user_id: str, item_ids: list[str]) -> dict[str, dict]:
    """``{item_id: {title, wi_seq, doc_type}}`` of the caller's LIVE items — the
    file's title and type through ``shared.files``. Fail-soft: ``{}``."""
    if not item_ids:
        return {}
    try:
        rows: list[dict] = []
        cols = files_store.with_file_id("item_id, title, wi_seq, metadata", user_id=user_id, supabase=supabase)
        for batch in _batches(item_ids):
            rows.extend(_data(
                supabase.table(ITEMS_TABLE)
                .select(cols)
                .in_("item_id", batch)
                .eq("user_id", user_id)
                .is_("deleted_at", "null")
                .execute()
            ))
        rows = files_store.overlay_files(supabase, rows, user_id=user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_maps: item read failed (%s)", type(exc).__name__)
        return {}
    out = {}
    for r in rows:
        meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        out[str(r["item_id"])] = {
            "title": str(r.get("title") or ""),
            "wi_seq": _int_or_none(r.get("wi_seq")),
            "doc_type": files_service._doc_type(meta),
        }
    return out


def _conversation_title(supabase: SupabaseClient, user_id: str, conversation_id: Any) -> Optional[str]:
    """The conversation's title, or ``None`` (none, deleted, or a failed read)."""
    if not conversation_id:
        return None
    try:
        row = files_service._first(
            supabase.table(CONVERSATIONS_TABLE)
            .select("title_ar, deleted_at")
            .eq("conversation_id", str(conversation_id))
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_maps: conversation read failed (%s)", type(exc).__name__)
        return None
    if row is None or row.get("deleted_at"):
        return None
    return _str_or_none(row.get("title_ar"))


def _doc_number(value: Any) -> Optional[int]:
    m = _DOC.search(str(value or ""))
    return int(m.group(1)) if m else None


def _label(index: dict, ref: str, angle: dict, client: dict) -> Optional[str]:
    """الموكّل / الخصم / محايد of one section from the angle's (or, with ``{}``,
    the client's) viewpoint — the preview page's rule. ``None`` when it cannot
    be told."""
    section = resolve_ref(index, ref)
    if section is None:
        return None
    try:
        me, party = angle_viewpoint(angle, client, ref)
        if me is None:
            return None
        label = str(relative_label(section, me, party))
    except Exception:  # noqa: BLE001
        return None
    return label if label in _SIDES else None


def _refs(value: Any, index: dict) -> list[str]:
    """Known refs of ``value``, in order, without repeats."""
    out: list[str] = []
    for r in value or []:
        ref = str(r or "")
        if _REF.match(ref) and ref in index and ref not in out:
            out.append(ref)
    return out


# ---------------------------------------------------------------------------
# One map → the response
# ---------------------------------------------------------------------------


def _map_out(supabase: SupabaseClient, user_id: str, stored: dict) -> dict:
    """One stored map (``load_angle_map_for_item`` shape) as ``AngleMap``."""
    head = stored["map"]
    members = [m for m in stored.get("items") or [] if m.get("item_id")]
    member_ids = [str(m["item_id"]) for m in members]
    client_raw = head.get("client") if isinstance(head.get("client"), dict) else {}

    seg_maps = load_segment_maps(supabase, member_ids, user_id=user_id)
    index = section_index(members, seg_maps)

    angles = []
    used: list[str] = []
    for a in stored.get("angles") or []:
        refs = _refs(a.get("sections"), index)
        used.extend(refs)
        angles.append({
            "seq": _int_or_none(a.get("seq")) or len(angles) + 1,
            "kind": a.get("kind") if a.get("kind") in _KINDS else "strengthen",
            "strength": a.get("strength") if a.get("strength") in _STRENGTHS else "medium",
            "for_side": str(a.get("for_side") or ""),
            "for_party": str(a.get("for_party") or ""),
            "title": str(a.get("title") or ""),
            "argument": str(a.get("argument") or ""),
            "legal_question": str(a.get("legal_question") or ""),
            "weakness": str(a.get("weakness") or ""),
            "ocr_suspect": bool(a.get("ocr_suspect")),
            "sections": [{"ref": r, "side": _label(index, r, a, client_raw)} for r in refs],
        })

    gaps = []
    for g in head.get("gaps") or []:
        if not isinstance(g, dict):
            continue
        refs = _refs(g.get("sections"), index)
        used.extend(refs)
        gaps.append({
            "kind": g.get("kind") if g.get("kind") in _GAP_KINDS else "missing",
            "issue": str(g.get("issue") or ""),
            "ocr_suspect": bool(g.get("ocr_suspect")),
            "sections": [{"ref": r, "side": _label(index, r, {}, client_raw)} for r in refs],
        })

    # The user's text fixes (attachment_agent.md §3.3) on the section text the
    # AngleSectionDialog shows — keyed by file, so they follow a «الملفات» file into
    # every conversation. One read, only when a section is sent and the attachment
    # agent is on for the user, keyed by the files the segment loader resolved;
    # none / flag off / a failed read = the stored text.
    used_items = list(dict.fromkeys(str(index[ref].get("item_id") or "") for ref in used))
    fixes = corrections_for_item_ids(
        supabase, used_items, user_id=user_id,
        file_keys={i: getattr(seg_maps.get(i), "file_key", None) for i in used_items},
    )
    sections = []
    for ref in dict.fromkeys(used):
        s = index[ref]
        m = _REF.match(ref)
        sections.append({
            "ref": ref,
            "document": int(m.group(1)),
            "seq": int(m.group(2)),
            "what": str(s.get("what") or ""),
            "value": str(s.get("value") or ""),
            "owner_class": _str_or_none(s.get("owner_class")),
            "owner": _str_or_none(s.get("owner")),
            "brought_by": _str_or_none(s.get("brought_by")),
            "page_from": _int_or_none(s.get("page_from")),
            "page_to": _int_or_none(s.get("page_to")),
            "side": _label(index, ref, {}, client_raw),
            "content_md": apply_text_corrections(
                str(s.get("content_md") or ""), fixes.get(str(s.get("item_id") or "")),
            ),
        })

    left_raw = [x for x in head.get("left_out") or [] if isinstance(x, dict) and x.get("item_id")]
    items = _item_rows(supabase, user_id, member_ids + [str(x["item_id"]) for x in left_raw])
    documents = [
        {
            "index": _int_or_none(m.get("doc_index")) or i + 1,
            "item_id": str(m["item_id"]),
            "title": (items.get(str(m["item_id"])) or {}).get("title") or "",
            "doc_type": (items.get(str(m["item_id"])) or {}).get("doc_type"),
            "wi_seq": (items.get(str(m["item_id"])) or {}).get("wi_seq"),
        }
        for i, m in enumerate(members)
    ]
    left_out = [
        {
            "item_id": str(x["item_id"]),
            "title": (items.get(str(x["item_id"])) or {}).get("title") or "",
            "reason": str(x.get("reason") or ""),
        }
        for x in left_raw
    ]

    by_document = []
    for e in client_raw.get("by_document") or []:
        n = _doc_number(e.get("document")) if isinstance(e, dict) else None
        if n:
            by_document.append({"document": n, "side": str(e.get("side") or ""), "party": str(e.get("party") or "")})
    confidence = client_raw.get("confidence")

    return {
        "map_id": str(head.get("map_id") or ""),
        "conversation_id": str(head["conversation_id"]) if head.get("conversation_id") else None,
        "conversation_title": _conversation_title(supabase, user_id, head.get("conversation_id")),
        "message_id": str(head["message_id"]) if head.get("message_id") else None,
        "created_at": str(head.get("created_at") or ""),
        "prompt_version": str(head.get("prompt_version") or ""),
        "client": {
            "side": str(client_raw.get("side") or ""),
            "party": str(client_raw.get("party") or ""),
            "basis": str(client_raw.get("basis") or ""),
            "confidence": confidence if confidence in _CONFIDENCES else "unclear",
            "by_document": by_document,
        },
        "documents": documents,
        "left_out": left_out,
        "sections": sections,
        "angles": angles,
        "gaps": gaps,
    }


def _fresh_members(
    supabase: SupabaseClient, user_id: str, items: list[dict], live: set[str]
) -> set[str]:
    """The member item ids whose segment map is still the one their map was made
    from (``group.stored_versions`` vs the current segment-map header, the
    mapper's own comparison). Raises on a failed read."""
    ids = [str(i["item_id"]) for i in items if str(i["item_id"]) in live]
    if not ids:
        return set()
    headers = segment_headers(supabase, ids, user_id=user_id)
    try:
        lengths = files_store.content_chars_for_items(supabase, ids, user_id=user_id)
    except Exception as exc:  # noqa: BLE001 — the header's own length stands in
        logger.warning("angle_maps: length read failed (%s)", type(exc).__name__)
        lengths = {}
    stored = stored_versions(items)
    fresh = set()
    for item_id in ids:
        header = headers.get(item_id)
        if header is None:
            continue
        chars = lengths.get(item_id, header.get("content_chars"))
        try:
            cur = (
                int(chars if chars is not None else -1),
                str(header.get("prompt_version") or ""),
                str(header.get("paragraph_rule_version") or ""),
                _ts(header.get("created_at")),
            )
        except (TypeError, ValueError):
            continue
        if item_id in stored and _same_version(cur, stored[item_id]):
            fresh.add(item_id)
    return fresh


def _carried_map_files(
    supabase: SupabaseClient, user_id: str, conversation_id: str, own: set[str]
) -> dict[str, set[str]]:
    """``{map_id: {file_id, …}}`` — the maps this conversation's «الملفات» copies
    carry (owner 2026-11-27, ``agents/memory/angle_mapper/library.py``): maps of
    the caller's OTHER live items of the same files, minus the conversation's own
    maps (``own``), with the files that bring each one. Needs the files read switch
    (``file_id``) and ``LIBRARY_FILE_MAPS_*`` on for the caller. Fail-soft: ``{}``
    (the conversation's own maps still list)."""
    try:
        from agents.utils.angle_map_flags import library_file_maps_enabled

        if not library_file_maps_enabled(user_id):
            return {}
        cols = files_store.with_file_id("item_id", user_id=user_id, supabase=supabase)
        if "file_id" not in cols:
            return {}
        here = _data(
            supabase.table(ITEMS_TABLE)
            .select(cols)
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .eq("kind", "attachment")
            .is_("deleted_at", "null")
            .execute()
        )
        here_ids = {str(r["item_id"]) for r in here}
        files = list(dict.fromkeys(str(r["file_id"]) for r in here if r.get("file_id")))
        if not files:
            return {}
        file_of: dict[str, str] = {}
        for batch in _batches(files):
            for r in _data(
                supabase.table(ITEMS_TABLE)
                .select("item_id, file_id")
                .in_("file_id", batch)
                .eq("user_id", user_id)
                .is_("deleted_at", "null")
                .execute()
            ):
                if str(r["item_id"]) not in here_ids:
                    file_of[str(r["item_id"])] = str(r["file_id"])
        if not file_of:
            return {}
        out: dict[str, set[str]] = {}
        for link in _rows_in(supabase, MAP_ITEMS_TABLE, "map_id, item_id", "item_id", list(file_of)):
            mid = str(link["map_id"])
            if mid not in own:
                out.setdefault(mid, set()).add(file_of[str(link["item_id"])])
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_maps: carried maps read failed (%s)", type(exc).__name__)
        return {}


def list_angle_maps(
    supabase: SupabaseClient, user_id: str, conversation_id: Optional[str] = None
) -> list[dict]:
    """``AngleMapSummary`` rows of the caller's fresh maps, newest first (at most
    ``LIST_CAP``), optionally of one conversation. A failed read is a 500.

    For one conversation, the maps its «الملفات» copies carry are listed too —
    whole, even when they hold other files, the newest fresh one per file, and
    still listed when the conversation they were made in is deleted
    (:func:`_carried_map_files`)."""
    try:
        q = (
            supabase.table(MAPS_TABLE)
            .select("map_id, conversation_id, message_id, created_at")
            .eq("user_id", user_id)
        )
        if conversation_id:
            q = q.eq("conversation_id", conversation_id)
        heads = _data(q.order("created_at", desc=True).limit(LIST_CAP).execute())
        carried: dict[str, set[str]] = {}
        if conversation_id:
            carried = _carried_map_files(supabase, user_id, conversation_id, {str(h["map_id"]) for h in heads})
            if carried:
                more = _rows_in(
                    supabase, MAPS_TABLE, "map_id, conversation_id, message_id, created_at, user_id",
                    "map_id", list(carried),
                )
                heads = heads + [h for h in more if str(h.get("user_id") or "") == str(user_id)]
                heads.sort(key=lambda h: str(h.get("created_at") or ""), reverse=True)
                heads = heads[:LIST_CAP]
        if not heads:
            return []
        map_ids = [str(h["map_id"]) for h in heads]
        items = _rows_in(
            supabase, MAP_ITEMS_TABLE,
            "map_id, item_id, doc_index, content_chars, segment_prompt_version, paragraph_rule_version, segmented_at",
            "map_id", map_ids,
        )
        kinds = _rows_in(supabase, ANGLES_TABLE, "map_id, kind", "map_id", map_ids)
        member_ids = list(dict.fromkeys(str(i["item_id"]) for i in items))
        rows = _item_rows(supabase, user_id, member_ids)
        fresh = _fresh_members(supabase, user_id, items, set(rows))
    except Exception as exc:  # noqa: BLE001
        logger.exception("angle_maps: list failed (%s)", type(exc).__name__)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب الخيوط",
        )
    convs = _conversations(
        supabase, user_id, list(dict.fromkeys(str(h["conversation_id"]) for h in heads if h.get("conversation_id")))
    )

    members: dict[str, list[dict]] = {}
    for i in items:
        members.setdefault(str(i["map_id"]), []).append(i)
    counts: dict[str, dict[str, int]] = {}
    for a in kinds:
        k = a.get("kind") if a.get("kind") in _KINDS else "strengthen"
        c = counts.setdefault(str(a["map_id"]), {"strengthen": 0, "attack": 0, "answer": 0})
        c[k] += 1

    out = []
    files_shown: set[str] = set()
    for h in heads:
        mid = str(h["map_id"])
        mine = sorted(members.get(mid) or [], key=lambda i: _int_or_none(i.get("doc_index")) or 0)
        if not mine or any(str(i["item_id"]) not in fresh for i in mine):
            continue   # a member gone, changed or segmented again: stale
        conv = convs.get(str(h.get("conversation_id") or ""))
        if mid in carried:
            if carried[mid] <= files_shown:
                continue   # a newer fresh map already brings these files
            files_shown |= carried[mid]
        elif h.get("conversation_id") and (conv is None or conv["deleted"]):
            continue
        c = counts.get(mid) or {"strengthen": 0, "attack": 0, "answer": 0}
        out.append({
            "map_id": mid,
            "conversation_id": str(h["conversation_id"]) if h.get("conversation_id") else None,
            "conversation_title": conv["title"] if conv else None,
            "message_id": str(h["message_id"]) if h.get("message_id") else None,
            "created_at": str(h.get("created_at") or ""),
            "documents": [
                {
                    "index": _int_or_none(i.get("doc_index")) or n + 1,
                    "item_id": str(i["item_id"]),
                    "title": rows[str(i["item_id"])]["title"],
                    "doc_type": rows[str(i["item_id"])]["doc_type"],
                    "wi_seq": rows[str(i["item_id"])]["wi_seq"],
                }
                for n, i in enumerate(mine)
            ],
            "angle_count": sum(c.values()),
            "kinds": c,
        })
    return out


def get_angle_map(supabase: SupabaseClient, user_id: str, map_id: str) -> Optional[dict]:
    """One of the caller's maps in full (``AngleMap``) — ``None`` when it is not
    theirs, does not exist, or is no longer fresh (→ 404). A map that fails to
    build is a 500."""
    try:
        link = _data(
            supabase.table(MAP_ITEMS_TABLE)
            .select("item_id, doc_index")
            .eq("map_id", map_id)
            .order("doc_index")
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("angle_maps: map read failed for %s (%s)", map_id, type(exc).__name__)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء جلب الخيوط")
    if not link:
        return None
    stored = load_fresh_angle_map_for_item(supabase, str(link[0]["item_id"]))
    head = (stored or {}).get("map") or {}
    if str(head.get("map_id") or "") != map_id or str(head.get("user_id") or "") != str(user_id):
        return None
    try:
        return _map_out(supabase, user_id, stored)
    except Exception as exc:  # noqa: BLE001
        logger.exception("angle_maps: map %s failed to build (%s)", map_id, type(exc).__name__)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR, detail="حدث خطأ أثناء جلب الخيوط")


__all__ = ["angle_maps_ui_enabled", "get_angle_map", "list_angle_maps"]
