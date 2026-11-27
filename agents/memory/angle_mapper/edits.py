"""User-asked edits on a stored angle map («الخيوط») — IN PLACE, each one logged.

Plan: ``.claude/plans/attachment_agent.md`` §3.2, D7 / D9 (the user names the
change → the agent makes it, no re-run of the mapper), §7 option (a): a map is
edited where it is stored, so every conversation that reads it sees the edit.

Safe in place: a map's freshness reads only its member rows and ``left_out``
(``group.stored_fits``), never its angles, client, context, gaps, no_angle or
unrelated documents — an edit never makes the map stale.

Every edit writes one ``attachment_angle_map_edits`` row (migration 234: no FK to
the map, so the log survives a re-analysis that replaces it) with what it changed
(``before`` / ``after``) and the user's words that asked for it (``evidence``).
Order: write the change, then the log row; a failed log write rolls the change
back, so an edit is never unrecorded. :func:`undo_edit` re-applies ``before``.

Checks before any write — a refused edit changes nothing and says why in Arabic
(the router shows it to the user; reasons never quote the user's text):

- the map exists and belongs to ``user_id`` (the client is service_role);
- only the editable fields (:data:`ANGLE_FIELDS`, :data:`GAP_FIELDS`,
  :data:`CLIENT_FIELDS`);
- ``kind`` / ``strength`` / gap kind / side / confidence among the values the
  table's CHECKs and the mapper's models allow;
- section refs canonicalised to ``"D1.S7"`` (any accepted spelling; a bare
  ``S7`` only in a one-document map) and present in the map's sections today
  (``labels.section_index`` over ``load_segment_maps`` of its members); an angle
  keeps at least one section;
- free text decoded (:func:`shared.attachments.text_corrections.decode_for_store`)
  — refused when a masked fake would be stored;
- ``evidence`` not empty.

Angle ``seq`` values may have gaps after a removal (readers sort by ``seq`` and
name angles ``A{seq}``); an added angle takes a ``seq`` never used in this map, so
``A3`` keeps naming one thread for the user.

Nothing here raises. Sync supabase client (callers run in a thread).
"""
from __future__ import annotations

import copy
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .models import CLIENT_SIDES, CONFIDENCES, GAP_KINDS, KINDS, STRENGTHS
from .refs import canonical_doc, format_doc, parse_ref, qualify
from .store import ANGLE_COLUMNS, ANGLES_TABLE, MAP_COLUMNS, MAPS_TABLE, _load_map, write_angle_map

logger = logging.getLogger(__name__)

EDITS_TABLE = "attachment_angle_map_edits"

#: ``attachment_angle_map_edits.kind`` (the migration's CHECK).
EDIT_KINDS: tuple[str, ...] = (
    "edit_angle", "add_angle", "remove_angle",
    "add_gap", "edit_gap", "remove_gap",
    "edit_client", "edit_context", "edit_no_angle", "edit_unrelated",
    "snapshot", "undo",
)

#: Fields of an angle the user may change (``attachment_angles`` columns).
ANGLE_FIELDS: tuple[str, ...] = (
    "for_side", "for_party", "kind", "title", "argument", "sections",
    "legal_question", "strength", "weakness", "ocr_suspect",
)
#: Fields of a gap.
GAP_FIELDS: tuple[str, ...] = ("kind", "issue", "sections", "ocr_suspect")
#: Fields of the client line.
CLIENT_FIELDS: tuple[str, ...] = ("side", "party", "basis", "confidence", "by_document")
#: Instead of ``sections``: add to / remove from the current refs.
SECTION_DELTAS: tuple[str, ...] = ("add_sections", "remove_sections")

#: «بحث شامل» needs this many angles (``extended/eligibility.py``).
MIN_ANGLES_FOR_EXTENDED = 2
#: Longest free text accepted in one field.
MAX_TEXT_CHARS = 4_000

_ANGLE_TEXT = ("for_party", "title", "argument", "legal_question", "weakness")
_KIND_ALIASES = {"strengthening": "strengthen", "attacking": "attack", "answering": "answer"}
_SIDE_ALIASES = {
    "plaintiff": "claimant", "defendant": "respondent", "accused": "respondent",
    "public_prosecution": "prosecution", "prosecutor": "prosecution",
}
_FIELD_NAME = re.compile(r"^[a-z_]{1,40}$")
_ANGLE_TARGET = re.compile(r"^A(\d+)$")
_GAP_TARGET = re.compile(r"^gap (\d+)$")

# ── reasons (Arabic; shown to the user by the router) ──────────────────────────
R_NO_MAP = "لم يُعثر على خريطة الخيوط هذه؛ ربما أعيد تحليل الملف بعد ذلك."
R_READ = "تعذّرت قراءة الخيوط الآن؛ حاول بعد قليل."
R_WRITE = "تعذّر حفظ التعديل الآن؛ لم يتغير شيء."
R_NO_EVIDENCE = "لا يوجد في رسالة المستخدم ما يطلب هذا التعديل."
R_NO_CHANGE = "لا يوجد تغيير: القيمة المطلوبة هي القائمة الآن."
R_NO_ANGLE = "لا يوجد خيط بهذا الرقم في الخريطة."
R_NO_GAP = "لا توجد ثغرة بهذا الرقم في الخريطة."
R_NO_FIELDS = "لم يُحدَّد ما يُعدَّل."
R_FAKE = "في النص رقم أو بريد مقنّع لا يمكن استرجاع أصله؛ اكتبه كما ورد في الملف."
R_TEXT_LONG = f"النص أطول من {MAX_TEXT_CHARS} حرف."
R_TITLE = "عنوان الخيط لا يكون فارغاً."
R_ISSUE = "وصف الثغرة لا يكون فارغاً."
R_KIND = "نوع الخيط غير صالح؛ المسموح: تقوية (strengthen) أو هجوم (attack) أو رد (answer)."
R_STRENGTH = "قوة الخيط غير صالحة؛ المسموح: قوي (strong) أو متوسط (medium) أو ضعيف (weak)."
R_GAP_KIND = "نوع الثغرة غير صالح؛ المسموح: نقص (missing) أو تعارض (contradiction) أو بلا رد (unanswered)."
R_SIDE = "صفة الطرف غير صالحة؛ المسموح: " + "، ".join(CLIENT_SIDES) + "."
R_CONFIDENCE = "درجة التأكد غير صالحة؛ المسموح: clear أو likely أو unclear."
R_BOOL = "قيمة «قد تكون القراءة خاطئة» تكون نعم أو لا."
R_SECTIONS_EMPTY = "الخيط يحتاج قسماً واحداً على الأقل من الملف."
R_SECTIONS_BOTH = "إما قائمة أقسام كاملة وإما إضافة وحذف، لا الاثنان معاً."
R_NOT_IN_ANGLE = "القسم المطلوب حذفه ليس في هذا الخيط."
R_NOT_IN_CONTEXT = "القسم المطلوب حذفه ليس في أقسام السياق."
R_DOCUMENT = "رقم المستند غير موجود في هذه الخريطة."
R_UNDO_NONE = "لم يُعثر على هذا التعديل."
R_UNDO_DONE = "سبق التراجع عن هذا التعديل."
R_UNDO_UNDO = "لا يُتراجع عن تراجع؛ اطلب التعديل من جديد."
R_UNDO_CHANGED = "تغيّر هذا الجزء من الخريطة بعد التعديل، فلا يمكن التراجع عنه تلقائياً."
R_UNDO_REANALYZED = "أعيد تحليل الخيوط بعد هذا التعديل، فلا يمكن التراجع عنه."
R_UNDO_BUSY = "تحليل الخيوط جارٍ الآن لهذه الملفات؛ اطلب التراجع بعد أن ينتهي."
R_UNDO_NOT_REPLACED = (
    "لم تكتمل إعادة التحليل، فالخيوط الحالية هي نفسها التي كانت قبلها؛ لا يوجد ما يُتراجع عنه."
)
W_BELOW_TWO = "بقي في الخريطة أقل من خيطين؛ «بحث شامل» يحتاج خيطين على الأقل."


@dataclass
class EditResult:
    """What an edit did. ``reason`` (Arabic) is set when ``ok`` is false;
    ``warning`` (Arabic) may be set when it is true."""

    ok: bool
    reason: str = ""
    edit_id: str | None = None
    kind: str = ""
    #: What was edited: ``"A3"``, ``"gap 2"``, ``"client"``, ``"context"``…
    target: str = ""
    before: Any = None
    after: Any = None
    #: ``add_angle`` / an undone ``remove_angle``: the angle's seq.
    seq: int | None = None
    #: Snapshot restore: the new map's id (the old one is replaced).
    map_id: str | None = None
    #: After ``remove_angle``: angles left, and whether «بحث شامل» lost its 2.
    angles_left: int | None = None
    below_two: bool = False
    warning: str = ""


def _fail(reason: str, kind: str = "", target: str = "") -> EditResult:
    return EditResult(ok=False, reason=reason, kind=kind, target=target)


class _Refused(Exception):
    """A validation failure carrying its Arabic reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _data(resp: Any) -> list[dict]:
    data = getattr(resp, "data", None)
    if isinstance(data, dict):
        return [data]
    return [r for r in (data or []) if isinstance(r, dict)]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(v: Any) -> str:
    return str(v or "").strip().lower().replace("-", "_").replace(" ", "_")


def _kind(v: Any) -> str:
    s = _key(v)
    s = _KIND_ALIASES.get(s, s)
    if s not in KINDS:
        raise _Refused(R_KIND)
    return s


def _strength(v: Any) -> str:
    s = _key(v)
    if s not in STRENGTHS:
        raise _Refused(R_STRENGTH)
    return s


def _side(v: Any) -> str:
    s = _key(v)
    s = _SIDE_ALIASES.get(s, s)
    if s not in CLIENT_SIDES:
        raise _Refused(R_SIDE)
    return s


def _gap_kind(v: Any) -> str:
    s = _key(v)
    if s not in GAP_KINDS:
        raise _Refused(R_GAP_KIND)
    return s


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    raise _Refused(R_BOOL)


def _decode(text: Any, source: str) -> str:
    """Free text to store: decoded, real. Refused when a fake would remain."""
    from shared.attachments.text_corrections import decode_for_store

    s = "" if text is None else str(text)
    if len(s) > MAX_TEXT_CHARS:
        raise _Refused(R_TEXT_LONG)
    s = s.strip()
    if not s:
        return s
    out = decode_for_store(s, source=source)
    if out is None:
        raise _Refused(R_FAKE)
    return out


def _evidence(text: Any) -> str:
    """The user's words, decoded best-effort (a log column: never refused)."""
    s = str(text or "").strip()
    if not s:
        return s
    try:
        from backend.app.services.masking_service import active_codec

        codec = active_codec()
        return codec.decode(s).text if codec is not None else s
    except Exception:  # noqa: BLE001
        return s


def _bad_fields(fields: Mapping[str, Any], allowed: Iterable[str]) -> str | None:
    unknown = [k for k in fields if k not in set(allowed)]
    if not unknown:
        return None
    names = "، ".join(k for k in unknown if isinstance(k, str) and _FIELD_NAME.match(k))
    return "حقل لا يُعدَّل هنا" + (f": {names}." if names else ".")


def _angle_out(a: Mapping[str, Any]) -> dict[str, Any]:
    """An angle row as stored, without ``map_id`` (JSON-ready)."""
    return {k: copy.deepcopy(a.get(k)) for k in ANGLE_COLUMNS if k in a}


def _json(v: Any) -> Any:
    return copy.deepcopy(v)


# ---------------------------------------------------------------------------
# The map being edited
# ---------------------------------------------------------------------------


@dataclass
class _Ctx:
    map_id: str
    map: dict
    items: list[dict]
    angles: list[dict]
    user_id: str
    _refs: frozenset[str] | None = field(default=None, repr=False)

    @property
    def item_ids(self) -> list[str]:
        return [str(i.get("item_id") or "") for i in self.items if i.get("item_id")]

    @property
    def docs(self) -> frozenset[str]:
        return frozenset(format_doc(int(i["doc_index"])) for i in self.items if i.get("doc_index"))

    def angle(self, seq: int) -> dict | None:
        return next((a for a in self.angles if int(a.get("seq") or 0) == int(seq)), None)


def _load(supabase: Any, map_id: str, user_id: str) -> tuple[_Ctx | None, str]:
    if not map_id or not user_id:
        return None, R_NO_MAP
    try:
        stored = _load_map(supabase, str(map_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: map %s not read for an edit (%s)", map_id, type(exc).__name__)
        return None, R_READ
    if not stored or str(stored["map"].get("user_id") or "") != str(user_id):
        return None, R_NO_MAP
    return _Ctx(str(map_id), stored["map"], stored["items"], stored["angles"], str(user_id)), ""


def _valid_refs(supabase: Any, ctx: _Ctx) -> frozenset[str]:
    """The map's section refs today (its members' fresh segment maps)."""
    if ctx._refs is None:
        from agents.memory.attachment_segmenter.store import load_segment_maps

        from .labels import section_index

        maps = load_segment_maps(supabase, ctx.item_ids, user_id=ctx.user_id or None)
        ctx._refs = frozenset(section_index(ctx.items, maps))
    return ctx._refs


def _refs(supabase: Any, ctx: _Ctx, values: Any) -> list[str]:
    """Canonical refs (order kept, repeats dropped), each one a section of the map
    today. Refused on any other."""
    if values is None:
        return []
    if isinstance(values, (str, int)):
        values = [values]
    if not isinstance(values, (list, tuple)):
        raise _Refused("قائمة الأقسام غير صالحة.")
    single = len(ctx.items) == 1
    valid = _valid_refs(supabase, ctx)
    out: list[str] = []
    bad: list[str] = []
    for v in values:
        ref = qualify(v, single)
        if ref in valid:
            if ref not in out:
                out.append(ref)
        else:
            bad.append(ref)
    if bad:
        shown = [b for b in bad if parse_ref(b) is not None]
        raise _Refused(
            "قسم غير موجود في ملفات هذه الخريطة" + (f": {'، '.join(shown)}." if shown else ".")
        )
    return out


def _canon(ctx: _Ctx, values: Any) -> list[str]:
    """Canonical refs without the existence check (for removals)."""
    if values is None:
        return []
    if isinstance(values, (str, int)):
        values = [values]
    single = len(ctx.items) == 1
    return list(dict.fromkeys(qualify(v, single) for v in values or []))


def _sections_after(supabase: Any, ctx: _Ctx, current: Sequence[str], fields: Mapping[str, Any]) -> list[str] | None:
    """The refs after ``sections`` / ``add_sections`` / ``remove_sections``, or
    ``None`` when none of them is given."""
    has_full = "sections" in fields
    has_delta = any(k in fields for k in SECTION_DELTAS)
    if has_full and has_delta:
        raise _Refused(R_SECTIONS_BOTH)
    if has_full:
        return _refs(supabase, ctx, fields["sections"])
    if not has_delta:
        return None
    out = list(current or [])
    for ref in _canon(ctx, fields.get("remove_sections")):
        if ref not in out:
            raise _Refused(R_NOT_IN_ANGLE)
        out.remove(ref)
    for ref in _refs(supabase, ctx, fields.get("add_sections")):
        if ref not in out:
            out.append(ref)
    return out


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------


def _write_log(
    supabase: Any,
    *,
    map_id: str,
    item_ids: Sequence[str],
    kind: str,
    target: str,
    before: Any,
    after: Any,
    evidence: str,
    user_id: str,
    conversation_id: str | None,
    message_id: str | None,
) -> str | None:
    row = {
        "id": str(uuid.uuid4()),
        "map_id": str(map_id),
        "conversation_id": str(conversation_id) if conversation_id else None,
        "user_id": str(user_id),
        "item_ids": [str(i) for i in item_ids],
        "kind": kind,
        "target": target,
        "before": _json(before),
        "after": _json(after),
        "evidence": evidence,
        "message_id": str(message_id) if message_id else None,
    }
    try:
        supabase.table(EDITS_TABLE).insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: edit log not written for map %s (%s)", map_id, type(exc).__name__)
        return None
    return row["id"]


def load_edit_log(
    supabase: Any,
    map_id: str | None,
    *,
    conversation_id: str | None = None,
    include_undone: bool = True,
) -> list[dict]:
    """The edit rows of a map (or, with ``map_id=None``, of a conversation), oldest
    first. ``[]`` on a failed read (logged). Never raises."""
    if not map_id and not conversation_id:
        return []
    try:
        q = supabase.table(EDITS_TABLE).select("*")
        if map_id:
            q = q.eq("map_id", str(map_id))
        if conversation_id:
            q = q.eq("conversation_id", str(conversation_id))
        if not include_undone:
            q = q.is_("undone_at", "null")
        return _data(q.order("created_at").execute())
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: edit log not read (%s)", type(exc).__name__)
        return []


def _commit(
    supabase: Any,
    ctx: _Ctx,
    *,
    kind: str,
    target: str,
    before: Any,
    after: Any,
    evidence: str,
    conversation_id: str | None,
    message_id: str | None,
    apply: Any,
    rollback: Any,
) -> EditResult:
    """``apply()`` then the log row; a failed log rolls the change back."""
    try:
        if apply() is False:
            return _fail(R_WRITE, kind, target)
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: %s not written on map %s (%s)", kind, ctx.map_id, type(exc).__name__)
        return _fail(R_WRITE, kind, target)
    edit_id = _write_log(
        supabase, map_id=ctx.map_id, item_ids=ctx.item_ids, kind=kind, target=target,
        before=before, after=after, evidence=evidence, user_id=ctx.user_id,
        conversation_id=conversation_id, message_id=message_id,
    )
    if edit_id is None:
        try:
            rollback()
        except Exception as exc:  # noqa: BLE001
            logger.warning("angle_mapper: %s on map %s not rolled back (%s)", kind, ctx.map_id, type(exc).__name__)
        return _fail(R_WRITE, kind, target)
    return EditResult(ok=True, edit_id=edit_id, kind=kind, target=target, before=_json(before), after=_json(after))


def _update_angle(supabase: Any, map_id: str, seq: int, values: Mapping[str, Any]) -> bool:
    resp = supabase.table(ANGLES_TABLE).update(dict(values)).eq("map_id", map_id).eq("seq", int(seq)).execute()
    return bool(_data(resp))


def _insert_angle(supabase: Any, map_id: str, angle: Mapping[str, Any]) -> bool:
    supabase.table(ANGLES_TABLE).insert({**_angle_out(angle), "map_id": map_id}).execute()
    return True


def _delete_angle(supabase: Any, map_id: str, seq: int) -> bool:
    supabase.table(ANGLES_TABLE).delete().eq("map_id", map_id).eq("seq", int(seq)).execute()
    return True


def _update_map(supabase: Any, map_id: str, values: Mapping[str, Any]) -> bool:
    resp = supabase.table(MAPS_TABLE).update(_json(dict(values))).eq("map_id", map_id).execute()
    return bool(_data(resp))


def _used_seqs(supabase: Any, ctx: _Ctx) -> set[int]:
    """Every angle seq this map has had (current + named in its log)."""
    used = {int(a.get("seq") or 0) for a in ctx.angles}
    for row in load_edit_log(supabase, ctx.map_id):
        m = _ANGLE_TARGET.match(str(row.get("target") or ""))
        if m:
            used.add(int(m.group(1)))
    return used


def _start(
    supabase: Any, map_id: str, user_id: str, evidence: Any, kind: str, target: str = "",
) -> tuple[_Ctx | None, str, EditResult | None]:
    ev = _evidence(evidence)
    if not ev:
        return None, "", _fail(R_NO_EVIDENCE, kind, target)
    ctx, reason = _load(supabase, map_id, user_id)
    if ctx is None:
        return None, "", _fail(reason, kind, target)
    return ctx, ev, None


# ---------------------------------------------------------------------------
# Angles
# ---------------------------------------------------------------------------


def _angle_values(supabase: Any, ctx: _Ctx, fields: Mapping[str, Any], current: Mapping[str, Any], source: str) -> dict:
    """The validated new values of the fields given (``sections`` resolved)."""
    new: dict[str, Any] = {}
    for k, v in fields.items():
        if k == "kind":
            new[k] = _kind(v)
        elif k == "strength":
            new[k] = _strength(v)
        elif k == "for_side":
            new[k] = _side(v)
        elif k == "ocr_suspect":
            new[k] = _bool(v)
        elif k in _ANGLE_TEXT:
            new[k] = _decode(v, source)
            if k == "title" and not new[k]:
                raise _Refused(R_TITLE)
    sections = _sections_after(supabase, ctx, list(current.get("sections") or []), fields)
    if sections is not None:
        if not sections:
            raise _Refused(R_SECTIONS_EMPTY)
        new["sections"] = sections
    return new


def edit_angle(
    supabase: Any,
    map_id: str,
    seq: int,
    fields: Mapping[str, Any],
    *,
    evidence: str,
    user_id: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
    source: str = "",
) -> EditResult:
    """Change fields of angle ``A{seq}``: any of :data:`ANGLE_FIELDS`, or
    ``add_sections`` / ``remove_sections`` instead of ``sections``. Logs only the
    fields that changed (``before`` / ``after``: ``{field: value}``)."""
    kind, target = "edit_angle", f"A{seq}"
    if not isinstance(fields, Mapping) or not fields:
        return _fail(R_NO_FIELDS, kind, target)
    bad = _bad_fields(fields, ANGLE_FIELDS + SECTION_DELTAS)
    if bad:
        return _fail(bad, kind, target)
    ctx, ev, err = _start(supabase, map_id, user_id, evidence, kind, target)
    if err:
        return err
    angle = ctx.angle(seq)
    if angle is None:
        return _fail(R_NO_ANGLE, kind, target)
    try:
        new = _angle_values(supabase, ctx, fields, angle, source)
    except _Refused as r:
        return _fail(r.reason, kind, target)
    before = {k: angle.get(k) for k, v in new.items() if angle.get(k) != v}
    after = {k: new[k] for k in before}
    if not after:
        return _fail(R_NO_CHANGE, kind, target)
    return _commit(
        supabase, ctx, kind=kind, target=target, before=before, after=after, evidence=ev,
        conversation_id=conversation_id, message_id=message_id,
        apply=lambda: _update_angle(supabase, ctx.map_id, seq, after),
        rollback=lambda: _update_angle(supabase, ctx.map_id, seq, before),
    )


def add_angle(
    supabase: Any,
    map_id: str,
    fields: Mapping[str, Any],
    *,
    evidence: str,
    user_id: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
    source: str = "",
) -> EditResult:
    """Add a thread. Needs ``title``, ``kind`` and ``sections`` (≥ 1); the rest of
    :data:`ANGLE_FIELDS` optional — ``for_side`` / ``for_party`` default to the
    client's, ``strength`` to ``medium``. Its ``seq`` (``result.seq``) is one never
    used in this map."""
    kind = "add_angle"
    if not isinstance(fields, Mapping) or not fields:
        return _fail(R_NO_FIELDS, kind)
    bad = _bad_fields(fields, ANGLE_FIELDS)
    if bad:
        return _fail(bad, kind)
    ctx, ev, err = _start(supabase, map_id, user_id, evidence, kind)
    if err:
        return err
    try:
        if not str(fields.get("title") or "").strip():
            raise _Refused(R_TITLE)
        if "kind" not in fields:
            raise _Refused(R_KIND)
        if not fields.get("sections"):
            raise _Refused(R_SECTIONS_EMPTY)
        new = _angle_values(supabase, ctx, fields, {}, source)
    except _Refused as r:
        return _fail(r.reason, kind)
    client = ctx.map.get("client") or {}
    client_side = str(client.get("side") or "")
    seq = max(_used_seqs(supabase, ctx) | {0}) + 1
    angle = {
        "seq": seq,
        "for_side": client_side if client_side in CLIENT_SIDES else "unknown",
        "for_party": str(client.get("party") or ""),
        "kind": new["kind"],
        "title": "",
        "argument": "",
        "sections": [],
        "legal_question": "",
        "strength": "medium",
        "weakness": "",
        "ocr_suspect": False,
        **new,
    }
    target = f"A{seq}"
    res = _commit(
        supabase, ctx, kind=kind, target=target, before=None, after=_angle_out(angle), evidence=ev,
        conversation_id=conversation_id, message_id=message_id,
        apply=lambda: _insert_angle(supabase, ctx.map_id, angle),
        rollback=lambda: _delete_angle(supabase, ctx.map_id, seq),
    )
    res.seq = seq if res.ok else None
    return res


def remove_angle(
    supabase: Any,
    map_id: str,
    seq: int,
    *,
    evidence: str,
    user_id: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> EditResult:
    """Remove thread ``A{seq}``. Done even when fewer than two threads are left —
    then ``below_two`` / ``warning`` say «بحث شامل» can no longer run on the map."""
    kind, target = "remove_angle", f"A{seq}"
    ctx, ev, err = _start(supabase, map_id, user_id, evidence, kind, target)
    if err:
        return err
    angle = ctx.angle(seq)
    if angle is None:
        return _fail(R_NO_ANGLE, kind, target)
    before = _angle_out(angle)
    res = _commit(
        supabase, ctx, kind=kind, target=target, before=before, after=None, evidence=ev,
        conversation_id=conversation_id, message_id=message_id,
        apply=lambda: _delete_angle(supabase, ctx.map_id, seq),
        rollback=lambda: _insert_angle(supabase, ctx.map_id, before),
    )
    if res.ok:
        res.angles_left = len(ctx.angles) - 1
        res.below_two = res.angles_left < MIN_ANGLES_FOR_EXTENDED
        res.warning = W_BELOW_TWO if res.below_two else ""
    return res


# ---------------------------------------------------------------------------
# Gaps
# ---------------------------------------------------------------------------


def _gaps(ctx: _Ctx) -> list[dict]:
    return [dict(g) for g in (ctx.map.get("gaps") or []) if isinstance(g, Mapping)]


def _gap_values(supabase: Any, ctx: _Ctx, fields: Mapping[str, Any], current: Mapping[str, Any], source: str) -> dict:
    new: dict[str, Any] = {}
    for k, v in fields.items():
        if k == "kind":
            new[k] = _gap_kind(v)
        elif k == "ocr_suspect":
            new[k] = _bool(v)
        elif k == "issue":
            new[k] = _decode(v, source)
            if not new[k]:
                raise _Refused(R_ISSUE)
    has_full = "sections" in fields
    if has_full and any(k in fields for k in SECTION_DELTAS):
        raise _Refused(R_SECTIONS_BOTH)
    if has_full:
        new["sections"] = _refs(supabase, ctx, fields["sections"])
    elif any(k in fields for k in SECTION_DELTAS):
        out = list(current.get("sections") or [])
        for ref in _canon(ctx, fields.get("remove_sections")):
            if ref not in out:
                raise _Refused("القسم المطلوب حذفه ليس في هذه الثغرة.")
            out.remove(ref)
        for ref in _refs(supabase, ctx, fields.get("add_sections")):
            if ref not in out:
                out.append(ref)
        new["sections"] = out
    return new


def add_gap(
    supabase: Any,
    map_id: str,
    gap: Mapping[str, Any],
    *,
    evidence: str,
    user_id: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
    source: str = "",
) -> EditResult:
    """Append a gap ``{kind (default missing), issue, sections (refs, may be
    empty), ocr_suspect (default false)}``; ``target`` = ``"gap n"`` (1-based)."""
    kind = "add_gap"
    if not isinstance(gap, Mapping) or not gap:
        return _fail(R_NO_FIELDS, kind)
    bad = _bad_fields(gap, GAP_FIELDS)
    if bad:
        return _fail(bad, kind)
    ctx, ev, err = _start(supabase, map_id, user_id, evidence, kind)
    if err:
        return err
    try:
        if not str(gap.get("issue") or "").strip():
            raise _Refused(R_ISSUE)
        new = {"kind": "missing", "sections": [], "issue": "", "ocr_suspect": False,
               **_gap_values(supabase, ctx, gap, {}, source)}
    except _Refused as r:
        return _fail(r.reason, kind)
    gaps = _gaps(ctx)
    before_col = _json(gaps)
    target = f"gap {len(gaps) + 1}"
    return _commit(
        supabase, ctx, kind=kind, target=target, before=None, after=new, evidence=ev,
        conversation_id=conversation_id, message_id=message_id,
        apply=lambda: _update_map(supabase, ctx.map_id, {"gaps": gaps + [new]}),
        rollback=lambda: _update_map(supabase, ctx.map_id, {"gaps": before_col}),
    )


def edit_gap(
    supabase: Any,
    map_id: str,
    index: int,
    fields: Mapping[str, Any],
    *,
    evidence: str,
    user_id: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
    source: str = "",
) -> EditResult:
    """Change gap ``index`` (1-based, the order shown): :data:`GAP_FIELDS` or
    ``add_sections`` / ``remove_sections``. ``before`` / ``after`` = the whole gap."""
    kind, target = "edit_gap", f"gap {index}"
    if not isinstance(fields, Mapping) or not fields:
        return _fail(R_NO_FIELDS, kind, target)
    bad = _bad_fields(fields, GAP_FIELDS + SECTION_DELTAS)
    if bad:
        return _fail(bad, kind, target)
    ctx, ev, err = _start(supabase, map_id, user_id, evidence, kind, target)
    if err:
        return err
    gaps = _gaps(ctx)
    if not isinstance(index, int) or not 1 <= index <= len(gaps):
        return _fail(R_NO_GAP, kind, target)
    old = gaps[index - 1]
    try:
        changes = _gap_values(supabase, ctx, fields, old, source)
    except _Refused as r:
        return _fail(r.reason, kind, target)
    new = {**old, **changes}
    if new == old:
        return _fail(R_NO_CHANGE, kind, target)
    before_col = _json(gaps)
    after_col = _json(gaps)
    after_col[index - 1] = new
    return _commit(
        supabase, ctx, kind=kind, target=target, before=old, after=new, evidence=ev,
        conversation_id=conversation_id, message_id=message_id,
        apply=lambda: _update_map(supabase, ctx.map_id, {"gaps": after_col}),
        rollback=lambda: _update_map(supabase, ctx.map_id, {"gaps": before_col}),
    )


def remove_gap(
    supabase: Any,
    map_id: str,
    index: int,
    *,
    evidence: str,
    user_id: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> EditResult:
    """Remove gap ``index`` (1-based)."""
    kind, target = "remove_gap", f"gap {index}"
    ctx, ev, err = _start(supabase, map_id, user_id, evidence, kind, target)
    if err:
        return err
    gaps = _gaps(ctx)
    if not isinstance(index, int) or not 1 <= index <= len(gaps):
        return _fail(R_NO_GAP, kind, target)
    old = gaps[index - 1]
    before_col = _json(gaps)
    after_col = gaps[: index - 1] + gaps[index:]
    return _commit(
        supabase, ctx, kind=kind, target=target, before=old, after=None, evidence=ev,
        conversation_id=conversation_id, message_id=message_id,
        apply=lambda: _update_map(supabase, ctx.map_id, {"gaps": after_col}),
        rollback=lambda: _update_map(supabase, ctx.map_id, {"gaps": before_col}),
    )


# ---------------------------------------------------------------------------
# Client, context, no_angle, unrelated documents
# ---------------------------------------------------------------------------


def _by_document(ctx: _Ctx, value: Any, source: str) -> list[dict]:
    if not isinstance(value, (list, tuple)):
        raise _Refused("قائمة صفة الموكّل في كل مستند غير صالحة.")
    out: list[dict] = []
    for e in value:
        if not isinstance(e, Mapping):
            raise _Refused("قائمة صفة الموكّل في كل مستند غير صالحة.")
        doc = canonical_doc(e.get("document"))
        if doc not in ctx.docs:
            raise _Refused(R_DOCUMENT)
        out.append({"document": doc, "side": _side(e.get("side")), "party": _decode(e.get("party"), source)})
    return out


def edit_client(
    supabase: Any,
    map_id: str,
    fields: Mapping[str, Any],
    *,
    evidence: str,
    user_id: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
    source: str = "",
) -> EditResult:
    """Change the client line: any of :data:`CLIENT_FIELDS` (``by_document`` =
    ``[{document "D2", side, party}]``, replaced whole). A changed ``side`` or
    ``party`` without a ``confidence`` sets it to ``clear`` (the user said so).
    ``before`` / ``after`` = the changed keys only. ``by_document`` is kept as is
    unless given — pass ``[]`` to clear it when the side flips."""
    kind, target = "edit_client", "client"
    if not isinstance(fields, Mapping) or not fields:
        return _fail(R_NO_FIELDS, kind, target)
    bad = _bad_fields(fields, CLIENT_FIELDS)
    if bad:
        return _fail(bad, kind, target)
    ctx, ev, err = _start(supabase, map_id, user_id, evidence, kind, target)
    if err:
        return err
    client = dict(ctx.map.get("client") or {})
    try:
        new: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "side":
                new[k] = _side(v)
            elif k == "confidence":
                c = _key(v)
                if c not in CONFIDENCES:
                    raise _Refused(R_CONFIDENCE)
                new[k] = c
            elif k in ("party", "basis"):
                new[k] = _decode(v, source)
            elif k == "by_document":
                new[k] = _by_document(ctx, v, source)
    except _Refused as r:
        return _fail(r.reason, kind, target)
    changed = {k: v for k, v in new.items() if client.get(k) != v}
    if ("side" in changed or "party" in changed) and "confidence" not in fields and client.get("confidence") != "clear":
        changed["confidence"] = "clear"
    if not changed:
        return _fail(R_NO_CHANGE, kind, target)
    before = {k: client.get(k) for k in changed}
    new_client = {**client, **changed}
    return _commit(
        supabase, ctx, kind=kind, target=target, before=before, after=changed, evidence=ev,
        conversation_id=conversation_id, message_id=message_id,
        apply=lambda: _update_map(supabase, ctx.map_id, {"client": new_client}),
        rollback=lambda: _update_map(supabase, ctx.map_id, {"client": client}),
    )


def edit_context(
    supabase: Any,
    map_id: str,
    *,
    add: Sequence[Any] = (),
    remove: Sequence[Any] = (),
    evidence: str,
    user_id: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> EditResult:
    """Add sections to / remove them from ``context`` (the sections every search
    keeps in view). ``before`` / ``after`` = the whole list."""
    kind, target = "edit_context", "context"
    if not add and not remove:
        return _fail(R_NO_FIELDS, kind, target)
    ctx, ev, err = _start(supabase, map_id, user_id, evidence, kind, target)
    if err:
        return err
    current = [str(r) for r in ctx.map.get("context") or []]
    try:
        out = list(current)
        for ref in _canon(ctx, list(remove)):
            if ref not in out:
                raise _Refused(R_NOT_IN_CONTEXT)
            out.remove(ref)
        for ref in _refs(supabase, ctx, list(add)):
            if ref not in out:
                out.append(ref)
    except _Refused as r:
        return _fail(r.reason, kind, target)
    if out == current:
        return _fail(R_NO_CHANGE, kind, target)
    return _commit(
        supabase, ctx, kind=kind, target=target, before=current, after=out, evidence=ev,
        conversation_id=conversation_id, message_id=message_id,
        apply=lambda: _update_map(supabase, ctx.map_id, {"context": out}),
        rollback=lambda: _update_map(supabase, ctx.map_id, {"context": current}),
    )


def edit_no_angle(
    supabase: Any,
    map_id: str,
    entries: Sequence[Mapping[str, Any]],
    *,
    evidence: str,
    user_id: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
    source: str = "",
) -> EditResult:
    """Replace ``no_angle`` (core sections in no thread): ``[{section, why}]``."""
    kind, target = "edit_no_angle", "no_angle"
    if not isinstance(entries, (list, tuple)):
        return _fail(R_NO_FIELDS, kind, target)
    ctx, ev, err = _start(supabase, map_id, user_id, evidence, kind, target)
    if err:
        return err
    try:
        out = []
        for e in entries:
            if not isinstance(e, Mapping) or set(e) - {"section", "why"}:
                raise _Refused("عنصر غير صالح في قائمة الأقسام خارج الخيوط.")
            refs = _refs(supabase, ctx, [e.get("section")])
            if not refs:
                raise _Refused("عنصر غير صالح في قائمة الأقسام خارج الخيوط.")
            out.append({"section": refs[0], "why": _decode(e.get("why"), source)})
    except _Refused as r:
        return _fail(r.reason, kind, target)
    current = [dict(x) for x in ctx.map.get("no_angle") or [] if isinstance(x, Mapping)]
    if out == current:
        return _fail(R_NO_CHANGE, kind, target)
    return _commit(
        supabase, ctx, kind=kind, target=target, before=current, after=out, evidence=ev,
        conversation_id=conversation_id, message_id=message_id,
        apply=lambda: _update_map(supabase, ctx.map_id, {"no_angle": out}),
        rollback=lambda: _update_map(supabase, ctx.map_id, {"no_angle": current}),
    )


def edit_unrelated(
    supabase: Any,
    map_id: str,
    entries: Sequence[Mapping[str, Any]],
    *,
    evidence: str,
    user_id: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
    source: str = "",
) -> EditResult:
    """Replace ``unrelated_documents`` (documents of another matter):
    ``[{document "D3", why}]``."""
    kind, target = "edit_unrelated", "unrelated_documents"
    if not isinstance(entries, (list, tuple)):
        return _fail(R_NO_FIELDS, kind, target)
    ctx, ev, err = _start(supabase, map_id, user_id, evidence, kind, target)
    if err:
        return err
    try:
        out = []
        for e in entries:
            if not isinstance(e, Mapping) or set(e) - {"document", "why"}:
                raise _Refused("عنصر غير صالح في قائمة المستندات غير المتصلة.")
            doc = canonical_doc(e.get("document"))
            if doc not in ctx.docs:
                raise _Refused(R_DOCUMENT)
            out.append({"document": doc, "why": _decode(e.get("why"), source)})
    except _Refused as r:
        return _fail(r.reason, kind, target)
    current = [dict(x) for x in ctx.map.get("unrelated_documents") or [] if isinstance(x, Mapping)]
    if out == current:
        return _fail(R_NO_CHANGE, kind, target)
    return _commit(
        supabase, ctx, kind=kind, target=target, before=current, after=out, evidence=ev,
        conversation_id=conversation_id, message_id=message_id,
        apply=lambda: _update_map(supabase, ctx.map_id, {"unrelated_documents": out}),
        rollback=lambda: _update_map(supabase, ctx.map_id, {"unrelated_documents": current}),
    )


# ---------------------------------------------------------------------------
# Snapshot and undo
# ---------------------------------------------------------------------------

#: Map-row keys a snapshot keeps besides :data:`MAP_COLUMNS`.
_SNAPSHOT_HEAD = ("map_id", "message_id", "conversation_id", "user_id", "created_at")
_ITEM_KEYS = ("item_id", "doc_index", "content_chars", "segment_prompt_version", "paragraph_rule_version", "segmented_at")


def snapshot_map(
    supabase: Any,
    map_id: str,
    reason: str,
    *,
    user_id: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> EditResult:
    """Log the whole map as it is now (``before`` = ``{map, items, angles}``) — a
    re-analysis does this first, so :func:`undo_edit` on the snapshot can put the
    map back. ``reason`` goes in ``evidence`` (the user's critique, or why)."""
    kind, target = "snapshot", "map"
    ctx, reason_load = _load(supabase, map_id, user_id)
    if ctx is None:
        return _fail(reason_load, kind, target)
    snap = {
        "map": {k: ctx.map.get(k) for k in _SNAPSHOT_HEAD + MAP_COLUMNS if k in ctx.map},
        "items": [{k: i.get(k) for k in _ITEM_KEYS} for i in ctx.items],
        "angles": [_angle_out(a) for a in ctx.angles],
    }
    edit_id = _write_log(
        supabase, map_id=ctx.map_id, item_ids=ctx.item_ids, kind=kind, target=target,
        before=snap, after=None, evidence=_evidence(reason), user_id=ctx.user_id,
        conversation_id=conversation_id, message_id=message_id,
    )
    if edit_id is None:
        return _fail(R_WRITE, kind, target)
    return EditResult(ok=True, edit_id=edit_id, kind=kind, target=target, before=_json(snap), map_id=ctx.map_id)


def _restore_snapshot(supabase: Any, row: Mapping[str, Any], user_id: str) -> tuple[str | None, str]:
    snap = row.get("before") or {}
    head = dict(snap.get("map") or {})
    items = [dict(i) for i in snap.get("items") or []]
    if not items or str(head.get("user_id") or "") != str(user_id):
        return None, R_UNDO_NONE
    new_id = write_angle_map(
        supabase,
        message_id=head.get("message_id"),
        conversation_id=head.get("conversation_id"),
        user_id=str(user_id),
        items=items,
        map_row={k: head.get(k) for k in MAP_COLUMNS},
        angles=[dict(a) for a in snap.get("angles") or []],
    )
    return (new_id, "") if new_id else (None, R_WRITE)


def _find(gaps: list[dict], gap: Any) -> int | None:
    return next((i for i, g in enumerate(gaps) if g == gap), None)


def _inverse(supabase: Any, ctx: _Ctx, row: Mapping[str, Any]) -> tuple[Any, Any, Any, str, int | None]:
    """``(apply, rollback, (before, after), target, seq)`` that undo ``row`` on
    ``ctx``. Raises :class:`_Refused`."""
    kind = str(row.get("kind") or "")
    target = str(row.get("target") or "")
    before, after = row.get("before"), row.get("after")
    mid = ctx.map_id

    if kind in ("edit_angle", "add_angle", "remove_angle"):
        m = _ANGLE_TARGET.match(target)
        if not m:
            raise _Refused(R_UNDO_NONE)
        seq = int(m.group(1))
        angle = ctx.angle(seq)
        if kind == "edit_angle":
            now = {k: angle.get(k) for k in (before or {})} if angle is not None else None
            if now is None or now != dict(after or {}):
                raise _Refused(R_UNDO_CHANGED)
            vals = dict(before or {})
            return (lambda: _update_angle(supabase, mid, seq, vals),
                    lambda: _update_angle(supabase, mid, seq, now), (now, vals), target, seq)
        if kind == "add_angle":
            if angle is None:
                raise _Refused(R_UNDO_CHANGED)
            gone = _angle_out(angle)
            return (lambda: _delete_angle(supabase, mid, seq),
                    lambda: _insert_angle(supabase, mid, gone), (gone, None), target, seq)
        back = dict(before or {})
        if ctx.angle(seq) is not None:   # its seq was taken since: a new one
            seq = max(_used_seqs(supabase, ctx) | {0}) + 1
        back["seq"] = seq
        return (lambda: _insert_angle(supabase, mid, back),
                lambda: _delete_angle(supabase, mid, seq), (None, back), f"A{seq}", seq)

    if kind in ("add_gap", "edit_gap", "remove_gap"):
        gaps = _gaps(ctx)
        new = _json(gaps)
        if kind == "add_gap":
            i = _find(gaps, after)
            if i is None:
                raise _Refused(R_UNDO_CHANGED)
            del new[i]
        elif kind == "edit_gap":
            i = _find(gaps, after)
            if i is None:
                raise _Refused(R_UNDO_CHANGED)
            new[i] = dict(before or {})
        else:
            m = _GAP_TARGET.match(target)
            pos = min(max(int(m.group(1)) - 1, 0), len(new)) if m else len(new)
            new.insert(pos, dict(before or {}))
        return (lambda: _update_map(supabase, mid, {"gaps": new}),
                lambda: _update_map(supabase, mid, {"gaps": gaps}), (gaps, new), target, None)

    if kind == "edit_client":
        client = dict(ctx.map.get("client") or {})
        if {k: client.get(k) for k in (after or {})} != dict(after or {}):
            raise _Refused(R_UNDO_CHANGED)
        new_client = {**client, **dict(before or {})}
        return (lambda: _update_map(supabase, mid, {"client": new_client}),
                lambda: _update_map(supabase, mid, {"client": client}),
                ({k: client.get(k) for k in (before or {})}, dict(before or {})), target, None)

    if kind == "edit_context":
        current = [str(r) for r in ctx.map.get("context") or []]
        added = [r for r in (after or []) if r not in (before or [])]
        removed = [r for r in (before or []) if r not in (after or [])]
        new = [r for r in current if r not in added] + [r for r in removed if r not in current]
        return (lambda: _update_map(supabase, mid, {"context": new}),
                lambda: _update_map(supabase, mid, {"context": current}), (current, new), target, None)

    if kind in ("edit_no_angle", "edit_unrelated"):
        col = "no_angle" if kind == "edit_no_angle" else "unrelated_documents"
        current = _json(ctx.map.get(col) or [])
        if current != list(after or []):
            raise _Refused(R_UNDO_CHANGED)
        back = _json(before or [])
        return (lambda: _update_map(supabase, mid, {col: back}),
                lambda: _update_map(supabase, mid, {col: current}), (current, back), target, None)

    raise _Refused(R_UNDO_NONE)


def _mark_undone(supabase: Any, edit_id: str) -> bool | None:
    """Mark the edit undone FIRST, only while it is not (``undone_at IS NULL``):
    ``True`` — this call marked it; ``False`` — it already was (another undo won);
    ``None`` — the write failed. So two undos of one edit can never both re-apply
    it (a removed thread re-inserted twice)."""
    try:
        resp = (
            supabase.table(EDITS_TABLE).update({"undone_at": _now()})
            .eq("id", str(edit_id)).is_("undone_at", "null").execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: edit %s not marked undone (%s)", edit_id, type(exc).__name__)
        return None
    return bool(_data(resp))


def _unmark_undone(supabase: Any, edit_id: str) -> None:
    """Roll a mark back when the undo itself did not happen."""
    try:
        supabase.table(EDITS_TABLE).update({"undone_at": None}).eq("id", str(edit_id)).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: edit %s left marked undone after a failed undo (%s)", edit_id,
                       type(exc).__name__)


def _drop_log_row(supabase: Any, row_id: str) -> None:
    try:
        supabase.table(EDITS_TABLE).delete().eq("id", str(row_id)).execute()
    except Exception as exc:  # noqa: BLE001 — an undo row for an undo that did not happen; harmless
        logger.warning("angle_mapper: undo row %s not removed (%s)", row_id, type(exc).__name__)


def _map_exists(supabase: Any, map_id: str) -> bool:
    """Raises on a failed read."""
    return bool(_data(supabase.table(MAPS_TABLE).select("map_id").eq("map_id", str(map_id)).limit(1).execute()))


def _undo_snapshot(
    supabase: Any,
    row: Mapping[str, Any],
    user_id: str,
    *,
    evidence: str,
    conversation_id: str | None,
    message_id: str | None,
) -> EditResult:
    """Restore the map a re-analysis replaced. Refused while an angle-map run is
    going on its files (the run would delete the restored map when it stores), and
    when the snapshot's map is still the current one (the re-analysis never
    replaced it — restoring would only copy it under a new id and strand its log).
    Order: mark the snapshot undone → write the undo row → restore → point the
    undo row at the restored map. A failed step undoes the earlier ones, so a
    restore is never left unrecorded and a failed restore leaves the snapshot
    listed."""
    kind, edit_id = "undo", str(row.get("id") or "")
    snap_map = str(row.get("map_id") or "")
    items = [str(i) for i in row.get("item_ids") or []]
    try:
        from .reanalysis import group_in_flight

        if group_in_flight(supabase, items):
            return _fail(R_UNDO_BUSY, kind, edit_id)
        if _map_exists(supabase, snap_map):
            return _fail(R_UNDO_NOT_REPLACED, kind, edit_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: snapshot %s not checked for undo (%s)", edit_id, type(exc).__name__)
        return _fail(R_READ, kind, edit_id)
    snap = row.get("before") or {}
    head = dict(snap.get("map") or {})
    if not snap.get("items") or str(head.get("user_id") or "") != str(user_id):
        return _fail(R_UNDO_NONE, kind, edit_id)

    marked = _mark_undone(supabase, edit_id)
    if marked is None:
        return _fail(R_WRITE, kind, edit_id)
    if not marked:
        return _fail(R_UNDO_DONE, kind, edit_id)
    after = {"restored_from": snap_map}
    undo_id = _write_log(
        supabase, map_id=snap_map, item_ids=items, kind=kind, target=edit_id,
        before=None, after=after, evidence=evidence, user_id=str(user_id),
        conversation_id=conversation_id, message_id=message_id,
    )
    if undo_id is None:
        _unmark_undone(supabase, edit_id)
        return _fail(R_WRITE, kind, edit_id)
    new_id, reason = _restore_snapshot(supabase, row, str(user_id))
    if new_id is None:
        _drop_log_row(supabase, undo_id)
        _unmark_undone(supabase, edit_id)
        return _fail(reason, kind, edit_id)
    try:  # the undo row names the restored map (its provisional map_id is the snapshot's)
        supabase.table(EDITS_TABLE).update({"map_id": new_id}).eq("id", undo_id).execute()
    except Exception as exc:  # noqa: BLE001 — the restore is recorded either way
        logger.warning("angle_mapper: undo row %s not pointed at map %s (%s)", undo_id, new_id, type(exc).__name__)
    return EditResult(ok=True, edit_id=undo_id, kind=kind, target=edit_id, map_id=new_id, after=after)


def undo_edit(
    supabase: Any,
    edit_id: str,
    user_id: str,
    *,
    evidence: str = "",
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> EditResult:
    """Undo one logged edit: put back its ``before``. Writes an ``undo`` row
    (``target`` = the undone edit's id) and sets the edit's ``undone_at``.

    Refused when the edit is unknown / not the user's / already undone / itself an
    undo, when its map was replaced since (a re-analysis), or when the part it
    changed was changed again so ``before`` no longer fits (a gap edited again, an
    angle removed). An undone ``remove_angle`` gets its old ``seq`` back unless that
    seq was taken since (``result.seq``). Undoing a ``snapshot`` RESTORES that map
    in place of the group's current one (``store.write_angle_map``; ``result.map_id``
    = the restored map's new id) — refused while an angle-map run is going on its
    files, and when the snapshot's map was never replaced (:func:`_undo_snapshot`).

    The edit is marked undone FIRST, only while it is not (a second undo loses
    cleanly: ``R_UNDO_DONE``); the mark is rolled back when the change itself
    cannot be applied or logged."""
    kind = "undo"
    try:
        rows = _data(
            supabase.table(EDITS_TABLE).select("*").eq("id", str(edit_id or "")).eq("user_id", str(user_id or ""))
            .limit(1).execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: edit %s not read for undo (%s)", edit_id, type(exc).__name__)
        return _fail(R_READ, kind)
    if not edit_id or not user_id or not rows:
        return _fail(R_UNDO_NONE, kind)
    row = rows[0]
    if row.get("undone_at"):
        return _fail(R_UNDO_DONE, kind, str(edit_id))
    if row.get("kind") == "undo":
        return _fail(R_UNDO_UNDO, kind, str(edit_id))
    ev = _evidence(evidence)

    if row.get("kind") == "snapshot":
        return _undo_snapshot(
            supabase, row, str(user_id), evidence=ev, conversation_id=conversation_id, message_id=message_id,
        )

    ctx, reason = _load(supabase, str(row.get("map_id") or ""), str(user_id))
    if ctx is None:
        return _fail(R_UNDO_REANALYZED if reason == R_NO_MAP else reason, kind, str(edit_id))
    try:
        apply, rollback, (before, after), _target, seq = _inverse(supabase, ctx, row)
    except _Refused as r:
        return _fail(r.reason, kind, str(edit_id))
    # Mark first (conditional): a second undo of the same edit cannot re-apply it.
    marked = _mark_undone(supabase, str(edit_id))
    if marked is None:
        return _fail(R_WRITE, kind, str(edit_id))
    if not marked:
        return _fail(R_UNDO_DONE, kind, str(edit_id))
    res = _commit(
        supabase, ctx, kind=kind, target=str(edit_id), before=before, after=after, evidence=ev,
        conversation_id=conversation_id, message_id=message_id, apply=apply, rollback=rollback,
    )
    if not res.ok:
        _unmark_undone(supabase, str(edit_id))
    else:
        res.seq = seq
        if row.get("kind") == "add_angle":
            res.angles_left = len(ctx.angles) - 1
            res.below_two = res.angles_left < MIN_ANGLES_FOR_EXTENDED
            res.warning = W_BELOW_TWO if res.below_two else ""
    return res


__all__ = [
    "ANGLE_FIELDS",
    "CLIENT_FIELDS",
    "EDITS_TABLE",
    "EDIT_KINDS",
    "EditResult",
    "GAP_FIELDS",
    "MIN_ANGLES_FOR_EXTENDED",
    "SECTION_DELTAS",
    "add_angle",
    "add_gap",
    "edit_angle",
    "edit_client",
    "edit_context",
    "edit_gap",
    "edit_no_angle",
    "edit_unrelated",
    "load_edit_log",
    "remove_angle",
    "remove_gap",
    "snapshot_map",
    "undo_edit",
]
