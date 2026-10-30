import assert from "node:assert/strict";
import { test } from "node:test";

import {
  PRICING_PLANS,
  deliberateUpgradePlans,
  pricingPlansAbove,
} from "../pricing";

/**
 * `deliberateUpgradePlans` — the ladder `QuotaUpgradeDialog` shows when it is
 * opened on purpose (no quota block, so no server ladder).
 *
 * The case this exists for is `WiPreviewWall` (wi_preview_lock.md §7): a FREE
 * user's «عرض الباقات». `"free"` has no catalog card, so `pricingPlansAbove`
 * answers `[]` — reusing it there would open an EMPTY plan picker on the one
 * surface built to sell a plan. Free sits below every paid plan, so the whole
 * catalog is the answer.
 *
 * Run:  cd frontend && npx tsx --test lib/__tests__/pricing-deliberate-upgrade.test.ts
 */

const ids = (plans: { id: string }[]) => plans.map((p) => p.id);

test("free → the whole catalog, in catalog order (never empty)", () => {
  const plans = deliberateUpgradePlans("free");
  assert.ok(plans.length > 0);
  assert.deepEqual(ids(plans), ids(PRICING_PLANS));
  // The old derivation that would have shipped an empty dialog.
  assert.deepEqual(pricingPlansAbove("free"), []);
});

test("a paid plan → exactly pricingPlansAbove (Settings unchanged)", () => {
  for (const plan of PRICING_PLANS) {
    assert.deepEqual(
      ids(deliberateUpgradePlans(plan.id)),
      ids(pricingPlansAbove(plan.id)),
    );
  }
  assert.deepEqual(ids(deliberateUpgradePlans("max")), []);
});

test("unknown / missing plan → [] (never sell a possible downgrade)", () => {
  for (const planId of [null, undefined, "", "dev", "marketing_lawyer"]) {
    assert.deepEqual(deliberateUpgradePlans(planId), []);
  }
});

test("plan card copy: Latin digits only", () => {
  for (const plan of PRICING_PLANS) {
    for (const line of [plan.nameAr, plan.tagline, plan.billingNote, ...plan.features]) {
      assert.doesNotMatch(line, /[٠-٩۰-۹]/, `${plan.id}: ${line}`);
    }
  }
});
