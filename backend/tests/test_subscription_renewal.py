"""Subscription auto-renewal — consent, tokenization, the job, dunning.

Plan: `.claude/plans/subscription_auto_renewal.md` §6 (tokenize), §7 (the job),
§8 (dunning). Covers ``payment_method_service`` and ``renewal_service`` plus the
three edits auto-renewal makes to ``payment_service``.

Same shape as ``test_payments.py``: no live DB and no network. Supabase is an
in-memory PostgREST stand-in that holds real rows and applies the filters, so
the REAL selection/insert/update code runs; Moyasar is monkeypatched at
``charge_saved_card`` / ``revoke_token_at_provider``.

⚠ This fake is a STRICTER stand-in than the one in ``test_payments.py``, in two
ways that are the point of the file:

  * ``grant_plan`` mirrors migration 092's REAL branch logic (same-plan renewal
    of a live term stacks onto ``expires_at``; anything else opens a window from
    ``now()``). A fake that always answered ``now() + 30d`` would let the term
    arithmetic regress silently — and shaving hours off every cycle is a bug
    nobody notices for months.
  * ``payment_transactions`` enforces migration 132's real constraints:
    ``uniq_payment_renewal_period`` — unique on ``(user_id, plan_id,
    period_start)`` where ``initiated_by='renewal' AND status <> 'failed'`` —
    plus the two CHECKs that make that index total. That index IS the
    double-charge guard; a fake without it would let a Python-only
    "idempotency" pass its own test. Note the predicate: only a ``failed`` row
    releases a period's slot, which is exactly what lets the dunning ladder
    retry while nothing can charge a period twice.

The load-bearing assertions:
  * ``test_two_sweeps_charge_exactly_once`` — the whole job in one line.
  * ``test_a_cancelled_subscriber_is_never_charged`` — the promise 120's UI has
    been making since before an engine existed.
  * ``test_a_code_grant_is_never_charged`` — no card behind it, ever.
  * ``test_term_extends_from_expires_at_not_now`` — plan trap 3.
  * ``test_flag_off_*`` — merging this with the flag down changes nothing.
  * ``test_a_renewal_row_survives_a_new_checkout`` — plan §7's ⚠, the bug that
    would otherwise be caused BY the user and be unexplainable TO them.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services import payment_method_service as pm
from backend.app.services import payment_service as ps
from backend.app.services import receipt_service
from backend.app.services import renewal_service as rs
from shared.config import get_settings

USER = "11111111-1111-1111-1111-111111111111"
OTHER_USER = "99999999-9999-9999-9999-999999999999"
TOKEN = "token_live_abc123"
CUSTOMER_NAME = "محمد الفلاتة"
CUSTOMER_EMAIL = "buyer@example.com"
# 132 CHECK-enforces lowercase hex sha256 on consent_text_hash, so fixtures use
# a real digest rather than a readable placeholder — the constraint is modelled
# in the fake and a placeholder would fail it (which is the point).
FAKE_HASH = pm.consent_text_hash("a disclosure")


def run(coro):
    return asyncio.run(coro)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(value) -> Optional[datetime]:
    return rs._parse_ts(value)


# ---------------------------------------------------------------------------
# In-memory PostgREST + RPC stand-in
# ---------------------------------------------------------------------------

PLAN_ROWS = [
    {"plan_id": "free", "name_ar": "المجانية", "price_sar": None, "duration_days": None,
     "billing_cycle": None},
    # basic stays one_time — it says «بدون تجديد تلقائي» on the card and must
    # never be picked up by the job.
    {"plan_id": "basic", "name_ar": "الأساسية", "price_sar": "49.90", "duration_days": 7,
     "billing_cycle": "one_time"},
    {"plan_id": "pro", "name_ar": "الاحترافية", "price_sar": "89.90", "duration_days": 30,
     "billing_cycle": "recurring_30d"},
    {"plan_id": "max", "name_ar": "القصوى", "price_sar": "189.90", "duration_days": 30,
     "billing_cycle": "recurring_30d"},
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
        self._cmp: list[tuple[str, str, Any]] = []
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
        assert val == "null", f"unsupported is_ value {val!r}"
        self._is_null.append(col)
        return self

    def gt(self, col, val):
        self._cmp.append((col, ">", val))
        return self

    def gte(self, col, val):
        self._cmp.append((col, ">=", val))
        return self

    def lt(self, col, val):
        self._cmp.append((col, "<", val))
        return self

    def lte(self, col, val):
        self._cmp.append((col, "<=", val))
        return self

    def order(self, col, desc=False):
        self._order, self._desc = col, desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    # execution ----------------------------------------------------------
    @staticmethod
    def _compare(actual, op, expected) -> bool:
        if actual is None:
            return False
        # Timestamps arrive as ISO strings; compare them as datetimes so
        # offsets/precision differences don't decide a money question.
        a, b = actual, expected
        if isinstance(a, str) and isinstance(b, str):
            pa, pb = _parse(a), _parse(b)
            if pa is not None and pb is not None:
                a, b = pa, pb
        try:
            if op == ">":
                return a > b
            if op == ">=":
                return a >= b
            if op == "<":
                return a < b
            return a <= b
        except TypeError:
            return False

    def _matches(self, row: dict) -> bool:
        return (
            all(row.get(c) == v for c, v in self._eq)
            and all(row.get(c) in vals for c, vals in self._in)
            and all(row.get(c) is None for c in self._is_null)
            and all(self._compare(row.get(c), op, v) for c, op, v in self._cmp)
        )

    def _guard_missing_columns(self) -> None:
        """Simulate a backend running ahead of migration 132 (42703)."""
        missing = getattr(self.db, "missing_columns", set())
        if not missing:
            return
        touched = {c for c, _ in self._eq} | {c for c, _, _ in self._cmp}
        touched |= set(self._is_null) | {c for c, _ in self._in}
        if isinstance(self._payload, dict):
            touched |= set(self._payload)
        clash = touched & missing
        if clash:
            raise RuntimeError(
                f"column {self.table}.{sorted(clash)[0]} does not exist (42703)"
            )

    def _enforce_constraints(self, row: dict, rows: list[dict]) -> None:
        """Migration 132's real guards — the indexes AND the CHECKs they need.

        Transcribed from ``132_subscription_auto_renewal.sql``. The predicate of
        ``uniq_payment_renewal_period`` is the whole design: only a ``failed``
        row releases a period's slot, which is what lets the dunning ladder
        retry while nothing can charge a period twice.
        """
        if self.table == "payment_transactions":
            is_renewal = row.get("initiated_by") == "renewal"
            # payment_transactions_renewal_period_check — makes the partial
            # index total (a renewal with a NULL period would fall out of it).
            if is_renewal != (row.get("period_start") is not None):
                raise RuntimeError(
                    "new row violates check constraint "
                    '"payment_transactions_renewal_period_check" (23514)'
                )
            # payment_transactions_renewal_attempt_check
            if not is_renewal and int(row.get("renewal_attempt") or 0) != 0:
                raise RuntimeError(
                    "new row violates check constraint "
                    '"payment_transactions_renewal_attempt_check" (23514)'
                )
            if is_renewal and row.get("status") != "failed" and row.get("user_id"):
                key = (row.get("user_id"), row.get("plan_id"), row.get("period_start"))
                for other in rows:
                    if (
                        other.get("initiated_by") == "renewal"
                        and other.get("status") != "failed"
                        and (other.get("user_id"), other.get("plan_id"),
                             other.get("period_start")) == key
                    ):
                        raise RuntimeError(
                            "duplicate key value violates unique constraint "
                            '"uniq_payment_renewal_period" (23505)'
                        )

        if self.table == "payment_methods":
            # payment_methods_consent_hash_check + the NOT NULLs. A row that
            # cannot be stored without consent is the schema doing the job the
            # renewal job would otherwise have to be trusted to do.
            digest = row.get("consent_text_hash")
            if not row.get("consent_given_at"):
                raise RuntimeError("null value in column consent_given_at (23502)")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise RuntimeError(
                    "new row violates check constraint "
                    '"payment_methods_consent_hash_check" (23514)'
                )
            last4 = row.get("last4")
            if last4 is not None and not re.fullmatch(r"[0-9]{4}", str(last4)):
                raise RuntimeError(
                    "new row violates check constraint "
                    '"payment_methods_last4_check" (23514)'
                )
            if row.get("revoked_at") is None:
                for other in rows:
                    if other.get("user_id") == row.get("user_id") and not other.get("revoked_at"):
                        raise RuntimeError(
                            "duplicate key value violates unique constraint "
                            '"uniq_payment_method_active_per_user" (23505)'
                        )

    def execute(self):
        self._guard_missing_columns()
        rows = self.db.tables.setdefault(self.table, [])

        if self._op == "insert":
            row = dict(self._payload)
            if self.table == "payment_transactions":
                row.setdefault("payment_id", str(uuid.uuid4()))
                row.setdefault("initiated_by", "user")   # the column DEFAULT
                row.setdefault("renewal_attempt", 0)
            self._enforce_constraints(row, rows)
            if self.table == "payment_methods":
                row.setdefault("payment_method_id", str(uuid.uuid4()))
            elif self.table != "payment_transactions":
                row.setdefault("id", str(uuid.uuid4()))
            row.setdefault("created_at", _iso(_now()))
            rows.append(row)
            self.db.writes.append((self.table, "insert"))
            return _Result([dict(row)])

        matched = [r for r in rows if self._matches(r)]

        if self._op == "update":
            for r in matched:
                r.update(self._payload)
            self.db.writes.append((self.table, "update", dict(self._payload)))
            return _Result([dict(r) for r in matched])

        if self._order:
            matched.sort(key=lambda r: str(r.get(self._order) or ""), reverse=self._desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return _Result([dict(r) for r in matched])


class _Rpc:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return _Result(self._data)


class FakeSupabase:
    def __init__(self, subscription: Optional[dict] = None):
        self.tables: dict[str, list[dict]] = {
            "plans": [dict(p) for p in PLAN_ROWS],
            "user_subscriptions": [dict(subscription)] if subscription else [],
            "payment_transactions": [],
            "payment_methods": [],
            "audit_logs": [],
            "users": [{"user_id": USER, "full_name_ar": CUSTOMER_NAME,
                       "email": CUSTOMER_EMAIL}],
        }
        self.calls: list[str] = []
        self.writes: list[tuple] = []
        self.missing_columns: set[str] = set()

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def _plan(self, plan_id):
        return next((p for p in self.tables["plans"] if p["plan_id"] == plan_id), None)

    def _sub(self, user_id):
        return next(
            (s for s in self.tables["user_subscriptions"] if s.get("user_id") == user_id),
            None,
        )

    def rpc(self, name: str, params: dict):
        self.calls.append(name)
        payment_id = params.get("p_payment_id")
        row = next(
            (r for r in self.tables["payment_transactions"]
             if r.get("payment_id") == payment_id),
            None,
        )

        if name == "stamp_payment_prior_snapshot":
            prior = None
            if row is not None and not row.get("fulfilled_at"):
                sub = self._sub(row.get("user_id"))
                if sub and sub.get("plan_id") != row.get("plan_id"):
                    prior = sub.get("plan_id")
                    row["prior_plan_id"] = prior
                    row["prior_expires_at"] = sub.get("expires_at")
            return _Rpc([{"prior_plan_id": prior}])

        if name == "grant_plan":
            # MIRRORS MIGRATION 092 EXACTLY — see the module docstring. The
            # renewal path's whole term arithmetic is this branch.
            if row is None:
                raise RuntimeError("payment_not_found")
            if row.get("user_id") != params.get("p_user_id") or row.get("plan_id") != params.get("p_plan_id"):
                raise RuntimeError("payment_mismatch")
            if row.get("status") != "paid":
                raise RuntimeError("payment_not_paid")

            sub = self._sub(row["user_id"])
            if row.get("fulfilled_at"):
                return _Rpc([{  # retry — current state, unchanged
                    "plan_id": (sub or {}).get("plan_id"),
                    "name_ar": "x",
                    "expires_at": (sub or {}).get("expires_at"),
                }])

            plan = self._plan(row["plan_id"]) or {}
            dur = plan.get("duration_days")
            cur_expires = _parse((sub or {}).get("expires_at"))
            if dur is None:
                new_expires = None
            elif (
                sub and sub.get("plan_id") == row["plan_id"]
                and cur_expires is not None and cur_expires > _now()
            ):
                # Same plan, still live → STACK on the remaining term.
                new_expires = _iso(cur_expires + timedelta(days=int(dur)))
            else:
                new_expires = _iso(_now() + timedelta(days=int(dur)))

            row["fulfilled_at"] = _iso(_now())
            patch = {"user_id": row["user_id"], "plan_id": row["plan_id"],
                     "source": params.get("p_source") or "payment",
                     "expires_at": new_expires}
            if sub is None:
                self.tables["user_subscriptions"].append(patch)
            else:
                sub.update(patch)          # MERGE, never replace
            return _Rpc([{"plan_id": row["plan_id"], "name_ar": plan.get("name_ar"),
                          "expires_at": new_expires}])

        if name == "stamp_usage_reset":
            # 137: a renewal charge resets like any other paid purchase. This
            # suite asserts on the renewal's plan/expiry bookkeeping, not on the
            # meters, so the fake reports the branch without mutating anything.
            return _Rpc([{"action": "reset"}])

        if name == "revoke_plan_grant":
            return _Rpc([{"action": "subtracted"}])

        raise AssertionError(f"unexpected rpc {name}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _env(monkeypatch, *, renewal: str):
    monkeypatch.setenv("MOYASAR_SECRET_KEY", "sk_test_abc")
    monkeypatch.setenv("MOYASAR_PUBLISHABLE_KEY", "pk_test_abc")
    # Empty, not unset: the repo .env carries a real app password on dev
    # machines and no test may open an SMTP socket.
    monkeypatch.setenv("RECEIPTS_SMTP_PASSWORD", "")
    monkeypatch.setenv("SUBSCRIPTION_AUTO_RENEWAL_ENABLED", renewal)
    get_settings.cache_clear()


@pytest.fixture
def flag_off(monkeypatch):
    _env(monkeypatch, renewal="false")
    yield
    get_settings.cache_clear()


@pytest.fixture
def flag_on(monkeypatch):
    _env(monkeypatch, renewal="true")
    yield
    get_settings.cache_clear()


@pytest.fixture
def no_smtp(monkeypatch):
    """Belt to the RECEIPTS_SMTP_PASSWORD braces — never touch a real socket."""
    sent: list[tuple] = []
    monkeypatch.setattr(
        receipt_service, "_smtp_send_sync",
        lambda to, subject, html: sent.append((to, subject)),
    )
    return sent


class _Charges(list):
    """A recording list that also carries the scripted provider outcome."""

    script: dict


@pytest.fixture
def charges(monkeypatch):
    """Record every token charge and script its outcome."""
    calls = _Charges()
    script: dict[str, Any] = {"status": "paid", "exc": None}

    async def _charge(*, token, amount_halalas, description, payment_id, metadata=None):
        calls.append({"token": token, "amount": amount_halalas,
                      "payment_id": payment_id, "description": description})
        if script["exc"] is not None:
            raise script["exc"]
        return {
            "id": str(uuid.uuid4()),
            "status": script["status"],
            "amount": amount_halalas,
            "currency": "SAR",
            "live": False,
            "metadata": {"payment_id": payment_id},
            "source": {"type": "creditcard", "company": "mada",
                       "number": "4111-11XX-XXXX-1111", "token": token,
                       "message": "Insufficient funds"},
        }

    monkeypatch.setattr(ps, "charge_saved_card", _charge)
    calls.script = script
    return calls


@pytest.fixture
def provider_revokes(monkeypatch):
    revoked: list[str] = []
    monkeypatch.setattr(
        pm, "revoke_token_at_provider", lambda token: (revoked.append(token), True)[1]
    )
    return revoked


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def sub(
    plan_id="pro",
    *,
    source="payment",
    hours_left: Optional[float] = 6,
    cancelled_at=None,
    failed_count=0,
    attempt_at=None,
    user_id=USER,
):
    expires = _iso(_now() + timedelta(hours=hours_left)) if hours_left is not None else None
    return {
        "user_id": user_id, "plan_id": plan_id, "source": source,
        "started_at": _iso(_now() - timedelta(days=30)), "expires_at": expires,
        "renewal_cancelled_at": cancelled_at,
        "renewal_attempt_at": attempt_at,
        "renewal_failed_count": failed_count,
    }


def store_method(db, *, user_id=USER, token=TOKEN, consent=True, revoked=False):
    row = {
        "payment_method_id": str(uuid.uuid4()),
        "user_id": user_id,
        "provider": "moyasar",
        "provider_token": token,
        "brand": "mada",
        "last4": "1111",
        "exp_month": 12,
        "exp_year": 2030,
        "consent_given_at": _iso(_now() - timedelta(days=30)) if consent else None,
        "consent_text_hash": FAKE_HASH if consent else None,
        "revoked_at": _iso(_now()) if revoked else None,
        "created_at": _iso(_now() - timedelta(days=30)),
    }
    db.tables["payment_methods"].append(row)
    return row


def renewal_rows(db):
    return [r for r in db.tables["payment_transactions"]
            if r.get("initiated_by") == "renewal"]


def sweep(db):
    return run(rs.run_due_renewals(db))


# ═══════════════════════════════════════════════════════════════════════════
# THE FLAG — merging this with it off must change behaviour for zero users
# ═══════════════════════════════════════════════════════════════════════════


def test_flag_defaults_to_off():
    """The single most important line in this feature."""
    get_settings.cache_clear()
    try:
        from shared.config import Settings

        assert Settings.model_fields["SUBSCRIPTION_AUTO_RENEWAL_ENABLED"].default is False
    finally:
        get_settings.cache_clear()


def test_flag_off_sweep_does_nothing(flag_off, charges):
    db = FakeSupabase(sub())
    store_method(db)
    assert sweep(db) == {"scanned": 0, "disabled": 1}
    assert charges == []
    assert db.writes == [] and db.calls == []


def test_flag_off_checkout_advertises_no_recurring_consent(flag_off):
    db = FakeSupabase(sub("free", source="signup", hours_left=None))
    result = run(ps.create_checkout(db, USER, "pro"))
    assert result["requires_recurring_consent"] is False
    assert result["recurring_disclosure_ar"] is None


def test_flag_off_consent_endpoint_answers_cleanly(flag_off):
    """A clean "not enabled" shape, not an exception the page has to swallow."""
    db = FakeSupabase(sub("free", source="signup", hours_left=None))
    pid = run(ps.create_checkout(db, USER, "pro"))["payment_id"]
    result = run(ps.record_recurring_consent(db, USER, pid, accepted=True))
    assert result == {"enabled": False, "accepted": False,
                      "payment_id": pid, "consent_given_at": None}
    assert db.tables["audit_logs"] == [] or all(
        (r.get("metadata") or {}).get("event") != pm.CONSENT_EVENT
        for r in db.tables["audit_logs"]
    )


def test_flag_off_never_stores_a_card(flag_off):
    """Even handed a payload with a token in it, nothing is written."""
    db = FakeSupabase(sub("free", source="signup", hours_left=None))
    row = {"payment_id": str(uuid.uuid4()), "user_id": USER, "plan_id": "pro"}
    fetched = {"id": "x", "source": {"type": "creditcard", "token": TOKEN}}
    assert run(pm.capture_payment_method(db, row, fetched)) is None
    assert db.tables["payment_methods"] == []


def test_flag_off_method_endpoint_is_inert(flag_off):
    """The route short-circuits before touching a table 132 may not have created,
    and "feature off" is indistinguishable from "no card" to the caller."""
    assert pm.auto_renewal_enabled() is False
    empty = pm.describe_method(None)
    assert empty["has_method"] is False
    assert all(v is None for k, v in empty.items() if k != "has_method")


def test_the_method_shape_is_flat_and_tokenless():
    """The contract إعدادات الحساب reads. `has_method` is the only field it may
    branch on — everything else is display."""
    row = {"payment_method_id": "m", "provider_token": TOKEN, "provider": "moyasar",
           "brand": "mada", "last4": "1111", "exp_month": 12, "exp_year": 2030,
           "consent_given_at": "2026-08-11T00:00:00+00:00", "created_at": "x"}
    described = pm.describe_method(row)
    assert set(described) == {
        "has_method", "payment_method_id", "provider", "brand", "last4",
        "exp_month", "exp_year", "consent_given_at", "created_at",
    }
    assert described["has_method"] is True and described["last4"] == "1111"


def test_the_renewal_job_is_registered_only_behind_the_flag():
    """A source-level guard, because the alternative is running the lifespan.

    If somebody ever lifts ``scheduler.add_job(id="subscription_renewals")`` out
    of its ``if settings.SUBSCRIPTION_AUTO_RENEWAL_ENABLED:`` block, a deploy
    starts charging cards. That is worth one ugly test.
    """
    source = Path(__file__).resolve().parents[1].joinpath("app", "main.py").read_text(
        encoding="utf-8"
    )
    guard = "if settings.SUBSCRIPTION_AUTO_RENEWAL_ENABLED:"
    assert guard in source
    lines = source.splitlines()
    guard_line = next(i for i, l in enumerate(lines) if l.strip() == guard)
    guard_indent = len(lines[guard_line]) - len(lines[guard_line].lstrip())
    job_line = next(i for i, l in enumerate(lines) if 'id="subscription_renewals"' in l)
    assert job_line > guard_line
    # Every line between the guard and the job registration stays INSIDE the
    # guard's block (deeper indentation, or blank).
    for line in lines[guard_line + 1: job_line + 1]:
        if line.strip():
            assert len(line) - len(line.lstrip()) > guard_indent


# ═══════════════════════════════════════════════════════════════════════════
# The disclosure + consent artefact (§6)
# ═══════════════════════════════════════════════════════════════════════════


def test_only_recurring_plans_get_a_disclosure(flag_on):
    db = FakeSupabase(sub("free", source="signup", hours_left=None))
    for plan_id, expected in (("pro", True), ("max", True), ("basic", False)):
        result = run(ps.create_checkout(db, USER, plan_id))
        assert result["requires_recurring_consent"] is expected, plan_id
        assert (result["recurring_disclosure_ar"] is not None) is expected, plan_id


def test_the_disclosure_states_that_renewal_is_on_and_how_to_stop(flag_on):
    """v2 (owner, 2026-08-12): the disclosure says renewal is on and where to
    stop it — nothing else.

    ⚠ The amount and cadence assertions this test used to carry were REMOVED
    deliberately, not because they stopped mattering. They are now disclosed by
    the /pay layout (order summary + billingNote) instead of by the hashed text,
    which no backend test can see. If that layout ever drops either number, this
    string becomes the only disclosure left and must take them back — so treat a
    /pay redesign as a change to this contract too.
    """
    text = pm.recurring_disclosure_ar(
        {"plan_id": "pro", "name_ar": "الاحترافية", "price_sar": "89.90",
         "duration_days": 30, "billing_cycle": "recurring_30d"}
    )
    assert "التجديد التلقائي" in text          # renewal is on
    assert "إيقاف التجديد" in text             # how to stop
    assert "إعدادات الحساب" in text            # where


def test_the_disclosure_version_tracks_the_text(flag_on):
    """A wording change that does not bump the version silently makes every
    older audit row claim the new words — which is the one thing the hash exists
    to prevent."""
    assert pm.DISCLOSURE_VERSION == "v2"


def test_a_one_time_billing_cycle_never_renews(flag_on):
    """Trap 6: `plans.billing_cycle` is finally READ, and it is a gate."""
    assert pm.plan_renews({"plan_id": "pro", "billing_cycle": "one_time"}) is False
    assert pm.plan_renews({"plan_id": "basic", "billing_cycle": "recurring_30d"}) is False
    assert pm.plan_renews({"plan_id": "pro", "billing_cycle": "recurring_30d"}) is True


def test_consent_hashes_the_servers_own_text(flag_on):
    """The client posts {"accepted": true} and NOTHING else, so it cannot claim
    it was shown different words."""
    db = FakeSupabase(sub("free", source="signup", hours_left=None))
    session = run(ps.create_checkout(db, USER, "pro"))
    result = run(ps.record_recurring_consent(db, USER, session["payment_id"], accepted=True))

    assert result["accepted"] is True and result["consent_given_at"]
    assert result["recurring_disclosure_ar"] == session["recurring_disclosure_ar"]

    stored = [r for r in db.tables["audit_logs"]
              if (r.get("metadata") or {}).get("event") == pm.CONSENT_EVENT]
    assert len(stored) == 1
    meta = stored[0]["metadata"]
    assert meta["consent_text_hash"] == pm.consent_text_hash(session["recurring_disclosure_ar"])
    assert meta["disclosure_version"] == pm.DISCLOSURE_VERSION
    assert stored[0]["resource_id"] == session["payment_id"]


def test_consent_is_idempotent_and_keeps_the_first_timestamp(flag_on):
    db = FakeSupabase(sub("free", source="signup", hours_left=None))
    pid = run(ps.create_checkout(db, USER, "pro"))["payment_id"]
    first = run(ps.record_recurring_consent(db, USER, pid, accepted=True))
    second = run(ps.record_recurring_consent(db, USER, pid, accepted=True))
    assert second["consent_given_at"] == first["consent_given_at"]
    assert len([r for r in db.tables["audit_logs"]
                if (r.get("metadata") or {}).get("event") == pm.CONSENT_EVENT]) == 1


def test_consent_refused_without_an_explicit_yes(flag_on):
    db = FakeSupabase(sub("free", source="signup", hours_left=None))
    pid = run(ps.create_checkout(db, USER, "pro"))["payment_id"]
    with pytest.raises(LunaHTTPException) as exc:
        run(ps.record_recurring_consent(db, USER, pid, accepted=False))
    assert exc.value.status_code == 400
    assert exc.value.detail == pm.CONSENT_REQUIRED_AR


def test_consent_refused_for_a_plan_that_does_not_renew(flag_on):
    db = FakeSupabase(sub("free", source="signup", hours_left=None))
    pid = run(ps.create_checkout(db, USER, "basic"))["payment_id"]
    with pytest.raises(LunaHTTPException) as exc:
        run(ps.record_recurring_consent(db, USER, pid, accepted=True))
    assert exc.value.status_code == 409
    assert exc.value.code is ErrorCode.PAYMENT_CONSENT_INVALID


def test_consent_refused_on_another_users_payment(flag_on):
    db = FakeSupabase(sub("free", source="signup", hours_left=None))
    pid = run(ps.create_checkout(db, USER, "pro"))["payment_id"]
    with pytest.raises(LunaHTTPException) as exc:
        run(ps.record_recurring_consent(db, OTHER_USER, pid, accepted=True))
    assert exc.value.status_code == 404
    assert exc.value.code is ErrorCode.PAYMENT_NOT_FOUND


def test_consent_refused_once_the_money_has_moved(flag_on):
    """Consent stamped after the charge is paperwork, not consent."""
    db = FakeSupabase(sub("free", source="signup", hours_left=None))
    pid = run(ps.create_checkout(db, USER, "pro"))["payment_id"]
    db.tables["payment_transactions"][0]["status"] = "paid"
    with pytest.raises(LunaHTTPException) as exc:
        run(ps.record_recurring_consent(db, USER, pid, accepted=True))
    assert exc.value.status_code == 409
    assert exc.value.detail == pm.CONSENT_NOT_OPEN_AR


# ═══════════════════════════════════════════════════════════════════════════
# Tokenization (§6) — the capture, from BOTH confirmation paths
# ═══════════════════════════════════════════════════════════════════════════


def _paid_payload(pid, *, token=TOKEN):
    return {
        "id": str(uuid.uuid4()), "status": "paid", "amount": 8990, "currency": "SAR",
        "live": False, "metadata": {"payment_id": pid},
        "source": {"type": "creditcard", "company": "mada",
                   "number": "4111-11XX-XXXX-1111", "token": token,
                   "month": "12", "year": "2030"},
    }


def test_token_extraction_reads_brand_last4_and_expiry():
    card = pm.extract_card_token(_paid_payload("x"))
    assert card == {"provider_token": TOKEN, "brand": "mada", "last4": "1111",
                    "exp_month": 12, "exp_year": 2030}


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"source": None}, {"source": {}}, {"source": {"type": "creditcard"}},
     {"source": {"token": "   "}}],
)
def test_token_extraction_is_silent_when_there_is_no_token(payload):
    """The ordinary case today (save_card is never requested) and the failure
    mode if the UNVERIFIED field names are wrong: nothing, never a crash."""
    assert pm.extract_card_token(payload) is None


def test_token_object_spellings_are_understood():
    """The TOKEN object names things differently from a payment source — `brand`
    not `company`, `last_four` not `number` — and carries the expiry the payment
    response omits entirely. Production feeds it through this same parser with
    `token` injected from `id`, which is what this asserts."""
    token_obj = {
        "id": TOKEN, "status": "active", "brand": "visa", "funding": "credit",
        "country": "SA", "month": "9", "year": "2029", "last_four": "4242",
    }
    card = pm.extract_card_token({"source": {**token_obj, "token": token_obj["id"]}})
    assert card == {"provider_token": TOKEN, "brand": "visa", "last4": "4242",
                    "exp_month": 9, "exp_year": 2029}


@pytest.fixture(autouse=True)
def provider_token_object(monkeypatch):
    """Stub ``GET /v1/tokens/{id}``. ``box[0]`` is what the provider answers;
    None models any failure (transport, 404, non-JSON).

    ⚠ AUTOUSE ON PURPOSE. Without it, any test that reaches
    ``capture_payment_method`` makes a REAL HTTPS call to Moyasar — observed
    once (a live 401 in the suite output) before this was made autouse. A unit
    suite must never touch a payment provider: it is slow, flaky, offline-hostile,
    and one bad refactor away from hitting a live endpoint with a real key.
    Tests wanting a specific token object just request this fixture and set
    ``box[0]``; everyone else silently gets the None default.
    """
    box: list = [None]
    monkeypatch.setattr(pm, "fetch_token_at_provider", lambda token: box[0])
    return box


def _consent_row(db, pid):
    db.tables["audit_logs"].append({
        "resource_type": "payment_transaction", "resource_id": pid,
        "created_at": _iso(_now()),
        "metadata": {"event": pm.CONSENT_EVENT, "consent_text_hash": FAKE_HASH,
                     "consented_at": _iso(_now())},
    })


def _payload_no_expiry(pid, *, token=TOKEN):
    """What ``save_card`` ACTUALLY returns: no month/year in the payment source
    (verified against docs.moyasar.com). The expiry exists only on the token."""
    payload = _paid_payload(pid, token=token)
    payload["source"].pop("month", None)
    payload["source"].pop("year", None)
    return payload


def test_token_object_supplies_the_expiry_the_payment_omits(flag_on, provider_token_object):
    provider_token_object[0] = {
        "id": TOKEN, "status": "active", "brand": "mada",
        "last_four": "1111", "month": "7", "year": "2028",
    }
    db = FakeSupabase(sub("pro", hours_left=720))
    pid = str(uuid.uuid4())
    _consent_row(db, pid)
    row = {"payment_id": pid, "user_id": USER, "plan_id": "pro"}

    assert run(pm.capture_payment_method(db, row, _payload_no_expiry(pid)))
    stored = db.tables["payment_methods"][0]
    assert (stored["exp_month"], stored["exp_year"]) == (7, 2028)


def test_a_token_the_provider_will_not_charge_is_never_stored(flag_on, provider_token_object):
    """Only `active` is chargeable. Storing an inactive token would put
    «مدى ••1111» in إعدادات الحساب — telling the user renewal is set up while
    every renewal silently declines."""
    provider_token_object[0] = {"id": TOKEN, "status": "inactive",
                                "brand": "mada", "last_four": "1111"}
    db = FakeSupabase(sub("pro", hours_left=720))
    pid = str(uuid.uuid4())
    _consent_row(db, pid)
    row = {"payment_id": pid, "user_id": USER, "plan_id": "pro"}

    assert run(pm.capture_payment_method(db, row, _paid_payload(pid))) is None
    assert db.tables["payment_methods"] == []


def test_an_unreachable_token_fetch_does_not_block_the_capture(flag_on, provider_token_object):
    """The fetch is an enrichment, never a gate: unknown status must PROCEED, or
    one Moyasar hiccup stops every card on the platform from being saved."""
    provider_token_object[0] = None
    db = FakeSupabase(sub("pro", hours_left=720))
    pid = str(uuid.uuid4())
    _consent_row(db, pid)
    row = {"payment_id": pid, "user_id": USER, "plan_id": "pro"}

    assert run(pm.capture_payment_method(db, row, _paid_payload(pid)))
    assert db.tables["payment_methods"][0]["provider_token"] == TOKEN


def test_a_purchase_with_no_explicit_consent_row_is_still_consented(flag_on):
    """v2 (owner, 2026-08-12): the consent CHECKBOX is gone. The disclosure is a
    plain reminder on /pay and the affirmative act is completing the purchase,
    so a paid renewing plan carries consent even with no audit row.

    ⚠ This test asserted the OPPOSITE until 2026-08-12, and the inversion is the
    point: under the old model a missing consent row meant "refuse to store".
    Keeping that behaviour after the checkbox was deleted would have disabled
    tokenization entirely — silently, since capture never raises.
    """
    db = FakeSupabase(sub("pro", hours_left=720))
    pid = str(uuid.uuid4())
    row = {"payment_id": pid, "user_id": USER, "plan_id": "pro",
           "paid_at": _iso(_now())}

    assert run(pm.capture_payment_method(db, row, _paid_payload(pid)))
    stored = db.tables["payment_methods"][0]
    assert stored["provider_token"] == TOKEN
    # The artefact is still real and still provable — hashed from the server's
    # own constant, not from anything the client sent.
    assert stored["consent_text_hash"] == pm.consent_text_hash(
        pm.RECURRING_DISCLOSURE_AR
    )
    assert stored["consent_given_at"]


def test_an_explicit_consent_row_still_wins(flag_on):
    """A user who ticked the v1 checkbox keeps v1's hash — evidence of the
    longer text THEY saw, not the shorter text we show today."""
    db = FakeSupabase(sub("pro", hours_left=720))
    pid = str(uuid.uuid4())
    db.tables["audit_logs"].append({
        "resource_type": "payment_transaction", "resource_id": pid,
        "created_at": _iso(_now()),
        "metadata": {"event": pm.CONSENT_EVENT, "consent_text_hash": FAKE_HASH,
                     "consented_at": _iso(_now())},
    })
    row = {"payment_id": pid, "user_id": USER, "plan_id": "pro"}

    assert run(pm.capture_payment_method(db, row, _paid_payload(pid)))
    assert db.tables["payment_methods"][0]["consent_text_hash"] == FAKE_HASH


def test_basic_never_tokenizes(flag_on):
    """Storing a card for a plan that cannot renew is a credential with no
    purpose — which PDPL does not love."""
    db = FakeSupabase(sub("basic", hours_left=48))
    row = {"payment_id": str(uuid.uuid4()), "user_id": USER, "plan_id": "basic"}
    db.tables["audit_logs"].append({
        "resource_type": "payment_transaction", "resource_id": row["payment_id"],
        "created_at": _iso(_now()),
        "metadata": {"event": pm.CONSENT_EVENT, "consent_text_hash": FAKE_HASH,
                     "consented_at": _iso(_now())},
    })
    assert run(pm.capture_payment_method(db, row, _paid_payload(row["payment_id"]))) is None
    assert db.tables["payment_methods"] == []


def test_capture_is_idempotent_across_both_confirmation_paths(flag_on):
    """3DS destroys the callback page, so /verify AND the webhook both run the
    capture. The second must find the first's row, not make another."""
    db = FakeSupabase(sub("pro", hours_left=720))
    pid = str(uuid.uuid4())
    row = {"payment_id": pid, "user_id": USER, "plan_id": "pro"}
    db.tables["audit_logs"].append({
        "resource_type": "payment_transaction", "resource_id": pid,
        "created_at": _iso(_now()),
        "metadata": {"event": pm.CONSENT_EVENT, "consent_text_hash": FAKE_HASH,
                     "consented_at": _iso(_now())},
    })

    first = run(pm.capture_payment_method(db, row, _paid_payload(pid)))
    second = run(pm.capture_payment_method(db, row, _paid_payload(pid)))

    assert first and first == second
    assert len(db.tables["payment_methods"]) == 1
    stored = db.tables["payment_methods"][0]
    assert stored["provider_token"] == TOKEN
    assert stored["consent_text_hash"] == FAKE_HASH


def test_a_second_card_replaces_and_revokes_the_first(flag_on, provider_revokes):
    """One active method per user (132's partial index) — and the displaced
    token dies at the provider, not just in our table."""
    db = FakeSupabase(sub("pro", hours_left=720))
    store_method(db, token="token_old")
    pid = str(uuid.uuid4())
    db.tables["audit_logs"].append({
        "resource_type": "payment_transaction", "resource_id": pid,
        "created_at": _iso(_now()),
        "metadata": {"event": pm.CONSENT_EVENT, "consent_text_hash": FAKE_HASH,
                     "consented_at": _iso(_now())},
    })

    run(pm.capture_payment_method(
        db, {"payment_id": pid, "user_id": USER, "plan_id": "pro"},
        _paid_payload(pid, token="token_new"),
    ))

    active = [m for m in db.tables["payment_methods"] if not m.get("revoked_at")]
    assert len(active) == 1 and active[0]["provider_token"] == "token_new"
    assert provider_revokes == ["token_old"]


def test_the_public_shape_never_carries_the_token():
    row = {"payment_method_id": "m", "provider_token": TOKEN, "brand": "mada",
           "last4": "1111", "consent_text_hash": FAKE_HASH, "user_id": USER}
    described = pm.describe_method(row)
    assert "provider_token" not in described
    assert TOKEN not in str(described)
    # The credential is not even SELECTED on the read path.
    assert "provider_token" not in pm._METHOD_PUBLIC_COLUMNS


def test_revoke_marks_the_row_and_kills_the_token(flag_on, provider_revokes):
    db = FakeSupabase(sub("pro", hours_left=720))
    store_method(db)
    result = run(pm.revoke_active_method(db, USER))
    assert result["revoked"] is True and result["provider_confirmed"] is True
    # The emptied card shape rides along so the caller can cache it directly.
    assert result["has_method"] is False and result["last4"] is None
    assert db.tables["payment_methods"][0]["revoked_at"]
    assert provider_revokes == [TOKEN]
    # …and it is idempotent.
    assert run(pm.revoke_active_method(db, USER))["revoked"] is False


def test_account_purge_revokes_the_token_at_the_provider(flag_on, provider_revokes):
    """Plan §10: a live token on a deleted account is the worst version of this
    bug — the row is about to be cascaded away, taking the token with it."""
    db = FakeSupabase(sub("pro", hours_left=720))
    store_method(db)
    assert pm.revoke_all_for_user_sync(db, USER, reason="account_purge") == 1
    assert provider_revokes == [TOKEN]
    assert db.tables["payment_methods"][0]["revoked_at"]


def test_purge_revoke_never_raises_when_the_table_is_missing(flag_on):
    """A purge that failed over a card token would miss a PDPL deadline."""
    db = FakeSupabase(sub("pro", hours_left=720))
    db.missing_columns = {"provider_token"}
    assert pm.revoke_all_for_user_sync(db, USER, reason="account_purge") == 0


# ═══════════════════════════════════════════════════════════════════════════
# THE JOB (§7)
# ═══════════════════════════════════════════════════════════════════════════


def test_a_due_term_is_charged_from_the_catalog_price(flag_on, charges, no_smtp):
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)

    stats = sweep(db)

    assert stats["renewed"] == 1
    assert len(charges) == 1
    assert charges[0]["token"] == TOKEN
    assert charges[0]["amount"] == 8990           # plans.price_sar, nothing else
    row = renewal_rows(db)[0]
    assert row["status"] == "paid" and row["fulfilled_at"]
    assert row["amount_sar"] == "89.90"
    assert row["renewal_attempt"] == 0
    assert row["upgrade_credit_sar"] == "0.00"    # a renewal is never prorated


def test_the_amount_is_never_the_last_payments_amount(flag_on, charges, no_smtp):
    """A prorated upgrade paid 100.00 for `max`; the RENEWAL is 189.90."""
    db = FakeSupabase(sub("max", hours_left=6))
    store_method(db)
    db.tables["payment_transactions"].append({
        "payment_id": str(uuid.uuid4()), "user_id": USER, "plan_id": "max",
        "amount_sar": "100.00", "status": "paid", "initiated_by": "user",
        "upgrade_credit_sar": "89.90", "created_at": _iso(_now() - timedelta(days=30)),
    })

    sweep(db)
    assert charges[0]["amount"] == 18990


def test_term_extends_from_expires_at_not_now(flag_on, charges, no_smtp):
    """Plan trap 3. The job runs at 03:30; a term that renewed from now() would
    shave hours off EVERY cycle, and nobody would notice for months."""
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)
    old_expiry = _parse(db.tables["user_subscriptions"][0]["expires_at"])

    sweep(db)

    new_expiry = _parse(db.tables["user_subscriptions"][0]["expires_at"])
    assert new_expiry == old_expiry + timedelta(days=30)
    # …and emphatically not now() + 30d, which would be ~6h earlier.
    assert new_expiry > _now() + timedelta(days=30, hours=5)


def test_two_sweeps_charge_exactly_once(flag_on, charges, no_smtp):
    """THE test. Two ticks, a retry, or a redeploy mid-run must not double-charge
    — and the guard that stops it is the DB unique index, not Python."""
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)

    first = sweep(db)
    second = sweep(db)

    assert first["renewed"] == 1
    assert second.get("renewed") is None
    assert len(charges) == 1
    assert len(renewal_rows(db)) == 1


def test_a_racing_insert_loses_at_the_database(flag_on, charges, no_smtp):
    """The pre-filter is a convenience; the constraint is the guard. Strip the
    pre-filter and the DB must still refuse the second row."""
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)
    subscription = dict(db.tables["user_subscriptions"][0])

    import backend.app.services.renewal_service as module

    original = module._existing_renewal_rows
    module._existing_renewal_rows = lambda *_a, **_k: []
    try:
        assert run(module._renew_one(db, subscription)) == "renewed"
        # The subscription row the second caller holds is the STALE one — the
        # exact shape a concurrent tick would have read.
        assert run(module._renew_one(db, subscription)) == "skipped_already_renewed"
    finally:
        module._existing_renewal_rows = original

    assert len(charges) == 1


def test_a_cancelled_subscriber_is_never_charged(flag_on, charges, no_smtp):
    """The promise «لن يُجدَّد اشتراكك» that 120's UI has been making since
    before an engine existed. This filter is what finally makes it true."""
    db = FakeSupabase(sub("pro", hours_left=6, cancelled_at=_iso(_now())))
    store_method(db)
    assert sweep(db) == {"scanned": 0}
    assert charges == [] and renewal_rows(db) == []


@pytest.mark.parametrize("source", ["code", "manual", "signup", "marketing"])
def test_a_non_payment_grant_is_never_charged(flag_on, charges, no_smtp, source):
    """A code / marketing / manual / signup grant has no card behind it. Even
    with a stored token on the account, it must not be touched."""
    db = FakeSupabase(sub("pro", source=source, hours_left=6))
    store_method(db)
    assert sweep(db)["scanned"] == 0
    assert charges == []


def test_basic_is_never_renewed(flag_on, charges, no_smtp):
    db = FakeSupabase(sub("basic", hours_left=6))
    store_method(db)
    assert sweep(db)["scanned"] == 0
    assert charges == []


def test_a_one_time_billing_cycle_stops_the_job(flag_on, charges, no_smtp):
    """Second wall: even a pro row is refused if the catalog says one_time."""
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)
    for plan in db.tables["plans"]:
        if plan["plan_id"] == "pro":
            plan["billing_cycle"] = "one_time"
    assert sweep(db)["skipped_plan_not_recurring"] == 1
    assert charges == [] and renewal_rows(db) == []


def test_a_term_outside_the_horizon_waits(flag_on, charges, no_smtp):
    db = FakeSupabase(sub("pro", hours_left=48))
    store_method(db)
    assert sweep(db)["scanned"] == 0
    assert charges == []


def test_no_stored_card_means_no_charge_and_no_row(flag_on, charges, no_smtp):
    db = FakeSupabase(sub("pro", hours_left=6))
    assert sweep(db)["skipped_no_method"] == 1
    assert charges == [] and renewal_rows(db) == []


def test_a_card_without_consent_is_not_chargeable(flag_on, charges, no_smtp):
    """A token with no consent artefact is a credential we hold with no right to
    use it (plan §5.1)."""
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db, consent=False)
    assert sweep(db)["skipped_no_method"] == 1
    assert charges == []


def test_a_revoked_card_is_not_chargeable(flag_on, charges, no_smtp):
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db, revoked=True)
    assert sweep(db)["skipped_no_method"] == 1
    assert charges == []


def test_the_ledger_row_exists_before_the_provider_is_called(flag_on, no_smtp, monkeypatch):
    """Crash-safe ordering — the same rule as "the user's message is saved
    before the AI call". If the process dies at the charge, the row is there."""
    seen: dict[str, Any] = {}

    async def _charge(*, token, amount_halalas, description, payment_id, metadata=None):
        seen["rows"] = [dict(r) for r in renewal_rows(db)]
        raise ps.MoyasarUnavailable("boom")

    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)
    monkeypatch.setattr(ps, "charge_saved_card", _charge)

    sweep(db)
    assert len(seen["rows"]) == 1
    assert seen["rows"][0]["status"] == "initiated"


def test_the_renewal_never_touches_plan_id(flag_on, charges, no_smtp):
    """⚠ `trg_user_subscriptions_assignment` is BEFORE UPDATE **OF plan_id** and
    re-derives expires_at. A bookkeeping write that named plan_id would re-stamp
    the very term this job just paid to extend."""
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)
    sweep(db)

    bookkeeping = [
        w for w in db.writes
        if w[0] == "user_subscriptions" and w[1] == "update"
        and "renewal_attempt_at" in w[2]
    ]
    assert bookkeeping
    for _table, _op, payload in bookkeeping:
        assert "plan_id" not in payload
        assert "expires_at" not in payload      # grant_plan owns the term


def test_a_successful_renewal_resets_the_ladder(flag_on, charges, no_smtp):
    db = FakeSupabase(sub("pro", hours_left=6, failed_count=1,
                          attempt_at=_iso(_now() - timedelta(days=2))))
    store_method(db)
    sweep(db)
    row = db.tables["user_subscriptions"][0]
    assert row["renewal_failed_count"] == 0 and row["renewal_attempt_at"]


# ═══════════════════════════════════════════════════════════════════════════
# Ambiguity is fail-closed (rule 3)
# ═══════════════════════════════════════════════════════════════════════════


def test_a_transport_failure_blocks_the_period_instead_of_retrying(flag_on, charges, no_smtp):
    """We never saw an answer, so the money may well have moved. Retrying blind
    is the double charge; blocking is a support ticket."""
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)
    charges.script["exc"] = ps.MoyasarUnavailable("timeout")   # type: ignore[attr-defined]

    assert sweep(db)["ambiguous"] == 1
    row = renewal_rows(db)[0]
    assert row["status"] == "initiated"                 # NOT failed
    # The ladder did not advance, so nothing schedules a retry…
    assert db.tables["user_subscriptions"][0]["renewal_failed_count"] == 0
    # …and the next tick refuses to charge again.
    charges.script["exc"] = None                        # type: ignore[attr-defined]
    assert sweep(db)["ambiguous"] == 1
    assert len(charges) == 1
    assert len(renewal_rows(db)) == 1


def test_an_unresolved_row_blocks_at_the_database_too(flag_on, charges, no_smtp):
    """Belt AND braces. Strip the Python pre-filter and 132's index still
    refuses: its predicate is `status <> 'failed'`, so an `initiated` row keeps
    the period closed at ANY attempt number."""
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)
    charges.script["exc"] = ps.MoyasarUnavailable("timeout")

    assert sweep(db)["ambiguous"] == 1
    charges.script["exc"] = None

    import backend.app.services.renewal_service as module

    original = module._existing_renewal_rows
    module._existing_renewal_rows = lambda *_a, **_k: []
    try:
        # Also pretend the ladder advanced, so this is attempt 1, not 0.
        stale = dict(db.tables["user_subscriptions"][0])
        stale["renewal_failed_count"] = 1
        assert run(module._renew_one(db, stale)) == "skipped_already_renewed"
    finally:
        module._existing_renewal_rows = original

    # Only the first (unresolved) attempt ever reached the provider.
    assert len(charges) == 1
    assert len(renewal_rows(db)) == 1


def test_an_upgrade_mid_charge_holds_the_grant_instead_of_downgrading(
    flag_on, charges, no_smtp, monkeypatch
):
    """The narrow race with the ugliest outcome: the user upgrades pro→max while
    their pro renewal is in flight. grant_plan writes plan_id unconditionally, so
    granting would silently downgrade somebody who just paid 189.90 — and neither
    they nor support would ever work out why."""
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)

    original = ps.charge_saved_card

    async def _charge_then_upgrade(**kwargs):
        result = await original(**kwargs)
        # …the upgrade lands between the charge and the grant.
        db.tables["user_subscriptions"][0]["plan_id"] = "max"
        return result

    monkeypatch.setattr(ps, "charge_saved_card", _charge_then_upgrade)

    assert sweep(db)["held"] == 1
    assert db.tables["user_subscriptions"][0]["plan_id"] == "max"   # untouched
    row = renewal_rows(db)[0]
    # Money IS recorded — a charge with an `initiated` row and no receipt is the
    # worse failure — and the customer can refund it for 24h.
    assert row["status"] == "paid" and row["paid_at"]
    assert not row.get("fulfilled_at")
    assert "grant_plan" not in db.calls
    assert ps.transaction_summary(row)["refundable"] is True


def test_a_provider_refusal_is_an_ordinary_decline(flag_on, charges, no_smtp):
    """A 4xx is Moyasar saying "no" BEFORE taking money — provable no-charge, so
    the ladder may advance."""
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)
    charges.script["exc"] = ps.MoyasarError("bad token", status=400)  # type: ignore[attr-defined]

    assert sweep(db)["declined"] == 1
    assert renewal_rows(db)[0]["status"] == "failed"
    assert db.tables["user_subscriptions"][0]["renewal_failed_count"] == 1


def test_a_decline_records_the_providers_reason(flag_on, charges, no_smtp):
    """`decline_reason` (migration 133) is what makes "expired card vs empty
    balance" a GROUP BY instead of a JSON dig. The full response still lands in
    raw_payload; this is the aggregatable copy."""
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)
    charges.script["exc"] = ps.MoyasarError("card expired", status=402)  # type: ignore[attr-defined]

    assert sweep(db)["declined"] == 1
    failed = renewal_rows(db)[0]
    assert failed["status"] == "failed"
    assert "card expired" in (failed.get("decline_reason") or "")
    assert failed.get("raw_payload")          # the untruncated original survives too


def test_a_decline_reason_is_truncated_not_stored_whole(flag_on, charges, no_smtp):
    """A provider is free to return an essay; this column exists to be grouped,
    not read as a document."""
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)
    charges.script["exc"] = ps.MoyasarError("x" * 4000, status=402)  # type: ignore[attr-defined]

    assert sweep(db)["declined"] == 1
    assert len(renewal_rows(db)[0]["decline_reason"]) <= 500


@pytest.mark.parametrize(
    "exc,provable",
    [(ps.MoyasarError("x", status=400), True),
     (ps.MoyasarError("x", status=402), True),
     (ps.MoyasarNotFound("x", status=404), True),
     (ps.MoyasarError("x", status=408), False),
     (ps.MoyasarError("x", status=429), False),
     (ps.MoyasarError("x", status=500), False),
     (ps.MoyasarError("no json body"), False),
     (ps.MoyasarUnavailable("timeout"), False)],
)
def test_only_a_provable_no_charge_advances_the_ladder(exc, provable):
    assert rs._charge_definitely_did_not_happen(exc) is provable


def test_an_unconfigured_provider_burns_no_rung(flag_on, monkeypatch, no_smtp):
    """Our misconfiguration must not cost the customer a retry, or leave an
    unresolvable row behind."""
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)
    settings = get_settings()
    monkeypatch.setattr(settings, "MOYASAR_SECRET_KEY", None)

    assert sweep(db)["skipped_unconfigured"] == 1
    assert renewal_rows(db) == []
    assert db.tables["user_subscriptions"][0]["renewal_failed_count"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Dunning (§8) — day 0, +1, +3, then lapse
# ═══════════════════════════════════════════════════════════════════════════


def test_the_ladder_is_zero_one_three():
    assert rs.LADDER_DAYS == (0, 1, 3) and rs.MAX_ATTEMPTS == 3


def test_a_retry_waits_for_its_rung(flag_on, charges, no_smtp):
    db = FakeSupabase(sub("pro", hours_left=-2, failed_count=1,
                          attempt_at=_iso(_now() - timedelta(hours=3))))
    store_method(db)
    assert sweep(db)["scanned"] == 0            # +1 day has not passed
    assert charges == []


def test_each_attempt_is_its_own_row_with_an_incremented_number(flag_on, charges, no_smtp):
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)
    charges.script["status"] = "failed"          # type: ignore[attr-defined]

    # day 0
    assert sweep(db)["declined"] == 1
    # +1 day
    db.tables["user_subscriptions"][0]["renewal_attempt_at"] = _iso(
        _now() - timedelta(days=1, minutes=5)
    )
    assert sweep(db)["declined"] == 1
    # +3 days (two more after the previous rung)
    db.tables["user_subscriptions"][0]["renewal_attempt_at"] = _iso(
        _now() - timedelta(days=2, minutes=5)
    )
    assert sweep(db)["lapsed"] == 1

    rows = sorted(renewal_rows(db), key=lambda r: r["renewal_attempt"])
    assert [r["renewal_attempt"] for r in rows] == [0, 1, 2]
    assert all(r["status"] == "failed" for r in rows)
    # Every attempt keyed the SAME period — that is what makes the unique index
    # able to tell a retry from a duplicate.
    assert len({r["period_start"] for r in rows}) == 1
    assert len(charges) == 3


def test_the_ladder_stops_and_lets_the_term_lapse(flag_on, charges, no_smtp):
    """No new mechanism: the existing expired→free fallback takes it from here."""
    db = FakeSupabase(sub("pro", hours_left=-1, failed_count=3,
                          attempt_at=_iso(_now() - timedelta(days=5))))
    store_method(db)
    assert sweep(db)["scanned"] == 0
    assert charges == []


def test_an_ancient_lapsed_term_is_not_resurrected(flag_on, charges, no_smtp):
    """A silent charge a fortnight after the plan ended is a chargeback."""
    db = FakeSupabase(sub("pro", hours_left=-24 * 20, failed_count=1,
                          attempt_at=_iso(_now() - timedelta(days=19))))
    store_method(db)
    assert sweep(db)["scanned"] == 0
    assert charges == []


def test_a_decline_emails_the_customer(flag_on, charges, monkeypatch):
    """§8: a declined card must not silently end a subscription the user
    believes is running."""
    notices: list[dict] = []

    async def _notice(_db, *, payment_row, plan_name_ar, expires_at=None, final=False):
        notices.append({"plan": plan_name_ar, "final": final})

    monkeypatch.setattr(rs, "send_renewal_failed_notice", _notice)

    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)
    charges.script["status"] = "failed"          # type: ignore[attr-defined]

    sweep(db)                                     # attempt 0 → email
    db.tables["user_subscriptions"][0]["renewal_attempt_at"] = _iso(
        _now() - timedelta(days=1, minutes=5)
    )
    sweep(db)                                     # attempt 1 → silent
    db.tables["user_subscriptions"][0]["renewal_attempt_at"] = _iso(
        _now() - timedelta(days=2, minutes=5)
    )
    sweep(db)                                     # attempt 2 → final email

    assert [n["final"] for n in notices] == [False, True]
    assert notices[0]["plan"] == "الاحترافية"


def test_the_dunning_email_never_raises_with_the_transport_down(flag_on, no_smtp):
    """Receipts are parked on the 465/SSL issue; the call is wired anyway so it
    starts working the day the transport does."""
    db = FakeSupabase(sub("pro", hours_left=6))
    run(receipt_service.send_renewal_failed_notice(
        db,
        payment_row={"payment_id": str(uuid.uuid4()), "user_id": USER,
                     "amount_sar": "89.90"},
        plan_name_ar="الاحترافية",
        expires_at=_iso(_now() + timedelta(days=1)),
        final=False,
    ))
    assert no_smtp == []          # nothing sent, nothing raised


def test_the_dunning_templates_render_both_shapes():
    for final in (False, True):
        subject, html = receipt_service.render_renewal_failed_notice(
            customer_name=CUSTOMER_NAME, plan_name_ar="الاحترافية",
            amount_sar="89.90", expires_at=_now(), final=final,
        )
        assert "تعذّر تجديد اشتراكك" in subject or "انتهى اشتراكك" in subject
        assert "الاحترافية" in html and "ريحان" in html


# ═══════════════════════════════════════════════════════════════════════════
# §7's ⚠ — a renewal row must survive the open-checkout sweep
# ═══════════════════════════════════════════════════════════════════════════


def test_a_renewal_row_survives_a_new_checkout(flag_on):
    """The bug the user causes and can never explain: open /pay during your own
    renewal window and _expire_open_checkouts supersedes the renewal row, the
    charge lands on an `expired` row, the period key is spent, and the
    subscription lapses despite a working card."""
    db = FakeSupabase(sub("pro", hours_left=6))
    renewal = {
        "payment_id": str(uuid.uuid4()), "user_id": USER, "plan_id": "pro",
        "amount_sar": "89.90", "status": "initiated", "provider": "moyasar",
        "initiated_by": "renewal", "renewal_attempt": 0,
        "period_start": db.tables["user_subscriptions"][0]["expires_at"],
        "created_at": _iso(_now()),
    }
    stale_user_row = {
        "payment_id": str(uuid.uuid4()), "user_id": USER, "plan_id": "pro",
        "amount_sar": "89.90", "status": "initiated", "provider": "moyasar",
        "initiated_by": "user", "created_at": _iso(_now()),
    }
    db.tables["payment_transactions"] += [renewal, stale_user_row]

    run(ps.create_checkout(db, USER, "pro"))

    by_id = {r["payment_id"]: r for r in db.tables["payment_transactions"]}
    assert by_id[renewal["payment_id"]]["status"] == "initiated"       # untouched
    assert by_id[stale_user_row["payment_id"]]["status"] == ps.STATUS_EXPIRED


def test_the_supersede_falls_back_when_132_is_unapplied(flag_off):
    """Zero behaviour change on a flag-off deploy that lands ahead of the
    migration: no initiated_by column means no renewal rows exist, so the
    pre-132 unfiltered sweep is exactly right."""
    db = FakeSupabase(sub("pro", hours_left=6))
    db.missing_columns = {"initiated_by", "renewal_attempt", "period_start",
                          "payment_method_id"}
    stale = {
        "payment_id": str(uuid.uuid4()), "user_id": USER, "plan_id": "pro",
        "amount_sar": "89.90", "status": "initiated", "provider": "moyasar",
        "created_at": _iso(_now()),
    }
    db.tables["payment_transactions"].append(stale)

    result = run(ps.create_checkout(db, USER, "pro"))

    assert result["payment_id"]
    by_id = {r["payment_id"]: r for r in db.tables["payment_transactions"]}
    assert by_id[stale["payment_id"]]["status"] == ps.STATUS_EXPIRED


# ═══════════════════════════════════════════════════════════════════════════
# Sweep hygiene
# ═══════════════════════════════════════════════════════════════════════════


def test_one_bad_subscription_never_stops_the_sweep(flag_on, charges, no_smtp, monkeypatch):
    db = FakeSupabase(sub("pro", hours_left=6))
    store_method(db)
    db.tables["user_subscriptions"].append(
        sub("pro", hours_left=5, user_id=OTHER_USER)
    )
    store_method(db, user_id=OTHER_USER, token="token_other")

    calls = {"n": 0}
    original = rs._renew_one

    async def _flaky(supabase, subscription):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("postgrest exploded")
        return await original(supabase, subscription)

    monkeypatch.setattr(rs, "_renew_one", _flaky)

    stats = sweep(db)
    assert stats["scanned"] == 2
    assert stats["error"] == 1 and stats["renewed"] == 1


def test_a_broken_selection_query_returns_cleanly(flag_on, charges, no_smtp):
    """42703 when the backend runs ahead of 132: log and return, never crash the
    scheduler tick."""
    db = FakeSupabase(sub("pro", hours_left=6))
    db.missing_columns = {"renewal_failed_count"}
    assert sweep(db) == {"scanned": 0, "error": 1}
    assert charges == []
