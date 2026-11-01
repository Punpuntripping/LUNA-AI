import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ONCE_PER_USER_NOTE,
  PRICING_PLANS,
  cheapestPricingPlan,
  resolvePlanPricing,
} from "../pricing";
import type { EarlyAdopterCampaign } from "../../types";

/**
 * Basic's one-time offer (migration 204) on the cards. It is a single purchase
 * that does not renew, so the card must never borrow Pro's «لأول شهر» wording,
 * and with the offer closed the card must be exactly the plain list-price card.
 *
 * Run:  cd frontend && npx tsx --test lib/__tests__/pricing-basic-offer.test.ts
 */

const basic = PRICING_PLANS.find((plan) => plan.id === "basic")!;
const OPEN = {
  open: true,
  promo: { basic: "9.80", pro: "59.90" },
  months: { basic: 1, pro: 2 },
};

test("offer open: 9.80, 39.90 struck through, single use and no refund stated", () => {
  const view = resolvePlanPricing(basic, OPEN);

  assert.equal(view.price, "9.80");
  assert.equal(view.listPrice, "39.90");
  assert.equal(view.isPromo, true);
  assert.ok(view.billingNote.includes(basic.billingNote));
  assert.ok(view.billingNote.includes(ONCE_PER_USER_NOTE));
});

test("offer open: no month wording, because nothing recurs", () => {
  const { billingNote } = resolvePlanPricing(basic, OPEN);

  assert.ok(!billingNote.includes("شهر"));
  assert.ok(!billingNote.includes("السعر المعتاد"));
});

test("offer closed or sold out: the plain list-price card", () => {
  const closed: (EarlyAdopterCampaign | null)[] = [
    null,
    { open: false, promo: {}, months: {} },
    { open: true, promo: { pro: "59.90" }, months: { pro: 2 } },
  ];
  for (const campaign of closed) {
    const view = resolvePlanPricing(basic, campaign);
    assert.equal(view.price, "39.90");
    assert.equal(view.listPrice, null);
    assert.equal(view.isPromo, false);
    assert.equal(view.billingNote, basic.billingNote);
  }
});

test("Pro keeps its step-up note and is untouched by the offer", () => {
  const pro = PRICING_PLANS.find((plan) => plan.id === "pro")!;
  const { billingNote } = resolvePlanPricing(pro, OPEN);

  assert.ok(billingNote.includes("ثم يعود إلى السعر المعتاد"));
  assert.ok(!billingNote.includes(ONCE_PER_USER_NOTE));
});

test("«ابتداءً من» quotes the offer while it sells", () => {
  assert.equal(cheapestPricingPlan(OPEN).id, "basic");
  assert.equal(resolvePlanPricing(cheapestPricingPlan(OPEN), OPEN).price, "9.80");
});
