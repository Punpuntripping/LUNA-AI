"""Moyasar payments — pricing, confirmation, webhook, refund.

Plan: ``.claude/plans/moyasar_payments.md`` (Phase A config guard, Phase C
backend). Covers ``backend.app.services.payment_service`` end to end plus the
webhook's shared-secret guard in ``backend.app.api.payments``.

No live DB and no network: Supabase is a small in-memory PostgREST stand-in that
holds real rows and applies the filters, so the REAL insert/update/locate code
runs; Moyasar is monkeypatched at ``fetch_payment`` / ``refund_at_provider``.

The load-bearing assertions:
  * ``test_upgrade_credit_matches_plan_example`` — the money arithmetic
    (charge, credit, VAT split) is exactly what the plan promises.
  * ``test_paid_path_stamps_snapshot_before_grant`` — migration 113 split the
    snapshot out of ``grant_plan``; taking it AFTER the grant would snapshot the
    plan the user just bought and make an upgrade refund unrestorable.
  * ``test_refund_marks_refunded_before_revoking`` — ``revoke_plan_grant``
    raises on a row that still says paid.
  * ``test_webhook_*`` — a webhook must never 5xx on a content problem, and a
    forged/tampered event must never reach the DB.
  * ``test_live_key_outside_production_refuses_boot`` — the accident that
    charges real cards from a dev box.

The last section covers ``subscription_service`` (إلغاء الاشتراك,
`.claude/plans/subscription_cancellation.md`) — same FakeSupabase, because a
cancellation reads the subscription the payment path writes and the two only
make sense together (a re-purchase has to clear the opt-out flag).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services import payment_service as ps
from backend.app.services import subscription_service as ss
from backend.tests.promo_ladder_model import NOT_MINE, PromoLadderModel
from shared.config import get_settings

USER = "11111111-1111-1111-1111-111111111111"
OTHER_USER = "99999999-9999-9999-9999-999999999999"
MOYASAR_ID = "33333333-3333-3333-3333-333333333333"
WEBHOOK_SECRET = "whsec_test_value"
CUSTOMER_NAME = "محمد الفلاتة"
CUSTOMER_EMAIL = "buyer@example.com"


def run(coro):
    """Run one coroutine to completion (no pytest-asyncio dependency)."""
    return asyncio.run(coro)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# In-memory PostgREST + RPC stand-in
# ---------------------------------------------------------------------------

# The live catalog (2026-10-27): pro 94.90, max 289.90, basic 39.90 FLAT
# (migration 191 — basic is in no tier and has no promo, pricing_campaigns_v2.md
# §8). `promo_price_sar` is migration 138's single promo; for pro/max it is
# frozen at 49.90 / 99.90 for the legacy seats (§1.4-4) and only matters when a
# campaign is enabled, which it ships not to be — see FakeSupabase.__init__.
PLAN_ROWS = [
    {"plan_id": "free", "name_ar": "المجانية", "price_sar": None, "duration_days": None,
     "billing_cycle": None, "promo_price_sar": None},
    {"plan_id": "basic", "name_ar": "الأساسية", "price_sar": "39.90", "duration_days": 7,
     "billing_cycle": "one_time", "promo_price_sar": None},
    {"plan_id": "pro", "name_ar": "الاحترافية", "price_sar": "94.90", "duration_days": 30,
     "billing_cycle": "one_time", "promo_price_sar": "49.90"},
    {"plan_id": "max", "name_ar": "القصوى", "price_sar": "289.90", "duration_days": 30,
     "billing_cycle": "one_time", "promo_price_sar": "99.90"},
    {"plan_id": "marketing_lawyer", "name_ar": "عرض المحامين", "price_sar": None,
     "duration_days": 7, "billing_cycle": None, "promo_price_sar": None},
]


class _Result:
    def __init__(self, data: Any, count: Optional[int] = None):
        self.data = data
        # PostgREST's Content-Range total for `select(..., count="exact")`,
        # counted BEFORE `limit` exactly like the real header.
        self.count = count


class _Query:
    def __init__(self, db: "FakeSupabase", table: str):
        self.db, self.table = db, table
        self._op = "select"
        self._payload: Optional[dict] = None
        self._eq: list[tuple[str, Any]] = []
        self._in: list[tuple[str, list]] = []
        self._is_null: list[str] = []
        self._single = False
        self._limit: Optional[int] = None
        self._order: Optional[str] = None
        self._desc = False
        self._count: Optional[str] = None

    # builder ------------------------------------------------------------
    def select(self, *_a, count: Optional[str] = None, **_k):
        self._op = "select"
        self._count = count
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def eq(self, col, val):
        self._eq.append((col, val))
        return self

    def in_(self, col, vals):
        self._in.append((col, list(vals)))
        return self

    def is_(self, col, val):
        """PostgREST's IS filter. Only ``"null"`` is used in this codebase (the
        `revoked_at IS NULL` survey lookup), so only that is modelled."""
        assert val == "null", f"unsupported is_ value {val!r}"
        self._is_null.append(col)
        return self

    def order(self, col, desc=False):
        self._order, self._desc = col, desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def maybe_single(self):
        self._single = True
        return self

    # execution ----------------------------------------------------------
    def _matches(self, row: dict) -> bool:
        return (
            all(row.get(c) == v for c, v in self._eq)
            and all(row.get(c) in vals for c, vals in self._in)
            and all(row.get(c) is None for c in self._is_null)
        )

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])

        if self._op == "insert":
            row = dict(self._payload)
            # Each table's own PK default, so a survey row does not come back
            # wearing a payment_id.
            if self.table == "payment_transactions":
                row.setdefault("payment_id", str(uuid.uuid4()))
                # Migration 132: `initiated_by text NOT NULL DEFAULT 'user'`.
                # Modelled here because _expire_open_checkouts now filters on
                # it — an ADD COLUMN … NOT NULL DEFAULT backfills every existing
                # row, so in prod there is no such thing as a NULL here.
                row.setdefault("initiated_by", "user")
            else:
                row.setdefault("id", str(uuid.uuid4()))
            row.setdefault("created_at", _iso(_now()))
            rows.append(row)
            self.db.writes.append((self.table, "insert"))
            return _Result([dict(row)])

        matched = [r for r in rows if self._matches(r)]

        if self._op == "update":
            for r in matched:
                r.update(self._payload)
            self.db.writes.append((self.table, "update"))
            return _Result([dict(r) for r in matched])

        if self._order:
            matched.sort(key=lambda r: str(r.get(self._order) or ""), reverse=self._desc)
        total = len(matched) if self._count == "exact" else None
        if self._limit is not None:
            matched = matched[: self._limit]
        if self._single:
            return _Result(dict(matched[0]) if matched else None, total)
        return _Result([dict(r) for r in matched], total)


class FakeSupabase(PromoLadderModel):
    """Rows + the payment RPCs, with their real preconditions.

    The seat + price functions (138/182/183/192) are the shared MODEL in
    ``backend/tests/promo_ladder_model.py`` — one transcription for this fake
    and the renewal suite's, switched by ``db.ladder_v2``.
    """

    def __init__(self, subscription: Optional[dict] = None):
        self.tables: dict[str, list[dict]] = {
            "plans": [dict(p) for p in PLAN_ROWS],
            "user_subscriptions": [dict(subscription)] if subscription else [],
            "payment_transactions": [],
            "audit_logs": [],
            # The exit-survey ledger (migration 120).
            "subscription_cancellations": [],
            # The buyer's identity row — read at checkout and snapshotted onto
            # the payment (117), so the record survives the account.
            # `profession_group` (115) is what عرض الشهر الأول keys on (183).
            "users": [{"user_id": USER, "full_name_ar": CUSTOMER_NAME,
                       "email": CUSTOMER_EMAIL, "profession_group": None}],
        }
        self.calls: list[str] = []      # rpc + write sequence, in order
        self.writes: list[tuple] = []
        # Every RPC's params, in order — for assertions on WHAT was asked (the
        # renewal job's `p_at`), not just that something was.
        self.rpc_params: list[tuple[str, dict]] = []

        # ── المشتركون الأوائل (migration 138) ───────────────────────────────
        # 138 ships `early_adopter_campaign.enabled = false`, so the default
        # here is deploy-day production: effective price == list price, every
        # claim answers 'campaign_disabled', nobody holds a seat and NOTHING in
        # this suite changes. Set `db.campaign_enabled = True` to model an open
        # campaign.
        self.campaign_enabled = False
        self.campaign_seat_limit = 100
        self.campaign_promo_days = 90
        self.early_adopter_seats: list[dict] = []
        # The singleton `early_adopter_campaign` row. Set False to model the
        # broken install `campaign_disabled` now means — NOT the flag being off,
        # which is `campaign_enabled` above and no longer refuses a claim.
        self.campaign_row_present = True

        # ── عرض الشهر الأول (migration 183) ─────────────────────────────────
        # Ships `intro_offer_campaign.enabled = false` like 138 did, so the
        # default here is deploy-day production: every quote is empty and
        # nothing in this suite changes. Set `db.intro_offer_enabled = True`
        # (plus an open early-adopter campaign and a profession) to quote it.
        self.intro_offer_enabled = False
        self.intro_offer_ends_at = _now() + timedelta(days=60)
        self.intro_offer_prices = {"legal": "1.99", "specialist": "9.90",
                                   "entrepreneur": "9.90"}

        # ── pricing campaigns v2 (migration 192) ────────────────────────────
        # `db.ladder_v2 = True` switches the model to price tiers + batches;
        # `db.promo_batches` is the seeded §1.2 table, edited like an UPDATE.
        self._init_promo_model()

    def table(self, name: str) -> _Query:
        self._sync_promo_table(name)
        return _Query(self, name)

    # -- RPCs ------------------------------------------------------------
    def rpc(self, name: str, params: dict):
        self.calls.append(name)
        self.rpc_params.append((name, dict(params)))
        payment_id = params.get("p_payment_id")
        row = next(
            (r for r in self.tables["payment_transactions"] if r.get("payment_id") == payment_id),
            None,
        )

        if name == "stamp_payment_prior_snapshot":
            prior = None
            if row is not None and not row.get("fulfilled_at"):
                sub = self.tables["user_subscriptions"]
                if sub and sub[0].get("plan_id") != row.get("plan_id"):
                    prior = sub[0].get("plan_id")
                    row["prior_plan_id"] = prior
                    row["prior_expires_at"] = sub[0].get("expires_at")
            return _Rpc([{"prior_plan_id": prior}])

        if name == "grant_plan":
            if row is None:
                raise RuntimeError("payment_not_found")
            if row.get("status") != "paid":
                raise RuntimeError("payment_not_paid")
            if row.get("fulfilled_at"):
                # Retry — already applied, current state unchanged.
                return _Rpc([{"plan_id": row["plan_id"], "name_ar": "x",
                              "expires_at": row.get("_granted_expiry")}])
            expiry = _iso(_now() + timedelta(days=30))
            row["fulfilled_at"] = _iso(_now())
            row["_granted_expiry"] = expiry
            # MERGE, never replace: the real RPC is an INSERT … ON CONFLICT DO
            # UPDATE that names its columns (plan_id, source, started_at,
            # expires_at, redeemed_code), so every OTHER column survives a
            # grant — renewal_cancelled_at included. A fake that rebuilt the row
            # would clear the opt-out flag by accident and let
            # test_paid_fulfilment_clears_the_renewal_flag pass with the
            # clear_renewal_cancellation call deleted.
            patch = {"user_id": row["user_id"], "plan_id": row["plan_id"],
                     "source": "payment", "expires_at": expiry}
            subs = self.tables.setdefault("user_subscriptions", [])
            existing = next(
                (s for s in subs if s.get("user_id") == row["user_id"]), None
            )
            if existing is None:
                subs.append(patch)
            else:
                existing.update(patch)
            return _Rpc([{"plan_id": row["plan_id"], "name_ar": "x", "expires_at": expiry}])

        if name == "revoke_plan_grant":
            # Branch order mirrors 113's real body exactly (revoked → fulfilled →
            # no_subscription → PLAN-MATCH GUARD → restore → subtract). The guard
            # is what M-1 turns on, so a fake that skipped it would let the
            # exploit pass its own test.
            if row is None:
                return _Rpc([{"action": "payment_not_found"}])
            if row.get("status") != "refunded":
                # The real RPC raises this — the ordering guard under test.
                raise RuntimeError("payment_not_refunded")
            if row.get("revoked_at"):
                return _Rpc([{"action": "already_revoked"}])
            row["revoked_at"] = _iso(_now())
            subs = self.tables["user_subscriptions"]
            current = subs[0].get("plan_id") if subs else None
            if not row.get("fulfilled_at"):
                action = "not_fulfilled"          # money in, plan never applied
            elif current is None:
                action = "no_subscription"
            elif current != row.get("plan_id"):
                # The user moved on — subtracting would eat the plan they hold.
                # NOTHING is written to the subscription. This is M-1's branch.
                action = "plan_switched"
            elif row.get("prior_plan_id"):
                subs[0]["plan_id"] = row["prior_plan_id"]
                subs[0]["expires_at"] = row.get("prior_expires_at")
                action = "restored"
            else:
                action = "subtracted"
            return _Rpc([{"plan_id": row["plan_id"], "name_ar": "x",
                          "expires_at": None, "action": action}])

        if name == "stamp_usage_reset":
            # Mirrors migration 137. Three things the real RPC does that this
            # fake must too, or a test here will pass over a bug there:
            #   * EVERY paid purchase resets — upgrade, renewal, or re-purchase
            #     after a lapse. 131 gated this on a price increase and that is
            #     what left a renewing customer blocked on 2026-08-16; there is
            #     no price comparison left to mirror.
            #   * the stamp is the payment's `paid_at`, never `now()`, so the
            #     replayed paid path (webhook + /verify) writes the identical
            #     value instead of silently erasing points spent in between.
            #   * it only ever moves forward, so an out-of-order replay of an
            #     older payment cannot rewind a newer reset.
            if row is None:
                return _Rpc([{"action": "payment_not_found"}])
            stamp = row.get("paid_at")
            if not stamp:
                return _Rpc([{"action": "not_paid"}])
            subs = self.tables.get("user_subscriptions") or []
            if not subs:
                return _Rpc([{"action": "no_subscription"}])
            prev = subs[0].get("usage_reset_at")
            if prev is None or stamp > prev:
                subs[0]["usage_reset_at"] = stamp
            return _Rpc([{"action": "reset"}])

        # ── migrations 138/182/183/192 — seats, prices, the quote ─────────
        # A MODEL of the SQL, shared with the renewal suite's fake (see
        # promo_ladder_model.py). Anything that turns on capacity, the advisory
        # lock or the promo window must be re-verified against the migrations
        # themselves before it is believed.
        answer = self.promo_rpc(name, params, row)
        if answer is not NOT_MINE:
            return _Rpc(answer)

        raise AssertionError(f"unexpected rpc {name}")


class _Rpc:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return _Result(self._data)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def keys(monkeypatch):
    """Test-mode Moyasar keys + webhook secret in the settings cache."""
    monkeypatch.setenv("MOYASAR_SECRET_KEY", "sk_test_abc")
    monkeypatch.setenv("MOYASAR_PUBLISHABLE_KEY", "pk_test_abc")
    monkeypatch.setenv("MOYASAR_WEBHOOK_SECRET", WEBHOOK_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def moyasar_payment(payment_id: str, *, status="paid", amount=9490, currency="SAR", live=False):
    """A Moyasar payment object as `GET /v1/payments/{id}` returns it."""
    return {
        "id": MOYASAR_ID,
        "status": status,
        "amount": amount,
        "currency": currency,
        "live": live,
        "metadata": {"payment_id": payment_id},
        "source": {"type": "creditcard", "message": "Insufficient funds"},
    }


def patch_fetch(monkeypatch, payload=None, exc: Optional[Exception] = None):
    async def _fetch(_moyasar_id):
        if exc:
            raise exc
        return payload

    monkeypatch.setattr(ps, "fetch_payment", _fetch)


def sub(
    plan_id="free",
    source="signup",
    days_left: Optional[float] = None,
    cancelled_at: Optional[str] = None,
):
    expires = _iso(_now() + timedelta(days=days_left)) if days_left is not None else None
    return {"user_id": USER, "plan_id": plan_id, "source": source,
            "expires_at": expires, "status": "active",
            # migration 120 — NULL means renewal is on, which is every row today.
            "renewal_cancelled_at": cancelled_at}


def checkout(db, plan_id="pro", user_id=USER):
    """Open a checkout — and FORGET the RPCs it made.

    Checkout is setup for most of the tests below, and since migration 138 it
    prices through ``effective_plan_price`` (twice on an upgrade: the new plan,
    then the old one for the credit). Every ``db.calls`` assertion in this file
    is about what CONFIRMATION does, so the pricing calls are dropped here
    rather than repeated in every expected list. What checkout PRICED is still
    asserted, on ``amount_sar``/``credit_sar``, by the tests just below.
    """
    result = run(ps.create_checkout(db, user_id, plan_id))
    db.calls.clear()
    return result


def paid_row(db, plan_id="pro", amount="94.90", **over):
    """A payment_transactions row already marked paid."""
    row = {
        "payment_id": str(uuid.uuid4()), "user_id": USER, "plan_id": plan_id,
        "amount_sar": amount, "currency": "SAR", "status": "paid", "provider": "moyasar",
        "provider_ref": MOYASAR_ID, "paid_at": _iso(_now() - timedelta(hours=2)),
        "fulfilled_at": _iso(_now() - timedelta(hours=2)),
        "created_at": _iso(_now() - timedelta(hours=2)),
        "vat_amount_sar": "12.38", "net_amount_sar": "82.52", "upgrade_credit_sar": "0.00",
        # Moyasar reports its own fee (halalas) on the payment object; the
        # refund deduction is computed FROM this, not from a rate we assume.
        # 173 = the 1.73 SAR observed on a real sandbox mada charge.
        "raw_payload": {"id": MOYASAR_ID, "fee": 173},
    }
    row.update(over)
    db.tables["payment_transactions"].append(row)
    return row


# ═══════════════════════════════════════════════════════════════════════════
# Phase A — the boot-time mode guard
# ═══════════════════════════════════════════════════════════════════════════


def _boot(monkeypatch, **env):
    for var in ("MOYASAR_SECRET_KEY", "MOYASAR_PUBLISHABLE_KEY", "APP_ENV", "ENVIRONMENT"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    try:
        ps.verify_moyasar_config()
    finally:
        get_settings.cache_clear()


def test_no_keys_boots_cleanly(monkeypatch):
    """Payments unconfigured is a normal state — the app must still start."""
    _boot(monkeypatch)


def test_matching_test_keys_boot_in_dev(monkeypatch):
    _boot(monkeypatch, MOYASAR_SECRET_KEY="sk_test_a", MOYASAR_PUBLISHABLE_KEY="pk_test_a")


def test_mixed_key_modes_refuse_boot(monkeypatch):
    with pytest.raises(RuntimeError, match="mode mismatch"):
        _boot(
            monkeypatch,
            MOYASAR_SECRET_KEY="sk_live_a",
            MOYASAR_PUBLISHABLE_KEY="pk_test_a",
            APP_ENV="production",
            ENVIRONMENT="production",
        )


def test_live_key_outside_production_refuses_boot(monkeypatch):
    """The accident this guard exists for: a live key charges real cards, and
    api.moyasar.com serves both modes from the same host."""
    with pytest.raises(RuntimeError, match="live"):
        _boot(monkeypatch, MOYASAR_SECRET_KEY="sk_live_a", MOYASAR_PUBLISHABLE_KEY="pk_live_a")


def test_live_keys_boot_in_production(monkeypatch):
    _boot(
        monkeypatch,
        MOYASAR_SECRET_KEY="sk_live_a",
        MOYASAR_PUBLISHABLE_KEY="pk_live_a",
        ENVIRONMENT="production",
    )


def test_unrecognized_key_prefix_refuses_boot(monkeypatch):
    with pytest.raises(RuntimeError, match="unrecognized prefix"):
        _boot(monkeypatch, MOYASAR_SECRET_KEY="oops_abc")


# ═══════════════════════════════════════════════════════════════════════════
# Money arithmetic
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "charge,halalas,net,vat",
    [("49.90", 4990, "43.39", "6.51"),
     ("89.90", 8990, "78.17", "11.73"),
     ("289.90", 28990, "252.09", "37.81")],
)
def test_vat_split_matches_plan_table(charge, halalas, net, vat):
    n, v = ps.vat_split(ps.q2(charge))
    assert ps.to_halalas(charge) == halalas
    assert f"{n:.2f}" == net and f"{v:.2f}" == vat
    assert n + v == ps.q2(charge)          # inclusive: the parts rebuild the whole


def test_halalas_never_uses_float_multiplication():
    """0.1 * 100 == 10.000000000000002 in binary float — the ×100 trap."""
    assert ps.to_halalas(0.1) == 10
    assert ps.to_halalas("49.90") == 4990
    assert ps.to_halalas(49.9) == 4990


# ═══════════════════════════════════════════════════════════════════════════
# Checkout
# ═══════════════════════════════════════════════════════════════════════════


def test_checkout_prices_from_the_catalog(keys):
    db = FakeSupabase(sub("free"))
    result = checkout(db, "pro")
    assert result["amount_halalas"] == 9490
    assert result["amount_sar"] == "94.90"
    assert result["credit_sar"] == "0.00"
    assert result["publishable_key"] == "pk_test_abc"
    assert result["callback_url"].endswith("/pay/callback")

    row = db.tables["payment_transactions"][0]
    assert row["status"] == "initiated" and row["provider"] == "moyasar"
    assert (row["vat_amount_sar"], row["net_amount_sar"]) == ("12.38", "82.52")
    # The snapshot columns belong to the RPC, never to checkout.
    assert "prior_plan_id" not in row and "prior_expires_at" not in row


def test_upgrade_credit_matches_plan_example(keys):
    """pro with 26 days left → max: 289.90 − round(26/30 × 94.90, 2) = 207.65.

    Flag off (ladder_v2 down): the old term is valued at its 'current' price.
    The v2 basis — the cash actually paid — is pinned in the pricing-campaigns
    section at the end of this file."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    result = checkout(db, "max")
    assert result["credit_sar"] == "82.25"
    assert result["amount_sar"] == "207.65"
    assert result["amount_halalas"] == 20765
    row = db.tables["payment_transactions"][0]
    assert row["upgrade_credit_sar"] == "82.25"
    # VAT is split on the CHARGED amount, not the catalog price.
    assert row["vat_amount_sar"] == "27.08" and row["net_amount_sar"] == "180.57"


def test_code_sourced_plan_earns_no_credit(keys):
    """Otherwise a promo code silently converts into a cash discount."""
    db = FakeSupabase(sub("pro", source="code", days_left=26))
    assert checkout(db, "max")["credit_sar"] == "0.00"


def test_same_plan_repurchase_earns_no_credit(keys):
    """grant_plan stacks the days instead — crediting them would pay twice."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    assert checkout(db, "pro")["amount_sar"] == "94.90"


def test_expired_plan_earns_no_credit(keys):
    db = FakeSupabase(sub("pro", source="payment", days_left=-1))
    assert checkout(db, "max")["credit_sar"] == "0.00"


def test_downgrade_is_blocked(keys):
    db = FakeSupabase(sub("max", source="payment", days_left=10))
    with pytest.raises(LunaHTTPException) as exc:
        checkout(db, "pro")
    assert exc.value.status_code == 409
    assert exc.value.code is ErrorCode.PAYMENT_DOWNGRADE_BLOCKED
    assert not db.tables["payment_transactions"]      # no row on a refusal


def test_downgrade_allowed_once_expired(keys):
    db = FakeSupabase(sub("max", source="payment", days_left=-1))
    assert checkout(db, "basic")["amount_halalas"] == 3990


def test_marketing_plan_does_not_block_a_purchase(keys):
    db = FakeSupabase(sub("marketing_lawyer", source="code", days_left=5))
    assert checkout(db, "basic")["amount_halalas"] == 3990


@pytest.mark.parametrize("plan_id", ["free", "marketing_lawyer", "platinum"])
def test_unpurchasable_plans_rejected(keys, plan_id):
    db = FakeSupabase(sub("free"))
    with pytest.raises(LunaHTTPException) as exc:
        checkout(db, plan_id)
    assert exc.value.status_code == 400
    assert exc.value.code is ErrorCode.PAYMENT_PLAN_NOT_PURCHASABLE


def test_checkout_503s_when_unconfigured(monkeypatch):
    # delenv alone is not enough: pydantic-settings ALSO reads the repo .env
    # file, which carries real test keys on dev machines. Null the attributes
    # on the cached instance so the test is hermetic regardless of .env.
    monkeypatch.delenv("MOYASAR_SECRET_KEY", raising=False)
    monkeypatch.delenv("MOYASAR_PUBLISHABLE_KEY", raising=False)
    get_settings.cache_clear()
    _s = get_settings()
    monkeypatch.setattr(_s, "MOYASAR_SECRET_KEY", None)
    monkeypatch.setattr(_s, "MOYASAR_PUBLISHABLE_KEY", None)
    try:
        with pytest.raises(LunaHTTPException) as exc:
            checkout(FakeSupabase(sub("free")), "pro")
        assert exc.value.status_code == 503
        assert exc.value.code is ErrorCode.SERVICE_UNAVAILABLE
    finally:
        get_settings.cache_clear()


def test_checkout_snapshots_the_customer_identity(keys):
    """117: `user_id` is ON DELETE SET NULL, so a purged account leaves this row
    standing with its money and its sequential receipt_no intact. These two
    columns are then the ONLY thing that can say whose payment it was, which is
    why they are stamped at initiation rather than resolved from users later."""
    db = FakeSupabase(sub("free"))
    checkout(db, "pro")

    row = db.tables["payment_transactions"][0]
    assert row["customer_name_snapshot"] == CUSTOMER_NAME
    assert row["customer_email_snapshot"] == CUSTOMER_EMAIL


def test_checkout_survives_a_missing_identity_row(keys):
    """An unidentified receipt is bad; a checkout that 500s on a caller who is
    already authenticated is worse. No users row → NULL snapshots, sale opens."""
    db = FakeSupabase(sub("free"))
    db.tables["users"] = []

    result = checkout(db, "pro")

    row = db.tables["payment_transactions"][0]
    assert result["payment_id"] == row["payment_id"]
    assert row["customer_name_snapshot"] is None
    assert row["customer_email_snapshot"] is None


# ═══════════════════════════════════════════════════════════════════════════
# /verify
# ═══════════════════════════════════════════════════════════════════════════


def test_verify_initiated_stores_provider_ref_without_granting(keys, monkeypatch):
    """The pre-3DS on_completed call: 3DS destroys the page, so the provider id
    must be persisted before the redirect or an abandoned payment is lost."""
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid, status="initiated"))

    result = run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert result == {"status": "pending", "payment_id": pid, "granted": False,
                      "provider_status": "initiated"}
    row = db.tables["payment_transactions"][0]
    assert row["provider_ref"] == MOYASAR_ID and row["status"] == "initiated"
    assert "grant_plan" not in db.calls


def test_verify_paid_grants_the_plan(keys, monkeypatch):
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid))

    result = run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert result["status"] == "paid" and result["granted"] is True
    row = db.tables["payment_transactions"][0]
    assert row["status"] == "paid" and row["paid_at"] and row["fulfilled_at"]
    assert db.tables["user_subscriptions"][0]["plan_id"] == "pro"


def test_paid_path_stamps_snapshot_before_grant(keys, monkeypatch):
    """113 left grant_plan alone, so the prior-subscription snapshot is its own
    RPC — and it MUST run first: grant_plan overwrites the row it snapshots."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    pid = checkout(db, "max")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=20765))

    run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert db.calls == [
        # A credited row is re-priced from live state before it grants (H-4
        # layer 3): the new plan, then the old one for the credit — both
        # through effective_plan_price since 138.
        "effective_plan_price", "effective_plan_price",
        "stamp_payment_prior_snapshot", "grant_plan", "stamp_usage_reset",
        # …and the seat is claimed AFTER the grant, never inside it.
        "claim_early_adopter_seat",
    ]
    row = db.tables["payment_transactions"][0]
    assert row["prior_plan_id"] == "pro"      # what a refund would restore


def test_renewal_of_the_same_plan_resets_usage(keys, monkeypatch):
    """137's headline change, and the one 131 got wrong.

    A renewal is the most common paid event in the product, and under 131 it was
    the one that never reset: 113 writes prior_plan_id NULL for a same-plan
    restack, NULL prior price read as `not_an_upgrade`, and the customer walked
    into their new cycle still carrying last cycle's spend. `usage_reset_at` MUST
    move to paid_at here.
    """
    db = FakeSupabase(sub("pro", source="payment", days_left=3))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid))

    run(ps.verify_payment(db, USER, MOYASAR_ID))
    paid_at = db.tables["payment_transactions"][0]["paid_at"]
    assert db.tables["user_subscriptions"][0]["usage_reset_at"] == paid_at


def test_purchase_after_a_lapsed_higher_plan_resets_usage(keys, monkeypatch):
    """The 2026-08-16 incident, as a test.

    Her subscription row still said `max` — expired, so she was being enforced as
    `free` — and 113 snapshots plan_id RAW, with no expiry check. Under 131's rank
    gate her `basic` purchase therefore read as max→basic, a downgrade, and reset
    nothing: she paid 49.90 and was blocked again four minutes later on the same
    window. 137 removed the comparison, so the raw-vs-effective mismatch in the
    snapshot can no longer reach the meters.
    """
    db = FakeSupabase(sub("max", source="payment", days_left=-5))   # lapsed
    pid = checkout(db, "basic")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=3990))

    run(ps.verify_payment(db, USER, MOYASAR_ID))
    row = db.tables["payment_transactions"][0]
    assert row["prior_plan_id"] == "max"        # the snapshot still reads raw…
    assert db.tables["user_subscriptions"][0]["usage_reset_at"] == row["paid_at"]


def test_usage_reset_is_idempotent_by_value_across_both_paths(keys, monkeypatch):
    """The trap 131 named and 137 keeps: the stamp is `paid_at`, never `now()`.

    Webhook and /verify both drive the paid path by design. A `now()` stamp would
    write a LATER floor on the replay and silently erase every point spent between
    the two runs — usage the customer legitimately consumed on the plan they just
    bought. Two runs, one value.
    """
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid))

    run(ps.verify_payment(db, USER, MOYASAR_ID))
    first = db.tables["user_subscriptions"][0]["usage_reset_at"]
    run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert db.tables["user_subscriptions"][0]["usage_reset_at"] == first


def test_verify_is_idempotent(keys, monkeypatch):
    """Both confirmation paths can fire; the second must not extend the term."""
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid))

    first = run(ps.verify_payment(db, USER, MOYASAR_ID))
    second = run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert first["expires_at"] == second["expires_at"]
    assert db.tables["payment_transactions"][0]["status"] == "paid"


def test_verify_failed_marks_the_row(keys, monkeypatch):
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid, status="failed"))

    result = run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert result["status"] == "failed" and result["granted"] is False
    assert db.tables["payment_transactions"][0]["status"] == "failed"
    assert "grant_plan" not in db.calls


def test_verify_refuses_another_users_payment(keys, monkeypatch):
    """Plan trap 6: ?id= is attacker-controllable, so the row must be bound to
    the caller. 404 (not 403) so it cannot be used as an existence oracle."""
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid))

    with pytest.raises(LunaHTTPException) as exc:
        run(ps.verify_payment(db, OTHER_USER, MOYASAR_ID))
    assert exc.value.status_code == 404
    assert exc.value.code is ErrorCode.PAYMENT_NOT_FOUND
    assert db.tables["payment_transactions"][0]["status"] == "initiated"


def test_verify_rejects_a_tampered_amount(keys, monkeypatch):
    """A client that edited the form's amount pays 1 SAR for max — and gets
    nothing, because the fetched amount is checked against OUR row."""
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "max")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=100))

    with pytest.raises(LunaHTTPException) as exc:
        run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert exc.value.status_code == 400
    assert exc.value.code is ErrorCode.PAYMENT_PROVIDER_ERROR
    assert db.calls == []


def test_verify_rejects_a_foreign_currency(keys, monkeypatch):
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid, currency="USD"))
    with pytest.raises(LunaHTTPException):
        run(ps.verify_payment(db, USER, MOYASAR_ID))


def test_verify_unknown_id_is_404(keys, monkeypatch):
    """An id from the other key mode is simply not fetchable — that IS the
    test/live isolation."""
    db = FakeSupabase(sub("free"))
    patch_fetch(monkeypatch, exc=ps.MoyasarNotFound("404"))
    with pytest.raises(LunaHTTPException) as exc:
        run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert exc.value.status_code == 404


def test_verify_provider_outage_is_503(keys, monkeypatch):
    db = FakeSupabase(sub("free"))
    patch_fetch(monkeypatch, exc=ps.MoyasarUnavailable("timeout"))
    with pytest.raises(LunaHTTPException) as exc:
        run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert exc.value.status_code == 503


def test_verify_live_flag_mismatch_refuses(keys, monkeypatch):
    """A live payment must never grant on a test-key backend."""
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid, live=True))
    with pytest.raises(LunaHTTPException) as exc:
        run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert exc.value.code is ErrorCode.PAYMENT_NOT_FOUND
    assert db.calls == []


# ═══════════════════════════════════════════════════════════════════════════
# Webhook
# ═══════════════════════════════════════════════════════════════════════════


def _authorized(body: dict) -> bool:
    from backend.app.api.payments import _webhook_authorized

    return _webhook_authorized(body)


def test_webhook_secret_fail_closed(monkeypatch):
    monkeypatch.delenv("MOYASAR_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()
    try:
        assert _authorized({"secret_token": "anything"}) is False
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "token",
    [pytest.param("wrong", id="wrong"),
     pytest.param(None, id="absent"),
     pytest.param(12345, id="non_string"),
     pytest.param(WEBHOOK_SECRET[:6], id="prefix"),
     # compare_digest raises TypeError on non-ASCII str -> a 500 Moyasar retries.
     pytest.param("رمز-خاطئ", id="non_ascii")],
)
def test_webhook_bad_secret_rejected(keys, token):
    assert _authorized({"secret_token": token} if token is not None else {}) is False


def test_webhook_correct_secret_accepted(keys):
    assert _authorized({"secret_token": WEBHOOK_SECRET}) is True


def _event(pid: str, event="payment_paid", live=False):
    return {"type": event, "live": live, "secret_token": WEBHOOK_SECRET,
            "data": {"id": MOYASAR_ID, "metadata": {"payment_id": pid}}}


def test_webhook_paid_grants(keys, monkeypatch):
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid))

    result = run(ps.handle_webhook_event(db, _event(pid)))
    assert result["status"] == "paid" and result["granted"] is True
    assert db.calls == ["stamp_payment_prior_snapshot", "grant_plan",
                        "stamp_usage_reset", "claim_early_adopter_seat"]


def test_webhook_retry_grants_exactly_once(keys, monkeypatch):
    """Moyasar retries 5×; fulfilled_at must make every replay a no-op."""
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid))

    for _ in range(3):
        run(ps.handle_webhook_event(db, _event(pid)))
    expiry = db.tables["user_subscriptions"][0]["expires_at"]
    assert db.tables["payment_transactions"][0]["_granted_expiry"] == expiry


def test_webhook_ignores_unhandled_events(keys, monkeypatch):
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid))
    for event in ("payment_authorized", "payment_captured", "payment_voided",
                  "payment_verified", ""):
        result = run(ps.handle_webhook_event(db, _event(pid, event=event)))
        assert result["status"] == "ignored" and result["reason"] == "unhandled_event"
    assert db.calls == []


def test_webhook_mode_mismatch_never_touches_the_db(keys, monkeypatch):
    """The ⚠ live-flag trap: one URL registered for both dashboard modes must
    not let a sandbox payment grant a real subscription."""
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid))

    result = run(ps.handle_webhook_event(db, _event(pid, live=True)))
    assert result == {"status": "ignored", "reason": "mode_mismatch"}
    assert db.calls == [] and db.tables["payment_transactions"][0]["status"] == "initiated"


def test_webhook_forged_amount_never_grants(keys, monkeypatch):
    """Right secret_token, wrong amount in the body: the re-fetch is the only
    evidence, and it disagrees with our row."""
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "max")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=100))

    result = run(ps.handle_webhook_event(db, _event(pid)))
    assert result["reason"] == "amount_mismatch"
    assert db.calls == []


def test_webhook_unknown_payment_is_200(keys, monkeypatch):
    """Never 5xx a replay — a non-2xx burns one of only 5 retries."""
    db = FakeSupabase(sub("free"))
    patch_fetch(monkeypatch, moyasar_payment(str(uuid.uuid4())))
    result = run(ps.handle_webhook_event(db, _event(str(uuid.uuid4()))))
    assert result["reason"] == "unknown_payment"


def test_webhook_provider_404_is_200(keys, monkeypatch):
    db = FakeSupabase(sub("free"))
    patch_fetch(monkeypatch, exc=ps.MoyasarNotFound("404"))
    result = run(ps.handle_webhook_event(db, _event(str(uuid.uuid4()))))
    assert result["reason"] == "provider_not_found"


def test_webhook_provider_outage_asks_for_a_retry(keys, monkeypatch):
    """The one deliberate non-2xx: money moved, our books didn't, and another
    attempt genuinely helps."""
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, exc=ps.MoyasarUnavailable("timeout"))
    with pytest.raises(ps.WebhookRetryable):
        run(ps.handle_webhook_event(db, _event(pid)))


def test_webhook_failed_marks_the_row(keys, monkeypatch):
    db = FakeSupabase(sub("free"))
    pid = checkout(db, "pro")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid, status="failed"))
    result = run(ps.handle_webhook_event(db, _event(pid, event="payment_abandoned")))
    assert result["status"] == "failed"
    assert db.tables["payment_transactions"][0]["status"] == "failed"


def test_webhook_refunded_revokes_the_grant(keys, monkeypatch):
    db = FakeSupabase(sub("pro", source="payment", days_left=30))
    row = paid_row(db)
    patch_fetch(monkeypatch, moyasar_payment(row["payment_id"], status="refunded"))

    result = run(ps.handle_webhook_event(db, _event(row["payment_id"], event="payment_refunded")))
    assert result["status"] == "refunded" and result["revoked"] is True
    assert db.tables["payment_transactions"][0]["status"] == "refunded"
    # A dashboard-side refund reaches us only as this event, so it releases the
    # early-adopter seat here too (138 — a refund voids the status).
    assert db.calls == ["revoke_plan_grant", "release_early_adopter_seat"]


def test_late_paid_event_never_resurrects_a_refund(keys, monkeypatch):
    """Event ordering is not guaranteed; a paid retry after a refund must not
    hand the plan back to a user who has their money."""
    db = FakeSupabase(sub("free"))
    row = paid_row(db, status="refunded")
    patch_fetch(monkeypatch, moyasar_payment(row["payment_id"]))

    result = run(ps.handle_webhook_event(db, _event(row["payment_id"])))
    assert result["status"] == "refunded" and result["granted"] is False
    assert db.calls == []


# ═══════════════════════════════════════════════════════════════════════════
# Refund
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def provider_refunds(monkeypatch):
    """Record what we ask Moyasar to refund."""
    calls: list[tuple[str, int]] = []

    async def _refund(ref, amount):
        calls.append((ref, amount))
        return {"id": ref, "status": "refunded", "refunded": amount}

    monkeypatch.setattr(ps, "refund_at_provider", _refund)
    return calls


def test_refund_deducts_the_processing_fee(keys, provider_refunds):
    """Trap 11: a refund without an explicit amount refunds in full and gives
    the processing fee away."""
    db = FakeSupabase(sub("pro", source="payment", days_left=29))
    row = paid_row(db)

    result = run(ps.refund_payment(db, USER, row["payment_id"]))
    assert provider_refunds == [(MOYASAR_ID, 9152)]        # 9490 − 338
    assert result["refunded_amount_sar"] == "91.52"   # 94.90 − 3.38
    assert result["refund_fee_sar"] == "3.38"   # provider 1.73 + Moyasar refund fee 1.15 + margin 0.50
    assert result["status"] == "refunded"


def test_refund_marks_refunded_before_revoking(keys, provider_refunds):
    """revoke_plan_grant raises 'payment_not_refunded' on a row that still says
    paid — the FakeSupabase RPC enforces that precondition."""
    db = FakeSupabase(sub("pro", source="payment", days_left=29))
    row = paid_row(db)
    result = run(ps.refund_payment(db, USER, row["payment_id"]))
    assert db.calls == ["revoke_plan_grant", "release_early_adopter_seat"]
    assert result["revoke_action"] in ps.REVOKE_ACTIONS_OK
    assert db.tables["payment_transactions"][0]["revoked_at"]


def test_refund_of_an_upgrade_restores_the_prior_plan(keys, provider_refunds):
    db = FakeSupabase(sub("max", source="payment", days_left=30))
    row = paid_row(db, plan_id="max", amount="211.99", prior_plan_id="pro",
                   prior_expires_at=_iso(_now() + timedelta(days=26)))
    result = run(ps.refund_payment(db, USER, row["payment_id"]))
    assert result["revoke_action"] == "restored"
    assert result["refunded_amount_sar"] == "208.61"       # 211.99 − 3.38


def test_refund_after_24h_is_refused(keys, provider_refunds):
    db = FakeSupabase(sub("pro", source="payment", days_left=29))
    row = paid_row(db, paid_at=_iso(_now() - timedelta(hours=25)))
    with pytest.raises(LunaHTTPException) as exc:
        run(ps.refund_payment(db, USER, row["payment_id"]))
    assert exc.value.status_code == 409
    assert exc.value.code is ErrorCode.PAYMENT_REFUND_WINDOW_CLOSED
    assert provider_refunds == []                           # nothing left the building
    assert db.tables["payment_transactions"][0]["status"] == "paid"


def test_refund_of_another_users_payment_is_404(keys, provider_refunds):
    db = FakeSupabase(sub("pro", source="payment", days_left=29))
    row = paid_row(db)
    with pytest.raises(LunaHTTPException) as exc:
        run(ps.refund_payment(db, OTHER_USER, row["payment_id"]))
    assert exc.value.status_code == 404
    assert provider_refunds == []


@pytest.mark.parametrize("status", ["initiated", "failed", "refunded"])
def test_refund_requires_a_paid_row(keys, provider_refunds, status):
    db = FakeSupabase(sub("free"))
    row = paid_row(db, status=status)
    with pytest.raises(LunaHTTPException) as exc:
        run(ps.refund_payment(db, USER, row["payment_id"]))
    assert exc.value.status_code == 409
    assert provider_refunds == []


# ═══════════════════════════════════════════════════════════════════════════
# History + Apple Pay
# ═══════════════════════════════════════════════════════════════════════════


def test_history_returns_only_the_callers_rows(keys):
    db = FakeSupabase(sub("free"))
    mine = paid_row(db)
    theirs = paid_row(db, user_id=OTHER_USER)

    rows = run(ps.list_history(db, USER))
    ids = [r["payment_id"] for r in rows]
    assert mine["payment_id"] in ids and theirs["payment_id"] not in ids
    assert rows[0]["amount_sar"] == "94.90" and rows[0]["amount_halalas"] == 9490
    assert rows[0]["plan_name_ar"] == "الاحترافية"
    assert rows[0]["refundable"] is True and rows[0]["refund_deadline"]


def test_history_refundable_expires_with_the_window(keys):
    db = FakeSupabase(sub("free"))
    paid_row(db, paid_at=_iso(_now() - timedelta(hours=25)))
    assert run(ps.list_history(db, USER))[0]["refundable"] is False


@pytest.mark.parametrize(
    "url",
    ["https://evil.com/session", "http://apple-pay-gateway.apple.com/x",
     "https://apple.com.evil.com/x", "javascript:alert(1)", ""],
)
def test_applepay_rejects_non_apple_urls(keys, url):
    """validation_url comes from the browser — this endpoint must not become a
    request forwarder aimed at arbitrary hosts."""
    with pytest.raises(LunaHTTPException) as exc:
        ps._validate_apple_validation_url(url)
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "url",
    ["https://apple-pay-gateway.apple.com/paymentservices/startSession",
     "https://cn-apple-pay-gateway.apple.com/paymentservices/startSession"],
)
def test_applepay_accepts_apple_urls(keys, url):
    assert ps._validate_apple_validation_url(url) == url


# ── How Apple Pay died in prod on 2026-08-18 ─────────────────────────────────
#
# The checkout pointed `apple_pay.validate_merchant_url` at OUR backend proxy.
# That is not Moyasar's integration: their guide has the browser call
# `https://api.moyasar.com/v1/applepay/initiate` directly, and their SDK is
# built for it — moyasar.js puts an `X-Moyasar-Form-Version` header on that
# fetch (our CORS allowlist 400'd the preflight) and no Authorization header
# (our authed route would have 401'd it). 46 mobile-Safari buyers hit a dead
# payment sheet, and the only trace was a 400 on an OPTIONS.
#
# The failure was SILENT and it was in a one-line frontend constant, so no
# service-level test above could have caught it. There is no frontend test
# runner in this repo, which makes this static assertion the only executable
# guard on that line. It is deliberately narrow: it pins WHERE merchant
# validation goes, nothing else.

PAY_PAGE = Path(__file__).resolve().parents[2] / "frontend" / "app" / "pay" / "[plan]" / "page.tsx"


def test_applepay_merchant_validation_does_not_route_through_our_backend():
    """`validate_merchant_url` must be Moyasar's endpoint, never ours."""
    src = PAY_PAGE.read_text(encoding="utf-8")

    assert "validate_merchant_url: MOYASAR_APPLEPAY_VALIDATE_URL" in src, (
        "the Apple Pay merchant-validation URL must come from the documented "
        "constant in frontend/lib/moyasar.ts"
    )
    # The exact shape of the regression: our own origin behind that key.
    assert "applepay/session" not in src, (
        "checkout is proxying Apple Pay merchant validation through our backend "
        "again — that is what killed every Apple Pay payment on 2026-08-18"
    )


def test_applepay_validate_url_constant_points_at_moyasar():
    const_src = (
        Path(__file__).resolve().parents[2] / "frontend" / "lib" / "moyasar.ts"
    ).read_text(encoding="utf-8")

    assert (
        'MOYASAR_APPLEPAY_VALIDATE_URL =\n  "https://api.moyasar.com/v1/applepay/initiate"'
        in const_src
    ), "merchant validation must post to Moyasar's documented endpoint"


# ═══════════════════════════════════════════════════════════════════════════
# Refund fee — FULL cost recovery (Moyasar support, 2026-08-05)
# ═══════════════════════════════════════════════════════════════════════════
#
# A refund costs the merchant twice and neither part comes back: the ORIGINAL
# transaction fee stays with Moyasar, plus a flat 1.15 SAR refund-execution
# fee. The deduction therefore has to be derived per payment from the fee the
# provider actually reported — a flat constant either overcharges cheap plans
# or eats the margin on expensive ones.


def test_refund_fee_recovers_provider_fee_plus_margin():
    """basic on mada: provider 1.73 + refund 1.15 + margin 0.50 = 3.38."""
    db = FakeSupabase(sub("basic", source="payment"))
    row = paid_row(db, plan_id="basic", amount="49.90",
                   raw_payload={"id": MOYASAR_ID, "fee": 173})
    assert ps._refund_fee_halalas(row) == 338


def test_refund_fee_scales_with_a_pricier_card_network():
    """A Visa max charge costs more to process — the deduction follows it."""
    db = FakeSupabase(sub("max", source="payment"))
    row = paid_row(db, plan_id="max", amount="189.90",
                   raw_payload={"id": MOYASAR_ID, "fee": 575})
    assert ps._refund_fee_halalas(row) == 740   # 5.75 + 1.15 + 0.50


def test_refund_fee_falls_back_when_provider_fee_missing():
    """Legacy rows have no fee — never silently under-charge."""
    db = FakeSupabase(sub("pro", source="payment"))
    for payload in ({}, {"id": MOYASAR_ID}, {"id": MOYASAR_ID, "fee": "abc"}, None):
        row = paid_row(db, raw_payload=payload)
        assert ps._refund_fee_halalas(row) == ps.REFUND_FEE_FALLBACK_HALALAS


def test_refund_quote_matches_what_refund_actually_deducts(keys, provider_refunds):
    """The number shown in the confirm dialog IS the number charged."""
    db = FakeSupabase(sub("pro", source="payment"))
    row = paid_row(db, raw_payload={"id": MOYASAR_ID, "fee": 173})
    quote = ps.transaction_summary(row)
    assert quote["refund_quote_fee_sar"] == "3.38"
    assert quote["refund_quote_amount_sar"] == "91.52"     # 94.90 − 3.38

    result = run(ps.refund_payment(db, USER, row["payment_id"]))
    assert result["refund_fee_sar"] == quote["refund_quote_fee_sar"]
    assert result["refunded_amount_sar"] == quote["refund_quote_amount_sar"]


def test_refund_quote_absent_once_not_refundable():
    """No stale quote next to an already-refunded row."""
    db = FakeSupabase(sub("pro", source="payment"))
    row = paid_row(db, status="refunded", raw_payload={"id": MOYASAR_ID, "fee": 173})
    summary = ps.transaction_summary(row)
    assert summary["refund_quote_fee_sar"] is None
    assert summary["refund_quote_amount_sar"] is None


def test_txn_columns_include_raw_payload():
    """REGRESSION (prod, 2026-08-05): the per-payment refund fee is read from
    ``raw_payload.fee``. It was absent from the select list, so every refund
    silently used the flat fallback instead — the dynamic mechanism existed
    but never once ran. Cheap guard, because the symptom is a 2-halala
    discrepancy nobody would notice."""
    assert "raw_payload" in ps._TXN_COLUMNS


def test_refund_uses_provider_fee_end_to_end(keys, provider_refunds):
    """The fee actually charged comes from the payload, not the fallback."""
    db = FakeSupabase(sub("basic", source="payment"))
    row = paid_row(db, plan_id="basic", amount="49.90",
                   raw_payload={"id": MOYASAR_ID, "fee": 173})
    result = run(ps.refund_payment(db, USER, row["payment_id"]))
    assert result["refund_fee_sar"] == "3.38"          # 1.73 + 1.15 + 0.50
    assert result["refund_fee_sar"] != "3.40"          # NOT the fallback
    assert provider_refunds == [(MOYASAR_ID, 4652)]    # 4990 − 338


# ═══════════════════════════════════════════════════════════════════════════
# H-4 — the upgrade credit is CONSUMED, not merely quoted
# ═══════════════════════════════════════════════════════════════════════════
#
# Security review 2026-08-07. The credit was priced at checkout from the
# caller's current subscription and then never looked at again: no lock, no cap
# on open rows, no TTL, no constraint. Three layers now stand between a quote
# and a grant, and there is a test here for each — plus the two exploits end to
# end, because the layers only matter as a chain.


def test_credit_ratio_is_clamped_to_one_period(keys):
    """The amplified exploit. Same-plan purchases STACK (grant_plan adds
    duration_days onto a live expiry), so remaining_days was unbounded: pro
    bought 3× left 90 days → 90/30 × 94.90 = 284.70 credit against a 289.90
    plan → clamped by `price − 1.00` to 288.90 → **a 30-day `max` for 1.00
    SAR**, below the ~1.73 SAR the card network charges us to collect it."""
    db = FakeSupabase(sub("pro", source="payment", days_left=90))
    result = checkout(db, "max")
    assert result["credit_sar"] == "94.90"       # one period of pro, never three
    assert result["amount_sar"] == "195.00"
    assert result["amount_halalas"] == 19500


@pytest.mark.parametrize("days_left", [30, 45, 90, 365])
def test_credit_never_exceeds_one_period_at_any_stack_depth(keys, days_left):
    db = FakeSupabase(sub("pro", source="payment", days_left=days_left))
    assert checkout(db, "max")["credit_sar"] == "94.90"


def test_new_checkout_supersedes_the_open_one(keys):
    """Layer 2: one payable quote per user. Two open discounted rows priced
    against the SAME untouched subscription is the whole stockpile."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    first = checkout(db, "max")["payment_id"]
    second = checkout(db, "max")["payment_id"]

    rows = {r["payment_id"]: r for r in db.tables["payment_transactions"]}
    assert rows[first]["status"] == ps.STATUS_EXPIRED
    assert rows[second]["status"] == "initiated"


def test_a_refused_checkout_expires_nothing(keys):
    """The supersede sits at the insert, not at the top of the function: a
    downgrade 409 must not kill a quote the user is in the middle of paying."""
    db = FakeSupabase(sub("max", source="payment", days_left=10))
    open_id = checkout(db, "max")["payment_id"]          # same-plan re-purchase

    with pytest.raises(LunaHTTPException):
        checkout(db, "pro")                              # blocked downgrade

    rows = {r["payment_id"]: r for r in db.tables["payment_transactions"]}
    assert rows[open_id]["status"] == "initiated"


def test_stockpiled_credited_checkouts_grant_exactly_once(keys, monkeypatch):
    """EXPLOIT 1, end to end. Open N checkouts BEFORE paying any — each reads
    the same untouched `pro` subscription and applies the full credit — then pay
    them all. 47% off every unit after the first.

    The rows are re-opened by hand after checkout: that is the state prod was
    already in before 119 (both payers held 7 concurrent open rows), and it is
    what a racing insert would produce if the unique index were ever dropped.
    The fulfilment re-derivation has to hold on its own, without the supersede
    in front of it."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    ids = [checkout(db, "max")["payment_id"] for _ in range(3)]
    for row in db.tables["payment_transactions"]:
        row["status"] = "initiated"

    results = []
    for pid in ids:
        # A distinct provider id per payment, as Moyasar would issue.
        patch_fetch(
            monkeypatch,
            {**moyasar_payment(pid, amount=20765), "id": str(uuid.uuid4())},
        )
        results.append(run(ps.verify_payment(db, USER, MOYASAR_ID)))

    assert [r["granted"] for r in results] == [True, False, False]
    # ONCE, TOTAL. The effective_plan_price reads around it are the
    # re-derivation (H-4 layer 3) running for all three payments — only the
    # first one gets past it — and they are filtered out so this assertion keeps
    # saying what it is for: exactly one grant.
    assert [c for c in db.calls if c != "effective_plan_price"] == [
        "stamp_payment_prior_snapshot", "grant_plan", "stamp_usage_reset",
        "claim_early_adopter_seat",
    ]
    assert db.tables["user_subscriptions"][0]["plan_id"] == "max"

    held = [r for r in db.tables["payment_transactions"] if not r.get("fulfilled_at")]
    assert len(held) == 2
    for row in held:
        # Money IS in and stays recorded — the plan is what is withheld.
        assert row["status"] == "paid" and row["paid_at"]
        assert ps.transaction_summary(row)["refundable"] is True
    assert all(r["review_reason"] == "credit_no_longer_owed" for r in results[1:])


def test_credit_revalidation_refuses_when_the_term_is_gone(keys, monkeypatch):
    """Layer 3 in isolation: the subscription that justified the discount has
    been refunded away by the time the money lands."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    pid = checkout(db, "max")["payment_id"]
    db.tables["user_subscriptions"] = []
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=20765))

    result = run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert result["granted"] is False
    assert result["review_reason"] == "credit_no_longer_owed"
    # Priced (138), then refused: neither snapshot nor grant ran.
    assert db.calls == ["effective_plan_price"]

    row = db.tables["payment_transactions"][0]
    assert row["status"] == "paid" and not row.get("fulfilled_at")


def test_a_stale_quote_cannot_be_banked(keys, monkeypatch):
    """Prod held a payable 200.00 SAR `max` quote for three days. Past the TTL
    the discount is not honoured on its own word."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    pid = checkout(db, "max")["payment_id"]
    db.tables["payment_transactions"][0]["created_at"] = _iso(_now() - timedelta(hours=25))
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=20765))

    result = run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert result["granted"] is False and result["review_reason"] == "quote_expired"
    assert db.calls == []


def test_an_honest_upgrade_still_grants(keys, monkeypatch):
    """REGRESSION WALL. On the honest path the only thing that moved between
    checkout and payment is the clock, and time decay must never read as a
    state change — the re-derivation is anchored at the quote's own timestamp."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    pid = checkout(db, "max")["payment_id"]
    db.tables["payment_transactions"][0]["created_at"] = _iso(_now() - timedelta(hours=3))
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=20765))

    result = run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert result["granted"] is True
    assert db.calls == [
        "effective_plan_price", "effective_plan_price",     # re-priced, still owed
        "stamp_payment_prior_snapshot", "grant_plan", "stamp_usage_reset",
        "claim_early_adopter_seat",
    ]


def test_a_credited_upgrade_is_idempotent_across_both_paths(keys, monkeypatch):
    """The webhook lands after /verify already granted, so the subscription now
    reads as the plan this very payment bought. Re-deriving there would compare
    the credit against its own result — `fulfilled_at` is the anchor that stops
    it, exactly as it stops grant_plan."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    pid = checkout(db, "max")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=20765))

    first = run(ps.verify_payment(db, USER, MOYASAR_ID))
    second = run(ps.handle_webhook_event(db, _event(pid)))
    assert first["granted"] is True and second["granted"] is True
    assert db.tables["payment_transactions"][0]["fulfilled_at"]


def test_a_price_cut_between_quote_and_payment_still_grants(keys, monkeypatch):
    """The re-derivation is `paid >= owed`, not `paid == owed`: overpaying is a
    refund question, never a reason to withhold a plan."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    pid = checkout(db, "max")["payment_id"]
    for plan in db.tables["plans"]:
        if plan["plan_id"] == "max":
            plan["price_sar"] = "249.90"
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=20765))

    assert run(ps.verify_payment(db, USER, MOYASAR_ID))["granted"] is True


def test_a_held_payment_stays_refundable_by_the_customer(keys, monkeypatch, provider_refunds):
    """The reason holding is acceptable at all: the customer is one click from
    their money back, and revoke_plan_grant answers `not_fulfilled` cleanly on a
    grant that never ran."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    pid = checkout(db, "max")["payment_id"]
    db.tables["user_subscriptions"] = []
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=20765))
    run(ps.verify_payment(db, USER, MOYASAR_ID))

    result = run(ps.refund_payment(db, USER, pid))
    assert result["status"] == "refunded"
    assert result["revoke_action"] == "not_fulfilled"
    assert result["revoked"] is True
    assert provider_refunds == [(MOYASAR_ID, 20765 - 340)]   # legacy-fee fallback


# ═══════════════════════════════════════════════════════════════════════════
# M-1 — the refund ladder unwinds newest-first
# ═══════════════════════════════════════════════════════════════════════════


def _ladder(db):
    """basic → pro → max: 289.90 paid across three rungs, each upgrade priced
    against the one below it. Audit logs from prod confirm the shape
    (`checkout_initiated plan=max from_plan=pro credit=89.90 amount=200.00`)."""
    basic = paid_row(
        db, plan_id="basic", amount="49.90", upgrade_credit_sar="0.00",
        created_at=_iso(_now() - timedelta(hours=3)),
        paid_at=_iso(_now() - timedelta(hours=3)),
        fulfilled_at=_iso(_now() - timedelta(hours=3)),
    )
    pro = paid_row(
        db, plan_id="pro", amount="40.00", upgrade_credit_sar="49.90",
        prior_plan_id="basic", prior_expires_at=_iso(_now() + timedelta(days=4)),
        created_at=_iso(_now() - timedelta(hours=2)),
        paid_at=_iso(_now() - timedelta(hours=2)),
        fulfilled_at=_iso(_now() - timedelta(hours=2)),
    )
    top = paid_row(
        db, plan_id="max", amount="200.00", upgrade_credit_sar="89.90",
        prior_plan_id="pro", prior_expires_at=_iso(_now() + timedelta(days=29)),
        created_at=_iso(_now() - timedelta(hours=1)),
        paid_at=_iso(_now() - timedelta(hours=1)),
        fulfilled_at=_iso(_now() - timedelta(hours=1)),
    )
    return basic, pro, top


def test_refund_of_a_superseded_payment_is_blocked(keys, provider_refunds):
    """EXPLOIT 2. Refunding the basic and the pro used to return 85.90 with both
    hitting `plan_switched` — the subscription untouched, a 289.90 `max` term
    standing for a net 204.00, full entitlement retained."""
    db = FakeSupabase(sub("max", source="payment", days_left=30))
    basic, pro, _top = _ladder(db)

    for rung in (basic, pro):
        with pytest.raises(LunaHTTPException) as exc:
            run(ps.refund_payment(db, USER, rung["payment_id"]))
        assert exc.value.status_code == 409
        assert exc.value.code is ErrorCode.PAYMENT_REFUND_WINDOW_CLOSED
        assert exc.value.detail == ps.REFUND_SUPERSEDED_AR

    assert provider_refunds == []               # nothing left the building
    assert db.calls == []                       # and nothing was revoked
    assert db.tables["user_subscriptions"][0]["plan_id"] == "max"


def test_refund_ladder_unwinds_newest_first(keys, provider_refunds):
    """Blocking is not a dead end: refunding the top RESTORES the plan beneath
    it, which un-blocks the next rung down. The whole ladder is still
    self-serve — just in the only order that stays consistent."""
    db = FakeSupabase(sub("max", source="payment", days_left=30))
    basic, pro, top = _ladder(db)

    assert run(ps.refund_payment(db, USER, top["payment_id"]))["revoke_action"] == "restored"
    assert db.tables["user_subscriptions"][0]["plan_id"] == "pro"

    assert run(ps.refund_payment(db, USER, pro["payment_id"]))["revoke_action"] == "restored"
    assert db.tables["user_subscriptions"][0]["plan_id"] == "basic"

    assert run(ps.refund_payment(db, USER, basic["payment_id"]))["revoke_action"] == "subtracted"
    assert len(provider_refunds) == 3


def test_history_hides_the_button_it_would_refuse(keys):
    """/history and /refund must agree — a button that always 409s is worse
    than no button, and both read the same predicate."""
    db = FakeSupabase(sub("max", source="payment", days_left=30))
    basic, pro, top = _ladder(db)
    by_id = {r["payment_id"]: r for r in run(ps.list_history(db, USER))}

    for rung in (basic, pro):
        item = by_id[rung["payment_id"]]
        assert item["superseded"] is True
        assert item["refundable"] is False
        assert item["refund_quote_fee_sar"] is None

    assert by_id[top["payment_id"]]["superseded"] is False
    assert by_id[top["payment_id"]]["refundable"] is True


def test_an_ordinary_purchase_is_never_marked_superseded(keys):
    """A later FULL-PRICE purchase consumed no credit, so it supersedes
    nothing — refunding the earlier one subtracts its own days, correctly."""
    db = FakeSupabase(sub("pro", source="payment", days_left=30))
    first = paid_row(db, created_at=_iso(_now() - timedelta(hours=3)))
    paid_row(db, created_at=_iso(_now() - timedelta(hours=1)))

    by_id = {r["payment_id"]: r for r in run(ps.list_history(db, USER))}
    assert by_id[first["payment_id"]]["superseded"] is False
    assert by_id[first["payment_id"]]["refundable"] is True


def test_a_revoked_upgrade_stops_blocking_the_rung_below(keys, provider_refunds):
    """`revoked_at` is why _TXN_COLUMNS has to carry it: an upgrade whose refund
    already unwound must not keep blocking the payment underneath it."""
    db = FakeSupabase(sub("max", source="payment", days_left=30))
    basic, pro, top = _ladder(db)
    top["status"], top["revoked_at"] = "refunded", _iso(_now())
    db.tables["user_subscriptions"][0]["plan_id"] = "pro"

    by_id = {r["payment_id"]: r for r in run(ps.list_history(db, USER))}
    assert by_id[pro["payment_id"]]["superseded"] is False      # top no longer blocks it
    assert by_id[basic["payment_id"]]["superseded"] is True     # pro still does
    assert run(ps.refund_payment(db, USER, pro["payment_id"]))["revoke_action"] == "restored"


def test_plan_switched_is_no_longer_a_silent_success(keys, monkeypatch):
    """It never was a success: the money goes back and the entitlement STANDS.
    Self-serve refunds can no longer reach it, but a dashboard-side refund can,
    so it must read as not-revoked and log loudly instead of at INFO."""
    assert "plan_switched" not in ps.REVOKE_ACTIONS_OK
    assert "plan_switched" in ps.REVOKE_ACTIONS_ATTENTION

    db = FakeSupabase(sub("max", source="payment", days_left=30))
    row = paid_row(db, plan_id="pro")          # the user has since moved to max
    patch_fetch(monkeypatch, moyasar_payment(row["payment_id"], status="refunded"))

    result = run(ps.handle_webhook_event(db, _event(row["payment_id"], event="payment_refunded")))
    assert result["revoke_action"] == "plan_switched"
    assert result["revoked"] is False


# ═══════════════════════════════════════════════════════════════════════════
# إلغاء الاشتراك — renewal opt-out + exit survey
# (.claude/plans/subscription_cancellation.md · migration 120)
# ═══════════════════════════════════════════════════════════════════════════
#
# ⚠ WHAT THESE TESTS CANNOT CATCH: FakeSupabase accepts any column name, so a
# rename or a missing migration passes here and 42703s in prod. Migration 120
# must be applied BEFORE the backend deploys, and the live cancel/undo smoke in
# the plan's §5 is mandatory, not optional.


def paid_sub(days_left=20, cancelled_at=None, plan_id="pro"):
    """A subscription bought with money and still running — the only kind that
    can be cancelled."""
    return sub(plan_id, source="payment", days_left=days_left, cancelled_at=cancelled_at)


def surveys(db):
    return db.tables["subscription_cancellations"]


def current_sub(db):
    return db.tables["user_subscriptions"][0]


# ── state ────────────────────────────────────────────────────────────────────


def test_subscription_state_reports_a_cancellable_paid_plan():
    db = FakeSupabase(paid_sub())
    state = run(ss.get_subscription(db, USER))
    assert state["plan_id"] == "pro"
    assert state["plan_name_ar"] == "الاحترافية"      # from the catalog, not the row
    assert state["source"] == "payment"
    assert state["cancellable"] is True
    assert state["renewal_cancelled_at"] is None


def test_subscription_state_of_a_user_with_no_row():
    """Never a 500 on the settings dialog — «no plan» is an ordinary answer."""
    state = run(ss.get_subscription(FakeSupabase(), USER))
    assert state == {"plan_id": None, "plan_name_ar": None, "expires_at": None,
                     "source": None, "cancellable": False,
                     "renewal_cancelled_at": None,
                     # 196 — set only while a renewal grace extends access.
                     "renewal_grace_until": None,
                     # 138 — always present, and "not a member" is what every
                     # user reads while the campaign is disabled. No count here,
                     # by design (early_adopters.md §1.10).
                     "early_adopter": {"is_member": False, "promo_ends_at": None,
                                       "returnable": False, "tier_rules": False}}


@pytest.mark.parametrize(
    "subscription,why",
    [
        (sub("free"), "free/signup"),
        (sub("pro", source="code", days_left=20), "code grant"),
        (sub("pro", source="manual", days_left=20), "manual grant"),
        (sub("pro", source="signup", days_left=20), "signup grant"),
        (sub("pro", source="payment", days_left=-1), "expired paid term"),
        (sub(None, source="payment", days_left=20), "locked account"),
        (sub("pro", source="payment"), "non-expiring grant"),
    ],
)
def test_only_a_running_paid_plan_is_cancellable(subscription, why):
    """Visibility rule: code/marketing/manual/signup grants renew nothing and
    expire on their own, so a cancel button there is noise — and on an expired
    or locked account it is a lie."""
    state = run(ss.get_subscription(FakeSupabase(subscription), USER))
    assert state["cancellable"] is False, why


def test_cancellable_stays_true_while_cancelled():
    """It describes the SUBSCRIPTION, not the button: an undo makes cancelling
    legal again, and the dialog branches on renewal_cancelled_at."""
    state = run(ss.get_subscription(FakeSupabase(paid_sub(cancelled_at=_iso(_now()))), USER))
    assert state["cancellable"] is True
    assert state["renewal_cancelled_at"]


# ── cancel ───────────────────────────────────────────────────────────────────


def test_cancel_sets_the_flag_and_writes_the_survey():
    db = FakeSupabase(paid_sub(days_left=20))
    expires_at = current_sub(db)["expires_at"]

    state = run(ss.cancel_renewal(db, USER, reason="expensive", comment="غالي جداً"))

    assert state["renewal_cancelled_at"] and state["cancellable"] is True
    assert current_sub(db)["renewal_cancelled_at"] == state["renewal_cancelled_at"]

    assert len(surveys(db)) == 1
    row = surveys(db)[0]
    assert row["user_id"] == USER
    assert row["plan_id"] == "pro"
    assert row["reason"] == "expensive"
    assert row["comment"] == "غالي جداً"
    # Snapshotted, so a later grant/refund cannot rewrite what the user was told.
    assert row["expires_at_snapshot"] == expires_at
    assert row.get("revoked_at") is None


def test_cancel_never_touches_the_term_or_the_plan():
    """Cancel ≠ refund: access runs to expires_at exactly as before. Writing
    plan_id in the same UPDATE would also wake the assignment trigger and
    re-stamp the expiry (the «set expiry ALONE» trap)."""
    db = FakeSupabase(paid_sub(days_left=20))
    before = dict(current_sub(db))

    run(ss.cancel_renewal(db, USER, reason="other"))

    after = current_sub(db)
    assert after["plan_id"] == before["plan_id"]
    assert after["expires_at"] == before["expires_at"]
    assert after["source"] == before["source"]


def test_cancel_comment_is_optional():
    db = FakeSupabase(paid_sub())
    run(ss.cancel_renewal(db, USER, reason="no_longer_needed"))
    assert surveys(db)[0]["comment"] is None


def test_cancel_blank_comment_is_stored_as_null():
    """«   » is not feedback — it should not read as a comment in the report."""
    db = FakeSupabase(paid_sub())
    run(ss.cancel_renewal(db, USER, reason="other", comment="   \n "))
    assert surveys(db)[0]["comment"] is None


def test_cancel_truncates_an_enormous_comment():
    db = FakeSupabase(paid_sub())
    run(ss.cancel_renewal(db, USER, reason="other", comment="ب" * 5000))
    assert len(surveys(db)[0]["comment"]) == ss.COMMENT_MAX_CHARS


@pytest.mark.parametrize("reason", ["", "too_expensive", "EXPENSIVE", "other ", None, 7])
def test_cancel_rejects_an_unknown_reason(reason):
    """The four keys are a CHECK constraint in 120 — a value that would 23514
    at the database must be refused in Arabic before it ever gets there."""
    db = FakeSupabase(paid_sub())
    with pytest.raises(LunaHTTPException) as exc:
        run(ss.cancel_renewal(db, USER, reason=reason))
    assert exc.value.status_code == 400
    assert exc.value.detail == ss.INVALID_REASON_AR
    assert surveys(db) == []
    assert current_sub(db)["renewal_cancelled_at"] is None


@pytest.mark.parametrize(
    "subscription",
    [
        None,                                          # no subscription row
        sub("free"),
        sub("pro", source="code", days_left=20),
        sub("pro", source="manual", days_left=20),
        sub("pro", source="signup", days_left=20),
        sub("pro", source="payment", days_left=-1),    # term already over
    ],
)
def test_cancel_refuses_anything_but_a_running_paid_plan(subscription):
    db = FakeSupabase(subscription)
    with pytest.raises(LunaHTTPException) as exc:
        run(ss.cancel_renewal(db, USER, reason="expensive"))
    assert exc.value.status_code == 409
    assert exc.value.code is ErrorCode.SUBSCRIPTION_NOT_CANCELLABLE
    assert exc.value.detail == ss.NO_PAID_SUBSCRIPTION_AR
    assert surveys(db) == []


def test_double_cancel_is_refused_and_writes_one_survey_row():
    """Idempotency is a refusal, not a no-op: a second row would double-count
    one departure in the only data this feature produces."""
    db = FakeSupabase(paid_sub())
    run(ss.cancel_renewal(db, USER, reason="expensive"))
    first_flag = current_sub(db)["renewal_cancelled_at"]

    with pytest.raises(LunaHTTPException) as exc:
        run(ss.cancel_renewal(db, USER, reason="something_wrong"))
    assert exc.value.status_code == 409
    assert exc.value.code is ErrorCode.SUBSCRIPTION_ALREADY_CANCELLED
    assert exc.value.detail == ss.ALREADY_CANCELLED_AR

    assert len(surveys(db)) == 1
    assert current_sub(db)["renewal_cancelled_at"] == first_flag


def test_a_lost_survey_row_never_undoes_the_cancellation(monkeypatch, caplog):
    """The flag IS the cancellation. Losing the answer is a lost datapoint;
    reporting the cancellation as failed would be a broken promise."""
    db = FakeSupabase(paid_sub())

    def _boom(*_a, **_k):
        raise RuntimeError("survey table unavailable")

    monkeypatch.setattr(ss, "_insert_survey", _boom)

    state = run(ss.cancel_renewal(db, USER, reason="expensive"))
    assert state["renewal_cancelled_at"]
    assert current_sub(db)["renewal_cancelled_at"]
    assert surveys(db) == []


def test_a_failed_flag_write_refuses_the_cancel(monkeypatch):
    """The opposite order: if the flag cannot be written there is no
    cancellation, so no survey row may claim there was one."""
    db = FakeSupabase(paid_sub())

    def _boom(*_a, **_k):
        raise RuntimeError("column renewal_cancelled_at does not exist")

    monkeypatch.setattr(ss, "_write_renewal_flag", _boom)

    with pytest.raises(LunaHTTPException) as exc:
        run(ss.cancel_renewal(db, USER, reason="expensive"))
    assert exc.value.status_code == 503
    assert surveys(db) == []


# ── reactivate ───────────────────────────────────────────────────────────────


def test_reactivate_clears_the_flag_and_revokes_the_newest_survey():
    db = FakeSupabase(paid_sub())
    run(ss.cancel_renewal(db, USER, reason="expensive"))

    state = run(ss.reactivate_renewal(db, USER))

    assert state["renewal_cancelled_at"] is None
    assert current_sub(db)["renewal_cancelled_at"] is None
    assert surveys(db)[0]["revoked_at"]


def test_reactivate_revokes_only_the_cancellation_it_undoes():
    """Cancel → undo → cancel again leaves two true answers on file; today's
    undo takes back today's."""
    db = FakeSupabase(paid_sub())
    run(ss.cancel_renewal(db, USER, reason="expensive"))
    run(ss.reactivate_renewal(db, USER))
    run(ss.cancel_renewal(db, USER, reason="something_wrong"))
    run(ss.reactivate_renewal(db, USER))

    rows = sorted(surveys(db), key=lambda r: r["created_at"])
    assert [r["reason"] for r in rows] == ["expensive", "something_wrong"]
    assert all(r["revoked_at"] for r in rows)          # each undo hit its own row


def test_reactivate_refused_when_nothing_was_cancelled():
    db = FakeSupabase(paid_sub())
    with pytest.raises(LunaHTTPException) as exc:
        run(ss.reactivate_renewal(db, USER))
    assert exc.value.status_code == 409
    assert exc.value.code is ErrorCode.SUBSCRIPTION_NOT_CANCELLABLE
    assert exc.value.detail == ss.NOT_CANCELLED_AR


def test_reactivate_refused_once_the_term_has_ended():
    """A lapsed plan comes back only through a new purchase — undoing here
    would promise access that no longer exists."""
    db = FakeSupabase(paid_sub(days_left=-1, cancelled_at=_iso(_now() - timedelta(days=5))))
    with pytest.raises(LunaHTTPException) as exc:
        run(ss.reactivate_renewal(db, USER))
    assert exc.value.status_code == 409
    assert exc.value.detail == ss.TERM_ENDED_AR
    assert current_sub(db)["renewal_cancelled_at"]     # flag untouched


def test_reactivate_survives_a_missing_survey_row():
    """A flag set by an operator (or a cancel whose survey insert was lost) must
    still be undoable."""
    db = FakeSupabase(paid_sub(cancelled_at=_iso(_now())))
    state = run(ss.reactivate_renewal(db, USER))
    assert state["renewal_cancelled_at"] is None


# ── re-purchase clears the flag ──────────────────────────────────────────────


def test_paid_fulfilment_clears_the_renewal_flag(keys, monkeypatch):
    """Buying again IS re-opting in. grant_plan names its columns, so it leaves
    the opt-out standing — this is the Python-side clear that follows it."""
    db = FakeSupabase(paid_sub(days_left=20, cancelled_at=_iso(_now() - timedelta(days=1))))

    pid = checkout(db, "pro")["payment_id"]          # same plan → no credit
    patch_fetch(monkeypatch, moyasar_payment(pid))

    result = run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert result["granted"] is True
    assert current_sub(db)["renewal_cancelled_at"] is None


def test_re_purchase_does_not_revoke_the_survey_row():
    """The survey recorded a true moment. Only an explicit undo un-says it —
    coming back later does not."""
    db = FakeSupabase(paid_sub())
    run(ss.cancel_renewal(db, USER, reason="expensive", comment="سأعود"))

    cleared = ss.clear_renewal_cancellation(db, USER)

    assert cleared is True
    assert current_sub(db)["renewal_cancelled_at"] is None
    # .get(): the insert omits revoked_at entirely (the DB defaults it to NULL),
    # which is the same "no value" the is_("revoked_at","null") lookup matches.
    assert surveys(db)[0].get("revoked_at") is None


def test_clearing_an_unset_flag_writes_nothing():
    db = FakeSupabase(paid_sub())
    assert ss.clear_renewal_cancellation(db, USER) is False
    assert not [w for w in db.writes if w == ("user_subscriptions", "update")]


def test_clearing_the_flag_never_raises_into_the_money_path(monkeypatch):
    """The plan is granted and the money is in; a stale flag is a Wave 2
    reporting bug, a 500 here would be a customer who paid and saw an error."""
    db = FakeSupabase(paid_sub(cancelled_at=_iso(_now())))

    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(ss, "_write_renewal_flag", _boom)
    assert ss.clear_renewal_cancellation(db, USER) is False


# ── re-purchase restarts the dunning ladder ──────────────────────────────────


def test_paid_fulfilment_resets_the_renewal_ladder(keys, monkeypatch):
    """Prod 2026-10-30: a declined renewal left renewal_failed_count=1, and a
    re-buy at checkout kept it — so the renewal job read the NEW term as a due
    retry. A paid grant must restart the ladder."""
    db = FakeSupabase(paid_sub(days_left=20))
    current_sub(db)["renewal_failed_count"] = 1

    pid = checkout(db, "pro")["payment_id"]          # same plan → no credit
    patch_fetch(monkeypatch, moyasar_payment(pid))

    result = run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert result["granted"] is True
    assert current_sub(db)["renewal_failed_count"] == 0


def test_a_checkout_inside_the_renewal_grace_ends_the_grace():
    """196: a user mid-ladder who buys at checkout gets a NEW term; the grace
    (access lent while the old card was retried) must not outlive it — a refund
    of the new grant would otherwise leave Pro running on the grace."""
    db = FakeSupabase(paid_sub(days_left=20))
    current_sub(db)["renewal_failed_count"] = 1
    current_sub(db)["renewal_grace_until"] = _iso(_now() + timedelta(hours=20))

    assert ss.reset_renewal_ladder(db, USER) is True
    assert current_sub(db)["renewal_failed_count"] == 0
    assert current_sub(db)["renewal_grace_until"] is None


def test_the_ladder_reset_works_before_196_is_applied():
    """Runs on every paid purchase: it must not name a column a pre-196 DB lacks
    (it reads `*`), so the §2 count reset survives either deploy order."""
    db = FakeSupabase(paid_sub(days_left=20))
    current_sub(db)["renewal_failed_count"] = 1
    current_sub(db).pop("renewal_grace_until", None)

    assert ss.reset_renewal_ladder(db, USER) is True
    assert current_sub(db)["renewal_failed_count"] == 0
    # the update merged into the row: naming the column would have re-added it
    assert "renewal_grace_until" not in current_sub(db)


def test_resetting_a_clean_ladder_writes_nothing():
    db = FakeSupabase(paid_sub())
    current_sub(db)["renewal_failed_count"] = 0
    assert ss.reset_renewal_ladder(db, USER) is False
    assert not [w for w in db.writes if w == ("user_subscriptions", "update")]


def test_resetting_the_ladder_never_raises_into_the_money_path(monkeypatch):
    db = FakeSupabase(paid_sub())

    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "table", _boom)
    assert ss.reset_renewal_ladder(db, USER) is False


# ═══════════════════════════════════════════════════════════════════════════
# عرض الشهر الأول — profession intro offer (profession_intro_offer.md §3)
#
# Eligibility is 183's (`intro_offer_quote`); FakeSupabase models it. What these
# pin is the PYTHON side: the quote replaces the price for pro only, it is
# stamped on the row, it fails closed, and the first month cannot be refunded.
# ═══════════════════════════════════════════════════════════════════════════


def set_profession(db, profession, user_index=0):
    """Declare a profession the way 183's trigger records it: the answer goes
    to ``profession_group`` and — unless it is 'declined' or absent — to the
    write-once ``profession_first_group``, which is the ONLY column the offer
    reads (§4b)."""
    user = db.tables["users"][user_index]
    user["profession_group"] = profession
    user["profession_first_group"] = (
        profession if profession not in (None, "declined") else None
    )


def intro_db(profession="legal", subscription=None) -> FakeSupabase:
    """Both switches on — the early-adopter campaign (whose seat prices months
    2–3) and the intro offer — and a declared profession."""
    db = FakeSupabase(subscription if subscription is not None else sub("free"))
    db.campaign_enabled = True
    db.intro_offer_enabled = True
    set_profession(db, profession)
    return db


def _row(db, payment_id):
    return next(r for r in db.tables["payment_transactions"]
                if r["payment_id"] == payment_id)


@pytest.mark.parametrize(
    "profession, first, halalas",
    [("legal", "1.99", 199), ("specialist", "9.90", 990), ("entrepreneur", "9.90", 990)],
)
def test_the_intro_offer_prices_the_first_month_by_profession(keys, profession, first, halalas):
    db = intro_db(profession)
    quote = checkout(db, "pro")

    assert quote["amount_sar"] == first
    assert quote["amount_halalas"] == halalas
    assert quote["credit_sar"] == "0.00"
    assert quote["non_refundable"] is True
    assert quote["intro_offer"] == {
        "profession_group": profession, "first_month_sar": first,
        "then_sar": "49.90", "then_months": 2, "list_sar": "94.90",
        # v2 keys are present and NULL with ladder_v2 off, so the web keeps
        # today's rendering (it reads null offer_* as the pre-192 backend).
        "offer_sar": None, "offer_months": None,
        "reason": None, "basic_ended_at": None,
    }
    assert quote["description"].endswith("(عرض الشهر الأول)")
    assert quote["promo"] is None                  # no batch with ladder_v2 off

    row = _row(db, quote["payment_id"])
    assert row["amount_sar"] == first
    assert row["intro_offer_group"] == profession
    # Frozen on the row BEFORE the page shows them — the v3-intro disclosure is
    # rebuilt from exactly these, never from the live catalog. Flag off the
    # stamp is byte-identical to the pre-192 one: no offer_* keys, no batch.
    assert row["intro_offer_terms"] == {
        "first_month_sar": first, "then_sar": "49.90", "then_months": 2,
        "list_sar": "94.90",
    }
    assert "promo_batch_id" not in row


@pytest.mark.parametrize(
    "case",
    ["individual", "declined", "unasked", "prior_pro_paid", "prior_max_refunded",
     "active_basic", "offer_switched_off", "offer_ended", "cancelled_seat"],
)
def test_the_intro_offer_is_not_quoted_to_ineligible_users(keys, case):
    """Every wall of 183's quote, one at a time. Each answers the ORDINARY price
    (the early-adopter 49.90 here, since that campaign is open) and stamps
    nothing — an ordinary row's INSERT stays byte-identical to the pre-183 one."""
    db = intro_db("legal")
    if case == "individual":
        set_profession(db, "individual")
    elif case == "declined":
        set_profession(db, "declined")
    elif case == "unasked":
        set_profession(db, None)
    elif case == "prior_pro_paid":
        paid_row(db, plan_id="pro", paid_at=_iso(_now() - timedelta(days=60)))
    elif case == "prior_max_refunded":
        paid_row(db, plan_id="max", status="refunded")
    elif case == "active_basic":
        db.tables["user_subscriptions"] = [sub("basic", source="payment", days_left=3)]
    elif case == "offer_switched_off":
        db.intro_offer_enabled = False
    elif case == "offer_ended":
        db.intro_offer_ends_at = _now() - timedelta(seconds=1)
    elif case == "cancelled_seat":
        db.early_adopter_seats.append({
            "seat_id": "s", "user_id": USER, "payment_id": "p",
            "promo_ends_at": _iso(_now() + timedelta(days=10)),
            "released_at": _iso(_now()), "release_reason": "cancelled",
            "over_capacity": False,
        })

    quote = checkout(db, "pro")

    assert quote["intro_offer"] is None
    assert quote["non_refundable"] is False
    # 49.90 seat price for most. Never the offer's price; the active-basic case
    # is an upgrade (49.90 minus its basic credit), and a non-member's
    # cancelled seat is a forfeiture (list price, §1.6 — unchanged by 183).
    assert quote["amount_sar"] not in ("1.99", "9.90")
    if case == "cancelled_seat":
        assert quote["amount_sar"] == "94.90"
    elif case != "active_basic":
        assert quote["amount_sar"] == "49.90"
    row = _row(db, quote["payment_id"])
    assert "intro_offer_group" not in row and "intro_offer_terms" not in row


def test_the_intro_offer_closes_with_the_early_adopter_seats(keys):
    """No seat ⇒ no months 2–3 ⇒ no offer. The checkout falls to list price."""
    db = intro_db("legal")
    db.campaign_seat_limit = 0
    quote = checkout(db, "pro")
    assert quote["intro_offer"] is None
    assert quote["amount_sar"] == "94.90"


def test_a_basic_only_past_buyer_still_qualifies(keys):
    """Owner, 2026-10-21: only a past PRO or MAX purchase disqualifies. A
    lapsed basic buyer is on free and has never paid for pro."""
    db = intro_db("legal", subscription=sub("basic", source="payment", days_left=-2))
    paid_row(db, plan_id="basic", amount="29.90",
             paid_at=_iso(_now() - timedelta(days=9)))
    assert checkout(db, "pro")["amount_sar"] == "1.99"


def test_max_is_never_discounted_by_the_intro_offer(keys):
    """Pro only (§1 rule 1). An eligible lawyer buying max pays max's own price."""
    db = intro_db("legal")
    quote = checkout(db, "max")
    assert quote["intro_offer"] is None
    assert quote["amount_sar"] == "99.90"


def test_an_unreadable_quote_sells_at_the_ordinary_price(keys):
    """FAIL-CLOSED: advertising 1.99 that checkout cannot honour is the failure
    to avoid, so an RPC error is simply "no offer"."""
    db = intro_db("legal")
    real_rpc = db.rpc

    def _rpc(name, params):
        if name == "intro_offer_quote":
            raise RuntimeError("function intro_offer_quote() does not exist")
        return real_rpc(name, params)

    db.rpc = _rpc
    quote = checkout(db, "pro")
    assert quote["intro_offer"] is None
    assert quote["amount_sar"] == "49.90"


def test_prices_that_do_not_step_up_are_not_sold(keys):
    """A price row at or above the seat price is a misconfiguration, not an
    offer — selling it would 'discount' someone into paying more."""
    db = intro_db("legal")
    db.intro_offer_prices["legal"] = "59.90"
    quote = checkout(db, "pro")
    assert quote["intro_offer"] is None
    assert quote["amount_sar"] == "49.90"


def test_an_intro_first_month_claims_a_seat(keys, monkeypatch):
    """138's gate is amount + credit <= promo, so 1.99 seats like 49.90 does —
    and that seat is what prices months 2–3 at 49.90."""
    db = intro_db("legal")
    quote = checkout(db, "pro")
    patch_fetch(monkeypatch, moyasar_payment(quote["payment_id"], amount=199))
    result = run(ps.verify_payment(db, USER, MOYASAR_ID))

    assert result["granted"] is True
    seats = [s for s in db.early_adopter_seats if s["user_id"] == USER]
    assert len(seats) == 1 and seats[0]["payment_id"] == quote["payment_id"]
    assert ps._parse_ts(seats[0]["promo_ends_at"]) > _now() + timedelta(days=89)


def test_an_intro_first_month_is_not_refundable(keys, provider_refunds):
    db = FakeSupabase(sub("pro", source="payment", days_left=29))
    row = paid_row(db, amount="1.99", intro_offer_group="legal",
                   intro_offer_terms={"first_month_sar": "1.99", "then_sar": "49.90",
                                      "then_months": 2, "list_sar": "89.90"})

    summary = ps.transaction_summary(row)
    assert summary["refundable"] is False
    assert summary["non_refundable"] is True
    assert summary["refund_quote_amount_sar"] is None
    assert run(ps.list_history(db, USER))[0]["refundable"] is False

    with pytest.raises(LunaHTTPException) as exc:
        run(ps.refund_payment(db, USER, row["payment_id"]))
    assert exc.value.status_code == 409
    assert exc.value.code is ErrorCode.PAYMENT_NOT_REFUNDABLE
    assert exc.value.detail == ps.REFUND_NOT_REFUNDABLE_AR
    assert provider_refunds == []                  # no money moved
    assert db.calls == []                          # and no grant was touched


def test_an_ordinary_payment_keeps_its_refund(keys):
    """Months 2+ are renewals and never carry the group — the normal 24h rule."""
    db = FakeSupabase(sub("pro", source="payment", days_left=29))
    summary = ps.transaction_summary(paid_row(db, amount="49.90"))
    assert summary["refundable"] is True
    assert summary["non_refundable"] is False


def test_a_dashboard_refund_of_an_intro_month_is_still_processed(keys, monkeypatch):
    """Self-serve is refused; an operator's refund from Moyasar's dashboard
    arrives as a webhook and must still revoke the term."""
    db = FakeSupabase(sub("pro", source="payment", days_left=29))
    row = paid_row(db, amount="1.99", intro_offer_group="legal")
    patch_fetch(monkeypatch, moyasar_payment(row["payment_id"], status="refunded", amount=199))
    result = run(ps.handle_webhook_event(
        db, _event(row["payment_id"], event="payment_refunded")))
    assert result["status"] == "refunded" and result["revoked"] is True
    assert row["status"] == "refunded"


def test_the_intro_offer_state_for_an_eligible_user(keys):
    db = intro_db("specialist")
    assert run(ps.intro_offer_state(db, USER)) == {
        "eligible": True, "profession_group": "specialist",
        "first_month_sar": "9.90", "then_sar": "49.90", "then_months": 2,
        "list_sar": "94.90",
        "offer_sar": None, "offer_months": None, "reason": None, "basic_ended_at": None,
    }


CLOSED_OFFER = {"eligible": False, "profession_group": None, "first_month_sar": None,
                "then_sar": None, "then_months": 2, "list_sar": None,
                "offer_sar": None, "offer_months": None, "reason": None,
                "basic_ended_at": None}


def test_the_intro_offer_state_when_not_eligible(keys):
    db = intro_db("individual")
    assert run(ps.intro_offer_state(db, USER)) == CLOSED_OFFER


def test_the_intro_offer_state_fails_closed(keys):
    """No end date, no seat count, no 5xx — "not eligible" is the whole answer."""
    db = intro_db("legal")

    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    db.rpc = _boom
    db.table = _boom
    assert run(ps.intro_offer_state(db, USER)) == CLOSED_OFFER


def test_the_intro_offer_route_returns_the_state_uncached(keys, monkeypatch):
    from backend.app.api import payments as api

    db = intro_db("legal")
    monkeypatch.setattr(api, "get_user_id", lambda _sb, _auth: USER)

    class _Resp:
        def __init__(self):
            self.headers: dict = {}

    resp = _Resp()
    user = type("U", (), {"auth_id": "auth-1"})()
    result = run(api.intro_offer(resp, current_user=user, supabase=db))

    assert result["eligible"] is True and result["first_month_sar"] == "1.99"
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert set(result) == {"eligible", "profession_group", "first_month_sar",
                           "then_sar", "then_months", "list_sar",
                           "offer_sar", "offer_months", "reason", "basic_ended_at"}


def test_an_intro_member_sees_returnable_on_the_subscription(keys, monkeypatch):
    """§4.10: the cancel dialog swaps the forfeiture warning for the "you can
    come back at 49.90" wording — only for users who started on the offer."""
    db = intro_db("legal")
    quote = checkout(db, "pro")
    patch_fetch(monkeypatch, moyasar_payment(quote["payment_id"], amount=199))
    run(ps.verify_payment(db, USER, MOYASAR_ID))

    block = run(ss.get_subscription(db, USER))["early_adopter"]
    assert block["is_member"] is True
    assert block["returnable"] is True


def test_returnable_is_false_for_everyone_else_and_on_failure(keys):
    db = intro_db("legal")
    assert run(ss.get_subscription(db, USER))["early_adopter"]["returnable"] is False

    def _boom(name, params):
        raise RuntimeError("permission denied")

    db.rpc = _boom
    assert run(ss.get_subscription(db, USER))["early_adopter"]["returnable"] is False



# ═══════════════════════════════════════════════════════════════════════════
# Pricing campaigns v2 — price tiers + seat batches (migration 192,
# `.claude/plans/pricing_campaigns_v2.md` §1, §3, §5)
#
# Everything here runs with `db.ladder_v2 = True` unless it says otherwise.
# The §1.3 rules themselves are 192's (`promo_quote`); FakeSupabase carries a
# MODEL of them (promo_ladder_model.py). What these pin is the PYTHON side: the
# quote is stamped as the batch only when it IS the price charged, the response
# states it, the credit is the cash actually paid, the Basic-ended reason, the
# alert — and that with the flag down none of it happens.
# ═══════════════════════════════════════════════════════════════════════════


def ladder_db(profession="legal", subscription=None) -> FakeSupabase:
    """ladder_v2 ON, the §1.2 seed batches, and a FIRST profession answer —
    with both switches 192 reads up (early_adopter_campaign.enabled for every
    tier, intro_offer_campaign.enabled for Pro Tier 1), as at the flip."""
    db = FakeSupabase(subscription if subscription is not None else sub("free"))
    db.campaign_enabled = True
    db.intro_offer_enabled = True
    db.ladder_v2 = True
    db.tables["users"][0]["profession_group"] = profession
    db.tables["users"][0]["profession_first_group"] = profession
    return db


def batch_seat(db, batch_id, *, user_id=USER, days_left=30, reason=None, payment_id=None):
    """A seat already held in a batch (live, or released with ``reason``)."""
    b = db._batch(batch_id)
    seat = {
        "seat_id": str(uuid.uuid4()), "user_id": user_id,
        "payment_id": payment_id or str(uuid.uuid4()),
        "claimed_at": _iso(_now() - timedelta(days=5)),
        "promo_ends_at": _iso(_now() + timedelta(days=days_left)),
        "released_at": _iso(_now()) if reason else None, "release_reason": reason,
        "over_capacity": False,
        "batch_id": batch_id, "plan_id": b["plan_id"],
        "price_tier": b["price_tier"], "price_sar": b["price_sar"],
    }
    db.early_adopter_seats.append(seat)
    return seat


def legacy_seat(db, plan_id, *, days_left=30, reason=None, intro_group=None):
    """A pre-192 seat: NULL batch_id / plan_id, resolved through its payment
    (legacy Pro = Pro Tier 2 used, legacy Max = Max Tier 1 used — §1.3-7; a
    legacy Pro seat bought on the old 1.99/9.90 offer = Pro Tier 1 AND 2)."""
    amount = {"pro": "49.90", "max": "99.90"}[plan_id]
    if intro_group:
        amount = "1.99"
    pay = paid_row(db, plan_id=plan_id, amount=amount, intro_offer_group=intro_group,
                   paid_at=_iso(_now() - timedelta(days=60)))
    seat = {
        "seat_id": str(uuid.uuid4()), "user_id": USER, "payment_id": pay["payment_id"],
        "claimed_at": pay["paid_at"],
        "promo_ends_at": _iso(_now() + timedelta(days=days_left)),
        "released_at": _iso(_now()) if reason else None, "release_reason": reason,
        "over_capacity": False,
    }
    db.early_adopter_seats.append(seat)
    return seat


def refund_released_seat(db, batch_id, *, purchase_status):
    """A batch seat released 'refund', claimed by a real purchase row whose
    status says WHY: 'refunded' = the purchase itself was refunded (owner
    2026-10-27: the tier was never used); 'paid' = a later renewal was refunded
    and the seat went with it (the tier WAS used)."""
    price = BATCH_PRICE[batch_id][0]
    plan_id = batch_id.split("_")[0]
    purchase = paid_row(db, plan_id=plan_id, amount=price, promo_batch_id=batch_id,
                        status=purchase_status, paid_at=_iso(_now() - timedelta(days=20)))
    return batch_seat(db, batch_id, payment_id=purchase["payment_id"], reason="refund")


def fill(db, batch_id):
    """Make a batch full the way an operator would see it: its seat_limit is
    reached by someone else's live seat."""
    db._batch(batch_id)["seat_limit"] = 1
    batch_seat(db, batch_id, user_id=str(uuid.uuid4()))


BATCH_PRICE = {"pro_t1_a": ("19.90", 2), "pro_t1_b": ("19.90", 1), "pro_t2_a": ("59.90", 2),
               "max_t1_a": ("149.90", 4), "max_t2_a": ("189.90", 3), "max_t2_b": ("189.90", 1)}
LIST = {"pro": "94.90", "max": "289.90"}


def _case(db, case):
    """Arrange one row of the §1.3 matrix. Returns the plan being bought."""
    users = db.tables["users"][0]
    if case == "lawyer_fresh":
        return "pro"
    if case == "specialist_fresh":
        users["profession_first_group"] = "specialist"
        return "pro"
    if case == "entrepreneur_fresh":
        users["profession_first_group"] = "entrepreneur"
        return "pro"
    if case == "individual_fresh":
        users["profession_first_group"] = "individual"
        return "pro"
    if case == "unasked_fresh":
        users["profession_first_group"] = None
        users["profession_group"] = None
        return "pro"
    if case == "lawyer_used_t1":                             # a refunded RENEWAL: used
        refund_released_seat(db, "pro_t1_a", purchase_status="paid")
        return "pro"
    if case == "lawyer_refunded_t1_purchase":                # a refunded PURCHASE: unused
        refund_released_seat(db, "pro_t1_a", purchase_status="refunded")
        return "pro"
    if case == "individual_refunded_t2_purchase":
        users["profession_first_group"] = "individual"
        refund_released_seat(db, "pro_t2_a", purchase_status="refunded")
        return "pro"
    if case == "individual_t2_renewal_refunded":
        users["profession_first_group"] = "individual"
        refund_released_seat(db, "pro_t2_a", purchase_status="paid")
        return "pro"
    if case == "lawyer_used_t1_and_t2":
        batch_seat(db, "pro_t1_a", reason="cancelled")
        batch_seat(db, "pro_t2_a", reason="cancelled")
        return "pro"
    if case == "lawyer_cancelled_t2_returns_at_t1":          # Q2's own example
        batch_seat(db, "pro_t2_a", reason="cancelled")
        return "pro"
    if case == "lawyer_lapsed_legacy_pro":                   # legacy Pro = T2 used
        db.tables["user_subscriptions"] = [sub("pro", source="payment", days_left=-3)]
        legacy_seat(db, "pro", days_left=20, reason="cancelled")
        return "pro"
    if case == "individual_lapsed_legacy_pro":
        users["profession_first_group"] = "individual"
        db.tables["user_subscriptions"] = [sub("pro", source="payment", days_left=-3)]
        legacy_seat(db, "pro", days_left=20, reason="cancelled")
        return "pro"
    if case == "lawyer_active_basic":                        # §1.3-2: no T1 on Basic
        db.tables["user_subscriptions"] = [sub("basic", source="payment", days_left=3)]
        return "pro"
    if case == "lawyer_lapsed_basic":
        db.tables["user_subscriptions"] = [sub("basic", source="payment", days_left=-1)]
        return "pro"
    if case == "pro_t1_a_full_rolls_to_b":                   # §1.4-1 rollover
        fill(db, "pro_t1_a")
        return "pro"
    if case == "pro_t1_all_full_falls_to_t2":                # A2
        fill(db, "pro_t1_a")
        fill(db, "pro_t1_b")
        return "pro"
    if case == "pro_t1_a_switched_off":                      # batches are data
        db._batch("pro_t1_a")["enabled"] = False
        return "pro"
    if case == "pro_t1_offer_ended":                         # §1.4-9 end date
        db.intro_offer_ends_at = _now() - timedelta(seconds=1)
        return "pro"
    if case == "pro_all_full":
        for b in ("pro_t1_a", "pro_t1_b", "pro_t2_a"):
            fill(db, b)
        return "pro"
    if case == "on_pro_buying_pro":                          # §1.3-1
        db.tables["user_subscriptions"] = [sub("pro", source="payment", days_left=20)]
        batch_seat(db, "pro_t2_a", days_left=40)
        return "pro"
    if case == "max_fresh":
        return "max"
    if case == "max_t1_full":
        fill(db, "max_t1_a")
        return "max"
    if case == "max_t1_and_t2a_full":
        fill(db, "max_t1_a")
        fill(db, "max_t2_a")
        return "max"
    if case == "max_all_full":
        for b in ("max_t1_a", "max_t2_a", "max_t2_b"):
            fill(db, b)
        return "max"
    if case == "max_lapsed_legacy_max":                      # legacy Max = T1 used
        db.tables["user_subscriptions"] = [sub("max", source="payment", days_left=-3)]
        legacy_seat(db, "max", days_left=20, reason="cancelled")
        return "max"
    if case == "max_used_t1_and_t2":
        batch_seat(db, "max_t1_a", reason="cancelled")
        refund_released_seat(db, "max_t2_b", purchase_status="paid")   # renewal refunded
        return "max"
    if case == "max_refunded_t1_purchase":
        refund_released_seat(db, "max_t1_a", purchase_status="refunded")
        return "max"
    if case == "lapsed_pro_t1_buys_max":                     # A8: tiers are per plan
        db.tables["user_subscriptions"] = [sub("pro", source="payment", days_left=-3)]
        batch_seat(db, "pro_t1_a", reason="cancelled")
        return "max"
    # ── 192 decisions (coordinator, 2026-10-27) ──
    if case == "active_basic_buys_max":                      # 2: Basic blocks Pro T1 only
        db.tables["user_subscriptions"] = [sub("basic", source="payment", days_left=3)]
        return "max"
    if case == "legacy_pro_holder_switches_to_max":          # 1: the quote beats the seat
        db.tables["user_subscriptions"] = [sub("pro", source="payment", days_left=20)]
        legacy_seat(db, "pro", days_left=30)
        return "max"
    if case == "lapsed_legacy_intro_lawyer":                 # 3: old offer = T1 + T2 used
        db.tables["user_subscriptions"] = [sub("pro", source="payment", days_left=-3)]
        legacy_seat(db, "pro", days_left=20, reason="cancelled", intro_group="legal")
        return "pro"
    if case == "lapsed_legacy_intro_lawyer_buys_max":        # …and only for Pro
        db.tables["user_subscriptions"] = [sub("pro", source="payment", days_left=-3)]
        legacy_seat(db, "pro", days_left=20, reason="cancelled", intro_group="legal")
        return "max"
    if case == "lapsed_with_a_live_seat_in_window":          # 5a / I2: the seat prices it
        db.tables["user_subscriptions"] = [sub("pro", source="payment", days_left=-3)]
        batch_seat(db, "pro_t1_a", days_left=20)
        return "pro"
    if case == "lapsed_with_a_spent_live_seat":              # window over: quote applies
        db.tables["user_subscriptions"] = [sub("pro", source="payment", days_left=-3)]
        batch_seat(db, "pro_t1_a", days_left=-1)
        return "pro"
    if case == "campaign_switched_off":                      # I8: enabled gates every tier
        db.campaign_enabled = False
        return "pro"
    if case == "intro_switch_off_blocks_only_t1":            # §1.4-9
        db.intro_offer_enabled = False
        return "pro"
    raise AssertionError(case)


@pytest.mark.parametrize(
    "case, batch, amount",
    [
        ("lawyer_fresh", "pro_t1_a", "19.90"),
        ("specialist_fresh", "pro_t1_a", "19.90"),
        ("entrepreneur_fresh", "pro_t1_a", "19.90"),
        ("individual_fresh", "pro_t2_a", "59.90"),
        ("unasked_fresh", "pro_t2_a", "59.90"),
        ("lawyer_used_t1", "pro_t2_a", "59.90"),
        ("lawyer_refunded_t1_purchase", "pro_t1_a", "19.90"),
        ("individual_refunded_t2_purchase", "pro_t2_a", "59.90"),
        ("individual_t2_renewal_refunded", None, "94.90"),
        ("max_refunded_t1_purchase", "max_t1_a", "149.90"),
        ("lawyer_used_t1_and_t2", None, "94.90"),
        ("lawyer_cancelled_t2_returns_at_t1", "pro_t1_a", "19.90"),
        ("lawyer_lapsed_legacy_pro", "pro_t1_a", "19.90"),
        ("individual_lapsed_legacy_pro", None, "94.90"),
        ("lawyer_active_basic", "pro_t2_a", None),           # upgrade: price minus credit
        ("lawyer_lapsed_basic", "pro_t1_a", "19.90"),
        ("pro_t1_a_full_rolls_to_b", "pro_t1_b", "19.90"),
        ("pro_t1_all_full_falls_to_t2", "pro_t2_a", "59.90"),
        ("pro_t1_a_switched_off", "pro_t1_b", "19.90"),
        ("pro_t1_offer_ended", "pro_t2_a", "59.90"),
        ("pro_all_full", None, "94.90"),
        ("on_pro_buying_pro", None, "59.90"),                # the held seat's own price
        ("max_fresh", "max_t1_a", "149.90"),
        ("max_t1_full", "max_t2_a", "189.90"),
        ("max_t1_and_t2a_full", "max_t2_b", "189.90"),
        ("max_all_full", None, "289.90"),
        ("max_lapsed_legacy_max", "max_t2_a", "189.90"),
        ("max_used_t1_and_t2", None, "289.90"),
        ("lapsed_pro_t1_buys_max", "max_t1_a", "149.90"),
        ("active_basic_buys_max", "max_t1_a", None),        # upgrade from Basic
        ("legacy_pro_holder_switches_to_max", "max_t1_a", None),
        ("lapsed_legacy_intro_lawyer", None, "94.90"),
        ("lapsed_legacy_intro_lawyer_buys_max", "max_t1_a", "149.90"),
        ("lapsed_with_a_live_seat_in_window", None, "19.90"),
        ("lapsed_with_a_spent_live_seat", "pro_t2_a", "59.90"),
        ("campaign_switched_off", None, "94.90"),
        ("intro_switch_off_blocks_only_t1", "pro_t2_a", "59.90"),
    ],
)
def test_v2_the_quote_matrix(keys, case, batch, amount):
    """§1.3 through checkout: profession × used tiers × active Basic × lapsed
    Pro/Max × batches open/full. The row carries the batch exactly when one was
    sold, and the response states its price and months — never its id."""
    db = ladder_db("legal")
    plan_id = _case(db, case)

    quote = checkout(db, plan_id)
    row = _row(db, quote["payment_id"])

    assert row.get("promo_batch_id") == batch
    if batch is None:
        assert quote["promo"] is None
        assert quote["amount_sar"] == amount
        assert "(سعر العرض)" not in quote["description"]
        return
    price, months = BATCH_PRICE[batch]
    assert quote["promo"] == {"price_sar": price, "months": months, "list_sar": LIST[plan_id]}
    assert quote["description"].endswith("(سعر العرض)")
    assert ps.q2(quote["amount_sar"]) + ps.q2(quote["credit_sar"]) == ps.q2(price)
    if amount is not None:
        assert quote["amount_sar"] == amount and quote["credit_sar"] == "0.00"


def test_v2_tier1_checkout_is_the_offer_and_stamps_both(keys):
    """Pro Tier 1 IS عرض الشهر الأول under v2: the batch is stamped, the group is
    stamped (first payment non-refundable), and the offer keys tell the truth
    to old and new clients alike."""
    db = ladder_db("legal")
    quote = checkout(db, "pro")

    assert (quote["amount_sar"], quote["amount_halalas"]) == ("19.90", 1990)
    assert quote["intro_offer"] == {
        "profession_group": "legal",
        "first_month_sar": "19.90", "then_sar": "19.90", "then_months": 1,
        "list_sar": "94.90", "offer_sar": "19.90", "offer_months": 2,
        "reason": None, "basic_ended_at": None,
    }
    assert quote["non_refundable"] is True
    assert quote["promo"] == {"price_sar": "19.90", "months": 2, "list_sar": "94.90"}
    assert quote["description"] == "ريحان — الاحترافية (سعر العرض)"

    row = _row(db, quote["payment_id"])
    assert row["promo_batch_id"] == "pro_t1_a"
    assert row["intro_offer_group"] == "legal"
    assert row["intro_offer_terms"] == {
        "first_month_sar": "19.90", "then_sar": "19.90", "then_months": 1,
        "list_sar": "94.90", "offer_sar": "19.90", "offer_months": 2,
    }
    assert ps.transaction_summary({**row, "status": "paid"})["non_refundable"] is True
    audit = next(r["metadata"] for r in db.tables["audit_logs"]
                 if (r.get("metadata") or {}).get("event") == "checkout_initiated")
    assert audit["promo_batch_id"] == "pro_t1_a"


@pytest.mark.parametrize(
    "profession, plan_id, batch, price, months",
    [("individual", "pro", "pro_t2_a", "59.90", 2),
     ("legal", "max", "max_t1_a", "149.90", 4)],
)
def test_v2_a_tier2_or_max_sale_stamps_the_batch_and_stays_refundable(
    keys, profession, plan_id, batch, price, months
):
    """Every batch sale carries its schedule (v4-tiers is built from these
    terms), but only Pro Tier 1 is the offer — no group, the normal refund."""
    db = ladder_db(profession)
    quote = checkout(db, plan_id)

    assert quote["intro_offer"] is None and quote["non_refundable"] is False
    assert quote["promo"] == {"price_sar": price, "months": months, "list_sar": LIST[plan_id]}
    row = _row(db, quote["payment_id"])
    assert row["promo_batch_id"] == batch
    assert "intro_offer_group" not in row
    assert row["intro_offer_terms"] == {
        "first_month_sar": price, "then_sar": price, "then_months": months - 1,
        "list_sar": LIST[plan_id], "offer_sar": price, "offer_months": months,
    }


def test_v2_flag_off_checkout_writes_exactly_what_it_wrote_before(keys):
    """THE ROLLOUT GUARANTEE: 192 applied, flag down ⇒ behaviour unchanged.
    The INSERT, the audit row and every pre-192 response key are byte-identical,
    and promo_quote is never even asked."""
    pre_192_row_keys = {
        "user_id", "plan_id", "amount_sar", "currency", "status", "provider",
        "vat_amount_sar", "net_amount_sar", "upgrade_credit_sar",
        "customer_name_snapshot", "customer_email_snapshot",
        # added by the table's defaults, not by checkout
        "payment_id", "initiated_by", "created_at",
    }
    pre_192_audit_keys = {"event", "plan_id", "amount_sar", "upgrade_credit_sar",
                          "from_plan_id", "provider", "intro_offer_group"}

    # an open early-adopter campaign + the profession offer, exactly as prod is
    db = intro_db("legal")
    result = run(ps.create_checkout(db, USER, "pro"))
    row = _row(db, result["payment_id"])

    assert "promo_quote" not in db.calls
    assert set(row) == pre_192_row_keys | {"intro_offer_group", "intro_offer_terms"}
    assert row["intro_offer_terms"] == {"first_month_sar": "1.99", "then_sar": "49.90",
                                        "then_months": 2, "list_sar": "94.90"}
    assert result["amount_sar"] == "1.99"
    assert result["description"] == "ريحان — الاحترافية (عرض الشهر الأول)"
    assert result["promo"] is None
    audit = next(r["metadata"] for r in db.tables["audit_logs"]
                 if (r.get("metadata") or {}).get("event") == "checkout_initiated")
    assert set(audit) == pre_192_audit_keys

    # …and an ordinary seat purchase (max, no offer) and an upgrade — whose
    # ONLY new key is 192 V6's credit_basis = 'price' (what NULL meant anyway).
    for subscription, plan_id, amount, extra in (
        (sub("free"), "max", "99.90", set()),
        (sub("pro", source="payment", days_left=26), "max", "17.65", {"credit_basis"}),
    ):
        db = FakeSupabase(subscription)
        db.campaign_enabled = True
        result = run(ps.create_checkout(db, USER, plan_id))
        row = _row(db, result["payment_id"])
        assert "promo_quote" not in db.calls
        assert set(row) == pre_192_row_keys | extra
        assert row.get("credit_basis", "price") == "price"
        assert result["amount_sar"] == amount and result["promo"] is None
        assert result["description"] in ("ريحان — القصوى", "ريحان — القصوى (ترقية)")


def test_v2_flag_on_without_a_batch_is_an_ordinary_sale(keys):
    """A quote that is not the price charged stamps nothing: here the user's
    own live seat prices the re-purchase, so it is not a batch sale."""
    db = ladder_db("legal", sub("pro", source="payment", days_left=20))
    batch_seat(db, "pro_t1_a", days_left=40)
    quote = checkout(db, "pro")
    row = _row(db, quote["payment_id"])
    assert quote["amount_sar"] == "19.90"            # the seat's frozen price
    assert quote["promo"] is None and "promo_batch_id" not in row
    assert "intro_offer_terms" not in row






# ── the intro-offer state under v2 + the Basic-ended reason ──────────────────


def test_v2_the_intro_offer_state_states_the_tier1_terms(keys):
    db = ladder_db("specialist")
    assert run(ps.intro_offer_state(db, USER)) == {
        "eligible": True, "profession_group": "specialist",
        "first_month_sar": "19.90", "then_sar": "19.90", "then_months": 1,
        "list_sar": "94.90", "offer_sar": "19.90", "offer_months": 2,
        "reason": None, "basic_ended_at": None,
    }


def test_v2_a_one_month_batch_keeps_the_legacy_keys_truthful(keys):
    db = ladder_db("legal")
    fill(db, "pro_t1_a")                              # rolls to pro_t1_b: 1 month
    state = run(ps.intro_offer_state(db, USER))
    assert (state["offer_sar"], state["offer_months"]) == ("19.90", 1)
    assert (state["first_month_sar"], state["then_sar"], state["then_months"]) == (
        "19.90", "19.90", 0)


def test_v2_the_basic_ended_reason(keys):
    """§1.3-2 / A10: once Basic has ended the offer is shown, and the popup
    trigger reads this flag. basic_ended_at is that expiry."""
    db = ladder_db("legal", sub("basic", source="payment", days_left=-2))
    expired = ps._parse_ts(db.tables["user_subscriptions"][0]["expires_at"])

    state = run(ps.intro_offer_state(db, USER))
    assert state["eligible"] is True
    assert state["reason"] == ps.INTRO_OFFER_REASON_BASIC_ENDED == "basic_ended"
    assert ps._parse_ts(state["basic_ended_at"]) == expired

    quote = checkout(db, "pro")                       # same keys on the checkout
    assert quote["intro_offer"]["reason"] == "basic_ended"
    assert ps._parse_ts(quote["intro_offer"]["basic_ended_at"]) == expired
    # …and never stamped into the terms the disclosure hashes.
    assert set(_row(db, quote["payment_id"])["intro_offer_terms"]) == {
        "first_month_sar", "then_sar", "then_months", "list_sar", "offer_sar", "offer_months"}


@pytest.mark.parametrize("subscription", [
    pytest.param(sub("basic", source="payment", days_left=3), id="basic_still_running"),
    pytest.param(sub("pro", source="payment", days_left=-2), id="lapsed_pro_not_basic"),
    pytest.param(sub("free"), id="never_had_basic"),
])
def test_v2_no_basic_ended_reason_otherwise(keys, subscription):
    db = ladder_db("legal", subscription)
    state = run(ps.intro_offer_state(db, USER))
    if state["eligible"]:
        assert state["reason"] is None and state["basic_ended_at"] is None
    else:                                             # active Basic: no Tier 1 at all
        assert state == CLOSED_OFFER


def test_the_basic_ended_reason_is_a_v2_behaviour(keys):
    """Flag off, a lapsed Basic lawyer is eligible for TODAY's offer (183) and
    the reason stays null — deploying changes nothing until the flip."""
    db = intro_db("legal", subscription=sub("basic", source="payment", days_left=-2))
    state = run(ps.intro_offer_state(db, USER))
    assert state["eligible"] is True and state["first_month_sar"] == "1.99"
    assert state["reason"] is None and state["basic_ended_at"] is None


# ── upgrade credit = the cash actually paid (§1.4-8) ─────────────────────────


def _pro_tier1_holder(db, *, days_left=26):
    """Paid 19.90 for pro_t1_a four days ago; the term has `days_left`."""
    pay = paid_row(db, plan_id="pro", amount="19.90", intro_offer_group="legal",
                   promo_batch_id="pro_t1_a",
                   paid_at=_iso(_now() - timedelta(days=4)),
                   fulfilled_at=_iso(_now() - timedelta(days=4)))
    batch_seat(db, "pro_t1_a", payment_id=pay["payment_id"], days_left=56)
    return pay


def test_v2_a_19_90_payer_upgrading_is_credited_what_they_paid(keys, monkeypatch):
    """The switch (rule 6): the Max quote for its months, the credit on 19.90
    (26/30 × 19.90 = 17.25), fulfilment re-derives the SAME credit and grants,
    and the Pro seat is released as 'upgraded' beside a 4-month Max seat."""
    db = ladder_db("legal", sub("pro", source="payment", days_left=26))
    _pro_tier1_holder(db)

    quote = checkout(db, "max")
    assert quote["promo"] == {"price_sar": "149.90", "months": 4, "list_sar": "289.90"}
    assert quote["credit_sar"] == "17.25"
    assert quote["amount_sar"] == "132.65"

    patch_fetch(monkeypatch, moyasar_payment(quote["payment_id"], amount=13265))
    result = run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert result["granted"] is True and result.get("review_reason") is None

    pro = next(s for s in db.early_adopter_seats if s.get("batch_id") == "pro_t1_a"
               and s["user_id"] == USER)
    assert pro["release_reason"] == "upgraded"
    live = [s for s in db.early_adopter_seats if s["user_id"] == USER and not s["released_at"]]
    assert len(live) == 1 and live[0]["batch_id"] == "max_t1_a"
    paid_at = ps._parse_ts(_row(db, quote["payment_id"])["paid_at"])
    assert ps._parse_ts(live[0]["promo_ends_at"]) == paid_at + timedelta(days=120)


@pytest.mark.parametrize("ladder, credit", [(False, "82.25"), (True, "67.43")])
def test_v2_the_credit_is_the_cash_not_the_terms_price(keys, ladder, credit):
    """A pro term bought at list WITH a basic credit cost 77.80 cash. Flag off
    it is credited at its 94.90 price (26/30 → 82.25); flag on at the 77.80
    actually paid (26/30 → 67.43) — so credits cannot chain."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    db.ladder_v2 = ladder
    paid_row(db, plan_id="pro", amount="77.80", upgrade_credit_sar="17.10",
             paid_at=_iso(_now() - timedelta(days=4)))
    assert checkout(db, "max")["credit_sar"] == credit


def test_v2_checkout_and_fulfilment_agree_on_the_credit(keys):
    """_upgrade_credit is the ONE implementation and both callers hand it the
    flag: the re-derivation owes exactly the credit checkout stamped."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    db.ladder_v2 = True
    paid_row(db, plan_id="pro", amount="77.80", upgrade_credit_sar="17.10",
             paid_at=_iso(_now() - timedelta(days=4)))
    quote = checkout(db, "max")
    row = {**_row(db, quote["payment_id"]), "status": "paid", "paid_at": _iso(_now())}

    verdict = ps._revalidate_credited_charge(db, row)
    assert verdict["ok"] is True
    assert verdict["credit_owed_sar"] == quote["credit_sar"] == "67.43"


def test_v2_the_funding_payment_is_the_newest_standing_one(keys):
    """Refunded/revoked/never-granted rows fund nothing; the newest standing
    row of the RUNNING plan does."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    db.ladder_v2 = True
    paid_row(db, plan_id="pro", amount="94.90", paid_at=_iso(_now() - timedelta(days=40)))
    paid_row(db, plan_id="pro", amount="59.90", paid_at=_iso(_now() - timedelta(days=4)))
    paid_row(db, plan_id="pro", amount="19.90", paid_at=_iso(_now() - timedelta(days=1)),
             revoked_at=_iso(_now()))                         # unwound
    paid_row(db, plan_id="pro", amount="9.90", paid_at=_iso(_now() - timedelta(hours=2)),
             fulfilled_at=None)                               # held, never granted
    paid_row(db, plan_id="pro", amount="5.00", paid_at=_iso(_now() - timedelta(hours=1)),
             status="refunded")                               # money returned
    paid_row(db, plan_id="basic", amount="39.90", paid_at=_iso(_now()))   # other plan
    assert ps._funding_payment_cash(db, USER, "pro") == ps.q2("59.90")


def test_v2_no_funding_row_falls_back_to_the_current_price(keys):
    """A term with no ledger row behind it (purged, hand-made) is credited by
    the pre-v2 rule — never more than today's code would."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    db.ladder_v2 = True
    assert checkout(db, "max")["credit_sar"] == "82.25"      # 26/30 × 94.90


# ── the quote is the promise + the batch-fill alert ──────────────────────────


def test_v2_the_quote_is_the_promise(keys, monkeypatch):
    """§1.4-5: the batch stamped at checkout is the batch the seat lands in,
    even if it filled — and even if the flag went down — before settlement."""
    db = ladder_db("legal")
    quote = checkout(db, "pro")
    fill(db, "pro_t1_a")
    db.ladder_v2 = False

    patch_fetch(monkeypatch, moyasar_payment(quote["payment_id"], amount=1990))
    assert run(ps.verify_payment(db, USER, MOYASAR_ID))["granted"] is True

    mine = [s for s in db.early_adopter_seats if s["user_id"] == USER]
    assert len(mine) == 1
    assert mine[0]["batch_id"] == "pro_t1_a" and mine[0]["over_capacity"] is True


class _Telemetry:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def warning(self, name, **fields):
        self.events.append((name, fields))


@pytest.fixture(autouse=True)
def telemetry(monkeypatch):
    """AUTOUSE: no test in this module may emit promo.batch_near_full to a real
    Logfire project (another suite in the same run may have configured it), and
    the once-per-process memory must not leak between tests."""
    rec = _Telemetry()
    monkeypatch.setattr(ps, "_logfire", rec)
    monkeypatch.setattr(ps, "_BATCH_NEAR_FULL_ALERTED", set())
    return rec


def _buy_t2(db, monkeypatch, user_id):
    quote = run(ps.create_checkout(db, user_id, "pro"))
    patch_fetch(monkeypatch, moyasar_payment(quote["payment_id"], amount=5990))
    return run(ps.verify_payment(db, user_id, MOYASAR_ID))


def test_v2_a_batch_at_90_percent_warns_once(keys, monkeypatch, telemetry):
    """promo.batch_near_full: batch id + percent, nothing about the buyer, and
    once per batch per process."""
    db = ladder_db("individual")
    db._batch("pro_t2_a")["seat_limit"] = 10
    for _ in range(8):
        batch_seat(db, "pro_t2_a", user_id=str(uuid.uuid4()))

    assert _buy_t2(db, monkeypatch, USER)["granted"] is True          # 9 / 10
    assert telemetry.events == [("promo.batch_near_full", {"batch_id": "pro_t2_a", "percent": 90})]

    assert _buy_t2(db, monkeypatch, OTHER_USER)["granted"] is True    # 10 / 10
    assert len(telemetry.events) == 1


def test_v2_a_batch_below_90_percent_is_quiet(keys, monkeypatch, telemetry):
    db = ladder_db("individual")
    db._batch("pro_t2_a")["seat_limit"] = 10
    for _ in range(7):
        batch_seat(db, "pro_t2_a", user_id=str(uuid.uuid4()))
    _buy_t2(db, monkeypatch, USER)                                     # 8 / 10
    assert telemetry.events == []


def test_v2_a_flag_off_purchase_never_checks_a_batch(keys, monkeypatch, telemetry):
    db = FakeSupabase(sub("free"))
    db.campaign_enabled = True
    quote = checkout(db, "pro")
    patch_fetch(monkeypatch, moyasar_payment(quote["payment_id"], amount=4990))
    reads: list[str] = []
    real_table = db.table

    def _table(name):
        reads.append(name)
        return real_table(name)

    db.table = _table
    assert run(ps.verify_payment(db, USER, MOYASAR_ID))["granted"] is True
    assert "promo_batches" not in reads and telemetry.events == []



# ── 192 decisions (coordinator, 2026-10-27) ──────────────────────────────────


def test_v2_a_legacy_pro_holder_switching_to_max_pays_the_max_quote(keys, monkeypatch):
    """Decision 1 (192 I3): for 'purchase' the quote beats a legacy seat. A
    legacy Pro holder (49.90, live window) switching to Max is charged the Max
    quote — not the 99.90 legacy carry-over — the row is stamped max_t1_a, the
    credit is the 49.90 actually paid, and the claim releases the legacy seat
    as 'upgraded' beside a 4-month Max seat."""
    db = ladder_db("individual", sub("pro", source="payment", days_left=20))
    old = legacy_seat(db, "pro", days_left=30)

    quote = checkout(db, "max")
    assert quote["promo"] == {"price_sar": "149.90", "months": 4, "list_sar": "289.90"}
    assert _row(db, quote["payment_id"])["promo_batch_id"] == "max_t1_a"
    assert quote["credit_sar"] == "33.27"                    # 20/30 × 49.90
    assert quote["amount_sar"] == "116.63"

    patch_fetch(monkeypatch, moyasar_payment(quote["payment_id"], amount=11663))
    assert run(ps.verify_payment(db, USER, MOYASAR_ID))["granted"] is True

    assert old["release_reason"] == "upgraded" and old["released_at"]
    live = [s for s in db.early_adopter_seats if s["user_id"] == USER and not s["released_at"]]
    assert len(live) == 1 and live[0]["batch_id"] == "max_t1_a"


def test_v2_a_pre_flip_carry_over_keeps_renewing_at_the_legacy_price(keys):
    """…while 'current' keeps the legacy order: someone who carried a legacy
    Pro seat onto Max before the flip renews at 99.90 inside the window, and is
    never re-quoted."""
    db = ladder_db("individual", sub("max", source="payment", days_left=1))
    legacy_seat(db, "pro", days_left=30)
    assert ps._effective_price(db, USER, "max", "289.90",
                               context=ps.PRICE_CONTEXT_CURRENT) == ps.q2("99.90")


def _settle(db, monkeypatch, quote, halalas):
    patch_fetch(monkeypatch, moyasar_payment(quote["payment_id"], amount=halalas))
    return run(ps.verify_payment(db, USER, MOYASAR_ID))




def test_v2_a_pre_flip_payment_keeps_todays_claim(keys, monkeypatch):
    """…and a quote opened BEFORE the flip that settles after it is seated
    exactly as today: a legacy 49.90 seat for promo_days."""
    db = FakeSupabase(sub("free"))
    db.campaign_enabled = True                       # today's campaign
    quote = checkout(db, "pro")
    assert quote["amount_sar"] == "49.90"

    db.ladder_v2 = True                              # the flip, mid-3DS
    created = ps._parse_ts(_row(db, quote["payment_id"])["created_at"])
    db.ladder_v2_at = created + timedelta(seconds=1)  # pin the order explicitly

    assert _settle(db, monkeypatch, quote, 4990)["granted"] is True
    mine = [s for s in db.early_adopter_seats if s["user_id"] == USER]
    assert len(mine) == 1 and mine[0].get("batch_id") is None
    paid_at = ps._parse_ts(_row(db, quote["payment_id"])["paid_at"])
    assert ps._parse_ts(mine[0]["promo_ends_at"]) == paid_at + timedelta(days=90)


def test_v2_the_claim_releases_a_spent_same_plan_seat(keys, monkeypatch):
    """Decision 5 (192 I4): ANY live seat not in the payment's batch is
    released 'upgraded' — here a same-plan Tier 1 seat whose window ended (it
    stays live until something releases it). Tier 1 is used, so the quote is
    Tier 2."""
    db = ladder_db("legal", sub("pro", source="payment", days_left=-3))
    spent = batch_seat(db, "pro_t1_a", days_left=-1)

    quote = checkout(db, "pro")
    assert quote["promo"]["price_sar"] == "59.90"
    assert _settle(db, monkeypatch, quote, 5990)["granted"] is True

    assert spent["release_reason"] == "upgraded"
    live = [s for s in db.early_adopter_seats if s["user_id"] == USER and not s["released_at"]]
    assert len(live) == 1 and live[0]["batch_id"] == "pro_t2_a"


def test_v2_a_second_payment_in_the_same_batch_keeps_the_seat(keys):
    """…but a live seat already IN the payment's batch is kept, window and all
    (already_claimed) — never released and re-issued."""
    db = ladder_db("individual")
    held = batch_seat(db, "pro_t2_a", days_left=40)
    row = paid_row(db, plan_id="pro", amount="59.90", promo_batch_id="pro_t2_a")

    claim = ss.claim_early_adopter_seat(db, USER, row["payment_id"], plan_id="pro")

    assert claim["action"] == ss.CLAIM_ACTION_ALREADY_CLAIMED
    assert claim["seat_id"] == held["seat_id"] and held["released_at"] is None
    assert len(db.early_adopter_seats) == 1


@pytest.mark.parametrize(
    "created_offset, amount, action",
    [
        # after the flip, unstamped, priced at/below the legacy promo: the
        # checkout bug 192 R2 refuses — on the wrapper's ERROR branch
        (timedelta(minutes=5), "19.90", "plan_not_eligible"),
        # after the flip at the list price: the legacy gate answers first,
        # exactly as today
        (timedelta(minutes=5), "94.90", ss.CLAIM_ACTION_NOT_PROMO_PRICED),
        # created before the flip: today's claim (the quote is the promise)
        (timedelta(minutes=-5), "49.90", ss.CLAIM_ACTION_CLAIMED),
    ],
    ids=["post_flip_promo_priced", "post_flip_list_priced", "pre_flip_promo_priced"],
)
def test_v2_r2_no_new_legacy_seats_after_the_flip(keys, created_offset, amount, action):
    """192 R2, action by action, straight at the wrapper."""
    db = ladder_db("individual")
    flip = _now() - timedelta(hours=1)
    db.ladder_v2_at = flip
    row = paid_row(db, plan_id="pro", amount=amount, created_at=_iso(flip + created_offset))

    claim = ss.claim_early_adopter_seat(db, USER, row["payment_id"], plan_id="pro")

    assert claim["action"] == action
    seated = [s for s in db.early_adopter_seats if s["user_id"] == USER]
    assert len(seated) == (1 if action == ss.CLAIM_ACTION_CLAIMED else 0)



# ═══════════════════════════════════════════════════════════════════════════
# Review fixes (2026-10-27): the atomic checkout price, null offer_* with the
# flag off, the stamped credit basis, tier_rules, and the 192 model changes.
# ═══════════════════════════════════════════════════════════════════════════


# ── 1. one atomic read prices the checkout ───────────────────────────────────


def test_v2_checkout_prices_from_one_atomic_read(keys):
    """promo_checkout_price is asked ONCE and nothing else is asked for the
    price: no promo_quote, no intro_offer_quote, no effective_plan_price."""
    db = ladder_db("legal")
    run(ps.create_checkout(db, USER, "pro"))
    assert db.calls.count("promo_checkout_price") == 1
    assert not {"promo_quote", "intro_offer_quote", "effective_plan_price"} & set(db.calls)


@pytest.mark.parametrize("ladder", [False, True], ids=["flag_off", "flag_on"])
def test_an_unreadable_checkout_price_is_a_503_and_writes_nothing(keys, ladder):
    """Never a silent fall back to the catalog: a 503 with the Arabic message
    and its own code, and no ledger row (so nothing is superseded either)."""
    db = ladder_db("legal") if ladder else intro_db("legal")
    open_row = paid_row(db, status="initiated", provider_ref=None, paid_at=None,
                        fulfilled_at=None, created_at=_iso(_now()))
    real_rpc = db.rpc

    def _rpc(name, params):
        if name == "promo_checkout_price":
            raise RuntimeError("canceling statement due to statement timeout")
        return real_rpc(name, params)

    db.rpc = _rpc
    with pytest.raises(LunaHTTPException) as exc:
        run(ps.create_checkout(db, USER, "pro"))

    assert exc.value.status_code == 503
    assert exc.value.code is ErrorCode.PAYMENT_PRICING_UNAVAILABLE
    assert exc.value.detail == "تعذّر تجهيز الدفع الآن، حاول مرة أخرى بعد قليل"
    assert db.tables["payment_transactions"] == [open_row]
    assert open_row["status"] == "initiated"                 # not superseded


@pytest.mark.parametrize("answer", [
    pytest.param([], id="no_row"),
    pytest.param([{"price_sar": "19.90"}], id="no_ladder_flag"),
    pytest.param([{"ladder_v2": True, "price_sar": None}], id="no_price"),
    pytest.param([{"ladder_v2": True, "price_sar": "0.50"}], id="below_minimum"),
    pytest.param([{"ladder_v2": True, "price_sar": "19.90", "batch_id": "pro_t1_a",
                   "price_tier": 1, "months": None}], id="batch_without_months"),
])
def test_an_unusable_checkout_price_is_a_503(keys, answer):
    db = ladder_db("legal")
    real_rpc = db.rpc
    db.rpc = lambda name, params: (_Rpc(answer) if name == "promo_checkout_price"
                                   else real_rpc(name, params))
    with pytest.raises(LunaHTTPException) as exc:
        run(ps.create_checkout(db, USER, "pro"))
    assert exc.value.code is ErrorCode.PAYMENT_PRICING_UNAVAILABLE
    assert db.tables["payment_transactions"] == []


def test_a_tier1_group_without_a_batch_is_still_non_refundable(keys):
    """The row's truth wins: the group is stamped as given, so the first
    payment is non-refundable even though no offer object can be built."""
    db = ladder_db("legal")
    real_rpc = db.rpc
    answer = [{"ladder_v2": True, "price_sar": "19.90", "batch_id": None,
               "price_tier": None, "months": None, "intro_group": "legal"}]
    db.rpc = lambda name, params: (_Rpc(answer) if name == "promo_checkout_price"
                                   else real_rpc(name, params))
    quote = checkout(db, "pro")
    row = _row(db, quote["payment_id"])
    assert row["intro_offer_group"] == "legal" and "promo_batch_id" not in row
    assert quote["non_refundable"] is True and quote["intro_offer"] is None


# ── 2. flag off: offer_* are null on both surfaces ───────────────────────────


def test_flag_off_offer_keys_are_null_everywhere(keys):
    db = intro_db("legal")
    state = run(ps.intro_offer_state(db, USER))
    quote = checkout(db, "pro")
    for offer in (state, quote["intro_offer"]):
        assert offer["first_month_sar"] == "1.99"            # today's offer…
        assert offer["offer_sar"] is None and offer["offer_months"] is None
        assert offer["reason"] is None and offer["basic_ended_at"] is None


# ── 3. the credit basis is stamped at checkout and read at settlement ────────


def _cash_paid_pro(db):
    """A running pro term bought at list WITH a basic credit: 77.80 cash."""
    paid_row(db, plan_id="pro", amount="77.80", upgrade_credit_sar="17.10",
             paid_at=_iso(_now() - timedelta(days=4)))


def test_a_credited_checkout_stamps_its_basis_in_both_regimes(keys):
    on = ladder_db("individual", sub("pro", source="payment", days_left=26))
    _cash_paid_pro(on)
    q_on = checkout(on, "max")
    assert q_on["credit_sar"] == "67.43"
    assert _row(on, q_on["payment_id"])["credit_basis"] == "cash"

    off = FakeSupabase(sub("pro", source="payment", days_left=26))
    _cash_paid_pro(off)
    q_off = checkout(off, "max")
    assert q_off["credit_sar"] == "82.25"
    assert _row(off, q_off["payment_id"])["credit_basis"] == "price"   # 192 V6

    # …and an uncredited checkout stamps nothing at all.
    fresh = FakeSupabase(sub("free"))
    assert "credit_basis" not in _row(fresh, checkout(fresh, "pro")["payment_id"])


@pytest.mark.parametrize("flip_to", [True, False], ids=["quoted_off_paid_on", "quoted_on_paid_off"])
def test_a_credited_checkout_across_a_flip_keeps_its_stamped_basis(keys, monkeypatch, flip_to):
    """The quote's basis is the row's, not the flag's at settlement. Before
    the stamp, a 'price'-basis quote settling after the flip was re-derived on
    'cash' (smaller credit ⇒ held for review)."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    db.campaign_enabled = True
    db.intro_offer_enabled = True
    set_profession(db, "individual")
    db.ladder_v2 = not flip_to
    _cash_paid_pro(db)
    quote = checkout(db, "max")
    row = _row(db, quote["payment_id"])
    stamped = row.get("upgrade_credit_sar")

    db.ladder_v2 = flip_to                                    # the flip, mid-3DS
    verdict = ps._revalidate_credited_charge(db, {**row, "status": "paid"})
    assert verdict["ok"] is True and verdict["credit_owed_sar"] == stamped

    result = _settle(db, monkeypatch, quote, ps.to_halalas(quote["amount_sar"]))
    assert result["granted"] is True and result.get("review_reason") is None


# ── 4. tier_rules on /payments/subscription ──────────────────────────────────


@pytest.mark.parametrize("ladder", [False, True])
def test_the_subscription_payload_says_which_cancellation_rules_apply(keys, ladder):
    db = ladder_db("legal") if ladder else FakeSupabase(sub("free"))
    assert run(ss.get_subscription(db, USER))["early_adopter"]["tier_rules"] is ladder


# ── 5. the 192 model changes, through the backend ────────────────────────────


def test_v2_the_seat_keeps_the_quoted_terms_after_a_batch_edit(keys, monkeypatch):
    """(5a) An operator edits the batch between checkout and claim; the seat
    freezes what THIS customer was quoted — 19.90 for 2 months."""
    db = ladder_db("legal")
    quote = checkout(db, "pro")
    db._batch("pro_t1_a").update({"price_sar": "29.90", "months": 1})

    assert _settle(db, monkeypatch, quote, 1990)["granted"] is True
    seat = next(s for s in db.early_adopter_seats if s["user_id"] == USER)
    paid_at = ps._parse_ts(_row(db, quote["payment_id"])["paid_at"])
    assert seat["price_sar"] == "19.90"
    assert ps._parse_ts(seat["promo_ends_at"]) == paid_at + timedelta(days=60)


def test_v2_a_lapsed_legacy_max_holder_buying_pro_stays_at_49_90(keys):
    """(5b) A live in-window LEGACY seat blocks quotes for every plan except
    legacy-pro → max, so a lapsed legacy Max holder buying Pro is priced by
    that seat (49.90, today's rule) — never re-quoted into a tier."""
    db = ladder_db("legal", sub("max", source="payment", days_left=-3))
    legacy_seat(db, "max", days_left=20)
    quote = checkout(db, "pro")
    assert quote["amount_sar"] == "49.90" and quote["promo"] is None
    assert "promo_batch_id" not in _row(db, quote["payment_id"])


def test_after_a_rollback_a_batch_seat_carries_over_like_a_legacy_seat(keys):
    """(5c) Flag OFF: a live batch seat prices its own plan at its frozen price
    and carries over to the OTHER plan at plans.promo_price_sar."""
    db = FakeSupabase(sub("pro", source="payment", days_left=20))
    batch_seat(db, "pro_t1_a", days_left=40)
    current = ps.PRICE_CONTEXT_CURRENT
    assert ps._effective_price(db, USER, "pro", "94.90", context=current) == ps.q2("19.90")
    assert ps._effective_price(db, USER, "max", "289.90", context=current) == ps.q2("99.90")
    assert checkout(db, "max")["amount_sar"] != "289.90"     # the promo, minus a credit


def test_v2_refunding_a_switch_restores_the_seat_it_superseded(keys, monkeypatch, provider_refunds):
    """(5d / 192 V5) Pro Tier 1 holder switches to Max, then refunds the switch
    inside 24h: the Max seat goes and the Pro seat (window still open) comes
    back with its original 19.90 terms and window. The release keeps its two
    arguments — the SQL recognises the switch because the released seat's own
    payment is now 'refunded', which the refund path stamps first."""
    db = ladder_db("legal", sub("pro", source="payment", days_left=26))
    pay = _pro_tier1_holder(db)
    pro_seat = next(s for s in db.early_adopter_seats if s["payment_id"] == pay["payment_id"])
    window = pro_seat["promo_ends_at"]

    quote = checkout(db, "max")
    assert _settle(db, monkeypatch, quote, ps.to_halalas(quote["amount_sar"]))["granted"] is True
    assert pro_seat["release_reason"] == "upgraded"
    assert pro_seat["released_by_payment_id"] == quote["payment_id"]

    row = _row(db, quote["payment_id"])
    row["raw_payload"] = {"id": MOYASAR_ID, "fee": 173}
    run(ps.refund_payment(db, USER, quote["payment_id"]))

    asked = [p for n, p in db.rpc_params if n == "release_early_adopter_seat"]
    assert asked[-1] == {"p_user_id": USER, "p_reason": "refund"}
    live = [s for s in db.early_adopter_seats if s["user_id"] == USER and not s["released_at"]]
    assert live == [pro_seat]
    assert pro_seat["promo_ends_at"] == window and pro_seat["price_sar"] == "19.90"
    max_seat = next(s for s in db.early_adopter_seats if s.get("batch_id") == "max_t1_a")
    assert max_seat["release_reason"] == "refund"


def _switched(db, *, pro_days_left):
    """A Pro seat released 'upgraded' by switch payment S, and the live Max
    seat S claimed."""
    switch = paid_row(db, plan_id="max", amount="149.90", promo_batch_id="max_t1_a")
    old = batch_seat(db, "pro_t1_a", days_left=pro_days_left, reason="upgraded")
    old["released_by_payment_id"] = switch["payment_id"]
    batch_seat(db, "max_t1_a", days_left=100, payment_id=switch["payment_id"])
    return switch, old


def test_a_switch_refund_after_the_pro_window_restores_nothing(keys):
    db = ladder_db("legal")
    switch, old = _switched(db, pro_days_left=-1)
    switch["status"] = "refunded"
    assert ss.release_early_adopter_seat(db, USER, reason="refund", plan_id="max") is True
    assert old["released_at"] and old["release_reason"] == "upgraded"


def test_refunding_some_other_payment_restores_nothing(keys):
    """The live seat's OWN claiming payment must be the refunded one; a refund
    of anything else (here the switch payment is still 'paid') releases the
    live seat as today and gives nothing back."""
    db = ladder_db("legal")
    switch, old = _switched(db, pro_days_left=30)
    assert switch["status"] == "paid"
    assert ss.release_early_adopter_seat(db, USER, reason="refund", plan_id="max") is True
    assert old["released_at"] and old["release_reason"] == "upgraded"
    assert [s for s in db.early_adopter_seats if s["user_id"] == USER and not s["released_at"]] == []


def test_a_cancellation_never_sends_a_payment_id(keys):
    db = ladder_db("individual", sub("pro", source="payment", days_left=20))
    batch_seat(db, "pro_t2_a", days_left=30)
    run(ss.cancel_renewal(db, USER, reason="expensive"))
    asked = [p for n, p in db.rpc_params if n == "release_early_adopter_seat"]
    assert asked == [{"p_user_id": USER, "p_reason": "cancelled"}]


# ── 6. model divergences fixed ───────────────────────────────────────────────


@pytest.mark.parametrize("ladder", [False, True], ids=["flag_off", "flag_on"])
def test_only_the_first_profession_answer_unlocks_the_offer(keys, ladder):
    """A user whose FIRST answer was «individual» and who later switched to
    «legal» gets no Tier 1 / profession offer in either regime."""
    db = ladder_db("individual") if ladder else intro_db("individual")
    db.tables["users"][0]["profession_group"] = "legal"     # the later answer
    assert run(ps.intro_offer_state(db, USER))["eligible"] is False
    assert checkout(db, "pro")["amount_sar"] not in ("1.99", "19.90")


def _released(db, reason, minutes_ago, **extra):
    seat = batch_seat(db, "pro_t2_a", days_left=30, reason=reason)
    seat["released_at"] = _iso(_now() - timedelta(minutes=minutes_ago))
    seat.update(extra)
    return seat


def test_restore_undoes_only_when_the_latest_release_was_a_cancellation(keys):
    """M138:1249-1315 — the user's MOST RECENT release decides."""
    db = ladder_db("individual")
    _released(db, "cancelled", 30)
    _released(db, "refund", 5)                    # a refund AFTER the cancel
    assert db.rpc("restore_early_adopter_seat", {"p_user_id": USER}).execute().data is False

    db2 = ladder_db("individual")
    _released(db2, "refund", 30)
    cancelled = _released(db2, "cancelled", 5)
    assert db2.rpc("restore_early_adopter_seat", {"p_user_id": USER}).execute().data is True
    assert cancelled["released_at"] is None and cancelled["release_reason"] is None


def test_restore_with_a_live_seat_answers_true_and_writes_nothing(keys):
    db = ladder_db("individual")
    live = batch_seat(db, "pro_t2_a", days_left=30)
    before = dict(live)
    assert db.rpc("restore_early_adopter_seat", {"p_user_id": USER}).execute().data is True
    assert live == before


def test_a_restore_past_capacity_is_stamped_over_capacity(keys):
    db = open_db = FakeSupabase(sub("free"))
    open_db.campaign_seat_limit = 1
    batch_seat(db, "pro_t2_a", user_id=OTHER_USER, days_left=30)
    seat = _released(db, "cancelled", 5)
    assert db.rpc("restore_early_adopter_seat", {"p_user_id": USER}).execute().data is True
    assert seat["over_capacity"] is True


def test_status_reports_the_window_end_but_no_seat_once_it_has_passed(keys):
    """early_adopter_status: has_seat needs v_ends > now(); promo_ends_at is
    the live seat's window end either way (the wrapper hides it)."""
    db = ladder_db("individual")
    seat = batch_seat(db, "pro_t2_a", days_left=-1)
    answer = db.rpc("early_adopter_status", {"p_user_id": USER}).execute().data[0]
    assert answer["has_seat"] is False and answer["promo_ends_at"] == seat["promo_ends_at"]
    block = run(ss.get_subscription(db, USER))["early_adopter"]
    assert block["is_member"] is False and block["promo_ends_at"] is None


def test_legacy_over_capacity_does_not_depend_on_the_switch(keys):
    """138 stamps over_capacity from the live COUNT against seat_limit; the
    switch being off is not "full"."""
    db = FakeSupabase(sub("free"))
    db.campaign_enabled = False                   # switched off, plenty of room
    row = paid_row(db, plan_id="pro", amount="49.90")
    claim = ss.claim_early_adopter_seat(db, USER, row["payment_id"], plan_id="pro")
    assert claim["action"] == ss.CLAIM_ACTION_CLAIMED and claim["over_capacity"] is False


def test_after_a_rollback_a_batch_seat_survives_an_old_cancellation(keys):
    """192 V4: flag off, the 183 forfeiture would quote the list price to a
    user with an old 'cancelled' legacy row — but not while they hold a live
    in-window BATCH seat, whose carry-over must survive the rollback."""
    db = FakeSupabase(sub("pro", source="payment", days_left=20))
    legacy_seat(db, "pro", days_left=-30, reason="cancelled")
    batch_seat(db, "pro_t2_a", days_left=40)
    current = ps.PRICE_CONTEXT_CURRENT
    assert ps._effective_price(db, USER, "max", "289.90", context=current) == ps.q2("99.90")
    assert ps._effective_price(db, USER, "pro", "94.90", context=current) == ps.q2("59.90")


# ── owner 2026-10-27: a refunded PURCHASE never used its tier ────────────────


def test_v2_a_refunded_purchase_is_quoted_the_same_tier_again(keys, monkeypatch, provider_refunds):
    """An individual buys Pro Tier 2, refunds it inside 24h, and comes back:
    Tier 2 again (59.90 for 2 months) — the refund voided the purchase, so the
    tier was never used. (Pro Tier 1's first payment is not self-refundable,
    which is why this runs on Tier 2; the dashboard path is below.)"""
    db = ladder_db("individual")
    first = checkout(db, "pro")
    assert first["promo"]["price_sar"] == "59.90"
    assert _settle(db, monkeypatch, first, 5990)["granted"] is True
    _row(db, first["payment_id"])["raw_payload"] = {"id": MOYASAR_ID, "fee": 173}
    run(ps.refund_payment(db, USER, first["payment_id"]))

    seat = next(s for s in db.early_adopter_seats if s["payment_id"] == first["payment_id"])
    assert seat["release_reason"] == "refund"
    again = checkout(db, "pro")
    assert again["promo"] == {"price_sar": "59.90", "months": 2, "list_sar": "94.90"}
    assert _row(db, again["payment_id"])["promo_batch_id"] == "pro_t2_a"


def test_v2_a_dashboard_refunded_tier1_offer_is_offered_again(keys, monkeypatch):
    """Same rule through the webhook (an operator's dashboard refund of the
    non-refundable Tier 1 month): the lawyer is offered 19.90 again."""
    db = ladder_db("legal")
    first = checkout(db, "pro")
    assert _settle(db, monkeypatch, first, 1990)["granted"] is True

    patch_fetch(monkeypatch, moyasar_payment(first["payment_id"], status="refunded", amount=1990))
    run(ps.handle_webhook_event(db, _event(first["payment_id"], event="payment_refunded")))

    assert run(ps.intro_offer_state(db, USER))["offer_sar"] == "19.90"
    again = checkout(db, "pro")
    assert again["promo"]["price_sar"] == "19.90"
    assert _row(db, again["payment_id"])["promo_batch_id"] == "pro_t1_a"


def test_v2_a_refunded_renewal_still_used_the_tier(keys, monkeypatch):
    """A seat that went with a refunded RENEWAL was a tier really held (its
    claiming purchase stays 'paid'): an individual is quoted the list price
    next, a lawyer whose Tier 1 renewal was refunded falls to Tier 2."""
    db = ladder_db("individual")
    purchase = paid_row(db, plan_id="pro", amount="59.90", promo_batch_id="pro_t2_a",
                        paid_at=_iso(_now() - timedelta(days=31)))
    batch_seat(db, "pro_t2_a", payment_id=purchase["payment_id"], days_left=29)
    renewal = paid_row(db, plan_id="pro", amount="59.90", initiated_by="renewal",
                       period_start=_iso(_now() - timedelta(hours=1)),
                       raw_payload={"id": MOYASAR_ID, "fee": 173})
    patch_fetch(monkeypatch, moyasar_payment(renewal["payment_id"], status="refunded", amount=5990))
    run(ps.handle_webhook_event(db, _event(renewal["payment_id"], event="payment_refunded")))

    seat = next(s for s in db.early_adopter_seats if s["payment_id"] == purchase["payment_id"])
    assert seat["release_reason"] == "refund" and purchase["status"] == "paid"
    quote = checkout(db, "pro")
    assert quote["amount_sar"] == "94.90" and quote["promo"] is None

    db2 = ladder_db("legal")
    refund_released_seat(db2, "pro_t1_a", purchase_status="paid")
    assert checkout(db2, "pro")["promo"]["price_sar"] == "59.90"
