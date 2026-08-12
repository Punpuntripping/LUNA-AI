"""Moyasar payments — checkout, verify, webhook, refund, history.

Implements `.claude/plans/moyasar_payments.md` Phase C. Wave 1 is **one-time
purchases**: the browser's `moyasar.js` form creates the payment, we only ever
price it, verify it, and grant the plan.

Three rules this module exists to enforce:

1. **The server owns the amount.** The client sends a `plan_id` and nothing
   else. Price comes from ``plans.price_sar``, the upgrade credit is computed
   here, and the VAT split is stamped once at initiation (never recomputed at
   display time — a future rate change must not rewrite history).
2. **Only a re-fetch is evidence.** Both confirmation paths (the browser's
   `/verify` after the redirect, and Moyasar's webhook) re-fetch
   ``GET /v1/payments/{id}`` with our secret key and trust ONLY that object.
   The webhook body carries a shared ``secret_token`` rather than an HMAC over
   the payload, so a forged body must buy nothing.
3. **Both paths are idempotent and either one alone suffices.** The redirect can
   be lost (closed tab) and the webhook is dropped after 5 failed retries.
   Whichever arrives first grants; the second no-ops on
   ``payment_transactions.fulfilled_at`` inside ``grant_plan``.

Mode safety: there is no separate sandbox host — ``sk_test_``/``sk_live_``
decides whether real cards are charged. ``verify_moyasar_config`` is called at
boot from ``backend.app.main.create_app`` and refuses to start on a key-mode
mismatch or a live key outside production.

DB dependency: migration ``113_payment_refund_revoke.sql`` (APPLIED) adds
``vat_amount_sar``, ``net_amount_sar``, ``upgrade_credit_sar``,
``refund_fee_sar``, ``refunded_amount_sar``, ``revoked_at``, ``prior_plan_id``,
``prior_expires_at`` plus two service-role RPCs:

* ``stamp_payment_prior_snapshot(payment_id)`` — 113 deliberately did NOT touch
  ``grant_plan`` (live money path), so the upgrade snapshot is a separate call.
  Every paid path runs **mark paid → stamp_payment_prior_snapshot → grant_plan**,
  in that order, unconditionally; the RPC self-guards.
* ``revoke_plan_grant(payment_id)`` — the refund mirror. Requires the row to
  already be ``status='refunded'`` and reports what it did via its ``action``
  column (see ``REVOKE_ACTIONS_OK``).

Migration ``117_payment_retention.sql`` (APPLIED) then makes the row outlive its
buyer: ``user_id`` is nullable with ON DELETE SET NULL, and
``customer_name_snapshot`` / ``customer_email_snapshot`` carry the identity so a
payment survives account deletion as a retained financial record rather than
being cascaded away — a sequential ``receipt_no`` (114) may not have holes in it.

Migration ``119_payment_credit_integrity.sql`` closes the two money holes the
2026-08-07 security audit found, both of which were the SAME mistake — reading
live subscription state and never re-reading it:

* **H-4** the upgrade credit was priced once and never consumed, reserved or
  expired, so N checkouts opened against ONE untouched subscription each applied
  the full credit. 119 adds the ``expired`` status and a partial unique index on
  one open credited checkout per user; this module clamps the proration ratio,
  supersedes the caller's earlier open rows, and RE-DERIVES the charge from live
  state before granting (``_revalidate_credited_charge``).
* **M-1** refunding a payment whose value a later upgrade had already spent
  returned the money and kept the plan (``revoke_plan_grant`` answers
  ``plan_switched`` and no-ops). Refunds now unwind NEWEST-FIRST
  (``_is_superseded``), and ``plan_switched`` is no longer a silent success.

Migration ``120_subscription_cancellation.sql`` adds a renewal opt-out flag that
this module has exactly ONE dealing with: after a successful grant it clears
``user_subscriptions.renewal_cancelled_at`` via ``subscription_service`` —
buying again is re-opting in. Everything else about cancellation lives there.

Migration ``132_subscription_auto_renewal.sql`` (⚠ NOT APPLIED at the time of
writing) turns that flag load-bearing. This module gains exactly three dealings
with auto-renewal, and every one of them is inert while
``settings.SUBSCRIPTION_AUTO_RENEWAL_ENABLED`` is False:

* ``create_checkout`` publishes ``requires_recurring_consent`` +
  ``recurring_disclosure_ar`` (false/null with the flag down, and always for
  ``basic``);
* ``record_recurring_consent`` stamps the consent artefact on an open row;
* ``_mark_paid_and_grant`` hands the provider payload to
  ``payment_method_service.capture_payment_method``, which stores the card token
  — from BOTH confirmation paths, because 3DS destroys the callback page.
* ``_expire_open_checkouts`` now excludes ``initiated_by='renewal'``: a renewal
  row is ``initiated`` too, and superseding it would let a user kill their own
  renewal just by opening ``/pay``.

The job that spends those tokens lives in ``renewal_service`` — a separate
module, on the 113/120 precedent that the grant path is not edited for a side
concern.
"""
from __future__ import annotations

import asyncio
import logging
import uuid as uuid_module
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional
from urllib.parse import quote, urlparse

import httpx
from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException, MSG_SERVICE_UNAVAILABLE
from backend.app.services import payment_method_service, subscription_service
from backend.app.services.audit_service import write_audit_log
from shared.config import get_settings
from shared.db.run import run_db

from backend.app.services.receipt_service import (
    send_payment_receipt,
    send_refund_receipt,
)

logger = logging.getLogger(__name__)

# ── provider constants ───────────────────────────────────────────────────────

PROVIDER = "moyasar"
MOYASAR_API_BASE = "https://api.moyasar.com/v1"

# One tight timeout for every provider call. Moyasar is on the critical path of
# a user staring at a spinner; 15s is generous for their API and short enough
# that a hung socket cannot pin a worker thread.
HTTP_TIMEOUT_S = 15.0

# Retries are for IDEMPOTENT calls only (GET /payments/{id}). A refund POST is
# never retried automatically — a double refund is worse than a failed one.
GET_RETRIES = 2

CURRENCY = "SAR"
VAT_RATE = Decimal("0.15")          # inclusive — net = charge / 1.15
# ── Refund fee: FULL COST RECOVERY + 0.50 SAR (owner 2026-08-05) ────────────
#
# Moyasar support confirmed in writing (2026-08-05) that a refund costs the
# merchant TWICE, and neither cost is recoverable:
#   1. the ORIGINAL transaction fee is never returned (card schemes already
#      did the work) — e.g. 1.73 SAR on a 49.90 mada charge; and
#   2. a FLAT refund-execution fee of 1.00 + 15% VAT = 1.15 SAR, charged the
#      same for full or partial refunds, on every card network.
# Their worked example: 49.90 in → 48.17 credited; refund 46.90 → 48.17 −
# 46.90 − 1.15 = 0.12 SAR left. A flat 3 SAR fee was therefore break-even by
# accident, not by design.
#
# So the deduction is computed PER PAYMENT from the provider's own reported
# fee (``raw_payload.fee``, halalas — the authoritative number for THAT card
# network and plan price), never from a rate we assume:
#
#     refund_fee = original_provider_fee + REFUND_EXECUTION_FEE + MARGIN
#
# Margin is deliberately small (0.50 SAR): the fee exists to make the business
# whole on a refund, not to profit from one.
#
# If ``fee`` is missing from the stored payload (older rows, provider change),
# fall back to a conservative flat figure rather than silently under-charging.
REFUND_EXECUTION_FEE_HALALAS = 115   # Moyasar's flat refund fee, VAT included
REFUND_MARGIN_HALALAS = 50           # our 0.50 SAR — break-even + a token margin
                                     # (owner 2026-08-05: refunds should cost the
                                     # business nothing, not earn it anything)
REFUND_FEE_FALLBACK_HALALAS = 340    # ≈ a mada basic refund; used ONLY when
                                     # raw_payload.fee is absent (legacy rows)


def _refund_fee_halalas(row: dict) -> int:
    """Total deduction for THIS payment: provider costs + our 0.50 margin.

    The provider fee is read from the stored ``raw_payload`` — the value
    Moyasar itself reported for the original charge — so mada vs Visa and
    49.90 vs 189.90 are each recovered exactly, with no rate table to drift.
    """
    payload = row.get("raw_payload") or {}
    provider_fee = payload.get("fee") if isinstance(payload, dict) else None
    try:
        provider_fee_halalas = int(provider_fee)
    except (TypeError, ValueError):
        logger.warning(
            "refund fee: payment=%s has no usable raw_payload.fee — using fallback",
            row.get("payment_id"),
        )
        return REFUND_FEE_FALLBACK_HALALAS
    if provider_fee_halalas < 0:
        return REFUND_FEE_FALLBACK_HALALAS
    return provider_fee_halalas + REFUND_EXECUTION_FEE_HALALAS + REFUND_MARGIN_HALALAS


REFUND_WINDOW = timedelta(hours=24)
MIN_HALALAS = 100                   # Moyasar's minimum chargeable amount
MIN_CHARGE_SAR = Decimal("1.00")
HISTORY_LIMIT = 50

# ── Upgrade-credit integrity (security review 2026-08-07, H-4) ───────────────
#
# The proration credit used to be priced once at checkout and never looked at
# again: nothing consumed it, reserved it, or expired it. Three layers now sit
# between a quoted credit and a granted plan, and each closes a different half
# of the hole:
#
#   1. the ratio is CLAMPED to one plan period (``_upgrade_credit``), so a
#      stacked term can never be worth more credit than one period of it. Buying
#      pro 3× used to mean 90 remaining days → 269.70 credit → a 1.00 SAR `max`,
#      which is less than the ~1.73 SAR Moyasar fee on the charge;
#   2. at most ONE open checkout per user: every new checkout supersedes the
#      caller's earlier ``initiated`` rows to ``STATUS_EXPIRED``, with 119's
#      partial unique index as the backstop. Without it a user opens N checkouts
#      against the SAME untouched subscription, each applying the full credit,
#      then pays them all;
#   3. the charge is RE-DERIVED from live state at fulfilment
#      (``_revalidate_credited_charge``) — a discount is honoured only while the
#      subscription that justified it is still standing.
#
# CREDIT_QUOTE_TTL is deliberately generous: it exists to stop a discounted
# quote being banked for days (prod held a payable 100.00 SAR `max` quote for
# three), not to punish a slow 3DS challenge, which completes in minutes.
# CREDIT_EPSILON_SAR absorbs rounding and clock skew between the two
# derivations; it can never absorb a real discount, the smallest of which is
# 1.00 SAR.
STATUS_EXPIRED = "expired"           # superseded / stale checkout (migration 119)
CREDIT_QUOTE_TTL = timedelta(hours=24)
CREDIT_EPSILON_SAR = Decimal("0.02")

# Purchasable-plan ordering. Only these three participate in the downgrade
# guard and in proration; free / marketing_* / dev are rank-less, so a user on a
# promo or dev plan can buy anything (and earns no credit — see create_checkout).
PLAN_RANK: dict[str, int] = {"basic": 1, "pro": 2, "max": 3}

# Webhook events we act on. Everything else (payment_authorized,
# payment_captured, payment_voided, payment_verified) is answered 200 + logged:
# we do not use manual capture, and an unhandled event must never burn a retry.
EVENT_PAID = "payment_paid"
EVENT_FAILED = "payment_failed"
EVENT_ABANDONED = "payment_abandoned"
EVENT_REFUNDED = "payment_refunded"
HANDLED_EVENTS = frozenset({EVENT_PAID, EVENT_FAILED, EVENT_ABANDONED, EVENT_REFUNDED})

# ── Arabic messages (rule 5 — every user-facing string) ──────────────────────

PAYMENTS_UNAVAILABLE_AR = "خدمة الدفع غير متاحة حالياً، حاول لاحقاً"
PLAN_NOT_PURCHASABLE_AR = "هذه الباقة غير متاحة للشراء"
DOWNGRADE_BLOCKED_AR = "لديك باقة أعلى فعّالة حالياً. يمكنك شراء باقة أقل بعد انتهاء باقتك الحالية."
PAYMENT_NOT_FOUND_AR = "لم يتم العثور على عملية الدفع"
PAYMENT_MISMATCH_AR = "بيانات عملية الدفع غير مطابقة"
PROVIDER_ERROR_AR = "تعذّر إتمام العملية مع مزوّد الدفع، حاول مجدداً"
REFUND_WINDOW_CLOSED_AR = "انتهت مهلة الاسترداد (٢٤ ساعة). للمساعدة تواصل معنا على support@rayhanai.com"
REFUND_NOT_PAID_AR = "لا يمكن استرداد عملية غير مكتملة"
REFUND_ALREADY_AR = "تم استرداد هذه العملية من قبل"
REFUND_FAILED_AR = "تعذّر تنفيذ الاسترداد، تواصل معنا على support@rayhanai.com"
REFUND_SUPERSEDED_AR = (
    "لا يمكن استرداد هذه العملية لأن قيمتها استُخدمت كخصم في ترقية أحدث. "
    "استرد العملية الأحدث أولاً، أو تواصل معنا على support@rayhanai.com"
)
APPLEPAY_URL_INVALID_AR = "رابط التحقق غير صالح"
APPLEPAY_DISABLED_AR = "Apple Pay غير متاح حالياً"


# ═══════════════════════════════════════════════════════════════════════════
# 1. Configuration + the boot-time mode guard
# ═══════════════════════════════════════════════════════════════════════════


class MoyasarError(Exception):
    """Any failure talking to Moyasar. ``status``/``payload`` carry their reply."""

    def __init__(self, message: str, *, status: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class MoyasarNotFound(MoyasarError):
    """404 from Moyasar — the id does not exist **for our key**.

    This is what enforces test/live isolation: a sandbox payment id is simply
    not fetchable with a live key, and vice-versa, so it can never grant.
    """


class MoyasarUnavailable(MoyasarError):
    """Transport failure / 5xx — retryable, not the caller's fault."""


class MoyasarUnconfigured(MoyasarError):
    """MOYASAR_SECRET_KEY (or the publishable key) is unset — fail closed."""


class WebhookRetryable(Exception):
    """Transient failure while processing an AUTHENTICATED webhook.

    The route answers 503 so Moyasar retries. This is the ONE case where
    spending one of the 5 retries buys a real second chance (provider
    unreachable, DB write failed — money is in, the grant is not). Content
    problems — unknown payment, unhandled event, mode mismatch, amount
    disagreement — never raise this: they answer 200 and are logged.
    """


def _key_mode(raw: Optional[str], expected_prefix: str, field: str) -> Optional[str]:
    """``'test'`` / ``'live'`` for a Moyasar key; ``None`` when unset.

    A non-empty value that matches no known prefix raises: that is a typo or a
    pasted-wrong secret, and guessing its mode is exactly the accident this
    guard exists to prevent.
    """
    key = (raw or "").strip()
    if not key:
        return None
    for mode in ("test", "live"):
        if key.startswith(f"{expected_prefix}{mode}_"):
            return mode
    raise RuntimeError(
        f"{field} has an unrecognized prefix — expected "
        f"{expected_prefix}test_… or {expected_prefix}live_…"
    )


def moyasar_mode(settings=None) -> Optional[str]:
    """The configured mode (``'test'``/``'live'``), or ``None`` when no key is set.

    Never raises — the boot guard already rejected junk keys, and a webhook must
    not 500 because of config.
    """
    settings = settings or get_settings()
    try:
        return _key_mode((settings.MOYASAR_SECRET_KEY or ""), "sk_", "MOYASAR_SECRET_KEY")
    except RuntimeError:
        return None


def verify_moyasar_config(settings=None) -> None:
    """Boot guard — called from ``create_app``. Raises to refuse the boot.

    Two accidents made impossible:
      (a) **mixed modes** — a live secret key with a test publishable key (or
          vice-versa) means the browser creates a payment we can never fetch;
      (b) **a live key outside production** — ``https://api.moyasar.com/v1``
          serves both modes, so a stray ``sk_live_`` in a dev ``.env`` charges
          real cards. ``is_production`` (APP_ENV *or* ENVIRONMENT == production)
          is the gate, so neither variable alone can be forgotten into an
          accidental live run.

    No key set at all is perfectly fine — payments are simply closed (checkout
    503s, the webhook 401s) and the rest of the app boots normally.
    """
    settings = settings or get_settings()
    sk_mode = _key_mode(settings.MOYASAR_SECRET_KEY, "sk_", "MOYASAR_SECRET_KEY")
    pk_mode = _key_mode(settings.MOYASAR_PUBLISHABLE_KEY, "pk_", "MOYASAR_PUBLISHABLE_KEY")

    if sk_mode and pk_mode and sk_mode != pk_mode:
        raise RuntimeError(
            f"Moyasar key mode mismatch: MOYASAR_SECRET_KEY is '{sk_mode}' but "
            f"MOYASAR_PUBLISHABLE_KEY is '{pk_mode}'. Both keys must come from "
            "the same dashboard mode."
        )

    if "live" in (sk_mode, pk_mode) and not settings.is_production:
        raise RuntimeError(
            "Refusing to boot: a Moyasar _live_ key is configured while "
            f"ENVIRONMENT='{settings.ENVIRONMENT}' / APP_ENV='{settings.APP_ENV}'. "
            "Live keys charge real cards — use sk_test_/pk_test_ outside production."
        )

    if sk_mode:
        logger.info("Moyasar payments enabled (mode=%s)", sk_mode)


def _require_keys() -> tuple[str, str]:
    """Secret + publishable key, or a 503. Fail-closed per Phase A.

    The publishable key is required too: without it the browser cannot mount the
    form, so returning a checkout row the user can never pay would just leave
    orphan ``initiated`` rows behind.
    """
    settings = get_settings()
    secret = (settings.MOYASAR_SECRET_KEY or "").strip()
    publishable = (settings.MOYASAR_PUBLISHABLE_KEY or "").strip()
    if not secret or not publishable:
        logger.error(
            "Checkout attempted with Moyasar unconfigured (secret=%s publishable=%s)",
            bool(secret), bool(publishable),
        )
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=PAYMENTS_UNAVAILABLE_AR,
            headers={"Retry-After": "60"},
        )
    return secret, publishable


# ═══════════════════════════════════════════════════════════════════════════
# 2. Money arithmetic — Decimal only, never float
# ═══════════════════════════════════════════════════════════════════════════


def _dec(value: Any, default: str = "0") -> Decimal:
    """Parse a DB numeric / JSON number into Decimal. PostgREST may hand back
    either ``49.90`` or ``"49.90"`` depending on the column type, so everything
    goes through ``str()`` first — ``Decimal(float)`` would inherit binary
    rounding error on the money path."""
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def q2(value: Any) -> Decimal:
    """Quantize to 2 dp (the ``numeric(10,2)`` shape of every money column)."""
    return _dec(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def to_halalas(amount_sar: Any) -> int:
    """SAR → halalas (the unit Moyasar charges in). Trap 2 in the plan: a missed
    ×100 charges 0.49 SAR instead of 49.00."""
    return int((q2(amount_sar) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def vat_split(charge_sar: Decimal) -> tuple[Decimal, Decimal]:
    """VAT-INCLUSIVE 15% split of the **charged** amount → ``(net, vat)``.

    Stamped once at initiation and stored (plan trap 8): displays read the
    stored numbers forever, so a future rate change cannot rewrite old rows.
    49.90 → (43.39, 6.51) · 89.90 → (78.17, 11.73) · 189.90 → (165.13, 24.77).
    """
    charge = q2(charge_sar)
    net = q2(charge / (Decimal("1") + VAT_RATE))
    return net, q2(charge - net)


def _money(value: Any) -> Optional[str]:
    """Money for the wire: a fixed 2-dp STRING (``"49.90"``), or None.

    Strings, not floats, so the frontend never renders 49.900000000000006 and so
    the value round-trips into ``numeric(10,2)`` unchanged.
    """
    if value is None:
        return None
    return f"{q2(value):.2f}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse a PostgREST timestamptz into an aware datetime (UTC-assumed)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_uuid(value: Any) -> bool:
    try:
        uuid_module.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# ═══════════════════════════════════════════════════════════════════════════
# 3. Moyasar HTTP client
# ═══════════════════════════════════════════════════════════════════════════


async def _moyasar_request(
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    authed: bool = True,
    retries: int = 0,
) -> dict:
    """One call to ``https://api.moyasar.com/v1``.

    HTTP Basic with the secret key as the username and a BLANK password (their
    scheme). ``retries`` is only ever non-zero for GETs.
    """
    settings = get_settings()
    secret = (settings.MOYASAR_SECRET_KEY or "").strip()
    if authed and not secret:
        raise MoyasarUnconfigured("MOYASAR_SECRET_KEY is not set")

    url = f"{MOYASAR_API_BASE}{path}"
    auth = (secret, "") if authed else None

    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
                response = await client.request(
                    method, url, params=params, json=json_body, auth=auth
                )
        except httpx.HTTPError as exc:
            if attempt < retries:
                await asyncio.sleep(0.4 * (attempt + 1))
                continue
            raise MoyasarUnavailable(f"{method} {path} transport error: {exc}") from exc

        if response.status_code >= 500:
            if attempt < retries:
                await asyncio.sleep(0.4 * (attempt + 1))
                continue
            raise MoyasarUnavailable(
                f"{method} {path} -> {response.status_code}",
                status=response.status_code,
                payload=response.text[:500],
            )

        if response.status_code == 404:
            raise MoyasarNotFound(f"{method} {path} -> 404", status=404)

        if response.status_code >= 400:
            # 401 here means OUR key is wrong — log it as a config problem, not
            # as the user's.
            if response.status_code in (401, 403):
                logger.error("Moyasar rejected our credentials on %s %s", method, path)
            raise MoyasarError(
                f"{method} {path} -> {response.status_code}",
                status=response.status_code,
                payload=response.text[:500],
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise MoyasarError(f"{method} {path} returned non-JSON") from exc
        return body if isinstance(body, dict) else {"data": body}

    raise MoyasarUnavailable(f"{method} {path} exhausted retries")  # pragma: no cover


async def fetch_payment(moyasar_id: str) -> dict:
    """``GET /v1/payments/{id}`` — the ONLY evidence either path trusts.

    The id is validated as a UUID first: it arrives from a query string
    (``/pay/callback?id=…``) and from a webhook body, and neither may be
    concatenated into a URL path unchecked.
    """
    if not _is_uuid(moyasar_id):
        raise MoyasarNotFound("malformed moyasar id")
    return await _moyasar_request(
        "GET", f"/payments/{quote(str(moyasar_id), safe='')}", retries=GET_RETRIES
    )


async def refund_at_provider(provider_ref: str, amount_halalas: int) -> dict:
    """``POST /v1/payments/{id}/refund`` with an explicit PARTIAL amount.

    Plan trap 11: calling this without ``amount`` refunds in full and silently
    gives away the 2 SAR processing fee. Never retried — a duplicated refund
    POST is worse than a failed one.
    """
    if not _is_uuid(provider_ref):
        raise MoyasarNotFound("malformed provider_ref")
    return await _moyasar_request(
        "POST",
        f"/payments/{quote(str(provider_ref), safe='')}/refund",
        json_body={"amount": int(amount_halalas)},
        retries=0,
    )


async def charge_saved_card(
    *,
    token: str,
    amount_halalas: int,
    description: str,
    payment_id: str,
    metadata: Optional[dict] = None,
) -> dict:
    """``POST /v1/payments`` against a STORED TOKEN — a merchant-initiated charge.

    The body below matches Moyasar's published contract verbatim
    (docs.moyasar.com/guides/tokenization/tokenized-cards), and tokenization was
    enabled on the live account 2026-08-12. Two consequences worth knowing:

    * **3DS is NOT triggered on a token charge** — per the docs, "the payment
      method was already verified when the token was created or saved". So the
      cardholder-absent problem the plan worried about does not arise for an
      ``active`` token. (A ``save_only`` token always challenges; ours are
      created by a real payment, so they are not save_only. ``"3ds": true`` can
      be added inside ``source`` to force a challenge — we never want that here.)
    * **ONLY an ``active`` token can be charged.** ``initiated`` / ``inactive``
      are rejected by the provider. Nothing reads token status yet — see the note
      in ``payment_method_service.extract_card_token``.

    ⚠ Still not exercised against this account. **Do not flip
    ``SUBSCRIPTION_AUTO_RENEWAL_ENABLED`` until one real charge has been made and
    the response's ``status`` checked.**

    Two hard rules, both inherited from ``refund_at_provider``:

    * **NEVER RETRIED.** ``retries=0``. This POST is not idempotent, and a
      duplicated renewal charge is materially worse than a missed one. A
      transport failure therefore leaves an AMBIGUOUS outcome, and
      ``renewal_service`` treats ambiguity as fail-closed (the row stays
      ``initiated`` and blocks every later attempt for that period until a human
      or the webhook resolves it).
    * **The amount comes from the caller, which read it from ``plans``.** No
      client is anywhere near this call.

    ``metadata.payment_id`` is what lets both ``/verify`` and the webhook find
    our row again (``_locate_transaction``) — it is the same binding a browser
    purchase carries, and it is what makes a late ``payment_paid`` webhook able
    to finish a renewal whose HTTP response we lost.
    """
    body: dict[str, Any] = {
        "amount": int(amount_halalas),
        "currency": CURRENCY,
        "description": description,
        "source": {"type": "token", "token": str(token)},
        "metadata": {"payment_id": str(payment_id), **(metadata or {})},
    }
    return await _moyasar_request("POST", "/payments", json_body=body, retries=0)


def event_mode_matches(live_flag: Any) -> bool:
    """Does an event's ``live`` flag agree with our configured key mode?

    The plan's ⚠ trap: if one endpoint URL is registered for both dashboard
    modes, a SANDBOX payment could grant a real subscription. Unknown/absent
    flag → ``True`` (nothing to disagree with; the re-fetch is still the real
    isolation, since a test id 404s on a live key).
    """
    if not isinstance(live_flag, bool):
        return True
    mode = moyasar_mode()
    if mode is None:
        return True
    return (mode == "live") == live_flag


# ═══════════════════════════════════════════════════════════════════════════
# 4. DB access — sync helpers, always run through run_db()
#
# Every write goes through the SERVICE-ROLE client (RLS allows the user only a
# self SELECT) with an explicit user_id filter in the query, exactly like every
# other route in this backend. The filter is the authorization, not RLS.
# ═══════════════════════════════════════════════════════════════════════════

_TXN_COLUMNS = (
    "payment_id, user_id, plan_id, amount_sar, currency, status, provider, "
    "provider_ref, paid_at, fulfilled_at, created_at, updated_at, "
    "vat_amount_sar, net_amount_sar, upgrade_credit_sar, refund_fee_sar, "
    # raw_payload is REQUIRED, not optional: _refund_fee_halalas reads the
    # provider's own `fee` out of it. Omitting it silently made every refund
    # fall back to the flat figure (caught on prod 2026-08-05 — a refund
    # charged 3.40 where the payload said 3.38). It never reaches the client:
    # transaction_summary whitelists its output fields.
    "raw_payload, "
    # revoked_at is REQUIRED too: _is_superseded has to tell an upgrade that is
    # still standing from one whose refund already unwound it, and a row that
    # reads as standing when it isn't would block a refund the user is owed.
    "revoked_at, "
    "refunded_amount_sar"
)


def _fetch_plans(supabase: SupabaseClient, plan_ids: list[str]) -> dict[str, dict]:
    ids = [p for p in plan_ids if p]
    if not ids:
        return {}
    res = (
        supabase.table("plans")
        .select("plan_id, name_ar, price_sar, duration_days, billing_cycle")
        .in_("plan_id", ids)
        .execute()
    )
    return {row["plan_id"]: row for row in (res.data or [])}


def _fetch_subscription(supabase: SupabaseClient, user_id: str) -> Optional[dict]:
    # limit(1) rather than maybe_single(): PostgREST answers 406 for a
    # single-object request that matches no row, and postgrest-py surfaces that
    # as an exception in some versions. A user with no subscription row must
    # read as "no plan", not as a 500 on the checkout page.
    res = (
        supabase.table("user_subscriptions")
        # NO `status` here: migration 091 DROPPED user_subscriptions.status —
        # it exists only on the user_subscriptions_live VIEW, derived at read
        # time. This code derives `active` from expires_at itself (below) and
        # never needed it; selecting it 42703s the whole checkout.
        .select("plan_id, source, started_at, expires_at")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def _fetch_customer_identity(supabase: SupabaseClient, user_id: str) -> dict:
    """Name + email to stamp onto the payment row at initiation (migration 117).

    Read here rather than resolved at display time because the snapshot must be
    what was true when the money moved: `payment_transactions.user_id` is ON
    DELETE SET NULL, so once a deleted account is purged this row is the only
    thing left that can say whose payment it was. Same stamp-once discipline as
    the VAT split — a later profile edit must not rewrite a settled record.

    limit(1) rather than maybe_single(), for the reason spelled out in
    _fetch_subscription. Returns {} when the row is somehow missing: an
    unidentified receipt is bad, a checkout that 500s on a caller who is already
    authenticated is worse.
    """
    res = (
        supabase.table("users")
        .select("full_name_ar, email")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else {}


def _insert_transaction(supabase: SupabaseClient, payload: dict) -> dict:
    res = supabase.table("payment_transactions").insert(payload).execute()
    rows = getattr(res, "data", None) or []
    if not rows:
        raise RuntimeError("payment_transactions insert returned no row")
    return rows[0]


def _is_unique_violation(exc: Exception) -> bool:
    """Did this write lose a race on 119's one-open-credited-checkout index?

    postgrest-py raises a generic APIError, so the SQLSTATE is only available as
    text. Matched on both the code and the message because PostgREST's error
    shape has changed between versions and a missed match would turn a
    retryable race into a 503 the user cannot get past.
    """
    text = str(exc).lower()
    return "23505" in text or "duplicate key" in text or "already exists" in text


def _is_undefined_column(exc: Exception) -> bool:
    """Is this "migration 132 is not applied yet" rather than a real failure?

    Only used by ``_expire_open_checkouts``, whose new ``initiated_by`` filter is
    on the LIVE checkout path: a backend deployed ahead of 132 must degrade to
    its pre-132 behaviour, not 503 every purchase. (That degradation is exactly
    correct pre-132, because without the column there are no renewal rows to
    protect.)
    """
    text = str(exc).lower()
    return "42703" in text or "pgrst204" in text or "does not exist" in text


# ``payment_transactions.initiated_by`` (migration 132) — 'user' for a browser
# purchase, 'renewal' for a job-created charge. Named here because BOTH the
# sweep below and renewal_service key on it.
INITIATED_BY_USER = "user"
INITIATED_BY_RENEWAL = "renewal"


def _expire_open_checkouts(supabase: SupabaseClient, user_id: str) -> int:
    """Supersede every open ``initiated`` row of this user (H-4, layer 2).

    Called immediately before a new checkout inserts. One open quote per user is
    the whole point: a discounted row that stays payable while a newer one is
    created is exactly the stockpile primitive — each was priced against the
    same untouched subscription and each would apply the full credit.

    Superseding is SAFE for a payment already in flight: ``_mark_paid_and_grant``
    gates only on ``refunded``, so if money does land on an expired row it is
    still marked paid and still fulfils (subject to the re-derivation below).
    The status is bookkeeping about the QUOTE, never about the money.

    It also costs no receipt number, which matters: 114's trigger is
    ``BEFORE UPDATE OF status`` and this writes ``status``. Its body was read
    live before this was written — it assigns only when ``NEW.status = 'paid'``,
    so the sequential series 117 must keep hole-free is untouched. Anything that
    widens that trigger has to revisit this call.

    ⚠ **RENEWAL ROWS ARE EXCLUDED** (auto-renewal plan §7). A renewal charge
    opens an ``initiated`` row too, and it is emphatically NOT a competing quote
    — nobody is looking at a page, there is no credit to stockpile, and the row
    is mid-flight against Moyasar. Without the ``initiated_by = 'user'`` filter,
    a subscriber who happens to open ``/pay`` during their renewal window
    silently supersedes their own renewal row; the charge then lands on an
    ``expired`` row and the DB-level idempotency key is spent, so the next tick
    skips them and the subscription lapses despite a working card. The user
    would have caused it and could never explain it.

    The filter degrades safely if this backend is somehow deployed ahead of 132:
    an undefined column falls back to the pre-132 unfiltered sweep, which is
    correct then — no ``initiated_by`` column means no renewal rows exist.
    """
    patch = {"status": STATUS_EXPIRED, "updated_at": _now_iso()}
    try:
        res = (
            supabase.table("payment_transactions")
            .update(patch)
            .eq("user_id", user_id)
            .eq("status", "initiated")
            .eq("initiated_by", INITIATED_BY_USER)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        if not _is_undefined_column(exc):
            raise
        logger.warning(
            "supersede: payment_transactions.initiated_by is missing (migration "
            "132 unapplied) — falling back to the pre-132 unfiltered sweep. That "
            "is safe only because no renewal rows can exist without the column."
        )
        res = (
            supabase.table("payment_transactions")
            .update(patch)
            .eq("user_id", user_id)
            .eq("status", "initiated")
            .execute()
        )
    return len(getattr(res, "data", None) or [])


def _get_transaction(supabase: SupabaseClient, payment_id: str) -> Optional[dict]:
    res = (
        supabase.table("payment_transactions")
        .select(_TXN_COLUMNS)
        .eq("payment_id", payment_id)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def _get_transaction_by_ref(supabase: SupabaseClient, provider_ref: str) -> Optional[dict]:
    res = (
        supabase.table("payment_transactions")
        .select(_TXN_COLUMNS)
        .eq("provider", PROVIDER)
        .eq("provider_ref", provider_ref)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def _locate_transaction(supabase: SupabaseClient, fetched: dict) -> Optional[dict]:
    """Our row for a fetched Moyasar payment.

    ``metadata.payment_id`` first (what the form was told to carry), falling
    back to ``UNIQUE(provider, provider_ref)`` for a payment we already stamped.
    Both are our own identifiers — the caller-supplied ``?id=`` never selects a
    row on its own.
    """
    metadata = fetched.get("metadata")
    if isinstance(metadata, dict):
        candidate = metadata.get("payment_id")
        if candidate and _is_uuid(candidate):
            row = _get_transaction(supabase, str(candidate))
            if row:
                return row
    ref = fetched.get("id")
    if ref and _is_uuid(ref):
        return _get_transaction_by_ref(supabase, str(ref))
    return None


def _update_transaction(supabase: SupabaseClient, payment_id: str, patch: dict) -> Optional[dict]:
    patch = {**patch, "updated_at": _now_iso()}
    res = (
        supabase.table("payment_transactions")
        .update(patch)
        .eq("payment_id", payment_id)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def _grant_plan(supabase: SupabaseClient, user_id: str, plan_id: str, payment_id: str) -> Optional[dict]:
    """``grant_plan(user, plan, 'payment', payment_id)`` — the ONE grant path.

    Idempotent by construction: the RPC returns early when ``fulfilled_at`` is
    already stamped, so the second confirmation path is a no-op.
    """
    res = supabase.rpc(
        "grant_plan",
        {
            "p_user_id": user_id,
            "p_plan_id": plan_id,
            "p_source": "payment",
            "p_payment_id": payment_id,
        },
    ).execute()
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def _stamp_prior_snapshot(supabase: SupabaseClient, payment_id: str) -> Optional[str]:
    """``stamp_payment_prior_snapshot(payment_id)`` (migration 113).

    Records the subscription this payment is about to replace
    (``prior_plan_id`` / ``prior_expires_at``) so an upgrade refund can RESTORE
    the old plan instead of leaving the user with nothing. **Must run before
    ``grant_plan``** — grant_plan overwrites the very row being snapshotted.

    Called unconditionally on every paid path: the RPC self-guards (no-op once
    ``fulfilled_at`` is set or the snapshot already exists, NULL prior for
    same-plan / locked / no-subscription cases), so there is no condition for
    this module to get wrong.

    Never raises into the caller. A failed snapshot costs the ability to restore
    on refund; failing the GRANT over it would cost the user the plan they just
    paid for. Logged at ERROR so it is visible.
    """
    try:
        res = supabase.rpc(
            "stamp_payment_prior_snapshot", {"p_payment_id": payment_id}
        ).execute()
        data = getattr(res, "data", None)
        if isinstance(data, list):
            data = data[0] if data else None
        if isinstance(data, dict):
            return str(data.get("prior_plan_id") or "") or None
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "stamp_payment_prior_snapshot failed for payment=%s (grant proceeds; "
            "an upgrade refund will revoke instead of restore): %s",
            payment_id, exc,
        )
        return None


def _stamp_usage_reset(supabase: SupabaseClient, payment_id: str) -> None:
    """``stamp_usage_reset(payment_id)`` (migration 131).

    Zeroes the points already spent this cycle when a payment moved the user UP
    the ladder, by stamping ``user_subscriptions.usage_reset_at`` — the windows
    then sum only calls made after it. The clocks are untouched: the session
    still expires at its original boundary, the user just walks in with 0 spent.

    **Runs AFTER ``grant_plan``**, deliberately: a reset that fails must never
    cost the customer the plan they paid for. The reverse order would put a
    convenience feature in front of the money path.

    The RPC decides FOR ITSELF whether this was a rank increase (it compares
    ``plans.price_sar`` of the new and prior plans, and stamps ``paid_at`` rather
    than ``now()`` so a webhook + client-confirm double-run writes the identical
    value). Python deliberately does NOT re-check ``PLAN_RANK`` here — two copies
    of that decision would be one too many, and the SQL side is the one holding
    the row.

    Never raises into the caller, and tolerates the RPC not existing yet (131 is
    unapplied): the worst case is a user who keeps their pre-upgrade usage until
    the window rolls off on its own, which is simply today's behaviour.
    """
    try:
        supabase.rpc("stamp_usage_reset", {"p_payment_id": payment_id}).execute()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "stamp_usage_reset failed for payment=%s (plan IS granted; the user "
            "keeps their pre-upgrade usage until the window rolls off): %s",
            payment_id, exc,
        )


# Every `action` revoke_plan_grant can return where the term WAS dealt with.
# Anything else (payment_not_found, or an exception) means it was not.
REVOKE_ACTIONS_OK = frozenset(
    {
        "already_revoked",   # webhook retry after the self-serve refund
        "not_fulfilled",     # paid but never granted — nothing to take back
        "no_subscription",
        "restored",          # prorated upgrade rolled back to the prior plan
        "subtracted",
        "no_expiry",
    }
)

# `plan_switched` used to sit in the set above and log at INFO. It is not an
# error — the RPC is right to leave a plan the user still pays for alone — but
# it is not a success either: money went back and the entitlement STANDS. That
# silence is half of M-1. Self-serve refunds can no longer reach it (the LIFO
# guard in refund_payment refuses first), so anything landing here now is a
# dashboard-side or provider-side refund that needs a human.
REVOKE_ACTIONS_ATTENTION = frozenset({"plan_switched"})


def _revoke_plan_grant(supabase: SupabaseClient, payment_id: str) -> str:
    """``revoke_plan_grant(payment_id)`` (migration 113) — the mirror of grant_plan.

    Returns the RPC's ``action`` (see ``REVOKE_ACTIONS_OK``), or ``'error'``.
    The row MUST already be ``status='refunded'`` — the RPC raises
    ``payment_not_refunded`` otherwise — so every caller stamps the status
    first. Best-effort: the money is already back with the customer, so a
    failure here is an ops incident to log loudly, not a reason to fail the
    user's request.
    """
    try:
        res = supabase.rpc("revoke_plan_grant", {"p_payment_id": payment_id}).execute()
        data = getattr(res, "data", None)
        if isinstance(data, list):
            data = data[0] if data else None
        action = str((data or {}).get("action") or "unknown") if isinstance(data, dict) else "unknown"
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "revoke_plan_grant failed for payment=%s (refund stands, plan NOT "
            "revoked — manual fix required): %s",
            payment_id, exc,
        )
        return "error"

    if action in REVOKE_ACTIONS_ATTENTION:
        logger.error(
            "revoke_plan_grant: payment=%s action=%s — MONEY RETURNED AND THE "
            "ENTITLEMENT STANDS. The refunded payment's plan was superseded by a "
            "later purchase, so the RPC correctly left the current plan alone; the "
            "credit that purchase consumed is NOT clawed back automatically. "
            "Reconcile by hand (M-1).",
            payment_id, action,
        )
    elif action not in REVOKE_ACTIONS_OK:
        logger.error(
            "revoke_plan_grant returned action=%s for payment=%s — term not revoked",
            action, payment_id,
        )
    else:
        logger.info("revoke_plan_grant: payment=%s action=%s", payment_id, action)
    return action


def _list_transactions(supabase: SupabaseClient, user_id: str) -> list[dict]:
    res = (
        supabase.table("payment_transactions")
        .select(_TXN_COLUMNS)
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(HISTORY_LIMIT)
        .execute()
    )
    return getattr(res, "data", None) or []


def _is_superseded(row: dict, siblings: list[dict]) -> bool:
    """Has a LATER purchase already spent this payment's value as a credit? (M-1)

    ``revoke_plan_grant`` deliberately no-ops (``plan_switched``) when the
    subscription no longer holds the refunded payment's plan — subtracting days
    there would eat a plan the user still pays for. Correct in isolation, and
    the whole exploit: buy basic → upgrade pro (credit 49.90) → upgrade max
    (credit 89.90) = 189.90 paid, then refund the basic and the pro. Both
    no-op, 85.90 comes back, and a 189.90 `max` term stands for 104.00.

    The credit only ever came from a term an EARLIER payment funded, so the
    ladder is a stack and it has to unwind from the top. This predicate names
    the rung that is not on top yet: some later payment of the caller's is
    still standing (paid, granted, not revoked) and carries a credit.

    Pure and sibling-driven so /history and /refund cannot disagree about which
    button they show and which one they honour.
    """
    anchor = _parse_ts(row.get("created_at"))
    if anchor is None:
        return False
    for other in siblings:
        if other.get("payment_id") == row.get("payment_id"):
            continue
        if other.get("status") != "paid":
            continue                                   # refunded/failed: spent nothing
        if _dec(other.get("upgrade_credit_sar")) <= 0:
            continue                                   # full price — consumed no credit
        if not other.get("fulfilled_at") or other.get("revoked_at"):
            continue                                   # never granted, or already unwound
        created = _parse_ts(other.get("created_at"))
        if created is not None and created > anchor:
            return True
    return False


def _find_superseding_payment(
    supabase: SupabaseClient, user_id: str, row: dict
) -> Optional[dict]:
    """The caller's later credited payment that consumed ``row``, if any (M-1).

    Filtered in Python off the same 50-row window /history reads rather than as
    a PostgREST numeric predicate — one query, one predicate, no chance of the
    refund guard and the ``refundable`` flag drifting apart.
    """
    siblings = _list_transactions(supabase, user_id)
    if not _is_superseded(row, siblings):
        return None
    anchor = _parse_ts(row.get("created_at"))
    for other in siblings:                             # newest first
        if other.get("payment_id") == row.get("payment_id"):
            continue
        if (
            other.get("status") == "paid"
            and _dec(other.get("upgrade_credit_sar")) > 0
            and other.get("fulfilled_at")
            and not other.get("revoked_at")
        ):
            created = _parse_ts(other.get("created_at"))
            if anchor is not None and created is not None and created > anchor:
                return other
    return None


# ═══════════════════════════════════════════════════════════════════════════
# 5. Serialization
# ═══════════════════════════════════════════════════════════════════════════


def transaction_summary(
    row: dict, plan_name_ar: Optional[str] = None, *, superseded: bool = False
) -> dict:
    """The receipt shape returned by /history and /{id}/refund.

    ``refundable`` is computed here so the Settings receipts list has one
    boolean to render a button from, instead of re-deriving the 24h rule in the
    browser (where the clock is the user's).

    ``superseded`` (M-1) is passed in rather than derived, because it is the one
    input this row cannot see: it depends on the caller's OTHER payments. It
    only ever turns ``refundable`` off — /history and /refund must agree, and a
    button that always 409s is worse than no button.
    """
    paid_at = _parse_ts(row.get("paid_at"))
    deadline = paid_at + REFUND_WINDOW if paid_at else None
    refundable = bool(
        row.get("status") == "paid"
        and deadline
        and _now() <= deadline
        and row.get("provider_ref")
        and not superseded
    )
    return {
        "payment_id": row.get("payment_id"),
        "plan_id": row.get("plan_id"),
        "plan_name_ar": plan_name_ar,
        "status": row.get("status"),
        "currency": row.get("currency") or CURRENCY,
        "amount_sar": _money(row.get("amount_sar")),
        "amount_halalas": to_halalas(row.get("amount_sar")),
        "vat_amount_sar": _money(row.get("vat_amount_sar")),
        "net_amount_sar": _money(row.get("net_amount_sar")),
        "upgrade_credit_sar": _money(row.get("upgrade_credit_sar") or 0),
        "refund_fee_sar": _money(row.get("refund_fee_sar")),
        "refunded_amount_sar": _money(row.get("refunded_amount_sar")),
        "provider": row.get("provider"),
        "created_at": row.get("created_at"),
        "paid_at": row.get("paid_at"),
        "fulfilled_at": row.get("fulfilled_at"),
        "updated_at": row.get("updated_at"),
        "refundable": refundable,
        # Why the button is missing on a row that is otherwise inside its
        # window: a later upgrade already spent this payment's value, so the
        # stack has to unwind from the top first (M-1).
        "superseded": superseded,
        "refund_deadline": deadline.isoformat() if deadline else None,
        # What a refund WOULD cost and return, quoted per-payment so the
        # confirm dialog shows the true numbers instead of a guessed flat fee
        # (the deduction is provider-fee-dependent — see _refund_fee_halalas).
        # Only meaningful while `refundable`; null afterwards so the UI cannot
        # display a stale quote next to an already-refunded row.
        "refund_quote_fee_sar": (
            _money(Decimal(_refund_fee_halalas(row)) / Decimal(100)) if refundable else None
        ),
        "refund_quote_amount_sar": (
            _money(
                (Decimal(to_halalas(row.get("amount_sar")) - _refund_fee_halalas(row)))
                / Decimal(100)
            )
            if refundable
            else None
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 6. Checkout
# ═══════════════════════════════════════════════════════════════════════════


def _plan_rank(plan_id: Optional[str]) -> Optional[int]:
    return PLAN_RANK.get(plan_id or "")


def _upgrade_credit(
    *,
    new_plan_id: str,
    new_price: Decimal,
    subscription: Optional[dict],
    plans: dict[str, dict],
    at: datetime,
) -> Decimal:
    """Prorated credit owed for replacing ``subscription`` with ``new_plan_id``.

    ONE implementation, deliberately, because it is called TWICE with different
    clocks: once by ``create_checkout`` to price the row, and once by
    ``_revalidate_credited_charge`` to re-derive that same price from live state
    before the plan is granted. Two copies of this arithmetic would drift, and
    the drift would be a discount.

    Credit is owed ONLY for a still-running plan the user actually PAID for. A
    code/marketing/manual grant earns nothing — otherwise a promo code becomes a
    cash discount. A same-plan re-purchase earns nothing either: ``grant_plan``
    stacks the days, so the user keeps that value rather than being refunded it.

    ``at`` is the moment the credit is measured from. Passing the quote's
    ``created_at`` at fulfilment is what keeps ordinary time decay between
    checkout and payment from reading as a state change.
    """
    current_plan_id = (subscription or {}).get("plan_id")
    expires_at = _parse_ts((subscription or {}).get("expires_at"))
    if not (
        subscription
        and current_plan_id
        and (subscription or {}).get("source") == "payment"
        and current_plan_id != new_plan_id
        and expires_at is not None
        and expires_at > at
    ):
        return Decimal("0.00")

    old_plan = plans.get(current_plan_id) or {}
    duration_days = old_plan.get("duration_days")
    old_price = old_plan.get("price_sar")
    if not duration_days or old_price is None:
        return Decimal("0.00")

    remaining_days = Decimal(str((expires_at - at).total_seconds())) / Decimal(86400)
    # CLAMP TO ONE PERIOD (H-4). Same-plan purchases STACK — grant_plan adds
    # duration_days onto a live expiry — so remaining_days is unbounded: pro
    # bought 3× left 90 days against a 30-day period, a ratio of 3, a credit of
    # 269.70 for a plan that costs 89.90, and a 1.00 SAR `max`. A term is only
    # ever worth what one period of it costs.
    ratio = min(remaining_days / Decimal(str(duration_days)), Decimal("1"))
    credit = q2(ratio * _dec(old_price))

    # Never negative, and never so large that the charge falls under Moyasar's
    # 1.00 SAR minimum. With the ratio clamped and downgrades blocked the
    # ceiling cannot bind on today's catalog (max credit 89.90 < 189.90) — but
    # it BOUND before the clamp existed (that is the 1.00 SAR `max` above), so
    # it stays as the second wall rather than as a comment claiming it is idle.
    ceiling = new_price - MIN_CHARGE_SAR
    return max(Decimal("0.00"), min(credit, ceiling if ceiling > 0 else Decimal("0.00")))


async def create_checkout(supabase: SupabaseClient, user_id: str, plan_id: str) -> dict:
    """Price a purchase and open an ``initiated`` ledger row.

    No Moyasar call happens here — the browser's form creates the payment. What
    this returns is exactly what the form needs, plus the credit line the page
    shows the user.
    """
    _secret, publishable = _require_keys()
    settings = get_settings()

    subscription = await run_db(_fetch_subscription, supabase, user_id)
    current_plan_id = (subscription or {}).get("plan_id")
    plans = await run_db(_fetch_plans, supabase, [plan_id, current_plan_id])
    customer = await run_db(_fetch_customer_identity, supabase, user_id)

    plan = plans.get(plan_id)
    if plan is None or plan.get("price_sar") is None:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.PAYMENT_PLAN_NOT_PURCHASABLE,
            detail=PLAN_NOT_PURCHASABLE_AR,
        )

    price = q2(plan["price_sar"])
    expires_at = _parse_ts((subscription or {}).get("expires_at"))
    # NULL expires_at = non-expiring grant (dev/manual) — active, not expired.
    active = bool(subscription and current_plan_id and (expires_at is None or expires_at > _now()))

    # ── downgrade guard (mirrors redeem_plan_code's "plan already active") ──
    new_rank, current_rank = _plan_rank(plan_id), _plan_rank(current_plan_id)
    if active and new_rank is not None and current_rank is not None and new_rank < current_rank:
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.PAYMENT_DOWNGRADE_BLOCKED,
            detail=DOWNGRADE_BLOCKED_AR,
        )

    # ── upgrade proration ──────────────────────────────────────────────────
    # The whole rule lives in _upgrade_credit, because fulfilment re-runs it.
    credit = _upgrade_credit(
        new_plan_id=plan_id,
        new_price=price,
        subscription=subscription,
        plans=plans,
        at=_now(),
    )

    charge = q2(price - credit)
    net, vat = vat_split(charge)
    amount_halalas = to_halalas(charge)

    plan_name = plan.get("name_ar") or plan_id
    description = f"ريحان — {plan_name}" + (" (ترقية)" if credit > 0 else "")

    # vat/net/upgrade_credit are stamped HERE, once (migration 113 columns).
    # prior_plan_id / prior_expires_at are NOT: the stamp_payment_prior_snapshot
    # RPC writes them at fulfilment, because the snapshot must reflect the
    # subscription at the moment the grant replaces it — which can be many
    # minutes after checkout, or never (abandoned payment).
    #
    # customer_name/email_snapshot are stamped here too (migration 117), and for
    # the opposite reason to the prior-plan snapshot: they must be captured at
    # the START because the users row they come from can legitimately disappear.
    # user_id is ON DELETE SET NULL, so a purged account leaves this row standing
    # with its money intact — these two columns are then all that identifies it.
    payload = {
        "user_id": user_id,
        "plan_id": plan_id,
        "amount_sar": _money(charge),
        "currency": CURRENCY,
        "status": "initiated",
        "provider": PROVIDER,
        "vat_amount_sar": _money(vat),
        "net_amount_sar": _money(net),
        "upgrade_credit_sar": _money(credit),
        "customer_name_snapshot": customer.get("full_name_ar"),
        "customer_email_snapshot": customer.get("email"),
    }

    # SUPERSEDE THEN INSERT (H-4, layer 2), and in that order: the caller may
    # hold only one open quote, so the previous one stops being payable at the
    # instant a new price is quoted. Everything above this line can still refuse
    # the checkout (unknown plan, downgrade) and must not expire anything on its
    # way out — hence this sits here and not at the top of the function.
    #
    # The retry is for the concurrent case ONLY: two simultaneous checkouts both
    # find nothing to supersede, both insert, and 119's partial unique index
    # rejects the loser. Re-running supersede+insert makes that loser the newest
    # quote instead of a 503 the user cannot get past. One retry, never a loop.
    row = None
    for attempt in (0, 1):
        await run_db(_expire_open_checkouts, supabase, user_id)
        try:
            row = await run_db(_insert_transaction, supabase, payload)
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == 0 and _is_unique_violation(exc):
                logger.warning(
                    "checkout for user=%s plan=%s lost the open-quote race — retrying",
                    user_id, plan_id,
                )
                continue
            # Includes "column does not exist" if this backend is ever deployed
            # ahead of migration 113, 117 or 119. A dependency failure is a 503,
            # not a user error — and the page must not mount a form for a row
            # that isn't there.
            logger.exception(
                "checkout insert failed for user=%s plan=%s: %s", user_id, plan_id, exc
            )
            raise LunaHTTPException(
                status_code=503,
                code=ErrorCode.SERVICE_UNAVAILABLE,
                detail=MSG_SERVICE_UNAVAILABLE,
                headers={"Retry-After": "5"},
            )
    payment_id = row["payment_id"]

    await run_db(
        write_audit_log,
        supabase,
        user_id=user_id,
        action="create",
        resource_type="payment_transaction",
        resource_id=payment_id,
        metadata={
            "event": "checkout_initiated",
            "plan_id": plan_id,
            "amount_sar": _money(charge),
            "upgrade_credit_sar": _money(credit),
            "from_plan_id": current_plan_id,
            "provider": PROVIDER,
        },
    )

    logger.info(
        "checkout initiated: user=%s plan=%s charge=%s credit=%s payment=%s",
        user_id, plan_id, charge, credit, payment_id,
    )

    return {
        "payment_id": payment_id,
        "plan_id": plan_id,
        "plan_name_ar": plan_name,
        "amount_halalas": amount_halalas,
        "amount_sar": _money(charge),
        "credit_sar": _money(credit),
        "vat_amount_sar": _money(vat),
        "currency": CURRENCY,
        "description": description,
        "publishable_key": publishable,
        "callback_url": f"{settings.PUBLIC_WEB_URL}/pay/callback",
        "applepay_enabled": settings.MOYASAR_APPLEPAY_ENABLED,
        # ── recurring consent (auto-renewal plan §6 + §9) ──────────────────
        # The SERVER owns the disclosure text: the page renders this string
        # verbatim next to the checkbox, and POST /payments/{id}/consent hashes
        # the very same string, rebuilt here from `plans`. The browser never
        # supplies it, so a client cannot claim it was shown different words.
        #
        # Both fields are inert (false / null) whenever
        # SUBSCRIPTION_AUTO_RENEWAL_ENABLED is off, and always for `basic` —
        # which does not renew, so tokenizing its card would collect a
        # credential with no purpose.
        "requires_recurring_consent": payment_method_service.requires_recurring_consent(plan),
        "recurring_disclosure_ar": (
            payment_method_service.recurring_disclosure_ar(plan)
            if payment_method_service.requires_recurring_consent(plan)
            else None
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 6b. Recurring consent (auto-renewal plan §6) — stamped BEFORE the money moves
# ═══════════════════════════════════════════════════════════════════════════


async def record_recurring_consent(
    supabase: SupabaseClient, user_id: str, payment_id: str, *, accepted: bool
) -> dict:
    """Record the caller's consent to auto-renewal for ONE open checkout row.

    Called from ``POST /payments/{payment_id}/consent`` after the user ticks the
    disclosure checkbox and BEFORE the Moyasar form is mounted. The artefact
    (hash of the SERVER's disclosure text + the timestamp) is written by
    ``payment_method_service``; this function owns only the payment-row guards,
    because it is the module that knows how to bind a payment to its caller.

    Guard order — refuse before writing anything:
      1. the feature flag is off → a clean, non-error "not enabled" answer. The
         page must be able to call this unconditionally and get a shape it can
         branch on rather than an exception to swallow;
      2. ``accepted`` is not literally True → 400. There is no "consent by
         omission" here;
      3. the row is not the caller's → 404 (never an existence oracle);
      4. the row is not still open (``initiated``) → 409. Consent stamped after
         the money moved is not consent, it is paperwork;
      5. the plan does not renew → 409. Nothing to consent to, and storing a
         `basic` card would be a credential with no purpose.

    Idempotent: a page reload re-posts and gets the FIRST artefact back, with
    its original timestamp. Rewriting it would quietly change when the user
    agreed.
    """
    if not payment_method_service.auto_renewal_enabled():
        return {
            "enabled": False,
            "accepted": False,
            "payment_id": payment_id,
            "consent_given_at": None,
        }

    if accepted is not True:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail=payment_method_service.CONSENT_REQUIRED_AR,
        )

    row = await run_db(_get_transaction, supabase, payment_id)
    if row is None or row.get("user_id") != user_id:
        raise LunaHTTPException(
            status_code=404, code=ErrorCode.PAYMENT_NOT_FOUND, detail=PAYMENT_NOT_FOUND_AR
        )

    if row.get("status") != "initiated":
        logger.warning(
            "recurring consent refused: payment=%s is '%s', not an open checkout",
            payment_id, row.get("status"),
        )
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.PAYMENT_CONSENT_INVALID,
            detail=payment_method_service.CONSENT_NOT_OPEN_AR,
        )

    plans = await run_db(_fetch_plans, supabase, [row.get("plan_id")])
    plan = plans.get(str(row.get("plan_id")))
    if not payment_method_service.plan_renews(plan):
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.PAYMENT_CONSENT_INVALID,
            detail=payment_method_service.CONSENT_NOT_APPLICABLE_AR,
        )

    try:
        consent = await payment_method_service.record_consent(
            supabase, user_id=user_id, payment_row=row, plan=plan
        )
    except Exception as exc:  # noqa: BLE001
        # A consent we cannot prove is a consent we do not have. Unlike an audit
        # row, this one refuses the request rather than swallowing the failure.
        logger.exception(
            "recurring consent write failed for payment=%s user=%s: %s",
            payment_id, user_id, exc,
        )
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=payment_method_service.CONSENT_STORE_FAILED_AR,
            headers={"Retry-After": "5"},
        )

    return {
        "enabled": True,
        # `accepted`, echoing the request field, is the name the frontend's
        # PaymentConsentResponse already reads. One name for one fact.
        "accepted": True,
        "payment_id": payment_id,
        "plan_id": row.get("plan_id"),
        "consent_given_at": consent.get("consent_given_at"),
        "disclosure_version": consent.get("disclosure_version"),
        # The exact text that was hashed, echoed back so the page can render the
        # confirmed wording without re-fetching the checkout session.
        "recurring_disclosure_ar": payment_method_service.recurring_disclosure_ar(plan),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 7. The shared confirmation core (used by BOTH /verify and the webhook)
# ═══════════════════════════════════════════════════════════════════════════


def _assert_matches(row: dict, fetched: dict) -> None:
    """The payment Moyasar reports must be the one we priced.

    Amount + currency are checked against OUR row, so a client that tampered
    with the form's amount (or a forged webhook naming a real payment) grants
    nothing. Raises ``MoyasarError`` — callers map it per-path.
    """
    expected_halalas = to_halalas(row.get("amount_sar"))
    actual_halalas = fetched.get("amount")
    actual_currency = (fetched.get("currency") or "").upper()

    if not isinstance(actual_halalas, int) or actual_halalas != expected_halalas:
        raise MoyasarError(
            f"amount mismatch: provider={actual_halalas} expected={expected_halalas}"
        )
    if actual_currency != CURRENCY:
        raise MoyasarError(f"currency mismatch: provider={actual_currency!r}")


def _revalidate_credited_charge(supabase: SupabaseClient, row: dict) -> dict:
    """Is the discount stamped on this row still owed, RIGHT NOW? (H-4, layer 3)

    ``_assert_matches`` proves the provider charged what OUR ROW says. It cannot
    prove the row is right: the row was priced minutes — or, before 119, days —
    ago against a subscription that may since have been replaced, refunded, or
    already credited once. Re-pricing here is the only thing that makes a credit
    *consumed* rather than merely *quoted*.

    The test is ONE inequality, deliberately:

        amount actually paid  >=  catalog price NOW − credit owed NOW

    ``>=`` and not ``==`` because overpaying is the customer's problem to be
    refunded, never a reason to withhold a plan — a catalog price CUT between
    checkout and payment must not hold up a grant. Only underpaying, which is
    what every variant of the stockpile produces, fails.

    Runs sync inside one ``run_db`` because it is two reads that belong to the
    same decision. Returns the numbers as well as the verdict so the audit row
    an operator reads later says what was expected and what arrived.
    """
    credit = _dec(row.get("upgrade_credit_sar"))
    verdict = {
        "ok": True,
        "reason": "no_credit",
        "paid_sar": _money(row.get("amount_sar")),
        "expected_sar": None,
        "credit_owed_sar": None,
    }
    if credit <= 0:
        return verdict                       # full-price purchase: nothing to re-derive

    if row.get("fulfilled_at"):
        # ALREADY GRANTED — the same anchor grant_plan itself uses. The second
        # confirmation path (webhook after /verify, or vice versa) arrives to a
        # subscription this very payment has already rewritten, so re-deriving
        # would compare the credit against its own result and hold a purchase
        # that completed correctly minutes ago.
        return {**verdict, "reason": "already_fulfilled"}

    created_at = _parse_ts(row.get("created_at"))
    if created_at is None or _now() - created_at > CREDIT_QUOTE_TTL:
        # The TTL (H-4, layer 2). A quote this old cannot be honoured on its own
        # word — prod held a payable 100.00 SAR `max` for three days.
        return {**verdict, "ok": False, "reason": "quote_expired"}

    user_id, plan_id = row.get("user_id"), row.get("plan_id")
    if not user_id or not plan_id:
        # A purged buyer (117) cannot have a live subscription to justify a
        # discount against, so there is nothing to re-derive from.
        return {**verdict, "ok": False, "reason": "unbound_row"}

    subscription = _fetch_subscription(supabase, str(user_id))
    current_plan_id = (subscription or {}).get("plan_id")
    plans = _fetch_plans(supabase, [str(plan_id), current_plan_id])
    plan = plans.get(str(plan_id)) or {}
    if plan.get("price_sar") is None:
        return {**verdict, "ok": False, "reason": "plan_not_purchasable"}

    price = q2(plan["price_sar"])
    # Anchored at the quote's own timestamp, NOT at now: between checkout and
    # payment the remaining term shrinks by the minutes the user spent typing a
    # card number, and that decay is not a state change. Anything that IS a
    # state change — plan switched, term refunded, source changed, this very
    # credit already spent by a sibling checkout — still collapses the credit.
    owed = _upgrade_credit(
        new_plan_id=str(plan_id),
        new_price=price,
        subscription=subscription,
        plans=plans,
        at=created_at,
    )
    expected = q2(price - owed)
    paid = q2(row.get("amount_sar"))
    verdict = {
        **verdict,
        "reason": "ok",
        "expected_sar": _money(expected),
        "credit_owed_sar": _money(owed),
    }
    if paid + CREDIT_EPSILON_SAR < expected:
        return {**verdict, "ok": False, "reason": "credit_no_longer_owed"}
    return verdict


async def _hold_for_review(supabase: SupabaseClient, row: dict, verdict: dict) -> dict:
    """Money is in and the discount is not owed: hold the grant for an operator.

    WHY HOLD RATHER THAN GRANT SOMETHING (the decision this function is):

    * Granting the purchased plan anyway is the exploit — that is the branch
      being closed.
    * Granting instead "the plan this amount does cover at catalog price" was
      the tempting alternative and is rejected: it rewrites ``plan_id`` on a
      settled financial record, sends a receipt for a product the customer did
      not choose, and makes ``prior_plan_id``/restore semantics on a later
      refund answer for a purchase that never happened. It also cannot be
      undone by the customer.
    * Holding costs the customer nothing they cannot immediately undo. The row
      stays ``status='paid'`` with ``provider_ref`` and ``paid_at`` set, so it
      is REFUNDABLE from the receipts list for the next 24 hours by one click,
      and ``revoke_plan_grant`` answers ``not_fulfilled`` cleanly on it.

    ``paid`` + ``fulfilled_at IS NULL`` is not a new state either — it is the
    one the schema already models for "money taken, plan never applied", the
    same state a grant crash leaves behind, and 119 indexes it as the operator
    alert surface.

    Never raises and never asks for a webhook retry: retrying re-runs the same
    re-derivation against the same state and reaches the same verdict, so it
    would only burn Moyasar's 5 attempts.
    """
    payment_id = row["payment_id"]

    logger.error(
        "GRANT HELD — upgrade credit no longer owed: payment=%s user=%s plan=%s "
        "paid=%s expected_now=%s credit_stamped=%s credit_owed_now=%s reason=%s. "
        "Money IS in; the row stays paid + unfulfilled for reconciliation and the "
        "customer can self-serve refund it inside 24h.",
        payment_id, row.get("user_id"), row.get("plan_id"), verdict.get("paid_sar"),
        verdict.get("expected_sar"), _money(row.get("upgrade_credit_sar") or 0),
        verdict.get("credit_owed_sar"), verdict.get("reason"),
    )

    await run_db(
        write_audit_log,
        supabase,
        user_id=row.get("user_id"),
        action="update",
        resource_type="payment_transaction",
        resource_id=payment_id,
        metadata={
            "event": "grant_held_credit_stale",
            "plan_id": row.get("plan_id"),
            "amount_sar": _money(row.get("amount_sar")),
            "expected_sar": verdict.get("expected_sar"),
            "upgrade_credit_sar": _money(row.get("upgrade_credit_sar") or 0),
            "credit_owed_sar": verdict.get("credit_owed_sar"),
            "reason": verdict.get("reason"),
            "provider_ref": row.get("provider_ref"),
        },
    )

    # The receipt still goes out: there is a charge on the customer's card, and
    # a charge with no receipt is worse than one whose plan is pending. It says
    # only "we received this amount" — receipt_service claims no entitlement.
    await send_payment_receipt(supabase, row)

    return {
        "status": "paid",
        "payment_id": payment_id,
        "granted": False,
        "plan_id": row.get("plan_id"),
        "expires_at": None,
        "review_reason": verdict.get("reason"),
    }


async def _mark_paid_and_grant(supabase: SupabaseClient, row: dict, fetched: dict) -> dict:
    """Mark the row paid, then grant the plan. The one path both callers share.

    Idempotent end to end: re-running on an already-fulfilled row leaves
    ``paid_at`` alone and ``grant_plan`` returns the current state untouched.
    A row already ``refunded`` is NEVER flipped back to paid — a late
    ``payment_paid`` retry arriving after a refund must not resurrect the grant.
    """
    payment_id = row["payment_id"]

    if row.get("status") == "refunded":
        logger.warning("paid event for an already-refunded payment=%s — ignored", payment_id)
        return {
            "status": "refunded",
            "payment_id": payment_id,
            "granted": False,
            "plan_id": row.get("plan_id"),
            "expires_at": None,
        }

    patch: dict[str, Any] = {
        "status": "paid",
        "provider_ref": fetched.get("id"),
        "raw_payload": fetched,
    }
    if not row.get("paid_at"):
        patch["paid_at"] = _now_iso()

    updated = await run_db(_update_transaction, supabase, payment_id, patch) or {**row, **patch}
    state = {**row, **(updated or {})}

    # The money is recorded BEFORE this check and the check never unrecords it:
    # a discount that stopped being owed is a fulfilment problem, never a reason
    # to pretend the charge did not happen.
    verdict = await run_db(_revalidate_credited_charge, supabase, state)
    if not verdict["ok"]:
        return await _hold_for_review(supabase, state, verdict)

    # ORDER IS LOAD-BEARING: paid → snapshot → grant → usage reset. grant_plan
    # overwrites the subscription row, so the "what were they on before" snapshot
    # has to be taken while it still exists (migration 113 owns that write; we
    # never set prior_plan_id/prior_expires_at from Python).
    prior_plan_id = await run_db(_stamp_prior_snapshot, supabase, payment_id)

    granted = await run_db(
        _grant_plan, supabase, row["user_id"], row["plan_id"], payment_id
    )

    # …and LAST: an upgrade zeroes the points already spent this cycle, so the
    # user is unblocked the moment they pay instead of buying a bigger cap they
    # are still sitting on top of. Non-fatal by design and self-guarding inside
    # the RPC (no-op unless the new plan is priced above the prior one) — see
    # _stamp_usage_reset.
    await run_db(_stamp_usage_reset, supabase, payment_id)

    # Buying again IS re-opting in. grant_plan's ON CONFLICT DO UPDATE lists its
    # columns explicitly, so it leaves a standing renewal opt-out alone — and a
    # user who cancelled in June and buys again in July must not be on the Wave 2
    # renewal job's skip list. Done HERE in Python and never inside grant_plan
    # (113's rule: the live money-path RPC is not edited for a side concern).
    # The call never raises: the plan is granted and the money is in.
    await run_db(
        subscription_service.clear_renewal_cancellation, supabase, row["user_id"]
    )

    # ── tokenize (auto-renewal plan §6) ────────────────────────────────────
    # BOTH confirmation paths run this function, which is precisely why the
    # capture lives here and not in verify_payment: 3DS destroys the page, so
    # the callback alone is not sufficient (that is why `on_completed` and the
    # webhook exist at all). The call is idempotent, never raises, and returns
    # immediately when SUBSCRIPTION_AUTO_RENEWAL_ENABLED is off — with the flag
    # down no token is ever extracted, let alone stored.
    await payment_method_service.capture_payment_method(supabase, state, fetched)

    await run_db(
        write_audit_log,
        supabase,
        user_id=row["user_id"],
        action="update",
        resource_type="payment_transaction",
        resource_id=payment_id,
        metadata={
            "event": "payment_paid",
            "plan_id": row.get("plan_id"),
            "amount_sar": _money(row.get("amount_sar")),
            "provider_ref": fetched.get("id"),
            "prior_plan_id": prior_plan_id,
        },
    )

    logger.info(
        "payment paid + granted: payment=%s user=%s plan=%s prior=%s expires=%s",
        payment_id, row["user_id"], row.get("plan_id"), prior_plan_id,
        (granted or {}).get("expires_at"),
    )

    # إيصال دفع — plain receipt email, NO tax language (receipt_service owns
    # the rules). Self-claiming (at-most-once across verify/webhook races) and
    # never raises — a lost email must not fail a payment.
    await send_payment_receipt(supabase, updated)

    return {
        "status": "paid",
        "payment_id": payment_id,
        "granted": True,
        "plan_id": (granted or {}).get("plan_id") or row.get("plan_id"),
        "plan_name_ar": (granted or {}).get("name_ar"),
        "expires_at": (granted or {}).get("expires_at"),
        "amount_sar": _money(updated.get("amount_sar") if updated else row.get("amount_sar")),
    }


async def _mark_failed(supabase: SupabaseClient, row: dict, fetched: dict) -> dict:
    """Terminal failure. Never touches a paid/refunded row — a stale
    ``payment_failed`` retry must not erase a successful payment."""
    payment_id = row["payment_id"]
    if row.get("status") in ("paid", "refunded"):
        logger.warning(
            "failed event for a %s payment=%s — ignored", row.get("status"), payment_id
        )
        return {"status": row.get("status"), "payment_id": payment_id, "granted": False}

    source = fetched.get("source") or {}
    message = source.get("message") if isinstance(source, dict) else None

    # `decline_reason` (migration 133) — the provider's own words, promoted out of
    # raw_payload so declines can be GROUPed BY. Truncated because a provider is
    # free to return an essay, and this column exists to be aggregated, not read
    # as a document; the untruncated original stays in raw_payload.
    #
    # Written through the same tolerant update as everything else here: if 133 has
    # not been applied yet, the column is simply absent and the write must not
    # take a payment row down with it — a lost reason string is a reporting gap,
    # while a failed _mark_failed would leave a dead payment marked live.
    update: dict[str, Any] = {
        "status": "failed",
        "provider_ref": fetched.get("id"),
        "raw_payload": fetched,
    }
    if isinstance(message, str) and message.strip():
        update["decline_reason"] = message.strip()[:500]

    try:
        await run_db(_update_transaction, supabase, payment_id, update)
    except Exception as exc:  # noqa: BLE001
        if "decline_reason" not in update:
            raise
        logger.warning(
            "payment=%s: failure update rejected (%s) — retrying without "
            "decline_reason; apply migration 133 to record it",
            payment_id, exc,
        )
        update.pop("decline_reason")
        await run_db(_update_transaction, supabase, payment_id, update)

    logger.info("payment failed: payment=%s message=%s", payment_id, message)
    return {
        "status": "failed",
        "payment_id": payment_id,
        "granted": False,
        "provider_message": (source.get("message") if isinstance(source, dict) else None),
    }


async def _mark_refunded(supabase: SupabaseClient, row: dict, fetched: dict) -> dict:
    """Provider-side refund (webhook path — the self-serve route stamps its own
    fee columns first, so this then finds the row already refunded)."""
    payment_id = row["payment_id"]
    # status='refunded' MUST be stamped before the RPC — revoke_plan_grant
    # raises 'payment_not_refunded' on a row that still says paid.
    if row.get("status") != "refunded":
        await run_db(
            _update_transaction,
            supabase,
            payment_id,
            {"status": "refunded", "raw_payload": fetched},
        )
    action = await run_db(_revoke_plan_grant, supabase, payment_id)
    logger.info("payment refunded: payment=%s revoke_action=%s", payment_id, action)

    # إيصال استرداد — self-claiming, so if the self-serve route already sent
    # it this is a no-op. Never raises.
    await send_refund_receipt(supabase, {**row, "status": "refunded"})

    return {
        "status": "refunded",
        "payment_id": payment_id,
        "revoked": action in REVOKE_ACTIONS_OK,
        "revoke_action": action,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 8. /verify — the browser-side sync (called twice per purchase)
# ═══════════════════════════════════════════════════════════════════════════


async def verify_payment(supabase: SupabaseClient, user_id: str, moyasar_id: str) -> dict:
    """Sync our row with Moyasar's truth for ``moyasar_id``.

    Called from two moments, so it is a SYNC, not a paid-assert:
      * ``on_completed``, before the 3DS redirect — status ``initiated``: we
        store the provider id so an abandoned redirect is still recoverable;
      * the callback page — status ``paid`` (grant) or ``failed`` (offer retry).

    Binding: ``metadata.payment_id`` → our row → ``row.user_id == caller``. The
    ``?id=`` in the URL is attacker-controllable and never selects a row alone
    (plan trap 6). Everything unresolvable answers 404 PAYMENT_NOT_FOUND —
    including another user's payment, so this cannot be used as an oracle.
    """
    try:
        fetched = await fetch_payment(moyasar_id)
    except MoyasarUnconfigured:
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=PAYMENTS_UNAVAILABLE_AR,
            headers={"Retry-After": "60"},
        )
    except MoyasarNotFound:
        # Also the test↔live isolation: an id from the other mode is not
        # fetchable with our key, so it can never grant.
        raise LunaHTTPException(
            status_code=404, code=ErrorCode.PAYMENT_NOT_FOUND, detail=PAYMENT_NOT_FOUND_AR
        )
    except MoyasarUnavailable as exc:
        logger.warning("Moyasar unreachable during verify: %s", exc)
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=MSG_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "5"},
        )
    except MoyasarError as exc:
        logger.warning("Moyasar error during verify: %s", exc)
        raise LunaHTTPException(
            status_code=502, code=ErrorCode.PAYMENT_PROVIDER_ERROR, detail=PROVIDER_ERROR_AR
        )

    if not event_mode_matches(fetched.get("live")):
        logger.error("verify: payment %s mode disagrees with our key — refused", moyasar_id)
        raise LunaHTTPException(
            status_code=404, code=ErrorCode.PAYMENT_NOT_FOUND, detail=PAYMENT_NOT_FOUND_AR
        )

    row = await run_db(_locate_transaction, supabase, fetched)
    if row is None or row.get("user_id") != user_id:
        if row is not None:
            logger.warning(
                "verify: user=%s tried to verify payment=%s owned by %s",
                user_id, row.get("payment_id"), row.get("user_id"),
            )
        raise LunaHTTPException(
            status_code=404, code=ErrorCode.PAYMENT_NOT_FOUND, detail=PAYMENT_NOT_FOUND_AR
        )

    try:
        _assert_matches(row, fetched)
    except MoyasarError as exc:
        logger.error("verify: %s for payment=%s — no grant", exc, row.get("payment_id"))
        raise LunaHTTPException(
            status_code=400, code=ErrorCode.PAYMENT_PROVIDER_ERROR, detail=PAYMENT_MISMATCH_AR
        )

    provider_status = str(fetched.get("status") or "").lower()

    if provider_status == "paid":
        try:
            return await _mark_paid_and_grant(supabase, row, fetched)
        except Exception as exc:  # noqa: BLE001
            # Money is in and the row says paid; only the grant failed. Do NOT
            # 500 the callback page — report paid+ungranted and let the webhook
            # (or a later /verify) finish the job.
            logger.exception(
                "verify: grant failed after payment=%s was marked paid: %s",
                row.get("payment_id"), exc,
            )
            return {
                "status": "paid",
                "payment_id": row["payment_id"],
                "granted": False,
                "plan_id": row.get("plan_id"),
                "expires_at": None,
            }

    if provider_status == "failed":
        return await _mark_failed(supabase, row, fetched)

    if provider_status == "refunded":
        return await _mark_refunded(supabase, row, fetched)

    # 'initiated' (the pre-3DS on_completed call) and anything else we don't
    # act on: persist the provider id so the payment stays recoverable, grant
    # nothing.
    await run_db(
        _update_transaction,
        supabase,
        row["payment_id"],
        {"provider_ref": fetched.get("id"), "raw_payload": fetched},
    )
    return {
        "status": "pending",
        "payment_id": row["payment_id"],
        "granted": False,
        "provider_status": provider_status or None,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 9. Webhook
# ═══════════════════════════════════════════════════════════════════════════


async def handle_webhook_event(supabase: SupabaseClient, body: dict) -> dict:
    """Process an ALREADY-AUTHENTICATED Moyasar webhook body.

    The body is a trigger, not evidence: the only fields read from it are the
    event ``type``, the ``live`` flag, and the payment ``id``. Everything else
    comes from a fresh ``GET /v1/payments/{id}``.

    Returns a small dict; the route answers 200 for every one of them. The only
    escape hatch is ``WebhookRetryable`` (→ 503) for a transient provider/DB
    failure, where another attempt genuinely helps.
    """
    event = str(body.get("type") or "").strip()
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    moyasar_id = data.get("id") or body.get("id")

    if not event_mode_matches(body.get("live")):
        logger.error(
            "webhook %s ignored: live=%r disagrees with key mode=%r",
            event, body.get("live"), moyasar_mode(),
        )
        return {"status": "ignored", "reason": "mode_mismatch"}

    if event not in HANDLED_EVENTS:
        logger.info("webhook %s ignored (not a handled event)", event or "<none>")
        return {"status": "ignored", "reason": "unhandled_event", "event": event}

    if not moyasar_id or not _is_uuid(moyasar_id):
        logger.warning("webhook %s ignored: no usable payment id", event)
        return {"status": "ignored", "reason": "no_payment_id"}

    try:
        fetched = await fetch_payment(str(moyasar_id))
    except MoyasarNotFound:
        # Unknown to our key (other mode / forged id) — 200 + log, never 5xx.
        logger.warning("webhook %s: payment %s not found for our key", event, moyasar_id)
        return {"status": "ignored", "reason": "provider_not_found"}
    except (MoyasarUnavailable, MoyasarUnconfigured) as exc:
        logger.error("webhook %s: re-fetch failed (%s) — asking for a retry", event, exc)
        raise WebhookRetryable(str(exc))
    except MoyasarError as exc:
        logger.error("webhook %s: re-fetch rejected (%s)", event, exc)
        return {"status": "ignored", "reason": "provider_error"}

    if not event_mode_matches(fetched.get("live")):
        logger.error("webhook %s: fetched payment mode disagrees with our key", event)
        return {"status": "ignored", "reason": "mode_mismatch"}

    try:
        row = await run_db(_locate_transaction, supabase, fetched)
    except Exception as exc:  # noqa: BLE001
        logger.exception("webhook %s: row lookup failed: %s", event, exc)
        raise WebhookRetryable(str(exc))

    if row is None:
        # A replay for a payment we never created (or one already purged).
        logger.warning("webhook %s: no matching payment_transactions row for %s", event, moyasar_id)
        return {"status": "ignored", "reason": "unknown_payment"}

    try:
        _assert_matches(row, fetched)
    except MoyasarError as exc:
        logger.error(
            "webhook %s: %s for payment=%s — no DB write", event, exc, row.get("payment_id")
        )
        return {"status": "ignored", "reason": "amount_mismatch"}

    try:
        if event == EVENT_PAID:
            # Trust the FETCHED status, not the event name.
            if str(fetched.get("status") or "").lower() != "paid":
                logger.warning(
                    "webhook payment_paid but provider status=%r for payment=%s",
                    fetched.get("status"), row.get("payment_id"),
                )
                return {"status": "ignored", "reason": "status_disagrees"}
            result = await _mark_paid_and_grant(supabase, row, fetched)
        elif event in (EVENT_FAILED, EVENT_ABANDONED):
            result = await _mark_failed(supabase, row, fetched)
        elif event == EVENT_REFUNDED:
            result = await _mark_refunded(supabase, row, fetched)
        else:  # pragma: no cover — HANDLED_EVENTS is checked above
            return {"status": "ignored", "reason": "unhandled_event"}
    except Exception as exc:  # noqa: BLE001
        # DB/RPC failure on a verified event: money moved, our books didn't.
        # Ask for a retry rather than silently dropping it.
        logger.exception("webhook %s: processing failed for %s: %s", event, moyasar_id, exc)
        raise WebhookRetryable(str(exc))

    return {"status": "ok", "event": event, **result}


# ═══════════════════════════════════════════════════════════════════════════
# 10. Refund (self-serve, inside 24h)
# ═══════════════════════════════════════════════════════════════════════════


async def refund_payment(supabase: SupabaseClient, user_id: str, payment_id: str) -> dict:
    """Refund ``payment_id`` minus the 2 SAR processing fee, then revoke the term.

    Every guard is server-side: ownership, ``status='paid'``, and the 24h window
    measured from ``paid_at`` (the same clock the copy promises). The fee is a
    module constant — it never arrives from the client — and both the fee and
    the net refunded amount are STAMPED on the row, so a later fee change cannot
    rewrite this refund's history.
    """
    row = await run_db(_get_transaction, supabase, payment_id)
    if row is None or row.get("user_id") != user_id:
        raise LunaHTTPException(
            status_code=404, code=ErrorCode.PAYMENT_NOT_FOUND, detail=PAYMENT_NOT_FOUND_AR
        )

    status = row.get("status")
    if status == "refunded":
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.PAYMENT_REFUND_WINDOW_CLOSED,
            detail=REFUND_ALREADY_AR,
        )
    if status != "paid":
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.PAYMENT_REFUND_WINDOW_CLOSED,
            detail=REFUND_NOT_PAID_AR,
        )

    paid_at = _parse_ts(row.get("paid_at"))
    if paid_at is None or _now() - paid_at > REFUND_WINDOW:
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.PAYMENT_REFUND_WINDOW_CLOSED,
            detail=REFUND_WINDOW_CLOSED_AR,
        )

    # ── refunds unwind NEWEST-FIRST (M-1) ──────────────────────────────────
    # Refunding a rung a later upgrade has already stood on returns the money
    # and keeps the plan: revoke_plan_grant answers `plan_switched` and — quite
    # rightly — refuses to eat days of a plan the user currently pays for.
    # Blocking is chosen over clawing the credit back out of the standing term:
    # that claw-back means converting SAR into days on a plan the customer is
    # still using, from an estimate, destructively, at refund time, with no
    # undo. Refusing moves no money, loses no entitlement, and leaves the
    # customer a working path — refund the newest purchase first, which
    # RESTORES the plan underneath it and un-blocks this one automatically.
    superseding = await run_db(_find_superseding_payment, supabase, user_id, row)
    if superseding is not None:
        logger.warning(
            "refund refused: payment=%s (user=%s plan=%s) was superseded by "
            "payment=%s plan=%s credit=%s — refunds must unwind newest-first",
            payment_id, user_id, row.get("plan_id"), superseding.get("payment_id"),
            superseding.get("plan_id"), _money(superseding.get("upgrade_credit_sar")),
        )
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.PAYMENT_REFUND_WINDOW_CLOSED,
            detail=REFUND_SUPERSEDED_AR,
        )

    provider_ref = row.get("provider_ref")
    if not provider_ref:
        logger.error("refund: payment=%s is paid but has no provider_ref", payment_id)
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.PAYMENT_REFUND_WINDOW_CLOSED,
            detail=REFUND_NOT_PAID_AR,
        )

    charged_halalas = to_halalas(row.get("amount_sar"))
    fee_halalas = _refund_fee_halalas(row)
    refund_halalas = charged_halalas - fee_halalas
    if refund_halalas < MIN_HALALAS:
        # Impossible with today's catalog (min charge 49.90); a future cheap
        # plan must not be able to produce a zero/negative refund.
        logger.error(
            "refund: computed %d halalas for payment=%s — below Moyasar's minimum",
            refund_halalas, payment_id,
        )
        raise LunaHTTPException(
            status_code=400, code=ErrorCode.PAYMENT_PROVIDER_ERROR, detail=REFUND_FAILED_AR
        )

    try:
        provider_response = await refund_at_provider(str(provider_ref), refund_halalas)
    except MoyasarUnconfigured:
        raise LunaHTTPException(
            status_code=503,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            detail=PAYMENTS_UNAVAILABLE_AR,
            headers={"Retry-After": "60"},
        )
    except MoyasarError as exc:
        logger.error("refund: Moyasar refused payment=%s: %s", payment_id, exc)
        raise LunaHTTPException(
            status_code=502, code=ErrorCode.PAYMENT_PROVIDER_ERROR, detail=PROVIDER_ERROR_AR
        )

    refunded_sar = q2(Decimal(refund_halalas) / Decimal(100))
    fee_sar = q2(Decimal(fee_halalas) / Decimal(100))

    # Provider-side refund SUCCEEDED past this point. Any failure below leaves
    # money returned with our row still 'paid' — recoverable, because the
    # payment_refunded webhook lands next and runs _mark_refunded.
    updated = await run_db(
        _update_transaction,
        supabase,
        payment_id,
        {
            "status": "refunded",
            "refund_fee_sar": _money(fee_sar),
            "refunded_amount_sar": _money(refunded_sar),
            "raw_payload": provider_response,
        },
    ) or {**row, "status": "refunded"}

    # Only now — the RPC refuses a row that is not yet 'refunded'.
    revoke_action = await run_db(_revoke_plan_grant, supabase, payment_id)

    await run_db(
        write_audit_log,
        supabase,
        user_id=user_id,
        action="update",
        resource_type="payment_transaction",
        resource_id=payment_id,
        metadata={
            "event": "refunded",
            "plan_id": row.get("plan_id"),
            "refunded_amount_sar": _money(refunded_sar),
            "refund_fee_sar": _money(fee_sar),
            "revoke_action": revoke_action,
        },
    )

    logger.info(
        "refund complete: payment=%s user=%s refunded=%s fee=%s revoke_action=%s",
        payment_id, user_id, refunded_sar, fee_sar, revoke_action,
    )

    # إيصال استرداد — self-claiming + never raises (see receipt_service).
    await send_refund_receipt(supabase, updated)

    summary = transaction_summary(updated)
    summary["revoked"] = revoke_action in REVOKE_ACTIONS_OK
    summary["revoke_action"] = revoke_action
    return summary


# ═══════════════════════════════════════════════════════════════════════════
# 11. History
# ═══════════════════════════════════════════════════════════════════════════


async def list_history(supabase: SupabaseClient, user_id: str) -> list[dict]:
    """The caller's own receipts, newest first, capped at 50.

    Supersession (M-1) is resolved here rather than per-row because this is the
    only place that holds all of the caller's payments at once — and it must be
    the same predicate ``refund_payment`` enforces, or the list offers a button
    the refund route then 409s.
    """
    rows = await run_db(_list_transactions, supabase, user_id)
    plan_ids = sorted({r.get("plan_id") for r in rows if r.get("plan_id")})
    plans = await run_db(_fetch_plans, supabase, list(plan_ids)) if plan_ids else {}
    return [
        transaction_summary(
            row,
            (plans.get(row.get("plan_id")) or {}).get("name_ar"),
            superseded=_is_superseded(row, rows),
        )
        for row in rows
    ]


# ═══════════════════════════════════════════════════════════════════════════
# 12. Apple Pay merchant validation
# ═══════════════════════════════════════════════════════════════════════════


def _validate_apple_validation_url(validation_url: str) -> str:
    """Apple's session URL, sanity-checked before we hand it to Moyasar.

    The value comes from the browser's ``onvalidatemerchant`` event, so it is
    client-controlled. Restricting it to ``https://*.apple.com`` keeps this
    endpoint from being turned into a request-forwarder aimed at arbitrary hosts.
    """
    url = (validation_url or "").strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "apple.com" or host.endswith(".apple.com")):
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            detail=APPLEPAY_URL_INVALID_AR,
        )
    return url


async def applepay_session(validation_url: str) -> dict:
    """Proxy ``GET /v1/applepay/initiate`` and return Moyasar's JSON as-is.

    Best-effort by design: the frontend falls back to cards-only when this
    fails, so every failure surfaces as PAYMENT_PROVIDER_ERROR rather than
    taking the checkout page down.
    """
    _secret, publishable = _require_keys()
    settings = get_settings()
    # Belt-and-braces behind the checkout session's `applepay_enabled` flag —
    # the form never calls this while disabled, but the route is authed-public.
    if not settings.MOYASAR_APPLEPAY_ENABLED:
        raise LunaHTTPException(
            status_code=502,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            detail=APPLEPAY_DISABLED_AR,
        )
    url = _validate_apple_validation_url(validation_url)

    try:
        return await _moyasar_request(
            "GET",
            "/applepay/initiate",
            params={
                "validation_url": url,
                "display_name": "ريحان",
                "domain_name": settings.MOYASAR_APPLEPAY_DOMAIN,
                "publishable_api_key": publishable,
            },
            authed=False,          # this endpoint authenticates by publishable key
            retries=1,             # idempotent GET
        )
    except MoyasarError as exc:
        logger.warning("Apple Pay merchant validation failed: %s", exc)
        raise LunaHTTPException(
            status_code=502, code=ErrorCode.PAYMENT_PROVIDER_ERROR, detail=PROVIDER_ERROR_AR
        )
