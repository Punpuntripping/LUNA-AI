"""Runner for the angle mapper (Layer 4 Memory, model tier_2, slot ``angle_mapper``).

ONE MAP PER MESSAGE GROUP (angles-v4+, plan D16–D17): the attachments a user sends
in one message are mapped together in one model call, so an angle can tie a
section of one document to a section of another. A message with one file is a
group of one — the same code path. Who is in a group: ``group.py``.

Two entry points:

- :func:`run_angle_mapper` ``(supabase, item_id)`` — what the helper frame calls,
  per item, after that item's segmenter. It resolves the item's group and makes
  sure the group is mapped ONCE;
- :func:`run_message_angle_map` ``(supabase, message_id, item_ids)`` — the group
  run itself, on the items a caller names (the preview tool).

**One run per group — the trigger rule (why this pattern).** The frame starts the
helper for each member when THAT member's segmenter run ends — whoever started that
run: a turn (``TurnHelpers._after``) or a consumer's ``ensure_ready``
(``agents/helpers/status.py``, ``CONSUMER_RUN_DEPENDENTS``) — and holds a lease per
item (``helper_runs``, under the item's file), not per group. So the group run is
decided inside the runner, the way the segmenter's bundles decide theirs
(``bundles.py`` / ``runner._BUNDLE_TASKS``). Every decision before the model call
is made from HEADERS only (``group.load_state``: pointer columns, lengths, segment-map
headers — no text, no segments, no paragraphs); the text is read once, by the run
that calls the model (``group.group_from_state``):

1. **The last member to be segmented runs the group.** A member's run looks at the
   whole message: when another candidate still has no usable segment map and its
   summary or segmenter is still running (here — the frame's in-flight registry —
   or in another process — a live ``helper_runs`` segmenter lease), it returns
   ``skipped_group_pending`` at once (no model call, not a failure). That other
   member's own run, started when its segmenter ends, finds every map in place.
   This needs no waiting and no leader election: the member whose segment map is
   written LAST always sees every map (its run starts after its own map is
   written), and a member that is never going to get a map (its segmenter failed,
   or skipped it) is simply not a member — whoever runs after it maps the rest.
   A deterministic leader would have had to wait (holding a slot and a lease) for
   members that may never arrive.
2. **Members that see "all ready" at the same moment join one run.** Bundle members
   finish segmenting together, so several runs can find every map at once. In
   this process they share ONE task per group (``_GROUP_TASKS``, keyed by the
   message): the first creates it, the others await it (shielded) and return
   ``joined_group`` — never a failure, so the frame does not count them; the
   member that started the run carries its real outcome (``stored`` / ``failed_*``).
   Across processes there is no group lock — the segmenter's bundles have none
   either; a turn runs its whole helper chain in one process. Two processes
   mapping one group at once would pay twice; the store keeps one map
   (``store.write_angle_map``: the second publisher loses cleanly).
3. **A fresh stored map is never redone.** Before a run: the group's stored map fits
   its header-usable attachments (``group.stored_fits``: the same members with the
   same lengths and segment versions, plus the attachments the map left out at the
   versions they were left out at) → ``skipped_fresh`` (and the frame's ``load`` —
   ``store.load_fresh_angle_map_for_item``, the same check — reports it ready
   before the runner is even called). A member that arrives LATER (its segment map
   lands after the group was mapped, or a member is re-segmented) makes the stored
   map stale, and that member's run maps the group again — one run. A member that
   joins a run which stored a different member set (the group changed while the
   run was going) resolves the group again and, when the map just stored does not
   fit it, runs once more.

**A failed group run** (``failed_*`` / ``error``) is not retried by itself: there is
no sweep. The group is mapped again when the segmenter of ANY member ends again
(that member's run finds no fresh map and runs it), or — otherwise — when the
extended-search planner asks for the map through ``ensure_ready`` (phase 2, not
built yet). The frame counts only failures in a row against ``max_attempts``
(``HelperSpec.attempts_count_failures_only``), so skips, joins and re-maps of a
stale group never use them up.

**Never raises** (cancellation aside) — every path returns an
:class:`AngleMapRunResult` whose ``outcome`` says what happened:

``stored`` · ``preview`` · ``joined_group`` (another member's run of this group,
in this process, was joined) · ``skipped_group_pending`` (another candidate is
still being segmented — its run maps the group) · ``skipped_fresh`` (the group's
stored map still fits) · ``skipped_not_found`` · ``skipped_not_attachment`` ·
``skipped_disabled`` · ``skipped_too_large`` (the group's text is over
:data:`MAX_GROUP_CHARS`) · a member reason when the group has NO member (the
item's own: ``skipped_no_map`` · ``skipped_old_segments`` (older than seg-v5.2: no
owners) · ``skipped_paragraph_rule`` (its paragraphs can no longer be rebuilt) ·
``skipped_stale`` (its text changed since its segment map) · ``skipped_no_sections``)
· ``failed_llm`` · ``failed_invalid`` (incl. every angle without a section) ·
``failed_store`` · ``error``.

Flow of a group run: the members' text (``group.group_from_state``) → the user's
words once (the segmenter's rule, on D1) → the titles of the message's attachments
left out (angles-v5: «sent but could not be read») → the message
(``prompts.build_user_message``: every document, numbered from P001 each) → ONE
masking codec for the whole group (وضع السرية; new fakes persisted BEFORE the model
reads them) → one call (``CALL_TIMEOUT_S``, one re-attempt; the agent's output
validator sends unknown refs and angles without a section back once) → unknown refs
still left are dropped, angles still without a section are dropped and counted
(``angles_without_sections``; none left → ``failed_invalid``); gaps fixed in code
(``equal_value_gaps_dropped``, ``ocr_gaps_merged``) → coverage: core
sections of every document in no angle / ``context`` / ``no_angle`` get ONE repair
call on the first call's history (its output under the same rules) → decode →
still unplaced → ``no_angle`` «لم يُصنَّف» (a second question still left is kept and
counted: ``compound_questions_after_retry``) → store (``store.write_angle_map``, with
the attachments the text level left out), or return it (``preview``).

Cost: every model call lands in ``llm_calls`` (agent ``angle_mapper``) through the
tracking hook (``AgentSpan.record_run``), in ONE scope the group run always opens,
keyed to the GROUP — its conversation, user and message (``_run_group``; scopes
nest: an open turn scope gets nothing). Never the turn's scope: the turn that
happens to start the run may be another message's (an abandoned upload, a file
OCR'd later); a joined run's calls land once, in the group's scope. A preview runs
DETACHED from any scope: no ledger row, no fakes saved, nothing stored.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.usage import RunUsage

from agents.memory.attachment_segmenter.bundles import segmenter_applies_to_item
from agents.memory.attachment_segmenter.runner import (
    _codec_for,
    _dec,
    _enc,
    _load_user_words,
    _persist_fakes,
)
from agents.utils.angle_map_flags import angle_map_enabled
from agents.utils.tracking import track_stage
from agents.utils.usage_sink import collect_llm_calls, detached

from .agent import ANGLE_MAPPER_LIMITS, MODEL_SLOT, create_angle_mapper
from .deps import AngleMapperDeps
from .group import (
    Group,
    GroupState,
    group_from_state,
    group_item_ids,
    item_titles,
    load_group,
    load_state,
    pointer_rows,
    segment_version_ok,
    stored_fits,
)
from .models import AngleMap, AngleMapRunResult, new_result
from .prompts import PROMPT_VERSION, PromptDocument, build_instructions, build_user_message, repair_message
from .validate import (
    core_refs,
    drop_equal_value_gaps,
    drop_invalid_refs,
    drop_sectionless_angles,
    mark_unplaced,
    merge_ocr_gaps,
    multi_question_angles,
    qualify_refs,
    section_refs,
    unplaced_core,
)

logger = logging.getLogger(__name__)

#: One call's ceiling. Tested (angles-v2, 10 single files): 49–150 s; a message
#: group's call reads up to ~165k chars (the largest real group) ≈ 3–4 min.
CALL_TIMEOUT_S = 480.0
#: Attempts of the map call: the first + one re-attempt. The repair call gets one.
MAX_ATTEMPTS = 2
#: A group's whole text above this is not mapped (``skipped_too_large``). The
#: largest real message group seen (prod, 2026-11-22) is 165k characters.
MAX_GROUP_CHARS = 400_000

#: In-process group runs, keyed by ``Group.key``: members of one group that start
#: at the same moment join ONE run. Preview runs never join.
_GROUP_TASKS: dict[str, asyncio.Task] = {}

#: Reasons an id is left out that do NOT make it an unread file of the message
#: (deleted / gone, not an attachment, another user's): never named to the model.
_NOT_UNREAD = frozenset({"skipped_not_found", "skipped_not_attachment", "skipped_other_user"})


# ---------------------------------------------------------------------------
# The helper frame's applies_to
# ---------------------------------------------------------------------------


def angle_mapper_applies_to_item(supabase: Any, item_id: str) -> bool:
    """The helper frame's ``applies_to``: an attachment the segmenter maps (over
    5,000 characters, or in its message's qualifying bundle — any document type,
    D16). Everything else (the group, its members' maps, pending members, a fresh
    map) is the runner's call, so the member whose segmenter ends last always gets
    to run the group — even when its own segmentation failed. Reads only. A failed
    read = ``False``. Never raises."""
    try:
        return bool(segmenter_applies_to_item(supabase, str(item_id)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_mapper: applies_to failed for %s (%s)", item_id, type(exc).__name__)
        return False


# ---------------------------------------------------------------------------
# Span helpers (the LUNA_TRACK_DISABLE handle has no record_run / set_outcome)
# ---------------------------------------------------------------------------


def _record(span: Any, result: Any) -> None:
    try:
        span.record_run(result, slot=MODEL_SLOT)
    except Exception:  # noqa: BLE001
        pass


def _set(span: Any, **attrs: Any) -> None:
    try:
        span.set(**attrs)
    except Exception:  # noqa: BLE001
        pass


def _outcome(span: Any, outcome: str) -> None:
    try:
        span.set_outcome(outcome)
    except Exception:  # noqa: BLE001
        pass


def _priced(result: Any) -> tuple[float, str]:
    """``(cost_usd, model)`` exactly as the ledger row prices it."""
    try:
        from agents.utils.tracking import _usage_attrs

        attrs = _usage_attrs(result, slot=MODEL_SLOT)
        return float(attrs.get("cost_usd") or 0.0), str(attrs.get("model_used") or "")
    except Exception:  # noqa: BLE001
        return 0.0, ""


# ---------------------------------------------------------------------------
# One model call with its re-attempts
# ---------------------------------------------------------------------------


@dataclass
class _CallOutcome:
    output: AngleMap | None
    messages: list | None
    attempts: int
    usage: RunUsage
    cost_usd: float
    model: str = ""
    failure: str = ""   # "llm" | "invalid" when output is None
    error: str = ""


async def _call(
    instructions: str,
    message: str,
    deps: AngleMapperDeps,
    *,
    attempts: int,
    history: list | None = None,
) -> _CallOutcome:
    total = RunUsage()
    cost = 0.0
    failures: list[str] = []
    error = ""
    for attempt in range(1, attempts + 1):
        agent = create_angle_mapper(instructions)
        spent = RunUsage()
        with track_stage(
            "angle_mapper",
            conversation_id=deps.conversation_id,
            case_id=deps.case_id,
            agent_family="memory",
            item_id=deps.item_id,
            message_id=deps.message_id,
            docs=len(deps.item_ids),
            call=deps.call,
            attempt=attempt,
        ) as span:
            try:
                result = await asyncio.wait_for(
                    agent.run(
                        message,
                        deps=deps,
                        message_history=history,
                        usage_limits=ANGLE_MAPPER_LIMITS,
                        usage=spent,
                    ),
                    timeout=CALL_TIMEOUT_S,
                )
            except asyncio.CancelledError:
                _record(span, SimpleNamespace(usage=lambda: spent))
                raise
            except Exception as exc:  # noqa: BLE001
                partial = SimpleNamespace(usage=lambda: spent)
                _record(span, partial)  # a failed attempt still bills what it used
                cost += _priced(partial)[0]
                total.incr(spent)
                _set(span, **{"error.type": type(exc).__name__})
                _outcome(span, "error")
                failures.append("invalid" if isinstance(exc, UnexpectedModelBehavior) else "llm")
                error = type(exc).__name__
                logger.warning(
                    "angle_mapper: %s call failed message=%s attempt=%d (%s)",
                    deps.call, deps.message_id or deps.item_id, attempt, error,
                )
                continue
            _record(span, result)
            price, model = _priced(result)
            cost += price
            total.incr(spent)
            return _CallOutcome(result.output, result.all_messages(), attempt, total, cost, model)
    failure = "invalid" if failures and all(f == "invalid" for f in failures) else "llm"
    return _CallOutcome(None, None, attempts, total, cost, failure=failure, error=error)


# ---------------------------------------------------------------------------
# Decode (store-real: free text the model wrote can carry masking fakes)
# ---------------------------------------------------------------------------


def _decode_map(codec: Any, amap: AngleMap) -> AngleMap:
    if codec is None:
        return amap

    def d(text: str) -> str:
        return _dec(codec, text)

    client = amap.client
    return amap.model_copy(update={
        "client": client.model_copy(update={
            "party": d(client.party),
            "basis": d(client.basis),
            "by_document": [e.model_copy(update={"party": d(e.party)}) for e in client.by_document],
        }),
        "angles": [
            a.model_copy(update={
                "for_party": d(a.for_party),
                "title": d(a.title),
                "argument": d(a.argument),
                "legal_question": d(a.legal_question),
                "weakness": d(a.weakness),
            })
            for a in amap.angles
        ],
        "gaps": [g.model_copy(update={"issue": d(g.issue)}) for g in amap.gaps],
        "no_angle": [n.model_copy(update={"why": d(n.why)}) for n in amap.no_angle],
        "unrelated_documents": [u.model_copy(update={"why": d(u.why)}) for u in amap.unrelated_documents],
    })


# ---------------------------------------------------------------------------
# Stats / span
# ---------------------------------------------------------------------------


def _absorb(stats: dict, call: _CallOutcome) -> None:
    usage = call.usage
    stats["input_tokens"] += int(usage.input_tokens or 0)
    stats["output_tokens"] += int(usage.output_tokens or 0)
    stats["reasoning_tokens"] += int((usage.details or {}).get("reasoning_tokens", 0) or 0)
    stats["cached_tokens"] += int(getattr(usage, "cache_read_tokens", 0) or 0)
    stats["requests"] = stats.get("requests", 0) + int(usage.requests or 0)
    stats["cost_usd"] = round(stats["cost_usd"] + call.cost_usd, 6)
    if call.model:
        stats["model"] = call.model


def _describe_state(result: AngleMapRunResult, state: GroupState) -> None:
    """What the headers say (no text read): the header-usable attachments and the
    rest with their reasons."""
    st = result.stats
    result.message_id = state.message_id
    result.item_ids = list(state.item_ids)
    result.excluded = dict(state.excluded)
    st["docs"] = len(state.item_ids)
    st["total_chars"] = state.total_chars
    if state.pending:
        st["pending"] = list(state.pending)


def _describe_group(result: AngleMapRunResult, group: Group) -> None:
    st = result.stats
    result.message_id = group.message_id
    result.item_ids = group.item_ids
    result.excluded = dict(group.excluded)
    st["docs"] = len(group.docs)
    st["total_chars"] = group.total_chars
    st["doc_types"] = [d.doc_type for d in group.docs]
    st["sections"] = len(group.sections)
    st["core_sections"] = len(core_refs(group.sections))
    st["paragraphs"] = sum(len(d.paragraphs) for d in group.docs)
    st["segment_prompt_versions"] = sorted({d.smap.prompt_version for d in group.docs})
    if group.left_out:
        st["left_out"] = len(group.left_out)
    if group.pending:
        st["pending"] = list(group.pending)


def _finish_span(span: Any, result: AngleMapRunResult, t0: float) -> None:
    st = result.stats
    st["secs"] = round(time.perf_counter() - t0, 1)
    _set(
        span,
        message_id=result.message_id,
        docs=st.get("docs"),
        doc_types=st.get("doc_types") or None,
        total_chars=st.get("total_chars"),
        excluded=len(result.excluded),
        sections=st.get("sections"),
        core_sections=st.get("core_sections"),
        model=st.get("model") or None,
        tokens_in=st.get("input_tokens"),
        tokens_out=st.get("output_tokens"),
        tokens_reasoning=st.get("reasoning_tokens"),
        cost_usd=st.get("cost_usd"),
        ms=int((time.perf_counter() - t0) * 1000),
        attempts=st.get("attempts"),
        repair_used=st.get("repair_used"),
        angle_count=st.get("angle_count"),
        cross_doc_angles=st.get("cross_doc_angles"),
        ocr_suspect_angles=st.get("ocr_suspect_angles"),
        kind_mix=st.get("kind_mix"),
        unplaced_before_repair=st.get("unplaced_before_repair"),
        unplaced=len(result.unplaced),
        invalid_refs_dropped=st.get("invalid_refs_dropped"),
        angles_without_sections=st.get("angles_without_sections"),
        compound_questions_after_retry=st.get("compound_questions_after_retry"),
        equal_value_gaps_dropped=st.get("equal_value_gaps_dropped"),
        ocr_gaps_merged=st.get("ocr_gaps_merged"),
        left_out=st.get("left_out"),
        client_confidence=st.get("client_confidence") or None,
        client_side=st.get("client_side") or None,
        user_words_chars=st.get("user_words_chars"),
    )
    if result.error:
        _set(span, **{"error.type": result.error})
    _outcome(span, result.outcome)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def run_angle_mapper(supabase: Any, item_id: str, *, preview: bool = False) -> AngleMapRunResult:
    """Make sure ``item_id``'s message group is mapped — once. What the helper frame
    calls, per item, after that item's segmenter (module docstring: the trigger
    rule). Never raises (cancellation aside).

    ``preview``: map the item's group NOW with the members that have a usable map —
    no pending check, no fresh check, no joining — masking of what the model reads
    included, but nothing is written: no map, no new masking fakes, no
    ``llm_calls`` row (run detached from any ledger scope). The flag gate is
    skipped; the other guards hold.
    """
    t0 = time.perf_counter()
    result = new_result(str(item_id))
    result.stats["prompt_version"] = PROMPT_VERSION
    with track_stage("angle_mapper", agent_family="memory", item_id=str(item_id), preview=preview) as span:
        try:
            await _run_item(supabase, str(item_id), result, span, preview=preview, t0=t0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            result.outcome = "error"
            result.error = type(exc).__name__
            logger.warning(
                "angle_mapper: unexpected failure item=%s (%s)", item_id, type(exc).__name__, exc_info=True,
            )
        _finish_span(span, result, t0)
    return result


async def run_message_angle_map(
    supabase: Any,
    message_id: str | None,
    item_ids: Sequence[str],
    *,
    preview: bool = False,
) -> AngleMapRunResult:
    """Map ONE message group — ``item_ids`` (the message's attachments, or any
    items of one user a caller names; members are ordered D1… by upload order) —
    and store it under ``message_id`` (``None`` = no message: a file with no
    message link). No pending check, no fresh check, no joining: the caller
    decided this group runs now (the preview tool). Never raises (cancellation
    aside).

    The group's user (and so its conversation) comes from the FIRST LIVE
    ATTACHMENT among ``item_ids`` (in the order given) — the run's ``item_id``.
    Every other id that is not a usable member (deleted, not an attachment,
    another user's, no usable segment map) is left out (``result.excluded``).
    Only when no id is a live attachment does the run skip, with the first id's
    reason (``skipped_not_found`` / ``skipped_not_attachment``) and every id in
    ``excluded``; a group with live attachments but no usable member is the
    anchor's own reason.

    ``preview`` as in :func:`run_angle_mapper`: nothing stored, no fakes saved, no
    ledger row, the flag gate skipped. Without it the flag must be on for the
    items' user.
    """
    t0 = time.perf_counter()
    ids = [str(i) for i in dict.fromkeys(item_ids or []) if i]
    result = new_result(ids[0] if ids else "")
    result.stats["prompt_version"] = PROMPT_VERSION
    with track_stage(
        "angle_mapper", agent_family="memory", item_id=result.item_id or None, message_id=message_id,
        preview=preview,
    ) as span:
        try:
            if not ids:
                result.outcome = "skipped_not_found"
            else:
                rows = await asyncio.to_thread(pointer_rows, supabase, ids)
                anchor = next((rows[i] for i in ids if _row_skip(rows.get(i)) is None), None)
                if anchor is None:
                    result.excluded = {i: _row_skip(rows.get(i)) or "skipped_not_found" for i in ids}
                    result.outcome = result.excluded[ids[0]]
                else:
                    result.item_id = str(anchor["item_id"])
                    _set(
                        span,
                        item_id=result.item_id,
                        conversation_id=str(anchor.get("conversation_id") or "") or None,
                        case_id=str(anchor.get("case_id") or "") or None,
                    )
                    user_id = str(anchor.get("user_id") or "")
                    if not preview and not angle_map_enabled(user_id):
                        result.outcome = "skipped_disabled"
                    else:
                        group = await asyncio.to_thread(load_group, supabase, message_id, ids, user_id=user_id)
                        _describe_group(result, group)
                        if not group.docs:
                            result.outcome = group.excluded.get(result.item_id) or "skipped_no_map"
                        else:
                            await _run_group(supabase, group, result, preview=preview, t0=t0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            result.outcome = "error"
            result.error = type(exc).__name__
            logger.warning(
                "angle_mapper: unexpected failure message=%s (%s)", message_id, type(exc).__name__, exc_info=True,
            )
        _finish_span(span, result, t0)
    return result


def _row_skip(row: Mapping[str, Any] | None) -> str | None:
    if not row or row.get("deleted_at"):
        return "skipped_not_found"
    if str(row.get("kind") or "") != "attachment":
        return "skipped_not_attachment"
    return None


async def _run_item(
    supabase: Any,
    item_id: str,
    result: AngleMapRunResult,
    span: Any,
    *,
    preview: bool,
    t0: float,
) -> None:
    # The pointer row only: whether this run goes on is decided without any text.
    row = (await asyncio.to_thread(pointer_rows, supabase, [item_id])).get(item_id)
    if row:
        _set(
            span,
            conversation_id=str(row.get("conversation_id") or "") or None,
            case_id=str(row.get("case_id") or "") or None,
        )
    reason = _row_skip(row)
    if reason:
        result.outcome = reason
        return
    user_id = str(row.get("user_id") or "")
    if not preview and not angle_map_enabled(user_id):
        result.outcome = "skipped_disabled"
        return
    if preview:
        _set(span, preview=True)
        message_id, ids = await asyncio.to_thread(group_item_ids, supabase, item_id)
        group = await asyncio.to_thread(load_group, supabase, message_id, ids, user_id=user_id)
        _describe_group(result, group)
        if not group.docs:
            result.outcome = group.excluded.get(item_id) or "skipped_no_map"
            return
        await _run_group(supabase, group, result, preview=True, t0=t0)
        return

    async def resolve() -> GroupState:
        message_id, ids = await asyncio.to_thread(group_item_ids, supabase, item_id)
        return await asyncio.to_thread(load_state, supabase, message_id, ids, user_id=user_id)

    # Pending, fresh, too large: headers only. The text is read by the group run.
    state = await resolve()
    _describe_state(result, state)
    if state.pending:
        result.outcome = "skipped_group_pending"
        return
    if not state.item_ids:
        result.outcome = state.excluded.get(item_id) or "skipped_no_map"
        return
    if await asyncio.to_thread(_stored_fresh, supabase, state):
        result.outcome = "skipped_fresh"
        return
    if state.total_chars > MAX_GROUP_CHARS:
        result.outcome = "skipped_too_large"
        return
    await _run_or_join(supabase, state, result, resolve)


def _stored_fresh(supabase: Any, state: GroupState) -> bool:
    """The map holding the group's attachments fits them (``group.stored_fits``:
    headers against its members and its left-out attachments). A failed read = not
    fresh (the store's reader never raises)."""
    from .store import load_angle_map_for_items

    return stored_fits(state.versions(), load_angle_map_for_items(supabase, state.item_ids))


# ---------------------------------------------------------------------------
# One run per group in this process
# ---------------------------------------------------------------------------


def _group_task(supabase: Any, state: GroupState, trigger: str) -> tuple[asyncio.Task, bool]:
    """``(task, started_here)``: the group's in-process run, created when none is
    going."""
    loop = asyncio.get_running_loop()
    task = _GROUP_TASKS.get(state.key)
    if task is not None and not task.done() and task.get_loop() is loop:
        return task, False
    task = loop.create_task(_group_run_task(supabase, state, trigger), name=f"angle_map.{state.key}")
    _GROUP_TASKS[state.key] = task

    def _forget(t: asyncio.Task, k: str = state.key) -> None:
        if _GROUP_TASKS.get(k) is t:
            _GROUP_TASKS.pop(k, None)

    task.add_done_callback(_forget)
    return task, True


async def _group_run_task(supabase: Any, state: GroupState, trigger: str) -> AngleMapRunResult:
    """The group run: the members' text (the only place it is read on the frame's
    path), then the model. No member left after the text checks → the trigger's own
    reason (``skipped_paragraph_rule`` / ``skipped_no_sections`` / …), no call."""
    t0 = time.perf_counter()
    res = new_result(trigger)
    res.stats["prompt_version"] = PROMPT_VERSION
    _describe_state(res, state)
    try:
        group = await asyncio.to_thread(group_from_state, supabase, state)
        _describe_group(res, group)
        if not group.docs:
            res.outcome = group.excluded.get(trigger) or "skipped_no_map"
        else:
            await _run_group(supabase, group, res, preview=False, t0=t0)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        res.outcome, res.error = "error", type(exc).__name__
        logger.warning("angle_mapper: group run failed %s (%s)", state.key, type(exc).__name__, exc_info=True)
    res.stats["secs"] = round(time.perf_counter() - t0, 1)
    return res


def _adopt(result: AngleMapRunResult, got: AngleMapRunResult, *, outcome: str | None = None) -> None:
    """Copy a group run's result into this member's result (its own item_id kept)."""
    for f in dataclasses.fields(AngleMapRunResult):
        if f.name != "item_id":
            setattr(result, f.name, getattr(got, f.name))
    result.stats = dict(got.stats)
    if outcome is not None:
        result.outcome = outcome
        result.error = "" if got.outcome == "stored" else f"group:{got.outcome}"


async def _run_or_join(
    supabase: Any,
    state: GroupState,
    result: AngleMapRunResult,
    resolve: Any,
) -> None:
    """Run the group, or join the run another member started in this process.

    A joined run that STORED another member set than this member saw means the
    group changed while it was going — but either view may be the older one (or
    the run left an attachment out after reading its text). So the group is
    resolved again (``resolve``, headers only): fresh against what was just stored
    → done; still pending → leave it to the pending member; otherwise ONE more run
    with the new view."""
    got: AngleMapRunResult | None = None
    for round_ in range(2):
        if round_:
            state = await resolve()
            if state.pending or not state.item_ids or await asyncio.to_thread(_stored_fresh, supabase, state):
                break
        task, mine = _group_task(supabase, state, result.item_id)
        # shield: this member giving up must not cancel the run others wait on
        got = await asyncio.shield(task)
        if mine:
            _adopt(result, got)
            return
        if got.outcome != "stored" or set(got.item_ids) == set(state.item_ids):
            break
    if got is not None:
        _adopt(result, got, outcome="joined_group")


# ---------------------------------------------------------------------------
# The group run
# ---------------------------------------------------------------------------


def _prompt_documents(group: Group) -> list[PromptDocument]:
    return [
        PromptDocument(
            index=d.index,
            title=d.title,
            doc_type=d.doc_type,
            content_md=d.content_md,
            summary=d.summary,
            documents_in_file=d.smap.documents_in_file,
            sections=d.sections,
            key_paragraphs=d.smap.key_paragraphs,
            paragraphs=d.paragraphs,
        )
        for d in group.docs
    ]


def _unread_titles(supabase: Any, group: Group) -> list[str]:
    """The titles of the message's attachments the map leaves out (no usable segment
    map, too old, stale…), for the user message (angles-v5) — never a deleted item,
    a non-attachment or another user's. A failed read → ``[]`` (logged)."""
    ids = [i for i, why in group.excluded.items() if why not in _NOT_UNREAD]
    if not ids:
        return []
    try:
        titles = item_titles(supabase, ids, user_id=group.user_id or None)
    except Exception as exc:  # noqa: BLE001 — the map is still worth making without them
        logger.warning("angle_mapper: unread titles not read for %s (%s)", group.key, type(exc).__name__)
        return []
    return [titles[i] for i in ids if i in titles]


def _clean(output: AngleMap, refs: frozenset[str]) -> tuple[AngleMap, int, int, dict[str, int]]:
    """``(map, refs dropped, angles dropped, gap counts)``: bare refs qualified, refs
    outside the group dropped, then every angle left with no section dropped (the
    validator already sent both back once); then the gaps fixed in code (never sent
    back): contradictions between equal figures dropped, OCR gaps re-labelled
    ``missing`` and merged to one per document."""
    amap, dropped = drop_invalid_refs(qualify_refs(output, refs), refs)
    amap, empty = drop_sectionless_angles(amap)
    amap, equal = drop_equal_value_gaps(amap)
    amap, merged = merge_ocr_gaps(amap)
    return amap, dropped, empty, {"equal_value_gaps_dropped": equal, "ocr_gaps_merged": merged}


async def _run_group(
    supabase: Any,
    group: Group,
    result: AngleMapRunResult,
    *,
    preview: bool,
    t0: float,
) -> None:
    st = result.stats
    if group.total_chars > MAX_GROUP_CHARS:
        result.outcome = "skipped_too_large"
        return
    sections = group.sections
    refs = section_refs(sections)
    user_id = group.user_id

    user_words = await asyncio.to_thread(_load_user_words, supabase, group.docs[0].row)
    st["user_words_chars"] = len(user_words)
    unread = await asyncio.to_thread(_unread_titles, supabase, group)
    message = build_user_message(documents=_prompt_documents(group), user_words=user_words, unread=unread)
    st["input_chars"] = len(message)
    instructions = build_instructions([d.doc_type for d in group.docs])

    # وضع السرية — ONE codec for the whole group: encode everything the model reads
    # (the user's words included), persist new fakes first. The repair message
    # carries refs only.
    codec = _codec_for(supabase, user_id)
    encoded = _enc(codec, message)
    if not preview:  # a preview keeps its fakes in memory only
        _persist_fakes(supabase, user_id, codec)

    deps = AngleMapperDeps(
        item_id=result.item_id or group.docs[0].item_id,
        message_id=group.message_id,
        item_ids=tuple(group.item_ids),
        conversation_id=group.conversation_id,
        case_id=group.case_id,
        doc_types=tuple(d.doc_type for d in group.docs),
        section_refs=refs,
    )
    if preview:
        scope: Any = detached()  # a preview records no ledger row, even inside a caller's scope
    else:
        # ALWAYS the group's own scope (nested inside any open one, which then gets
        # nothing): the turn whose scope is open may be another message's — an
        # abandoned upload, a file OCR'd later — and a joined run bills once, here.
        scope = collect_llm_calls(
            supabase,
            conversation_id=group.conversation_id,
            user_id=user_id or None,
            message_id=group.message_id,
            case_id=group.case_id,
        )

    with scope:
        first = await _call(instructions, encoded, deps, attempts=MAX_ATTEMPTS)
        _absorb(st, first)
        st["attempts"] = first.attempts
        if first.output is None:
            result.outcome = "failed_invalid" if first.failure == "invalid" else "failed_llm"
            result.error = first.error
            return
        amap, dropped, empty, gap_fixes = _clean(first.output, refs)
        st["angles_without_sections"] = empty
        if empty and not amap.angles:
            # Every angle the model gave pointed at no section of the group, even
            # after the validator's retry: nothing to plan a search on.
            st["invalid_refs_dropped"] = dropped
            result.outcome, result.error = "failed_invalid", "angles_without_sections"
            return
        missing = unplaced_core(amap, sections)
        st["unplaced_before_repair"] = len(missing)
        if missing:
            st["repair_used"] = True
            fix = await _call(
                instructions, repair_message(missing), replace(deps, call="repair"),
                attempts=1, history=first.messages,
            )
            _absorb(st, fix)
            fixed = None
            if fix.output is not None:
                fixed, more, empty_fix, gap_fixes_fix = _clean(fix.output, refs)
                st["angles_without_sections"] += empty_fix
                # The corrected FULL map replaces the first — unless it lost every
                # angle (or the no-section rule took them all).
                if not (fixed.angles or (not amap.angles and not empty_fix)):
                    fixed = None
            if fixed is not None:
                amap = fixed
                dropped += more
                gap_fixes = gap_fixes_fix  # the gaps of the map that is kept
                st["repair_applied"] = True
            else:
                st["repair_applied"] = False
                if fix.output is None:
                    st["repair_error"] = fix.error
    st["invalid_refs_dropped"] = dropped
    st.update(gap_fixes)

    amap = _decode_map(codec, amap)
    still = unplaced_core(amap, sections)
    amap = mark_unplaced(amap, still)
    result.unplaced = still
    result.output = amap.model_dump()
    st["angle_count"] = len(amap.angles)
    st["cross_doc_angles"] = amap.cross_doc_angles()
    st["ocr_suspect_angles"] = sum(1 for a in amap.angles if a.ocr_suspect)
    # kept as the model left them after its one retry (code cannot pick the question)
    st["compound_questions_after_retry"] = len(multi_question_angles(amap))
    st["kind_mix"] = amap.kind_mix()
    st["client_confidence"] = amap.client.confidence
    st["client_side"] = amap.client.side
    st["unrelated_documents"] = len(amap.unrelated_documents)

    if preview:
        result.outcome = "preview"
        return

    map_row = {
        "prompt_version": PROMPT_VERSION,
        "model": st["model"] or None,
        "client": amap.client.model_dump(),
        "context": list(amap.context),
        "gaps": [g.model_dump() for g in amap.gaps],
        "no_angle": [n.model_dump() for n in amap.no_angle],
        "unrelated_documents": [u.model_dump() for u in amap.unrelated_documents],
        "left_out": list(group.left_out),
        "input_tokens": st["input_tokens"],
        "output_tokens": st["output_tokens"],
        "reasoning_tokens": st["reasoning_tokens"],
        "cost_usd": st["cost_usd"],
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }
    angles = [{"seq": i, **a.model_dump()} for i, a in enumerate(amap.angles, start=1)]
    items = [d.item_record() for d in group.docs]
    map_id, error = await asyncio.to_thread(_write, supabase, group, items, map_row, angles)
    if map_id:
        result.outcome, result.map_id = "stored", map_id
    else:
        result.outcome, result.error = "failed_store", error
        logger.warning("angle_mapper: store failed %s (%s)", group.key, error or "write_angle_map=None")


def _write(
    supabase: Any, group: Group, items: list[dict], map_row: dict, angles: list[dict]
) -> tuple[str | None, str]:
    """``store.write_angle_map`` (never raises by its contract) — imported here so a
    missing or broken store module only fails the store step."""
    try:
        from .store import write_angle_map
    except Exception as exc:  # noqa: BLE001
        return None, f"store_unavailable:{type(exc).__name__}"
    try:
        map_id = write_angle_map(
            supabase,
            message_id=group.message_id,
            conversation_id=group.conversation_id,
            user_id=group.user_id,
            items=items,
            map_row=map_row,
            angles=angles,
        )
        return (str(map_id), "") if map_id else (None, "")
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__


__all__ = [
    "CALL_TIMEOUT_S",
    "MAX_ATTEMPTS",
    "MAX_GROUP_CHARS",
    "angle_mapper_applies_to_item",
    "run_angle_mapper",
    "run_message_angle_map",
    "segment_version_ok",
]
