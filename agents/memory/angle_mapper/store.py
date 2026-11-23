"""Storage of angle maps (migration 228) — ONE map per message group (D17).

Three tables:

- ``attachment_angle_maps`` — one row per map (``map_id``): the group's message
  (``message_id``, UNIQUE, NULL for a file with no message link), conversation,
  user, versions, client, context, gaps, no_angle, unrelated_documents,
  ``left_out`` (the group's attachments left out after their text was read, with
  the versions they were judged on — ``group.py``), tokens, cost, duration;
- ``attachment_angle_map_items`` — its members: ``(map_id, doc_index)`` → item_id
  (UNIQUE: an item is in at most one map) + what the map was built from
  (``content_chars``, the segment map's prompt / paragraph-rule versions and
  ``created_at`` as ``segmented_at``);
- ``attachment_angles`` — one row per angle, ``seq`` 1-based, most decisive first;
  ``sections`` = refs (``"D1.S7"``, ``refs.py``); ``ocr_suspect`` (angles-v5).

A map is found FROM ANY MEMBER: item → its items row → the map (UNIQUE item_id,
one indexed read). The item is the CONVERSATION's item (``workspace_items``), not
its file: segment maps hang on the file (migration 223), but an angle map belongs
to one conversation's use of it — the same library file attached to two
conversations is in two groups, with two maps.

**Write = replace, published last.** (1) insert the new map row with
``message_id`` NULL, (2) its angles — a failure so far drops the new row and leaves
the group's old map untouched; (3) delete the group's previous maps (the
message's, and any map holding one of the members — their items and angles
cascade); (4) set ``message_id`` on the new row; (5) insert its items. Readers
reach a map only through its items, so a map is visible only once it is complete.
A failure after (3) drops the new row: the group then has no map until the next
run (never a half map). Two writers for one group at once cannot both win: the
second ``message_id`` update hits the UNIQUE constraint (or the second items
insert hits UNIQUE item_id, or its map was deleted by the other writer's step 3)
and that writer drops its own row and returns ``None``. A process killed between
(1) and (5) leaves an orphan map row with no items — invisible to every reader,
removed with its conversation / user.

Nothing here raises. Failures are logged with the exception CLASS NAME only
(never the message: PostgREST errors can echo row values — the user's text).

The client is ``service_role`` (bypasses RLS): every read is by an item id the
caller already owns. The الموكّل / الخصم / محايد labels are not stored (plan
§4.4): code computes them from the sections' owners (``labels.py``).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping, Sequence

from .refs import canonical_doc, canonical_ref

logger = logging.getLogger(__name__)

MAPS_TABLE = "attachment_angle_maps"
ITEMS_TABLE = "attachment_angle_map_items"
ANGLES_TABLE = "attachment_angles"

#: ``map_row`` keys written to ``attachment_angle_maps`` (besides map_id,
#: message_id, conversation_id, user_id).
MAP_COLUMNS: tuple[str, ...] = (
    "prompt_version",
    "model",
    "client",
    "context",
    "gaps",
    "no_angle",
    "unrelated_documents",
    "left_out",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cost_usd",
    "duration_ms",
)

#: Keys of one ``left_out`` entry (the item columns, minus doc_index, plus why).
LEFT_OUT_KEYS: tuple[str, ...] = (
    "item_id",
    "reason",
    "content_chars",
    "segment_prompt_version",
    "paragraph_rule_version",
    "segmented_at",
)

#: Item dict keys written to ``attachment_angle_map_items`` (besides map_id).
ITEM_COLUMNS: tuple[str, ...] = (
    "item_id",
    "doc_index",
    "content_chars",
    "segment_prompt_version",
    "paragraph_rule_version",
    "segmented_at",
)

#: Angle dict keys written to ``attachment_angles`` (besides map_id).
ANGLE_COLUMNS: tuple[str, ...] = (
    "seq",
    "for_side",
    "for_party",
    "kind",
    "title",
    "argument",
    "sections",
    "legal_question",
    "strength",
    "weakness",
    "ocr_suspect",
)

_INT_COLUMNS = ("input_tokens", "output_tokens", "reasoning_tokens", "duration_ms")
_TEXT_ANGLE_COLUMNS = ("for_side", "for_party", "kind", "title", "argument", "legal_question", "strength", "weakness")

#: PostgREST returns at most 1,000 rows per request; a map has ~3–12 angles.
_PAGE_ROWS = 1_000


def _data(resp: Any) -> list[dict]:
    return list(getattr(resp, "data", None) or [])


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _flag(v: Any) -> bool:
    """A boolean column's value: ``True`` / ``"true"`` / ``1`` → ``True``; anything
    else (``None``, ``"false"``, missing) → ``False``."""
    if isinstance(v, str):
        return v.strip().lower() in ("true", "t", "1", "yes")
    return bool(v) if isinstance(v, (bool, int)) else False


def _refs(v: Any) -> list[str]:
    """A list of refs as canonical strings (→ jsonb array); empties dropped."""
    out: list[str] = []
    for x in v or []:
        ref = canonical_ref(x)
        if ref:
            out.append(ref)
    return out


def _gap(g: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(g)
    out["sections"] = _refs(out.get("sections"))
    return out


def _no_angle(n: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(n)
    out["section"] = canonical_ref(out.get("section"))
    return out


def _doc_entry(e: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(e)
    out["document"] = canonical_doc(out.get("document"))
    return out


def _left_out_entry(e: Mapping[str, Any]) -> dict[str, Any]:
    """One ``left_out`` entry, JSON-ready (its keys only)."""
    out = {key: e.get(key) for key in LEFT_OUT_KEYS}
    out["item_id"] = str(out["item_id"] or "")
    out["reason"] = str(out["reason"] or "")
    out["content_chars"] = None if out["content_chars"] is None else _int(out["content_chars"])
    for key in ("segment_prompt_version", "paragraph_rule_version", "segmented_at"):
        out[key] = None if out[key] is None else str(out[key])
    return out


def _map_record(
    map_id: str,
    *,
    conversation_id: str | None,
    user_id: str,
    map_row: Mapping[str, Any],
) -> dict[str, Any]:
    """The full ``attachment_angle_maps`` row, JSON-ready, ``message_id`` NULL (set
    at publish). ``None`` counts become 0 (the columns are ``NOT NULL DEFAULT 0``
    and an explicit null would not take the default); ``cost_usd`` goes as a float.
    ``prompt_version`` is required: missing, the NOT NULL column refuses the row."""
    rec: dict[str, Any] = {
        "map_id": map_id,
        "message_id": None,
        "conversation_id": str(conversation_id) if conversation_id else None,
        "user_id": str(user_id),
    }
    for key in MAP_COLUMNS:
        rec[key] = map_row.get(key)
    for key in _INT_COLUMNS:
        rec[key] = _int(rec[key])
    rec["cost_usd"] = _float(rec["cost_usd"])
    client = dict(rec["client"] or {})
    client["by_document"] = [_doc_entry(e) for e in (client.get("by_document") or [])]
    rec["client"] = client
    rec["context"] = _refs(rec["context"])
    rec["gaps"] = [_gap(g) for g in (rec["gaps"] or [])]
    rec["no_angle"] = [_no_angle(n) for n in (rec["no_angle"] or [])]
    rec["unrelated_documents"] = [_doc_entry(u) for u in (rec["unrelated_documents"] or [])]
    rec["left_out"] = [_left_out_entry(e) for e in (rec["left_out"] or [])]
    return rec


def _item_records(map_id: str, items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Members as rows. Raises ``ValueError`` on a member without an item id, a
    doc_index outside 1..n or repeated, or an item listed twice."""
    rows: list[dict[str, Any]] = []
    for it in items:
        rec: dict[str, Any] = {"map_id": map_id}
        for key in ITEM_COLUMNS:
            rec[key] = it.get(key)
        rec["item_id"] = str(rec["item_id"] or "")
        rec["doc_index"] = _int(rec["doc_index"], 0)
        rec["content_chars"] = None if rec["content_chars"] is None else _int(rec["content_chars"])
        rows.append(rec)
    ids = [r["item_id"] for r in rows]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("member item ids")
    if sorted(r["doc_index"] for r in rows) != list(range(1, len(rows) + 1)):
        raise ValueError("doc_index must be 1..n")
    return rows


def _angle_records(map_id: str, angles: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, a in enumerate(angles, start=1):
        rec: dict[str, Any] = {"map_id": map_id}
        for key in ANGLE_COLUMNS:
            if key in a:
                rec[key] = a[key]
        rec["seq"] = _int(rec.get("seq"), i) or i
        for key in _TEXT_ANGLE_COLUMNS:
            if key in rec:
                rec[key] = "" if rec[key] is None else str(rec[key])
        rec["sections"] = _refs(rec.get("sections"))
        rec["ocr_suspect"] = _flag(rec.get("ocr_suspect"))  # NOT NULL: always written
        rows.append(rec)
    return rows


def _drop_map(supabase: Any, map_id: str) -> None:
    """Remove a map row (its items and angles cascade) after a failed write."""
    try:
        supabase.table(MAPS_TABLE).delete().eq("map_id", map_id).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: could not remove half-written map %s (%s)", map_id, type(exc).__name__)


def _old_map_ids(supabase: Any, message_id: str | None, item_ids: Sequence[str], keep: str) -> list[str]:
    """The group's previous maps: the message's, and any map holding a member.
    Raises on a failed read."""
    found: list[str] = []
    if message_id:
        found += [
            str(r["map_id"])
            for r in _data(supabase.table(MAPS_TABLE).select("map_id").eq("message_id", message_id).execute())
        ]
    if item_ids:
        found += [
            str(r["map_id"])
            for r in _data(supabase.table(ITEMS_TABLE).select("map_id").in_("item_id", list(item_ids)).execute())
        ]
    return [m for m in dict.fromkeys(found) if m and m != keep]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_angle_map(
    supabase: Any,
    *,
    message_id: str | None,
    conversation_id: str | None,
    user_id: str,
    items: list[dict],
    map_row: dict,
    angles: list[dict],
) -> str | None:
    """Replace the group's angle map. The new ``map_id`` when stored; ``None`` on
    any failure (logged by exception class name). Never raises.

    ``items`` — the members in D-order: ``item_id, doc_index (1..n),
    content_chars, segment_prompt_version, paragraph_rule_version, segmented_at``.
    ``map_row`` — ``prompt_version, model, client (dict, with by_document),
    context (refs), gaps (dicts, sections = refs), no_angle (dicts, section = a
    ref), unrelated_documents (dicts), left_out (dicts: ``LEFT_OUT_KEYS``),
    input_tokens, output_tokens, reasoning_tokens, cost_usd, duration_ms``.
    ``angles`` — dicts with ``seq``
    (1-based), ``for_side, for_party, kind, title, argument, sections (refs),
    legal_question, strength, weakness, ocr_suspect`` (bool, ``False`` when
    absent). Other keys are ignored.

    Steps (module docstring): new map row (message_id NULL) → angles → delete the
    group's previous maps → set message_id → items (publish).
    """
    if not user_id or not items:
        logger.warning("angle_mapper: write_angle_map without user_id / items")
        return None
    map_id = str(uuid.uuid4())
    try:
        map_rec = _map_record(map_id, conversation_id=conversation_id, user_id=user_id, map_row=map_row or {})
        item_rows = _item_records(map_id, items)
        angle_rows = _angle_records(map_id, angles or [])
    except Exception as exc:  # noqa: BLE001 — a malformed row must not raise
        logger.warning("angle_mapper: map for %s not storable (%s)", message_id or "-", type(exc).__name__)
        return None
    member_ids = [r["item_id"] for r in item_rows]
    msg = str(message_id) if message_id else None

    # 1–2. The new map and its angles, not yet reachable (no items, no message).
    try:
        supabase.table(MAPS_TABLE).insert(map_rec).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: map row not stored (%s)", type(exc).__name__)
        return None
    if angle_rows:
        try:
            supabase.table(ANGLES_TABLE).insert(angle_rows).execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("angle_mapper: angles not stored for map %s (%s)", map_id, type(exc).__name__)
            _drop_map(supabase, map_id)
            return None

    # 3. The group's previous maps out (their items and angles cascade).
    try:
        old = _old_map_ids(supabase, msg, member_ids, keep=map_id)
        if old:
            supabase.table(MAPS_TABLE).delete().in_("map_id", old).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: previous map not removed for %s (%s)", msg or member_ids[0], type(exc).__name__)
        _drop_map(supabase, map_id)
        return None

    # 4–5. Publish: the message, then the members.
    try:
        if msg:
            supabase.table(MAPS_TABLE).update({"message_id": msg}).eq("map_id", map_id).execute()
        supabase.table(ITEMS_TABLE).insert(item_rows).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: map %s not published (%s)", map_id, type(exc).__name__)
        _drop_map(supabase, map_id)
        return None
    return map_id


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def _normalise_map(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("map_id", "message_id", "conversation_id", "user_id"):
        if out.get(key) is not None:
            out[key] = str(out[key])
    client = dict(out.get("client") or {})
    client["by_document"] = list(client.get("by_document") or [])
    out["client"] = client
    out["context"] = _refs(out.get("context"))
    out["gaps"] = list(out.get("gaps") or [])
    out["no_angle"] = list(out.get("no_angle") or [])
    out["unrelated_documents"] = list(out.get("unrelated_documents") or [])
    out["left_out"] = list(out.get("left_out") or [])
    if "cost_usd" in out:
        out["cost_usd"] = _float(out.get("cost_usd"))  # numeric may come back as a string
    return out


def _normalise_item(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["map_id"] = str(out.get("map_id") or "")
    out["item_id"] = str(out.get("item_id") or "")
    out["doc_index"] = _int(out.get("doc_index"))
    return out


def _normalise_angle(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["map_id"] = str(out.get("map_id") or "")
    out["seq"] = _int(out.get("seq"))
    out["sections"] = _refs(out.get("sections"))
    out["ocr_suspect"] = _flag(out.get("ocr_suspect"))
    return out


def _load_map(supabase: Any, map_id: str) -> dict:
    """One map with its members and angles. Raises on a failed read."""
    rows = _data(supabase.table(MAPS_TABLE).select("*").eq("map_id", map_id).limit(1).execute())
    if not rows:
        return {}
    items = _data(supabase.table(ITEMS_TABLE).select("*").eq("map_id", map_id).order("doc_index").execute())
    angles: list[dict] = []
    offset = 0
    while True:
        batch = _data(
            supabase.table(ANGLES_TABLE)
            .select("*")
            .eq("map_id", map_id)
            .order("seq")
            .range(offset, offset + _PAGE_ROWS - 1)
            .execute()
        )
        angles.extend(batch)
        if len(batch) < _PAGE_ROWS:
            break
        offset += _PAGE_ROWS
    return {
        "map": _normalise_map(rows[0]),
        "items": sorted((_normalise_item(i) for i in items), key=lambda i: i["doc_index"]),
        "angles": sorted((_normalise_angle(a) for a in angles), key=lambda a: a["seq"]),
    }


def load_angle_map_for_item(supabase: Any, item_id: str) -> dict | None:
    """``{"map": <attachment_angle_maps row>, "items": [<members by doc_index>],
    "angles": [<angles by seq>]}`` of the map ``item_id`` is a member of, or
    ``None`` (no map, or a failed read). Never raises.

    Rows come back as stored — whether the map still fits today's members is
    :func:`load_fresh_angle_map_for_item`'s question. A map whose files truly
    support no angle comes back with ``angles == []``.
    """
    item_id = str(item_id or "")
    if not item_id:
        return None
    try:
        link = _data(supabase.table(ITEMS_TABLE).select("map_id").eq("item_id", item_id).limit(1).execute())
        if not link:
            return None
        return _load_map(supabase, str(link[0]["map_id"])) or None
    except Exception as exc:  # noqa: BLE001 — no map is a valid state for every reader
        logger.warning("angle_mapper: load_angle_map_for_item failed for %s (%s)", item_id, type(exc).__name__)
        return None


def load_angle_map_for_items(supabase: Any, item_ids: Sequence[str]) -> dict | None:
    """The ONE map holding any of ``item_ids`` (a group's attachments), in
    :func:`load_angle_map_for_item`'s shape — ``None`` when none of them is in a
    map, when they are spread over several maps (not one map of this group), or on
    a failed read. Never raises. The runner's freshness check: the group's first
    attachment may be one the stored map left out, so no single item decides."""
    ids = list(dict.fromkeys(str(i) for i in item_ids or [] if i))
    if not ids:
        return None
    try:
        links = _data(supabase.table(ITEMS_TABLE).select("map_id").in_("item_id", ids).execute())
        map_ids = list(dict.fromkeys(str(r["map_id"]) for r in links if r.get("map_id")))
        if len(map_ids) != 1:
            return None
        return _load_map(supabase, map_ids[0]) or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: load_angle_map_for_items failed (%s)", type(exc).__name__)
        return None


def load_fresh_angle_map_for_item(supabase: Any, item_id: str) -> dict | None:
    """The stored map of ``item_id``'s group (``load_angle_map_for_item`` shape) when
    it is still FRESH, or ``None`` (stale, no map, or any failure) — the helper
    frame's ``load`` (``LoadFn`` shape, ``agents/helpers/spec.py``): ``None`` lets it
    run the map again.

    Fresh = ``group.stored_fits``: today's header-usable attachments of the group
    are exactly the map's members (same ``content_chars``, segment prompt version,
    paragraph rule and segment map ``segmented_at``) plus the attachments the map
    LEFT OUT after reading their text, at the versions they were left out at. Read
    from HEADERS only (``group.load_state``): no text, no segments, no paragraphs —
    the runner's own pending / fresh check is the same one, so the two always
    agree. A member still being segmented does not count yet; when its map lands
    the stored map becomes stale. An attachment the map left out is in no map
    itself (``None`` for it); its own run finds the group fresh."""
    try:
        stored = load_angle_map_for_item(supabase, item_id)
        if not stored or not stored.get("items"):
            return None
        from .group import group_item_ids, load_state, stored_fits

        message_id, ids = group_item_ids(supabase, str(item_id))
        state = load_state(
            supabase, message_id, ids, user_id=str(stored["map"].get("user_id") or ""), check_pending=False,
        )
        return stored if stored_fits(state.versions(), stored) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: freshness check failed for %s (%s)", item_id, type(exc).__name__)
        return None


__all__ = [
    "ANGLES_TABLE",
    "ANGLE_COLUMNS",
    "ITEMS_TABLE",
    "ITEM_COLUMNS",
    "LEFT_OUT_KEYS",
    "MAPS_TABLE",
    "MAP_COLUMNS",
    "load_angle_map_for_item",
    "load_angle_map_for_items",
    "load_fresh_angle_map_for_item",
    "write_angle_map",
]
