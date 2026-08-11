"""Payments API — /api/v1/payments (Moyasar, one-time checkout).

`.claude/plans/moyasar_payments.md` Phase C. Thin routes: validate, resolve the
caller, delegate to ``backend.app.services.payment_service``, return.

    POST /checkout          authed   price a purchase, open an `initiated` row
    POST /verify            authed   sync one payment with Moyasar's truth
    POST /webhook/moyasar   NO JWT   Moyasar's server-to-server event
    POST /{payment_id}/refund authed self-serve refund inside 24h
    POST /{payment_id}/consent authed record auto-renewal consent (flag-gated)
    GET  /history           authed   the caller's receipts
    GET  /method            authed   the stored card (brand/last4 only, never the token)
    DELETE /method          authed   forget the stored card + revoke it at Moyasar
    POST /applepay/session  authed   Apple Pay merchant-validation proxy
    GET  /subscription      authed   plan + term + cancel eligibility
    POST /subscription/cancel     authed  opt out of renewal + exit survey
    POST /subscription/reactivate authed  undo that opt-out

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
    CancelSubscriptionRequest,
    CheckoutRequest,
    RecurringConsentRequest,
    VerifyPaymentRequest,
)
from backend.app.services import (
    payment_method_service,
    payment_service,
    subscription_service,
)
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

    **This supersedes the caller's previous open quote** (migration 119): a user
    may hold at most one payable ``initiated`` row, because two discounted rows
    priced against the same untouched subscription would each apply the full
    upgrade credit. The old row moves to ``expired``; if money somehow still
    lands on it, it is honoured — expiry is bookkeeping about the QUOTE, never
    about the money.

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
          "callback_url": "https://rayhanai.com/pay/callback",
          "applepay_enabled": false      # MOYASAR_APPLEPAY_ENABLED — form offers Apple Pay only when true
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

    One ``granted: false`` case does NOT self-heal and carries a
    ``review_reason``: the row's upgrade credit was re-derived from live
    subscription state at fulfilment and is no longer owed (migration 119). The
    payment is held for an operator — still ``paid``, still refundable by the
    customer for 24h — and retrying reaches the same verdict.

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
          "status": "initiated"|"expired"|"paid"|"failed"|"refunded",
          "currency": "SAR",
          "amount_sar": "89.90", "amount_halalas": 8990,
          "vat_amount_sar": "11.73", "net_amount_sar": "78.17",
          "upgrade_credit_sar": "0.00",
          "refund_fee_sar": null, "refunded_amount_sar": null,
          "provider": "moyasar",
          "created_at", "paid_at", "fulfilled_at", "updated_at",
          "refundable": true,               # status=paid AND within 24h AND not superseded
          "superseded": false,              # a later upgrade already spent this one's value
          "refund_deadline": "2026-08-04T…" # paid_at + 24h, null before payment
        }

    ``refundable`` is computed server-side on purpose: the 24h promise is
    measured by the server's clock, and the receipts list must not decide it
    from the browser's. ``superseded`` is the second reason it can be false —
    refunds unwind newest-first (see ``/{payment_id}/refund``), and this list is
    the only place that can see all of the caller's payments at once, so it is
    resolved here rather than per row.
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


# ── /subscription ────────────────────────────────────────────────────────────
# The cancellation feature (`.claude/plans/subscription_cancellation.md`). It
# lives on the payments router because إعدادات الحساب reads it next to the
# receipts list and because "what did you buy, and until when" is the same
# question /history answers per row — but the logic is in its own service:
# nothing here moves money.

@router.get("/subscription")
async def subscription(
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """The caller's subscription as إعدادات الحساب renders it.

    Returns::

        {
          "plan_id": "pro",                     # null when there is no row
          "plan_name_ar": "الاحترافية",
          "expires_at": "2026-09-02T…",         # null for a non-expiring grant
          "source": "payment",                  # payment|code|manual|signup
          "cancellable": true,                  # a PAID term still running
          "renewal_cancelled_at": null          # set = already opted out
        }

    ``cancellable`` describes the SUBSCRIPTION, not the button: it stays true
    while ``renewal_cancelled_at`` is set, because an undo makes cancelling
    legal again. The dialog reads both.

    This exists rather than a ``source`` field on ``GET /usage`` because the
    quota report is read on the message path by every send — a money-shaped
    field does not belong on it.
    """
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    result = await subscription_service.get_subscription(supabase, user_id)
    response.headers["Cache-Control"] = _NO_STORE
    return result


@router.post("/subscription/cancel")
async def cancel_subscription(
    payload: CancelSubscriptionRequest,
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Opt out of renewal and record the exit survey.

    Body ``{"reason": "expensive"|"no_longer_needed"|"something_wrong"|"other",
    "comment": "…"?}``. Returns the same shape as ``GET /subscription``, with
    ``renewal_cancelled_at`` now set.

    **Nothing about the current term changes** — access runs to ``expires_at``
    and then falls back to the free plan, exactly as it would have. This is not
    a refund (that is ``POST /{payment_id}/refund``, unrelated and unchanged),
    and in Wave 1 it stops no automatic charge, because there is none.

    Errors: 400 VALIDATION_ERROR (unknown reason),
    409 SUBSCRIPTION_NOT_CANCELLABLE (no paid subscription with a running term),
    409 SUBSCRIPTION_ALREADY_CANCELLED (a second call — never a second survey
    row), 503 when the write itself fails.
    """
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    result = await subscription_service.cancel_renewal(
        supabase, user_id, reason=payload.reason, comment=payload.comment
    )
    response.headers["Cache-Control"] = _NO_STORE
    return result


@router.post("/subscription/reactivate")
async def reactivate_subscription(
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Undo a cancellation («تراجع عن الإلغاء»). Free — no money moved either way.

    Clears ``renewal_cancelled_at`` and stamps ``revoked_at`` on the newest
    un-revoked survey row. Returns the refreshed ``GET /subscription`` shape.

    Errors: 409 SUBSCRIPTION_NOT_CANCELLABLE (nothing to undo, or the term has
    already ended — a lapsed plan comes back only through a new purchase),
    503 when the write itself fails.
    """
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    result = await subscription_service.reactivate_renewal(supabase, user_id)
    response.headers["Cache-Control"] = _NO_STORE
    return result


# ── /method (stored card — auto-renewal plan §6 / §8) ────────────────────────
# The card-update surface the dunning ladder needs. Read is display-only; the
# token itself has no route, on any method, ever.

@router.get("/method")
async def payment_method(
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """The caller's stored card, as إعدادات الحساب renders it.

    Returns (flat — ``has_method`` is the only field guaranteed meaningful)::

        {
          "enabled": true,                  # SUBSCRIPTION_AUTO_RENEWAL_ENABLED
          "has_method": true,
          "payment_method_id": "<uuid>",
          "provider": "moyasar",
          "brand": "mada",                  # may be null
          "last4": "1234",                  # may be null
          "exp_month": 12, "exp_year": 2030,
          "consent_given_at": "2026-08-11T…",
          "created_at": "2026-08-11T…"
        }

    With the feature off, or with nothing stored, every field but ``enabled``
    is null/false and the DB is not touched at all — so "feature off", "no
    card" and "backend predates this endpoint" all look the same to the
    settings dialog, which is what keeps a billing hiccup from standing in
    front of the password and delete-account controls.

    There is **no field carrying the provider token**, on any branch: it is not
    selected on this path, so it cannot be serialized by accident.
    """
    response.headers["Cache-Control"] = _NO_STORE
    if not payment_method_service.auto_renewal_enabled():
        return {"enabled": False, **payment_method_service.describe_method(None)}
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    row = await run_db(payment_method_service.get_active_method, supabase, user_id)
    return {"enabled": True, **payment_method_service.describe_method(row)}


@router.delete("/method")
async def delete_payment_method(
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Forget the caller's stored card («حذف البطاقة المحفوظة»).

    Marks the row revoked AND asks Moyasar to invalidate the token — a live
    token behind a card the user believes they deleted is the bug this endpoint
    exists to prevent.

    Deliberately NOT gated on the feature flag: a card stored while the feature
    was on must remain deletable after it is turned off.

    Returns ``{"revoked": bool, "provider_confirmed": bool, "has_method": false,
    …the emptied card shape}`` — the emptied state rides along so the caller can
    write it straight into its cache.

    ``revoked: false`` simply means there was nothing stored (idempotent, never
    an error). ``provider_confirmed: false`` with ``revoked: true`` means our row
    is gone but Moyasar did not confirm — logged at ERROR for an operator; the
    user is still shown a success, because from their side the card IS
    forgotten.
    """
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    result = await payment_method_service.revoke_active_method(
        supabase, user_id, reason="user_request"
    )
    response.headers["Cache-Control"] = _NO_STORE
    return result


# ── /{payment_id}/consent ────────────────────────────────────────────────────
# Declared before /{payment_id}/refund but after every static path, for the
# same declaration-order reason noted below.

@router.post("/{payment_id}/consent")
async def recurring_consent(
    payment_id: str,
    payload: RecurringConsentRequest,
    response: Response,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Record consent to auto-renewal for an open checkout, BEFORE it is paid.

    Body: ``{"accepted": true}``. The page must call this after the user ticks
    the checkbox and **before** mounting the Moyasar form with
    ``credit_card: {save_card: true}`` — a token stored against a payment with
    no consent record is refused at capture time and silently discarded.

    Returns::

        {"enabled": true, "accepted": true, "payment_id": "<uuid>",
         "plan_id": "pro", "consent_given_at": "2026-08-11T…",
         "disclosure_version": "v1",
         "recurring_disclosure_ar": "بتأكيد الشراء تُفوّض «ريحان» …"}

    …or, when the feature is off::

        {"enabled": false, "accepted": false, "payment_id": "<uuid>",
         "consent_given_at": null}

    The flag-off answer is a **200**, not an error: the page calls this
    unconditionally for pro/max and branches on ``enabled``. It is also exactly
    what the checkout session's ``requires_recurring_consent: false`` already
    told the page to expect, so a correct client never gets here with the flag
    down.

    Idempotent — a reload returns the FIRST consent, with its original
    timestamp.

    Errors: 400 VALIDATION_ERROR (``accepted`` not true),
    404 PAYMENT_NOT_FOUND (unknown / another user's payment),
    409 PAYMENT_CONSENT_INVALID (the payment is no longer an open checkout, or
    the plan does not renew — ``basic`` never does),
    503 SERVICE_UNAVAILABLE (the consent write failed; the purchase must not
    proceed as a recurring one on an unprovable consent).
    """
    validate_uuid(payment_id, "معرف عملية الدفع")
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    result = await payment_service.record_recurring_consent(
        supabase, user_id, payment_id, accepted=payload.accepted
    )
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

    **Refunds unwind newest-first.** A payment whose value a LATER purchase
    already spent as an upgrade credit is refused (409): returning it would hand
    back the money while ``revoke_plan_grant`` — correctly — declines to eat
    days of the plan the user currently holds. Refunding the newest purchase
    first RESTORES the plan underneath it and un-blocks the one below
    automatically, so the whole ladder is still self-serve, just in order.

    Returns the updated receipt (same shape as ``/history`` items) plus::

        "revoked": true,
        "revoke_action": "restored"|"subtracted"|"already_revoked"|…

    ``revoked: false`` with ``revoke_action: "plan_switched"`` means the money
    went back and the entitlement STANDS — it is logged at ERROR and needs a
    human. Self-serve refunds cannot produce it any more (the guard above
    refuses first); a dashboard-side refund still can.

    Errors: 404 PAYMENT_NOT_FOUND (not the caller's / unknown),
    409 PAYMENT_REFUND_WINDOW_CLOSED (past 24h, already refunded, not paid, or
    superseded by a later upgrade),
    502 PAYMENT_PROVIDER_ERROR (Moyasar refused the refund).
    """
    validate_uuid(payment_id, "معرف عملية الدفع")
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    result = await payment_service.refund_payment(supabase, user_id, payment_id)
    response.headers["Cache-Control"] = _NO_STORE
    return result
