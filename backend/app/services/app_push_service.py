"""App push «إجابتك جاهزة» — Expo token store + sender (native iOS/Android app).

Spec: .claude/plans/mobile/06_phase3_push.md §3 B. Table: migration 185
(app_push_tokens). The web half (VAPID, migration 165) lives in push_service;
this module mirrors its shape and reuses its constants and fixed texts.

Two halves:

* Token CRUD (sync, run via ``run_db`` from ``api/push.py``):
  ``upsert_app_token`` / ``delete_app_token``. The backend uses the
  service-role client (RLS bypassed), so ownership is enforced HERE by always
  filtering on ``user_id``. Registration works whatever EXPO_PUSH_ENABLED says:
  tokens are collected while the flag is off, and only SENDING is gated.

* Sender: ``notify_turn_ready_app`` (async, never raises). Scheduled by
  ``push_service.schedule_turn_ready`` as its own fire-and-forget task, iff
  EXPO_PUSH_ENABLED.

PRIVACY (PDPL, وضع السرية) — the message is FIXED (``build_expo_message``):
    {"to": <token>, "title": "ريحان", "body": "إجابتك جاهزة",
     "data": {"conversation_id": "<uuid>"}, "channelId": "turn-ready",
     "priority": "high", "sound": "default", "ttl": 21600}
It travels through Expo and then FCM / APNs, and it is rendered on a lock
screen anyone can see: no question, no answer, no conversation title, ever.

The token is a device identifier AND a send capability (without Expo
"Enhanced push security", anyone holding it can push to that phone in our
name). It is NEVER logged: not in Logfire events, not in log lines, and not
via Expo's ticket ``message`` / ``details`` (both echo the token back), so only
the sanitised ``details.error`` code is ever recorded.

Transport: ONE ``httpx.AsyncClient`` POST of the whole batch (≤ 5 tokens, far
under Expo's 100-message limit) to the FIXED host exp.host, so there is no
SSRF surface and nothing is caller-supplied but the token string. Async I/O,
so no thread pool is involved (unlike pywebpush on the web half).

Delivery bookkeeping. Expo answers ``{"data": [ticket, ...]}``, one ticket per
message in order: ``{"status": "ok", "id": ...}`` or
``{"status": "error", "message": ..., "details": {"error": "<code>"}}``.
    ticket ok                      → last_success_at = now(), failure_count = 0
    ticket DeviceNotRegistered     → the app was uninstalled / token revoked → delete row
    ticket InvalidCredentials /
           DeveloperError /
           MessageTooBig           → OUR config or request is at fault, not the
                                     device → log only, row untouched
    any other ticket error         → failure_count += 1; delete the row at 5
    network error / 5xx /
    malformed 2xx body             → failure_count += 1 for every token in the
                                     batch (same threshold)
    4xx, or a top-level "errors"   → Expo refused OUR request (bad access token,
                                     rate limit, validation) → log only
The "log only" rows exist because the app re-registers its token only when the
token or the signed-in user changes (cached in SecureStore): a row pruned for a
server-side misconfiguration would stay gone after the fix.

Push receipts (``/getReceipts``, late FCM/APNs errors) are out of scope for v1;
ticket errors plus the failure counter cover it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import httpx
from supabase import Client as SupabaseClient

from backend.app.services.push_service import (
    _PUSH_TTL_S,
    MAX_FAILURES,
    MAX_SUBS_PER_USER,
    PUSH_BODY_TURN_READY_AR,
    PUSH_TITLE_AR,
    _parse_ts,
)
from shared.config import get_settings
from shared.db.run import run_db
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()

TABLE = "app_push_tokens"
CHANNEL = "expo"                        # Logfire `channel` on push.* events (web events carry none)

EXPO_PUSH_SEND_URL = "https://exp.host/--/api/v2/push/send"
EXPO_BATCH_LIMIT = 100                  # Expo's max messages per send request
EXPO_ANDROID_CHANNEL_ID = "turn-ready"  # created by the app at startup (mobile P2)
_EXPO_HTTP_TIMEOUT_S = 10.0

APP_VERSION_MAX_CHARS = 32
Platform = Literal["android", "ios"]

# ExponentPushToken[...] (what getExpoPushTokenAsync returns) or the newer
# ExpoPushToken[...]. The inner charset is deliberately loose — Expo's own SDK
# only checks the prefix and the closing bracket — but no whitespace, no "]"
# and a hard length bound.
EXPO_PUSH_TOKEN_RE = re.compile(r"^Expo(nent)?PushToken\[[^\]\s]{1,200}\]$")

# Ticket error codes that blame our configuration / request, never the device.
_CONFIG_TICKET_ERRORS = frozenset({"InvalidCredentials", "DeveloperError", "MessageTooBig"})

# Only identifier-shaped strings from Expo's responses are ever logged.
_SAFE_CODE_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_SAFE_TICKET_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")

assert MAX_SUBS_PER_USER <= EXPO_BATCH_LIMIT  # one POST always carries every token


# ============================================
# Config
# ============================================

def expo_push_enabled() -> bool:
    """The send gate. Registration is never gated (module doc)."""
    return bool(getattr(get_settings(), "EXPO_PUSH_ENABLED", False))


def _expo_access_token() -> Optional[str]:
    token = getattr(get_settings(), "EXPO_ACCESS_TOKEN", None)
    if not isinstance(token, str):
        return None
    return token.strip() or None


def is_valid_expo_push_token(token: str) -> bool:
    return isinstance(token, str) and EXPO_PUSH_TOKEN_RE.fullmatch(token) is not None


# ============================================
# Token store (sync — call via run_db)
# ============================================

def upsert_app_token(
    supabase: SupabaseClient,
    *,
    user_id: str,
    token: str,
    platform: Platform,
    app_version: Optional[str],
) -> None:
    """Insert or refresh a device token keyed on the (globally unique) token.

    ON CONFLICT (token) the row is re-owned by ``user_id`` — a phone that signs
    in under another account must stop notifying the previous one. Platform and
    app version are refreshed and the failure counter reset (a fresh
    registration proves the app is installed).

    Then enforces ``MAX_SUBS_PER_USER``, keeping the token just registered.
    """
    version = (app_version or "").strip()[:APP_VERSION_MAX_CHARS] or None
    supabase.table(TABLE).upsert(
        {
            "user_id": user_id,
            "token": token,
            "platform": platform,
            "app_version": version,
            "failure_count": 0,
        },
        on_conflict="token",
    ).execute()
    _trim_user_app_tokens(supabase, user_id, keep_token=token)


def _trim_user_app_tokens(
    supabase: SupabaseClient, user_id: str, *, keep_token: Optional[str] = None,
) -> int:
    """Delete the user's tokens beyond ``MAX_SUBS_PER_USER``. Returns rows deleted.

    Ranked by ``last_success_at or created_at`` (newest first); ``keep_token``
    always ranks first — it is the one device we KNOW is live right now.
    """
    res = (
        supabase.table(TABLE)
        .select("id, token, created_at, last_success_at")
        .eq("user_id", user_id)
        .execute()
    )
    rows = list(res.data or [])
    if len(rows) <= MAX_SUBS_PER_USER:
        return 0
    rows.sort(
        key=lambda r: (
            r.get("token") == keep_token,
            _parse_ts(r.get("last_success_at") or r.get("created_at")),
        ),
        reverse=True,
    )
    excess = [r["id"] for r in rows[MAX_SUBS_PER_USER:]]
    supabase.table(TABLE).delete().eq("user_id", user_id).in_("id", excess).execute()
    logger.info("push(expo): trimmed %d token(s) over the per-user cap", len(excess))
    return len(excess)


def delete_app_token(supabase: SupabaseClient, *, user_id: str, token: str) -> int:
    """Delete the caller's OWN row for ``token``. Returns rows deleted.

    The ``user_id`` filter is the ownership check (service role bypasses RLS):
    another user's token is simply not matched.
    """
    res = (
        supabase.table(TABLE)
        .delete()
        .eq("user_id", user_id)
        .eq("token", token)
        .execute()
    )
    return len(res.data or [])


def _list_user_app_tokens(supabase: SupabaseClient, user_id: str) -> list[dict]:
    # Most recently successful first, hard LIMIT: even if concurrent
    # registrations race past the trim, one turn never fans out to more than
    # MAX_SUBS_PER_USER devices.
    res = (
        supabase.table(TABLE)
        .select("id, token, platform, failure_count")
        .eq("user_id", user_id)
        .order("last_success_at", desc=True, nullsfirst=False)
        .order("created_at", desc=True)
        .limit(MAX_SUBS_PER_USER)
        .execute()
    )
    return list(res.data or [])


def _delete_by_id(supabase: SupabaseClient, row_id: str) -> None:
    supabase.table(TABLE).delete().eq("id", row_id).execute()


def _update_by_id(supabase: SupabaseClient, row_id: str, data: dict) -> None:
    supabase.table(TABLE).update(data).eq("id", row_id).execute()


# ============================================
# Message
# ============================================

def build_expo_message(token: str, conversation_id: str) -> dict[str, Any]:
    """One Expo push message. Content-free by construction — see module doc."""
    return {
        "to": token,
        "title": PUSH_TITLE_AR,
        "body": PUSH_BODY_TURN_READY_AR,
        "data": {"conversation_id": str(conversation_id)},
        "channelId": EXPO_ANDROID_CHANNEL_ID,
        "priority": "high",
        "sound": "default",
        "ttl": _PUSH_TTL_S,
    }


# ============================================
# Sender
# ============================================

@dataclass(frozen=True)
class _BatchResult:
    """Outcome of the one POST to Expo.

    kind:
      "tickets"  — 2xx with one ticket per message, in order (``tickets``)
      "transient"— network error / 5xx / malformed 2xx: counts against every token
      "rejected" — 4xx or a top-level ``errors`` array: Expo refused OUR request
    """
    kind: Literal["tickets", "transient", "rejected"]
    tickets: Optional[list] = None
    status: Optional[int] = None
    error: Optional[str] = None   # sanitised Expo code or exception type — never a message


def _safe(value: Any, pattern: re.Pattern[str] = _SAFE_CODE_RE) -> Optional[str]:
    """``value`` if it is an identifier-shaped string, else None (never echo tokens)."""
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _first_error_code(body: Any) -> Optional[str]:
    errors = body.get("errors") if isinstance(body, dict) else None
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return _safe(errors[0].get("code")) or "unknown"
    return None


def _new_http_client() -> httpx.AsyncClient:
    """A short-lived client per send. Tests replace this with a MockTransport one."""
    return httpx.AsyncClient(timeout=_EXPO_HTTP_TIMEOUT_S, follow_redirects=False)


async def _post_batch(messages: list[dict]) -> _BatchResult:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    access_token = _expo_access_token()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    body = json.dumps(messages, ensure_ascii=False).encode("utf-8")

    try:
        async with _new_http_client() as client:
            resp = await client.post(EXPO_PUSH_SEND_URL, content=body, headers=headers)
    except httpx.HTTPError as exc:  # connect / timeout / protocol
        return _BatchResult("transient", error=type(exc).__name__)

    status = resp.status_code
    try:
        parsed = resp.json()
    except ValueError:
        parsed = None
    error_code = _first_error_code(parsed)

    if 400 <= status < 500:
        return _BatchResult("rejected", status=status, error=error_code)
    if not 200 <= status < 300:
        return _BatchResult("transient", status=status, error=error_code)
    if error_code is not None:  # 200 + {"errors": [...]} — Expo's SDK treats it as a request error
        return _BatchResult("rejected", status=status, error=error_code)
    tickets = parsed.get("data") if isinstance(parsed, dict) else None
    if not isinstance(tickets, list) or len(tickets) != len(messages):
        return _BatchResult("transient", status=status, error="malformed_response")
    return _BatchResult("tickets", tickets=tickets, status=status)


def _ticket_error_code(ticket: Any) -> str:
    details = ticket.get("details") if isinstance(ticket, dict) else None
    code = details.get("error") if isinstance(details, dict) else None
    return _safe(code) or "unknown"


async def _record_failure(
    supabase: SupabaseClient,
    row: dict,
    conversation_id: str,
    *,
    error: Optional[str],
    status: Optional[int] = None,
) -> None:
    failures = int(row.get("failure_count") or 0) + 1
    fields = dict(
        channel=CHANNEL, conversation_id=conversation_id, platform=row.get("platform"),
        error=error, status=status, failure_count=failures,
    )
    if failures >= MAX_FAILURES:
        await run_db(_delete_by_id, supabase, row["id"])
        _logfire.warning("push.pruned", reason="max_failures", **fields)
    else:
        await run_db(_update_by_id, supabase, row["id"], {"failure_count": failures})
        _logfire.warning("push.failed", **fields)


async def _apply_ticket(
    supabase: SupabaseClient, row: dict, ticket: Any, conversation_id: str,
) -> None:
    platform = row.get("platform")
    if isinstance(ticket, dict) and ticket.get("status") == "ok":
        await run_db(
            _update_by_id, supabase, row["id"],
            {"last_success_at": datetime.now(timezone.utc).isoformat(), "failure_count": 0},
        )
        _logfire.info(
            "push.sent", channel=CHANNEL, conversation_id=conversation_id, platform=platform,
            ticket_id=_safe(ticket.get("id"), _SAFE_TICKET_ID_RE),
        )
        return

    code = _ticket_error_code(ticket)
    if code == "DeviceNotRegistered":
        await run_db(_delete_by_id, supabase, row["id"])
        _logfire.info(
            "push.pruned", channel=CHANNEL, conversation_id=conversation_id,
            platform=platform, error=code,
        )
        return
    if code in _CONFIG_TICKET_ERRORS:
        _logfire.warning(
            "push.failed", channel=CHANNEL, conversation_id=conversation_id,
            platform=platform, error=code, reason="config_error",
        )
        return
    await _record_failure(supabase, row, conversation_id, error=code)


async def notify_turn_ready_app(
    user_id: str,
    conversation_id: str,
    *,
    supabase: Optional[SupabaseClient] = None,
) -> None:
    """Send «إجابتك جاهزة» to every registered app install of ``user_id``. Never raises.

    No-op unless EXPO_PUSH_ENABLED. ``supabase`` defaults to the shared
    service-role client.
    """
    try:
        if not expo_push_enabled():
            logger.debug("push(expo): EXPO_PUSH_ENABLED is off — skipping")
            return

        if supabase is None:
            from shared.db.client import get_supabase_client
            supabase = get_supabase_client()

        conversation_id = str(conversation_id)
        rows = await run_db(_list_user_app_tokens, supabase, user_id)
        # Defence in depth: a row written by any path other than the validated
        # route (a manual SQL insert, a future writer) is
        # never sent — one malformed `to` could make Expo reject the batch.
        targets = []
        for r in rows:
            if is_valid_expo_push_token(r.get("token") or ""):
                targets.append(r)
            else:
                _logfire.warning(
                    "push.skipped_invalid_token", channel=CHANNEL,
                    conversation_id=conversation_id, platform=r.get("platform"),
                )
        targets = targets[:EXPO_BATCH_LIMIT]
        if not targets:
            return

        result = await _post_batch(
            [build_expo_message(r["token"], conversation_id) for r in targets]
        )

        if result.kind == "rejected":
            _logfire.warning(
                "push.failed", channel=CHANNEL, conversation_id=conversation_id,
                status=result.status, error=result.error, tokens=len(targets),
                reason="request_rejected",
            )
            return
        if result.kind == "transient":
            jobs = [
                _record_failure(supabase, r, conversation_id,
                                error=result.error, status=result.status)
                for r in targets
            ]
        else:
            jobs = [
                _apply_ticket(supabase, r, t, conversation_id)
                for r, t in zip(targets, result.tickets or [])
            ]
        for res in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(res, BaseException):
                logger.warning("push(expo): delivery bookkeeping failed: %s", type(res).__name__)
    except Exception:  # noqa: BLE001
        logger.warning("push(expo): notify_turn_ready_app failed", exc_info=True)
