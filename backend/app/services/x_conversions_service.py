"""X (Twitter) Conversions API — report ad signups and first purchases.

Request: ``marketing/marketing_content/X/ads/request_to_luna_x_conversions.md``.
Storage: ``public.user_signup_attribution`` (migration 172, service-role only).

⚠ THE ONLY THING ABOUT A USER THAT GOES TO X IS ``twclid`` — X's own click id
from the ad link. The payload is exactly::

    {"conversions": [{"event_id", "conversion_time", "conversion_id",
                      "identifiers": [{"twclid": ...}]}]}

No email/phone/IP/UA (hashed or not), no value/currency/plan. Do not add
fields here without an operator decision.

Three entry points, all never-raise:

* :func:`record_signup` — ``POST /api/v1/attribution/signup``. Write-once
  source columns (all channels), then SignUp to X when there is a twclid.
* :func:`on_payment_paid` — from ``payment_service._mark_paid_and_grant``.
  Claims the FIRST purchase (renewals never), then Purchase to X.
* :func:`schedule_visit` — ``POST /api/v1/attribution/x-visit`` (anonymous,
  X8): the landing was visible ≥3 s. ``conversion_id`` = the twclid itself, so
  X counts one visit per click. No DB row, no retry — a lost visit is fine.
* :func:`retry_pending` — daily APScheduler job. Re-sends rows whose
  ``*_sent_at`` is still NULL with the ORIGINAL event time; X dedupes on
  ``conversion_id`` so a re-send is safe.

Sends are fire-and-forget background tasks with a short timeout: signup and
payment never wait on X. A failed send leaves ``*_sent_at`` NULL for the retry.
``X_CONVERSIONS_ENABLED=false`` (default) records everything and sends nothing.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from urllib.parse import quote

import httpx
from supabase import Client as SupabaseClient

from shared.config import get_settings
from shared.db.run import run_db

logger = logging.getLogger(__name__)

Kind = Literal["signup", "purchase", "visit"]

_TABLE = "user_signup_attribution"
FRESH_ACCOUNT = timedelta(hours=24)
_SEND_TIMEOUT_S = 10.0
_RETRY_BATCH = 500

# twclid is attacker-controlled (it arrives on a URL). Mirrors 172's CHECK.
_TWCLID_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_UTM_MAX = 120

# Strong refs to in-flight sends (asyncio keeps only weak refs to tasks).
_background_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Input hygiene
# ---------------------------------------------------------------------------


def clean_twclid(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _TWCLID_RE.match(value) else None


def clean_utm(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or not value.isascii() or not value.isprintable():
        return None
    return value[:_UTM_MAX]


# ---------------------------------------------------------------------------
# Config + OAuth 1.0a (HMAC-SHA1, user context)
# ---------------------------------------------------------------------------


def _config() -> Optional[dict[str, str]]:
    """Keys + pixel, or None when disabled / incomplete. Event ids are checked
    per kind in :func:`_send`, so a missing visit id never blocks signups."""
    s = get_settings()
    if not s.X_CONVERSIONS_ENABLED:
        return None
    cfg = {
        "consumer_key": s.X_ADS_CONSUMER_KEY,
        "consumer_secret": s.X_ADS_CONSUMER_SECRET,
        "token": s.X_ADS_ACCESS_TOKEN,
        "token_secret": s.X_ADS_ACCESS_SECRET,
        "pixel_id": s.X_PIXEL_ID,
    }
    missing = [k for k, v in cfg.items() if not (v or "").strip()]
    if missing:
        logger.warning("x_conversions: enabled but config missing %s — not sending", missing)
        return None
    cfg = {k: v.strip() for k, v in cfg.items()}  # type: ignore[union-attr]
    for kind, value in (("signup", s.X_EVENT_ID_SIGNUP), ("purchase", s.X_EVENT_ID_PURCHASE),
                        ("visit", s.X_EVENT_ID_VISIT)):
        cfg[f"event_{kind}"] = (value or "").strip()
    cfg["url"] = (
        f"https://ads-api.x.com/{s.X_ADS_API_VERSION.strip()}"
        f"/measurement/conversions/{quote(cfg['pixel_id'], safe='')}"
    )
    return cfg


def _pct(value: str) -> str:
    return quote(value, safe="~-._")


def _oauth1_header(method: str, url: str, cfg: dict[str, str]) -> str:
    """RFC 5849 Authorization header. A JSON body is NOT part of the signature
    base string (only form-encoded bodies are), and the URL has no query."""
    params = {
        "oauth_consumer_key": cfg["consumer_key"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": cfg["token"],
        "oauth_version": "1.0",
    }
    param_str = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(params.items()))
    base = "&".join([method.upper(), _pct(url), _pct(param_str)])
    key = f"{_pct(cfg['consumer_secret'])}&{_pct(cfg['token_secret'])}"
    sig = base64.b64encode(
        hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    ).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(params.items()))


def _iso_utc(value: Any) -> str:
    """ISO 8601 UTC with milliseconds and Z, from a datetime or PostgREST string."""
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def build_payload(event_id: str, conversion_time: str, conversion_id: str, twclid: str) -> dict:
    """THE WHOLE PAYLOAD. Nothing else goes in it — see the module docstring."""
    return {
        "conversions": [
            {
                "event_id": event_id,
                "conversion_time": conversion_time,
                "conversion_id": conversion_id,
                "identifiers": [{"twclid": twclid}],
            }
        ]
    }


async def _send(kind: Kind, *, conversion_id: str, conversion_time: Any, twclid: str) -> bool:
    """POST one conversion. True only on a 2xx. Never raises."""
    cfg = _config()
    if cfg is None:
        return False
    try:
        event_id = cfg[f"event_{kind}"]
        if not event_id:
            logger.warning("x_conversions: X_EVENT_ID_%s unset — %s not sent", kind.upper(), kind)
            return False
        payload = build_payload(event_id, _iso_utc(conversion_time), conversion_id, twclid)
        headers = {
            "Authorization": _oauth1_header("POST", cfg["url"], cfg),
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=_SEND_TIMEOUT_S) as client:
            resp = await client.post(cfg["url"], json=payload, headers=headers)
        if 200 <= resp.status_code < 300:
            logger.info(
                "x_conversions: %s delivered conversion_id=%s body=%s",
                kind, conversion_id, payload,
            )
            return True
        logger.warning(
            "x_conversions: %s rejected conversion_id=%s status=%s body=%s",
            kind, conversion_id, resp.status_code, resp.text[:500],
        )
    except Exception:  # noqa: BLE001
        logger.warning("x_conversions: %s send failed conversion_id=%s", kind, conversion_id, exc_info=True)
    return False


# ---------------------------------------------------------------------------
# DB helpers (sync — always called through run_db)
# ---------------------------------------------------------------------------


def _stamp_sent(supabase: SupabaseClient, user_id: str, kind: Literal["signup", "purchase"]) -> None:
    col = "x_signup_sent_at" if kind == "signup" else "x_purchase_sent_at"
    now = datetime.now(timezone.utc).isoformat()
    (
        supabase.table(_TABLE)
        .update({col: now, "updated_at": now})
        .eq("user_id", user_id)
        .is_(col, "null")
        .execute()
    )


def _rpc_row(result: Any) -> Optional[dict]:
    data = getattr(result, "data", None)
    if isinstance(data, list):
        data = data[0] if data else None
    # A plpgsql function returning a NULL composite comes back as all-null keys.
    if isinstance(data, dict) and data.get("user_id"):
        return data
    return None


async def _deliver(supabase: SupabaseClient, kind: Kind, *, user_id: str,
                   conversion_id: str, conversion_time: Any, twclid: str) -> bool:
    ok = await _send(kind, conversion_id=conversion_id,
                     conversion_time=conversion_time, twclid=twclid)
    if ok:
        try:
            await run_db(_stamp_sent, supabase, user_id, kind)
        except Exception:  # noqa: BLE001
            # Delivered but unstamped → the retry re-sends; X dedupes it.
            logger.warning("x_conversions: stamp failed user=%s kind=%s", user_id, kind, exc_info=True)
    return ok


def _schedule(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except Exception:  # noqa: BLE001
        coro.close()
        logger.warning("x_conversions: could not schedule send", exc_info=True)


# ---------------------------------------------------------------------------
# X3 — signup
# ---------------------------------------------------------------------------


def _attr(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _is_google(auth_user: Any) -> bool:
    app_meta = _attr(auth_user, "app_metadata") or {}
    providers = list(app_meta.get("providers") or []) + [app_meta.get("provider")]
    for ident in _attr(auth_user, "identities") or []:
        providers.append(_attr(ident, "provider"))
    return "google" in providers


def _record_signup_sync(
    supabase: SupabaseClient,
    auth_id: str,
    body_twclid: Any,
    body_utm_source: Any,
    body_utm_campaign: Any,
) -> Optional[dict]:
    """Validate the account, write the source once. Returns a send job or None."""
    auth_user = supabase.auth.admin.get_user_by_id(auth_id).user
    if auth_user is None:
        return None

    created_at = _as_dt(_attr(auth_user, "created_at"))
    if created_at is None or datetime.now(timezone.utc) - created_at > FRESH_ACCOUNT:
        return None
    if not (_attr(auth_user, "email_confirmed_at") or _is_google(auth_user)):
        return None

    meta = _attr(auth_user, "user_metadata") or {}
    twclid = clean_twclid(body_twclid) or clean_twclid(meta.get("x_twclid"))
    utm_source = clean_utm(body_utm_source) or clean_utm(meta.get("signup_utm_source"))
    utm_campaign = clean_utm(body_utm_campaign) or clean_utm(meta.get("signup_utm_campaign"))

    users = (
        supabase.table("users").select("user_id, created_at")
        .eq("auth_id", auth_id).limit(1).execute()
    ).data or []
    if not users:
        return None
    user_id = users[0]["user_id"]

    if not (twclid or utm_source or utm_campaign):
        # Nothing to record — but a row from an earlier call may still be pending.
        existing = (
            supabase.table(_TABLE).select("*").eq("user_id", user_id).limit(1).execute()
        ).data or []
        row = existing[0] if existing else None
    else:
        row = _rpc_row(supabase.rpc("record_signup_attribution", {
            "p_user_id": user_id,
            "p_twclid": twclid,
            "p_utm_source": utm_source,
            "p_utm_campaign": utm_campaign,
        }).execute())

    if not row or not row.get("x_twclid") or row.get("x_signup_sent_at"):
        return None
    return {
        "user_id": user_id,
        "twclid": row["x_twclid"],
        "conversion_time": users[0].get("created_at") or created_at,
    }


async def record_signup(
    supabase: SupabaseClient,
    auth_id: str,
    *,
    twclid: Any = None,
    utm_source: Any = None,
    utm_campaign: Any = None,
) -> None:
    """X3. Never raises; the caller always answers 2xx."""
    try:
        job = await run_db(_record_signup_sync, supabase, auth_id, twclid, utm_source, utm_campaign)
    except Exception:  # noqa: BLE001
        logger.warning("x_conversions: record_signup failed auth=%s", auth_id, exc_info=True)
        return
    if job and _config() is not None:
        _schedule(_deliver(
            supabase, "signup",
            user_id=job["user_id"],
            conversion_id=str(job["user_id"]),
            conversion_time=job["conversion_time"],
            twclid=job["twclid"],
        ))


# ---------------------------------------------------------------------------
# X6 — first purchase
# ---------------------------------------------------------------------------


async def on_payment_paid(supabase: SupabaseClient, row: dict) -> None:
    """X6. Called once a payment is paid + granted. Never raises.

    Renewals are skipped outright; otherwise the 172 claim makes it the FIRST
    purchase only, so webhook + /verify for the same payment send at most once
    more (and X dedupes on conversion_id anyway).
    """
    try:
        if str(row.get("initiated_by") or "user") == "renewal":
            return
        user_id, payment_id = row.get("user_id"), row.get("payment_id")
        if not user_id or not payment_id:
            return
        claimed = _rpc_row(await run_db(
            lambda: supabase.rpc("claim_x_first_purchase", {
                "p_user_id": user_id,
                "p_payment_id": str(payment_id),
                "p_paid_at": row.get("paid_at"),
            }).execute()
        ))
        if not claimed or _config() is None:
            return
        _schedule(_deliver(
            supabase, "purchase",
            user_id=user_id,
            conversion_id=str(payment_id),
            conversion_time=claimed.get("x_purchase_at"),
            twclid=claimed["x_twclid"],
        ))
    except Exception:  # noqa: BLE001
        logger.warning("x_conversions: on_payment_paid failed payment=%s", row.get("payment_id"), exc_info=True)


# ---------------------------------------------------------------------------
# X8 — engaged visit (anonymous)
# ---------------------------------------------------------------------------


def visit_enabled() -> bool:
    cfg = _config()
    return bool(cfg and cfg["event_visit"])


def schedule_visit(twclid: Any) -> None:
    """Fire-and-forget visit event. Never raises, never blocks."""
    try:
        clean = clean_twclid(twclid)
        if not clean or not visit_enabled():
            return
        _schedule(_send(
            "visit",
            conversion_id=clean,
            conversion_time=datetime.now(timezone.utc),
            twclid=clean,
        ))
    except Exception:  # noqa: BLE001
        logger.warning("x_conversions: schedule_visit failed", exc_info=True)


# ---------------------------------------------------------------------------
# X7 — retry
# ---------------------------------------------------------------------------


def _pending_sync(supabase: SupabaseClient) -> tuple[list[dict], list[dict]]:
    signups = (
        supabase.table(_TABLE)
        .select("user_id, x_twclid, created_at, users(created_at)")
        .not_.is_("x_twclid", "null")
        .is_("x_signup_sent_at", "null")
        .order("created_at")
        .limit(_RETRY_BATCH)
        .execute()
    ).data or []
    purchases = (
        supabase.table(_TABLE)
        .select("user_id, x_twclid, x_purchase_payment_id, x_purchase_at")
        .not_.is_("x_twclid", "null")
        .not_.is_("x_purchase_payment_id", "null")
        .is_("x_purchase_sent_at", "null")
        .order("x_purchase_at")
        .limit(_RETRY_BATCH)
        .execute()
    ).data or []
    return signups, purchases


async def retry_pending(supabase: SupabaseClient) -> dict:
    """Re-send everything still unstamped, with the original event time."""
    stats = {"enabled": _config() is not None, "signup_sent": 0, "signup_failed": 0,
             "purchase_sent": 0, "purchase_failed": 0}
    if not stats["enabled"]:
        return stats
    signups, purchases = await run_db(_pending_sync, supabase)
    for r in signups:
        signup_time = (r.get("users") or {}).get("created_at") or r.get("created_at")
        ok = await _deliver(supabase, "signup", user_id=r["user_id"],
                            conversion_id=str(r["user_id"]),
                            conversion_time=signup_time, twclid=r["x_twclid"])
        stats["signup_sent" if ok else "signup_failed"] += 1
    for r in purchases:
        ok = await _deliver(supabase, "purchase", user_id=r["user_id"],
                            conversion_id=str(r["x_purchase_payment_id"]),
                            conversion_time=r.get("x_purchase_at"), twclid=r["x_twclid"])
        stats["purchase_sent" if ok else "purchase_failed"] += 1
    return stats
