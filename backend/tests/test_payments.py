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
from typing import Any, Optional

import pytest

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services import payment_service as ps
from backend.app.services import subscription_service as ss
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

PLAN_ROWS = [
    {"plan_id": "free", "name_ar": "المجانية", "price_sar": None, "duration_days": None,
     "billing_cycle": None},
    {"plan_id": "basic", "name_ar": "الأساسية", "price_sar": "49.90", "duration_days": 7,
     "billing_cycle": "one_time"},
    {"plan_id": "pro", "name_ar": "الاحترافية", "price_sar": "89.90", "duration_days": 30,
     "billing_cycle": "one_time"},
    {"plan_id": "max", "name_ar": "القصوى", "price_sar": "189.90", "duration_days": 30,
     "billing_cycle": "one_time"},
    {"plan_id": "marketing_lawyer", "name_ar": "عرض المحامين", "price_sar": None,
     "duration_days": 7, "billing_cycle": None},
]


class _Result:
    def __init__(self, data: Any):
        self.data = data


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

    # builder ------------------------------------------------------------
    def select(self, *_a, **_k):
        self._op = "select"
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
        if self._limit is not None:
            matched = matched[: self._limit]
        if self._single:
            return _Result(dict(matched[0]) if matched else None)
        return _Result([dict(r) for r in matched])


class FakeSupabase:
    """Rows + the three payment RPCs, with their real preconditions."""

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
            "users": [{"user_id": USER, "full_name_ar": CUSTOMER_NAME,
                       "email": CUSTOMER_EMAIL}],
        }
        self.calls: list[str] = []      # rpc + write sequence, in order
        self.writes: list[tuple] = []

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    # -- RPCs ------------------------------------------------------------
    def rpc(self, name: str, params: dict):
        self.calls.append(name)
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


def moyasar_payment(payment_id: str, *, status="paid", amount=8990, currency="SAR", live=False):
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
    return run(ps.create_checkout(db, user_id, plan_id))


def paid_row(db, plan_id="pro", amount="89.90", **over):
    """A payment_transactions row already marked paid."""
    row = {
        "payment_id": str(uuid.uuid4()), "user_id": USER, "plan_id": plan_id,
        "amount_sar": amount, "currency": "SAR", "status": "paid", "provider": "moyasar",
        "provider_ref": MOYASAR_ID, "paid_at": _iso(_now() - timedelta(hours=2)),
        "fulfilled_at": _iso(_now() - timedelta(hours=2)),
        "created_at": _iso(_now() - timedelta(hours=2)),
        "vat_amount_sar": "11.73", "net_amount_sar": "78.17", "upgrade_credit_sar": "0.00",
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
     ("189.90", 18990, "165.13", "24.77")],
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
    assert result["amount_halalas"] == 8990
    assert result["amount_sar"] == "89.90"
    assert result["credit_sar"] == "0.00"
    assert result["publishable_key"] == "pk_test_abc"
    assert result["callback_url"].endswith("/pay/callback")

    row = db.tables["payment_transactions"][0]
    assert row["status"] == "initiated" and row["provider"] == "moyasar"
    assert (row["vat_amount_sar"], row["net_amount_sar"]) == ("11.73", "78.17")
    # The snapshot columns belong to the RPC, never to checkout.
    assert "prior_plan_id" not in row and "prior_expires_at" not in row


def test_upgrade_credit_matches_plan_example(keys):
    """pro with 26 days left → max: 189.90 − round(26/30 × 89.90, 2) = 111.99."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    result = checkout(db, "max")
    assert result["credit_sar"] == "77.91"
    assert result["amount_sar"] == "111.99"
    assert result["amount_halalas"] == 11199
    row = db.tables["payment_transactions"][0]
    assert row["upgrade_credit_sar"] == "77.91"
    # VAT is split on the CHARGED amount, not the catalog price.
    assert row["vat_amount_sar"] == "14.61" and row["net_amount_sar"] == "97.38"


def test_code_sourced_plan_earns_no_credit(keys):
    """Otherwise a promo code silently converts into a cash discount."""
    db = FakeSupabase(sub("pro", source="code", days_left=26))
    assert checkout(db, "max")["credit_sar"] == "0.00"


def test_same_plan_repurchase_earns_no_credit(keys):
    """grant_plan stacks the days instead — crediting them would pay twice."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    assert checkout(db, "pro")["amount_sar"] == "89.90"


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
    assert checkout(db, "basic")["amount_halalas"] == 4990


def test_marketing_plan_does_not_block_a_purchase(keys):
    db = FakeSupabase(sub("marketing_lawyer", source="code", days_left=5))
    assert checkout(db, "basic")["amount_halalas"] == 4990


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
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=11199))

    run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert db.calls == ["stamp_payment_prior_snapshot", "grant_plan", "stamp_usage_reset"]
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
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=4990))

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
    assert db.calls == ["stamp_payment_prior_snapshot", "grant_plan", "stamp_usage_reset"]


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
    assert db.calls == ["revoke_plan_grant"]


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
    assert provider_refunds == [(MOYASAR_ID, 8652)]        # 8990 − 338
    assert result["refunded_amount_sar"] == "86.52"   # 89.90 − 3.38
    assert result["refund_fee_sar"] == "3.38"   # provider 1.73 + Moyasar refund fee 1.15 + margin 0.50
    assert result["status"] == "refunded"


def test_refund_marks_refunded_before_revoking(keys, provider_refunds):
    """revoke_plan_grant raises 'payment_not_refunded' on a row that still says
    paid — the FakeSupabase RPC enforces that precondition."""
    db = FakeSupabase(sub("pro", source="payment", days_left=29))
    row = paid_row(db)
    result = run(ps.refund_payment(db, USER, row["payment_id"]))
    assert db.calls == ["revoke_plan_grant"]
    assert result["revoke_action"] in ps.REVOKE_ACTIONS_OK
    assert db.tables["payment_transactions"][0]["revoked_at"]


def test_refund_of_an_upgrade_restores_the_prior_plan(keys, provider_refunds):
    db = FakeSupabase(sub("max", source="payment", days_left=30))
    row = paid_row(db, plan_id="max", amount="111.99", prior_plan_id="pro",
                   prior_expires_at=_iso(_now() + timedelta(days=26)))
    result = run(ps.refund_payment(db, USER, row["payment_id"]))
    assert result["revoke_action"] == "restored"
    assert result["refunded_amount_sar"] == "108.61"       # 111.99 − 3.38


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
    assert rows[0]["amount_sar"] == "89.90" and rows[0]["amount_halalas"] == 8990
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
    assert quote["refund_quote_amount_sar"] == "86.52"

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
    bought 3× left 90 days → 90/30 × 89.90 = 269.70 credit against a 189.90
    plan → clamped by `price − 1.00` to 188.90 → **a 30-day `max` for 1.00
    SAR**, below the ~1.73 SAR the card network charges us to collect it."""
    db = FakeSupabase(sub("pro", source="payment", days_left=90))
    result = checkout(db, "max")
    assert result["credit_sar"] == "89.90"       # one period of pro, never three
    assert result["amount_sar"] == "100.00"
    assert result["amount_halalas"] == 10000


@pytest.mark.parametrize("days_left", [30, 45, 90, 365])
def test_credit_never_exceeds_one_period_at_any_stack_depth(keys, days_left):
    db = FakeSupabase(sub("pro", source="payment", days_left=days_left))
    assert checkout(db, "max")["credit_sar"] == "89.90"


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
            {**moyasar_payment(pid, amount=11199), "id": str(uuid.uuid4())},
        )
        results.append(run(ps.verify_payment(db, USER, MOYASAR_ID)))

    assert [r["granted"] for r in results] == [True, False, False]
    assert db.calls == ["stamp_payment_prior_snapshot", "grant_plan", "stamp_usage_reset"]  # once, total
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
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=11199))

    result = run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert result["granted"] is False
    assert result["review_reason"] == "credit_no_longer_owed"
    assert db.calls == []                       # neither snapshot nor grant ran

    row = db.tables["payment_transactions"][0]
    assert row["status"] == "paid" and not row.get("fulfilled_at")


def test_a_stale_quote_cannot_be_banked(keys, monkeypatch):
    """Prod held a payable 100.00 SAR `max` quote for three days. Past the TTL
    the discount is not honoured on its own word."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    pid = checkout(db, "max")["payment_id"]
    db.tables["payment_transactions"][0]["created_at"] = _iso(_now() - timedelta(hours=25))
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=11199))

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
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=11199))

    result = run(ps.verify_payment(db, USER, MOYASAR_ID))
    assert result["granted"] is True
    assert db.calls == ["stamp_payment_prior_snapshot", "grant_plan", "stamp_usage_reset"]


def test_a_credited_upgrade_is_idempotent_across_both_paths(keys, monkeypatch):
    """The webhook lands after /verify already granted, so the subscription now
    reads as the plan this very payment bought. Re-deriving there would compare
    the credit against its own result — `fulfilled_at` is the anchor that stops
    it, exactly as it stops grant_plan."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    pid = checkout(db, "max")["payment_id"]
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=11199))

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
            plan["price_sar"] = "149.90"
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=11199))

    assert run(ps.verify_payment(db, USER, MOYASAR_ID))["granted"] is True


def test_a_held_payment_stays_refundable_by_the_customer(keys, monkeypatch, provider_refunds):
    """The reason holding is acceptable at all: the customer is one click from
    their money back, and revoke_plan_grant answers `not_fulfilled` cleanly on a
    grant that never ran."""
    db = FakeSupabase(sub("pro", source="payment", days_left=26))
    pid = checkout(db, "max")["payment_id"]
    db.tables["user_subscriptions"] = []
    patch_fetch(monkeypatch, moyasar_payment(pid, amount=11199))
    run(ps.verify_payment(db, USER, MOYASAR_ID))

    result = run(ps.refund_payment(db, USER, pid))
    assert result["status"] == "refunded"
    assert result["revoke_action"] == "not_fulfilled"
    assert result["revoked"] is True
    assert provider_refunds == [(MOYASAR_ID, 11199 - 340)]   # legacy-fee fallback


# ═══════════════════════════════════════════════════════════════════════════
# M-1 — the refund ladder unwinds newest-first
# ═══════════════════════════════════════════════════════════════════════════


def _ladder(db):
    """basic → pro → max: 189.90 paid across three rungs, each upgrade priced
    against the one below it. Audit logs from prod confirm the shape
    (`checkout_initiated plan=max from_plan=pro credit=89.90 amount=100.00`)."""
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
        db, plan_id="max", amount="100.00", upgrade_credit_sar="89.90",
        prior_plan_id="pro", prior_expires_at=_iso(_now() + timedelta(days=29)),
        created_at=_iso(_now() - timedelta(hours=1)),
        paid_at=_iso(_now() - timedelta(hours=1)),
        fulfilled_at=_iso(_now() - timedelta(hours=1)),
    )
    return basic, pro, top


def test_refund_of_a_superseded_payment_is_blocked(keys, provider_refunds):
    """EXPLOIT 2. Refunding the basic and the pro used to return 85.90 with both
    hitting `plan_switched` — the subscription untouched, a 189.90 `max` term
    standing for a net 104.00, full entitlement retained."""
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
                     "renewal_cancelled_at": None}


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
