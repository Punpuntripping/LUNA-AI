"""Marketing-email links — unsubscribe (RFC 8058) and opt-in, no login.

Phase 0 T6 of ``marketing/plans/email/00_email_program.md``. Mounted under
``/api/v1`` (the router declares the prefix itself, like ``public_ask.py``) and
anonymous by OMITTING ``Depends(get_current_user)`` — the link in the email is
the credential. Four endpoints:

    GET  /api/v1/public/email/unsubscribe?u=&t=  — the footer link: a page that
                                                   ASKS, with one button.
    POST /api/v1/public/email/unsubscribe?u=&t=  — does it. Also the RFC 8058
                                                   one-click target Gmail/Apple
                                                   POST ``List-Unsubscribe=One-Click``
                                                   to (the body is ignored).
    GET  /api/v1/public/email/subscribe?u=&t=    — the re-permission link: asks.
    POST /api/v1/public/email/subscribe?u=&t=    — opts in, stamped
                                                   ``repermission_email``.

⚠ GET NEVER WRITES. Corporate mail gateways and link scanners prefetch every
URL in a message; a GET that unsubscribed would silently opt out whole firms
(and a GET that subscribed would manufacture consent). Only POST changes a row.

⚠ THE TOKEN IS PURPOSE-BOUND. ``t`` is an HMAC over ``<purpose>:<user_id>``, so
an unsubscribe link cannot be replayed as an opt-in. The re-subscribe button on
the "stopped" page carries a subscribe token minted here, after the unsubscribe
token verified.

⚠ TOKENS NEVER EXPIRE (plan D8) — a year-old email's link must still work.

⚠ A BAD TOKEN LOOKS LIKE A GOOD ONE ON POST. The one-click target always answers
200 and the same page whether or not ``u`` exists or ``t`` verifies, so the
endpoint is not an oracle for which user ids are real. (GET says "invalid
link" on a bad token — that reveals nothing, the HMAC does not depend on the
row existing.)

The marketing repo mints identical tokens in ``marketing/scripts/email_unsub.py``;
the two must stay byte-for-byte in step. Secret: ``EMAIL_LINK_SECRET``
(fail-closed — unset, every token is invalid).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import logging
import re
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from supabase import Client as SupabaseClient

from backend.app.deps import get_supabase
from shared.config import get_settings
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["email-prefs"])

Purpose = Literal["unsubscribe", "subscribe"]

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

# 32 base64url chars = 192 bits of the HMAC — unguessable, short enough to keep
# the footer link from wrapping in narrow mail clients.
TOKEN_CHARS = 32


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def make_token(secret: str, purpose: Purpose, user_id: str) -> str:
    """HMAC-SHA256 over ``<purpose>:<user_id>``, base64url, truncated."""
    mac = hmac.new(
        secret.encode(), f"{purpose}:{user_id.lower()}".encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:TOKEN_CHARS]


def verify_token(purpose: Purpose, user_id: str, token: str) -> bool:
    secret = get_settings().EMAIL_LINK_SECRET
    if not secret or not _UUID_RE.match(user_id.lower()):
        return False
    return hmac.compare_digest(make_token(secret, purpose, user_id), token)


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def _set_opt_in(
    supabase: SupabaseClient, user_id: str, opt_in: bool, source: str
) -> None:
    """Record the decision. Stamped explicitly (the 167 trigger would stamp
    'manual' otherwise); an unknown id updates zero rows, silently."""
    supabase.table("users").update(
        {
            "marketing_opt_in": opt_in,
            "marketing_consent_at": datetime.now(timezone.utc).isoformat(),
            "marketing_consent_src": source,
        }
    ).eq("user_id", user_id.lower()).execute()


# ---------------------------------------------------------------------------
# Pages — one self-contained RTL card in the email palette
# ---------------------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>ريحان — تفضيلات البريد</title>
<style>
body{{margin:0;background:#EFEAE0;font-family:'Segoe UI',Tahoma,sans-serif;color:#4A4742}}
.card{{max-width:440px;margin:12vh auto 0;background:#FBF9F3;border:1px solid #E4DECF;
border-radius:14px;padding:32px 28px;text-align:center}}
h1{{color:#2E382B;font-size:20px;margin:0 0 12px}}
p{{font-size:15px;line-height:1.8;margin:0 0 20px}}
button{{background:#4C5B49;color:#fff;border:0;border-radius:10px;padding:12px 22px;
font:inherit;font-size:15px;cursor:pointer}}
button.quiet{{background:none;color:#4C5B49;text-decoration:underline;padding:4px}}
small{{display:block;margin-top:22px;color:#8A857A;font-size:12px}}
a{{color:#4C5B49}}
</style></head><body><main class="card">
<h1>{title}</h1><p>{body}</p>{form}
<small>ريحان — مساعدك القانوني الذكي · <a href="mailto:support@rayhanai.com">support@rayhanai.com</a></small>
</main></body></html>"""


def _form(path: str, user_id: str, token: str, label: str, quiet: bool = False) -> str:
    action = html.escape(f"{path}?{urlencode({'u': user_id, 't': token})}")
    cls = ' class="quiet"' if quiet else ""
    return (
        f'<form method="post" action="{action}">'
        f"<button{cls} type=\"submit\">{html.escape(label)}</button></form>"
    )


def _page(title: str, body: str, form: str = "", status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _PAGE.format(title=html.escape(title), body=html.escape(body), form=form),
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            # The token rides in the URL — never leak it to a clicked link.
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex",
        },
    )


UNSUB_PATH = "/api/v1/public/email/unsubscribe"
SUB_PATH = "/api/v1/public/email/subscribe"

_INVALID = ("الرابط غير صالح", "تعذّر التحقق من هذا الرابط. للمساعدة راسلنا على البريد أدناه.")
_FAILED = ("تعذّر حفظ طلبك", "حدث خطأ مؤقت. أعد المحاولة بعد قليل.")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/public/email/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_page(u: str = Query(""), t: str = Query("")):
    if not verify_token("unsubscribe", u, t):
        return _page(*_INVALID)
    return _page(
        "إيقاف الرسائل التسويقية",
        "لن تصلك بعدها رسائل ترويجية من ريحان. رسائل حسابك (مثل تأكيد التسجيل وانتهاء الاشتراك) تبقى كما هي.",
        _form(UNSUB_PATH, u, t, "أوقف الرسائل التسويقية"),
    )


@router.post("/public/email/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(
    u: str = Query(""),
    t: str = Query(""),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """One-click + form target. Same 200 page for valid and invalid tokens."""
    resubscribe = ""
    if verify_token("unsubscribe", u, t):
        try:
            await run_db(_set_opt_in, supabase, u, False, "unsubscribe")
        except Exception:
            logger.exception("email unsubscribe write failed")
            return _page(*_FAILED, form=_form(UNSUB_PATH, u, t, "أعد المحاولة"), status=503)
        secret = get_settings().EMAIL_LINK_SECRET or ""
        resubscribe = _form(
            SUB_PATH, u, make_token(secret, "subscribe", u), "تراجعت؟ أعد الاشتراك", quiet=True
        )
    return _page(
        "تم إيقاف الرسائل التسويقية",
        "لن نرسل لك بعد الآن رسائل ترويجية. ويمكنك تغيير ذلك في أي وقت من إعدادات حسابك.",
        resubscribe,
    )


@router.get("/public/email/subscribe", response_class=HTMLResponse)
async def subscribe_page(u: str = Query(""), t: str = Query("")):
    if not verify_token("subscribe", u, t):
        return _page(*_INVALID)
    return _page(
        "رسائل ريحان عبر البريد",
        "نرسل مقالات قانونية مختارة وأخبار ريحان، بحدّ أقصى رسالتين في الشهر، ويمكنك الإيقاف بنقرة واحدة في أي وقت.",
        _form(SUB_PATH, u, t, "نعم، أرسلوها لي"),
    )


@router.post("/public/email/subscribe", response_class=HTMLResponse)
async def subscribe(
    u: str = Query(""),
    t: str = Query(""),
    supabase: SupabaseClient = Depends(get_supabase),
):
    if not verify_token("subscribe", u, t):
        return _page(*_INVALID)
    try:
        await run_db(_set_opt_in, supabase, u, True, "repermission_email")
    except Exception:
        logger.exception("email subscribe write failed")
        return _page(*_FAILED, form=_form(SUB_PATH, u, t, "أعد المحاولة"), status=503)
    return _page("تم الاشتراك", "شكراً لك. ستصلك رسائلنا بحدّ أقصى مرتين في الشهر.")
