"""«الأوجه» in an opened file — ``GET /files/{item_id}/angle-maps``.

Plan: ``.claude/plans/angle_map_ui.md`` §4. Read-only. The angle map belongs to
the MESSAGE the files were sent in (``attachment_angle_maps``, one map per
message group, members by conversation item), so one file can sit in several
maps — one per conversation it was sent in. This module answers, for one file,
every map that holds it and is still FRESH, in the shape the web's «الأوجه» tab
reads (``frontend/lib/contracts/angles.ts``):

- the file is resolved to the caller's conversation items of that file (the
  pointer rows of its ``file_id``; just the item when it links no file);
- each item's map comes from the mapper's own store with its own freshness check
  (``load_fresh_angle_map_for_item``): a map whose members changed or were
  segmented again is not shown — its section numbers may no longer match;
- sections come from the segmenter's own loader (by file id), the الموكّل / الخصم
  / محايد labels from the mapper's own ``labels`` (as the preview page computes
  them); only the sections an angle or a gap rests on are sent, with their text.

Behind ``ANGLE_MAP_UI_ENABLED`` / ``ANGLE_MAP_UI_USERS`` (on top of «الملفات»'s
own switch). A map exists only where the mapper's switch (``ANGLE_MAP_ENABLED``)
built one. Nothing here writes.
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
from agents.memory.angle_mapper.store import load_fresh_angle_map_for_item
from agents.memory.attachment_segmenter.store import load_segment_maps
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services import files_service
from shared.config import get_settings
from shared.files import flags as files_flags
from shared.files import store as files_store

logger = logging.getLogger(__name__)

ITEMS_TABLE = "workspace_items"
CONVERSATIONS_TABLE = "conversations"

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


def _pointer_ids(supabase: SupabaseClient, user_id: str, item_id: str) -> Optional[list[str]]:
    """The caller's live conversation items of the file ``item_id`` names, oldest
    first — ``None`` when it names none of the caller's files. ``item_id`` is a
    conversation item (the pane, the page) or, with the files read switch on, a
    file id (a library row whose file has no live item answers with it). Raises
    on a failed read."""
    row = files_service._first(
        supabase.table(ITEMS_TABLE)
        .select(files_store.with_file_id("item_id", user_id=user_id, supabase=supabase))
        .eq("item_id", item_id)
        .eq("user_id", user_id)
        .eq("kind", "attachment")
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    if row is not None and not row.get("file_id"):
        return [str(row["item_id"])]
    if row is None and not files_flags.files_read_enabled(user_id, supabase=supabase):
        return None
    file_id = str(row["file_id"]) if row is not None else item_id
    rows = _data(
        supabase.table(ITEMS_TABLE)
        .select("item_id, created_at")
        .eq("file_id", file_id)
        .eq("user_id", user_id)
        .eq("kind", "attachment")
        .is_("deleted_at", "null")
        .execute()
    )
    ids = [str(r["item_id"]) for r in sorted(rows, key=lambda r: str(r.get("created_at") or ""))]
    if row is not None and str(row["item_id"]) not in ids:
        ids.insert(0, str(row["item_id"]))
    return ids or None


def _item_rows(supabase: SupabaseClient, user_id: str, item_ids: list[str]) -> dict[str, dict]:
    """``{item_id: {title, wi_seq, doc_type}}`` of the caller's items — the file's
    title and type through ``shared.files``. Fail-soft: ``{}``."""
    if not item_ids:
        return {}
    try:
        rows = _data(
            supabase.table(ITEMS_TABLE)
            .select(files_store.with_file_id("item_id, title, wi_seq, metadata", user_id=user_id, supabase=supabase))
            .in_("item_id", item_ids)
            .eq("user_id", user_id)
            .execute()
        )
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

    index = section_index(members, load_segment_maps(supabase, member_ids, user_id=user_id))

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
            "content_md": str(s.get("content_md") or ""),
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


def get_file_angle_maps(supabase: SupabaseClient, user_id: str, item_id: str) -> Optional[list[dict]]:
    """Every fresh angle map holding the caller's file ``item_id``, newest first
    (``FileAngleMapsResponse.maps``) — ``None`` when ``item_id`` is not one of the
    caller's files (→ 404). ``[]`` = the file is in no fresh map. A map that
    fails to build is left out (logged); a failed file read is a 500."""
    try:
        pointers = _pointer_ids(supabase, user_id, item_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("angle_maps: file read failed for %s (%s)", item_id, type(exc).__name__)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب أوجه الملف",
        )
    if pointers is None:
        return None

    maps: list[dict] = []
    seen: set[str] = set()
    for pointer in pointers:
        stored = load_fresh_angle_map_for_item(supabase, pointer)
        if not stored:
            continue
        head = stored.get("map") or {}
        map_id = str(head.get("map_id") or "")
        if not map_id or map_id in seen or str(head.get("user_id") or "") != str(user_id):
            continue
        seen.add(map_id)
        try:
            maps.append(_map_out(supabase, user_id, stored))
        except Exception as exc:  # noqa: BLE001
            logger.warning("angle_maps: map %s left out (%s)", map_id, type(exc).__name__)
    maps.sort(key=lambda m: m["created_at"], reverse=True)
    return maps


__all__ = ["angle_maps_ui_enabled", "get_file_angle_maps"]
