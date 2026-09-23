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

Also ``163_payment_method_source_type.sql`` — ``payment_methods.source_type``,
which instrument a credential came from. That one is deliberately NOT a hard
dependency: both the read and the write fall back when the column is absent, so
this module works applied or unapplied, in either order. See
``_METHOD_OPTIONAL_COLUMNS`` and ``capture_payment_method``'s insert.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import httpx
from supabase import Client as SupabaseClient

from backend.app.services.audit_service import write_audit_log
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

# ── Instrument provenance (migration 163) ────────────────────────────────────
#
# WHY THIS EXISTS. Until 163 nothing anywhere recorded WHICH instrument a stored
# credential came from: capture is a pure duck-type on ``source.token``, so an
# Apple Pay wallet token would be stored as a plain card and the two populations
# would be indistinguishable forever after. That mattered the moment Apple Pay
# started returning tokens at all — a wallet credential is a DEVICE PAN behind a
# funding PAN, and if wallet renewals ever start declining differently from card
# renewals, this column is the only thing that makes the question answerable.
#
# ⚠ THE DOMAIN IS CHECK-CONSTRAINED IN 163. Moyasar is free to invent a fourth
# source type tomorrow, and a value outside this set would make the INSERT fail —
# i.e. it would cost us the whole stored credential (and therefore the renewal)
# to record a label. So the value is only PERSISTED when it is one of these;
# anything else is logged and stored as NULL. `extract_card_token` still carries
# the raw value out so the logs and the audit row tell the truth.
KNOWN_SOURCE_TYPES = frozenset({"creditcard", "applepay", "samsungpay"})

# Consent bookkeeping.
CONSENT_EVENT = "recurring_consent"
# Telemetry, NOT consent: a renewable purchase that produced no token at all.
# See ``capture_payment_method`` — this is the event that makes the "silently
# never enrolled" population queryable instead of only greppable.
NO_TOKEN_EVENT = "card_token_missing"
# v2 (2026-08-12): shortened to renewal-is-on + how-to-stop; the amount and
# cadence moved out of the hashed text and are carried by the /pay layout. v1
# consents keep their own hash and remain evidence of the longer text those
# users saw — which is the entire reason this is versioned rather than edited
# in place.
DISCLOSURE_VERSION = "v2"

# ── Arabic (rule 5) ──────────────────────────────────────────────────────────

#: THE disclosure, verbatim. A module constant since v2 because it no longer
#: interpolates plan name, price or term — which is what lets a completed
#: purchase carry a provable hash without a separate checkbox (see
#: ``capture_payment_method``). Change this and bump ``DISCLOSURE_VERSION``.
RECURRING_DISCLOSURE_AR = (
    "التجديد التلقائي مُفعّل على هذه الباقة، ويمكنك إيقاف التجديد في أي "
    "وقت من إعدادات الحساب."
)

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

    It states two things: that renewal is on, and how to stop it.

    ⚠ **THE AMOUNT AND CADENCE ARE DELIBERATELY NOT IN THIS STRING** (owner,
    2026-08-12 — the v1 wording was judged too heavy at the point of sale). They
    are still disclosed, but by the PAGE rather than by the hashed artefact:
    `/pay` renders «المبلغ المستحق 89.90» in the order summary and «فترة الاشتراك
    30 يوماً» under the plan name, both directly above this box. If that layout
    ever changes, this string becomes the only disclosure left and must take the
    numbers back — a recurring consent that never states a price is not worth
    much in a dispute.

    No absolute next-charge DATE either, for a different and permanent reason: at
    checkout it is not yet knowable, because ``grant_plan`` stacks a same-plan
    purchase onto a live term, so the real boundary is whatever ``expires_at``
    ends up being after the grant.

    ⚠ CHANGING ONE CHARACTER OF THIS STRING CHANGES THE HASH. That is the point
    (``consent_text_hash`` exists so a wording change stays provable), but bump
    ``DISCLOSURE_VERSION`` at the same time so the audit rows say which text a
    given user actually agreed to. Consents recorded under v1 keep v1's hash and
    remain valid evidence of the longer text those users were shown.
    """
    if not plan_renews(plan):
        return None
    return RECURRING_DISCLOSURE_AR


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

    Tokenization was ENABLED on the live merchant account on 2026-08-12 (Moyasar
    support, in writing). The field names below are now checked against the
    published contract at docs.moyasar.com/guides/tokenization — but NOT yet
    against a real response from this account, so treat the shape as documented,
    not observed.

    VERIFIED FROM THE DOCS — a payment created with ``save_card: true``::

        {"id": "…", "status": "initiated", "amount": 10000, "currency": "SAR",
         "source": {"type": "creditcard", "company": "visa", "name": "John Doe",
                    "number": "XXXX-XXXX-XXXX-1111",
                    "token": "token_qbmmXzo…",
                    "transaction_url": "…"}}

    ⚠ **THE PAYMENT RESPONSE CARRIES NO EXPIRY.** There is no ``month``/``year``
    inside that ``source``; only the TOKEN object does::

        GET /v1/tokens/:id →
        {"id": "token_…", "status": "active", "brand": "visa",
         "funding": "credit", "country": "SA", "month": "12", "year": "2030",
         "name": "…", "last_four": "1111", …}

    So ``exp_month``/``exp_year`` come back None from a save_card capture and the
    stored card renders without an expiry line. Fetching the token object after
    capture is what fixes that — and it is the only way to read ``status``, which
    matters because ONLY an ``active`` token can be charged (``initiated`` and
    ``inactive`` are rejected at the provider). Not wired yet.

    Note the two objects name the same things differently — ``company`` vs
    ``brand``, ``number`` vs ``last_four`` — so both spellings are accepted below
    and this function works on either.

    ⚠ WALLET SOURCES (Apple Pay / Samsung Pay) — verified with Moyasar in writing
    2026-09-22. An ``applepay`` source looks like::

        {"type": "applepay", "token": "<token or null>", "company": "visa",
         "number": "XXXX-XXXX-XXXX-2796", "dpan": "4326-9974-XXXX-2764", …}

    ``number``/``last_four`` and the expiry describe the **FUNDING** card (…2796);
    ``dpan`` is the device PAN (…2764) that actually processes. The parse below
    reads the funding PAN and that is DELIBERATE and CORRECT — …2796 is the number
    the user recognises from their bank statement, so it is the one «فيزا ••2796»
    must show. Do **not** "fix" this to read ``dpan``: it would replace a number
    the customer knows with one they have never seen.

    Returns ``{"provider_token", "brand", "last4", "exp_month", "exp_year",
    "source_type"}`` or None. Only ``provider_token`` is required; the display
    fields are best-effort and a missing one renders as «بطاقة محفوظة» rather
    than failing the capture.
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
    # ``number`` is the PAYMENT response's masked PAN. ``last_four`` is what the
    # TOKEN object (GET /v1/tokens/:id) calls the same thing — both are accepted
    # so this function works on either object.
    last4 = None
    number = (
        source.get("number")
        or source.get("last_four")
        or source.get("masked_number")
    )
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

    # Provenance (163). Carried RAW (lowercased/trimmed) rather than filtered to
    # KNOWN_SOURCE_TYPES here: a value we do not recognise is exactly the value a
    # log line and an audit row need to say out loud. The filtering to the CHECK
    # domain happens at the INSERT, where getting it wrong would cost a credential.
    # A token object (GET /v1/tokens/:id) carries no `type`, so the reuse of this
    # parser during enrichment yields None — the payment's source is the authority
    # on provenance and the enrichment deliberately never overwrites it.
    source_type = source.get("type")
    source_type = (
        source_type.strip().lower()[:32]
        if isinstance(source_type, str) and source_type.strip()
        else None
    )

    return {
        "provider_token": token,
        "brand": brand,
        "last4": last4,
        "exp_month": exp_month,
        "exp_year": exp_year,
        "source_type": source_type,
    }


#: Token statuses Moyasar will actually charge. Per
#: docs.moyasar.com/guides/tokenization/tokenized-cards only ``active`` is
#: chargeable — ``initiated`` and ``inactive`` are rejected at the provider.
TOKEN_STATUS_ACTIVE = "active"


def fetch_token_at_provider(token: str) -> Optional[dict]:
    """``GET /v1/tokens/{id}`` — the token object. SYNC — call via ``run_db``.

    Exists for two reasons, and the second is not cosmetic:

    1. **The expiry lives ONLY here.** A payment created with ``save_card: true``
       returns ``source.token`` but no ``month``/``year`` (verified against the
       docs), so without this call the stored card renders with no expiry line.
    2. **``status`` lives only here too**, and only an ``active`` token can be
       charged. Storing a token we could have known was unchargeable would show
       the user «مدى ••1234» in إعدادات الحساب, implying renewal is set up, while
       every renewal silently declines.

    Shape (per the docs)::

        {"id": "token_…", "status": "active", "brand": "visa",
         "funding": "credit", "country": "SA", "month": "12", "year": "2030",
         "name": "…", "last_four": "1111", "metadata": null, …}

    Returns the parsed object, or None on ANY failure. None means "unknown", NOT
    "bad" — the caller must treat it as an enrichment that didn't arrive and
    proceed, never as a reason to refuse a token. This call is not on the
    money path and must never become a gate we depend on.

    Sync for the same reason as ``revoke_token_at_provider``: one implementation,
    reachable from both the sync purge sweep and async callers via ``run_db``.
    """
    secret = (get_settings().MOYASAR_SECRET_KEY or "").strip()
    if not secret or not token:
        return None
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_S) as client:
            response = client.get(
                f"{MOYASAR_API_BASE}/tokens/{quote(str(token), safe='')}",
                auth=(secret, ""),
            )
    except httpx.HTTPError as exc:
        logger.warning("token fetch: transport failure (display fields degrade): %s", exc)
        return None

    if response.status_code != 200:
        logger.warning(
            "token fetch: Moyasar answered %d (%s) — display fields degrade and "
            "token status is unknown",
            response.status_code, response.text[:200],
        )
        return None
    try:
        parsed = response.json()
    except ValueError:
        logger.warning("token fetch: response was not JSON")
        return None
    return parsed if isinstance(parsed, dict) else None


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

# Columns added by a migration that MAY NOT BE APPLIED YET. Selected in a
# separate attempt so that a backend running ahead of 163 degrades to "we don't
# know the instrument" instead of to "you have no stored card" — which is what a
# bare 42703 would look like to ``get_active_method``'s missing-relation handler,
# i.e. a user with a perfectly good card being told, in إعدادات الحساب, that they
# have none. Delete this and fold ``source_type`` into the list above once 163 is
# applied everywhere.
_METHOD_OPTIONAL_COLUMNS = "source_type"


def _select_active(
    supabase: SupabaseClient, user_id: str, *, with_token: bool
) -> Optional[dict]:
    base = _METHOD_SECRET_COLUMNS if with_token else _METHOD_PUBLIC_COLUMNS

    def _read(columns: str) -> Optional[dict]:
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

    try:
        return _read(base + ", " + _METHOD_OPTIONAL_COLUMNS)
    except Exception as exc:  # noqa: BLE001
        # Same tolerance as the INSERT below, and for the same reason. If it was
        # the TABLE that was missing rather than the column, the retry raises the
        # identical error class and every caller's existing `_is_missing_relation`
        # handler still sees it — so this cannot mask a flag-off-before-132 deploy.
        if not _is_missing_relation(exc):
            raise
        return _read(base)


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

    ``source_type`` (163) is ``"creditcard" | "applepay" | "samsungpay" | null``
    and is DISPLAY PROVENANCE, not a second brand: a wallet purchase still carries
    the funding card's ``brand``/``last4``, so the settings dialog renders «Apple
    Pay» *plus* «فيزا ••2796» rather than one instead of the other. It is null for
    every row written before 163 and for every row written by a backend running
    ahead of it — the UI must treat null as "a card", not as a broken row, because
    that is what all existing rows are.
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
            "source_type": None,
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
        "source_type": row.get("source_type"),
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
      * no token in the provider payload — WARNING + an ``audit_logs`` row, see
        below. This is the only refusal that is a symptom rather than a policy.

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
            # ⚠ PRODUCTION INCIDENT, 2026-09-20 — READ BEFORE LOWERING THIS AGAIN.
            #
            # This line was logger.INFO with the message "save_card not requested",
            # and that pair of mistakes is why Apple Pay buyers went a month
            # without a single one of them being enrolled in auto-renewal. At INFO
            # it is indistinguishable from a `basic` purchase or a flag-off deploy,
            # so nothing ever stood out; and the message asserted a CAUSE this
            # function cannot possibly know — from here, a wallet payment whose
            # tokenization was never requested, a save_card that was requested and
            # refused by the provider, and a payload whose field names moved all
            # look exactly the same. It said "save_card not requested" for a month
            # while the truth was "we ask for it on the card path only".
            #
            # So: WARNING (a renewable purchase that stores nothing is a revenue
            # event, not a routine one), it names only what it can see, and it
            # carries `source.type` — because "which instrument" is the single
            # field that separates the populations and it was the field missing
            # from every log we had to go on.
            source = fetched.get("source") if isinstance(fetched, dict) else None
            source_type = (
                source.get("type") if isinstance(source, dict) else None
            ) or "unknown"
            logger.warning(
                "no card token on payment=%s source_type=%s plan=%s — nothing "
                "stored, so this subscription will NOT auto-renew",
                payment_id, source_type, plan_id,
            )
            # And an audit row, so the affected population is QUERYABLE rather
            # than greppable across log retention. Fire-and-forget on purpose —
            # the opposite of `_insert_consent_row`, which raises: a consent we
            # cannot record must stop the flow, while a telemetry row we cannot
            # record must never turn a successful purchase into an exception. The
            # money is already in and the plan is already granted by the time this
            # runs.
            await run_db(
                write_audit_log,
                supabase,
                user_id=str(user_id),
                action="update",
                resource_type="payment_transaction",
                resource_id=str(payment_id),
                metadata={
                    "event": NO_TOKEN_EVENT,
                    "plan_id": str(plan_id) if plan_id else None,
                    "source_type": source_type,
                    "provider_ref": fetched.get("id") if isinstance(fetched, dict) else None,
                },
            )
            return None

        # The consent artefact.
        #
        # v2 (owner, 2026-08-12): there is no consent CHECKBOX any more — the
        # disclosure renders as a plain reminder on /pay and the affirmative act
        # is completing the purchase after being shown it. So an explicit consent
        # row is no longer required, only preferred:
        #
        #   * an explicit row (someone who ticked the old checkbox, or any future
        #     caller of POST /payments/{id}/consent) WINS — it carries the real
        #     moment and the exact text that user saw, including v1's longer
        #     wording;
        #   * otherwise the completed purchase IS the consent, stamped at
        #     ``paid_at`` and hashed against RECURRING_DISCLOSURE_AR.
        #
        # That fallback is only sound because the disclosure is now a CONSTANT.
        # If it ever interpolates price or plan again, this hash stops being
        # reproducible from here and the served text must be persisted at
        # checkout instead — do not paper over it by hashing a rebuilt string.
        consent = await run_db(fetch_consent, supabase, str(payment_id))
        if not consent or not consent.get("consent_text_hash"):
            consent = {
                "consent_given_at": payment_row.get("paid_at") or _now_iso(),
                "consent_text_hash": consent_text_hash(RECURRING_DISCLOSURE_AR),
            }
            logger.info(
                "payment=%s carries no explicit consent row — treating the "
                "completed purchase as consent (disclosure %s)",
                payment_id, DISCLOSURE_VERSION,
            )

        existing = await run_db(_select_active_with_token_safe, supabase, str(user_id))
        if existing and existing.get("provider_token") == card["provider_token"]:
            return str(existing.get("payment_method_id"))       # the other path won

        # Enrich from the token object — AFTER the dedup short-circuit above, so
        # only the winning confirmation path spends a provider call.
        #
        # Two things come back that the payment response cannot give us: the
        # expiry, and `status`. On status the rule is asymmetric ON PURPOSE:
        #   * unknown (fetch failed) → PROCEED. The fetch is an enrichment and
        #     must never become a gate; a Moyasar hiccup here would otherwise
        #     silently stop every card from being saved.
        #   * known and not `active` → REFUSE. The provider will reject every
        #     charge against it, so storing it would put «مدى ••1234» in
        #     إعدادات الحساب — telling the user renewal is set up while every
        #     renewal declines. Better to store nothing and lapse like today.
        token_obj = await run_db(fetch_token_at_provider, card["provider_token"])
        if token_obj:
            status = str(token_obj.get("status") or "").strip().lower()
            if status and status != TOKEN_STATUS_ACTIVE:
                logger.error(
                    "payment=%s produced a token with status=%r, which Moyasar "
                    "will NOT charge — refusing to store it. If this repeats, the "
                    "save_card flow is minting save_only tokens and "
                    "capture_payment_method needs revisiting.",
                    payment_id, status,
                )
                return None
            # Fill only what the payment response left empty — the payment's own
            # `company`/`number` are equally authoritative and already parsed.
            #
            # ⚠ `token` is injected from `id`: the token object names its own id
            # `id`, while extract_card_token keys off `source.token`. Without this
            # the reuse silently returns None and the whole enrichment no-ops.
            enriched = extract_card_token(
                {"source": {**token_obj, "token": token_obj.get("id") or "x"}}
            ) or {}
            #
            # `source_type` is deliberately NOT in this list. Provenance is a fact
            # about the PAYMENT, and the token object carries no `type` of its own;
            # letting the enrichment write it would mean a field of the token
            # object we have not verified could relabel a wallet credential as a
            # card, which is precisely the confusion 163 exists to prevent.
            for field in ("brand", "last4", "exp_month", "exp_year"):
                if card.get(field) in (None, "") and enriched.get(field) not in (None, ""):
                    card[field] = enriched[field]

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

        # `source_type` (migration 163) — which instrument this credential came
        # from, promoted out of nowhere at all (before 163 the information was
        # simply discarded). Only written when it is inside 163's CHECK domain:
        # an unknown fourth source type would fail the INSERT, and losing the
        # whole credential — and therefore the renewal — to record a label would
        # be a far worse trade than a NULL.
        raw_source_type = card.get("source_type")
        if raw_source_type in KNOWN_SOURCE_TYPES:
            payload["source_type"] = raw_source_type
        elif raw_source_type:
            logger.warning(
                "payment=%s carries source.type=%r, which is outside 163's CHECK "
                "domain %s — storing the credential with source_type NULL. If this "
                "is a real new Moyasar instrument, widen the constraint.",
                payment_id, raw_source_type, sorted(KNOWN_SOURCE_TYPES),
            )

        try:
            row = await run_db(_insert_method, supabase, payload)
        except Exception as exc:  # noqa: BLE001
            text = str(exc).lower()
            if "source_type" in payload and _is_missing_relation(exc):
                # 163 is not applied yet. Exactly the `decline_reason` / migration
                # 133 pattern in `payment_service._mark_failed`: the column is a
                # reporting nicety, the row is the whole feature, and a backend
                # that refuses to store a card because it cannot label it would
                # reintroduce the very bug this column exists to make visible.
                logger.warning(
                    "payment=%s: insert rejected (%s) — retrying without "
                    "source_type; apply migration 163 to record it",
                    payment_id, exc,
                )
                payload.pop("source_type")
                # NOT followed by a `raise`. ⚠ The retry branch below returns and
                # the fall-through re-raises, so a branch that RECOVERS has to say
                # so by leaving `row` bound and skipping the raise — an `elif`
                # chain ending in `else: raise`, never a trailing bare `raise`.
                row = await run_db(_insert_method, supabase, payload)
            elif "23505" in text or "duplicate key" in text:
                # The other confirmation path inserted between our read and our
                # write. Its row is as good as ours.
                logger.info(
                    "card token for user=%s already stored by the other "
                    "confirmation path (payment=%s)", user_id, payment_id,
                )
                again = await run_db(_select_active_with_token_safe, supabase, str(user_id))
                return str((again or {}).get("payment_method_id") or "") or None
            else:
                raise

        # `source_type` is on this line for the same reason it is on the WARNING
        # above: brand=visa last4=2796 reads identically for a card and for an
        # Apple Pay wallet whose funding card is that visa, and telling the two
        # apart after the fact was impossible for a month.
        logger.info(
            "card token stored: user=%s payment=%s method=%s brand=%s last4=%s "
            "source_type=%s",
            user_id, payment_id, row.get("payment_method_id"),
            card.get("brand"), card.get("last4"), card.get("source_type"),
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
    "KNOWN_SOURCE_TYPES",
    "NO_TOKEN_EVENT",
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
