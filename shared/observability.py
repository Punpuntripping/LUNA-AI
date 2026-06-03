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
# Live wiring state, mutated by configure_logfire() and read by
# observability_status() for the /api/v1/_meta/observability endpoint.
_LOGFIRE_AVAILABLE = False
_BOOT_SPAN_EMITTED = False
_SERVICE_VERSION: str | None = None
_INSTRUMENTED: dict[str, bool] = {}


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


def _is_production_env(env_label: str) -> bool:
    """The list of labels we treat as 'production' for fail-loud purposes."""
    return env_label.lower() in {"production", "prod"}


def observability_status() -> dict[str, Any]:
    """Public snapshot of the current Logfire wiring.

    Exposed via ``/api/v1/_meta/observability`` so it can be verified from
    anywhere (curl, smoke test, browser DevTools) without trusting deployment
    config alone. Safe to expose — contains no secrets, only booleans and
    short labels.
    """
    return {
        "configured": _CONFIGURED,
        "sdk_installed": _LOGFIRE_AVAILABLE,
        "token_present": bool(os.getenv("LOGFIRE_TOKEN")),
        "environment": _resolve_environment(),
        "service_name": os.getenv("LOGFIRE_SERVICE_NAME", "luna-backend"),
        "service_version": _SERVICE_VERSION,
        "boot_span_emitted": _BOOT_SPAN_EMITTED,
        "instrumented": dict(_INSTRUMENTED),
        "railway_env": os.getenv("RAILWAY_ENVIRONMENT"),
        "railway_service": os.getenv("RAILWAY_SERVICE_NAME"),
        "railway_deployment_id": os.getenv("RAILWAY_DEPLOYMENT_ID"),
        "railway_git_sha": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
    }


def configure_logfire(service_version: str | None = None) -> bool:
    """Configure Logfire and instrument SDKs that are already imported.

    Returns ``True`` once configured (or when Logfire is genuinely active),
    ``False`` when the SDK is missing or configuration failed. ``main.py``
    does not gate on the return value — failures must never block startup.

    Side effects beyond `logfire.configure`:
    - Emits a one-shot ``luna.boot`` span so the deploy is always visible in
      Logfire even before the first request, with env/version/has_token. If
      the boot span never appears in the dashboard, observability is broken.
    - In production (``RAILWAY_ENVIRONMENT=production`` or
      ``LOGFIRE_ENVIRONMENT=production``) without a token, raises ``RuntimeError``
      unless ``LUNA_ALLOW_PROD_NO_LOGFIRE=1`` — silent telemetry loss in prod
      is the failure mode we are explicitly trying to prevent.
    """
    global _CONFIGURED, _LOGFIRE_AVAILABLE, _BOOT_SPAN_EMITTED, _SERVICE_VERSION
    if _CONFIGURED:
        return True

    _SERVICE_VERSION = service_version
    env_label = _resolve_environment()
    has_token = bool(os.getenv("LOGFIRE_TOKEN"))

    if _is_production_env(env_label) and not has_token:
        if os.getenv("LUNA_ALLOW_PROD_NO_LOGFIRE") != "1":
            raise RuntimeError(
                "LOGFIRE_TOKEN missing in production environment "
                f"(resolved env={env_label!r}). Telemetry would silently drop. "
                "Set LOGFIRE_TOKEN or LUNA_ALLOW_PROD_NO_LOGFIRE=1 to override."
            )
        logger.error(
            "Production boot WITHOUT LOGFIRE_TOKEN — observability disabled by override."
        )

    try:
        import logfire
        _LOGFIRE_AVAILABLE = True
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
            environment=env_label,
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
    # one failure does not block the others. Result recorded for the status
    # endpoint so we can spot a partial wiring (e.g. httpx OK but redis dead).
    _INSTRUMENTED["pydantic_ai"] = _safe_instrument(logfire, "instrument_pydantic_ai")
    _INSTRUMENTED["httpx"] = _safe_instrument(logfire, "instrument_httpx")
    _INSTRUMENTED["redis"] = _safe_instrument(logfire, "instrument_redis")

    _CONFIGURED = True
    logger.info(
        "logfire configured (token_present=%s, environment=%s)",
        has_token, env_label,
    )

    # Emit the boot sentinel. Without this, a misconfigured deploy looks
    # identical to an idle deploy in the Logfire dashboard.
    try:
        logfire.info(
            "luna.boot",
            environment=env_label,
            service_version=service_version,
            token_present=has_token,
            railway_env=os.getenv("RAILWAY_ENVIRONMENT"),
            railway_service=os.getenv("RAILWAY_SERVICE_NAME"),
            railway_deployment_id=os.getenv("RAILWAY_DEPLOYMENT_ID"),
            railway_git_sha=os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            instrumented=dict(_INSTRUMENTED),
        )
        _BOOT_SPAN_EMITTED = True
    except Exception as exc:
        logger.warning("luna.boot sentinel span failed: %s", exc)

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


def _safe_instrument(logfire_mod: Any, name: str) -> bool:
    fn = getattr(logfire_mod, name, None)
    if fn is None:
        return False
    try:
        fn()
        return True
    except Exception as exc:
        # Surfaced at WARNING — a silently-dead instrument_*() call (e.g. a
        # logfire/pydantic-ai version skew) otherwise drops whole span
        # families with no visible signal. instrument_redis raising because
        # redis isn't installed is the one benign case in dev.
        logger.warning("logfire.%s failed: %s — spans from this SDK disabled", name, exc)
        return False


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
    "observability_status",
]


# Sanity check: keep the regex list valid at import time so a typo fails
# loudly during dev rather than silently disabling scrubbing in prod.
for _p in _PII_EXTRA_PATTERNS:
    re.compile(_p)
