"""
Ambient per-request caller context (client IP + User-Agent + native-app
X-App-* headers).

``audit_logs`` has carried ``ip_address`` and ``user_agent`` columns since the
original schema, but nothing ever populated them: :func:`write_audit_log` took
no such arguments, so every row landed with both NULL. That left abuse
investigations with no way to tie an audited action to a network origin or a
client — you could see *that* an account created a conversation, never from
where or from how many distinct machines.

Threading a ``Request`` through the 13 service-layer call sites would touch a
lot of unrelated code, so the context travels out-of-band instead: the
request-id middleware publishes it on a ContextVar, and the audit writer reads
it. Call sites stay untouched, and anything running outside a request (scripts,
APScheduler jobs, agent workers) simply sees ``None`` and writes NULL exactly
as before.

The IP is resolved through ``rate_limit.resolve_client_ip`` — the backend's ONE
trust boundary for client IP — so the ``TRUST_CF_HEADERS`` / ``CF-Connecting-IP``
decision keeps living in exactly one place.
"""
from __future__ import annotations

import contextvars
from typing import Optional

from fastapi import Request

# Truncated on write: `user_agent` is TEXT, but a hostile client can send a
# multi-kilobyte header and audit rows are not worth that storage.
_MAX_USER_AGENT = 400

_CLIENT_IP: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "audit_client_ip", default=None
)
_USER_AGENT: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "audit_user_agent", default=None
)

# Native app identity (mobile/04 §S3, B7). The Expo app sends these four on
# every request; the web sends none of them, so for the web this stays empty.
# Header → attribute key. Values are client-supplied and therefore spoofable:
# they are observability labels, never an authorization input.
APP_HEADER_ATTRIBUTES: dict[str, str] = {
    "x-app-platform": "app.platform",
    "x-app-version": "app.version",
    "x-app-build": "app.build",
    "x-app-update-id": "app.update_id",
}
_MAX_APP_HEADER = 64

_APP_CLIENT: contextvars.ContextVar[Optional[dict[str, str]]] = contextvars.ContextVar(
    "app_client", default=None
)


def capture_request_context(request: Request) -> None:
    """Publish the caller's IP + User-Agent for the rest of this request.

    Best-effort: a failure here must never break the request, since the only
    thing downstream of it is an audit column.
    """
    try:
        # Imported lazily — rate_limit pulls in redis/settings, and this module
        # is imported by audit_service, which tests construct standalone.
        from backend.app.middleware.rate_limit import resolve_client_ip

        _CLIENT_IP.set(resolve_client_ip(request))
    except Exception:  # noqa: BLE001
        _CLIENT_IP.set(None)

    try:
        ua = request.headers.get("user-agent")
        _USER_AGENT.set(ua[:_MAX_USER_AGENT] if ua else None)
    except Exception:  # noqa: BLE001
        _USER_AGENT.set(None)

    try:
        attrs = _app_client_attributes(request)
        _APP_CLIENT.set(attrs or None)
        if attrs:
            _set_span_attributes(attrs)
    except Exception:  # noqa: BLE001
        _APP_CLIENT.set(None)


def _app_client_attributes(request: Request) -> dict[str, str]:
    """X-App-* headers present on this request → {attribute key: value}."""
    attrs: dict[str, str] = {}
    for header, key in APP_HEADER_ATTRIBUTES.items():
        value = request.headers.get(header)
        if value:
            value = value.strip()[:_MAX_APP_HEADER]
            if value:
                attrs[key] = value
    return attrs


def _set_span_attributes(attrs: dict[str, str]) -> None:
    """Stamp the attributes on the current (Logfire/OTEL request) span.

    The request-id middleware runs inside the Logfire FastAPI instrumentation,
    so the current span here is the request's server span. No-op when OTEL is
    absent or the span is not recording.
    """
    try:
        from opentelemetry import trace
    except Exception:  # noqa: BLE001
        return
    span = trace.get_current_span()
    if span is not None and span.is_recording():
        span.set_attributes(attrs)


def get_client_ip() -> Optional[str]:
    """Caller IP for the request in flight, or None outside a request."""
    return _CLIENT_IP.get()


def get_user_agent() -> Optional[str]:
    """Caller User-Agent for the request in flight, or None outside a request."""
    return _USER_AGENT.get()


def get_app_client() -> Optional[dict[str, str]]:
    """Native-app attributes (``app.platform`` …) for the request in flight.

    None for the web, and outside a request.
    """
    attrs = _APP_CLIENT.get()
    return dict(attrs) if attrs else None
