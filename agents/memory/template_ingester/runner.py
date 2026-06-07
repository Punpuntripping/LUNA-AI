"""Runner for the template_ingester (Layer-4 Memory) agent.

One ``handle_template_ingestion(item_id, deps)`` invocation:

    1. Fetch the ``workspace_items.content_md`` for ``item_id`` scoped to the
       user (service-role client + ``.eq("user_id", deps.user_id)``).
    2. Run the agent → :class:`CleanedTemplate` (cleaned body + unique title).
    3. INSERT into ``user_templates`` reusing the insert primitive constants
       from ``agents/tool_repository/add_user_template.py`` (``created_by='agent'``,
       ``user_id`` from deps — NEVER from the model).

Failure contract: EVERY failure (missing item / empty body / LLM error /
insert error) collapses into ``IngestResult(ok=False, error_ar=INGEST_FAILED_AR)``
— the single Arabic message the endpoint + frontend chip surface. The runner
never raises for these conditions.

The LLM call is wrapped in ``track_stage("template.ingest", ...)`` so its cost
self-emits to the ``llm_calls`` ledger (tier_2). The caller is responsible for
opening a ``collect_llm_calls`` scope around this call so the ledger row is
actually flushed (the endpoint does).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from agents.tool_repository.add_user_template import (
    CONTENT_COL,
    CREATED_BY_AGENT,
    CREATED_BY_COL,
    TABLE,
    TITLE_COL,
    USER_COL,
)
from agents.utils.tracking import track_stage
from agents.utils.usage_sink import record_call

from .agent import INGESTER_LIMITS, create_template_ingester
from .deps import IngesterDeps
from .models import INGEST_FAILED_AR, CleanedTemplate, IngestResult
from .prompts import render_ingest_user_msg

logger = logging.getLogger(__name__)


# ===========================================================================
# Public entrypoint
# ===========================================================================


async def handle_template_ingestion(
    item_id: str,
    deps: IngesterDeps,
) -> IngestResult:
    """Turn one raw attached doc into a cleaned ``user_templates`` row.

    Returns ``IngestResult(ok=True, template_id, title)`` on success, or
    ``IngestResult(ok=False, error_ar=INGEST_FAILED_AR)`` on ANY failure.
    Never raises for caller-recoverable conditions.
    """
    log = deps.logger or logger
    t0 = time.perf_counter()

    # --- 1. Fetch the raw document (scoped to the user) --------------------
    raw = _load_content_md(deps.supabase, item_id, user_id=deps.user_id, log=log)
    if not raw:
        log.warning(
            "template_ingester: no content_md for item_id=%s (user_id=%s) — failing",
            item_id, deps.user_id,
        )
        return IngestResult(ok=False, error_ar=INGEST_FAILED_AR)

    title_hint, content_md = raw

    # --- 2. Run the LLM (cost self-emits via track_stage) -----------------
    cleaned = await _run_ingester(content_md, title_hint, deps, log=log, t0=t0)
    if cleaned is None:
        return IngestResult(ok=False, error_ar=INGEST_FAILED_AR)

    # --- 3. Insert into user_templates ------------------------------------
    template_id = _insert_template(
        deps.supabase,
        user_id=deps.user_id,
        title=cleaned.title,
        content_md=cleaned.content_md,
        log=log,
    )
    if template_id is None:
        return IngestResult(ok=False, error_ar=INGEST_FAILED_AR)

    return IngestResult(ok=True, template_id=template_id, title=cleaned.title)


# ===========================================================================
# LLM call
# ===========================================================================


async def _run_ingester(
    content_md: str,
    title_hint: str | None,
    deps: IngesterDeps,
    *,
    log: logging.Logger,
    t0: float,
) -> CleanedTemplate | None:
    """One LLM call → ``CleanedTemplate``. Returns ``None`` on any failure.

    Wrapped in ``track_stage("template.ingest", ...)`` and self-emits one row to
    the ``llm_calls`` ledger so cost is captured (tier_2). All exceptions are
    swallowed → ``None`` so the runner can map them to the Arabic failure.
    """
    with track_stage(
        "template.ingest",
        conversation_id=str(deps.conversation_id) if deps.conversation_id else None,
        agent_family="memory",
        subtype="template.ingest",
        source_chars=len(content_md or ""),
    ) as span:
        try:
            agent = create_template_ingester()
            user_msg = render_ingest_user_msg(title=title_hint, content_md=content_md)
        except Exception as exc:  # noqa: BLE001
            log.warning("template_ingester: agent build failed (%s)", exc)
            _set_outcome(span, "build_failed", exc)
            return None

        try:
            result = await agent.run(user_msg, usage_limits=INGESTER_LIMITS)
        except Exception as exc:  # noqa: BLE001
            log.warning("template_ingester: LLM call failed (%s)", exc)
            _set_outcome(span, "llm_failed", exc)
            return None

        out: CleanedTemplate = result.output

        # Capture usage onto the span AND feed the llm_calls ledger.
        span.record_run(result, slot="template_ingester")
        _record_run(result)

        # Defensive: the salvager + schema guarantee non-empty strings on the
        # happy path, but a blank title/body would produce a useless template —
        # treat it as a failure rather than inserting a hollow row.
        if not (out.title or "").strip() or not (out.content_md or "").strip():
            log.warning("template_ingester: LLM returned empty title/content — failing")
            try:
                span.set_outcome("empty")
            except Exception:
                pass
            return None

        try:
            span.set(duration_s=round(time.perf_counter() - t0, 3))
        except Exception:
            pass
        return out


# ===========================================================================
# Supabase I/O
# ===========================================================================


def _load_content_md(
    supabase: Any,
    item_id: str,
    *,
    user_id: str,
    log: logging.Logger,
) -> tuple[str | None, str] | None:
    """SELECT ``title``, ``content_md`` for ``item_id`` in this user's scope.

    Returns ``(title, content_md)`` or ``None`` when the row is missing /
    out-of-scope / has no body. Service-role bypasses RLS, so the
    ``.eq("user_id", user_id)`` filter is load-bearing scope enforcement (same
    discipline as ``item_analyzer._load_workspace_items``).
    """
    try:
        resp = (
            supabase.table("workspace_items")
            .select("title, content_md")
            .eq("item_id", item_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "template_ingester._load_content_md: SELECT failed for item_id=%s: %s",
            item_id, exc,
        )
        return None

    if resp is None:
        return None
    data = getattr(resp, "data", None)
    if not data:
        return None

    content_md = (data.get("content_md") or "").strip()
    if not content_md:
        return None
    title = data.get("title")
    return (title, content_md)


def _insert_template(
    supabase: Any,
    *,
    user_id: str,
    title: str,
    content_md: str,
    log: logging.Logger,
) -> str | None:
    """Insert a cleaned template into ``user_templates`` (``created_by='agent'``).

    Reuses the table/column constants from
    ``agents/tool_repository/add_user_template.py`` so a schema rename stays a
    one-line change. ``user_id`` comes from deps — never from the model.
    Returns the new ``template_id`` or ``None`` on failure.
    """
    try:
        res = (
            supabase.table(TABLE)
            .insert(
                {
                    USER_COL: user_id,
                    TITLE_COL: title,
                    CONTENT_COL: content_md,
                    CREATED_BY_COL: CREATED_BY_AGENT,
                }
            )
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("template_ingester._insert_template: insert failed: %s", exc)
        return None

    rows = getattr(res, "data", None) or []
    if not rows:
        log.warning("template_ingester._insert_template: insert returned no row")
        return None

    template_id = rows[0].get("template_id")
    if not template_id:
        log.warning("template_ingester._insert_template: row missing template_id")
        return None
    return str(template_id)


# ===========================================================================
# Cost recording
# ===========================================================================


def _record_run(result: Any) -> None:
    """Feed one per-call row to the ``llm_calls`` ledger for the ingest call.

    Mirrors ``item_analyzer._record_run``: the ingester captures usage manually
    (``_safe_usage``) so it emits the ledger row explicitly. Best-effort —
    failures are logged and swallowed. No-op outside a ``collect_llm_calls``
    scope (telemetry is best-effort).
    """
    try:
        usage = _safe_usage(result)
        record_call(
            agent="memory.template_ingester",
            model=_model_label_from_result(result),
            agent_family="memory",
            subtype="template.ingest",
            tokens_in=usage["input"],
            tokens_out=usage["output"],
            tokens_reasoning=usage["reasoning"],
            tokens_cached=usage.get("cached", 0),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("template_ingester._record_run failed (non-blocking): %s", exc)


# ===========================================================================
# Small helpers
# ===========================================================================


def _safe_usage(result: Any) -> dict[str, int]:
    """Pull ``input/output/reasoning/cached`` tokens from an AgentRunResult.

    Never raises; missing fields fall back to 0. Mirrors
    ``item_analyzer.runner._safe_usage``.
    """
    try:
        usage = result.usage()
        details = dict(usage.details) if getattr(usage, "details", None) else {}
        return {
            "input": int(getattr(usage, "input_tokens", 0) or 0),
            "output": int(getattr(usage, "output_tokens", 0) or 0),
            "reasoning": int(details.get("reasoning_tokens", 0) or 0),
            "cached": int(getattr(usage, "cache_read_tokens", 0) or 0),
        }
    except Exception:
        return {"input": 0, "output": 0, "reasoning": 0, "cached": 0}


def _model_label_from_result(result: Any) -> str:
    """Best-effort provenance label from the AgentRunResult.

    FallbackModel doesn't reliably surface the fired model, so fall back to the
    slot's intent label — accurate for telemetry since the slot is fixed. Mirrors
    ``item_analyzer.runner._model_label_from_result``.
    """
    try:
        for attr in ("_model", "model"):
            model = getattr(result, attr, None)
            if model is None:
                continue
            name = getattr(model, "model_name", None) or getattr(model, "name", None)
            if name:
                return str(name)
    except Exception:
        pass
    return "template_ingester:tier_2"


def _set_outcome(span: Any, outcome: str, err: BaseException | None = None) -> None:
    """Stamp a failure outcome (+ error attrs) onto the track_stage span.

    The runner returns ``None`` rather than re-raising on these paths, so the
    span's exit finalizer would otherwise default the outcome to ``"ok"``.
    """
    try:
        span.set_outcome(outcome)
        if err is not None:
            span.set(error=str(err))
            span.set(**{"error.type": type(err).__name__})
    except Exception:
        pass


__all__ = ["handle_template_ingestion"]
