"""Runner for the attachment segmenter (Layer 4 Memory, model tier_2).

``run_attachment_segmenter(supabase, item_id)`` maps one attachment into labelled,
value-rated sections and stores them (migration 215). Plan:
``.claude/plans/attachment_segmentation.md`` §4.3–§4.5.

**Never raises** (cancellation aside) — every path returns a
:class:`SegmenterResult` whose ``outcome`` says what happened. Nothing here is on
a user's critical path yet: the helper frame (``.claude/plans/helper_frame.md``)
will start it and decide who waits for it.

Flow: load the row (+ page rows) → guards (attachment, flag, > 5,000 chars, no
fresh map unless ``force``) → paragraphize → mask (وضع السرية, new fakes persisted
BEFORE the model reads them) → one call, or two parts above 30,000 chars →
validate + repair (one re-attempt per call) → stitch → repeat detection →
decode descriptions → slice every section's text from the REAL ``content_md`` →
store.

Cost: every model call lands in ``llm_calls`` through the tracking hook
(``AgentSpan.record_run``). The helper frame runs each helper inside its own
capture scope keyed to the turn's message (``HelperSpec.own_ledger_scope``), so a
run that outlives the turn still lands its rows; called bare (no scope open),
this runner opens one keyed to the item's conversation and user. Do NOT start it
as a bare background task inside a TURN's scope: rows appended after that turn
flushed would be lost. A failed attempt still bills what its finished requests
cost (``RunUsage`` kept across the exception, the ``convo_memory_writer``
pattern).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from contextlib import nullcontext
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from pydantic_ai.usage import RunUsage

from agents.utils.segmenter_flags import segmenter_enabled
from agents.utils.tracking import track_stage
from agents.utils.usage_sink import collect_llm_calls, in_scope

from . import store
from .agent import MODEL_SLOT, SEGMENTER_LIMITS, create_attachment_segmenter
from .bundles import (
    FileSpan,
    bundle_doc_type,
    bundle_for_item,
    bundle_id_for,
    cut_back,
    number_globally,
    primary_messages,
    split_at_boundaries,
    split_evenly,
    upload_order,
)
from .deps import SegmenterDeps
from .models import SegmenterResult, SegmentMap
from .paragraphs import PARAGRAPH_RULE_VERSION, Paragraph, coerce_pages, paragraphize
from .prompts import (
    PROMPT_VERSION,
    BundleFile,
    build_bundle_message,
    build_bundle_part_message,
    build_instructions,
    build_part_message,
    build_user_message,
)
from .repair import (
    MAX_KEY_PARAGRAPHS_PER_PART,
    OVERLAP_PARAGRAPHS,
    SPLIT_THRESHOLD_CHARS,
    InvalidMap,
    RepairStats,
    choose_cut,
    first_page_end,
    repair_map,
    stitch,
)
from .repeats import find_repeats

logger = logging.getLogger(__name__)

# Attachments longer than SEGMENT_MIN_CHARS are segmented — one threshold for
# segmenting and delivery (plan D9), defined with the delivery rules.
from agents.utils.attachment_delivery import SEGMENT_MIN_CHARS  # noqa: E402
#: One call's ceiling. Phase 0 (93 runs, v4.4 family): median 71 s, p90 157 s, max 197 s.
CALL_TIMEOUT_S = 240.0
#: Attempts per call (whole document, or each part): the first + one re-attempt.
MAX_ATTEMPTS = 2

_ITEM_COLUMNS = (
    "item_id, user_id, conversation_id, case_id, kind, title, summary, content_md, metadata, "
    "deleted_at, created_at"
)

#: "What the user said" (owner, 2026-11-12): the user's own messages only, never the
#: assistant's. The message the file came with goes in full (up to this safety cap);
#: earlier user messages follow newest first until this budget is spent.
USER_WORDS_ATTACHED_CAP = 10_000
USER_WORDS_EARLIER_BUDGET = 4_000
USER_WORDS_EARLIER_LOOKBACK = 20
_ATTACHED_HEADING = "### With the file"
_EARLIER_HEADING = "### Earlier messages (newest first)"


def _load_user_words(supabase: Any, row: Mapping[str, Any]) -> str:
    """The user's words up to the moment this file was sent. Best-effort: ``""`` on
    any failure (the segmenter then runs without them).

    The anchor is the user message the file is attached to (``message_attachments``);
    without one (an abandoned upload), the item's own ``created_at``.
    """
    item_id = str(row.get("item_id") or "")
    conversation_id = str(row.get("conversation_id") or "")
    if not item_id or not conversation_id:
        return ""
    try:
        anchor_text, anchor_at = "", str(row.get("created_at") or "")
        link = (
            supabase.table("message_attachments")
            .select("message_id")
            .eq("document_id", item_id)
            .limit(1)
            .execute()
        )
        link_rows = getattr(link, "data", None) or []
        if link_rows:
            msg = (
                supabase.table("messages")
                .select("content, created_at, role")
                .eq("message_id", link_rows[0]["message_id"])
                .limit(1)
                .execute()
            )
            msg_rows = getattr(msg, "data", None) or []
            if msg_rows and str(msg_rows[0].get("role") or "") == "user":
                anchor_text = str(msg_rows[0].get("content") or "").strip()
                anchor_at = str(msg_rows[0].get("created_at") or anchor_at)
        earlier: list[str] = []
        if anchor_at:
            prev = (
                supabase.table("messages")
                .select("content")
                .eq("conversation_id", conversation_id)
                .eq("role", "user")
                .lt("created_at", anchor_at)
                .order("created_at", desc=True)
                .limit(USER_WORDS_EARLIER_LOOKBACK)
                .execute()
            )
            budget = USER_WORDS_EARLIER_BUDGET
            for r in getattr(prev, "data", None) or []:
                text = " ".join(str(r.get("content") or "").split())
                if not text or budget <= 0:
                    continue
                if len(text) > budget:
                    text = text[:budget].rstrip() + " …"
                earlier.append(f"- {text}")
                budget -= len(text)
    except Exception as exc:  # noqa: BLE001 — the user's words are context, never required
        logger.warning("attachment_segmenter: user words not loaded for %s (%s)", item_id, type(exc).__name__)
        return ""
    parts: list[str] = []
    if anchor_text:
        parts += [_ATTACHED_HEADING, anchor_text[:USER_WORDS_ATTACHED_CAP]]
    if earlier:
        parts += [_EARLIER_HEADING, *earlier]
    return "\n".join(parts)


def segmenter_applies(row: Mapping[str, Any]) -> bool:
    """The helper frame's ``applies_to``: an attachment over ``SEGMENT_MIN_CHARS``."""
    return str(row.get("kind") or "") == "attachment" and len(row.get("content_md") or "") > SEGMENT_MIN_CHARS


# ---------------------------------------------------------------------------
# Masking — the summarizer's helpers (codec per user on detached paths)
# ---------------------------------------------------------------------------


def _codec_for(supabase: Any, user_id: str) -> Any:
    try:
        from agents.memory.summarize import _summarize_codec

        return _summarize_codec(supabase, user_id)
    except Exception:  # noqa: BLE001 — passthrough
        logger.debug("attachment_segmenter: codec build failed", exc_info=True)
        return None


def _enc(codec: Any, text: str) -> str:
    from agents.memory.summarize import _enc as enc

    return enc(codec, text)


def _dec(codec: Any, text: str) -> str:
    if codec is None or not text:
        return text
    try:
        from agents.memory.summarize import _dec as dec

        return dec(codec, text)
    except Exception:  # noqa: BLE001
        return text


def _persist_fakes(supabase: Any, user_id: str, codec: Any) -> None:
    """New fakes reach the DB before any model sees them (resumable decode)."""
    if codec is None or not user_id:
        return
    from backend.app.services.masking_service import persist_new_mappings

    persist_new_mappings(supabase, user_id, codec)


# ---------------------------------------------------------------------------
# One model call (whole document or one part), with its re-attempt
# ---------------------------------------------------------------------------


@dataclass
class _CallPlan:
    part: int | None
    a: int
    b: int
    instructions: str
    message: str            # already encoded
    key_cap: int


@dataclass
class _CallOutcome:
    smap: SegmentMap | None
    stats: RepairStats
    attempts: int
    failure: str = ""       # "llm" | "invalid" when smap is None
    model: str = ""
    usage: RunUsage | None = None


def _model_name(result: Any) -> str:
    name = ""
    try:
        for m in result.all_messages():
            name = getattr(m, "model_name", None) or name
    except Exception:  # noqa: BLE001
        pass
    return name


def _record(span: Any, result: Any) -> None:
    """Ledger row + usage attrs. The ``LUNA_TRACK_DISABLE`` handle has no ``record_run``."""
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


async def _call(plan: _CallPlan, paragraphs: Sequence[Paragraph], deps: SegmenterDeps) -> _CallOutcome:
    total = RunUsage()
    failure = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        agent = create_attachment_segmenter(plan.instructions)
        spent = RunUsage()
        with track_stage(
            "attachment_segmenter.run",
            conversation_id=deps.conversation_id,
            case_id=deps.case_id,
            agent_family="memory",
            item_id=deps.item_id,
            part=plan.part or 0,
            attempt=attempt,
        ) as span:
            try:
                result = await asyncio.wait_for(
                    agent.run(plan.message, deps=deps, usage_limits=SEGMENTER_LIMITS, usage=spent),
                    timeout=CALL_TIMEOUT_S,
                )
            except asyncio.CancelledError:
                _record(span, SimpleNamespace(usage=lambda: spent))
                raise
            except Exception as exc:  # noqa: BLE001
                _record(span, SimpleNamespace(usage=lambda: spent))
                _set(span, **{"error.type": type(exc).__name__})
                _outcome(span, "error")
                total.incr(spent)
                failure = "llm"
                logger.warning(
                    "attachment_segmenter: call failed item=%s part=%s attempt=%d (%s)",
                    deps.item_id, plan.part, attempt, type(exc).__name__,
                )
                continue
            _record(span, result)
            total.incr(spent)
            try:
                smap, stats = repair_map(result.output, plan.a, plan.b, paragraphs, key_cap=plan.key_cap)
            except InvalidMap as exc:
                _outcome(span, "invalid")
                failure = "invalid"
                logger.warning(
                    "attachment_segmenter: invalid map item=%s part=%s attempt=%d (%s)",
                    deps.item_id, plan.part, attempt, exc,
                )
                continue
            _set(span, **{f"repair.{k}": v for k, v in stats.as_dict().items()})
            return _CallOutcome(smap, stats, attempt, model=_model_name(result), usage=total)
    return _CallOutcome(None, RepairStats(), MAX_ATTEMPTS, failure=failure, usage=total)


# ---------------------------------------------------------------------------
# Building the stored rows
# ---------------------------------------------------------------------------


def _segment_rows(item_id: str, smap: SegmentMap, paragraphs: Sequence[Paragraph], content_md: str) -> list[dict]:
    rows: list[dict] = []
    for seq, seg in enumerate(smap.segments, start=1):
        first, last = paragraphs[seg.from_p - 1], paragraphs[seg.to_p - 1]
        rows.append({
            "item_id": item_id,
            "seq": seq,
            "from_p": seg.from_p,
            "to_p": seg.to_p,
            "page_from": first.page,
            "page_to": last.page,
            "char_start": first.char_start,
            "char_end": last.char_end,
            "label": seg.label,
            "value": seg.value,
            "what": seg.what,
            # the ORIGINAL text, cut by code — never model output
            "content_md": content_md[first.char_start:last.char_end],
        })
    return rows


def _decode_map(codec: Any, smap: SegmentMap) -> SegmentMap:
    """Store-real: descriptions the model wrote can carry fakes."""
    return SegmentMap(
        documents_in_file=[d.model_copy(update={"name": _dec(codec, d.name)}) for d in smap.documents_in_file],
        # label too: it is free text (other:<name>) and must be stored real
        segments=[s.model_copy(update={"what": _dec(codec, s.what), "label": _dec(codec, s.label)}) for s in smap.segments],
        key_paragraphs=[k.model_copy(update={"why": _dec(codec, k.why)}) for k in smap.key_paragraphs],
    )


def _plans(
    paragraphs: Sequence[Paragraph],
    *,
    content_chars: int,
    doc_type: str,
    title: str,
    summary: str,
    encode: Any,
    user_words: str = "",
) -> tuple[list[_CallPlan], dict[str, Any]]:
    n = len(paragraphs)
    if content_chars <= SPLIT_THRESHOLD_CHARS or n < 2:
        msg = build_user_message(
            paragraphs, doc_type=doc_type, title=title, summary=summary, chars=content_chars,
            user_words=user_words,
        )
        return [_CallPlan(None, 1, n, build_instructions(doc_type), encode(msg), 15)], {}
    cut, how = choose_cut(paragraphs)
    first_end = first_page_end(paragraphs, cut)
    plans = []
    for part, a, b in ((1, 1, cut - 1), (2, cut, n)):
        msg = build_part_message(
            paragraphs, part=part, a=a, b=b, first_end=first_end, overlap=OVERLAP_PARAGRAPHS,
            doc_type=doc_type, title=title, summary=summary, chars=content_chars,
            user_words=user_words,
        )
        plans.append(_CallPlan(
            part, a, b,
            build_instructions(doc_type, part=part, a=a, b=b, first_end=first_end),
            encode(msg),
            MAX_KEY_PARAGRAPHS_PER_PART,
        ))
    return plans, {"cut_at": cut, "cut_how": how, "first_page_end": first_end}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _load_item(supabase: Any, item_id: str) -> dict | None:
    resp = (
        supabase.table("workspace_items")
        .select(_ITEM_COLUMNS)
        .eq("item_id", item_id)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


async def run_attachment_segmenter(
    supabase: Any,
    item_id: str,
    *,
    force: bool = False,
    preview: bool = False,
    min_chars: int | None = None,
) -> SegmenterResult:
    """Segment one attachment and store its map. Never raises (cancellation aside).

    ``force`` re-runs even when a fresh (non-stale) map exists; it never bypasses
    the flag, the kind or the length guard.

    ``preview`` (test tool ``scripts/segmentation_preview.py``): the same run —
    masking of what the model reads included — but nothing is written: no map, no
    new masking fakes, no ``llm_calls`` row (no ledger scope, so the user's quota
    is never charged). The flag gate is skipped; ``min_chars`` may lower the length
    guard. The would-be rows come back in ``result.preview``.
    """
    t0 = time.perf_counter()
    result = SegmenterResult(item_id=str(item_id), outcome="error")
    with track_stage("attachment_segmenter", agent_family="memory", item_id=str(item_id), force=force) as span:
        try:
            await _run(supabase, str(item_id), force, result, span, preview=preview, min_chars=min_chars)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            result.outcome = "error"
            result.error = type(exc).__name__
            logger.warning(
                "attachment_segmenter: unexpected failure item=%s (%s)", item_id, type(exc).__name__, exc_info=True,
            )
        result.duration_ms = int((time.perf_counter() - t0) * 1000)
        _set(
            span,
            doc_type=result.doc_type,
            chars=result.chars,
            paragraphs=result.paragraphs,
            split=result.split,
            attempts=result.attempts,
            segments=result.segments,
            repeats=result.repeats,
            core_chars=result.core_chars,
            model=result.model_used or None,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            tokens_reasoning=result.tokens_reasoning,
            tokens_cached=result.tokens_cached,
            ms=result.duration_ms,
            **{f"repair.{k}": v for k, v in result.repairs.items()},
        )
        if result.error:
            _set(span, **{"error.type": result.error})
        _outcome(span, result.outcome)
    return result


async def _run(
    supabase: Any,
    item_id: str,
    force: bool,
    result: SegmenterResult,
    span: Any,
    *,
    preview: bool = False,
    min_chars: int | None = None,
) -> None:
    row = await asyncio.to_thread(_load_item, supabase, item_id)
    if not row or row.get("deleted_at"):
        result.outcome = "not_found"
        return
    _set(span, conversation_id=str(row.get("conversation_id") or "") or None, case_id=str(row.get("case_id") or "") or None)
    content_md = row.get("content_md") or ""
    metadata = row.get("metadata") or {}
    doc_type = str(metadata.get("doc_type") or "") if isinstance(metadata, Mapping) else ""
    result.chars, result.doc_type = len(content_md), doc_type

    if str(row.get("kind") or "") != "attachment":
        result.outcome = "skipped_not_attachment"
        return
    user_id = str(row.get("user_id") or "")
    if not preview and not segmenter_enabled(user_id):
        result.outcome = "skipped_disabled"
        return
    threshold = SEGMENT_MIN_CHARS if min_chars is None else min_chars
    if len(content_md) <= threshold:
        # A small file runs with the other small files of its message when they
        # qualify as a bundle (owner, 2026-11-12) — this item's result comes back.
        members = await asyncio.to_thread(bundle_for_item, supabase, item_id, min_chars=threshold)
        if not members:
            result.outcome = "skipped_short"
            return
        _set(span, bundle_size=len(members))
        member = await _bundle_member_result(supabase, members, item_id, force=force, preview=preview)
        for name in SegmenterResult.model_fields:
            setattr(result, name, getattr(member, name))
        return
    if preview:
        _set(span, preview=True)
    if not force and not preview:
        state = await asyncio.to_thread(store.map_state, supabase, item_id)
        if state is not None and state[0] == len(content_md) and state[1]:
            result.outcome = "skipped_fresh"
            return

    pages = await asyncio.to_thread(store.load_pages, supabase, item_id)
    paragraphs = paragraphize(content_md, doc_type=doc_type, pages=coerce_pages(pages))
    result.paragraphs = len(paragraphs)
    if not paragraphs:
        result.outcome = "failed_invalid"
        return

    user_words = await asyncio.to_thread(_load_user_words, supabase, row)
    result.user_words_chars = len(user_words)

    # وضع السرية — encode everything the model reads (the user's words included, since
    # they are part of the message), persist new fakes first.
    codec = _codec_for(supabase, user_id)
    plans, split_info = _plans(
        paragraphs,
        content_chars=len(content_md),
        doc_type=doc_type,
        title=str(row.get("title") or ""),
        summary=str(row.get("summary") or ""),
        encode=lambda text: _enc(codec, text),
        user_words=user_words,
    )
    if not preview:  # a preview keeps its fakes in memory only
        _persist_fakes(supabase, user_id, codec)
    result.split = len(plans) > 1
    if split_info:
        _set(span, **split_info)

    conversation_id = str(row.get("conversation_id") or "") or None
    case_id = str(row.get("case_id") or "") or None
    # The helper frame gives each run its own scope keyed to the turn's message
    # (``HelperSpec.own_ledger_scope``) — use it. Called bare, open one here.
    scope = (
        nullcontext()
        if in_scope() or preview  # a preview records no ledger row
        else collect_llm_calls(supabase, conversation_id=conversation_id, user_id=user_id or None, case_id=case_id)
    )
    with scope:
        outcomes = await asyncio.gather(*(
            _call(
                plan,
                paragraphs,
                SegmenterDeps(
                    item_id=item_id, conversation_id=conversation_id, case_id=case_id,
                    doc_type=doc_type, part=plan.part, from_p=plan.a, to_p=plan.b,
                ),
            )
            for plan in plans
        ))

    usage = RunUsage()
    for o in outcomes:
        if o.usage is not None:
            usage.incr(o.usage)
    result.tokens_in = int(usage.input_tokens or 0)
    result.tokens_out = int(usage.output_tokens or 0)
    result.tokens_reasoning = int((usage.details or {}).get("reasoning_tokens", 0) or 0)
    result.tokens_cached = int(getattr(usage, "cache_read_tokens", 0) or 0)
    result.attempts = sum(o.attempts for o in outcomes)
    result.model_used = next((o.model for o in outcomes if o.model), "")

    failed = [o for o in outcomes if o.smap is None]
    if failed:
        result.outcome = "failed_invalid" if all(o.failure == "invalid" for o in failed) else "failed_llm"
        return

    repairs = RepairStats()
    for o in outcomes:
        repairs = repairs.add(o.stats)
    result.repairs = {k: v for k, v in repairs.as_dict().items() if v}

    if len(outcomes) == 1:
        smap = outcomes[0].smap
    else:
        smap, seam = stitch(outcomes[0].smap, outcomes[1].smap, paragraphs)
        _set(span, **{f"seam.{k}": v for k, v in seam.as_dict().items()})

    repeats = find_repeats(smap.segments, paragraphs)
    if repeats:
        segs = list(smap.segments)
        for i in repeats:
            segs[i] = segs[i].model_copy(update={"value": "repeat"})
        smap = smap.model_copy(update={"segments": segs})
    result.repeats = len(repeats)

    smap = _decode_map(codec, smap)
    seg_rows = _segment_rows(item_id, smap, paragraphs, content_md)
    map_row = {
        "item_id": item_id,
        "user_id": user_id,
        "paragraph_rule_version": PARAGRAPH_RULE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": result.model_used or None,
        "split": result.split,
        "documents_in_file": [d.model_dump() for d in smap.documents_in_file],
        "key_paragraphs": [k.model_dump() for k in smap.key_paragraphs],
        "content_chars": len(content_md),
    }
    if preview:
        from dataclasses import asdict

        result.preview = {
            "paragraphs": [asdict(p) for p in paragraphs],
            "map": smap.model_dump(),
            "segments": seg_rows,
            "map_row": map_row,
            "split_info": split_info or {},
            "user_words": user_words,
        }
        result.segments = len(seg_rows)
        result.core_chars = sum(len(r["content_md"]) for r in seg_rows if r["value"] == "core")
        result.outcome = "ok"
        return
    try:
        await asyncio.to_thread(store.write_segment_map, supabase, map_row, seg_rows)
    except store.StoreError as exc:
        result.outcome = "failed_store"
        result.error = str(exc)
        logger.warning("attachment_segmenter: store failed item=%s (%s)", item_id, exc)
        return

    result.segments = len(seg_rows)
    result.core_chars = sum(len(r["content_md"]) for r in seg_rows if r["value"] == "core")
    result.outcome = "ok"


# ---------------------------------------------------------------------------
# Bundles — the small files of one message in ONE run (owner, 2026-11-12)
# ---------------------------------------------------------------------------

_BUNDLE_COLUMNS = _ITEM_COLUMNS + ", wi_seq"

#: In-process bundle runs, keyed by the sorted member ids: concurrent calls for
#: different members of one bundle join ONE run (the helper frame starts one task
#: per item). Preview runs never join (the test tool runs every unit twice).
_BUNDLE_TASKS: dict[tuple[str, ...], asyncio.Task] = {}


def _load_items(supabase: Any, item_ids: Sequence[str]) -> dict[str, dict]:
    resp = (
        supabase.table("workspace_items")
        .select(_BUNDLE_COLUMNS)
        .in_("item_id", list(item_ids))
        .execute()
    )
    return {str(r["item_id"]): r for r in (getattr(resp, "data", None) or [])}


def _doc_type_of(row: Mapping[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return str(metadata.get("doc_type") or "") if isinstance(metadata, Mapping) else ""


def _bundle_plans(
    paragraphs: Sequence[Paragraph],
    files: Sequence[BundleFile],
    *,
    chars: int,
    doc_type: str,
    encode: Any,
    user_words: str = "",
) -> tuple[list[_CallPlan], dict[str, Any]]:
    n, n_files = len(paragraphs), len(files)
    if chars <= SPLIT_THRESHOLD_CHARS or n < 2:
        msg = build_bundle_message(paragraphs, files, doc_type=doc_type, chars=chars, user_words=user_words)
        return [_CallPlan(None, 1, n, build_instructions(doc_type, bundle_files=n_files, bundle_types=[f.doc_type for f in files]), encode(msg), 15)], {}
    cut, how = choose_cut(paragraphs)
    first_end = first_page_end(paragraphs, cut)
    plans = []
    for part, a, b in ((1, 1, cut - 1), (2, cut, n)):
        msg = build_bundle_part_message(
            paragraphs, files, part=part, a=a, b=b, first_end=first_end, overlap=OVERLAP_PARAGRAPHS,
            doc_type=doc_type, chars=chars, user_words=user_words,
        )
        plans.append(_CallPlan(
            part, a, b,
            build_instructions(
                doc_type, part=part, a=a, b=b, first_end=first_end, bundle_files=n_files,
                bundle_types=[f.doc_type for f in files],
            ),
            encode(msg),
            MAX_KEY_PARAGRAPHS_PER_PART,
        ))
    return plans, {"cut_at": cut, "cut_how": how, "first_page_end": first_end}


async def run_attachment_bundle(
    supabase: Any,
    item_ids: Sequence[str],
    *,
    force: bool = False,
    preview: bool = False,
) -> dict[str, SegmenterResult]:
    """Segment the small attachments of one message as ONE run; every member gets
    its own map. Never raises (cancellation aside) — read each result's ``outcome``.

    The members are taken as given (``bundle_for_item`` / ``plan_units`` decide who
    belongs) and put in upload order. Each file is paragraphized by ITS OWN
    doc_type rule, then numbered globally (P001…), with an un-numbered boundary line
    before each file. One call, or two above ~30k combined. Repair and stitch run on
    the global numbering; segments that cross a file are split; repeats are found
    across the whole bundle; then each file's map is cut back, renumbered from 1 and
    sliced from THAT file's real ``content_md``. Stored maps carry ``bundle_id`` and
    ``bundle_size``. A member whose text changed (stale map) or that has no map yet
    makes the whole bundle re-run.

    ``preview`` as in ``run_attachment_segmenter``: nothing stored, no fakes saved,
    no ledger row; each member's would-be rows in ``result.preview``.
    """
    ids = list(dict.fromkeys(str(i) for i in item_ids if i))
    results = {i: SegmenterResult(item_id=i, outcome="error") for i in ids}
    if not ids:
        return results
    t0 = time.perf_counter()
    with track_stage(
        "attachment_segmenter.bundle", agent_family="memory", item_id=ids[0], bundle_size=len(ids), force=force,
    ) as span:
        try:
            await _run_bundle(supabase, ids, force, results, span, preview=preview)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            for r in results.values():  # members not finished yet are still "error"
                if r.outcome == "error" and not r.error:
                    r.error = type(exc).__name__
            logger.warning(
                "attachment_segmenter: bundle failed items=%s (%s)", ids, type(exc).__name__, exc_info=True,
            )
        ms = int((time.perf_counter() - t0) * 1000)
        for r in results.values():
            r.duration_ms = ms
        first = next(iter(results.values()))
        outcomes = sorted({r.outcome for r in results.values()})
        _set(
            span,
            bundle_id=first.bundle_id,
            members=len(first.bundle_members) or len(ids),
            chars=sum(r.chars for r in results.values()),
            split=first.split,
            attempts=first.attempts,
            model=first.model_used or None,
            tokens_in=sum(r.tokens_in for r in results.values()),
            tokens_out=sum(r.tokens_out for r in results.values()),
            ms=ms,
            outcomes=",".join(outcomes),
        )
        _outcome(span, outcomes[0] if len(outcomes) == 1 else "mixed")
    return results


#: A bundle starts as soon as its FIRST member's summary lands (the frame chains the
#: segmenter after that item's summary) while the other members' summaries are still
#: running. Their doc_type picks each file's label list and paragraph rule, and their
#: summaries are context — so the bundle waits for them, up to this long, polling.
BUNDLE_SUMMARY_WAIT_S = 90.0
BUNDLE_SUMMARY_POLL_S = 3.0
#: Only a member uploaded this recently is still expected to get its summary.
BUNDLE_SUMMARY_FRESH_S = 600.0
#: The summarizer skips text shorter than this (``summarize.MIN_CONTENT_LENGTH_CHARS``).
_SUMMARY_MIN_CHARS = 300


def _awaiting_summary(row: Mapping[str, Any] | None) -> bool:
    """True while a fresh member's summary (and so its doc_type) is still due."""
    if not row or row.get("deleted_at"):
        return False
    if len((row.get("content_md") or "").strip()) < _SUMMARY_MIN_CHARS:
        return False
    metadata = row.get("metadata") or {}
    if (row.get("summary") or "").strip() or (isinstance(metadata, Mapping) and metadata.get("doc_type")):
        return False
    try:
        created = datetime.fromisoformat(str(row.get("created_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds() < BUNDLE_SUMMARY_FRESH_S


async def _wait_for_member_summaries(supabase: Any, ids: list[str], rows: dict) -> dict:
    """Re-read the members until none is still waiting for its summary, or the wait ends."""
    deadline = time.monotonic() + BUNDLE_SUMMARY_WAIT_S
    while any(_awaiting_summary(rows.get(i)) for i in ids) and time.monotonic() < deadline:
        await asyncio.sleep(BUNDLE_SUMMARY_POLL_S)
        rows = await asyncio.to_thread(_load_items, supabase, ids)
    return rows


async def _run_bundle(
    supabase: Any,
    ids: list[str],
    force: bool,
    results: dict[str, SegmenterResult],
    span: Any,
    *,
    preview: bool,
) -> None:
    rows = await asyncio.to_thread(_load_items, supabase, ids)
    if not preview:
        rows = await _wait_for_member_summaries(supabase, ids, rows)
    live: list[dict] = []
    for i in ids:
        row = rows.get(i)
        if not row or row.get("deleted_at"):
            results[i].outcome = "not_found"
        elif str(row.get("kind") or "") != "attachment":
            results[i].outcome = "skipped_not_attachment"
        elif not (row.get("content_md") or "").strip():
            results[i].outcome = "skipped_short"
        else:
            live.append(row)
    if not live:
        return
    live.sort(key=upload_order)
    user_id = str(live[0].get("user_id") or "")
    for row in live[1:]:
        if str(row.get("user_id") or "") != user_id:  # never mix two users' text
            results[str(row["item_id"])].outcome, results[str(row["item_id"])].error = "error", "other_user"
    live = [r for r in live if str(r.get("user_id") or "") == user_id]
    member_ids = [str(r["item_id"]) for r in live]

    try:
        primary = await asyncio.to_thread(primary_messages, supabase, member_ids)
    except Exception:  # noqa: BLE001 — only the bundle id depends on it
        primary = {}
    msgs = [m for m in (primary.get(i) for i in member_ids) if m]
    message_id = max(set(msgs), key=msgs.count) if msgs else None
    bundle_id = bundle_id_for(message_id, member_ids)
    _set(span, bundle_id=bundle_id, conversation_id=str(live[0].get("conversation_id") or "") or None)
    for row in live:
        r = results[str(row["item_id"])]
        r.bundle_id, r.bundle_members = bundle_id, list(member_ids)
        r.doc_type, r.chars = _doc_type_of(row), len(row.get("content_md") or "")

    def _all(outcome: str) -> None:
        for i in member_ids:
            results[i].outcome = outcome

    if not preview and not segmenter_enabled(user_id):
        _all("skipped_disabled")
        return
    if preview:
        _set(span, preview=True)
    if not force and not preview:
        states = await asyncio.gather(*(asyncio.to_thread(store.map_state, supabase, i) for i in member_ids))
        if all(
            s is not None and s[0] == len(row.get("content_md") or "") and s[1]
            for s, row in zip(states, live)
        ):
            _all("skipped_fresh")
            return

    # each file by ITS OWN paragraph rule, then one global numbering
    contents = [str(r.get("content_md") or "") for r in live]
    doc_types = [_doc_type_of(r) for r in live]
    pages = await asyncio.gather(*(asyncio.to_thread(store.load_pages, supabase, i) for i in member_ids))
    local = [
        paragraphize(c, doc_type=t, pages=coerce_pages(p)) for c, t, p in zip(contents, doc_types, pages)
    ]
    paragraphs, spans = number_globally(member_ids, local, contents)
    for i, paras in zip(member_ids, local):
        results[i].paragraphs = len(paras)
    files = [
        BundleFile(
            index=k, first_p=s.first_p, last_p=s.last_p, doc_type=t,
            title=str(r.get("title") or ""), summary=str(r.get("summary") or ""),
        )
        for k, (s, t, r) in enumerate(zip(spans, doc_types, live), start=1)
    ]
    keep = [k for k, s in enumerate(spans) if s.last_p >= s.first_p]  # files with text
    if not paragraphs:
        _all("failed_invalid")
        return
    doc_type = bundle_doc_type(doc_types)
    total_chars = sum(len(c) for c in contents)
    _set(span, doc_type=doc_type, paragraphs=len(paragraphs), total_chars=total_chars)

    # the user's words are the same for every member: load them once
    user_words = await asyncio.to_thread(_load_user_words, supabase, live[0])
    for i in member_ids:
        results[i].user_words_chars = len(user_words)

    codec = _codec_for(supabase, user_id)
    plans, split_info = _bundle_plans(
        paragraphs, files, chars=total_chars, doc_type=doc_type,
        encode=lambda text: _enc(codec, text), user_words=user_words,
    )
    if not preview:
        _persist_fakes(supabase, user_id, codec)
    split = len(plans) > 1
    if split_info:
        _set(span, **split_info)

    conversation_id = str(live[0].get("conversation_id") or "") or None
    case_id = str(live[0].get("case_id") or "") or None
    scope = (
        nullcontext()
        if in_scope() or preview
        else collect_llm_calls(supabase, conversation_id=conversation_id, user_id=user_id or None, case_id=case_id)
    )
    with scope:
        outcomes = await asyncio.gather(*(
            _call(
                plan,
                paragraphs,
                SegmenterDeps(
                    item_id=member_ids[0], conversation_id=conversation_id, case_id=case_id,
                    doc_type=doc_type, part=plan.part, from_p=plan.a, to_p=plan.b,
                ),
            )
            for plan in plans
        ))

    usage = RunUsage()
    for o in outcomes:
        if o.usage is not None:
            usage.incr(o.usage)
    weights = [len(c) for c in contents]
    token_shares = {
        "tokens_in": split_evenly(int(usage.input_tokens or 0), weights),
        "tokens_out": split_evenly(int(usage.output_tokens or 0), weights),
        "tokens_reasoning": split_evenly(int((usage.details or {}).get("reasoning_tokens", 0) or 0), weights),
        "tokens_cached": split_evenly(int(getattr(usage, "cache_read_tokens", 0) or 0), weights),
    }
    attempts = sum(o.attempts for o in outcomes)
    model = next((o.model for o in outcomes if o.model), "")
    for k, i in enumerate(member_ids):
        r = results[i]
        r.split, r.attempts, r.model_used = split, attempts, model
        for field, shares in token_shares.items():
            setattr(r, field, shares[k])

    failed = [o for o in outcomes if o.smap is None]
    if failed:
        _all("failed_invalid" if all(o.failure == "invalid" for o in failed) else "failed_llm")
        return

    repairs = RepairStats()
    for o in outcomes:
        repairs = repairs.add(o.stats)
    if len(outcomes) == 1:
        smap = outcomes[0].smap
    else:
        smap, seam = stitch(outcomes[0].smap, outcomes[1].smap, paragraphs)
        _set(span, **{f"seam.{k}": v for k, v in seam.as_dict().items()})
    segments, boundary_cuts = split_at_boundaries(smap.segments, spans)
    smap = smap.model_copy(update={"segments": segments})
    repair_dict = {k: v for k, v in repairs.as_dict().items() if v}
    if boundary_cuts:
        repair_dict["boundary_splits"] = boundary_cuts

    # repeats across the WHOLE bundle: a later file repeating an earlier one
    repeat_idx = find_repeats(smap.segments, paragraphs)
    if repeat_idx:
        segs = list(smap.segments)
        for i in repeat_idx:
            segs[i] = segs[i].model_copy(update={"value": "repeat"})
        smap = smap.model_copy(update={"segments": segs})

    smap = _decode_map(codec, smap)
    per_member: list[tuple[str, dict, list[dict], SegmentMap]] = []
    for k in keep:
        span_k: FileSpan = spans[k]
        item_id = member_ids[k]
        local_map = cut_back(smap, span_k)
        seg_rows = _segment_rows(item_id, local_map, local[k], contents[k])
        map_row = {
            "item_id": item_id,
            "user_id": user_id,
            "paragraph_rule_version": PARAGRAPH_RULE_VERSION,
            "prompt_version": PROMPT_VERSION,
            "model": model or None,
            "split": split,
            "documents_in_file": [d.model_dump() for d in local_map.documents_in_file],
            "key_paragraphs": [kp.model_dump() for kp in local_map.key_paragraphs],
            "content_chars": len(contents[k]),
            "bundle_id": bundle_id,
            "bundle_size": len(member_ids),
        }
        per_member.append((item_id, map_row, seg_rows, local_map))
        r = results[item_id]
        r.repairs = dict(repair_dict)
        r.segments = len(seg_rows)
        r.repeats = sum(1 for row in seg_rows if row["value"] == "repeat")
        r.core_chars = sum(len(row["content_md"]) for row in seg_rows if row["value"] == "core")
    for k, s in enumerate(spans):
        if k not in keep:
            results[member_ids[k]].outcome = "skipped_short"  # whitespace-only: no paragraphs

    if preview:
        from dataclasses import asdict

        for item_id, map_row, seg_rows, local_map in per_member:
            k = member_ids.index(item_id)
            results[item_id].preview = {
                "paragraphs": [asdict(p) for p in local[k]],
                "map": local_map.model_dump(),
                "segments": seg_rows,
                "map_row": map_row,
                "split_info": split_info or {},
                "user_words": user_words,
                "bundle": {
                    "bundle_id": bundle_id,
                    "members": list(member_ids),
                    "index": k + 1,
                    "global_from_p": spans[k].first_p,
                    "global_to_p": spans[k].last_p,
                    "doc_type": doc_type,
                    "chars": total_chars,
                },
            }
            results[item_id].outcome = "ok"
        return

    for item_id, map_row, seg_rows, _local in per_member:
        try:
            await asyncio.to_thread(store.write_segment_map, supabase, map_row, seg_rows)
        except store.StoreError as exc:
            results[item_id].outcome, results[item_id].error = "failed_store", str(exc)
            logger.warning("attachment_segmenter: bundle store failed item=%s (%s)", item_id, exc)
            continue
        results[item_id].outcome = "ok"


async def _bundle_member_result(
    supabase: Any,
    members: Sequence[str],
    item_id: str,
    *,
    force: bool,
    preview: bool,
) -> SegmenterResult:
    """``item_id``'s result from a run of its bundle — a fresh run in preview, else
    the in-process run of the same bundle when one is going (``_BUNDLE_TASKS``)."""
    if preview:
        res = await run_attachment_bundle(supabase, members, force=force, preview=True)
    else:
        key = tuple(sorted(members))
        loop = asyncio.get_running_loop()
        task = _BUNDLE_TASKS.get(key)
        if task is None or task.done() or task.get_loop() is not loop:
            task = loop.create_task(run_attachment_bundle(supabase, list(members), force=force))
            _BUNDLE_TASKS[key] = task

            def _forget(t: asyncio.Task, k: tuple[str, ...] = key) -> None:
                if _BUNDLE_TASKS.get(k) is t:
                    _BUNDLE_TASKS.pop(k, None)

            task.add_done_callback(_forget)
        # shield: one caller giving up must not cancel the run the others wait on
        res = await asyncio.shield(task)
    return res.get(str(item_id)) or SegmenterResult(item_id=str(item_id), outcome="error", error="not_in_bundle")


__all__ = [
    "CALL_TIMEOUT_S",
    "MAX_ATTEMPTS",
    "SEGMENT_MIN_CHARS",
    "run_attachment_bundle",
    "run_attachment_segmenter",
    "segmenter_applies",
]
