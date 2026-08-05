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
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services import payment_service as ps
from shared.config import get_settings

USER = "11111111-1111-1111-1111-111111111111"
OTHER_USER = "99999999-9999-9999-9999-999999999999"
MOYASAR_ID = "33333333-3333-3333-3333-333333333333"
WEBHOOK_SECRET = "whsec_test_value"


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
        return all(row.get(c) == v for c, v in self._eq) and all(
            row.get(c) in vals for c, vals in self._in
        )

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])

        if self._op == "insert":
            row = dict(self._payload)
            row.setdefault("payment_id", str(uuid.uuid4()))
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
            self.tables["user_subscriptions"] = [
                {"user_id": row["user_id"], "plan_id": row["plan_id"],
                 "source": "payment", "expires_at": expiry}
            ]
            return _Rpc([{"plan_id": row["plan_id"], "name_ar": "x", "expires_at": expiry}])

        if name == "revoke_plan_grant":
            if row is None:
                return _Rpc([{"action": "payment_not_found"}])
            if row.get("status") != "refunded":
                # The real RPC raises this — the ordering guard under test.
                raise RuntimeError("payment_not_refunded")
            if row.get("revoked_at"):
                return _Rpc([{"action": "already_revoked"}])
            row["revoked_at"] = _iso(_now())
            action = "restored" if row.get("prior_plan_id") else "subtracted"
            return _Rpc([{"plan_id": row["plan_id"], "name_ar": "x",
                          "expires_at": None, "action": action}])

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


def sub(plan_id="free", source="signup", days_left: Optional[float] = None):
    expires = _iso(_now() + timedelta(days=days_left)) if days_left is not None else None
    return {"user_id": USER, "plan_id": plan_id, "source": source,
            "expires_at": expires, "status": "active"}


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
    assert db.calls == ["stamp_payment_prior_snapshot", "grant_plan"]
    row = db.tables["payment_transactions"][0]
    assert row["prior_plan_id"] == "pro"      # what a refund would restore


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
    assert db.calls == ["stamp_payment_prior_snapshot", "grant_plan"]


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
