"""المشتركون الأوائل — the launch campaign, end to end.

Plan: `.claude/plans/early_adopters.md` §8. Migration ``138_early_adopters.sql``
(the seats table, the campaign singleton, and the six service-role functions);
the product-docs copy rides along in ``140_early_adopter_product_docs.sql``.

WHAT THIS SUITE IS FOR ─────────────────────────────────────────────────────
The campaign turns ONE column (``plans.price_sar``) into a function of
*(user, plan, context, now)*, and three separate readers spend money from it:
checkout, the upgrade credit, and the unattended renewal job. Every test below
pins a rule that, if it broke, would either charge somebody the wrong amount or
hand out a discount nobody bought — and most of them would do it silently,
because the campaign's whole design is that nothing about it is visible to the
user beyond a price.

The two rules most likely to be lost in a later refactor have their names in the
test names: **rule 6** (a completed cancellation forfeits the price forever) and
**the context split** ('current' never consults the campaign flag).

THE INVARIANT MOST OF THIS FILE DEFENDS ────────────────────────────────────

    A seat holder is precisely someone who was charged the promotional price.

138 gates every claim on ``amount_sar + upgrade_credit_sar <= promo_price_sar +
0.01``. Break that in either direction and the campaign leaks: seats to people
who paid the list price, or promo renewals to terms that were never discounted.

WHAT THIS SUITE CANNOT DO — VERIFY THESE AGAINST THE LIVE DB ───────────────
``FakeSupabase`` is a MODEL of 138, not 138: a wrong model produces a green
test. This list is kept HERE, rather than in a chat message, because it is the
part of the work that outlives the conversation. After 138 is applied:

1. **RLS + EXECUTE grants — the one that matters most.** Deny-all RLS on
   ``early_adopter_seats`` and ``early_adopter_campaign``, and EXECUTE revoked
   from ``anon``/``authenticated`` on all six functions. Every call site here
   uses the service-role client, so a missing REVOKE breaks NO test — it simply
   publishes the remaining seat count to any logged-in user through PostgREST,
   which is the single thing §1.10 forbids.
2. **Is forfeiture user-level or pro/max-scoped?** This file encodes user-level
   (see ``test_forfeiture_is_a_property_of_the_user_not_of_the_plan``).
3. **An unknown ``p_context`` must RAISE (22023), not return NULL.** A NULL
   slips silently into the catalog fallback — the one place fail-open hides a
   real fault.
4. **The 100th-seat race** (``pg_advisory_xact_lock``) — every claim here is
   single-threaded by construction.
5. **Column and shape drift**: ``plans.promo_price_sar`` populated 39.90/49.90/
   99.90; ``early_adopter_seats.payment_id`` UNIQUE; the partial one-live-seat
   index; ``early_adopter_status``'s three columns; and that a PostgREST numeric
   of ``49.9`` still renders ``"49.90"`` on the public endpoint.
6. **The gate's exact inputs**: the ledger row's ``amount_sar`` +
   ``upgrade_credit_sar`` (not the provider payload) and the 0.01 epsilon.

138's claim vocabulary is settled (8 values, mirrored in
``subscription_service.CLAIM_ACTIONS_OK``): ``claimed``, ``already_claimed``,
``campaign_disabled``, ``not_promo_priced``, ``forfeited``,
``plan_not_eligible``, ``payment_not_found``, ``user_mismatch``. There is no
"campaign is full" refusal — capacity only ever STAMPS.

⚠ ``.gitignore`` line 19 blankets ``backend/tests/*``. THIS FILE NEEDS
``git add -f`` or it will never be committed — exactly what happened to
``test_subscription_renewal.py`` (80 tests over the money path, invisible to git
for weeks).
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

import pytest

from backend.app.services import payment_method_service as pm
from backend.app.services import payment_service as ps
from backend.app.services import renewal_service as rs
from backend.app.services import subscription_service as ss
from shared.config import get_settings

# The in-memory PostgREST + RPC stand-in, and its builders. Imported rather than
# re-implemented: a third copy of the 138 model is a third thing to keep true.
# ONLY non-test names are imported — pulling a `test_*` symbol in here would make
# pytest collect and run that test a second time under this module.
from backend.tests.test_payments import (
    FakeSupabase,
    MOYASAR_ID,
    USER,
    _event,
    _iso,
    _now,
    checkout,
    moyasar_payment,
    paid_row,
    patch_fetch,
    run,
    sub,
)
from backend.tests import test_subscription_renewal as rt

OTHER = "22222222-2222-2222-2222-222222222222"
THIRD = "33333333-3333-3333-3333-333333333333"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def keys(monkeypatch):
    """Test-mode Moyasar keys. Same shape as the payments suite's fixture,
    defined locally so this module does not depend on another test module's
    fixture names surviving a rename."""
    monkeypatch.setenv("MOYASAR_SECRET_KEY", "sk_test_abc")
    monkeypatch.setenv("MOYASAR_PUBLISHABLE_KEY", "pk_test_abc")
    monkeypatch.setenv("MOYASAR_WEBHOOK_SECRET", "whsec_test_value")
    monkeypatch.delenv("RECEIPTS_SMTP_PASSWORD", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def renewals_on(keys, monkeypatch):
    """The renewal job armed, with the provider replaced by a recorder.

    Returns the list of charged amounts in halalas — the only number that
    matters for the context-split tests, because it is what the customer's card
    is actually debited.
    """
    monkeypatch.setenv("SUBSCRIPTION_AUTO_RENEWAL_ENABLED", "true")
    get_settings.cache_clear()

    charged: list[int] = []

    async def _charge(*, token, amount_halalas, description, payment_id, metadata=None):
        charged.append(amount_halalas)
        return {
            "id": str(uuid.uuid4()), "status": "paid", "amount": amount_halalas,
            "currency": "SAR", "live": False, "metadata": {"payment_id": payment_id},
            "source": {"type": "creditcard", "company": "mada", "token": token},
        }

    monkeypatch.setattr(ps, "charge_saved_card", _charge)
    yield charged
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def no_provider_calls(monkeypatch):
    """⚠ AUTOUSE, for the reason the renewal suite's version of this is autouse:
    ``capture_payment_method`` fetches the TOKEN object, and without a stub that
    is a REAL HTTPS call to Moyasar from a unit suite."""
    monkeypatch.setattr(pm, "fetch_token_at_provider", lambda token: None)


@pytest.fixture(autouse=True)
def fresh_campaign_cache():
    """``GET /payments/early-adopter`` is cached for 30s in module state. Tests
    must not inherit each other's answer."""
    ps._CAMPAIGN_CACHE = None
    yield
    ps._CAMPAIGN_CACHE = None


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def open_campaign(subscription=None, *, seat_limit=100) -> FakeSupabase:
    db = FakeSupabase(subscription if subscription is not None else sub("free"))
    db.campaign_enabled = True
    db.campaign_seat_limit = seat_limit
    return db


def seat(db, *, user_id=USER, days_left=60, released=False, reason=None,
         over_capacity=False, payment_id=None):
    """Put a seat row in the table directly — the pre-existing state a test
    starts from, as opposed to one earned through a purchase."""
    row = {
        "seat_id": str(uuid.uuid4()),
        "user_id": user_id,
        "payment_id": payment_id or str(uuid.uuid4()),
        "claimed_at": _iso(_now() - timedelta(days=90 - days_left)),
        "promo_ends_at": _iso(_now() + timedelta(days=days_left)),
        "released_at": _iso(_now()) if released else None,
        "release_reason": reason,
        "over_capacity": over_capacity,
    }
    db.early_adopter_seats.append(row)
    return row


def live_seats(db, user_id=None):
    return [
        s for s in db.early_adopter_seats
        if not s["released_at"] and (user_id is None or s["user_id"] == user_id)
    ]


def pay(db, monkeypatch, payment_id, halalas, *, user_id=USER):
    """Settle an open checkout through /verify (the browser path)."""
    patch_fetch(monkeypatch, moyasar_payment(payment_id, amount=halalas))
    return run(ps.verify_payment(db, user_id, MOYASAR_ID))


def buy(db, monkeypatch, plan_id="pro"):
    """Checkout + settle, at whatever price the campaign quotes."""
    quote = checkout(db, plan_id)
    result = pay(db, monkeypatch, quote["payment_id"], quote["amount_halalas"])
    return quote, result


def price_now(db, plan_id="pro", *, context=ps.PRICE_CONTEXT_PURCHASE, user_id=USER):
    catalog = {"basic": "49.90", "pro": "89.90", "max": "189.90"}[plan_id]
    return ps._effective_price(db, user_id, plan_id, catalog, context=context)


# ═══════════════════════════════════════════════════════════════════════════
# 1. The shipped state — merging this must change behaviour for zero users
# ═══════════════════════════════════════════════════════════════════════════


def test_the_campaign_ships_disabled_and_nothing_changes(keys, monkeypatch):
    """138 inserts ``enabled = false``. Deploy day must be indistinguishable
    from the day before it — same price, no seats, nobody a member."""
    db = FakeSupabase(sub("free"))                 # campaign_enabled defaults False
    quote, result = buy(db, monkeypatch, "pro")

    assert quote["amount_sar"] == "89.90"
    assert result["granted"] is True
    assert db.early_adopter_seats == []
    assert run(ss.get_subscription(db, USER))["early_adopter"] == {
        "is_member": False, "promo_ends_at": None
    }


def test_the_claim_is_attempted_even_while_disabled(keys, monkeypatch):
    """The RPC decides, not Python. A local "is the campaign on?" check would be
    a second copy of the rule, and it would be the one that goes stale."""
    db = FakeSupabase(sub("free"))
    buy(db, monkeypatch, "pro")
    assert "claim_early_adopter_seat" in db.calls


def test_the_kill_switch_stops_new_quotes_not_settled_money(keys, monkeypatch):
    """An operator flips ``enabled = false`` while a promo-priced quote is still
    in 3DS. That payment was charged 49.90 and it keeps its seat — the switch
    exists to stop new quotes, and money already taken is not a quote.

    The seat is the RECORD of what they paid: while the switch is down the price
    resolves to list for everybody (§3.4 rule 1, the kill switch is total), and
    flipping it back on restores this user's 49.90 rather than having lost their
    membership forever."""
    db = open_campaign(sub("free"))
    quote = checkout(db, "pro")
    assert quote["amount_sar"] == "49.90"

    db.campaign_enabled = False                    # the kill switch, mid-flight
    result = pay(db, monkeypatch, quote["payment_id"], quote["amount_halalas"])

    assert result["granted"] is True
    assert len(live_seats(db, USER)) == 1

    db.campaign_enabled = True                     # …and back on
    assert price_now(db, "pro", context=ps.PRICE_CONTEXT_CURRENT) == ps.q2("49.90")


def test_a_missing_campaign_row_is_an_error_not_a_shrug(keys, caplog):
    """``campaign_disabled`` is the ONE remaining case where somebody charged the
    promotional price goes unseated, and it is a broken install rather than a
    policy state: no ``promo_days`` to anchor a window, no ``seat_limit`` to
    measure against.

    It must be LOUD. The customer paid 49.90 and will renew at 89.90 unless an
    operator restores the row and re-runs the claim — a DEBUG line here is how
    that goes unnoticed for 90 days."""
    db = open_campaign(sub("free"))
    db.campaign_row_present = False
    row = paid_row(db, plan_id="pro", amount="49.90")

    with caplog.at_level(logging.ERROR, logger="backend.app.services.subscription_service"):
        ss.claim_early_adopter_seat(db, USER, row["payment_id"], plan_id="pro")

    assert db.early_adopter_seats == []
    assert any(
        r.levelno >= logging.ERROR and "early_adopter_campaign row" in r.getMessage()
        for r in caplog.records
    )


# ═══════════════════════════════════════════════════════════════════════════
# 2. Capacity — observable as a price, never as a count
# ═══════════════════════════════════════════════════════════════════════════
#
# NOTE ON EVERY TEST IN THIS SECTION: none of them asks how many seats are left,
# because nothing in the product may. They assert what a user can actually see —
# the price they are quoted and whether their own purchase earned a seat.


def test_the_last_seat_is_claimable_at_the_promo_price(keys, monkeypatch):
    db = open_campaign(seat_limit=3)
    seat(db, user_id=OTHER)
    seat(db, user_id=THIRD)

    quote, result = buy(db, monkeypatch, "pro")

    assert quote["amount_sar"] == "49.90"
    assert result["granted"] is True
    mine = live_seats(db, USER)
    assert len(mine) == 1 and mine[0]["over_capacity"] is False


def test_once_the_seats_are_gone_the_price_is_simply_the_list_price(keys, monkeypatch):
    """Payer #101. No error, no explanation, no hint that a campaign ever ran —
    §7's "never anywhere" rule. Just 89.90."""
    db = open_campaign(seat_limit=2)
    seat(db, user_id=OTHER)
    seat(db, user_id=THIRD)

    quote, result = buy(db, monkeypatch, "pro")

    assert quote["amount_sar"] == "89.90"
    assert result["granted"] is True                # they still get what they paid for
    assert live_seats(db, USER) == []


def test_a_released_seat_returns_to_the_pool(keys, monkeypatch):
    """Capacity is ``count(*) WHERE released_at IS NULL`` — 138 has no seat_no,
    so a refunded seat is re-issuable rather than a hole in a numbering."""
    db = open_campaign(seat_limit=2)
    seat(db, user_id=OTHER)
    seat(db, user_id=THIRD, released=True, reason="refund")

    assert checkout(db, "pro")["amount_sar"] == "49.90"


# ═══════════════════════════════════════════════════════════════════════════
# 3. THE PROMO-PRICE GATE — both edges of the campaign
#
#     A seat holder is precisely someone who was charged the promotional price.
#
# 138 gates every claim on ``amount_sar + upgrade_credit_sar <= promo_price_sar
# + 0.01``. The two boundary tests are a PAIR, one at each instant the campaign
# moves, and they fail in opposite directions if the gate is ever narrowed back
# to the over-capacity branch alone:
#
#   * the CLOSING instant — a promo quote settling after the seats ran out keeps
#     its seat (§3.5), but a list-priced payment does not, which is what stops
#     payer #101, #102 … each taking one forever;
#   * the OPENING instant — a list-priced quote from the day before settles
#     during the campaign and earns nothing, so it cannot collect 90 days of
#     promo renewals it never paid for.
# ═══════════════════════════════════════════════════════════════════════════


def test_a_promo_quote_settling_after_the_close_still_gets_its_seat(keys, monkeypatch):
    """The alternatives were refusing a payment that already succeeded, or
    charging the full price after quoting the promo. Both are worse than one
    extra seat, so the seat is granted and STAMPED for the one query that finds
    them (``WHERE over_capacity``)."""
    db = open_campaign(seat_limit=1)
    quote = checkout(db, "pro")                    # 49.90, seats still open
    assert quote["amount_sar"] == "49.90"

    seat(db, user_id=OTHER)                        # the hundredth fills mid-3DS
    result = pay(db, monkeypatch, quote["payment_id"], quote["amount_halalas"])

    assert result["granted"] is True
    mine = live_seats(db, USER)
    assert len(mine) == 1 and mine[0]["over_capacity"] is True


def test_at_the_closing_instant_a_list_priced_payment_gets_no_seat(keys, monkeypatch):
    """THE GATE, AT THE CLOSING EDGE. Without it, "grant the seat anyway when
    the campaign is full" is not a bounded exception for a handful of in-flight
    quotes — it is every payer after the hundredth, forever, each one collecting
    90 days of promo renewals they never paid for."""
    db = open_campaign(seat_limit=1)
    seat(db, user_id=OTHER)                        # closed before they even start

    quote, result = buy(db, monkeypatch, "pro")

    assert quote["amount_sar"] == "89.90"          # quoted at list…
    assert result["granted"] is True
    assert live_seats(db, USER) == []              # …so no seat, over-capacity or not


def test_at_the_opening_instant_a_list_priced_quote_gets_no_seat(keys, monkeypatch):
    """THE GATE, AT THE OPENING EDGE — the mirror of the test above, and the
    hole that existed while the gate only guarded the over-capacity branch.

    A quote priced at 89.90 the moment BEFORE the campaign opened settles a few
    minutes after it did. The campaign is wide open and the claim would have
    succeeded on capacity alone — but this payment was never charged the promo
    price, so it earns no seat and, critically, no promo renewals."""
    db = FakeSupabase(sub("free"))                 # campaign not yet running
    quote = checkout(db, "pro")
    assert quote["amount_sar"] == "89.90"

    db.campaign_enabled = True                     # marketing flips the switch
    result = pay(db, monkeypatch, quote["payment_id"], quote["amount_halalas"])

    assert result["granted"] is True
    assert live_seats(db, USER) == []
    # The part that costs money if it is wrong: no seat ⇒ 'current' ⇒ list price
    # at every renewal, for a term that was bought at the list price.
    assert price_now(db, "pro", context=ps.PRICE_CONTEXT_CURRENT) == ps.q2("89.90")
    assert run(ss.get_subscription(db, USER))["early_adopter"]["is_member"] is False


def test_being_full_stamps_a_seat_it_never_refuses_one(keys, monkeypatch):
    """Past the gate, capacity is not a veto — it only STAMPS. Two promo-priced
    quotes opened while seats remained both settle after the limit fills, and
    both are honoured, because the alternative is charging someone the full
    price after quoting them the promo. The overshoot is bounded by the number
    of open quotes at that instant and visible in one query."""
    db = open_campaign(seat_limit=1)
    mine = checkout(db, "pro")
    theirs = checkout(db, "pro", user_id=OTHER)
    assert (mine["amount_sar"], theirs["amount_sar"]) == ("49.90", "49.90")

    seat(db, user_id=THIRD)                        # the limit fills mid-3DS
    pay(db, monkeypatch, mine["payment_id"], mine["amount_halalas"])
    pay(db, monkeypatch, theirs["payment_id"], theirs["amount_halalas"], user_id=OTHER)

    granted = live_seats(db, USER) + live_seats(db, OTHER)
    assert len(granted) == 2                       # neither was refused…
    assert all(s["over_capacity"] for s in granted)  # …and both are stamped


def test_a_payment_that_cannot_say_what_it_charged_mints_nothing(keys):
    """The gate fails CLOSED on a NULL ``amount_sar``: a row that cannot prove it
    was promo-priced does not get the benefit of the doubt.

    Driven straight at the wrapper because the state itself is impossible to
    reach through checkout — which is the point of testing it."""
    db = open_campaign(sub("free"))
    row = paid_row(db, plan_id="pro", amount=None)

    ss.claim_early_adopter_seat(db, USER, row["payment_id"], plan_id="pro")

    assert db.early_adopter_seats == []


def test_an_over_capacity_upgrade_is_measured_on_its_quote_not_its_charge(keys, monkeypatch):
    """An upgrade pays ``promo − credit``, so the gate has to add the credit back
    before comparing. Measuring the CHARGE alone would refuse every promo-priced
    upgrade that settled late (charge 56.65 ≤ 49.90 is false)."""
    db = open_campaign(sub("pro", source="payment", days_left=26), seat_limit=1)
    seat(db, user_id=USER)                         # a member upgrading
    quote = checkout(db, "max")
    assert (quote["amount_sar"], quote["credit_sar"]) == ("56.65", "43.25")

    db.campaign_seat_limit = 0                     # campaign closes mid-flight
    result = pay(db, monkeypatch, quote["payment_id"], quote["amount_halalas"])

    assert result["granted"] is True               # 56.65 + 43.25 = 99.90 = max promo
    assert len(live_seats(db, USER)) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 4. RULE 6 — a completed cancellation forfeits the price, permanently
#
# The owner's rule, stated in the cancel dialog, and the one a later refactor is
# most likely to drop: it lives in a predicate nobody looking at the price code
# would think to check. Both halves are here, and both name the rule.
# ═══════════════════════════════════════════════════════════════════════════


def test_rule_6_a_completed_cancellation_forfeits_the_promo_price_forever(keys, monkeypatch):
    db = open_campaign(sub("free"))
    buy(db, monkeypatch, "pro")                    # joins at 49.90
    db.tables["user_subscriptions"][0]["source"] = "payment"
    run(ss.cancel_renewal(db, USER, reason="expensive"))

    # Seats are still wide open — and it makes no difference to them.
    assert checkout(db, "pro")["amount_sar"] == "89.90"
    assert price_now(db, "pro") == ps.q2("89.90")
    assert run(ss.get_subscription(db, USER))["early_adopter"]["is_member"] is False


def test_rule_6_a_forfeited_repurchase_earns_no_new_seat(keys, monkeypatch):
    """The other half of the same rule. If the re-buy claimed a seat, the price
    rule would be undone by the seat rule: their next renewal reads 'current',
    sees a live seat, and discounts a term they paid list price for."""
    db = open_campaign(sub("free"))
    buy(db, monkeypatch, "pro")
    db.tables["user_subscriptions"][0]["source"] = "payment"
    run(ss.cancel_renewal(db, USER, reason="expensive"))

    quote, result = buy(db, monkeypatch, "pro")

    assert quote["amount_sar"] == "89.90"
    assert result["granted"] is True
    assert live_seats(db, USER) == []
    assert price_now(db, "pro", context=ps.PRICE_CONTEXT_CURRENT) == ps.q2("89.90")


def test_rule_6_undo_restores_the_seat_the_price_and_the_original_window(keys, monkeypatch):
    """«يمكنك التراجع عن الإلغاء قبل انتهاء اشتراكك» has to be literally true.
    The undo restores the ORIGINAL seat — a new one would silently hand out a
    fresh 90 days, which is the cancel-and-undo loop nobody should be able to
    run."""
    db = open_campaign(sub("free"))
    buy(db, monkeypatch, "pro")
    db.tables["user_subscriptions"][0]["source"] = "payment"
    original_window = live_seats(db, USER)[0]["promo_ends_at"]

    run(ss.cancel_renewal(db, USER, reason="expensive"))
    state = run(ss.reactivate_renewal(db, USER))

    assert state["early_adopter"]["is_member"] is True
    assert state["early_adopter"]["promo_ends_at"] == original_window
    assert price_now(db, "pro", context=ps.PRICE_CONTEXT_CURRENT) == ps.q2("49.90")
    assert len(db.early_adopter_seats) == 1         # restored, not re-issued


def test_a_cancellation_failure_never_fails_the_cancellation(keys, monkeypatch):
    """The flag write IS the cancellation (subscription_service rule 2). A seat
    row that will not move is bookkeeping, and the user asked to cancel."""
    db = open_campaign(sub("free"))
    buy(db, monkeypatch, "pro")
    db.tables["user_subscriptions"][0]["source"] = "payment"

    original_rpc = db.rpc

    def exploding(name, params):
        if name == "release_early_adopter_seat":
            raise RuntimeError("postgrest exploded")
        return original_rpc(name, params)

    monkeypatch.setattr(db, "rpc", exploding)
    state = run(ss.cancel_renewal(db, USER, reason="other"))

    assert state["renewal_cancelled_at"]           # the cancellation stands
    assert db.tables["subscription_cancellations"]  # and the survey was recorded


# ═══════════════════════════════════════════════════════════════════════════
# 5. A refund is not a cancellation
# ═══════════════════════════════════════════════════════════════════════════


def test_a_refund_releases_the_seat_and_lets_them_rejoin(keys, monkeypatch):
    """§1.5 — a refund voids the status rather than punishing it. The refunded
    buyer is in exactly the position of someone who never bought."""
    db = open_campaign(sub("free"))
    quote, _ = buy(db, monkeypatch, "pro")
    row = db.tables["payment_transactions"][0]
    row["raw_payload"] = {"id": MOYASAR_ID, "fee": 173}

    async def _refund(ref, amount):
        return {"id": ref, "status": "refunded", "amount": amount}

    monkeypatch.setattr(ps, "refund_at_provider", _refund)
    run(ps.refund_payment(db, USER, row["payment_id"]))

    assert db.early_adopter_seats[0]["release_reason"] == "refund"
    assert live_seats(db, USER) == []
    assert checkout(db, "pro")["amount_sar"] == "49.90"      # may buy back in


def test_a_refund_release_and_a_cancel_release_are_not_the_same_thing(keys, monkeypatch):
    """Same table, same column, opposite meaning — and the difference is the
    whole of rule 5 vs rule 6. This is the test that fails if a later change
    stamps one reason where it meant the other."""
    refunded = open_campaign(sub("free"))
    seat(refunded, released=True, reason="refund")
    cancelled = open_campaign(sub("free"))
    seat(cancelled, released=True, reason="cancelled")

    assert checkout(refunded, "pro")["amount_sar"] == "49.90"
    assert checkout(cancelled, "pro")["amount_sar"] == "89.90"


# ═══════════════════════════════════════════════════════════════════════════
# 6. THE CONTEXT SPLIT — 'current' never consults the campaign flag
#
# The line that keeps the 90-day promise, and the line that stops an automatic
# charge from enrolling somebody. Both are one keyword argument.
# ═══════════════════════════════════════════════════════════════════════════


def test_the_context_split_a_non_member_renews_at_the_list_price(renewals_on):
    """An open campaign must not reach the renewal job. If it did, every
    existing subscriber would be silently discounted on a charge they did not
    choose to make — and, if a seat followed it, enrolled by it (§1.2: the
    current payers are not enrolled)."""
    db = rt.FakeSupabase(rt.sub("pro"))
    rt.store_method(db)
    db.campaign_enabled = True                     # wide open

    assert run(rs.run_due_renewals(db)) == {"scanned": 1, "renewed": 1}
    assert renewals_on == [8990]
    assert db.early_adopter_seats == []
    assert "claim_early_adopter_seat" not in db.calls


def test_the_context_split_a_renewal_never_claims_a_seat(renewals_on):
    """Even for a user who IS a member: «المشتركون الأوائل» is the first 100 to
    PAY, and a saved-card charge is not a decision made today. The claim is
    gated on ``initiated_by``, which is why this holds for the seat holder too."""
    db = rt.FakeSupabase(rt.sub("pro"))
    rt.store_method(db)
    db.campaign_enabled = True
    db.early_adopter_seats.append({
        "seat_id": "s", "user_id": rt.USER, "payment_id": "p",
        "promo_ends_at": _iso(_now() + timedelta(days=45)),
        "released_at": None, "release_reason": None, "over_capacity": False,
    })

    run(rs.run_due_renewals(db))

    assert renewals_on == [4990]
    assert "claim_early_adopter_seat" not in db.calls
    assert len(db.early_adopter_seats) == 1


def test_the_context_split_a_seat_holder_keeps_the_promo_after_the_campaign_closes(renewals_on):
    """§1.4 — the promise travels with the subscription. Closing the campaign
    repriced nobody who was already inside it."""
    db = rt.FakeSupabase(rt.sub("pro"))
    rt.store_method(db)
    db.campaign_enabled = True
    db.campaign_seat_limit = 0                     # closed to newcomers
    db.early_adopter_seats.append({
        "seat_id": "s", "user_id": rt.USER, "payment_id": "p",
        "promo_ends_at": _iso(_now() + timedelta(days=30)),
        "released_at": None, "release_reason": None, "over_capacity": False,
    })

    run(rs.run_due_renewals(db))
    assert renewals_on == [4990]


def test_the_context_split_day_91_renews_at_the_list_price(renewals_on):
    """The window is 90 wall-clock days from the claim, not a count of charges
    (§1.3). The first period that BEGINS after it is full price — on a saved
    card, which is exactly why §5 owes this user a heads-up email."""
    db = rt.FakeSupabase(rt.sub("pro"))
    rt.store_method(db)
    db.campaign_enabled = True                     # still open, still irrelevant
    db.early_adopter_seats.append({
        "seat_id": "s", "user_id": rt.USER, "payment_id": "p",
        "promo_ends_at": _iso(_now() - timedelta(days=1)),
        "released_at": None, "release_reason": None, "over_capacity": False,
    })

    run(rs.run_due_renewals(db))
    assert renewals_on == [8990]


def test_an_unknown_price_context_raises_rather_than_pricing(keys):
    """22023 in SQL. The return value IS the amount charged, so a context the
    function does not recognise must never be answered with a number."""
    db = open_campaign()
    with pytest.raises(RuntimeError, match="22023"):
        db.rpc("effective_plan_price",
               {"p_user_id": USER, "p_plan_id": "pro", "p_context": "nonsense"}).execute()


def test_an_unresolvable_price_falls_back_to_the_catalog(keys):
    """…and the CALL SITE degrades to the list price rather than inventing one.
    Same behaviour when 138 is not applied yet: the backend keeps selling at the
    catalog price instead of 503-ing every checkout."""
    db = open_campaign()
    seat(db)                                       # a member, who would get 49.90
    assert price_now(db, "pro", context="nonsense") == ps.q2("89.90")


# ═══════════════════════════════════════════════════════════════════════════
# 7. The upgrade credit — both directions of the same mistake
# ═══════════════════════════════════════════════════════════════════════════


def test_a_seat_holders_old_plan_is_credited_at_what_they_actually_pay(keys):
    """Crediting the catalog's 89.90 to someone who paid 49.90 refunds them
    value they never bought — the H-4 class of bug through a new door."""
    db = open_campaign(sub("pro", source="payment", days_left=26))
    seat(db)

    quote = checkout(db, "max")

    assert quote["credit_sar"] == "43.25"          # 26/30 × 49.90
    assert quote["amount_sar"] == "56.65"          # 99.90 − 43.25


def test_a_full_price_holders_old_plan_is_credited_at_the_list_price(keys):
    """The mirror. Context 'current' answers both by asking the only question
    that matters — what does this user's RUNNING term cost — instead of what
    anyone could buy it for today."""
    db = open_campaign(sub("pro", source="payment", days_left=26))   # no seat

    quote = checkout(db, "max")

    assert quote["credit_sar"] == "77.91"          # 26/30 × 89.90
    assert quote["amount_sar"] == "21.99"          # 99.90 − 77.91


def test_a_promo_priced_upgrade_still_grants_when_the_campaign_closes_mid_flight(keys, monkeypatch):
    """The fulfilment re-derivation prices at 'purchase' and would read the LIST
    price once the campaign closed — so it is capped at what we quoted. A price
    RISE between quote and settlement is never the customer's problem, and §3.5
    already decided this case in their favour."""
    db = open_campaign(sub("pro", source="payment", days_left=26))
    quote = checkout(db, "max")
    assert quote["amount_sar"] == "21.99"

    db.campaign_enabled = False                    # the campaign ends entirely
    result = pay(db, monkeypatch, quote["payment_id"], quote["amount_halalas"])

    assert result["granted"] is True
    assert result.get("review_reason") is None
    assert db.tables["user_subscriptions"][0]["plan_id"] == "max"


def test_an_underpaid_credited_upgrade_is_still_held(keys, monkeypatch):
    """REGRESSION WALL for the cap above. It must not have turned
    _revalidate_credited_charge into a rubber stamp: a credit that stopped being
    owed still holds the grant (H-4 layer 3)."""
    db = open_campaign(sub("pro", source="payment", days_left=26))
    quote = checkout(db, "max")
    db.tables["user_subscriptions"] = []           # the term was refunded away

    result = pay(db, monkeypatch, quote["payment_id"], quote["amount_halalas"])

    assert result["granted"] is False
    assert result["review_reason"] == "credit_no_longer_owed"


# ═══════════════════════════════════════════════════════════════════════════
# 8. Idempotency — the webhook and /verify both arrive
# ═══════════════════════════════════════════════════════════════════════════


def test_verify_and_the_webhook_together_claim_exactly_one_seat(keys, monkeypatch):
    """``early_adopter_seats.payment_id`` UNIQUE is the idempotency key, the
    same way ``fulfilled_at`` is grant_plan's. There is deliberately no
    Python-side "have they got one already?" guard: it would race precisely
    where the index does not."""
    db = open_campaign(sub("free"))
    quote = checkout(db, "pro")
    patch_fetch(monkeypatch, moyasar_payment(quote["payment_id"], amount=4990))

    run(ps.verify_payment(db, USER, MOYASAR_ID))
    first_window = live_seats(db, USER)[0]["promo_ends_at"]
    run(ps.handle_webhook_event(db, _event(quote["payment_id"])))

    assert len(db.early_adopter_seats) == 1
    assert live_seats(db, USER)[0]["promo_ends_at"] == first_window


def test_a_second_purchase_does_not_restart_the_ninety_days(keys, monkeypatch):
    """One LIVE seat per user (the partial unique index), so a re-purchase
    inside the window answers 'already_claimed'. Otherwise buying twice would
    quietly renew the promo forever."""
    db = open_campaign(sub("free"))
    buy(db, monkeypatch, "pro")
    first_window = live_seats(db, USER)[0]["promo_ends_at"]

    buy(db, monkeypatch, "pro")                    # stacks the days, same seat

    assert len(db.early_adopter_seats) == 1
    assert live_seats(db, USER)[0]["promo_ends_at"] == first_window


def test_a_failed_claim_never_fails_the_purchase(keys, monkeypatch):
    """The plan is granted and the money is in. A 500 here would be a customer
    who paid and saw an error — the exact posture clear_renewal_cancellation
    documents."""
    db = open_campaign(sub("free"))
    quote = checkout(db, "pro")
    original_rpc = db.rpc

    def exploding(name, params):
        if name == "claim_early_adopter_seat":
            raise RuntimeError("postgrest exploded")
        return original_rpc(name, params)

    monkeypatch.setattr(db, "rpc", exploding)
    result = pay(db, monkeypatch, quote["payment_id"], quote["amount_halalas"])

    assert result["granted"] is True
    assert db.tables["user_subscriptions"][0]["plan_id"] == "pro"


# ═══════════════════════════════════════════════════════════════════════════
# 9. The public endpoint leaks nothing
# ═══════════════════════════════════════════════════════════════════════════


def test_the_public_payload_has_exactly_two_keys_and_no_count(keys):
    """§1.10 — the remaining count never leaves the server. The key set is
    asserted EXACTLY so that a later "helpful" addition (seats_left,
    ends_at, total) fails here instead of shipping to an anonymous page."""
    db = open_campaign()
    payload = run(ps.early_adopter_campaign_state(db))

    assert set(payload) == {"open", "promo"}
    assert payload["open"] is True
    assert payload["promo"] == {"basic": "39.90", "pro": "49.90", "max": "99.90"}
    assert set(payload["promo"]) <= {"basic", "pro", "max"}
    # 2-dp strings, the house wire format for SAR (never floats).
    assert all(isinstance(v, str) and v[-3] == "." for v in payload["promo"].values())


def test_the_public_payload_says_nothing_when_the_campaign_is_over(keys):
    """"Closed" and "there was never a campaign" must be indistinguishable —
    no count, no total, no closing date, no explanation (§7)."""
    db = open_campaign(seat_limit=1)
    seat(db, user_id=OTHER)                        # the hundredth seat filled

    assert run(ps.early_adopter_campaign_state(db)) == {"open": False, "promo": {}}


def test_the_public_endpoint_answers_closed_rather_than_erroring(keys):
    """A marketing page must not break on a promotion. This is also the answer
    while 138 is unapplied."""
    db = open_campaign()

    def exploding(name, params):
        raise RuntimeError("function early_adopter_open() does not exist")

    db.rpc = exploding
    assert run(ps.early_adopter_campaign_state(db)) == {"open": False, "promo": {}}


def test_the_public_answer_is_cached(keys):
    """~30s server-side so an anonymous marketing page cannot turn a public
    endpoint into a query per visitor."""
    db = open_campaign()
    run(ps.early_adopter_campaign_state(db))
    calls_after_first = list(db.calls)
    run(ps.early_adopter_campaign_state(db))

    assert db.calls == calls_after_first


# ═══════════════════════════════════════════════════════════════════════════
# 10. basic — a discount for everybody, an enrolment for nobody
# ═══════════════════════════════════════════════════════════════════════════


def test_basic_is_discounted_for_anyone_while_seats_remain(keys):
    assert checkout(open_campaign(), "basic")["amount_sar"] == "39.90"


def test_basic_returns_to_the_list_price_the_moment_the_seats_are_gone(keys):
    """§1.9 — including for people who bought during the campaign. basic has no
    per-user promise to keep, because it has no seat."""
    db = open_campaign(seat_limit=1)
    seat(db, user_id=OTHER)
    assert checkout(db, "basic")["amount_sar"] == "49.90"


def test_buying_basic_enrols_nobody(keys, monkeypatch):
    """No seat, no window, no membership — a basic buyer is not one of
    المشتركون الأوائل and their renewal-less plan has nothing to reprice."""
    db = open_campaign()
    quote, result = buy(db, monkeypatch, "basic")

    assert quote["amount_sar"] == "39.90" and result["granted"] is True
    assert db.early_adopter_seats == []
    assert run(ss.get_subscription(db, USER))["early_adopter"]["is_member"] is False


def test_a_basic_refund_does_not_release_a_pro_seat(keys, monkeypatch):
    """The wrapper's plan guard. Without it, refunding a 39.90 basic purchase
    would destroy the promotional price on a live pro subscription."""
    db = open_campaign(sub("pro", source="payment", days_left=20))
    seat(db)
    row = paid_row(db, plan_id="basic", amount="39.90")

    async def _refund(ref, amount):
        return {"id": ref, "status": "refunded", "amount": amount}

    monkeypatch.setattr(ps, "refund_at_provider", _refund)
    run(ps.refund_payment(db, USER, row["payment_id"]))

    assert len(live_seats(db, USER)) == 1
    assert price_now(db, "pro", context=ps.PRICE_CONTEXT_CURRENT) == ps.q2("49.90")


def test_forfeiture_is_a_property_of_the_user_not_of_the_plan(keys):
    """⚠ MODEL-ONLY, and the one assumption in this file most worth checking
    against 138 itself: the forfeiture predicate is user-level, so a user who
    cancelled and let it stand is quoted the LIST price for `basic` too, even
    with seats open. If 138 scopes forfeiture to pro/max instead, this is the
    test that has to change — the fake cannot tell us which is true."""
    db = open_campaign()
    seat(db, released=True, reason="cancelled")

    assert checkout(db, "basic")["amount_sar"] == "49.90"


# ═══════════════════════════════════════════════════════════════════════════
# 11. The settings payload
# ═══════════════════════════════════════════════════════════════════════════


def test_the_subscription_payload_carries_membership_and_no_count(keys, monkeypatch):
    """What the cancel dialog renders its forfeiture warning from. Two fields,
    both about the caller; nothing about the campaign's capacity."""
    db = open_campaign(sub("free"))
    buy(db, monkeypatch, "pro")

    block = run(ss.get_subscription(db, USER))["early_adopter"]

    assert set(block) == {"is_member", "promo_ends_at"}
    assert block["is_member"] is True
    assert block["promo_ends_at"] == live_seats(db, USER)[0]["promo_ends_at"]


def test_an_unreadable_seat_table_reads_as_not_a_member(keys):
    """The settings dialog stands in front of the password and delete-account
    controls; a billing hiccup must never be what blocks them."""
    db = open_campaign(sub("pro", source="payment", days_left=20))
    seat(db)

    def exploding(name, params):
        raise RuntimeError("permission denied for function early_adopter_status")

    db.rpc = exploding
    assert run(ss.get_subscription(db, USER))["early_adopter"] == {
        "is_member": False, "promo_ends_at": None
    }
