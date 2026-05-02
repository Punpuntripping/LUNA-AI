"""Pydantic Logfire wiring for the Luna backend.

Single entry point: :func:`configure_logfire`. Idempotent — safe to call from
both ``backend/app/main.py`` and standalone scripts (``run_monitor.py``,
agent CLIs). When ``LOGFIRE_TOKEN`` is absent, Logfire degrades to a local
no-op so dev runs without a token still boot.

Tags / spans live in the call sites:

- ``router.classify`` — wraps ``run_router()`` (agents/router/router.py)
- ``task.run`` — wraps orchestrator task dispatch (agents/orchestrator.py)
- ``task.ended`` — info event on task completion
- ``deep_search.run_full_loop`` — top-level deep_search v4 span
- ``deep_search.phase.{reg,compliance,case}`` — per-executor phase spans
- ``deep_search.planner`` / ``deep_search.aggregator`` — sub-stage spans
"""
from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

# Module-level guard so repeat calls are cheap. Logfire itself is also
# idempotent on configure(), but instrument_*() helpers are not always.
_CONFIGURED = False


# ── PII patterns ──────────────────────────────────────────────────────────
# Run as additional regex patterns through Logfire's scrubber. Logfire's
# default patterns already cover password / api_key / authorization etc.
# These extras target Arabic legal documents and Saudi PII shapes.

_PII_EXTRA_PATTERNS = [
    # Field-name based (case-insensitive — Logfire lowers names before match)
    r"national[_ ]?id",
    r"id[_ ]?number",
    r"iqama",
    r"\bphone\b",
    r"mobile",
    r"\bemail\b",
    # Value-based — Saudi national ID is exactly 10 digits, starts with 1 or 2
    r"\b[12]\d{9}\b",
    # E.164-ish phone numbers
    r"\+?\d{1,3}[ \-]?\d{6,12}",
    # Email addresses
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
]

# Span attribute paths whose contents we never want to redact even if they
# happen to match a pattern. Conservative — extend only when a real false
# positive shows up in the dashboard.
_KEEP_PATHS: set[tuple[str, ...]] = {
    ("attributes", "task_type"),
    ("attributes", "agent_family"),
    ("attributes", "task_id"),
    ("attributes", "model"),
}


def _scrub_callback(match: Any) -> Any:
    """Return original value when path is in the allowlist."""
    try:
        if match.path in _KEEP_PATHS:
            return match.value
    except Exception:
        pass
    return None  # let logfire redact


def _resolve_environment() -> str:
    """Pick the environment label the way the prompt asked.

    Priority: explicit LOGFIRE_ENVIRONMENT → RAILWAY_ENVIRONMENT → APP_ENV → 'dev'.
    """
    return (
        os.getenv("LOGFIRE_ENVIRONMENT")
        or os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or "dev"
    )


def configure_logfire(service_version: str | None = None) -> bool:
    """Configure Logfire and instrument SDKs that are already imported.

    Returns ``True`` once configured (or when Logfire is genuinely active),
    ``False`` when the SDK is missing or configuration failed. ``main.py``
    does not gate on the return value — failures must never block startup.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return True

    try:
        import logfire
    except ImportError:
        logger.info("logfire not installed — observability disabled")
        return False

    try:
        # ``send_to_logfire='if-token-present'`` means: when LOGFIRE_TOKEN is
        # missing, do not phone home. Spans are still created locally (no-op
        # exporter), so wrapping code keeps working.
        logfire.configure(
            service_name=os.getenv("LOGFIRE_SERVICE_NAME", "luna-backend"),
            service_version=service_version,
            environment=_resolve_environment(),
            send_to_logfire="if-token-present",
            scrubbing=logfire.ScrubbingOptions(
                extra_patterns=_PII_EXTRA_PATTERNS,
                callback=_scrub_callback,
            ),
        )
    except Exception as exc:
        logger.warning("logfire.configure failed: %s — observability disabled", exc)
        return False

    # Instrument SDKs we actually use. Each instrument_* call is wrapped so
    # one failure does not block the others.
    _safe_instrument(logfire, "instrument_pydantic_ai")
    _safe_instrument(logfire, "instrument_httpx")  # captures Anthropic / OpenRouter / Alibaba
    _safe_instrument(logfire, "instrument_redis")  # async client used by rate limiting

    _CONFIGURED = True
    has_token = bool(os.getenv("LOGFIRE_TOKEN"))
    logger.info(
        "logfire configured (token_present=%s, environment=%s)",
        has_token, _resolve_environment(),
    )
    return True


def instrument_fastapi_app(app: "FastAPI") -> None:
    """Instrument a FastAPI app — separated so it runs after the app exists."""
    try:
        import logfire
    except ImportError:
        return
    try:
        logfire.instrument_fastapi(app)
    except Exception as exc:
        logger.warning("logfire.instrument_fastapi failed: %s", exc)


def _safe_instrument(logfire_mod: Any, name: str) -> None:
    fn = getattr(logfire_mod, name, None)
    if fn is None:
        return
    try:
        fn()
    except Exception as exc:
        # instrument_redis raises if redis isn't installed; that's fine.
        logger.debug("logfire.%s skipped: %s", name, exc)


# ── Lazy-imported logfire helper for non-critical call sites ──────────────
# Call sites use ``with get_logfire().span(...)`` so a missing/broken SDK
# turns into a cheap no-op span via _NoopLogfire.


class _NoopSpan:
    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def set_attribute(self, *_: Any, **__: Any) -> None:
        return None

    def set_attributes(self, *_: Any, **__: Any) -> None:
        return None


class _NoopLogfire:
    def span(self, *_: Any, **__: Any) -> _NoopSpan:
        return _NoopSpan()

    def info(self, *_: Any, **__: Any) -> None:
        return None

    def warning(self, *_: Any, **__: Any) -> None:
        return None

    def error(self, *_: Any, **__: Any) -> None:
        return None


_NOOP = _NoopLogfire()


def get_logfire() -> Any:
    """Return the real ``logfire`` module if importable, else a no-op shim."""
    try:
        import logfire
        return logfire
    except ImportError:
        return _NOOP


__all__ = [
    "configure_logfire",
    "instrument_fastapi_app",
    "get_logfire",
]


# Sanity check: keep the regex list valid at import time so a typo fails
# loudly during dev rather than silently disabling scrubbing in prod.
for _p in _PII_EXTRA_PATTERNS:
    re.compile(_p)
