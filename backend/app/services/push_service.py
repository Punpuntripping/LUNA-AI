"""Web push «إجابتك جاهزة» — subscription store + sender.

Spec: .claude/plans/pwa_step1.md §1D. Table: migration 165 (push_subscriptions).

Two halves:

* Subscription CRUD (sync, run via ``run_db`` from ``api/push.py``):
  ``upsert_subscription`` / ``delete_subscription``. The backend uses the
  service-role client (RLS bypassed), so ownership is enforced HERE by always
  filtering on ``user_id``.

* Sender: ``notify_turn_ready`` (async, never raises) + ``schedule_turn_ready``
  (sync fire-and-forget wrapper used by message_service right after the
  ``done`` SSE event). A push is a side-effect of a finished turn; it must
  never block, delay, or fail that turn.

PRIVACY (PDPL, وضع السرية) — the payload is FIXED:
    {"title": "ريحان", "body": "إجابتك جاهزة",
     "url": "/chat/<conversation_id>", "tag": "<conversation_id>"}
The encrypted payload is still delivered through Apple / Google / Mozilla push
infrastructure, and it is rendered on a lock screen anyone can see. So no
question text, no answer text, and — by default — no conversation title
(titles are auto-derived from the user's first message, i.e. they ARE the
question). ``title`` is accepted for a future opt-in but only rendered when the
caller also passes ``include_title=True``, which it must only do after checking
the user has NOT enabled وضع السرية; even then it is truncated to 40 chars.
No caller passes it today.

Delivery bookkeeping per subscription:
    success          → last_success_at = now(), failure_count = 0
    404 / 410        → subscription is gone at the push service → delete row
    any other error  → failure_count += 1; delete the row once it reaches 5

No-op (with one debug log) when VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY are unset.

ABUSE BOUNDS (the endpoint URL is caller-supplied, and every send is a blocking
outbound HTTPS POST):
    * host allowlist  — ``is_allowed_push_endpoint``: only the real browser push
                        services. Enforced at subscribe AND again at send time,
                        so a row stored before the allowlist is never contacted
                        (no SSRF, no attacker-controlled tarpit endpoints).
    * per-user cap    — ``MAX_SUBS_PER_USER`` rows, trimmed on every subscribe
                        and LIMITed on every send.
    * own thread pool — sends run on ``_PUSH_EXECUTOR`` (4 threads), never the
                        default executor that every ``run_db`` call shares, so
                        slow push services cannot starve the DB layer.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlsplit

from supabase import Client as SupabaseClient

from shared.config import get_settings
from shared.db.run import run_db
from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()

TABLE = "push_subscriptions"

PUSH_TITLE_AR = "ريحان"
PUSH_BODY_TURN_READY_AR = "إجابتك جاهزة"
DEFAULT_VAPID_SUBJECT = "mailto:support@rayhanai.com"

MAX_FAILURES = 5            # delete the subscription at this many consecutive failures
_TITLE_MAX_CHARS = 40       # only relevant for the opt-in include_title path
_PUSH_TTL_S = 6 * 3600      # push service keeps an undelivered message this long
_PUSH_HTTP_TIMEOUT_S = 10   # per-endpoint POST timeout inside pywebpush/requests
MAX_SUBS_PER_USER = 5       # devices per account; least-recently-active trimmed on subscribe

# Web Push service hosts of the browsers we support: exact hosts, plus suffix
# rules for services that shard across numbered subdomains.
#   Chrome / Android / Samsung Internet / Opera / Brave → fcm.googleapis.com
#     (jmt17.google.com: alternate Google push front reported on some Chrome
#      desktop installs — Google-owned, so allowing it is not an SSRF surface)
#   Firefox                                               → updates.push.services.mozilla.com
#   Safari macOS / iOS 16.4+ home-screen web apps         → web.push.apple.com
#   Edge (Windows)                                        → wns2-*.notify.windows.com
_PUSH_HOSTS_EXACT = frozenset({
    "fcm.googleapis.com",
    "jmt17.google.com",
    "updates.push.services.mozilla.com",
})
_PUSH_HOST_SUFFIXES = (".push.apple.com", ".notify.windows.com")

# Dedicated, small pool for the blocking pywebpush POSTs. NOT the default
# executor: main.py sizes that to 40 "luna-db" threads shared by every run_db
# call, and slow push services must never be able to occupy them.
_PUSH_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="luna-push")

# Strong references to in-flight fire-and-forget sends (asyncio only keeps weak
# refs to tasks — an unreferenced task can be GC'd mid-flight).
_background_tasks: set[asyncio.Task] = set()


# ============================================
# Config
# ============================================

def vapid_public_key() -> Optional[str]:
    """The VAPID public key served to browsers, or None when push is unconfigured."""
    s = get_settings()
    if s.VAPID_PUBLIC_KEY and s.VAPID_PRIVATE_KEY:
        return s.VAPID_PUBLIC_KEY
    return None


def _vapid_config() -> Optional[tuple[str, str]]:
    s = get_settings()
    if not (s.VAPID_PUBLIC_KEY and s.VAPID_PRIVATE_KEY):
        return None
    return s.VAPID_PRIVATE_KEY, (s.VAPID_SUBJECT or DEFAULT_VAPID_SUBJECT)


def is_allowed_push_endpoint(endpoint: str) -> bool:
    """True iff ``endpoint`` is an https URL on a known browser push service.

    Also rejects userinfo (``https://fcm.googleapis.com@evil.example/``) and
    non-default ports. ``hostname`` is lower-cased by urlsplit.
    """
    try:
        parts = urlsplit((endpoint or "").strip())
        if parts.scheme != "https" or not parts.hostname:
            return False
        if parts.username is not None or parts.password is not None:
            return False
        if parts.port not in (None, 443):
            return False
    except ValueError:  # malformed netloc / non-numeric port
        return False
    host = parts.hostname.rstrip(".")
    return host in _PUSH_HOSTS_EXACT or host.endswith(_PUSH_HOST_SUFFIXES)


# ============================================
# Subscription store (sync — call via run_db)
# ============================================

def _parse_ts(v: Any) -> datetime:
    """PostgREST timestamptz string → aware datetime; None/unparseable sorts oldest."""
    if isinstance(v, str) and v:
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def upsert_subscription(
    supabase: SupabaseClient,
    *,
    user_id: str,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: Optional[str],
) -> None:
    """Insert or refresh a subscription keyed on its (globally unique) endpoint.

    ON CONFLICT (endpoint) the row is re-owned by ``user_id`` — a device that
    re-subscribes under another account must stop notifying the previous one.
    Keys are refreshed and the failure counter reset (a fresh subscribe proves
    the endpoint is live).

    Then enforces ``MAX_SUBS_PER_USER``: the user's rows are ranked by
    ``last_success_at or created_at`` (newest first) and everything past the
    cap is deleted. The endpoint just subscribed always ranks first — it is the
    one device we KNOW is live right now.
    """
    supabase.table(TABLE).upsert(
        {
            "user_id": user_id,
            "endpoint": endpoint,
            "p256dh": p256dh,
            "auth": auth,
            "user_agent": (user_agent or None) and user_agent[:512],
            "failure_count": 0,
        },
        on_conflict="endpoint",
    ).execute()
    _trim_user_subscriptions(supabase, user_id, keep_endpoint=endpoint)


def _trim_user_subscriptions(
    supabase: SupabaseClient, user_id: str, *, keep_endpoint: Optional[str] = None,
) -> int:
    """Delete the user's rows beyond ``MAX_SUBS_PER_USER``. Returns rows deleted."""
    res = (
        supabase.table(TABLE)
        .select("id, endpoint, created_at, last_success_at")
        .eq("user_id", user_id)
        .execute()
    )
    rows = list(res.data or [])
    if len(rows) <= MAX_SUBS_PER_USER:
        return 0
    rows.sort(
        key=lambda r: (
            r.get("endpoint") == keep_endpoint,
            _parse_ts(r.get("last_success_at") or r.get("created_at")),
        ),
        reverse=True,
    )
    excess = [r["id"] for r in rows[MAX_SUBS_PER_USER:]]
    supabase.table(TABLE).delete().eq("user_id", user_id).in_("id", excess).execute()
    logger.info("push: trimmed %d subscription(s) over the per-user cap", len(excess))
    return len(excess)


def delete_subscription(supabase: SupabaseClient, *, user_id: str, endpoint: str) -> int:
    """Delete the caller's OWN subscription for ``endpoint``. Returns rows deleted.

    The ``user_id`` filter is the ownership check (service role bypasses RLS):
    another user's endpoint is simply not matched.
    """
    res = (
        supabase.table(TABLE)
        .delete()
        .eq("user_id", user_id)
        .eq("endpoint", endpoint)
        .execute()
    )
    return len(res.data or [])


def _list_user_subscriptions(supabase: SupabaseClient, user_id: str) -> list[dict]:
    # Most recently active first, hard LIMIT: even if concurrent subscribes race
    # past the trim in upsert_subscription, one turn never fans out to more than
    # MAX_SUBS_PER_USER endpoints.
    res = (
        supabase.table(TABLE)
        .select("id, endpoint, p256dh, auth, failure_count")
        .eq("user_id", user_id)
        .order("last_success_at", desc=True, nullsfirst=False)
        .order("created_at", desc=True)
        .limit(MAX_SUBS_PER_USER)
        .execute()
    )
    return list(res.data or [])


def _delete_by_id(supabase: SupabaseClient, sub_id: str) -> None:
    supabase.table(TABLE).delete().eq("id", sub_id).execute()


def _update_by_id(supabase: SupabaseClient, sub_id: str, data: dict) -> None:
    supabase.table(TABLE).update(data).eq("id", sub_id).execute()


# ============================================
# Payload
# ============================================

def build_turn_ready_payload(
    conversation_id: str,
    *,
    title: Optional[str] = None,
    include_title: bool = False,
) -> dict[str, Any]:
    """The notification payload. Content-free by construction — see module doc."""
    payload: dict[str, Any] = {
        "title": PUSH_TITLE_AR,
        "body": PUSH_BODY_TURN_READY_AR,
        "url": f"/chat/{conversation_id}",
        "tag": str(conversation_id),
    }
    if include_title and title:
        t = title.strip()
        if len(t) > _TITLE_MAX_CHARS:
            t = t[:_TITLE_MAX_CHARS].rstrip() + "…"
        if t:
            payload["body"] = f"{PUSH_BODY_TURN_READY_AR} — {t}"
    return payload


# ============================================
# Sender
# ============================================

def _status_of(exc: Exception) -> Optional[int]:
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return code if isinstance(code, int) else None


def _endpoint_host(endpoint: str) -> str:
    # The full endpoint is a bearer capability URL — log the host only.
    try:
        return urlsplit(endpoint).netloc or "?"
    except Exception:  # noqa: BLE001
        return "?"


def _send_one(sub: dict, data: str, private_key: str, subject: str) -> None:
    """Blocking pywebpush call. Raises on failure (WebPushException et al.)."""
    from pywebpush import webpush  # lazy: keeps app import free of the dep

    webpush(
        subscription_info={
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        },
        data=data,
        vapid_private_key=private_key,
        # FRESH dict per call: pywebpush writes `aud` (the endpoint's origin)
        # into vapid_claims, so a shared dict would sign the 2nd endpoint with
        # the 1st one's audience and it would be rejected.
        vapid_claims={"sub": subject},
        ttl=_PUSH_TTL_S,
        headers={"Urgency": "high"},
        timeout=_PUSH_HTTP_TIMEOUT_S,
    )


async def _deliver(
    supabase: SupabaseClient,
    sub: dict,
    data: str,
    private_key: str,
    subject: str,
    conversation_id: str,
) -> None:
    host = _endpoint_host(sub.get("endpoint", ""))
    try:
        # Own pool, NOT asyncio.to_thread (default executor = the luna-db pool).
        # copy_context keeps the Logfire trace context, as to_thread would.
        ctx = contextvars.copy_context()
        await asyncio.get_running_loop().run_in_executor(
            _PUSH_EXECUTOR,
            functools.partial(ctx.run, _send_one, sub, data, private_key, subject),
        )
    except Exception as exc:  # noqa: BLE001 — every failure is bookkeeping, never a raise
        status = _status_of(exc)
        if status in (404, 410):
            await run_db(_delete_by_id, supabase, sub["id"])
            _logfire.info(
                "push.pruned", conversation_id=conversation_id,
                status=status, push_host=host,
            )
            return
        failures = int(sub.get("failure_count") or 0) + 1
        if failures >= MAX_FAILURES:
            await run_db(_delete_by_id, supabase, sub["id"])
            _logfire.warning(
                "push.pruned", conversation_id=conversation_id, status=status,
                push_host=host, failure_count=failures, reason="max_failures",
            )
        else:
            await run_db(_update_by_id, supabase, sub["id"], {"failure_count": failures})
            _logfire.warning(
                "push.failed", conversation_id=conversation_id, status=status,
                push_host=host, failure_count=failures, error_type=type(exc).__name__,
            )
        return

    await run_db(
        _update_by_id, supabase, sub["id"],
        {"last_success_at": datetime.now(timezone.utc).isoformat(), "failure_count": 0},
    )
    _logfire.info("push.sent", conversation_id=conversation_id, push_host=host)


async def notify_turn_ready(
    user_id: str,
    conversation_id: str,
    *,
    title: Optional[str] = None,
    include_title: bool = False,
    supabase: Optional[SupabaseClient] = None,
) -> None:
    """Send «إجابتك جاهزة» to every subscribed device of ``user_id``. Never raises.

    ``title`` is ignored unless ``include_title=True`` (privacy — module doc).
    ``supabase`` defaults to the shared service-role client.
    """
    try:
        cfg = _vapid_config()
        if cfg is None:
            logger.debug("push: VAPID not configured — skipping notify_turn_ready")
            return
        private_key, subject = cfg

        if supabase is None:
            from shared.db.client import get_supabase_client
            supabase = get_supabase_client()

        rows = await run_db(_list_user_subscriptions, supabase, user_id)
        # Defence in depth: rows stored before the subscribe-time allowlist (or
        # written by any other path) are never contacted.
        subs = []
        for s in rows:
            if is_allowed_push_endpoint(s.get("endpoint", "")):
                subs.append(s)
            else:
                _logfire.warning(
                    "push.skipped_disallowed_host", conversation_id=conversation_id,
                    push_host=_endpoint_host(s.get("endpoint", "")),
                )
        if not subs:
            return

        data = json.dumps(
            build_turn_ready_payload(conversation_id, title=title, include_title=include_title),
            ensure_ascii=False,
        )
        results = await asyncio.gather(
            *(_deliver(supabase, s, data, private_key, subject, conversation_id) for s in subs),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, BaseException):
                logger.warning("push: delivery bookkeeping failed: %r", r)
    except Exception:  # noqa: BLE001
        logger.warning("push: notify_turn_ready failed", exc_info=True)


def schedule_turn_ready(
    user_id: str,
    conversation_id: str,
    *,
    supabase: Optional[SupabaseClient] = None,
) -> None:
    """Fire-and-forget ``notify_turn_ready``. Sync, never raises, never blocks."""
    try:
        if _vapid_config() is None:
            return
        task = asyncio.get_running_loop().create_task(
            notify_turn_ready(user_id, conversation_id, supabase=supabase)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except Exception:  # noqa: BLE001
        logger.warning("push: failed to schedule notify_turn_ready", exc_info=True)
