import assert from "node:assert/strict";
import { test } from "node:test";

import {
  PRICING_PLANS,
  findPricingPlan,
  pricingPlansAbove,
  resolvePlanPricing,
} from "../pricing";
import {
  DOWNGRADE_NO_METHOD_DISMISS,
  DOWNGRADE_PRICE_PROMISE,
  cheapestDowngrade,
  downgradeBlockedReason,
  downgradeConfirmSentence,
  downgradeNoMethodNote,
  downgradePricePhrase,
  errorCodeOf,
  expectedRenewalPriceSar,
  isPriceChangedError,
  pendingDowngradeSentence,
  pendingPricePhrase,
  planChangeErrorMessage,
  readDowngradeOptions,
  retentionOfferSentence,
  scheduleDowngradeBody,
} from "../../components/Settings/plan-change-content";
import type { EarlyAdopterCampaign, SubscriptionState } from "../../types";

/**
 * Pro Plus + the Max price cut + the scheduled downgrade copy
 * (.claude/plans/pro_plus_tier_and_downgrade.md §0, §3.3, §4.1, §8).
 *
 * Run:  cd frontend && npx tsx --test lib/__tests__/pricing-pro-plus.test.ts
 */

const ids = (plans: { id: string }[]) => plans.map((p) => p.id);

/** The launch-day public payload: Pro 59.90×2, Pro Plus 99.90×2, Max 169.90×2. */
const LAUNCH: EarlyAdopterCampaign = {
  open: true,
  promo: { pro: "59.90", pro_plus: "99.90", max: "169.90" },
  months: { pro: 2, pro_plus: 2, max: 2 },
};

test("catalog: rank order basic < pro < pro_plus < max, Max at 219.90", () => {
  assert.deepEqual(ids(PRICING_PLANS), ["basic", "pro", "pro_plus", "max"]);
  assert.equal(findPricingPlan("max")?.price, "219.90");
  const proPlus = findPricingPlan("pro_plus");
  assert.equal(proPlus?.price, "149.90");
  assert.equal(proPlus?.nameAr, "الاحترافية بلس");
  assert.equal(proPlus?.period, "شهرياً");
  assert.equal(proPlus?.promoStepsUp, true);
  // Pro stays the one emphasised card.
  assert.deepEqual(
    PRICING_PLANS.filter((p) => p.highlighted).map((p) => p.id),
    ["pro"],
  );
});

test("upgrade ladder: the next plan above pro is pro_plus, above pro_plus is max", () => {
  assert.deepEqual(ids(pricingPlansAbove("basic")), ["pro", "pro_plus", "max"]);
  assert.deepEqual(ids(pricingPlansAbove("pro")), ["pro_plus", "max"]);
  assert.deepEqual(ids(pricingPlansAbove("pro_plus")), ["max"]);
  assert.deepEqual(ids(pricingPlansAbove("max")), []);
});

test("pro_plus promo flows through resolvePlanPricing like pro/max", () => {
  const proPlus = findPricingPlan("pro_plus")!;
  const view = resolvePlanPricing(proPlus, LAUNCH);
  assert.equal(view.price, "99.90");
  assert.equal(view.listPrice, "149.90");
  assert.equal(view.isPromo, true);
  assert.equal(view.promoMonths, 2);
  assert.match(view.billingNote, /سعر العرض لأول شهرين، ثم يعود إلى السعر المعتاد/);

  const max = resolvePlanPricing(findPricingPlan("max")!, LAUNCH);
  assert.equal(max.price, "169.90");
  assert.equal(max.listPrice, "219.90");

  // Missing from the payload ⇒ list price, no strike-through.
  const closed = resolvePlanPricing(proPlus, { open: true, promo: {}, months: {} });
  assert.equal(closed.price, "149.90");
  assert.equal(closed.listPrice, null);
});

function maxSubscription(extra: Partial<SubscriptionState> = {}): SubscriptionState {
  return {
    plan_id: "max",
    plan_name_ar: "القصوى",
    expires_at: "2026-12-20T10:00:00Z",
    source: "payment",
    cancellable: true,
    renewal_cancelled_at: null,
    ...extra,
  };
}

test("readDowngradeOptions: nearest first, malformed and self entries dropped", () => {
  const state = maxSubscription({
    downgrade_options: [
      { plan_id: "pro", name_ar: "الاحترافية", price_sar: "94.90", promo_price_sar: "59.90", promo_months: 2 },
      { plan_id: "pro_plus", name_ar: "الاحترافية بلس", price_sar: "149.90", promo_price_sar: null, promo_months: null },
      { plan_id: "max", name_ar: "القصوى", price_sar: "219.90", promo_price_sar: null, promo_months: null },
      { plan_id: "", name_ar: "x", price_sar: "1.00", promo_price_sar: null, promo_months: null },
      { plan_id: "weird", name_ar: "y", price_sar: "abc", promo_price_sar: null, promo_months: null },
    ],
  });
  const options = readDowngradeOptions(state);
  assert.deepEqual(options.map((o) => o.plan_id), ["pro_plus", "pro"]);
  assert.equal(cheapestDowngrade(options)?.plan_id, "pro");
  // Absent field (older backend) ⇒ nothing to offer.
  assert.deepEqual(readDowngradeOptions(maxSubscription()), []);
});

test("an offer not below the list price is ignored", () => {
  const [only] = readDowngradeOptions(
    maxSubscription({
      downgrade_options: [
        { plan_id: "pro", name_ar: "الاحترافية", price_sar: "94.90", promo_price_sar: "94.90", promo_months: 2 },
      ],
    }),
  );
  assert.equal(only.promo_price_sar, null);
  assert.equal(only.promo_months, null);
});

test("confirm + retention sentences: Latin digits, dual months, list after offer", () => {
  const [proPlus, pro] = readDowngradeOptions(
    maxSubscription({
      downgrade_options: [
        { plan_id: "pro_plus", name_ar: "الاحترافية بلس", price_sar: "149.90", promo_price_sar: null, promo_months: null },
        { plan_id: "pro", name_ar: "الاحترافية", price_sar: "94.90", promo_price_sar: "59.90", promo_months: 2 },
      ],
    }),
  );
  assert.equal(downgradePricePhrase(proPlus), "149.90 ريال شهرياً");
  assert.equal(
    downgradePricePhrase(pro),
    "59.90 ريال لأول شهرين، ثم 94.90 ريال شهرياً",
  );
  assert.equal(
    downgradeConfirmSentence("القصوى", "20 ديسمبر 2026", proPlus),
    "ستبقى على باقة القصوى حتى 20 ديسمبر 2026، ثم تنتقل إلى باقة الاحترافية بلس بسعر 149.90 ريال شهرياً.",
  );
  assert.equal(
    retentionOfferSentence(pro),
    "بدلاً من الإلغاء، انتقل إلى باقة الاحترافية بسعر 59.90 ريال لأول شهرين، ثم 94.90 ريال شهرياً.",
  );
  for (const line of [
    downgradePricePhrase(pro),
    downgradeConfirmSentence("القصوى", null, pro),
    retentionOfferSentence(proPlus),
  ]) {
    assert.doesNotMatch(line, /[٠-٩۰-۹]/);
  }
});

test("pending banner: price from the payload, list from options or catalog", () => {
  const state = maxSubscription({
    pending_plan_id: "pro",
    pending_plan_name_ar: "الاحترافية",
    pending_price_sar: "59.90",
    pending_promo_months: 2,
  });
  // No downgrade_options entry for it: the catalog card supplies the list.
  const phrase = pendingPricePhrase(state, []);
  assert.equal(phrase, "59.90 ريال لأول شهرين، ثم 94.90 ريال شهرياً");
  assert.equal(
    pendingDowngradeSentence("الاحترافية", "20 ديسمبر 2026", phrase),
    "سيتم تحويل باقتك إلى «الاحترافية» عند التجديد في 20 ديسمبر 2026، بسعر 59.90 ريال لأول شهرين، ثم 94.90 ريال شهرياً.",
  );
  // List price, no offer.
  assert.equal(
    pendingPricePhrase(
      maxSubscription({ pending_plan_id: "pro_plus", pending_price_sar: "149.90" }),
      [],
    ),
    "149.90 ريال شهرياً",
  );
  // The promise is never null while pending; if a payload ever lacks it the
  // banner keeps the switch sentence and simply names no price (no lapse
  // claim, no guessed number).
  const missing = pendingPricePhrase(
    maxSubscription({ pending_plan_id: "pro_plus" }),
    [],
  );
  assert.equal(missing, null);
  assert.equal(
    pendingDowngradeSentence("الاحترافية بلس", "20 ديسمبر 2026", missing),
    "سيتم تحويل باقتك إلى «الاحترافية بلس» عند التجديد في 20 ديسمبر 2026.",
  );
});

test("the promise line (owner rule 2026-11-20)", () => {
  assert.equal(
    DOWNGRADE_PRICE_PROMISE,
    "هذا السعر محفوظ لك عند التجديد، وإن كان السعر يومئذٍ أقل دفعت الأقل.",
  );
});

test("errors: only Arabic server text reaches the UI", () => {
  const fallback = "تعذّر تغيير الباقة. حاول مجددًا.";
  assert.equal(
    planChangeErrorMessage(new Error("لا يمكن التحويل إلى هذه الباقة."), fallback),
    "لا يمكن التحويل إلى هذه الباقة.",
  );
  assert.equal(planChangeErrorMessage(new TypeError("Failed to fetch"), fallback), fallback);
  assert.equal(planChangeErrorMessage(null, fallback), fallback);
});

test("expected_price_sar: the exact wire string the confirm sheet led with", () => {
  const [proPlus, pro] = readDowngradeOptions(
    maxSubscription({
      downgrade_options: [
        { plan_id: "pro_plus", name_ar: "الاحترافية بلس", price_sar: "149.90", promo_price_sar: null, promo_months: null },
        { plan_id: "pro", name_ar: "الاحترافية", price_sar: "94.90", promo_price_sar: "59.90", promo_months: 2 },
      ],
    }),
  );
  // Kept as sent: «94.90», never «94.9».
  assert.equal(pro.price_sar, "94.90");
  assert.equal(expectedRenewalPriceSar(pro), "59.90");
  assert.equal(expectedRenewalPriceSar(proPlus), "149.90");
  // An offer the sheet does not show (not below list) is not what is sent.
  const [noOffer] = readDowngradeOptions(
    maxSubscription({
      downgrade_options: [
        { plan_id: "pro", name_ar: "الاحترافية", price_sar: "94.90", promo_price_sar: "99.90", promo_months: 2 },
      ],
    }),
  );
  assert.equal(expectedRenewalPriceSar(noOffer), "94.90");
  // A bare number on the wire (not the documented shape) gets two decimals.
  const [numeric] = readDowngradeOptions(
    maxSubscription({
      downgrade_options: [
        { plan_id: "pro", name_ar: "الاحترافية", price_sar: 94.9 as unknown as string, promo_price_sar: null, promo_months: null },
      ],
    }),
  );
  assert.equal(expectedRenewalPriceSar(numeric), "94.90");
});

test("downgrade_blocked_reason: only no_payment_method blocks", () => {
  assert.equal(
    downgradeBlockedReason(maxSubscription({ downgrade_blocked_reason: "no_payment_method" })),
    "no_payment_method",
  );
  assert.equal(downgradeBlockedReason(maxSubscription({ downgrade_blocked_reason: null })), null);
  assert.equal(downgradeBlockedReason(maxSubscription()), null);
  assert.equal(
    downgradeBlockedReason(
      maxSubscription({ downgrade_blocked_reason: "something_new" as unknown as "no_payment_method" }),
    ),
    null,
  );
  assert.equal(downgradeBlockedReason(null), null);
});

test("no stored card: the truthful note, end date when known, one «حسناً»", () => {
  // Without a date: the owner's sentence verbatim.
  assert.equal(
    downgradeNoMethodNote(null),
    "لا توجد بطاقة محفوظة لتجديد اشتراكك تلقائياً، لذلك لن يُجدَّد عند انتهاء مدته. يمكنك الاشتراك في الباقة الأقل بعد انتهاء اشتراكك الحالي.",
  );
  // With the formatted end date (Latin digits).
  const dated = downgradeNoMethodNote("20 ديسمبر 2026");
  assert.equal(
    dated,
    "لا توجد بطاقة محفوظة لتجديد اشتراكك تلقائياً، لذلك لن يُجدَّد عند انتهاء مدته في 20 ديسمبر 2026. يمكنك الاشتراك في الباقة الأقل بعد انتهاء اشتراكك الحالي.",
  );
  assert.doesNotMatch(dated, /[٠-٩۰-۹]/);
  // Never the retired add-a-card wording: there is no add-card path.
  assert.doesNotMatch(dated, /أضف بطاقة/);
  assert.equal(DOWNGRADE_NO_METHOD_DISMISS, "حسناً");
});

test("error codes: price change is recognised, its Arabic text is shown", () => {
  const changed = Object.assign(new Error("تغيّر سعر الباقة، راجع السعر الجديد."), {
    code: "SUBSCRIPTION_DOWNGRADE_PRICE_CHANGED",
  });
  assert.equal(errorCodeOf(changed), "SUBSCRIPTION_DOWNGRADE_PRICE_CHANGED");
  assert.equal(isPriceChangedError(changed), true);
  assert.equal(planChangeErrorMessage(changed, "x"), "تغيّر سعر الباقة، راجع السعر الجديد.");
  const noMethod = Object.assign(new Error("أضف بطاقة"), { code: "SUBSCRIPTION_DOWNGRADE_NO_METHOD" });
  assert.equal(isPriceChangedError(noMethod), false);
  assert.equal(errorCodeOf(new TypeError("Failed to fetch")), null);
  assert.equal(errorCodeOf(null), null);
});

test("POST body: {plan_id, expected_price_sar, expected_promo_months} as shown", () => {
  const [proPlus, pro] = readDowngradeOptions(
    maxSubscription({
      downgrade_options: [
        { plan_id: "pro_plus", name_ar: "الاحترافية بلس", price_sar: "149.90", promo_price_sar: null, promo_months: null },
        { plan_id: "pro", name_ar: "الاحترافية", price_sar: "94.90", promo_price_sar: "59.90", promo_months: 2 },
      ],
    }),
  );
  // Offer shown → its price string and its months.
  assert.deepEqual(scheduleDowngradeBody(pro), {
    plan_id: "pro",
    expected_price_sar: "59.90",
    expected_promo_months: 2,
  });
  // List price shown → no months.
  assert.deepEqual(scheduleDowngradeBody(proPlus), {
    plan_id: "pro_plus",
    expected_price_sar: "149.90",
    expected_promo_months: null,
  });
  // An offer the sheet does not show (not below list) sends neither its price
  // nor its months.
  const [hidden] = readDowngradeOptions(
    maxSubscription({
      downgrade_options: [
        { plan_id: "pro", name_ar: "الاحترافية", price_sar: "94.90", promo_price_sar: "99.90", promo_months: 2 },
      ],
    }),
  );
  assert.deepEqual(scheduleDowngradeBody(hidden), {
    plan_id: "pro",
    expected_price_sar: "94.90",
    expected_promo_months: null,
  });
  // Exactly these three keys, nothing else on the wire.
  assert.deepEqual(Object.keys(scheduleDowngradeBody(pro)).sort(), [
    "expected_price_sar",
    "expected_promo_months",
    "plan_id",
  ]);
});
