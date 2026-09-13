"""
Ambient per-request caller context (client IP + User-Agent).

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


def get_client_ip() -> Optional[str]:
    """Caller IP for the request in flight, or None outside a request."""
    return _CLIENT_IP.get()


def get_user_agent() -> Optional[str]:
    """Caller User-Agent for the request in flight, or None outside a request."""
    return _USER_AGENT.get()
