"""Re-analysis of an angle map («الخيوط») — ONLY when the user asks for one.

Plan: ``.claude/plans/attachment_agent.md`` §2.3, D8 (a re-analysis runs only on
the user's request — «ماعجبني التحليل اعيد تحليله»), D9 (a NAMED change is an edit,
``edits.py``; a request to redo is this), D16 (the user's critique word for word
PLUS their earlier change requests on that map; billed like a normal map, ≈ 2
points, to the CURRENT turn), D20 (free plans have no map: refused), and the owner's
11-27 rule that a library file carries its whole map into new conversations — a
CARRIED map is re-run for its own group (all its files).

:func:`start_reanalysis` — the attachment agent's ``request_reanalysis`` tool calls
it. Quick checks, then the run is SCHEDULED and the call returns at once (the run
takes ~1–4 min; the router answers right away):

1. the user's words are there (decoded to real text — the agent read masked text);
2. the angle map is on for the user (``angle_map_enabled``), and the plan maps
   (the mapper's own gate, ``runner._plan_allows``: free → refused, D20);
3. the items name ONE map — a member's own map, or the map a library file
   carries (the newest map of another item of the same file, same user); two
   maps → the user is asked which file;
4. nothing maps that group now: in this process (the frame's in-flight runs —
   our own re-analysis says «إعادة التحليل جارية بالفعل», a normal run says it
   is being analysed) and across processes (the ``helper_runs`` lease of every
   member file; a lease this module took carries :data:`LEASE_SUFFIX` in its
   version);
5. the ``helper_runs`` lease is CLAIMED for every member file (one conditional
   write each), so a frame run of a member in any process waits instead of racing
   the re-analysis; then the run is spawned through the frame (``_runtime.spawn``
   under ``angle_map`` for every member and every item carrying the map — the
   router's ``_wait_in_flight("angle_map", …)`` and ``ensure_ready`` see it as an
   angle map in flight) and run by ``_runtime.execute`` (the angle map's
   concurrency limit, the lease renewed every third of it and closed when the run
   ends — success, failure or a raised error alike). Claim and spawn are ONE
   shielded step: a caller's timeout cannot leave a claimed lease without the run
   that closes it, and a failed claim releases the ones before it.

The run (``_reanalyze``): snapshot the map into the edit log (``edits.snapshot_map``,
evidence = the critique — so «تراجع» can restore it) → the earlier requests
(:func:`earlier_requests`) → ``prompts.format_guidance`` → the mapper on the map's
own group (``runner.run_message_angle_map`` with ``guidance`` and ``bill_to`` = the
current conversation + message) → stored by replace (``store.write_angle_map``:
insert, then delete the old — the old map stays readable until the new one is
complete; a redeploy that kills the run leaves the old map). The masking codec is
the turn's (copied into the task): the guidance is encoded with the documents.

Nothing here raises. Reasons are Arabic (the router shows them) and never quote
the user's text.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Any, Iterable, Mapping, Sequence

from . import runner as runner_mod
from .edits import EDITS_TABLE, _evidence, snapshot_map
from .models import AngleMapRunResult, new_result
from .prompts import format_guidance
from .store import ITEMS_TABLE, MAPS_TABLE, _load_map

logger = logging.getLogger(__name__)

#: The helper the re-analysis runs as (its lease rows, its in-flight registry).
HELPER = "angle_map"
#: Appended to the helper's version on a lease this module claims: another process
#: can tell a re-analysis from a normal run. A later frame run sees another version
#: and starts its attempt count over (``status.decide``).
LEASE_SUFFIX = "+reanalysis"
#: The earlier requests in the guidance, at most (the newest kept).
EARLIER_MAX_CHARS = 2_000
#: One earlier request, at most.
EARLIER_LINE_MAX_CHARS = 500
#: The critique, at most (a user message; kept word for word up to this).
CRITIQUE_MAX_CHARS = 6_000

# ── reasons (Arabic; the router shows them; never the user's text) ─────────────
R_NO_REQUEST = "لا يوجد في رسالة المستخدم ما يطلب إعادة التحليل."
R_NO_MAP = "لا توجد خريطة خيوط لهذه الملفات، فلا يوجد تحليل يُعاد."
R_SEVERAL = "الملفات المختارة لها أكثر من خريطة خيوط؛ حدّد الملف المقصود."
R_DISABLED = "الخيوط غير متاحة لهذا الحساب الآن."
R_FREE = "الخيوط وإعادة تحليلها متاحة في الباقات المدفوعة فقط."
R_RUNNING = "إعادة التحليل جارية بالفعل"
R_BUSY = "تحليل الخيوط جارٍ الآن لهذه الملفات؛ اطلب إعادة التحليل بعد أن ينتهي."
R_ERROR = "تعذّر بدء إعادة التحليل الآن؛ حاول بعد قليل."

#: The re-analyses running in this process (to tell them from normal runs).
_JOBS: set[asyncio.Task] = set()
#: Claim-and-schedule steps going (strong references: a caller that timed out no
#: longer holds them).
_STARTING: set[asyncio.Task] = set()


@dataclass(frozen=True)
class ReanalysisStart:
    """What :func:`start_reanalysis` decided. ``reason`` (Arabic) when not ``ok``;
    ``map_id`` — the map being (or that would have been) re-analyzed."""

    ok: bool
    reason: str = ""
    map_id: str | None = None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _data(resp: Any) -> list[dict]:
    data = getattr(resp, "data", None)
    if isinstance(data, dict):
        return [data]
    return [r for r in (data or []) if isinstance(r, dict)]


def _unique(ids: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(i) for i in ids or [] if i))


def _one_line(text: Any) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= EARLIER_LINE_MAX_CHARS else s[: EARLIER_LINE_MAX_CHARS - 1].rstrip() + "…"


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


# ---------------------------------------------------------------------------
# Which map
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Found:
    map_id: str
    #: The map's message (``None``: a group with no message link).
    message_id: str | None
    #: Its members, D-order.
    member_ids: tuple[str, ...]
    #: The caller's items that resolved to this map (members, or items carrying it).
    via_items: tuple[str, ...]
    carried: bool


def _carried_maps(supabase: Any, item_ids: Sequence[str], user_id: str) -> dict[str, str]:
    """``{item_id: map_id}`` for items that are in no map themselves but whose FILE
    is (a «الملفات» file attached here carries its map whole): the newest map of
    another live item of the same file, same user. Raises on a failed read."""
    from shared.files import store as files_store

    files = files_store.file_ids_for_items(supabase, item_ids, user_id=user_id)
    if not files:
        return {}
    siblings = _data(
        supabase.table("workspace_items").select("item_id, file_id")
        .in_("file_id", _unique(files.values())).eq("user_id", user_id).is_("deleted_at", "null").execute()
    )
    file_of = {str(r["item_id"]): str(r["file_id"]) for r in siblings if r.get("item_id") and r.get("file_id")}
    sibling_ids = [i for i in file_of if i not in item_ids]
    if not sibling_ids:
        return {}
    links = _data(supabase.table(ITEMS_TABLE).select("item_id, map_id").in_("item_id", sibling_ids).execute())
    map_of = {str(r["item_id"]): str(r["map_id"]) for r in links if r.get("map_id")}
    if not map_of:
        return {}
    heads = _data(
        supabase.table(MAPS_TABLE).select("map_id, created_at")
        .in_("map_id", _unique(map_of.values())).eq("user_id", user_id).execute()
    )
    made = {str(r["map_id"]): str(r.get("created_at") or "") for r in heads}
    newest: dict[str, tuple[str, str]] = {}  # file → (created_at, map_id)
    for sib, map_id in map_of.items():
        if map_id not in made:
            continue
        key = (made[map_id], map_id)
        fid = file_of[sib]
        if fid not in newest or key > newest[fid]:
            newest[fid] = key
    return {item: newest[fid][1] for item, fid in files.items() if fid in newest}


def _find_map(supabase: Any, item_ids: Sequence[str], user_id: str) -> _Found | str:
    """The ONE map the items name — each item's own map, or the one its file
    carries — or the Arabic reason there is none / more than one. Raises on a
    failed read."""
    links = _data(supabase.table(ITEMS_TABLE).select("item_id, map_id").in_("item_id", list(item_ids)).execute())
    chosen = {str(r["item_id"]): str(r["map_id"]) for r in links if r.get("map_id")}
    rest = [i for i in item_ids if i not in chosen]
    carried: dict[str, str] = _carried_maps(supabase, rest, user_id) if rest else {}
    chosen.update(carried)
    maps = _unique(chosen.values())
    if not maps:
        return R_NO_MAP
    if len(maps) > 1:
        return R_SEVERAL
    stored = _load_map(supabase, maps[0])
    if not stored or str(stored["map"].get("user_id") or "") != user_id or not stored.get("items"):
        return R_NO_MAP
    members = tuple(str(i["item_id"]) for i in stored["items"] if i.get("item_id"))
    return _Found(
        map_id=maps[0],
        message_id=stored["map"].get("message_id") or None,
        member_ids=members,
        via_items=tuple(i for i in item_ids if chosen.get(i) == maps[0]),
        carried=bool(carried),
    )


# ---------------------------------------------------------------------------
# Running now? The lease
# ---------------------------------------------------------------------------


def _busy(keys: Iterable[str]) -> str | None:
    """A run of the angle map in THIS process on any of ``keys``: ours → «جارية
    بالفعل», the frame's → being analysed. ``None`` when nothing runs."""
    from agents.helpers._runtime import inflight

    for key in _unique(keys):
        task = inflight(HELPER, key)
        if task is not None:
            return R_RUNNING if task in _JOBS else R_BUSY
    return None


def _claim(supabase: Any, member_ids: Sequence[str], user_id: str, spec: Any) -> tuple[Any, list, str]:
    """Claim the ``helper_runs`` lease of every member FILE (one per file).
    ``(store, rows, "")``, or ``(store, [], reason)`` when a live lease is held
    elsewhere or another claim won (the rows claimed so far are released). Raises
    on a failed read."""
    from agents.helpers._runtime import utcnow
    from agents.helpers.status import SupabaseHelperRunStore
    from shared.attachments.text_corrections import file_keys_for_items

    store = SupabaseHelperRunStore(supabase)
    by_file: dict[str, str] = {}
    for item, fid in file_keys_for_items(supabase, member_ids, user_id=user_id).items():
        by_file.setdefault(fid, item)
    now = utcnow()
    prev: dict[str, Any] = {}
    for item in by_file.values():
        row = store.get(item, HELPER)
        if row is not None and row.lease_live(now):
            return store, [], (R_RUNNING if str(row.version or "").endswith(LEASE_SUFFIX) else R_BUSY)
        prev[item] = row
    claimed: list = []
    try:
        for item, row in prev.items():
            got = store.claim(
                item_id=item,
                helper=HELPER,
                user_id=user_id,
                prev=row,
                attempt=(row.attempt + 1) if row is not None else 1,
                lease_until=now + timedelta(seconds=spec.lease_s),
                version=f"{spec.version}{LEASE_SUFFIX}",
            )
            if got is None:  # another run took it between the read and the claim
                _release(store, claimed, "reanalysis_not_started")
                return store, [], R_BUSY
            claimed.append(got)
    except BaseException:
        _release(store, claimed, "reanalysis_not_started")  # a failed claim never keeps the earlier ones
        raise
    return store, claimed, ""


def group_in_flight(supabase: Any, item_ids: Iterable[str]) -> bool:
    """An angle-map run — a re-analysis or a normal one — is going on any of
    ``item_ids``: in this process (the frame's in-flight runs) or in another (a
    live ``helper_runs`` lease on the item's file). Undoing a re-analysis snapshot
    is refused meanwhile: the run would delete the restored map when it stores.
    Sync. Raises on a failed read."""
    from agents.helpers._runtime import inflight, utcnow
    from agents.helpers.status import SupabaseHelperRunStore

    ids = _unique(item_ids)
    if any(inflight(HELPER, i) is not None for i in ids):
        return True
    store = SupabaseHelperRunStore(supabase)
    now = utcnow()
    for item in ids:
        row = store.get(item, HELPER)
        if row is not None and row.lease_live(now):
            return True
    return False


def _release(store: Any, rows: Sequence[Any], error: str | None) -> None:
    for row in rows:
        try:
            store.finish(row, status="done", error=error)
        except Exception as exc:  # noqa: BLE001 — the lease then just expires
            logger.warning("angle_mapper: re-analysis lease not released (%s)", type(exc).__name__)


class _GroupLease:
    """The claimed rows of every member file, as ONE ``store`` for
    ``_runtime.execute``: renewed together while the run lives, closed together
    when it ends (whatever the row ``execute`` passes)."""

    def __init__(self, store: Any, rows: Sequence[Any]) -> None:
        self.store = store
        self.rows = list(rows)

    def renew(self, row: Any, lease_until: Any) -> bool:
        kept = False
        for r in self.rows:
            try:
                kept = bool(self.store.renew(r, lease_until)) or kept
            except Exception as exc:  # noqa: BLE001 — the next beat tries again
                logger.warning("angle_mapper: re-analysis lease not renewed (%s)", type(exc).__name__)
        return kept

    def finish(self, row: Any, *, status: str, error: str | None) -> bool:
        done = True
        for r in self.rows:
            try:
                done = bool(self.store.finish(r, status=status, error=error)) and done
            except Exception as exc:  # noqa: BLE001
                logger.warning("angle_mapper: re-analysis lease not closed (%s)", type(exc).__name__)
                done = False
        return done


# ---------------------------------------------------------------------------
# The user's earlier requests on the map (D16)
# ---------------------------------------------------------------------------


def _lineage(supabase: Any, map_id: str, member_ids: Sequence[str], user_id: str) -> list[str]:
    """This map and the maps earlier re-analyses replaced: the user's ``snapshot``
    rows whose members overlap this map's (a snapshot is written on the map being
    replaced — undone or not, its map's edits are this group's history). Raises
    on a failed read."""
    members = set(member_ids)
    rows = _data(
        supabase.table(EDITS_TABLE).select("map_id, item_ids").eq("user_id", user_id).eq("kind", "snapshot").execute()
    )
    out = [str(map_id)]
    for r in rows:
        ids = r.get("item_ids") or []
        if isinstance(ids, (list, tuple)) and members & {str(i) for i in ids}:
            out.append(str(r.get("map_id") or ""))
    return _unique(out)


def earlier_requests(
    supabase: Any,
    map_id: str,
    member_ids: Sequence[str],
    user_id: str,
    *,
    exclude_ids: Iterable[str] = (),
    skip: Iterable[str] = (),
    max_chars: int = EARLIER_MAX_CHARS,
) -> list[str]:
    """The user's earlier change requests on this map (and on the maps earlier
    re-analyses replaced): the ``evidence`` of every logged edit not undone —
    ``undo`` rows left out, an earlier critique (``snapshot``) kept — oldest first,
    each once (whitespace / case folded; ``skip``: texts already in the guidance,
    the critique), one line each (cut at :data:`EARLIER_LINE_MAX_CHARS`). Over
    ``max_chars`` the OLDEST are dropped (a later request may supersede them).
    ``exclude_ids``: log rows to leave out (this run's own snapshot). ``[]`` on a
    failed read (logged). Never raises."""
    try:
        lineage = _lineage(supabase, map_id, member_ids, user_id)
        rows = _data(
            supabase.table(EDITS_TABLE).select("id, kind, evidence, created_at")
            .in_("map_id", lineage).eq("user_id", user_id).is_("undone_at", "null")
            .order("created_at").execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: earlier requests not read for map %s (%s)", map_id, type(exc).__name__)
        return []
    excluded = {str(i) for i in exclude_ids if i}
    seen = {_norm(s) for s in skip if _norm(s)}
    lines: list[str] = []
    for r in rows:
        if str(r.get("kind") or "") == "undo" or str(r.get("id") or "") in excluded:
            continue
        line = _one_line(r.get("evidence"))
        key = _norm(line)
        if not key or key in seen:
            continue
        seen.add(key)
        lines.append(line)
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        total += len(line) + 3  # "- " and the newline
        if total > max_chars:
            break
        kept.append(line)
    return kept[::-1]


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Job:
    map_id: str
    map_message_id: str | None
    member_ids: tuple[str, ...]
    user_id: str
    conversation_id: str | None
    message_id: str | None
    critique: str  # real text


def _group_of(supabase: Any, job: _Job) -> tuple[str | None, list[str]]:
    """The map's own group (``group.group_item_ids`` of its first member — its
    message's attachments), every member kept. On a failed read: the map's message
    and members."""
    from .group import group_item_ids

    try:
        message_id, ids = group_item_ids(supabase, job.member_ids[0])
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: re-analysis group not read for map %s (%s)", job.map_id, type(exc).__name__)
        return job.map_message_id, list(job.member_ids)
    ids = list(ids) + [m for m in job.member_ids if m not in ids]
    return job.map_message_id or message_id, ids


async def _reanalyze(job: _Job, ctx: Any) -> AngleMapRunResult:
    """Snapshot → earlier requests → the mapper with guidance, billed to the
    current turn. Never raises (cancellation aside): a failed snapshot stops here
    (``skipped_no_snapshot`` — the old map is kept and nothing is billed)."""
    supabase = ctx.supabase
    snap = await asyncio.to_thread(
        snapshot_map, supabase, job.map_id, job.critique,
        user_id=job.user_id, conversation_id=job.conversation_id, message_id=job.message_id,
    )
    if not snap.ok:
        res = new_result(job.member_ids[0])
        res.outcome, res.error = "skipped_no_snapshot", "snapshot"
        logger.warning("angle_mapper: re-analysis of map %s stopped — no snapshot", job.map_id)
        return res
    earlier = await asyncio.to_thread(
        earlier_requests, supabase, job.map_id, job.member_ids, job.user_id,
        exclude_ids=(snap.edit_id,), skip=(job.critique,),
    )
    guidance = format_guidance(job.critique, earlier)
    message_id, ids = await asyncio.to_thread(_group_of, supabase, job)
    res = await runner_mod.run_message_angle_map(
        supabase, message_id, ids,
        guidance=guidance,
        bill_to=runner_mod.BillTo(conversation_id=job.conversation_id, message_id=job.message_id),
    )
    logger.info(
        "angle_mapper: re-analysis of map %s → %s (new map %s, %d earlier requests)",
        job.map_id, res.outcome, res.map_id or "-", len(earlier),
    )
    return res


def _schedule(supabase: Any, job: _Job, spec: Any, store: Any, rows: Sequence[Any], keys: Sequence[str]) -> None:
    """Spawn the run through the frame: in flight under every key, the angle map's
    concurrency limit, the lease renewed and closed by ``execute``."""
    from agents.helpers._runtime import execute, spawn
    from agents.helpers.spec import HelperContext

    ctx = HelperContext(
        helper=HELPER,
        supabase=supabase,
        user_id=job.user_id,
        conversation_id=job.conversation_id,
        message_id=job.message_id,
        item_id=job.member_ids[0],
        attempt=getattr(rows[0], "attempt", 1) if rows else 1,
    )
    coro = execute(
        spec, ctx, partial(_reanalyze, job),
        check_applicable=False, store=_GroupLease(store, rows), row=rows[0] if rows else None,
    )
    task = spawn(HELPER, job.member_ids[0], coro, also=[k for k in keys if k != job.member_ids[0]])
    _JOBS.add(task)
    task.add_done_callback(_JOBS.discard)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def start_reanalysis(
    supabase: Any,
    *,
    item_ids: Sequence[str],
    user_id: str,
    conversation_id: str,
    message_id: str | None,
    critique: str,
) -> ReanalysisStart:
    """Check, claim and SCHEDULE a re-analysis of the map ``item_ids`` name (module
    docstring), then return at once. ``critique`` — the user's words asking for it
    (masked text is decoded); ``conversation_id`` / ``message_id`` — the CURRENT
    turn, which the run is billed to. Never raises (cancellation aside)."""
    try:
        return await _start(
            supabase, item_ids=item_ids, user_id=user_id, conversation_id=conversation_id,
            message_id=message_id, critique=critique,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: re-analysis not started (%s)", type(exc).__name__)
        return ReanalysisStart(False, R_ERROR)


async def _start(
    supabase: Any,
    *,
    item_ids: Sequence[str],
    user_id: str,
    conversation_id: str,
    message_id: str | None,
    critique: str,
) -> ReanalysisStart:
    uid = str(user_id or "")
    ids = _unique(item_ids)
    words = _evidence(critique)[:CRITIQUE_MAX_CHARS].strip()
    if not words:
        return ReanalysisStart(False, R_NO_REQUEST)
    if not uid or not ids:
        return ReanalysisStart(False, R_NO_MAP)
    if not runner_mod.angle_map_enabled(uid):
        return ReanalysisStart(False, R_DISABLED)
    if not await runner_mod._plan_allows(supabase, uid):
        return ReanalysisStart(False, R_FREE)

    found = await asyncio.to_thread(_find_map, supabase, ids, uid)
    if isinstance(found, str):
        return ReanalysisStart(False, found)
    keys = _unique([*found.member_ids, *found.via_items])
    busy = _busy(keys)
    if busy:
        return ReanalysisStart(False, busy, found.map_id)

    from agents.helpers.registry import get_spec

    spec = get_spec(HELPER)
    if spec is None:
        return ReanalysisStart(False, R_ERROR, found.map_id)
    job = _Job(
        map_id=found.map_id,
        map_message_id=found.message_id,
        member_ids=found.member_ids,
        user_id=uid,
        conversation_id=str(conversation_id) if conversation_id else None,
        message_id=str(message_id) if message_id else None,
        critique=words,
    )
    # Claim + schedule as ONE step a caller's timeout cannot cut in half: shielded,
    # so a cancelled caller leaves it running to the end — a claimed lease always
    # gets the run that closes it (or is released).
    step = asyncio.get_running_loop().create_task(_claim_and_schedule(supabase, job, spec, keys))
    _STARTING.add(step)
    step.add_done_callback(_STARTING.discard)
    reason = await asyncio.shield(step)
    if reason:
        return ReanalysisStart(False, reason, found.map_id)
    logger.info(
        "angle_mapper: re-analysis of map %s scheduled (%d members%s)",
        found.map_id, len(found.member_ids), ", carried" if found.carried else "",
    )
    return ReanalysisStart(True, "", found.map_id)


async def _claim_and_schedule(supabase: Any, job: _Job, spec: Any, keys: Sequence[str]) -> str:
    """Claim every member file's lease (in a thread: ``_claim`` releases its own
    claims when it fails), then spawn the run that closes them; a spawn that fails
    releases them. ``""`` = scheduled, else the Arabic reason. Never raises."""
    try:
        store, rows, reason = await asyncio.to_thread(_claim, supabase, job.member_ids, job.user_id, spec)
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: re-analysis lease not claimed for map %s (%s)", job.map_id, type(exc).__name__)
        return R_ERROR
    if reason:
        return reason
    try:
        _schedule(supabase, job, spec, store, rows, keys)
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: re-analysis of map %s not scheduled (%s)", job.map_id, type(exc).__name__)
        await asyncio.to_thread(_release, store, rows, "reanalysis_not_started")
        return R_ERROR
    return ""


__all__ = [
    "CRITIQUE_MAX_CHARS",
    "EARLIER_MAX_CHARS",
    "LEASE_SUFFIX",
    "R_BUSY",
    "R_DISABLED",
    "R_ERROR",
    "R_FREE",
    "R_NO_MAP",
    "R_NO_REQUEST",
    "R_RUNNING",
    "R_SEVERAL",
    "ReanalysisStart",
    "earlier_requests",
    "group_in_flight",
    "start_reanalysis",
]
