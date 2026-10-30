"""WI preview lock — the backend half (``.claude/plans/wi_preview_lock.md`` §5).

The decision and the cut live in ``shared/workspace/preview_lock.py`` (shared
with the router's ``unfold_workspace_item``, so both see byte-identical
previews). This module applies them to what leaves the backend:

* :func:`previews_for` — rows → ``{item_id: WiPreview}`` for the LOCKED ones.
  ``api/workspace.py::_to_response`` is the one choke point that turns a
  ``WiPreview`` into a truncated ``content_md`` + ``preview`` block, so every
  workspace route (lists, get, PATCH / visibility / feedback / finalize
  responses) ships the same preview.
* :func:`refuse_if_locked` — the 402 for content edits (§1.3) and share /
  save-as-blog (§1.2, §9.4).
* :func:`filter_references` — the citation list of a locked item: only the
  sources cited in the visible part, plus a count of the rest (§9.3).
* :func:`note_served` — the server-side ``wi_preview_served`` analytics event
  (§8), once per item.

Flag off — or no row that could lock — returns before any extra DB read: no
paid lookup, no reference query, nothing in the response changes.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services.demo_service import is_demo_conversation, is_demo_item
from shared.db.run import run_db
from shared.workspace.preview_lock import (
    LockPolicy,
    PreviewCut,
    cited_numbers,
    configured_lock_from,
    is_locked,
    preview_cut,
    resolve_lock_policy,
)

logger = logging.getLogger(__name__)

# HTTP 402 like every other "your plan does not include this" answer. Neutral
# wording, no call to buy: an old app build can show this verbatim.
MSG_WI_PREVIEW_EDIT_LOCKED = (
    "هذا المستند معروض معاينةً في الباقة المجانية، وتعديل نصّه متاح في الباقات المدفوعة."
)
MSG_WI_PREVIEW_SHARE_LOCKED = (
    "هذا المستند معروض معاينةً في الباقة المجانية، ونشره ومشاركته متاحان في الباقات المدفوعة."
)
MSG_WI_PREVIEW_REF_LOCKED = "هذا المرجع في الجزء المقفل من المستند."

# Writer metadata that can carry the hidden body's substance. ``notes`` (the
# writer's own remarks, up to ~2k chars) and ``citations_used`` (source-WI
# picks) are rendered by no client, so a locked item simply loses them;
# ``references`` (the provenance overlay NoteEditor reads) is filtered to the
# visible citations.
_METADATA_DROPPED_WHEN_LOCKED = ("notes", "citations_used")


@dataclass(frozen=True)
class WiPreview:
    """One locked item: the cut, and which citations it keeps."""

    cut: PreviewCut
    visible_ns: frozenset[int]
    hidden_ref_count: int


def _is_demo(row: Mapping[str, Any]) -> bool:
    return is_demo_item(str(row.get("item_id") or "")) or is_demo_conversation(
        row.get("conversation_id")
    )


def _could_lock(row: Mapping[str, Any], lock_from) -> bool:
    """Every §3 clause except the paid check — the paid lookup only runs when
    at least one row passes this."""
    return is_locked(row, LockPolicy(lock_from=lock_from), is_demo=_is_demo(row))


def _used_ref_numbers(supabase: SupabaseClient, item_ids: list[str]) -> dict[str, set[int]]:
    """``{item_id: {n, …}}`` of the USED references, one batched query.

    Fail-soft to "no references": the count line then says nothing rather than
    failing a read that is otherwise fine.
    """
    out: dict[str, set[int]] = {iid: set() for iid in item_ids}
    if not item_ids:
        return out
    try:
        # ``wi_id`` is the owning item; ``item_id`` on this table is the cited
        # source row (references_service._load_references).
        res = (
            supabase.table("workspace_item_references")
            .select("wi_id, n")
            .in_("wi_id", item_ids)
            .eq("used", True)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("wi_preview: used-reference read failed: %s", exc)
        return out
    for ref in getattr(res, "data", None) or []:
        iid = str(ref.get("wi_id") or "")
        try:
            n = int(ref.get("n"))
        except (TypeError, ValueError):
            continue
        if iid in out:
            out[iid].add(n)
    return out


def _build(supabase: SupabaseClient, rows: list[Mapping[str, Any]]) -> dict[str, WiPreview]:
    ids = [str(r.get("item_id")) for r in rows if r.get("item_id")]
    used = _used_ref_numbers(supabase, ids)
    previews: dict[str, WiPreview] = {}
    for row in rows:
        iid = str(row.get("item_id") or "")
        if not iid:
            continue
        cut = preview_cut(row.get("content_md"))
        visible = cited_numbers(cut.visible)
        previews[iid] = WiPreview(
            cut=cut,
            visible_ns=visible,
            hidden_ref_count=len(used.get(iid, set()) - visible),
        )
    return previews


async def previews_for(
    supabase: SupabaseClient, rows: Iterable[Mapping[str, Any]]
) -> dict[str, WiPreview]:
    """``{item_id: WiPreview}`` for every row that is locked for its owner.

    All rows of one call belong to one owner (the caller — every workspace
    read is owner-filtered; demo items are exempt), so the paid lookup runs at
    most once, and only when some row could lock at all.
    """
    lock_from = configured_lock_from()
    if lock_from is None:
        return {}
    candidates = [r for r in rows if _could_lock(r, lock_from)]
    if not candidates:
        return {}
    owner = str(candidates[0].get("user_id") or "")
    policy = await resolve_lock_policy(supabase, owner)
    if policy is None:
        return {}
    locked = [
        r for r in candidates
        if str(r.get("user_id") or "") == owner and is_locked(r, policy, is_demo=_is_demo(r))
    ]
    if not locked:
        return {}
    return await run_db(_build, supabase, locked)


async def preview_for(
    supabase: SupabaseClient, row: Mapping[str, Any]
) -> Optional[WiPreview]:
    """:func:`previews_for` for one row."""
    return (await previews_for(supabase, [row])).get(str(row.get("item_id") or ""))


async def policy_for_caller(
    supabase: SupabaseClient, auth_id: str
) -> Optional[LockPolicy]:
    """The caller's :class:`LockPolicy` — for write paths that check one row
    inside a sync service. Flag off returns before any lookup."""
    if configured_lock_from() is None:
        return None
    from backend.app.services.case_service import get_user_id

    user_id = await run_db(get_user_id, supabase, auth_id)
    return await resolve_lock_policy(supabase, user_id)


def is_row_locked(row: Mapping[str, Any], policy: Optional[LockPolicy]) -> bool:
    """:func:`shared.workspace.preview_lock.is_locked` with the demo exemption."""
    return is_locked(row, policy, is_demo=_is_demo(row))


def locked_metadata(metadata: Any, preview: WiPreview) -> dict:
    """The metadata a locked item may ship (see ``_METADATA_DROPPED_WHEN_LOCKED``)."""
    md = dict(metadata) if isinstance(metadata, Mapping) else {}
    for key in _METADATA_DROPPED_WHEN_LOCKED:
        md.pop(key, None)
    refs = md.get("references")
    if isinstance(refs, list):
        kept = []
        for entry in refs:
            try:
                n = int(entry.get("n")) if isinstance(entry, Mapping) else None
            except (TypeError, ValueError):
                n = None
            if n is not None and n in preview.visible_ns:
                kept.append(entry)
        md["references"] = kept
    return md


async def refuse_if_locked(
    supabase: SupabaseClient, row: Mapping[str, Any], *, detail: str
) -> None:
    """402 ``WI_PREVIEW_LOCKED`` when ``row`` is preview-locked for its owner."""
    if await preview_for(supabase, row) is not None:
        raise LunaHTTPException(
            status_code=402, code=ErrorCode.WI_PREVIEW_LOCKED, detail=detail
        )


def filter_references(
    references: list[dict], preview: Optional[WiPreview]
) -> tuple[list[dict], int]:
    """``(references, hidden_count)`` — a locked item keeps only the sources its
    visible part cites (§9.3); an open one keeps everything and hides none."""
    if preview is None:
        return references, 0
    kept = []
    for ref in references:
        try:
            n = int(ref.get("n"))
        except (TypeError, ValueError):
            continue
        if n in preview.visible_ns:
            kept.append(ref)
    return kept, preview.hidden_ref_count


# ── analytics: wi_preview_served (§8) ───────────────────────────────────────
#
# The app has no analytics client, so the server counts the cross-platform
# "a free user met the wall": ONE event per locked item, ever (items belong to
# one user). Dedup is a Redis SET NX when Redis is up, else an in-process set
# (per worker — a restart can count an item twice; accepted). Fire-and-forget:
# never on the response path, never raises.

_SERVED_KEY = "analytics:wi_preview_served:{item_id}"
_SERVED_TTL_S = 180 * 86_400          # = the analytics_events retention
_served_local: set[str] = set()
_SERVED_LOCAL_MAX = 20_000
_pending: set[asyncio.Task] = set()


async def _first_serve(redis: Any, item_id: str) -> bool:
    if redis is not None:
        try:
            return bool(await redis.set(
                _SERVED_KEY.format(item_id=item_id), "1", nx=True, ex=_SERVED_TTL_S
            ))
        except Exception as exc:  # noqa: BLE001
            logger.debug("wi_preview_served: redis dedup failed: %s", exc)
    if item_id in _served_local:
        return False
    if len(_served_local) >= _SERVED_LOCAL_MAX:
        _served_local.clear()
    _served_local.add(item_id)
    return True


async def _emit_served(
    supabase: SupabaseClient,
    redis: Any,
    rows: list[Mapping[str, Any]],
    previews: dict[str, WiPreview],
    platform: str,
) -> None:
    from backend.app.services import analytics_service

    try:
        events = []
        for row in rows:
            iid = str(row.get("item_id") or "")
            preview = previews.get(iid)
            if preview is None or not await _first_serve(redis, iid):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            events.append(analytics_service.build_server_event_row(
                "wi_preview_served",
                user_id=str(row.get("user_id") or "") or None,
                props={
                    "item_id": iid,
                    "kind": str(row.get("kind") or ""),
                    "subtype": str(metadata.get("subtype") or ""),
                    "platform": platform,
                    "shown_chars": preview.cut.shown_chars,
                    "total_chars": preview.cut.total_chars,
                },
            ))
        if events:
            await run_db(analytics_service.insert_events, supabase, events)
    except Exception as exc:  # noqa: BLE001
        logger.warning("wi_preview_served: emit failed: %s", exc)


def note_served(
    supabase: SupabaseClient,
    redis: Any,
    rows: list[Mapping[str, Any]],
    previews: dict[str, WiPreview],
    *,
    platform: Optional[str],
) -> None:
    """Schedule the ``wi_preview_served`` events for ``previews`` (no-op if none)."""
    if not previews:
        return
    p = (platform or "").strip().lower()
    task = asyncio.create_task(_emit_served(
        supabase, redis, list(rows), dict(previews), p if p in ("ios", "android") else "web",
    ))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


__all__ = [
    "MSG_WI_PREVIEW_EDIT_LOCKED",
    "MSG_WI_PREVIEW_REF_LOCKED",
    "MSG_WI_PREVIEW_SHARE_LOCKED",
    "WiPreview",
    "filter_references",
    "locked_metadata",
    "is_row_locked",
    "note_served",
    "policy_for_caller",
    "preview_for",
    "previews_for",
    "refuse_if_locked",
]
