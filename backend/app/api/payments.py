"""Payments API — /api/v1/payments (Moyasar, one-time checkout).

`.claude/plans/moyasar_payments.md` Phase C. Thin routes: validate, resolve the
caller, delegate to ``backend.app.services.payment_service``, return.

    POST /checkout          authed   price a purchase, open an `initiated` row
    POST /verify            authed   sync one payment with Moyasar's truth
    POST /webhook/moyasar   NO JWT   Moyasar's server-to-server event
    POST /{payment_id}/refund authed self-serve refund inside 24h
    GET  /history           authed   the caller's receipts
    POST /applepay/session  authed   Apple Pay merchant-validation proxy

Two things are unusual here and both are deliberate:

* **The webhook has no auth dependency.** Moyasar authenticates with a shared
  ``secret_token`` *inside the JSON body* — there is no HMAC header to verify.
  It is compared constant-time (``hmac.compare_digest`` on bytes, the
  ``internal_webhooks.py`` pattern) and the endpoint is fail-closed: with
  ``MOYASAR_WEBHOOK_SECRET`` unset, every call 401s.
* **The webhook almost never returns non-2xx.** Moyasar retries 5 times over
  ~2h and then drops the event forever, so an unknown payment, an unhandled
  event type, or a mode mismatch answers 200 + a log line. The only exceptions:
  401 for a bad secret, and 503 when a transient provider/DB failure means a
  retry would genuinely help.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase, validate_uuid
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.models.requests import (
    ApplePaySessionRequest,
    CheckoutRequest,
    VerifyPaymentRequest,
)
from backend.app.services import payment_service
from backend.app.services.case_service import get_user_id
from shared.auth.jwt import AuthUser
from shared.config import get_settings
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter()

# Money answers are per-user and must never be reused by another visitor —
# same posture as the library's per-user reveals.
_NO_STORE = "private, no-store"


# ── /checkout ────────────────────────────────────────────────────────────────

@router.post("/checkout")
async def checkout(
    payload: CheckoutRequest,
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Price a purchase and open an ``initiated`` ledger row.

    No Moyasar call happens here — the browser's ``moyasar.js`` form creates the
    payment using the returned ``amount_halalas`` + ``publishable_key``, and
    carries ``metadata: {payment_id}`` so both confirmation paths can find our
    row again.

    Returns::

        {
          "payment_id": "<uuid>",          # put this in the form's metadata
          "plan_id": "pro",
          "plan_name_ar": "الاحترافية",
          "amount_halalas": 8990,          # int — what the form charges
          "amount_sar": "89.90",           # 2-dp string
          "credit_sar": "0.00",            # prorated upgrade credit, "0.00" if none
          "vat_amount_sar": "11.73",
          "currency": "SAR",
          "description": "ريحان — الاحترافية",
          "publishable_key": "pk_test_…",
          "callback_url": "https://rayhanai.com/pay/callback"
        }

    Errors: 400 PAYMENT_PLAN_NOT_PURCHASABLE (unknown plan / price_sar NULL),
    409 PAYMENT_DOWNGRADE_BLOCKED (a higher plan is still active),
    503 SERVICE_UNAVAILABLE (Moyasar keys not configured).
    """
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    result = await payment_service.create_checkout(supabase, user_id, payload.plan_id)
    response.headers["Cache-Control"] = _NO_STORE
    return result


# ── /verify ──────────────────────────────────────────────────────────────────

@router.post("/verify")
async def verify(
    payload: VerifyPaymentRequest,
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Sync one payment with Moyasar and grant the plan when it is paid.

    Called twice per purchase: once from ``on_completed`` before the 3DS
    redirect (status ``initiated`` → ``pending``), once from ``/pay/callback``
    after it (``paid`` / ``failed``). Safe to call repeatedly — ``grant_plan``
    no-ops on ``fulfilled_at``.

    Returns one of::

        {"status": "pending", "payment_id": "…", "granted": false,
         "provider_status": "initiated"}

        {"status": "paid", "payment_id": "…", "granted": true,
         "plan_id": "pro", "plan_name_ar": "الاحترافية",
         "expires_at": "2026-09-02T…", "amount_sar": "89.90"}

        {"status": "failed", "payment_id": "…", "granted": false,
         "provider_message": "Insufficient funds"}

    ``granted: false`` on a ``paid`` response means the money landed but the
    grant did not — the webhook (or another /verify) will finish it; the page
    should say "payment received, activating" rather than showing an error.

    Errors: 404 PAYMENT_NOT_FOUND (unknown id, another user's payment, or an id
    from the other key mode), 400 PAYMENT_PROVIDER_ERROR (amount/currency
    disagree with our row), 503 on provider/auth outage.
    """
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    result = await payment_service.verify_payment(supabase, user_id, payload.moyasar_id)
    response.headers["Cache-Control"] = _NO_STORE
    return result


# ── /webhook/moyasar (no JWT) ────────────────────────────────────────────────

def _webhook_authorized(body: dict) -> bool:
    """Constant-time check of the body's ``secret_token``.

    Fail-closed: an unset ``MOYASAR_WEBHOOK_SECRET`` rejects everything, exactly
    like ``INTERNAL_WEBHOOK_SECRET``. Compared on BYTES because
    ``hmac.compare_digest`` raises TypeError on non-ASCII ``str`` — and this
    value arrives from an unauthenticated request body, so a junk token would
    turn a clean 401 into a 500 (which Moyasar would then retry).
    """
    expected = (get_settings().MOYASAR_WEBHOOK_SECRET or "").strip()
    if not expected:
        logger.error("Moyasar webhook called but MOYASAR_WEBHOOK_SECRET is unset — refused")
        return False
    supplied = body.get("secret_token")
    supplied = supplied.strip() if isinstance(supplied, str) else ""
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


@router.post("/webhook/moyasar")
async def moyasar_webhook(
    request: Request,
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Moyasar server-to-server event. NOT for browsers.

    Flow: parse → constant-time ``secret_token`` → reject a ``live`` flag that
    disagrees with our key mode → **re-fetch the payment from the API** and act
    only on that object → route by event type.

    ``payment_paid`` runs the same mark-paid + snapshot + grant path as
    ``/verify``; ``payment_failed``/``payment_abandoned`` mark the row failed;
    ``payment_refunded`` marks it refunded and calls ``revoke_plan_grant``.
    Anything else is answered 200 and logged.

    Responses: 200 ``{"status": "ok"|"ignored", …}`` · 401 on a bad/absent
    secret · 503 only when a retry would help (provider unreachable, DB write
    failed).
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed body: no secret is provable
        logger.warning("Moyasar webhook: unparseable body")
        raise LunaHTTPException(
            status_code=401, code=ErrorCode.AUTH_INVALID, detail="invalid webhook payload"
        )

    if not isinstance(body, dict) or not _webhook_authorized(body):
        raise LunaHTTPException(
            status_code=401, code=ErrorCode.AUTH_INVALID, detail="invalid webhook secret"
        )

    try:
        result = await payment_service.handle_webhook_event(supabase, body)
    except payment_service.WebhookRetryable as exc:
        # The ONE deliberate non-2xx on a verified event: money moved but our
        # books didn't. Spending one of the 5 retries buys a real second chance,
        # which is exactly what retries are for. Content problems never land
        # here — they return 200 above.
        logger.error("Moyasar webhook asking for retry: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "retry", "detail": "temporary processing failure"},
            headers={"Retry-After": "60"},
        )
    except Exception as exc:  # noqa: BLE001
        # Belt and braces: an unexpected bug must not 500 into Moyasar's retry
        # budget. Log loudly, answer 200 — /verify remains the second path.
        logger.exception("Moyasar webhook handler crashed: %s", exc)
        return {"status": "ignored", "reason": "handler_error"}

    return result


# ── /history ─────────────────────────────────────────────────────────────────

@router.get("/history")
async def history(
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """The caller's own receipts, newest first, capped at 50.

    Returns ``{"payments": [ …summary… ]}`` where each item is::

        {
          "payment_id", "plan_id", "plan_name_ar",
          "status": "initiated"|"paid"|"failed"|"refunded",
          "currency": "SAR",
          "amount_sar": "89.90", "amount_halalas": 8990,
          "vat_amount_sar": "11.73", "net_amount_sar": "78.17",
          "upgrade_credit_sar": "0.00",
          "refund_fee_sar": null, "refunded_amount_sar": null,
          "provider": "moyasar",
          "created_at", "paid_at", "fulfilled_at", "updated_at",
          "refundable": true,               # status=paid AND within 24h of paid_at
          "refund_deadline": "2026-08-04T…" # paid_at + 24h, null before payment
        }

    ``refundable`` is computed server-side on purpose: the 24h promise is
    measured by the server's clock, and the receipts list must not decide it
    from the browser's.
    """
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    payments = await payment_service.list_history(supabase, user_id)
    response.headers["Cache-Control"] = _NO_STORE
    return {"payments": payments}


# ── /applepay/session ────────────────────────────────────────────────────────

@router.post("/applepay/session")
async def applepay_session(
    payload: ApplePaySessionRequest,
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
):
    """Apple Pay merchant validation — proxies ``GET /v1/applepay/initiate``.

    Called from Safari's ``onvalidatemerchant``; Moyasar's JSON is returned
    verbatim because Apple's ``completeMerchantValidation`` wants that object
    unmodified. Best-effort: a 502 PAYMENT_PROVIDER_ERROR here means the page
    should fall back to cards-only, not fail the checkout.

    Requires the domain to be registered under Moyasar → Apple Pay Domains and
    the association file served at ``/.well-known/`` on
    ``MOYASAR_APPLEPAY_DOMAIN``.
    """
    result = await payment_service.applepay_session(payload.validation_url)
    response.headers["Cache-Control"] = _NO_STORE
    return result


# ── /{payment_id}/refund ─────────────────────────────────────────────────────
# Declared LAST: FastAPI matches in declaration order, so every static path
# above wins over this template.

@router.post("/{payment_id}/refund")
async def refund(
    payment_id: str,
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Self-serve refund of the caller's own payment, inside 24 hours.

    Refunds ``amount − 2.00 SAR`` (the disclosed processing fee), stamps
    ``refund_fee_sar`` + ``refunded_amount_sar``, and revokes the granted term
    via ``revoke_plan_grant`` — a refund is an UNDO, so a refunded upgrade
    restores the prior plan rather than destroying it.

    Returns the updated receipt (same shape as ``/history`` items) plus::

        "revoked": true,
        "revoke_action": "restored"|"subtracted"|"plan_switched"|…

    Errors: 404 PAYMENT_NOT_FOUND (not the caller's / unknown),
    409 PAYMENT_REFUND_WINDOW_CLOSED (past 24h, already refunded, or not paid),
    502 PAYMENT_PROVIDER_ERROR (Moyasar refused the refund).
    """
    validate_uuid(payment_id, "معرف عملية الدفع")
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    result = await payment_service.refund_payment(supabase, user_id, payment_id)
    response.headers["Cache-Control"] = _NO_STORE
    return result
