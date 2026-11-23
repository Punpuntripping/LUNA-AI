"""The matrix as code: helper_frame.md §4. The single source of truth.

| Helper              | Scope   | Depends on | Waits (critical)                                   | Ceiling |
|---------------------|---------|------------|----------------------------------------------------|---------|
| ocr                 | message | —          | router (everyone after it is covered upstream)     | 180 s   |
| summary             | item    | ocr        | router (everyone after it is covered upstream)     | 180 s   |
| pre_router_memory   | message | —          | router (everyone after it is covered upstream)     | 180 s   |
| memory              | message | —          | everyone but the router; expanders + aggregator covered upstream | 60 s |
| segmenter           | item    | ocr, summary | case expander, reg expander, aggregator, writer  | 300 s   |
| angle_map           | item    | segmenter  | — (nobody waits; relationship_map.md Q3)           | —       |

``angle_map`` is started per item but maps the item's whole MESSAGE group in one
run (relationship_map.md D17; the runner decides which member runs it).

Adding a helper = one :class:`HelperSpec` here (or :func:`register`) + its wait
point. A test checks every consumer used at a wait point exists and that
``depends_on`` names registered helpers with no cycle.

OCR is a **message** helper here, not an item helper: ``run_ocr_extraction``
scans the conversation and OCRs every new attachment in one call, and per-item
OCR needs a change in ``agents/memory/ocr_extractor`` (helper_frame.md §5.7).
The summary is per item and runs on the items OCR filled, in parallel.

The ``run`` functions below call the memory modules through their module
objects, so a run started outside a turn (``ensure_ready``) works on its own.
The orchestrator passes its own bindings (``TurnHelpers(runs=...)``) so the
steps it ran inline before keep going through the same names.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from .spec import CONSUMERS, HelperContext, HelperSpec

logger = logging.getLogger(__name__)

# Ceilings (helper_frame.md §7 Q1: per helper, today's values). OCR's is today's
# whole-step budget. The summary and the pre-router hook had none (awaited until
# they ended); 180 s only binds on a stalled provider, where today the turn hung.
OCR_CEILING_S = 600.0  # owner 2026-11-16: wait up to 10 min for big files (ocr_page_budget.md)
SUMMARY_CEILING_S = 180.0
PRE_ROUTER_MEMORY_CEILING_S = 180.0
#: Same value as ``agents.orchestrator.MEMORY_WRITER_CEILING_S`` (a test pins it).
MEMORY_CEILING_S = 60.0
#: Summaries of one message run in parallel, at most this many at once.
SUMMARY_CONCURRENCY = 4

#: The consumers after the router, which the router's waits already cover.
_AFTER_ROUTER = frozenset(c for c in CONSUMERS if c != "router")


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


async def run_ocr(ctx: HelperContext) -> Any:
    """OCR every new attachment of the conversation. Never raises (its contract)."""
    from agents.memory import ocr_extractor

    return await ocr_extractor.run_ocr_extraction(
        ctx.supabase, ctx.conversation_id, ctx.user_id
    )


async def run_summary(ctx: HelperContext) -> Any:
    """Summarize one attachment OCR filled. Never raises (its contract)."""
    from agents.memory import summarize

    return await summarize.summarize_workspace_item(ctx.supabase, ctx.item_id)


async def run_pre_router_memory(ctx: HelperContext) -> None:
    """``resummarize_dirty_items`` + ``compact_conversation``: best-effort, as before."""
    import agents.memory.agent as memory

    try:
        await memory.resummarize_dirty_items(ctx.supabase, ctx.conversation_id)
        await memory.compact_conversation(ctx.supabase, ctx.conversation_id, ctx.user_id)
    except Exception:  # noqa: BLE001 - the hook never aborts a turn
        logger.warning("memory pre-hook failed", exc_info=True)


def _ocr_filled(stats: Any) -> list[str]:
    return [str(i) for i in (getattr(stats, "filled_item_ids", None) or []) if i]


# --------------------------------------------------------------------------- #
# The matrix
# --------------------------------------------------------------------------- #

OCR = HelperSpec(
    name="ocr",
    scope="message",
    critical_for=frozenset({"router"}),
    covered_upstream=_AFTER_ROUTER,
    ceiling_s=OCR_CEILING_S,
    run=run_ocr,
    produces_items=_ocr_filled,
    queue_per_conversation=True,
    fallback=(
        "the attachment travels as its filename, and the status line "
        "«تعذّرت قراءة بعض المرفقات…»"
    ),
)

SUMMARY = HelperSpec(
    name="summary",
    scope="item",
    depends_on=("ocr",),
    critical_for=frozenset({"router"}),
    covered_upstream=_AFTER_ROUTER,
    ceiling_s=SUMMARY_CEILING_S,
    run=run_summary,
    concurrency=SUMMARY_CONCURRENCY,
    fallback="no summary: the router reads content_md; case docs show a 1,500-char opening",
)

PRE_ROUTER_MEMORY = HelperSpec(
    name="pre_router_memory",
    scope="message",
    critical_for=frozenset({"router"}),
    covered_upstream=_AFTER_ROUTER,
    ceiling_s=PRE_ROUTER_MEMORY_CEILING_S,
    run=run_pre_router_memory,
    # Started by the orchestrator after the pause check, on the routing path only
    # (a resumed agent never ran it), never by a dependency finishing.
    auto_start=False,
    fallback="the conversation as it was before this message (no new compaction)",
)

MEMORY = HelperSpec(
    name="memory",
    scope="message",
    critical_for=frozenset(
        {"deep_planner", "writer_planner", "analyzer", "writer", "other"}
    ),
    covered_upstream=frozenset({"case_expander", "reg_expander", "aggregator"}),
    ceiling_s=MEMORY_CEILING_S,
    # Started by the orchestrator (its per-conversation queue lives in
    # convo_memory_writer.runner) and adopted by the turn.
    run=None,
    auto_start=False,
    own_ledger_scope=False,  # the runner opens its own
    fallback="the memory as it was before this message",
)

# --------------------------------------------------------------------------- #
# Attachment segmenter — .claude/plans/attachment_segmentation.md (D9, D15, D16)
# --------------------------------------------------------------------------- #

#: Critical consumers wait up to 5 minutes (owner, 2026-11-11).
SEGMENTER_CEILING_S = 300.0
#: Owner (2026-11-12): every run of a message starts in parallel — each long file
#: alone (one parallel call per started 150k chars — owner, 2026-11-19), the small
#: files of the message as one bundle.
#: Each member of a bundle holds a slot while it waits on the shared run, so the
#: limit is set above the unit count of a busy message.
SEGMENTER_CONCURRENCY = 8


def _segmenter_enabled(user_id: str) -> bool:
    from agents.utils.segmenter_flags import segmenter_enabled

    return segmenter_enabled(user_id)


async def segmenter_applies_to(ctx: HelperContext) -> bool:
    """An attachment over 5,000 characters (D9), or a small one that belongs to a
    bundle — the small files of one message that pass 5k between them (owner rule
    D26, 2026-11-12). A failed read = not applicable (the consumer then reads the
    full text within the caps)."""
    import asyncio

    from agents.memory.attachment_segmenter import segmenter_applies_to_item

    try:
        return bool(
            await asyncio.to_thread(segmenter_applies_to_item, ctx.supabase, str(ctx.item_id))
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("segmenter applies_to: read failed: %s", type(exc).__name__)
        return False


async def run_segmenter(ctx: HelperContext) -> Any:
    """Segment one attachment and store its sections. A bundle member runs (or joins)
    its bundle's one run, so the files of a bundle trigger a single model call.
    Never raises (its contract)."""
    from agents.memory.attachment_segmenter import run_attachment_segmenter

    result = await run_attachment_segmenter(ctx.supabase, str(ctx.item_id))
    outcome = str(getattr(result, "outcome", "") or "")
    if outcome.startswith("failed") or outcome == "error":
        # Recorded as a FAILED run (helper_runs.error = this class name), so a
        # consumer sees the fallback and the next one retries within max_attempts.
        raise SegmenterFailed(f"{outcome}:{getattr(result, 'error', '') or ''}")
    return result


class SegmenterFailed(RuntimeError):
    """A segmenter run that ended without a usable map (failed_llm / failed_invalid /
    failed_store / error)."""


def load_segmenter(supabase: Any, item_id: str) -> Any:
    from agents.memory.attachment_segmenter import load_segment_map

    return load_segment_map(supabase, item_id)


def _segmenter_version() -> str:
    """Prompt + paragraph-rule versions. Built at import of this registry (which the
    orchestrator imports), so a broken segmenter package must not break the turn:
    on any error the helper gets a placeholder version (its runs would fail and fall
    back, the orchestrator still imports)."""
    try:
        from agents.memory.attachment_segmenter import PARAGRAPH_RULE_VERSION, PROMPT_VERSION

        return f"{PROMPT_VERSION}/{PARAGRAPH_RULE_VERSION}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("segmenter version unavailable: %s", type(exc).__name__)
        return "unavailable"


SEGMENTER = HelperSpec(
    name="segmenter",
    scope="item",
    # doc_type (the label list) comes from the summary.
    depends_on=("ocr", "summary"),
    critical_for=frozenset({"case_expander", "reg_expander", "aggregator", "writer"}),
    ceiling_s=SEGMENTER_CEILING_S,
    run=run_segmenter,
    load=load_segmenter,
    applies_to=segmenter_applies_to,
    enabled=_segmenter_enabled,
    stored=True,
    # Renewed while the run is alive; a run of two 240 s attempts outlives 90 s.
    lease_s=300.0,
    # The runner already re-attempts once inside a run.
    max_attempts=2,
    version=_segmenter_version(),
    concurrency=SEGMENTER_CONCURRENCY,
    fallback="the full text within the caps (100k per document, 300k overall)",
)

# --------------------------------------------------------------------------- #
# Angle map (خريطة الأوجه) — .claude/plans/relationship_map.md §4.1 (D3), D16–D17
# --------------------------------------------------------------------------- #
# ONE map per message group (angles-v4): the frame still starts this helper per
# item after that item's segmenter run — a turn's run (``TurnHelpers._after``) or a
# consumer's (``ensure_ready``: ``status.CONSUMER_RUN_DEPENDENTS``, which names this
# helper only); the runner resolves the item's message group
# and makes the group run once (agents/memory/angle_mapper/runner.py, top): the
# member segmented last runs it, members still waiting on another member's
# segmenter return ``skipped_group_pending``, members starting together join one
# in-process run (``joined_group``). Neither is a failure.
#
# Attempts (``max_attempts`` per item and version): the version is the prompt AND
# the segmenter's version (its input), and only failures in a row count
# (``attempts_count_failures_only``) — a skip, a join, or a re-map of a group whose
# map went stale ends ``done`` and starts the count over. A FAILED group run is not
# retried by itself (no sweep job): the group is mapped again when the segmenter
# of ANY member ends again (that member's run finds no fresh map), or else when
# the extended-search planner asks for it through ``ensure_ready`` (phase 2, not
# built yet). ``helper_runs`` rows hang on the item's FILE (migration 223), so a
# library file attached to two conversations shares one row.

#: Angle-map runs at once in this process — one V4.1 call per group (49–150 s
#: tested per file; a 165k-char group ≈ 3–4 min), plus at most one re-attempt and
#: one repair call. Each member that JOINS a group's run holds a slot while it
#: waits on that one run (the segmenter's bundle reason), so the limit is set
#: above the member count of a busy message.
ANGLE_MAP_CONCURRENCY = 8


def _angle_map_enabled(user_id: str) -> bool:
    from agents.utils.angle_map_flags import angle_map_enabled

    return angle_map_enabled(user_id)


async def angle_map_applies_to(ctx: HelperContext) -> bool:
    """An attachment the segmenter maps — over 5,000 characters or in its
    message's qualifying bundle, any document type (D16). The group, its members'
    maps and freshness are the runner's call. A failed read = not applicable (the
    planner then plans without angles)."""
    import asyncio

    from agents.memory.angle_mapper import angle_mapper_applies_to_item

    try:
        return bool(
            await asyncio.to_thread(angle_mapper_applies_to_item, ctx.supabase, str(ctx.item_id))
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_map applies_to: read failed: %s", type(exc).__name__)
        return False


async def run_angle_map(ctx: HelperContext) -> Any:
    """Make sure this item's message group is mapped into angles and stored — once
    per group. Never raises (its contract). Only the member whose run ended
    without a map it was responsible for (failed_llm / failed_invalid /
    failed_store / error) is recorded as failed; ``joined_group``,
    ``skipped_group_pending`` and ``skipped_*`` are done."""
    from agents.memory.angle_mapper import run_angle_mapper

    result = await run_angle_mapper(ctx.supabase, str(ctx.item_id))
    outcome = str(getattr(result, "outcome", "") or "")
    if outcome.startswith("failed") or outcome == "error":
        # helper_runs = failed (error = this class name). Nothing retries it by
        # itself: the next start — a member's segmenter ending again, or (phase 2)
        # the planner's ensure_ready — runs it again, up to max_attempts failures
        # in a row (the comment above ANGLE_MAP_CONCURRENCY).
        raise AngleMapFailed(f"{outcome}:{getattr(result, 'error', '') or ''}")
    return result


class AngleMapFailed(RuntimeError):
    """An angle-map run that ended without a stored map (failed_llm / failed_invalid
    / failed_store / error)."""


def load_angle_map(supabase: Any, item_id: str) -> Any:
    """The stored map of the item's message group while it is fresh (same members,
    same lengths and segment versions)."""
    from agents.memory.angle_mapper import load_fresh_angle_map_for_item

    return load_fresh_angle_map_for_item(supabase, item_id)


def _angle_map_version() -> str:
    """``<prompt version>/<segmenter version>`` — the map's own prompt and its input
    (the segmenter's prompt + paragraph rule), so a new segmenter starts the
    attempts over too. Built at import of this registry, so a broken angle_mapper
    package must not break the orchestrator import (see ``_segmenter_version``)."""
    try:
        from agents.memory.angle_mapper import PROMPT_VERSION

        return f"{PROMPT_VERSION}/{SEGMENTER.version}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("angle_map version unavailable: %s", type(exc).__name__)
        return "unavailable"


ANGLE_MAP = HelperSpec(
    name="angle_map",
    scope="item",
    # Its input is the segmenter's sections; it starts when the segmenter ends.
    depends_on=("segmenter",),
    # Critical for nobody (plan Q3 default): the planner uses a stored map and
    # plans without angles when there is none — nobody waits.
    critical_for=frozenset(),
    run=run_angle_map,
    load=load_angle_map,
    applies_to=angle_map_applies_to,
    enabled=_angle_map_enabled,
    stored=True,
    # Renewed every third of it while the run is alive. One group call reads up to
    # ~165k chars (≈ 3–4 min, CALL_TIMEOUT_S 480 s); a killed run is noticed
    # within one lease.
    lease_s=600.0,
    # The runner already re-attempts once inside a run. Only failures in a row
    # count: skips, joins and re-maps of a stale group end done and start over.
    max_attempts=2,
    attempts_count_failures_only=True,
    version=_angle_map_version(),
    concurrency=ANGLE_MAP_CONCURRENCY,
    fallback="the planner plans without angles",
)

_SPECS: tuple[HelperSpec, ...] = (OCR, SUMMARY, PRE_ROUTER_MEMORY, MEMORY, SEGMENTER, ANGLE_MAP)

REGISTRY: dict[str, HelperSpec] = {spec.name: spec for spec in _SPECS}


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #


def topo_order(registry: Mapping[str, HelperSpec] | None = None) -> list[HelperSpec]:
    """Every helper after the helpers it depends on. Raises ``ValueError`` on an
    unknown dependency or a cycle (a registration bug, caught by the tests)."""
    reg = REGISTRY if registry is None else registry
    order: list[HelperSpec] = []
    state: dict[str, str] = {}

    def visit(name: str, path: tuple[str, ...]) -> None:
        if state.get(name) == "done":
            return
        if state.get(name) == "visiting":
            raise ValueError(f"helper dependency cycle: {' -> '.join(path + (name,))}")
        spec = reg.get(name)
        if spec is None:
            raise ValueError(f"helper {path[-1] if path else '?'} depends on unknown {name!r}")
        state[name] = "visiting"
        for dep in spec.depends_on:
            visit(dep, path + (name,))
        state[name] = "done"
        order.append(spec)

    for name in reg:
        visit(name, ())
    return order


def dependents(name: str, registry: Mapping[str, HelperSpec] | None = None) -> list[HelperSpec]:
    """Helpers that depend directly on ``name``, in dependency order."""
    return [spec for spec in topo_order(registry) if name in spec.depends_on]


def get_spec(name: str, registry: Mapping[str, HelperSpec] | None = None) -> HelperSpec | None:
    return (REGISTRY if registry is None else registry).get(name)


def register(spec: HelperSpec) -> None:
    """Add (or replace) a helper. Validates the graph first; a bad spec is refused."""
    candidate = {**REGISTRY, spec.name: spec}
    topo_order(candidate)
    REGISTRY[spec.name] = spec


def unregister(name: str) -> None:
    """Remove a helper nothing depends on (tests)."""
    if any(name in s.depends_on for s in REGISTRY.values() if s.name != name):
        raise ValueError(f"helper {name!r} still has dependents")
    REGISTRY.pop(name, None)


# Fail at import, not at the first turn, if the matrix is broken.
topo_order()

__all__ = [
    "MEMORY",
    "MEMORY_CEILING_S",
    "OCR",
    "OCR_CEILING_S",
    "PRE_ROUTER_MEMORY",
    "PRE_ROUTER_MEMORY_CEILING_S",
    "REGISTRY",
    "SUMMARY",
    "SUMMARY_CEILING_S",
    "SUMMARY_CONCURRENCY",
    "dependents",
    "get_spec",
    "register",
    "run_ocr",
    "run_pre_router_memory",
    "run_summary",
    "topo_order",
    "unregister",
]
