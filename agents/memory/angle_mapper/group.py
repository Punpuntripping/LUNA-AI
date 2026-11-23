"""The message group — the attachments of ONE user message, mapped together (D17).

**Who is in the group.** An item belongs to its FIRST message (``message_attachments``,
the segmenter's ``bundles.primary_messages`` rule); the group is that message's live
attachments of the same user (an item with no message link = a group of one). A
member must have a USABLE segment map: fresh (same ``content_chars``), with
sections, seg-v5.2 or newer (the owner fields), the current paragraph rule, and
paragraphs that still fall on its sections' offsets. That covers both files over
5,000 characters (segmented alone) and the small files the segmenter BUNDLED
because together they pass 5,000 (segmentation D26) — by default they join their
message's group like any other member (plan D16 / Q7 default). Any other
attachment of the message is ``excluded`` with its reason. Members are numbered
D1, D2… in upload order (``wi_seq``, then ``created_at``).

**Segment maps hang on the FILE** (migration 223: ``attachment_segment_maps.item_id``
holds ``user_files.file_id``). A file attached from «الملفات» to another
conversation (migration 227_user_files_attach) is a NEW item with its file's id —
its segment map is found under that file id. Nothing here reads a segmenter table
by item id: the headers go through the segmenter store's own item → file mapping
(``store._file_keys``), the maps through its loader (``load_segment_maps``), the
lengths through its ``_content_lengths`` (the file's ``content_chars``), the text
through its ``_load_items`` (the ``shared.files`` overlay). The angle map itself
belongs to the CONVERSATION's use of a file: it is keyed by message + item id.

**Two levels, one rule for a usable member.**

1. :func:`load_state` — HEADERS ONLY, never the text: the items' pointer columns
   (live, owner, kind, upload order), their length, and their file's segment-map
   header (prompt / paragraph-rule versions, ``content_chars``, ``created_at``). A
   header-usable attachment is live, the user's, has a map, seg-v5.2 or newer, the
   current paragraph rule and the same ``content_chars``. This decides PENDING and
   FRESHNESS: every member's run and the frame's ``load``
   (``store.load_fresh_angle_map_for_item``) read nothing more.
2. :func:`group_from_state` — only in the run that will call the model: the members'
   text, segment maps (``load_segment_maps``) and paragraphs, and the checks that
   need them (the map has sections; the rebuilt paragraphs still fall on its
   sections' offsets — a changed ``doc_type`` moves them). A header-usable
   attachment that fails them is LEFT OUT of the map and stored WITH the map
   (``left_out``: its reason and the versions it was judged on).

The freshness check (:func:`stored_fits`) compares the header-usable attachments
with the stored map's members AND its left-out ones, version by version. So it
agrees with the runner on who is in the group without reading text: an attachment
left out for its paragraphs stays left out while its versions are unchanged (no
"stale" for the frame while the runner answers ``skipped_fresh``); when it is
segmented again its versions change, the map is stale, and the group is mapped
again — once. A member already in a map is judged by its versions only: a later
``doc_type`` change does not touch the map (its sections are what the map's refs
name).

The run that calls the model also reads the TITLES (only) of the attachments it
leaves out (:func:`item_titles`, angles-v5): the model is told they were sent but
could not be read.

Both levels raise on a failed read (a caller must not mistake "could not read"
for "no map"), except the pending probe, which treats a failed read as "not
pending". Without the files read switch, the length comes from ``content_md``
(the segmenter store's own fallback) — the only text the header level then reads.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from agents.memory.attachment_segmenter.bundles import plan_units, primary_messages, upload_order
from agents.memory.attachment_segmenter.paragraphs import PARAGRAPH_RULE_VERSION, Paragraph, paragraphize
from agents.memory.attachment_segmenter.runner import _load_items
from agents.memory.attachment_segmenter.store import (
    MAPS_TABLE as SEGMENT_MAPS_TABLE,
    _content_lengths,
    _file_keys,
    load_segment_maps,
)
from agents.utils.attachment_delivery import SEGMENT_MIN_CHARS
from shared.files import store as files_store

from .refs import format_ref

logger = logging.getLogger(__name__)

#: Segment maps older than this have no owner fields (seg-v5.2, 2026-11-18).
MIN_SEGMENT_PROMPT = (5, 2)

_SEG_VERSION_RE = re.compile(r"seg-v(\d+)(?:\.(\d+))?")
#: The pointer columns the header level reads — never a file column (title, text,
#: summary, metadata…: ``shared.files.store.FILE_COLUMNS``).
_POINTER_COLUMNS = "item_id, user_id, conversation_id, case_id, kind, deleted_at, wi_seq, created_at"
#: A segment map's header — no segments, no text.
_HEADER_COLUMNS = "item_id, prompt_version, paragraph_rule_version, content_chars, created_at"
#: Section fields carried in the group's section dicts (no section text — the
#: prompt reads the numbered paragraphs, labels read the owners).
_SECTION_FIELDS = (
    "seq", "from_p", "to_p", "page_from", "page_to", "char_start", "char_end",
    "label", "value", "what", "owner_class", "owner", "brought_by",
)


def _data(resp: Any) -> list[dict]:
    return list(getattr(resp, "data", None) or [])


def _doc_type_of(row: Mapping[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return str(metadata.get("doc_type") or "") if isinstance(metadata, Mapping) else ""


def _live_attachment(row: Mapping[str, Any] | None) -> bool:
    return bool(row) and str(row.get("kind") or "") == "attachment" and not row.get("deleted_at")


def segment_version_ok(prompt_version: Any) -> bool:
    """``seg-v5.2`` or newer (the owner fields exist); anything unparsable is not."""
    m = _SEG_VERSION_RE.fullmatch(str(prompt_version or "").strip())
    if not m:
        return False
    return (int(m.group(1)), int(m.group(2) or 0)) >= MIN_SEGMENT_PROMPT


def _ts(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# The group's ids
# ---------------------------------------------------------------------------


def message_items(supabase: Any, message_id: str) -> list[str]:
    """The items whose FIRST message is ``message_id`` (link order). Raises on a
    failed read."""
    links = _data(
        supabase.table("message_attachments").select("document_id").eq("message_id", message_id).execute()
    )
    ids = list(dict.fromkeys(str(r["document_id"]) for r in links if r.get("document_id")))
    if not ids:
        return []
    primary = primary_messages(supabase, ids)
    return [i for i in ids if primary.get(i) == str(message_id)]


def group_item_ids(supabase: Any, item_id: str) -> tuple[str | None, list[str]]:
    """``(message_id, item ids)`` of ``item_id``'s group — ``(None, [item_id])`` for
    an item with no message link. Raises on a failed read."""
    item_id = str(item_id)
    message_id = primary_messages(supabase, [item_id]).get(item_id)
    if not message_id:
        return None, [item_id]
    ids = message_items(supabase, message_id)
    if item_id not in ids:
        ids.append(item_id)
    return message_id, ids


# ---------------------------------------------------------------------------
# Headers (no text)
# ---------------------------------------------------------------------------


def pointer_rows(supabase: Any, item_ids: Sequence[str]) -> dict[str, dict]:
    """``{item_id: row}`` — the POINTER columns only (owner, conversation, case,
    kind, liveness, upload order): no file column, so no files overlay and no text.
    Raises on a failed read."""
    ids = list(dict.fromkeys(str(i) for i in item_ids if i))
    if not ids:
        return {}
    rows = _data(supabase.table("workspace_items").select(_POINTER_COLUMNS).in_("item_id", ids).execute())
    return {str(r["item_id"]): r for r in rows}


def item_titles(supabase: Any, item_ids: Sequence[str], *, user_id: str | None = None) -> dict[str, str]:
    """``{item_id: title}`` — the title only (the file's, through ``shared.files``),
    of the user's items. For the message's attachments the map leaves out: the
    model is told they were sent but could not be read (angles-v5). Raises on a
    failed read."""
    ids = list(dict.fromkeys(str(i) for i in item_ids if i))
    if not ids:
        return {}
    q = supabase.table("workspace_items").select(
        files_store.with_file_id("item_id, title", user_id=user_id, supabase=supabase)
    ).in_("item_id", ids)
    if user_id:
        q = q.eq("user_id", user_id)
    rows = files_store.overlay_files(supabase, _data(q.execute()), user_id=user_id)
    return {str(r["item_id"]): str(r.get("title") or "") for r in rows}


def segment_headers(supabase: Any, item_ids: Sequence[str], *, user_id: str | None = None) -> dict[str, dict]:
    """``{item_id: its FILE's segment-map header}`` (``_HEADER_COLUMNS``): each item is
    turned into the id its map is stored under by the segmenter store's own mapping
    (``_file_keys`` — the file, or the item when it links none), then one read of
    the headers. An item without a map is left out. Raises on a failed read."""
    ids = list(dict.fromkeys(str(i) for i in item_ids if i))
    if not ids:
        return {}
    keys = _file_keys(supabase, ids, user_id=user_id or None, strict=True)
    q = supabase.table(SEGMENT_MAPS_TABLE).select(_HEADER_COLUMNS).in_("item_id", list(dict.fromkeys(keys.values())))
    if user_id:
        q = q.eq("user_id", user_id)
    by_key = {str(r["item_id"]): r for r in _data(q.execute())}
    return {item: by_key[key] for item, key in keys.items() if key in by_key}


def _header_skip(header: Mapping[str, Any] | None, content_chars: int) -> str | None:
    if header is None:
        return "skipped_no_map"
    if not segment_version_ok(header.get("prompt_version")):
        return "skipped_old_segments"
    if str(header.get("paragraph_rule_version") or "") != PARAGRAPH_RULE_VERSION:
        return "skipped_paragraph_rule"
    if int(header.get("content_chars") if header.get("content_chars") is not None else -1) != content_chars:
        return "skipped_stale"
    return None


def _header_version(content_chars: int, header: Mapping[str, Any]) -> tuple:
    """A header-usable attachment's version tuple — what a stored map must match."""
    return (
        int(content_chars),
        str(header.get("prompt_version") or ""),
        str(header.get("paragraph_rule_version") or ""),
        _ts(header.get("created_at")),
    )


def _version(row: Mapping[str, Any], smap: Any) -> tuple:
    """A member's version tuple from its text and its segment map — equal to its
    header version (same map row, same length)."""
    return (
        len(str(row.get("content_md") or "")),
        str(smap.prompt_version or ""),
        str(smap.paragraph_rule_version or ""),
        _ts(smap.created_at),
    )


def is_pending(supabase: Any, item_id: str) -> bool:
    """The item's summary or segmenter is still running: in this process (the helper
    frame's in-flight runs) or in another one (a live ``helper_runs`` segmenter
    lease — the run store looks it up under the item's file). A failed read = not
    pending (the group then maps without it, and its arrival later makes the map
    stale). Never raises."""
    try:
        from agents.helpers._runtime import inflight, utcnow

        if inflight("segmenter", str(item_id)) is not None or inflight("summary", str(item_id)) is not None:
            return True
        from agents.helpers.status import SupabaseHelperRunStore

        row = SupabaseHelperRunStore(supabase).get(str(item_id), "segmenter")
        return row is not None and row.lease_live(utcnow())
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: pending probe failed for %s (%s)", item_id, type(exc).__name__)
        return False


@dataclass
class GroupState:
    """A group resolved from HEADERS only (:func:`load_state`): what decides pending
    and freshness. ``item_ids`` — the header-usable attachments, upload order (the
    members-to-be: the text level may still leave some out)."""

    message_id: str | None
    user_id: str
    conversation_id: str | None = None
    case_id: str | None = None
    item_ids: list[str] = field(default_factory=list)
    #: item_id → pointer row (every live attachment of the user, upload order).
    rows: dict[str, dict] = field(default_factory=dict)
    #: item_id → current length (the file's ``content_chars``).
    lengths: dict[str, int] = field(default_factory=dict)
    #: item_id → its file's segment-map header.
    headers: dict[str, dict] = field(default_factory=dict)
    #: The message's other attachments: item_id → why it is not header-usable.
    excluded: dict[str, str] = field(default_factory=dict)
    #: Candidates still being summarized / segmented (see the module docstring).
    pending: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """One in-process run per group: its message, or its single item."""
        if self.message_id:
            return f"message:{self.message_id}"
        return "items:" + ",".join(sorted(self.item_ids))

    @property
    def total_chars(self) -> int:
        return sum(self.lengths.get(i, 0) for i in self.item_ids)

    def version_of(self, item_id: str) -> tuple:
        return _header_version(self.lengths[item_id], self.headers[item_id])

    def versions(self) -> dict[str, tuple]:
        """``{item_id: (content_chars, segment_prompt_version, paragraph_rule_version,
        segmented_at)}`` of the header-usable attachments."""
        return {i: self.version_of(i) for i in self.item_ids}


def load_state(
    supabase: Any,
    message_id: str | None,
    item_ids: Sequence[str],
    *,
    user_id: str,
    check_pending: bool = True,
) -> GroupState:
    """The group of ``item_ids`` from headers only (module docstring, level 1) —
    and, ``check_pending``, the candidates still being segmented. Sync (call it in
    a thread). Raises on a failed read."""
    ids = list(dict.fromkeys(str(i) for i in item_ids if i))
    state = GroupState(message_id=str(message_id) if message_id else None, user_id=str(user_id or ""))
    if not ids:
        return state
    rows = pointer_rows(supabase, ids)
    live: list[dict] = []
    for i in ids:
        row = rows.get(i)
        if not _live_attachment(row):
            state.excluded[i] = "skipped_not_found" if not row or row.get("deleted_at") else "skipped_not_attachment"
        elif str(row.get("user_id") or "") != state.user_id:  # never mix two users' text
            state.excluded[i] = "skipped_other_user"
        else:
            live.append(row)
    live.sort(key=upload_order)
    if not live:
        return state
    state.conversation_id = str(live[0].get("conversation_id") or "") or None
    state.case_id = str(live[0].get("case_id") or "") or None
    live_ids = [str(r["item_id"]) for r in live]
    state.rows = {str(r["item_id"]): r for r in live}
    state.lengths = {str(k): int(v) for k, v in _content_lengths(supabase, live_ids, state.user_id or None).items()}
    state.headers = segment_headers(supabase, live_ids, user_id=state.user_id or None)

    for i in live_ids:
        if i not in state.lengths:  # gone since the pointer read
            state.excluded[i] = "skipped_not_found"
            continue
        reason = _header_skip(state.headers.get(i), state.lengths[i])
        if reason is not None:
            state.excluded[i] = reason
            continue
        state.item_ids.append(i)

    if check_pending:
        candidates = {
            i
            for unit in plan_units(
                [
                    {"item_id": i, "chars": state.lengths.get(i, 0), "message_id": state.message_id or "",
                     "order": upload_order(state.rows[i])}
                    for i in live_ids
                ],
                min_chars=SEGMENT_MIN_CHARS,
            )
            for i in unit
        }
        usable = set(state.item_ids)
        state.pending = [i for i in live_ids if i in candidates and i not in usable and is_pending(supabase, i)]
    return state


def member_versions(supabase: Any, item_id: str, *, user_id: str | None = None) -> dict[str, tuple]:
    """The header versions of ``item_id``'s group NOW (:meth:`GroupState.versions`).
    ``user_id`` (the stored map's owner) — ``None``: the owner of ``item_id``.
    Raises on a failed read."""
    message_id, ids = group_item_ids(supabase, item_id)
    if not user_id:
        user_id = str((pointer_rows(supabase, [str(item_id)]).get(str(item_id)) or {}).get("user_id") or "")
    return load_state(supabase, message_id, ids, user_id=str(user_id or ""), check_pending=False).versions()


# ---------------------------------------------------------------------------
# The group the runner maps (text)
# ---------------------------------------------------------------------------


@dataclass
class GroupDoc:
    """One member: D-number, its row, its segment map, its paragraphs, its section
    dicts (each with ``ref`` / ``doc`` / ``item_id``)."""

    index: int
    item_id: str
    row: dict
    smap: Any                       # attachment_segmenter.models.StoredMap
    paragraphs: list[Paragraph]
    sections: list[dict]

    @property
    def content_md(self) -> str:
        return str(self.row.get("content_md") or "")

    @property
    def chars(self) -> int:
        return len(self.content_md)

    @property
    def doc_type(self) -> str:
        return _doc_type_of(self.row)

    @property
    def title(self) -> str:
        return str(self.row.get("title") or "")

    @property
    def summary(self) -> str:
        return str(self.row.get("summary") or "")

    def item_record(self) -> dict[str, Any]:
        """Its ``attachment_angle_map_items`` row (without ``map_id``) — the versions
        from the segment map the segmenter's loader returned."""
        return {
            "item_id": self.item_id,
            "doc_index": self.index,
            "content_chars": self.chars,
            "segment_prompt_version": self.smap.prompt_version,
            "paragraph_rule_version": self.smap.paragraph_rule_version,
            "segmented_at": self.smap.created_at,
        }


@dataclass
class Group:
    message_id: str | None
    user_id: str
    conversation_id: str | None = None
    case_id: str | None = None
    docs: list[GroupDoc] = field(default_factory=list)
    #: The message's other attachments: item_id → why it is not a member.
    excluded: dict[str, str] = field(default_factory=dict)
    #: Candidates still being summarized / segmented (from the state).
    pending: list[str] = field(default_factory=list)
    #: Header-usable attachments the text level left out, as the map stores them
    #: (``attachment_angle_maps.left_out``): item_id, reason and the versions they
    #: were judged on.
    left_out: list[dict] = field(default_factory=list)

    @property
    def item_ids(self) -> list[str]:
        return [d.item_id for d in self.docs]

    @property
    def key(self) -> str:
        """One in-process run per group: its message, or its single item."""
        if self.message_id:
            return f"message:{self.message_id}"
        return "items:" + ",".join(sorted(self.item_ids))

    @property
    def total_chars(self) -> int:
        return sum(d.chars for d in self.docs)

    @property
    def sections(self) -> list[dict]:
        """Every section of every member, D-order then document order."""
        return [s for d in self.docs for s in d.sections]

    def versions(self) -> dict[str, tuple]:
        """``{item_id: (content_chars, segment_prompt_version, paragraph_rule_version,
        segmented_at)}`` of the members."""
        return {d.item_id: _version(d.row, d.smap) for d in self.docs}


def _paragraphs_fit(paragraphs: Sequence[Paragraph], sections: Sequence[Any]) -> bool:
    """The rebuilt paragraphs are the ones the map was cut on: every section's
    first / last paragraph sits at its stored offsets (a changed ``doc_type`` —
    another paragraph max — would move them)."""
    n = len(paragraphs)
    for s in sections:
        if not (1 <= s.from_p <= s.to_p <= n):
            return False
        if paragraphs[s.from_p - 1].char_start != s.char_start or paragraphs[s.to_p - 1].char_end != s.char_end:
            return False
    return True


def _left_out_record(state: GroupState, item_id: str, reason: str) -> dict[str, Any]:
    chars, prompt, rule, _segmented = state.version_of(item_id)
    return {
        "item_id": item_id,
        "reason": reason,
        "content_chars": chars,
        "segment_prompt_version": prompt,
        "paragraph_rule_version": rule,
        "segmented_at": state.headers[item_id].get("created_at"),
    }


def group_from_state(supabase: Any, state: GroupState) -> Group:
    """The members of ``state`` with their text (module docstring, level 2) — only
    the run that calls the model calls this. A header-usable attachment whose map
    has no sections, or whose rebuilt paragraphs no longer fall on its sections'
    offsets, is left out (``excluded`` + ``left_out``); so is one that changed or
    went away since the headers were read. Members are D1… in the state's upload
    order. Sync. Raises on a failed read (the segment-map loader itself never
    raises: a failed read there looks like "no sections")."""
    group = Group(
        message_id=state.message_id,
        user_id=state.user_id,
        conversation_id=state.conversation_id,
        case_id=state.case_id,
        excluded=dict(state.excluded),
        pending=list(state.pending),
    )
    ids = list(state.item_ids)
    if not ids:
        return group
    rows = _load_items(supabase, ids)
    lengths = {i: len(str(rows[i].get("content_md") or "")) for i in ids if i in rows}
    maps = load_segment_maps(supabase, list(lengths), user_id=state.user_id or None, content_chars=lengths)

    index = 0
    for item_id in ids:
        row = rows.get(item_id)
        reason: str | None = None
        smap: Any = None
        paragraphs: list[Paragraph] = []
        if not _live_attachment(row) or str(row.get("user_id") or "") != state.user_id:
            reason = "skipped_not_found"
        else:
            smap = maps.get(item_id)
            if smap is None or not smap.segments:
                reason = _header_skip(state.headers.get(item_id), lengths[item_id]) or "skipped_no_sections"
            else:
                paragraphs = paragraphize(str(row.get("content_md") or ""), doc_type=_doc_type_of(row))
                if not _paragraphs_fit(paragraphs, smap.segments):
                    reason = "skipped_paragraph_rule"
        if reason is not None:
            group.excluded[item_id] = reason
            group.left_out.append(_left_out_record(state, item_id, reason))
            continue
        index += 1
        sections = [
            {
                **{k: getattr(s, k, None) for k in _SECTION_FIELDS},
                "ref": format_ref(index, int(s.seq)),
                "doc": index,
                "item_id": item_id,
            }
            for s in smap.segments
        ]
        group.docs.append(GroupDoc(index, item_id, row, smap, paragraphs, sections))
    return group


def load_group(supabase: Any, message_id: str | None, item_ids: Sequence[str], *, user_id: str) -> Group:
    """The group of ``item_ids`` (the message's items, or the items a caller names)
    with its text: :func:`load_state` (no pending check) then
    :func:`group_from_state`. For a run that maps NOW (the preview tool, a
    preview run). Sync. Raises on a failed read."""
    return group_from_state(supabase, load_state(supabase, message_id, item_ids, user_id=user_id, check_pending=False))


# ---------------------------------------------------------------------------
# Freshness — headers against the stored map (members + left out)
# ---------------------------------------------------------------------------


def stored_versions(items: Sequence[Mapping[str, Any]]) -> dict[str, tuple]:
    """The version tuples of a stored map's ``attachment_angle_map_items`` rows (or
    its ``left_out`` entries — same keys)."""
    out: dict[str, tuple] = {}
    for it in items:
        try:
            chars = int(it.get("content_chars"))
        except (TypeError, ValueError):
            chars = -1
        out[str(it.get("item_id") or "")] = (
            chars,
            str(it.get("segment_prompt_version") or ""),
            str(it.get("paragraph_rule_version") or ""),
            _ts(it.get("segmented_at")),
        )
    return out


def _same_version(cur: tuple, old: tuple) -> bool:
    """Same length, segment prompt version and paragraph rule — and the same segment
    map (``segmented_at``) when both sides know it."""
    if cur[:3] != old[:3]:
        return False
    return cur[3] is None or old[3] is None or cur[3] == old[3]


def stored_fits(current: Mapping[str, tuple], stored: Mapping[str, Any] | None) -> bool:
    """Is ``stored`` (``store.load_angle_map_for_item`` shape) the map of the group
    whose header-usable attachments are ``current`` (``GroupState.versions()``)?

    Every stored member is still header-usable with the same versions, and every
    header-usable attachment is either a stored member or a stored ``left_out``
    entry with the same versions it was left out at. Anything else — a member gone
    or changed, a new attachment, one left out that was segmented again — is
    stale."""
    if not stored:
        return False
    members = stored_versions(stored.get("items") or [])
    left_out = stored_versions((stored.get("map") or {}).get("left_out") or [])
    if not members or not set(members) <= set(current):
        return False
    for item_id, cur in current.items():
        old = members[item_id] if item_id in members else left_out.get(item_id)
        if old is None or not _same_version(cur, old):
            return False
    return True


__all__ = [
    "Group",
    "GroupDoc",
    "GroupState",
    "MIN_SEGMENT_PROMPT",
    "group_from_state",
    "group_item_ids",
    "is_pending",
    "item_titles",
    "load_group",
    "load_state",
    "member_versions",
    "message_items",
    "pointer_rows",
    "segment_headers",
    "segment_version_ok",
    "stored_fits",
    "stored_versions",
]
