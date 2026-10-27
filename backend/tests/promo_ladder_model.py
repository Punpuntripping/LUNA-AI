"""A MODEL of the seat + price SQL (migrations 138, 182, 183, 192) shared by the
in-memory Supabase stand-ins in ``test_payments.py`` and
``test_subscription_renewal.py``.

Not a test module (no ``test_`` prefix), so pytest never collects it.
⚠ ``.gitignore`` blankets ``backend/tests/*``: this file needs ``git add -f``,
or all three payment suites ImportError on a clean checkout.

WHY ONE COPY. Both fakes used to carry their own transcription of
``effective_plan_price`` / ``claim_early_adopter_seat`` / the forfeiture
predicate, and pricing_campaigns_v2.md §2.3 lists them as copies that must move
together with the SQL. Two transcriptions drift; one does not.

⚠ IT IS A MODEL, NOT THE SQL. A wrong model produces a green test. It is
transcribed from ``192_promo_price_tiers.sql`` (its interpretation notes
I1–I9 are cited inline) plus the coordinator's 2026-10-27 decisions; anything
that turns on capacity, the advisory lock or the quote order must be re-verified
against 192 itself before it is believed.

TWO REGIMES, switched by ``ladder_v2`` (``early_adopter_campaign.ladder_v2``):

* **off** — today's 138/182/183 behaviour, with ONE correction: the live-seat
  branch of ``effective_plan_price`` never depended on
  ``early_adopter_campaign.enabled`` in the real body; the old fakes returned
  the list price for a seat holder whenever the switch was down.
* **on** — pricing_campaigns_v2.md §1: price tiers sold through seat batches.

Turning the flag ON stamps ``ladder_v2_at`` (192 R2's trigger, on every
false → true flip); with the flag on, a payment WITHOUT ``promo_batch_id``
created at/after that instant that passes the legacy price gate gets no seat.

Deliberately NOT modelled: ``plans.billing_cycle``. 192 gates the seat branches
on ``'recurring_30d'``; the payments suite's catalog predates 132 and says
``one_time`` for pro/max, so the model treats pro/max as the recurring plans.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.app.services import payment_service as ps
from backend.app.services import subscription_service as ss

#: Returned by ``promo_rpc`` for an RPC this model does not own.
NOT_MINE: Any = object()

#: pricing_campaigns_v2.md §1.2 — the 192 seed. Batches are DATA: a test edits
#: these rows exactly the way an operator's UPDATE would.
SEED_BATCHES: tuple[dict, ...] = (
    {"batch_id": "pro_t1_a", "plan_id": "pro", "price_tier": 1, "batch_order": 1,
     "price_sar": "19.90", "months": 2, "seat_limit": 500, "profession_only": True,
     "enabled": True},
    {"batch_id": "pro_t1_b", "plan_id": "pro", "price_tier": 1, "batch_order": 2,
     "price_sar": "19.90", "months": 1, "seat_limit": 500, "profession_only": True,
     "enabled": True},
    {"batch_id": "pro_t2_a", "plan_id": "pro", "price_tier": 2, "batch_order": 1,
     "price_sar": "59.90", "months": 2, "seat_limit": 500, "profession_only": False,
     "enabled": True},
    {"batch_id": "max_t1_a", "plan_id": "max", "price_tier": 1, "batch_order": 1,
     "price_sar": "149.90", "months": 4, "seat_limit": 100, "profession_only": False,
     "enabled": True},
    {"batch_id": "max_t2_a", "plan_id": "max", "price_tier": 2, "batch_order": 1,
     "price_sar": "189.90", "months": 3, "seat_limit": 400, "profession_only": False,
     "enabled": True},
    {"batch_id": "max_t2_b", "plan_id": "max", "price_tier": 2, "batch_order": 2,
     "price_sar": "189.90", "months": 1, "seat_limit": 1000, "profession_only": False,
     "enabled": True},
)

#: §1.3-3 / A6 — the first profession answers that unlock Pro Tier 1.
OFFER_PROFESSIONS = frozenset({"legal", "specialist", "entrepreneur"})

SEAT_PLAN_IDS = ("pro", "max")

#: 192 (R2): an UNSTAMPED payment created after the flip that is nonetheless
#: priced at or below the legacy promo is a checkout bug — 'plan_not_eligible',
#: deliberately the backend wrapper's ERROR branch. A list-priced one never
#: gets that far: the legacy gate answers 'not_promo_priced' first, as today.
POST_FLIP_NO_BATCH_ACTION = "plan_not_eligible"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _seat_row(seat, action) -> list[dict]:
    return [{"seat_id": seat["seat_id"], "promo_ends_at": seat.get("promo_ends_at"),
             "over_capacity": bool(seat.get("over_capacity")), "action": action}]


def _no_seat(action) -> list[dict]:
    return [{"seat_id": None, "promo_ends_at": None, "over_capacity": False, "action": action}]


class PromoLadderModel:
    """Mixin for a FakeSupabase that has ``tables``, ``early_adopter_seats``,
    ``campaign_enabled``, ``campaign_seat_limit``, ``campaign_promo_days`` and
    ``campaign_row_present``. Call ``_init_promo_model()`` from ``__init__`` and
    ``_sync_promo_table(name)`` from ``table()``."""

    # ── setup ────────────────────────────────────────────────────────────
    def _init_promo_model(self) -> None:
        # 192 ships `ladder_v2 = false`: deploy-day production, nothing changes.
        self._ladder_v2 = False
        self.ladder_v2_at: Optional[datetime] = None
        self.promo_batches: list[dict] = [dict(b) for b in SEED_BATCHES]
        # 183's switch and end date — Pro Tier 1 keeps both under v2 (§1.4-9).
        # Only defaulted: the payments fake sets its own first.
        if not hasattr(self, "intro_offer_enabled"):
            self.intro_offer_enabled = False
        if not hasattr(self, "intro_offer_ends_at"):
            self.intro_offer_ends_at = _now() + timedelta(days=60)
        if not hasattr(self, "intro_offer_prices"):
            self.intro_offer_prices = {"legal": "1.99", "specialist": "9.90",
                                       "entrepreneur": "9.90"}

    @property
    def ladder_v2(self) -> bool:
        return self._ladder_v2

    @ladder_v2.setter
    def ladder_v2(self, value: bool) -> None:
        """The 192 trigger: flipping the flag ON stamps ``ladder_v2_at``. A test
        that needs a specific flip instant sets ``ladder_v2_at`` afterwards."""
        value = bool(value)
        if value and not self._ladder_v2:
            self.ladder_v2_at = _now()
        self._ladder_v2 = value

    def _sync_promo_table(self, name: str) -> None:
        """Expose the model's state as the tables the backend reads directly
        (the ladder flag, the batch-fill alert's seat count)."""
        if name == "early_adopter_campaign":
            self.tables[name] = (
                [{"id": True, "enabled": self.campaign_enabled,
                  "seat_limit": self.campaign_seat_limit,
                  "promo_days": self.campaign_promo_days,
                  "ladder_v2": self.ladder_v2,
                  "ladder_v2_at": _iso(self.ladder_v2_at) if self.ladder_v2_at else None}]
                if self.campaign_row_present else []
            )
        elif name == "early_adopter_seats":
            self.tables[name] = self.early_adopter_seats      # the SAME list
        elif name == "promo_batches":
            self.tables[name] = self.promo_batches

    # ── shared reads ─────────────────────────────────────────────────────
    def _plan(self, plan_id):
        return next((p for p in self.tables["plans"] if p["plan_id"] == plan_id), None)

    def _payment(self, payment_id):
        return next((r for r in self.tables["payment_transactions"]
                     if r.get("payment_id") == payment_id), None)

    def _live_seat(self, user_id):
        """early_adopter_seats_one_live: at most one live seat per user."""
        return next(
            (s for s in self.early_adopter_seats
             if s.get("user_id") == user_id and not s.get("released_at")),
            None,
        )

    def _in_window(self, seat, at: Optional[datetime] = None) -> bool:
        return ps._parse_ts(seat["promo_ends_at"]) > (at or _now())

    def _cancelled_seat(self, user_id) -> bool:
        return any(
            s.get("user_id") == user_id and s.get("release_reason") == "cancelled"
            for s in self.early_adopter_seats
        )

    def _intro_member(self, user_id) -> bool:
        """``is_intro_offer_member`` (183): ever PAID an intro first month."""
        return any(
            r.get("user_id") == user_id and r.get("intro_offer_group")
            and r.get("status") == "paid"
            for r in self.tables["payment_transactions"]
        )

    def _forfeited(self, user_id) -> bool:
        """The flag-off forfeiture predicate (138 §1.6 + 183's exemption): a
        standing 'cancelled' release, unless the user ever paid an intro first
        month. 'upgraded' and 'refund' never match. Not used with ``ladder_v2``
        (§1.4-6 replaces it with "tier used")."""
        return self._cancelled_seat(user_id) and not self._intro_member(user_id)

    def _legacy_open(self) -> bool:
        """138's ``early_adopter_open()``: enabled AND under capacity, where
        capacity is ``count(*) WHERE released_at IS NULL``."""
        if not self.campaign_enabled:
            return False
        live = [s for s in self.early_adopter_seats if not s.get("released_at")]
        return len(live) < self.campaign_seat_limit

    def _campaign_open(self) -> bool:
        """``early_adopter_open()`` — with ``ladder_v2``: enabled AND some
        enabled batch has free seats (any plan, any tier)."""
        if self.ladder_v2:
            return bool(self.campaign_enabled) and any(
                b.get("enabled") and self._batch_live(b["batch_id"]) < int(b["seat_limit"])
                for b in self.promo_batches
            )
        return self._legacy_open()

    # ── 192 helpers ──────────────────────────────────────────────────────
    def _legacy_seat_payment(self, seat) -> dict:
        return self._payment(seat.get("payment_id")) or {}

    def _legacy_seat_plan(self, seat) -> Optional[str]:
        """A legacy seat (NULL batch_id) has no plan of its own: it is its
        claiming payment's plan, or None when that payment is gone."""
        plan_id = self._legacy_seat_payment(seat).get("plan_id")
        return str(plan_id) if plan_id else None

    def _seat_tiers_used(self, seat) -> set[tuple[str, int]]:
        """``promo_tier_used`` per seat: which (plan, tier) pairs it uses up.

        * a batch seat: its own (plan_id, price_tier);
        * a legacy seat, through its claiming payment (Q3): legacy Pro ⇒ Pro
          Tier 2, legacy Max ⇒ Max Tier 1 — and (192 R1) a payment carrying
          ``intro_offer_group`` (the old 1.99/9.90 offer) ⇒ Pro Tier 1 AND 2;
        * a legacy seat with no payment ⇒ nothing.
        """
        if seat.get("batch_id"):
            return {(str(seat.get("plan_id")), int(seat.get("price_tier") or 0))}
        pay = self._legacy_seat_payment(seat)
        used: set[tuple[str, int]] = set()
        if pay.get("plan_id") == "pro":
            used.add(("pro", 2))
        if pay.get("plan_id") == "max":
            used.add(("max", 1))
        if pay.get("intro_offer_group"):
            used |= {("pro", 1), ("pro", 2)}
        return used

    def _refunded_purchase(self, seat) -> bool:
        """The seat was released 'refund' because the PURCHASE that claimed it
        was refunded (its ``payment_id`` row is 'refunded'). A 'refund' release
        caused by a later renewal's refund leaves the claiming payment 'paid'."""
        return (seat.get("release_reason") == "refund"
                and (self._payment(seat.get("payment_id")) or {}).get("status") == "refunded")

    def _tier_used(self, user_id, plan_id, tier) -> bool:
        """§1.3-7: ever held a seat at that tier for that plan, live or
        released — 'upgraded', 'cancelled' and 'refund' alike, EXCEPT (owner,
        2026-10-27) a seat whose own purchase was refunded: a refunded purchase
        never used the tier. A refunded renewal still did."""
        return any(
            (plan_id, tier) in self._seat_tiers_used(s)
            for s in self.early_adopter_seats
            if s.get("user_id") == user_id and not self._refunded_purchase(s)
        )

    def _batch(self, batch_id):
        return next((b for b in self.promo_batches if b["batch_id"] == batch_id), None)

    def _batch_live(self, batch_id) -> int:
        return sum(1 for s in self.early_adopter_seats
                   if s.get("batch_id") == batch_id and not s.get("released_at"))

    def _active_batch(self, plan_id, tier):
        """``promo_active_batch`` (§1.4-1): the lowest-ordered enabled batch of
        this plan + tier with a free seat, or None. Ignores the switches."""
        candidates = sorted(
            (b for b in self.promo_batches
             if b["plan_id"] == plan_id and int(b["price_tier"]) == tier and b.get("enabled")),
            key=lambda b: int(b["batch_order"]),
        )
        for b in candidates:
            if self._batch_live(b["batch_id"]) < int(b["seat_limit"]):
                return b
        return None

    def _active_plan(self, user_id) -> Optional[str]:
        """``user_subscriptions_live.effective_plan_id`` — a running non-free
        term (or a non-expiring grant), else None."""
        row = next((s for s in self.tables["user_subscriptions"]
                    if s.get("user_id") == user_id), None)
        if not row or row.get("plan_id") in (None, "free"):
            return None
        expires = ps._parse_ts(row.get("expires_at"))
        if expires is not None and expires <= _now():
            return None
        return str(row["plan_id"])

    def _first_profession(self, user_id) -> Optional[str]:
        """``users.profession_first_group`` ONLY — the write-once FIRST answer
        (183 §4b). Never ``profession_group``: changing the profession later
        can neither unlock nor lose an offer, in either regime."""
        user = next((u for u in self.tables.get("users", []) if u.get("user_id") == user_id), {})
        return user.get("profession_first_group")

    def _holds_window_for(self, user_id, plan_id) -> bool:
        """(I2) a LIVE seat still inside its window that keeps pricing this
        plan, so no quote is issued for it:
          * a batch seat — for its own plan only;
          * a LEGACY seat — for EVERY plan (it prices any plan today) except the
            legacy Pro → Max switch, which is quoted (decision 1). A legacy seat
            whose payment is gone has no plan, so it blocks everything."""
        seat = self._live_seat(user_id)
        if seat is None or not self._in_window(seat):
            return False
        if seat.get("batch_id"):
            return seat.get("plan_id") == plan_id
        return not (self._legacy_seat_plan(seat) == "pro" and plan_id == "max")

    def _quote(self, user_id, plan_id) -> Optional[dict]:
        """``promo_quote(user, plan)`` — §1.3, in 192's order. None = regular."""
        if not user_id or plan_id not in SEAT_PLAN_IDS:
            return None
        if not (self.ladder_v2 and self.campaign_enabled and self.campaign_row_present):
            return None
        active = self._active_plan(user_id)
        # §1.3-1: on Pro/Max now → no new seat for that plan; Max → Pro is a
        # downgrade. Pro → Max is the switch (rule 6) and IS quoted.
        if active == plan_id or (active == "max" and plan_id == "pro"):
            return None
        # (I2) the held seat keeps pricing this plan.
        if self._holds_window_for(user_id, plan_id):
            return None
        prof_ok = self._first_profession(user_id) in OFFER_PROFESSIONS
        if plan_id == "max":
            t1_ok = True                          # Max Tier 1: everyone, Basic too
        else:
            # Pro Tier 1: the profession offer — the three groups, 183's switch
            # and end date (§1.4-9), and NOT while Basic runs (§1.3-2; decision
            # 2: this blocks ONLY Pro Tier 1).
            t1_ok = (prof_ok and active != "basic"
                     and self.intro_offer_enabled and _now() < self.intro_offer_ends_at)
        for tier in (1, 2):
            if tier == 1 and not t1_ok:
                continue
            if self._tier_used(user_id, plan_id, tier):
                continue                          # Q1/A7: each tier once per plan
            batch = self._active_batch(plan_id, tier)
            if batch is None:
                continue                          # A2: a full tier falls through
            if batch.get("profession_only") and not prof_ok:
                continue                          # (I7)
            return {"batch_id": batch["batch_id"], "price_tier": tier,
                    "price_sar": batch["price_sar"], "months": batch["months"]}
        return None

    def _public_quote(self) -> list[dict]:
        """``promo_public_quote()``."""
        if not self.campaign_row_present:
            return []
        if not self.ladder_v2:
            if not self._legacy_open():
                return []
            months = int(self.campaign_promo_days) // 30
            return [{"plan_id": p["plan_id"], "price_sar": p["promo_price_sar"], "months": months}
                    for p in self.tables["plans"] if p.get("promo_price_sar") is not None]
        if not self.campaign_enabled:                                   # (I8)
            return []
        rows = []
        pro = self._active_batch("pro", 2)               # Tier 1 is personal
        if pro is not None:
            rows.append({"plan_id": "pro", "price_sar": pro["price_sar"], "months": pro["months"]})
        mx = self._active_batch("max", 1) or self._active_batch("max", 2)
        if mx is not None:
            rows.append({"plan_id": "max", "price_sar": mx["price_sar"], "months": mx["months"]})
        return rows

    # ── the functions ────────────────────────────────────────────────────
    def _effective_plan_price(self, params: dict):
        """192's branch order:
          (b1) ✱ a live BATCH seat for THIS plan inside its window → its price
               (flag-independent, before any forfeiture — I5);
          ON:  'purchase' → promo_quote's price (I3: BEFORE the legacy seat, so
               a legacy Pro holder switching to Max pays the Max quote);
               (b2) ✱ a live LEGACY seat in window → promo_price_sar (any plan);
               else list. No forfeiture.
          OFF: 183 — forfeiture, then ANY live seat in window (a legacy seat,
               or a batch seat of the OTHER plan, which carries over at
               ``plans.promo_price_sar`` exactly like a legacy seat), then the
               open campaign.
        """
        plan = self._plan(params.get("p_plan_id")) or {}
        plan_id = plan.get("plan_id")
        listed, promo = plan.get("price_sar"), plan.get("promo_price_sar")
        context = params.get("p_context", "purchase")
        if context not in ("purchase", "current"):
            # 22023 in 138: the return value IS the amount charged.
            raise RuntimeError(f"22023 invalid_parameter_value: unknown p_context {context!r}")
        uid = params.get("p_user_id")
        at = ps._parse_ts(params.get("p_at")) or _now()
        seat = self._live_seat(uid)
        in_window = seat is not None and self._in_window(seat, at)
        recurring = plan_id in SEAT_PLAN_IDS

        # (b1)
        if (listed is not None and recurring and in_window and seat.get("batch_id")
                and seat.get("plan_id") == plan_id):
            return seat["price_sar"]
        legacy_in_window = in_window and not seat.get("batch_id")

        if self.ladder_v2:
            if listed is None:
                return None
            if recurring:
                if context == "purchase":
                    quote = self._quote(uid, plan_id)
                    if quote:
                        return quote["price_sar"]
                if promo is not None and legacy_in_window:
                    return promo                                # (b2)
            return listed

        if listed is None or promo is None:
            return listed
        if recurring:
            # (a) forfeiture — except intro members (183) and (V4) except a
            # user holding a live in-window BATCH seat, whose carry-over must
            # survive a rollback beside an old 'cancelled' legacy row.
            batch_in_window = in_window and bool(seat.get("batch_id"))
            if self._forfeited(uid) and not batch_in_window:
                return listed
            if in_window:                       # legacy, or a batch seat of the other plan
                return promo
        return promo if (context == "purchase" and self._legacy_open()) else listed

    def _intro_offer_quote(self, params: dict) -> list[dict]:
        uid = params.get("p_user_id")
        if self.ladder_v2:
            # 192: a thin wrapper — eligible exactly when the Pro quote is Tier 1.
            quote = self._quote(uid, "pro")
            if quote and quote["price_tier"] == 1:
                return [{"profession_group": self._first_profession(uid),
                         "first_month_sar": quote["price_sar"]}]
            return []
        if not (self.intro_offer_enabled and _now() < self.intro_offer_ends_at
                and self._legacy_open()):
            return []
        group = self._first_profession(uid)            # 183 joins on the first answer
        price = self.intro_offer_prices.get(group)
        if price is None:
            return []
        if self._active_plan(uid) is not None:
            return []
        if any(r.get("user_id") == uid and r.get("plan_id") in SEAT_PLAN_IDS
               and r.get("status") in ("paid", "refunded")
               for r in self.tables["payment_transactions"]):
            return []
        if self._cancelled_seat(uid):
            return []
        return [{"profession_group": group, "first_month_sar": price}]

    def _claim(self, params: dict, row: Optional[dict]) -> list[dict]:
        """192's order: payment → idempotency key 1 (this payment's seat) →
        user mismatch → the v2 path (``promo_batch_id``) or the legacy path."""
        uid = params.get("p_user_id")
        payment_id = params.get("p_payment_id")
        if row is None:
            return _no_seat("payment_not_found")
        held = next((s for s in self.early_adopter_seats if s.get("payment_id") == payment_id), None)
        if held is not None:
            return _seat_row(held, ss.CLAIM_ACTION_ALREADY_CLAIMED)
        if not uid or (row.get("user_id") is not None and row.get("user_id") != uid):
            return _no_seat("user_mismatch")
        if row.get("promo_batch_id"):
            return self._claim_batch(uid, payment_id, row)
        return self._claim_legacy(uid, payment_id, row)

    def _quoted_terms(self, row, batch) -> tuple[str, int]:
        """(5a) The price and months the CUSTOMER WAS QUOTED, from the row's
        ``intro_offer_terms`` (``offer_sar`` / ``offer_months``) — never the
        batch row's current values, which an operator may have edited since.
        Falls back to the batch only for a row with no v2 terms."""
        terms = row.get("intro_offer_terms") if isinstance(row.get("intro_offer_terms"), dict) else {}
        price = terms.get("offer_sar") or batch["price_sar"]
        months = terms.get("offer_months") or batch["months"]
        return str(price), int(months)

    def _claim_batch(self, uid, payment_id, row) -> list[dict]:
        """The payment carries the batch it was QUOTED (§1.4-5) — honoured even
        if the flag went down before capture, and even if the batch filled
        meanwhile (stamped over_capacity) — and the seat freezes the QUOTED
        price and months (5a)."""
        batch = self._batch(row["promo_batch_id"])
        if batch is None or batch["plan_id"] != row.get("plan_id"):
            return _no_seat("plan_not_eligible")
        price, months = self._quoted_terms(row, batch)
        # The amount gate, against the QUOTED price (not plans.promo_price_sar).
        quoted = ps.q2(ps._dec(row.get("amount_sar")) + ps._dec(row.get("upgrade_credit_sar")))
        if row.get("amount_sar") is None or quoted > ps.q2(price) + ps._dec("0.01"):
            return _no_seat(ss.CLAIM_ACTION_NOT_PROMO_PRICED)
        live = self._live_seat(uid)
        if live is not None and live.get("batch_id") == batch["batch_id"]:
            return _seat_row(live, ss.CLAIM_ACTION_ALREADY_CLAIMED)    # same batch: keep
        over = self._batch_live(batch["batch_id"]) >= int(batch["seat_limit"])
        anchor = ps._parse_ts(row.get("paid_at")) or _now()
        if live is not None:
            # (I4) ANY live seat not in this batch — legacy, other plan, or a
            # spent / other batch of this plan — is released 'upgraded': counts
            # as used, never forfeits. (5d) It records WHICH payment superseded
            # it, so refunding that payment can give it back.
            live["released_at"] = _iso(_now())
            live["release_reason"] = "upgraded"
            live["released_by_payment_id"] = payment_id
        seat = {
            "seat_id": str(uuid.uuid4()), "user_id": uid, "payment_id": payment_id,
            "claimed_at": _iso(anchor),
            "promo_ends_at": _iso(anchor + timedelta(days=30 * months)),
            "released_at": None, "release_reason": None, "over_capacity": over,
            "batch_id": batch["batch_id"], "plan_id": batch["plan_id"],
            "price_tier": int(batch["price_tier"]), "price_sar": price,
        }
        self.early_adopter_seats.append(seat)
        return _seat_row(seat, ss.CLAIM_ACTION_CLAIMED)

    def _paid_after_flip(self, row) -> bool:
        """192 R2: flag ON and the payment was CREATED at/after
        ``ladder_v2_at``. A NULL created_at compares NULL in SQL, i.e. not after
        (the column is NOT NULL in practice)."""
        if not (self.ladder_v2 and self.ladder_v2_at):
            return False
        created = ps._parse_ts(row.get("created_at"))
        return created is not None and created >= self.ladder_v2_at

    def _claim_legacy(self, uid, payment_id, row) -> list[dict]:
        """183's body (no promo_batch_id), in 192's order: carry-over → plan →
        forfeiture (flag off) → the legacy gate → R2 → campaign row → seat."""
        # Idempotency key 2: a live seat carries over (an upgrade keeps its window).
        live = self._live_seat(uid)
        if live is not None:
            return _seat_row(live, ss.CLAIM_ACTION_ALREADY_CLAIMED)
        plan_row = self._plan(row.get("plan_id")) or {}
        promo = plan_row.get("promo_price_sar")
        if promo is None or row.get("plan_id") not in SEAT_PLAN_IDS:
            return _no_seat("plan_not_eligible")
        if not self.ladder_v2 and self._forfeited(uid):
            return _no_seat(ss.CLAIM_ACTION_FORFEITED)
        # THE PROMO-PRICE GATE; fails closed on a NULL amount.
        amount = row.get("amount_sar")
        quoted = ps.q2(ps._dec(amount) + ps._dec(row.get("upgrade_credit_sar")))
        if amount is None or quoted > ps.q2(promo) + ps._dec("0.01"):
            return _no_seat(ss.CLAIM_ACTION_NOT_PROMO_PRICED)
        # 192 R2 — after the gate, so only the unstamped promo-priced anomaly
        # lands here.
        if self._paid_after_flip(row):
            return _no_seat(POST_FLIP_NO_BATCH_ACTION)
        if not self.campaign_row_present:
            return _no_seat(ss.CLAIM_ACTION_CAMPAIGN_DISABLED)
        anchor = ps._parse_ts(row.get("paid_at")) or _now()
        seat = {
            "seat_id": str(uuid.uuid4()), "user_id": uid, "payment_id": payment_id,
            "claimed_at": _iso(anchor),
            "promo_ends_at": _iso(anchor + timedelta(days=self.campaign_promo_days)),
            "released_at": None, "release_reason": None,
            # Past the gate capacity never refuses — it only STAMPS (§3.5): the
            # live count against seat_limit, whatever `enabled` says.
            "over_capacity": self._live_count() >= int(self.campaign_seat_limit),
        }
        self.early_adopter_seats.append(seat)
        return _seat_row(seat, ss.CLAIM_ACTION_CLAIMED)

    def _live_count(self) -> int:
        return sum(1 for s in self.early_adopter_seats if not s.get("released_at"))

    def _release(self, params: dict) -> bool:
        """192 6.10 — 138's release, SAME signature, plus (V5): on a 'refund'
        whose released seat was claimed by a payment that is now 'refunded',
        give back the seat THAT payment superseded ('upgraded',
        ``released_by_payment_id``) while its window is open, over_capacity
        sticky against that seat's own pool. A refund of any other payment (a
        renewal) releases the live seat and restores nothing."""
        uid = params.get("p_user_id")
        reason = params.get("p_reason")
        if not uid or reason not in ("refund", "cancelled"):
            return False                       # 138: NOTICE + false, never the CHECK
        seat = self._live_seat(uid)
        if seat is None:
            return False
        seat["released_at"] = _iso(_now())
        seat["release_reason"] = reason
        claimed_by = seat.get("payment_id")
        if reason == "refund" and claimed_by and \
                (self._payment(claimed_by) or {}).get("status") == "refunded":
            superseded = [s for s in self.early_adopter_seats
                          if s.get("user_id") == uid and s.get("release_reason") == "upgraded"
                          and s.get("released_by_payment_id") == claimed_by
                          and self._in_window(s)]
            if superseded:
                prev = max(superseded, key=lambda s: ps._parse_ts(s["released_at"]))
                if prev.get("batch_id"):
                    b = self._batch(prev["batch_id"]) or {}
                    full = self._batch_live(prev["batch_id"]) >= int(b.get("seat_limit") or 0) \
                        if b else False
                else:
                    full = self._live_count() >= int(self.campaign_seat_limit)
                prev["over_capacity"] = bool(prev.get("over_capacity")) or full
                prev["released_at"] = None
                prev["release_reason"] = None
                prev["released_by_payment_id"] = None
        return True

    def _restore(self, params: dict) -> bool:
        """M138:1260-1315 — «تراجع عن الإلغاء»:
          * a live seat already → TRUE, nothing written;
          * else the user's MOST RECENT release (by released_at) must be a
            'cancelled' one — a refund (or anything else) after it means there
            is nothing to undo;
          * restored unconditionally; over_capacity is sticky, and set when the
            live count already reached seat_limit."""
        uid = params.get("p_user_id")
        if not uid:
            return False
        if self._live_seat(uid) is not None:
            return True
        released = [s for s in self.early_adopter_seats
                    if s.get("user_id") == uid and s.get("released_at")]
        if not released:
            return False
        latest = max(released, key=lambda s: ps._parse_ts(s["released_at"]))
        if latest.get("release_reason") != "cancelled":
            return False
        latest["over_capacity"] = bool(latest.get("over_capacity")) or (
            self.campaign_row_present and self._live_count() >= int(self.campaign_seat_limit)
        )
        latest["released_at"] = None
        latest["release_reason"] = None
        return True

    def _status(self, params: dict) -> list[dict]:
        """``early_adopter_status`` (138 / 192 I6): has_seat = a live seat whose
        window is still open (``v_ends > now()``) and not forfeited — the 183
        predicate only bites a live LEGACY seat with the flag off.
        promo_ends_at = the live seat's window end, whether or not has_seat."""
        uid = params.get("p_user_id")
        seat = self._live_seat(uid)
        ends = (seat or {}).get("promo_ends_at")
        forfeit = (seat is not None and not seat.get("batch_id")
                   and not self.ladder_v2 and self._forfeited(uid))
        has_seat = ends is not None and ps._parse_ts(ends) > _now() and not forfeit
        return [{"campaign_open": self._campaign_open(),
                 "has_seat": has_seat,
                 "promo_ends_at": ends}]

    def _checkout_price(self, params: dict) -> list[dict]:
        """``promo_checkout_price(user, plan)`` (192) — EXACTLY ONE row, the
        whole v2 sale in one statement: ``ladder_v2``, the price to charge, and
        when it comes from a batch its id / tier / months, plus the profession
        group for Pro Tier 1. Flag off: only ``ladder_v2 = false`` matters.
        Mirrors effective_plan_price('purchase') with the quote taken once."""
        uid, plan_id = params.get("p_user_id"), params.get("p_plan_id")
        if self._plan(plan_id) is None:
            return []                                    # unknown plan: no row
        row = {"ladder_v2": bool(self.ladder_v2), "price_sar": None, "batch_id": None,
               "price_tier": None, "months": None, "intro_group": None}
        if not self.ladder_v2:
            # today's purchase price, nothing to stamp (the backend ignores it)
            return [{**row, "price_sar": self._effective_plan_price(
                {"p_user_id": uid, "p_plan_id": plan_id, "p_context": "purchase"})}]
        plan = self._plan(plan_id) or {}
        listed, promo = plan.get("price_sar"), plan.get("promo_price_sar")
        seat = self._live_seat(uid)
        in_window = seat is not None and self._in_window(seat)
        recurring = plan_id in SEAT_PLAN_IDS
        if listed is None:
            return [row]
        if recurring and in_window and seat.get("batch_id") and seat.get("plan_id") == plan_id:
            return [{**row, "price_sar": seat["price_sar"]}]               # (b1)
        if recurring:
            quote = self._quote(uid, plan_id)
            if quote:
                group = (self._first_profession(uid)
                         if plan_id == "pro" and quote["price_tier"] == 1 else None)
                return [{**row, "price_sar": quote["price_sar"], "batch_id": quote["batch_id"],
                         "price_tier": quote["price_tier"], "months": quote["months"],
                         "intro_group": group}]
            if promo is not None and in_window and not seat.get("batch_id"):
                return [{**row, "price_sar": promo}]                       # (b2)
        return [{**row, "price_sar": listed}]

    # ── dispatch ─────────────────────────────────────────────────────────
    def promo_rpc(self, name: str, params: dict, row: Optional[dict]):
        """The model's answer for ``name``, or ``NOT_MINE``."""
        if name == "early_adopter_open":
            return self._campaign_open()
        if name == "effective_plan_price":
            return self._effective_plan_price(params)
        if name == "intro_offer_quote":
            return self._intro_offer_quote(params)
        if name == "is_intro_offer_member":
            return self._intro_member(params.get("p_user_id"))
        if name == "claim_early_adopter_seat":
            return self._claim(params, row)
        if name == "release_early_adopter_seat":
            return self._release(params)
        if name == "restore_early_adopter_seat":
            return self._restore(params)
        if name == "early_adopter_status":
            return self._status(params)
        if name == "promo_quote":
            quote = self._quote(params.get("p_user_id"), params.get("p_plan_id"))
            return [quote] if quote else []
        if name == "promo_public_quote":
            return self._public_quote()
        if name == "promo_checkout_price":
            return self._checkout_price(params)
        return NOT_MINE
