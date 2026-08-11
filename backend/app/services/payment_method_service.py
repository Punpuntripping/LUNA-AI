"""Stored payment methods — token custody for auto-renewal (بطاقة محفوظة).

Implements `.claude/plans/subscription_auto_renewal.md` Phase 3 (§6). Deliberately
a separate module from ``payment_service``: that module moves money on a path a
user is watching, this one holds a CREDENTIAL. The two have different failure
modes and different blast radii, and 113/120 both established the precedent —
a side concern gets its own module, never an edit to the grant path.

WHAT THIS MODULE IS RESPONSIBLE FOR ────────────────────────────────────────────

1. **The token never leaves the backend.** ``provider_token`` is selected only by
   the two functions that must charge or revoke it. Every shape that reaches a
   route goes through ``describe_method`` — brand, last4, expiry, consent date.
   There is no code path, anywhere, that serializes the token to a client.
2. **The consent artefact is the server's, not the client's.** The Arabic
   disclosure is BUILT here from ``plans`` (name, price, duration) and hashed
   here; the browser posts ``{"accepted": true}`` and nothing else. A client
   cannot claim it was shown different words, because it never supplies them.
3. **A token with no consent is not chargeable.** ``capture_payment_method``
   refuses to store a token for a payment that carries no consent record, and
   the renewal job refuses to charge a row whose ``consent_given_at`` is NULL.
   Both walls, deliberately — the row could also be written by an operator.
4. **Revoking means revoking at the provider too.** A row marked ``revoked_at``
   whose token is still live at Moyasar is the bug this feature can produce that
   actually costs somebody money.

THE FEATURE FLAG ──────────────────────────────────────────────────────────────
``settings.SUBSCRIPTION_AUTO_RENEWAL_ENABLED`` is checked at the TOP of every
write path. With it off nothing is ever stored, so a deploy with the flag down
cannot create the state the rest of this feature acts on.

WHERE THE CONSENT ROW LIVES (and why it is not on payment_transactions) ────────
Migration 132 as specified in the plan's §5 adds **no consent columns to
``payment_transactions``** — ``consent_given_at`` / ``consent_text_hash`` exist
only on ``payment_methods``, and that row cannot exist until a token does (the
token arrives with the payment, minutes after the checkbox is ticked). So the
consent is recorded, at the moment it is given, as an **append-only
``audit_logs`` row** keyed to the payment (``resource_type='payment_transaction'``,
``resource_id=<payment_id>``, ``metadata.event='recurring_consent'``) and copied
onto the ``payment_methods`` row when the token lands. ``audit_logs`` is the
right ledger for this: append-only by policy (012), never deleted (090 exempts
it from the account purge), and already indexed on ``(resource_type, resource_id)``.

⚠ If migration 132 ends up adding ``recurring_consent_at`` / ``recurring_consent_hash``
to ``payment_transactions`` after all, move ``record_consent`` / ``fetch_consent``
onto those columns and delete this note — reading a consent artefact out of the
audit trail is defensible, but a first-class column is better.

DB dependency: migration ``132_subscription_auto_renewal.sql`` — the
``payment_methods`` table (§5.1). ⚠ APPLY 132 BEFORE DEPLOYING WITH THE FLAG ON.
Every read here treats a missing table as "no stored method" so a flag-off
deploy ahead of the migration is inert rather than broken.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import httpx
from supabase import Client as SupabaseClient

from shared.config import get_settings
from shared.db.run import run_db

logger = logging.getLogger(__name__)

PROVIDER = "moyasar"
MOYASAR_API_BASE = "https://api.moyasar.com/v1"
HTTP_TIMEOUT_S = 15.0

# ``plans.billing_cycle`` for a plan that renews. Trap 6 in the plan: this
# column has been decorative since 076 — 132 sets it to this value for pro/max
# and THIS constant is the branch that finally reads it.
RECURRING_CYCLE = "recurring_30d"

# Belt AND braces. A plan renews only if it is one of these two AND its
# billing_cycle says so. Either half alone is one typo away from charging a
# `basic` buyer (who was told, on the card they were looking at, that their plan
# does not renew) or from silently renewing nothing.
RENEWABLE_PLAN_IDS = frozenset({"pro", "max"})

# Consent bookkeeping.
CONSENT_EVENT = "recurring_consent"
DISCLOSURE_VERSION = "v1"

# ── Arabic (rule 5) ──────────────────────────────────────────────────────────

CONSENT_REQUIRED_AR = "يلزم الموافقة على شروط التجديد التلقائي"
CONSENT_NOT_APPLICABLE_AR = "هذه الباقة لا تتضمن تجديداً تلقائياً"
CONSENT_NOT_OPEN_AR = "لا يمكن تسجيل الموافقة على عملية دفع منتهية"
CONSENT_STORE_FAILED_AR = "تعذّر حفظ الموافقة، حاول مجدداً"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def auto_renewal_enabled() -> bool:
    """The master kill-switch. Read fresh every call — it is a Railway env var
    and an operator flipping it must not need a redeploy to take effect."""
    return bool(get_settings().SUBSCRIPTION_AUTO_RENEWAL_ENABLED)


def _is_missing_relation(exc: Exception) -> bool:
    """Is this "migration 132 is not applied yet" rather than a real failure?

    PostgREST answers a missing table with PGRST205 ("Could not find the table
    … in the schema cache") and a missing column with 42703. Both mean the same
    thing here: there is no stored-card state, which is exactly the state a
    flag-off deploy is supposed to be in. Matched on text because postgrest-py
    raises a generic APIError and its shape has changed between versions.
    """
    text = str(exc).lower()
    return (
        "42p01" in text
        or "42703" in text
        or "pgrst205" in text
        or "could not find the table" in text
        or "does not exist" in text
        or "schema cache" in text
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. The disclosure — the server owns the words it hashes
# ═══════════════════════════════════════════════════════════════════════════


def plan_renews(plan: Optional[dict]) -> bool:
    """Does this catalog row describe a self-renewing plan? (plan §5.4, trap 6)"""
    if not plan:
        return False
    return (
        str(plan.get("plan_id") or "") in RENEWABLE_PLAN_IDS
        and str(plan.get("billing_cycle") or "") == RECURRING_CYCLE
    )


def requires_recurring_consent(plan: Optional[dict]) -> bool:
    """Must this purchase collect a recurring-payment consent before it pays?

    False whenever the feature is off, which is what makes a flag-off deploy
    invisible: the checkout session then carries no disclosure, the form renders
    no checkbox, and ``save_card`` is never requested.
    """
    return auto_renewal_enabled() and plan_renews(plan)


def _fmt_price(value: Any) -> str:
    """``49.9`` / ``'49.90'`` → ``'49.90'``. Two decimals, Western digits.

    The hash is over the EXACT string the user is shown, so the formatting is
    part of the artefact — it lives here and nowhere else.
    """
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def recurring_disclosure_ar(plan: Optional[dict]) -> Optional[str]:
    """THE canonical Arabic recurring disclosure, or None when none is owed.

    This is simultaneously the KSA e-commerce pre-purchase disclosure and the
    card-scheme stored-credential consent artefact (plan §9); one string serves
    both, and it is built from ``plans`` so it cannot disagree with what is
    actually charged.

    It states, in this order: the amount, the cadence, that the card is stored,
    and how to stop. It deliberately does NOT state an absolute next-charge
    DATE: at checkout that date is not yet knowable — ``grant_plan`` stacks a
    same-plan purchase onto a live term, so the real boundary is whatever
    ``expires_at`` ends up being after the grant. A relative cadence is the
    strongest statement that is true for every buyer.

    ⚠ CHANGING ONE CHARACTER OF THIS STRING CHANGES THE HASH. That is the point
    (``consent_text_hash`` exists so a wording change stays provable), but bump
    ``DISCLOSURE_VERSION`` at the same time so the audit rows say which text a
    given user actually agreed to.
    """
    if not plan_renews(plan):
        return None
    name = str(plan.get("name_ar") or plan.get("plan_id") or "").strip()
    price = _fmt_price(plan.get("price_sar"))
    days = int(plan.get("duration_days") or 30)
    return (
        f"بتأكيد الشراء تُفوّض «ريحان» بحفظ بطاقتك لدى مزوّد الدفع وخصم "
        f"{price} ريال سعودي كل {days} يوماً لتجديد باقة {name} تلقائياً، "
        f"إلى أن توقف التجديد. يمكنك إيقاف التجديد في أي وقت من إعدادات "
        f"الحساب، ويستمر اشتراكك حتى نهاية المدة المدفوعة. لن يُخصم أي مبلغ "
        f"إضافي بعد الإيقاف."
    )


def consent_text_hash(text: str) -> str:
    """sha256 of the exact disclosure, hex. NFC-normalized? No — deliberately.

    The string is a Python literal in this file, so its byte sequence is fixed
    at author time; normalizing would add a step that could itself change
    between runtimes. UTF-8 of the literal is the artefact.
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# 2. Consent — recorded against the PAYMENT, before the money moves
# ═══════════════════════════════════════════════════════════════════════════


def _insert_consent_row(
    supabase: SupabaseClient,
    *,
    user_id: str,
    payment_id: str,
    plan_id: str,
    text_hash: str,
    given_at: str,
) -> dict:
    """Append the consent artefact. RAISES on failure, unlike ``write_audit_log``.

    That difference is the whole reason this is not a call to
    ``audit_service.write_audit_log``: that helper is fire-and-forget by design
    ("failures NEVER block user operations"), which is right for an audit trail
    and wrong for a consent record. If we cannot prove the user agreed, the
    purchase must not proceed as a recurring one.
    """
    payload = {
        "user_id": str(user_id),
        "action": "create",
        "resource_type": "payment_transaction",
        "resource_id": str(payment_id),
        "metadata": {
            "event": CONSENT_EVENT,
            "plan_id": plan_id,
            "consent_text_hash": text_hash,
            "disclosure_version": DISCLOSURE_VERSION,
            # Stamped explicitly rather than read back from created_at: this is
            # the value copied onto payment_methods.consent_given_at, and it must
            # not depend on whether PostgREST returned the row's defaults.
            "consented_at": given_at,
        },
    }
    res = supabase.table("audit_logs").insert(payload).execute()
    rows = getattr(res, "data", None) or []
    if not rows:
        raise RuntimeError("consent audit insert returned no row")
    return rows[0]


def fetch_consent(supabase: SupabaseClient, payment_id: str) -> Optional[dict]:
    """The consent artefact for one payment, or None. Sync — call via run_db.

    Filtered in Python on ``metadata.event`` rather than with a PostgREST
    ``metadata->>event`` predicate: the index that makes this cheap is
    ``(resource_type, resource_id)`` (012), a payment has a handful of audit
    rows at most, and a JSON predicate would be one more thing to get wrong in
    two places.
    """
    try:
        res = (
            supabase.table("audit_logs")
            .select("metadata, created_at")
            .eq("resource_type", "payment_transaction")
            .eq("resource_id", str(payment_id))
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("consent lookup failed for payment=%s: %s", payment_id, exc)
        return None

    for row in getattr(res, "data", None) or []:
        meta = row.get("metadata")
        if isinstance(meta, dict) and meta.get("event") == CONSENT_EVENT:
            return {
                "consent_given_at": meta.get("consented_at") or row.get("created_at"),
                "consent_text_hash": meta.get("consent_text_hash"),
                "disclosure_version": meta.get("disclosure_version"),
                "plan_id": meta.get("plan_id"),
            }
    return None


async def record_consent(
    supabase: SupabaseClient, *, user_id: str, payment_row: dict, plan: dict
) -> dict:
    """Stamp the caller's recurring consent against an OPEN payment row.

    Called from ``POST /payments/{payment_id}/consent`` before the browser
    mounts the Moyasar form. Idempotent: a second call returns the first
    artefact untouched, because a user who reloads the page must not end up with
    two consent records for one purchase (and the second would carry a later
    timestamp, quietly rewriting when they agreed).

    Returns the consent shape the route serializes. Raises RuntimeError only if
    the write itself failed — the route maps that to 503.
    """
    payment_id = str(payment_row.get("payment_id"))
    existing = await run_db(fetch_consent, supabase, payment_id)
    if existing:
        return {**existing, "already_recorded": True}

    text = recurring_disclosure_ar(plan)
    if not text:                       # caller checked; second wall
        raise ValueError("plan does not renew — no consent to record")

    given_at = _now_iso()
    await run_db(
        _insert_consent_row,
        supabase,
        user_id=str(user_id),
        payment_id=payment_id,
        plan_id=str(plan.get("plan_id")),
        text_hash=consent_text_hash(text),
        given_at=given_at,
    )
    logger.info(
        "recurring consent recorded: user=%s payment=%s plan=%s version=%s",
        user_id, payment_id, plan.get("plan_id"), DISCLOSURE_VERSION,
    )
    return {
        "consent_given_at": given_at,
        "consent_text_hash": consent_text_hash(text),
        "disclosure_version": DISCLOSURE_VERSION,
        "plan_id": plan.get("plan_id"),
        "already_recorded": False,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 3. ⚠ THE PROVIDER ADAPTER — UNVERIFIED AGAINST THE LIVE MOYASAR API
# ═══════════════════════════════════════════════════════════════════════════


def extract_card_token(fetched: Optional[dict]) -> Optional[dict]:
    """Pull the reusable card token + display fields out of a Moyasar payment.

    ⚠⚠ **UNVERIFIED.** Every field name below is inferred from Moyasar's public
    documentation and has NEVER been checked against a real ``save_card: true``
    response on this merchant account — as of 2026-08-08 tokenization was not
    even enabled on it (see `.claude/plans/subscription_cancellation.md`). This
    function is the ONE place the guesswork lives, deliberately: verifying the
    feature means dumping one real payment object and correcting this function,
    not auditing the codebase.

    **DO NOT FLIP ``SUBSCRIPTION_AUTO_RENEWAL_ENABLED`` UNTIL THIS HAS BEEN
    CHECKED AGAINST A REAL RESPONSE.** The failure mode if it is wrong is benign
    by construction (no token found → nothing stored → nothing renews → the
    subscription lapses exactly as it does today), which is why it is safe to
    ship — but it is a silent no-op, not a loud error, so it will not announce
    itself.

    Expected shape (unverified)::

        {"id": "...", "status": "paid",
         "source": {"type": "creditcard", "company": "mada",
                    "name": "…", "number": "4111-11XX-XXXX-1111",
                    "token": "token_xxx", "month": "12", "year": "2030"}}

    Returns ``{"provider_token", "brand", "last4", "exp_month", "exp_year"}`` or
    None. Only ``provider_token`` is required; the display fields are best-effort
    and a missing one renders as «بطاقة محفوظة» rather than failing the capture.
    """
    if not isinstance(fetched, dict):
        return None
    source = fetched.get("source")
    if not isinstance(source, dict):
        return None

    # Candidate keys, most-likely first. `token` is what the docs show on a
    # saved-card source; `saved_card_token` / `card_token` are defensive.
    token = None
    for key in ("token", "saved_card_token", "card_token"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            token = value.strip()
            break
    if not token:
        return None

    brand = None
    for key in ("company", "brand", "network"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            brand = value.strip().lower()[:32]
            break

    # Moyasar masks the PAN as e.g. "4111-11XX-XXXX-1111"; the last 4 real
    # digits are the trailing run. Never store more than four.
    last4 = None
    number = source.get("number") or source.get("masked_number")
    if isinstance(number, str):
        digits = "".join(ch for ch in number if ch.isdigit())
        if len(digits) >= 4:
            last4 = digits[-4:]

    def _int_or_none(*keys: str) -> Optional[int]:
        for key in keys:
            value = source.get(key)
            if value in (None, ""):
                continue
            try:
                return int(str(value).strip())
            except (TypeError, ValueError):
                continue
        return None

    exp_month = _int_or_none("month", "exp_month", "expiry_month")
    exp_year = _int_or_none("year", "exp_year", "expiry_year")
    if exp_year is not None and exp_year < 100:        # "30" → 2030
        exp_year += 2000
    if exp_month is not None and not (1 <= exp_month <= 12):
        exp_month = None

    return {
        "provider_token": token,
        "brand": brand,
        "last4": last4,
        "exp_month": exp_month,
        "exp_year": exp_year,
    }


def revoke_token_at_provider(token: str) -> bool:
    """Invalidate a stored token at Moyasar. SYNC — call via ``run_db``.

    ⚠ **UNVERIFIED.** ``DELETE /v1/tokens/{token}`` is the endpoint the docs
    imply; it has never been exercised on this account. A 404/405 is treated as
    "already gone / not supported" and does NOT block the local revoke — the row
    must be marked revoked either way, because leaving it usable while the user
    believes they deleted their card is the worse of the two failures. Every
    non-success is logged at ERROR so an unsupported endpoint surfaces instead of
    quietly leaving live tokens behind.

    Sync rather than async because the account-purge sweep is sync (it runs in a
    worker thread), and ONE implementation of an unverified provider call is
    worth more than an ergonomic async twin. The async callers go through
    ``run_db``.

    Returns True only when the provider confirmed the revocation.
    """
    secret = (get_settings().MOYASAR_SECRET_KEY or "").strip()
    if not secret:
        logger.error("token revoke skipped: MOYASAR_SECRET_KEY unset — token may still be live")
        return False
    if not token:
        return False
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_S) as client:
            response = client.delete(
                f"{MOYASAR_API_BASE}/tokens/{quote(str(token), safe='')}",
                auth=(secret, ""),
            )
    except httpx.HTTPError as exc:
        logger.error(
            "token revoke: transport failure — THE TOKEN MAY STILL BE LIVE AT "
            "MOYASAR (revoke it from the dashboard): %s", exc,
        )
        return False

    if response.status_code in (200, 202, 204):
        return True
    if response.status_code in (404, 405, 501):
        logger.error(
            "token revoke: Moyasar answered %d — the delete-token endpoint is "
            "wrong or unsupported. The local row IS revoked, but the token may "
            "still be chargeable; revoke it from the dashboard and fix "
            "revoke_token_at_provider.", response.status_code,
        )
        return False
    logger.error(
        "token revoke: Moyasar answered %d (%s) — token may still be live",
        response.status_code, response.text[:200],
    )
    return False


# ═══════════════════════════════════════════════════════════════════════════
# 4. Storage — DB access (sync helpers, always through run_db)
# ═══════════════════════════════════════════════════════════════════════════

# Display columns ONLY. `provider_token` is absent on purpose: this list feeds
# every read that can reach a route, and a column that is never selected cannot
# be leaked by a serializer that forgets to whitelist it.
_METHOD_PUBLIC_COLUMNS = (
    "payment_method_id, user_id, provider, brand, last4, exp_month, exp_year, "
    "consent_given_at, consent_text_hash, revoked_at, created_at, updated_at"
)

# The one list that includes the credential. Used by exactly two callers: the
# renewal charge and the revoke.
_METHOD_SECRET_COLUMNS = _METHOD_PUBLIC_COLUMNS + ", provider_token"


def _select_active(
    supabase: SupabaseClient, user_id: str, *, with_token: bool
) -> Optional[dict]:
    columns = _METHOD_SECRET_COLUMNS if with_token else _METHOD_PUBLIC_COLUMNS
    res = (
        supabase.table("payment_methods")
        .select(columns)
        .eq("user_id", str(user_id))
        .is_("revoked_at", "null")
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def get_active_method(supabase: SupabaseClient, user_id: str) -> Optional[dict]:
    """The caller's active stored card, WITHOUT the token. Sync — via run_db.

    A missing table (132 unapplied) reads as "no stored card", which is the
    truth: with the flag off nothing was ever stored.
    """
    try:
        return _select_active(supabase, user_id, with_token=False)
    except Exception as exc:  # noqa: BLE001
        if _is_missing_relation(exc):
            return None
        logger.warning("payment_methods read failed for user=%s: %s", user_id, exc)
        return None


def get_chargeable_method(supabase: SupabaseClient, user_id: str) -> Optional[dict]:
    """The active method WITH its token, only if it is actually chargeable.

    "Chargeable" adds one condition to "active": ``consent_given_at`` is set.
    A token with no consent is not a payment instrument — it is a credential we
    are holding with no right to use (plan §5.1). The renewal job's selection
    already requires this; enforcing it here too means an operator-inserted row
    cannot be charged either.
    """
    try:
        row = _select_active(supabase, user_id, with_token=True)
    except Exception as exc:  # noqa: BLE001
        if _is_missing_relation(exc):
            return None
        raise
    if not row:
        return None
    if not row.get("consent_given_at") or not row.get("provider_token"):
        logger.warning(
            "payment method %s for user=%s has no consent (or no token) — NOT chargeable",
            row.get("payment_method_id"), user_id,
        )
        return None
    return row


def _insert_method(supabase: SupabaseClient, payload: dict) -> dict:
    res = supabase.table("payment_methods").insert(payload).execute()
    rows = getattr(res, "data", None) or []
    if not rows:
        raise RuntimeError("payment_methods insert returned no row")
    return rows[0]


def _mark_revoked(supabase: SupabaseClient, payment_method_id: str) -> bool:
    res = (
        supabase.table("payment_methods")
        .update({"revoked_at": _now_iso(), "updated_at": _now_iso()})
        .eq("payment_method_id", str(payment_method_id))
        .is_("revoked_at", "null")
        .execute()
    )
    return bool(getattr(res, "data", None) or [])


def describe_method(row: Optional[dict]) -> dict:
    """The ONLY shape a stored card may take on the wire.

    Whitelist, never a blacklist: a future column added to ``payment_methods``
    is invisible here until somebody adds it deliberately. ``provider_token``
    can therefore never be leaked by forgetting to remove it — and it is not
    even SELECTED on the read path (see ``_METHOD_PUBLIC_COLUMNS``).

    FLAT, with ``has_method`` as the only field guaranteed meaningful, because
    that is the contract the settings dialog is written against: "no card" and
    "no such endpoint" must look identical to it, so a backend that predates
    the feature degrades to an absent section rather than a billing error in
    front of the password and delete-account controls.
    """
    if not row:
        return {
            "has_method": False,
            "payment_method_id": None,
            "provider": None,
            "brand": None,
            "last4": None,
            "exp_month": None,
            "exp_year": None,
            "consent_given_at": None,
            "created_at": None,
        }
    return {
        "has_method": True,
        "payment_method_id": row.get("payment_method_id"),
        "provider": row.get("provider") or PROVIDER,
        "brand": row.get("brand"),
        "last4": row.get("last4"),
        "exp_month": row.get("exp_month"),
        "exp_year": row.get("exp_year"),
        "consent_given_at": row.get("consent_given_at"),
        "created_at": row.get("created_at"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 5. Capture — called from BOTH confirmation paths, idempotently
# ═══════════════════════════════════════════════════════════════════════════


async def capture_payment_method(
    supabase: SupabaseClient, payment_row: dict, fetched: dict
) -> Optional[str]:
    """Persist the card token that came back with a successful payment.

    Called from ``payment_service._mark_paid_and_grant``, which is the single
    function BOTH confirmation paths run (the browser's ``/verify`` and the
    webhook). That is what makes this idempotent across the 3DS redirect the
    plan's §6 worries about: whichever path arrives first stores the token, the
    second finds it already stored and no-ops.

    **NEVER RAISES.** The money is in and the plan is granted by the time this
    runs; a failure to store a card must not surface as a failed purchase. The
    cost of failing is that the subscription does not auto-renew — which is
    exactly today's behaviour.

    Refuses, in this order:
      * the feature flag is off — nothing is ever stored;
      * the plan does not renew (``basic``: storing its card collects a
        credential with no purpose, which PDPL does not love);
      * no consent artefact for this payment — a token without consent is not
        chargeable, so storing it would only create a liability;
      * no token in the provider payload (the ordinary case today: ``save_card``
        was never requested).

    Returns the ``payment_method_id`` when a row was written or already existed.
    """
    payment_id = payment_row.get("payment_id")
    user_id = payment_row.get("user_id")
    plan_id = payment_row.get("plan_id")

    try:
        if not auto_renewal_enabled():
            return None
        if not user_id or str(plan_id) not in RENEWABLE_PLAN_IDS:
            return None

        card = extract_card_token(fetched)
        if not card:
            logger.info(
                "no card token on payment=%s (save_card not requested, or the "
                "extraction adapter needs correcting) — nothing stored",
                payment_id,
            )
            return None

        consent = await run_db(fetch_consent, supabase, str(payment_id))
        if not consent or not consent.get("consent_text_hash"):
            logger.warning(
                "payment=%s returned a card token but carries NO recurring "
                "consent — refusing to store it (plan §6)", payment_id,
            )
            return None

        existing = await run_db(_select_active_with_token_safe, supabase, str(user_id))
        if existing and existing.get("provider_token") == card["provider_token"]:
            return str(existing.get("payment_method_id"))       # the other path won

        if existing:
            # A DIFFERENT card on the same account. One active method per user
            # (132's partial unique index), so the old one goes first — and it
            # goes at the provider too, not just in our table.
            await revoke_method_row(supabase, existing, reason="replaced_by_new_card")

        payload = {
            "user_id": str(user_id),
            "provider": PROVIDER,
            "provider_token": card["provider_token"],
            "brand": card.get("brand"),
            "last4": card.get("last4"),
            "exp_month": card.get("exp_month"),
            "exp_year": card.get("exp_year"),
            "consent_given_at": consent.get("consent_given_at") or _now_iso(),
            "consent_text_hash": consent.get("consent_text_hash"),
        }
        try:
            row = await run_db(_insert_method, supabase, payload)
        except Exception as exc:  # noqa: BLE001
            text = str(exc).lower()
            if "23505" in text or "duplicate key" in text:
                # The other confirmation path inserted between our read and our
                # write. Its row is as good as ours.
                logger.info(
                    "card token for user=%s already stored by the other "
                    "confirmation path (payment=%s)", user_id, payment_id,
                )
                again = await run_db(_select_active_with_token_safe, supabase, str(user_id))
                return str((again or {}).get("payment_method_id") or "") or None
            raise

        logger.info(
            "card token stored: user=%s payment=%s method=%s brand=%s last4=%s",
            user_id, payment_id, row.get("payment_method_id"),
            card.get("brand"), card.get("last4"),
        )
        return str(row.get("payment_method_id"))
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "card token capture failed for payment=%s (the plan IS granted and "
            "the money IS in; this subscription simply will not auto-renew): %s",
            payment_id, exc,
        )
        return None


def _select_active_with_token_safe(
    supabase: SupabaseClient, user_id: str
) -> Optional[dict]:
    """``_select_active(with_token=True)`` that answers None on a missing table."""
    try:
        return _select_active(supabase, user_id, with_token=True)
    except Exception as exc:  # noqa: BLE001
        if _is_missing_relation(exc):
            return None
        raise


# ═══════════════════════════════════════════════════════════════════════════
# 6. Revoke
# ═══════════════════════════════════════════════════════════════════════════


async def revoke_method_row(
    supabase: SupabaseClient, row: dict, *, reason: str
) -> bool:
    """Revoke ONE stored method: local row first, then the provider.

    ORDER IS DELIBERATE. The local mark goes first so that a provider call that
    hangs or fails cannot leave a row we still consider chargeable — the renewal
    job reads ``revoked_at IS NULL`` and must never pick up a card the user
    asked us to forget. The provider call then follows best-effort, and a
    failure is logged at ERROR as an ops item (a live token at Moyasar with no
    local row is invisible until somebody looks).

    Returns True when the provider also confirmed.
    """
    method_id = row.get("payment_method_id")
    token = row.get("provider_token")

    try:
        await run_db(_mark_revoked, supabase, str(method_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("could not mark method=%s revoked: %s", method_id, exc)
        return False

    if not token:
        logger.error(
            "method=%s revoked locally but its token was not loaded — cannot "
            "invalidate it at the provider", method_id,
        )
        return False

    revoked = await run_db(revoke_token_at_provider, str(token))
    logger.info(
        "payment method revoked: method=%s reason=%s provider_confirmed=%s",
        method_id, reason, revoked,
    )
    return revoked


async def revoke_active_method(
    supabase: SupabaseClient, user_id: str, *, reason: str = "user_request"
) -> dict:
    """Revoke the caller's active card. Idempotent — no card is a clean answer.

    Returns ``{"revoked": bool, "provider_confirmed": bool, …the emptied
    describe_method shape}``. ``revoked`` is False only when there was nothing
    to revoke.
    """
    row = await run_db(_select_active_with_token_safe, supabase, str(user_id))
    if not row:
        return {"revoked": False, "provider_confirmed": False, **describe_method(None)}
    confirmed = await revoke_method_row(supabase, row, reason=reason)
    # The emptied state rides along so the caller can write it straight into its
    # cache instead of re-reading (and so a 200 body and a re-read agree).
    return {"revoked": True, "provider_confirmed": confirmed, **describe_method(None)}


def revoke_all_for_user_sync(supabase: SupabaseClient, user_id: str, *, reason: str) -> int:
    """Kill every stored token for one user. SYNC, and it NEVER raises.

    Exists for the account-purge sweep (plan §10: "the purge path MUST revoke the
    token at Moyasar, not merely delete the row — a live token on a deleted
    account is the worst version of this bug"). That sweep is sync, runs in a
    worker thread, and must not be breakable by this call: a purge that fails
    over a card token would miss a PDPL erasure deadline, which is worse.

    Not gated on the feature flag, deliberately — tokens stored while it was ON
    must still die if it is later turned off.

    Returns the number of tokens the provider confirmed revoked.
    """
    confirmed = 0
    try:
        res = (
            supabase.table("payment_methods")
            .select("payment_method_id, provider_token, revoked_at")
            .eq("user_id", str(user_id))
            .is_("revoked_at", "null")
            .execute()
        )
        rows = getattr(res, "data", None) or []
    except Exception as exc:  # noqa: BLE001
        if not _is_missing_relation(exc):
            logger.warning("purge: payment_methods lookup failed for user=%s: %s", user_id, exc)
        return 0

    for row in rows:
        method_id = row.get("payment_method_id")
        try:
            _mark_revoked(supabase, str(method_id))
        except Exception as exc:  # noqa: BLE001
            logger.error("purge: could not mark method=%s revoked: %s", method_id, exc)
        try:
            if revoke_token_at_provider(str(row.get("provider_token") or "")):
                confirmed += 1
            else:
                logger.error(
                    "purge: token for method=%s (user=%s) was NOT confirmed "
                    "revoked at Moyasar — revoke it from the dashboard",
                    method_id, user_id,
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("purge: provider revoke failed for method=%s: %s", method_id, exc)

    if rows:
        logger.info(
            "purge: revoked %d stored card(s) for user=%s (provider confirmed %d, reason=%s)",
            len(rows), user_id, confirmed, reason,
        )
    return confirmed


__all__ = [
    "RENEWABLE_PLAN_IDS",
    "RECURRING_CYCLE",
    "auto_renewal_enabled",
    "plan_renews",
    "requires_recurring_consent",
    "recurring_disclosure_ar",
    "consent_text_hash",
    "record_consent",
    "fetch_consent",
    "extract_card_token",
    "capture_payment_method",
    "get_active_method",
    "get_chargeable_method",
    "describe_method",
    "revoke_active_method",
    "revoke_method_row",
    "revoke_all_for_user_sync",
    "revoke_token_at_provider",
]
