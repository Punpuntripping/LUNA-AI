"""Unified agent tracking — one Logfire telemetry contract for every stage.

Design + rationale: ``.claude/plans/agent_tracking_protocol.md``.

Two entry points, one attribute namespace:

- :func:`run_tracked` — wraps a pydantic-ai ``Agent.run()`` (LLM stages). Captures
  identity + bounded input + output + output-schema-ref + tokens/cost/model +
  duration + outcome automatically. Returns the raw ``AgentRunResult`` unchanged.
- :func:`track_stage` — context manager for non-LLM stages (publish, OCR
  orchestration, DB writes). Same identity/input/output/outcome semantics; the
  caller supplies tokens/cost via the yielded :class:`AgentSpan` handle.

Per-agent hooks (all OPTIONAL — a reflective fallback covers anything that
doesn't implement them, so new agents are tracked with zero extra code):

- ``deps.tracking_input() -> dict``        bounded → span attributes ``input.*``
- ``deps.tracking_input_full() -> dict``   verbatim → env-gated span event
- ``output.tracking_output() -> dict``     heavy outputs (override the default dump)

Env gates (read once at import):

- ``LUNA_TRACK_DISABLE=1`` — the whole layer no-ops (the agent still runs).
- ``LUNA_TRACK_VERBOSE=1`` — emit full-content span events. OFF by default; left
  unset in production so client content never leaves via Logfire.

Layering note: this lives in ``agents/utils`` (not ``shared/``) because it needs
``agents.utils.agent_models`` for tier-accurate cost, and ``shared/`` must not
import from ``agents/``. Both ``agents`` and ``backend`` may import ``agents.utils``.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()


# ── env gates ────────────────────────────────────────────────────────────────
def _envbool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


_DISABLE = _envbool("LUNA_TRACK_DISABLE")
_VERBOSE = _envbool("LUNA_TRACK_VERBOSE")

# ── size caps ────────────────────────────────────────────────────────────────
MAX_ATTR_CHARS = 512          # per bounded string attribute
MAX_OUTPUT_JSON_CHARS = 4000  # the output_json dump
MAX_EVENT_CHARS = 20000       # the verbose full-content event
MAX_SEQ_ELEMENTS = 50         # array attribute element cap

# Schema files dumped once per (stage, output_type) per process.
_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "agents_reports" / "schemas"
_SCHEMA_REFS: dict[str, str] = {}


@runtime_checkable
class Trackable(Protocol):
    """Optional per-agent hooks. Implement on a deps object to curate what gets
    tracked; omit to fall back to the reflective snapshot."""

    def tracking_input(self) -> dict[str, Any]: ...        # bounded → input.*
    def tracking_input_full(self) -> dict[str, Any]: ...   # verbatim → span event


# ── primitives ────────────────────────────────────────────────────────────────
def _trunc(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"...(+{len(s) - n} chars)"


_DENY_TYPE_NAMES = {"Client", "AsyncClient", "SyncClient", "Connection", "Pool", "Engine"}
_DENY_MODULE_PREFIXES = ("httpx", "supabase", "asyncio", "postgrest", "gotrue", "storage3", "redis")


def _is_denied(name: str, value: Any) -> bool:
    """Skip infra/sinks: private fields, callables, live clients."""
    if name.startswith("_"):
        return True
    if callable(value):
        return True
    t = type(value)
    if t.__name__ in _DENY_TYPE_NAMES:
        return True
    mod = (t.__module__ or "")
    return mod.startswith(_DENY_MODULE_PREFIXES)


def _attr_value(v: Any) -> Any:
    """Coerce a value into a Logfire-safe attribute (scalar / homogeneous array)."""
    if isinstance(v, str):
        return _trunc(v, MAX_ATTR_CHARS)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) or v is None:
        return v
    if isinstance(v, (list, tuple, set)):
        seq = list(v)
        if seq and all(isinstance(e, (str, int, float, bool)) for e in seq):
            seq = seq[:MAX_SEQ_ELEMENTS]
            return [(_trunc(e, MAX_ATTR_CHARS) if isinstance(e, str) else e) for e in seq]
        return len(seq)
    if isinstance(v, dict):
        return _trunc(json.dumps(v, ensure_ascii=False, default=str), MAX_ATTR_CHARS)
    return _trunc(str(v), MAX_ATTR_CHARS)


# ── input snapshots ────────────────────────────────────────────────────────────
def _bounded_snapshot(obj: Any) -> dict[str, Any]:
    """Bounded ``input.*`` attributes. Uses ``tracking_input()`` if present,
    else a reflective snapshot of dataclass fields."""
    if obj is None:
        return {}
    fn = getattr(obj, "tracking_input", None)
    if callable(fn):
        try:
            return {f"input.{k}": _attr_value(v) for k, v in (fn() or {}).items()}
        except Exception:
            logger.debug("tracking_input() failed for %s", type(obj).__name__, exc_info=True)
            return {}
    return _reflect_bounded(obj)


def _reflect_bounded(obj: Any) -> dict[str, Any]:
    if not dataclasses.is_dataclass(obj):
        return {}
    out: dict[str, Any] = {}
    for f in dataclasses.fields(obj):
        try:
            v = getattr(obj, f.name)
        except Exception:
            continue
        if _is_denied(f.name, v):
            continue
        if isinstance(v, str):
            out[f"input.{f.name}_chars"] = len(v)
        elif isinstance(v, bool):
            out[f"input.{f.name}"] = v
        elif isinstance(v, (int, float)):
            out[f"input.{f.name}"] = v
        elif isinstance(v, (list, tuple, set, dict)):
            out[f"input.{f.name}_count"] = len(v)
        # complex objects / None: skipped in the bounded view
    return out


def _full_snapshot(obj: Any) -> dict[str, Any]:
    """Verbatim "what it saw". Uses ``tracking_input_full()`` if present, else a
    deep reflective dump. Caller is responsible for the env gate + size cap."""
    if obj is None:
        return {}
    fn = getattr(obj, "tracking_input_full", None)
    if callable(fn):
        try:
            return {str(k): _jsonable(v) for k, v in (fn() or {}).items()}
        except Exception:
            logger.debug("tracking_input_full() failed for %s", type(obj).__name__, exc_info=True)
            return {}
    if not dataclasses.is_dataclass(obj):
        return {}
    out: dict[str, Any] = {}
    for f in dataclasses.fields(obj):
        try:
            v = getattr(obj, f.name)
        except Exception:
            continue
        if _is_denied(f.name, v):
            continue
        out[f.name] = _jsonable(v)
    return out


def _jsonable(v: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "...(max depth)"
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {str(k): _jsonable(x, depth + 1) for k, x in list(v.items())[:100]}
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x, depth + 1) for x in list(v)[:100]]
    dump = getattr(v, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except Exception:
            return str(v)
    if dataclasses.is_dataclass(v):
        try:
            return {
                f.name: _jsonable(getattr(v, f.name), depth + 1)
                for f in dataclasses.fields(v)
                if not _is_denied(f.name, getattr(v, f.name, None))
            }
        except Exception:
            return str(v)
    return str(v)


def _emit_full_event(stage: str, obj: Any) -> None:
    """Emit the verbatim input as a span event — only when LUNA_TRACK_VERBOSE is on.
    When off, ``tracking_input_full()`` is never even called."""
    if not _VERBOSE or obj is None:
        return
    try:
        payload = _full_snapshot(obj)
        if not payload:
            return
        blob = _trunc(json.dumps(payload, ensure_ascii=False, default=str), MAX_EVENT_CHARS)
        _logfire.info("agent.input.full", stage=stage, payload=blob)
    except Exception:
        logger.debug("verbose event emit failed for %s", stage, exc_info=True)


# ── identity ─────────────────────────────────────────────────────────────────
def _identity_attrs(
    *,
    conversation_id: Any,
    case_id: Any,
    agent_family: str | None,
    subtype: str | None,
    stage: str,
    turn_number: int | None,
) -> dict[str, Any]:
    """The mandatory basics. ``user_id`` is intentionally never stamped (PII)."""
    d: dict[str, Any] = {"stage": stage}
    if conversation_id:
        d["conversation_id"] = str(conversation_id)
    if case_id:
        d["case_id"] = str(case_id)
    if agent_family:
        d["agent_family"] = agent_family
    if subtype:
        d["subtype"] = subtype
    if turn_number is not None:
        d["turn_number"] = turn_number
    return d


def _identity_from_deps(deps: Any, *, stage: str, agent_family: str | None, subtype: str | None) -> dict[str, Any]:
    return _identity_attrs(
        conversation_id=getattr(deps, "conversation_id", None),
        case_id=getattr(deps, "case_id", None),
        agent_family=agent_family,
        subtype=subtype,
        stage=stage,
        turn_number=getattr(deps, "turn_number", None),
    )


# ── output + schema ─────────────────────────────────────────────────────────
def _schema_ref(stage: str, output: Any) -> tuple[str | None, str | None]:
    """``(output_type_name, output_schema_ref)``. Schema is dumped to a file once
    per process; the per-span ref is ``"{stage}@{sha8}"``."""
    if output is None:
        return None, None
    tname = type(output).__name__
    schema_fn = getattr(type(output), "model_json_schema", None)
    if not callable(schema_fn):
        return tname, None
    key = f"{stage}:{tname}"
    ref = _SCHEMA_REFS.get(key)
    if ref is None:
        try:
            blob = json.dumps(schema_fn(), ensure_ascii=False, sort_keys=True, default=str)
            ref = f"{stage}@{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:8]}"
            _write_schema_file(stage, tname, blob)
        except Exception:
            ref = ""  # memoize the failure so we don't retry every run
        _SCHEMA_REFS[key] = ref
    return tname, (ref or None)


def _write_schema_file(stage: str, tname: str, blob: str) -> None:
    try:
        _SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
        safe = stage.replace("/", "_").replace("\\", "_")
        (_SCHEMA_DIR / f"{safe}__{tname}.json").write_text(blob, encoding="utf-8")
    except Exception:
        pass  # best-effort; the in-memory ref is what spans actually carry


def _output_attrs(stage: str, output: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    tname, ref = _schema_ref(stage, output)
    if tname:
        out["output_type"] = tname
    if ref:
        out["output_schema_ref"] = ref
    fn = getattr(output, "tracking_output", None)
    if callable(fn):
        try:
            for k, v in (fn() or {}).items():
                out[f"output.{k}"] = _attr_value(v)
            return out
        except Exception:
            logger.debug("tracking_output() failed for %s", stage, exc_info=True)
    dump = getattr(output, "model_dump", None)
    if callable(dump):
        try:
            blob = json.dumps(dump(mode="json"), ensure_ascii=False, default=str)
            out["output_json"] = _trunc(blob, MAX_OUTPUT_JSON_CHARS)
        except Exception:
            logger.debug("output model_dump failed for %s", stage, exc_info=True)
    return out


# ── resource (tokens / cost / model) ──────────────────────────────────────────
def _usage_attrs(result: Any, *, slot: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        usage = result.usage()
    except Exception:
        return out
    ti = int(getattr(usage, "input_tokens", 0) or 0)
    to = int(getattr(usage, "output_tokens", 0) or 0)
    details = getattr(usage, "details", None) or {}
    reasoning = int(details.get("reasoning_tokens", 0) or 0)
    # pydantic_ai 1.39 promotes prompt-cache reads to the first-class
    # ``usage.cache_read_tokens`` field (NOT into ``details``) for every
    # OpenAI-compatible provider — Alibaba DashScope and OpenRouter both. Read
    # it directly; fall back to legacy ``details`` keys only if it's absent.
    cached = int(getattr(usage, "cache_read_tokens", 0) or 0)
    if not cached:
        for k in ("cached_tokens", "prompt_cache_hit_tokens", "cache_read_input_tokens"):
            try:
                cached += int(details.get(k, 0) or 0)
            except Exception:
                pass
    out["tokens_in"] = ti
    out["tokens_out"] = to
    if reasoning:
        out["tokens_reasoning"] = reasoning
    if cached:
        out["cache_hit_tokens"] = cached
    req = getattr(usage, "requests", None)
    if req is not None:
        out["requests"] = int(req)
    model = _model_from_result(result)
    out["cost_usd"] = _cost(slot, model, ti, to, reasoning, cached)
    if model:
        out["model_used"] = model
    return out


def _cost(
    slot: str | None,
    model_used: str | None,
    ti: int,
    to: int,
    reasoning: int,
    cached: int,
) -> float:
    """Per-model cost. Bills at the actually-fired ``model_used`` when it is a
    known pricing key (FallbackModel can swap off the slot's primary on a
    4xx/5xx — ``or-`` prefixes normalise to the same canonical row). When the
    fired name is absent or unknown to the registry — provider echo names like
    ``qwen-plus`` / ``qwen/qwen-...`` don't match our canonical keys
    (``qwen3.6-plus``) — fall back to the slot's declared primary, the reliable
    bridge. Without this fallback the echo-name mismatch silently bills $0."""
    try:
        from agents.utils.agent_models import AGENT_MODELS, cost_usd, resolve_chain
        from shared import pricing
        model = model_used
        if (not model or pricing.get_price(model) is None) and slot and slot in AGENT_MODELS:
            model = resolve_chain(AGENT_MODELS[slot])[0]
        return round(cost_usd(model, ti, to, reasoning, cached), 6)
    except Exception:
        return 0.0


def _model_from_result(result: Any) -> str | None:
    """The model that actually responded (FallbackModel may pick the fallback) —
    read from the last ModelResponse's ``model_name``."""
    try:
        msgs = result.all_messages()
    except Exception:
        return None
    model = None
    for m in msgs or []:
        mn = getattr(m, "model_name", None)
        if mn:
            model = mn
    return model


def _feed_sink(
    stage: str,
    usage: dict[str, Any] | None,
    *,
    agent_family: str | None,
    subtype: str | None,
    duration_ms: int | None = None,
    outcome: str = "ok",
) -> None:
    """Emit one ``llm_calls`` ledger row from a computed usage dict.

    Single integration point between the Logfire-span layer and the per-call
    cost ledger (``agents/utils/usage_sink``). No-op outside a capture scope and
    for no-op calls (no tokens, no cost). Never raises — telemetry is
    best-effort and must not perturb the run.
    """
    if not usage:
        return
    if not usage.get("tokens_in") and not usage.get("tokens_out") and not usage.get("cost_usd"):
        return
    try:
        from agents.utils.usage_sink import record_call

        record_call(
            agent=stage,
            model=usage.get("model_used"),
            agent_family=agent_family,
            subtype=subtype,
            tokens_in=usage.get("tokens_in", 0) or 0,
            tokens_out=usage.get("tokens_out", 0) or 0,
            tokens_reasoning=usage.get("tokens_reasoning", 0) or 0,
            tokens_cached=usage.get("cache_hit_tokens", 0) or 0,
            cost_usd=usage.get("cost_usd"),
            requests=usage.get("requests", 1) or 1,
            duration_ms=duration_ms,
            outcome=outcome,
        )
    except Exception:
        logger.debug("usage sink feed failed for %s", stage, exc_info=True)


def _classify_outcome(output: Any) -> str:
    try:
        from pydantic_ai import DeferredToolRequests
        if isinstance(output, DeferredToolRequests):
            return "paused"
    except Exception:
        pass
    if output is None:
        return "empty"
    return "ok"


# ── span handle ────────────────────────────────────────────────────────────────
class AgentSpan:
    """Handle over the active Logfire span. Caller stamps extra attrs + output."""

    def __init__(
        self,
        span: Any,
        stage: str,
        *,
        agent_family: str | None = None,
        subtype: str | None = None,
    ) -> None:
        self._span = span
        self.stage = stage
        self._agent_family = agent_family
        self._subtype = subtype
        self._outcome: str | None = None
        self._finalized = False

    def set(self, **attrs: Any) -> None:
        clean = {k: _attr_value(v) for k, v in attrs.items() if v is not None}
        if not clean:
            return
        try:
            self._span.set_attributes(clean)
        except Exception:
            pass

    def record_output(self, output: Any, *, outcome: str | None = None) -> None:
        try:
            self._span.set_attributes(_output_attrs(self.stage, output))
        except Exception:
            pass
        self._outcome = outcome or _classify_outcome(output)

    def record_run(self, result: Any, *, slot: str | None = None) -> None:
        """Capture an ``AgentRunResult`` onto this span — usage/cost/model + the
        output dump + schema ref + outcome. For callers that own the span (the
        ``track_stage`` form) so they can also stamp their own ``set(...)`` attrs
        before/after, or swallow errors with their own fallback."""
        usage = _usage_attrs(result, slot=slot)
        try:
            self._span.set_attributes(usage)
        except Exception:
            pass
        _feed_sink(
            self.stage,
            usage,
            agent_family=self._agent_family,
            subtype=self._subtype,
        )
        self.record_output(getattr(result, "output", None))

    def set_outcome(self, outcome: str) -> None:
        """Override the outcome (e.g. ``"error"`` on a swallowed-exception path
        where the caller returns a fallback instead of re-raising)."""
        self._outcome = outcome

    def _finalize(self, t0: float, outcome: str, err: BaseException | None = None) -> None:
        if self._finalized:
            return
        self._finalized = True
        attrs: dict[str, Any] = {
            "duration_ms": int((time.perf_counter() - t0) * 1000),
            "outcome": outcome,
        }
        if err is not None:
            attrs["error"] = _trunc(str(err), MAX_ATTR_CHARS)
            attrs["error.type"] = type(err).__name__
        try:
            self._span.set_attributes(attrs)
        except Exception:
            pass


class _NoopHandle:
    def set(self, **_: Any) -> None: ...
    def record_output(self, *_: Any, **__: Any) -> None: ...
    def _finalize(self, *_: Any, **__: Any) -> None: ...


# ── entry points ─────────────────────────────────────────────────────────────
async def run_tracked(
    agent: Any,
    user_prompt: Any = None,
    *,
    deps: Any,
    stage: str,
    slot: str | None = None,
    agent_family: str | None = None,
    subtype: str | None = None,
    **run_kwargs: Any,
) -> Any:
    """Run a pydantic-ai ``Agent.run()`` inside the unified tracking span.

    ``slot`` is the ``agent_models`` slot (e.g. ``"writer_planner_decider"``) used
    for tier-accurate cost; omit for tier_1 fallback. Extra ``run_kwargs``
    (``message_history``, ``deferred_tool_results``, ``usage_limits``, …) pass
    through to ``agent.run`` untouched. Returns the raw ``AgentRunResult``.
    """
    run_kwargs.setdefault("deps", deps)

    async def _call() -> Any:
        if user_prompt is not None:
            return await agent.run(user_prompt, **run_kwargs)
        return await agent.run(**run_kwargs)

    if _DISABLE:
        return await _call()

    identity = _identity_from_deps(deps, stage=stage, agent_family=agent_family, subtype=subtype)
    t0 = time.perf_counter()
    with _logfire.span(stage, **identity) as span:
        handle = AgentSpan(span, stage, agent_family=agent_family, subtype=subtype)
        try:
            span.set_attributes(_bounded_snapshot(deps))
        except Exception:
            pass
        _emit_full_event(stage, deps)
        try:
            result = await _call()
        except asyncio.CancelledError as e:
            handle._finalize(t0, "cancelled", e)
            raise
        except Exception as e:
            handle._finalize(t0, "error", e)
            raise
        output = getattr(result, "output", None)
        usage = _usage_attrs(result, slot=slot)
        try:
            span.set_attributes(usage)
            span.set_attributes(_output_attrs(stage, output))
        except Exception:
            logger.debug("run_tracked: post-run attr capture failed for %s", stage, exc_info=True)
        outcome = _classify_outcome(output)
        _feed_sink(
            stage,
            usage,
            agent_family=agent_family,
            subtype=subtype,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            outcome=outcome,
        )
        handle._finalize(t0, outcome)
        return result


@contextmanager
def track_stage(
    stage: str,
    *,
    conversation_id: Any = None,
    case_id: Any = None,
    agent_family: str | None = None,
    subtype: str | None = None,
    turn_number: int | None = None,
    input_obj: Any = None,
    **extra: Any,
) -> Iterator[AgentSpan]:
    """Track a non-LLM stage. Yields an :class:`AgentSpan`; call ``t.set(...)`` /
    ``t.record_output(...)`` to stamp resource + output. ``input_obj`` (optional)
    gets the same bounded + verbose input treatment as a deps object."""
    if _DISABLE:
        yield _NoopHandle()  # type: ignore[misc]
        return

    identity = _identity_attrs(
        conversation_id=conversation_id,
        case_id=case_id,
        agent_family=agent_family,
        subtype=subtype,
        stage=stage,
        turn_number=turn_number,
    )
    t0 = time.perf_counter()
    with _logfire.span(stage, **identity) as span:
        handle = AgentSpan(span, stage, agent_family=agent_family, subtype=subtype)
        if extra:
            handle.set(**extra)
        if input_obj is not None:
            try:
                span.set_attributes(_bounded_snapshot(input_obj))
            except Exception:
                pass
            _emit_full_event(stage, input_obj)
        try:
            yield handle
        except asyncio.CancelledError as e:
            handle._finalize(t0, "cancelled", e)
            raise
        except Exception as e:
            handle._finalize(t0, "error", e)
            raise
        handle._finalize(t0, handle._outcome or "ok")


__all__ = [
    "Trackable",
    "AgentSpan",
    "run_tracked",
    "track_stage",
    "MAX_ATTR_CHARS",
    "MAX_OUTPUT_JSON_CHARS",
    "MAX_EVENT_CHARS",
]
