import type { EarlyAdopterCampaign } from "@/types";
import { AR_NUM_LOCALE, toLatinDigits } from "@/lib/format/numerals";

/**
 * Pricing catalog for the public /pricing page. This is marketing copy — the
 * single source of truth for what is *displayed*. The matching enforcement
 * limits live in the `plans` DB table (migration 076); keep the two in sync by
 * hand when limits change.
 *
 * ⚠ THE DB IS AUTHORITATIVE FOR THE AMOUNT. `plans.price_sar` is what
 * `/payments/checkout` charges; the strings below are display only, and the two
 * drift silently if edited apart. They were repriced together to 49.90 / 89.90 /
 * 189.90 (VAT-inclusive) in the Moyasar Wave 1 commit — migration 113 and this
 * file must always move as one.
 *
 * Billing model (owner, 2026-08-10 — see .claude/plans/subscription_auto_renewal.md):
 *   - basic — a ONE-TIME 7-day purchase that ends without any further charge.
 *     Its card is the ONLY one that states a renewal position, and it states
 *     «بدون تجديد تلقائي», because a term that simply stops is the thing a buyer
 *     has to be told before paying.
 *   - pro / max — 30-day terms that AUTO-RENEW. That is the owner's model and it
 *     is what /terms §5.2 has promised publicly since 2026-08-10.
 *   - An early re-purchase of the same plan STACKS (extends the term) rather
 *     than resetting it.
 *
 * ⚠ WHY pro/max SAY NOTHING ABOUT RENEWAL HERE. The engine does not exist yet —
 * no card tokenization, no renewal job, `plans.billing_cycle` still `one_time`.
 * Silence is the only wording true both today and after Wave 2 ships:
 *   - «بدون تجديد تلقائي» (what these two cards used to say) contradicts the
 *     live terms — do NOT restore it;
 *   - «تجديد تلقائي» is equally wrong until the engine ships TOGETHER WITH an
 *     explicit pre-purchase recurring consent on /pay. Promising a renewal the
 *     scheduler cannot perform is the same class of bug pointed the other way.
 *
 * Usage points: the headline allowance shown is the WEEKLY points window, which
 * is anchored to the user's first message and runs 7 days (so for `basic` it
 * spans the whole 7-day subscription). The per-session (5h) cap is shown as a
 * secondary line. The monthly backstop is enforced but intentionally not shown
 * (and `basic` has no monthly cap at all). There is no internet-search feature.
 *
 * Numerals are written in Western (Latin) digits with a `.` decimal separator,
 * matching the rest of the product (`lib/format/numerals`) — the same shape
 * `formatSar` produces, so a hard-coded card price and a server-computed charge
 * on /pay read identically.
 *
 * ── المشتركون الأوائل (.claude/plans/early_adopters.md) ──────────────────────
 *
 * Each plan carries a `promoPrice` beside its list `price`, and a CAMPAIGN FLAG
 * — never a hard-coded date — chooses which one renders. The flag arrives from
 * `GET /payments/early-adopter`; absent, unreachable or malformed, every surface
 * falls back to the LIST price. Failing that direction is the only safe one: a
 * card promising 39.90 while checkout charges 49.90 is a mismatch discovered at
 * the moment of payment.
 *
 * ⚠ THE DB REMAINS AUTHORITATIVE FOR THE AMOUNT, exactly as it is for
 * `price_sar`. `plans.promo_price_sar` is what `effective_plan_price()` quotes
 * to checkout, to the renewal job and to the upgrade credit; the strings here
 * are display copy and drift silently if edited apart. Migration 138 and this
 * file must move as one — the same rule migration 113 already imposes on
 * `price`. The promo AMOUNT a card shows is in fact taken straight from the
 * campaign endpoint (`promoPriceFor()`); the hard-coded `promoPrice` is the
 * catalog mirror and is read only to rank the upgrade ladder.
 *
 * ⚠ NEVER RENDER A SEAT COUNT, A SEAT TOTAL, OR A CLOSING DATE. Not here, not on
 * a card, not in an error. The only permitted scarcity signal anywhere is
 * `SEATS_LIMITED_NOTE` («المقاعد محدودة») — an owner decision (plan §1.10), not
 * a stylistic one, and it holds even if the API ever grows a count field.
 *
 * ⚠ THE PROMO CHANGES THE PRICE, NOT THE BILLING MODEL. `promoBillingNote`
 * exists so pro/max can state the step-up honestly, and it still says NOTHING
 * about renewal for the reasons in the block above. `basic` deliberately has no
 * `promoBillingNote`: «بدون تجديد تلقائي · فترة الاشتراك 7 أيام فقط» is true
 * during the campaign and after it, and must never be copied onto pro/max.
 */
export interface PricingPlan {
  /** Matches plans.plan_id in the DB. */
  id: "basic" | "pro" | "max";
  nameAr: string;
  tagline: string;
  /** Price in SAR, Latin digits, with the `.` decimal separator. */
  price: string;
  /**
   * The المشتركون الأوائل price, same numeral convention — the catalog's mirror
   * of `plans.promo_price_sar`, kept in sync by hand exactly like `price`.
   *
   * ⚠ NOT WHAT GETS RENDERED. The amount on a card comes from the campaign
   * endpoint via `promoPriceFor()`; this value is read only for ORDERING, by
   * `cheapestPricingPlan()` and `pricingPlansAbove()`, which are handed the
   * boolean "is the campaign open" and never the server's price map. Trusting it
   * to rank is safe (the order is the same either way); trusting it to price
   * would advertise a discount the server might not honour.
   *
   * Undefined = this plan is not part of the campaign and never discounts.
   */
  promoPrice?: string;
  /** Billing cadence label shown next to the price. */
  period: "أسبوعياً" | "شهرياً";
  /**
   * Small muted line under the price. Carries the TERM always, and the renewal
   * position only on `basic` — see the ⚠ block in this file's header for why
   * pro/max are deliberately silent on renewal.
   */
  billingNote: string;
  /**
   * Replaces `billingNote` while the promo price is on screen. pro/max ONLY:
   * their price steps back up to the list price after 90 days and a buyer has to
   * be told that before paying, which is the same standard `basic`'s
   * «بدون تجديد تلقائي» meets.
   *
   * Still carries the term (the invariant `billingNote` owes the reader), still
   * asserts nothing about renewal, and deliberately does NOT repeat the list
   * number — `PlanPrice` already renders it struck through directly above, and
   * two copies of one price is one copy too many to keep in sync.
   *
   * `basic` has none on purpose: its discount is not time-boxed to 90 days (it
   * simply ends when seats run out), so its permanent note stays as it is.
   */
  promoBillingNote?: string;
  features: string[];
  /** The visually emphasised "most popular" card. */
  highlighted?: boolean;
}

export const PRICING_PLANS: PricingPlan[] = [
  {
    id: "basic",
    nameAr: "الأساسية",
    tagline: "للبدء والاستخدام الخفيف",
    price: "49.90",
    promoPrice: "39.90",
    period: "أسبوعياً",
    billingNote: "بدون تجديد تلقائي · فترة الاشتراك 7 أيام فقط",
    features: [
      "50 نقطة استخدام طوال الاشتراك (7 أيام)",
      "10 نقاط لكل جلسة (5 ساعات)",
      "15 صفحة استخراج نص",
    ],
  },
  {
    id: "pro",
    nameAr: "الاحترافية",
    tagline: "الأنسب للممارسة اليومية",
    price: "89.90",
    promoPrice: "49.90",
    period: "شهرياً",
    billingNote: "فترة الاشتراك 30 يوماً",
    promoBillingNote:
      "فترة الاشتراك 30 يوماً · سعر المشتركين الأوائل لأول 90 يوماً، ثم يعود إلى السعر المعتاد",
    highlighted: true,
    features: [
      "75 نقطة استخدام أسبوعياً",
      "15 نقطة لكل جلسة (5 ساعات)",
      "40 صفحة استخراج نص شهرياً",
    ],
  },
  {
    id: "max",
    nameAr: "القصوى",
    tagline: "أقصى سعة للقضايا المكثّفة",
    price: "189.90",
    promoPrice: "99.90",
    period: "شهرياً",
    billingNote: "فترة الاشتراك 30 يوماً",
    promoBillingNote:
      "فترة الاشتراك 30 يوماً · سعر المشتركين الأوائل لأول 90 يوماً، ثم يعود إلى السعر المعتاد",
    features: [
      "250 نقطة استخدام أسبوعياً",
      "50 نقطة لكل جلسة (5 ساعات)",
      "200 صفحة استخراج نص شهرياً",
    ],
  },
];

/** Look up a plan by its `plans.plan_id`. `undefined` for an unknown slug. */
export function findPricingPlan(id: string): PricingPlan | undefined {
  return PRICING_PLANS.find((plan) => plan.id === id);
}

/**
 * The cheapest purchasable plan — what «ابتداءً من …» quotes in the free-quota
 * upgrade dialog.
 *
 * Derived, never hard-coded: a repricing that reorders the catalog would
 * otherwise leave the dialog advertising a price no card shows. Compared on the
 * parsed Arabic-Indic string rather than a second numeric field, so there is
 * still exactly one place a price is written down.
 *
 * `campaignOpen` makes the comparison EFFECTIVE (promo where one exists), so
 * «ابتداءً من …» quotes what the cards beneath it actually show. Defaults to
 * `false` = list prices — the same fail-safe direction as every other campaign
 * read here, and byte-identical to the pre-campaign behaviour for any caller
 * that does not pass it.
 */
export function cheapestPricingPlan(campaignOpen = false): PricingPlan {
  return PRICING_PLANS.reduce((cheapest, plan) =>
    effectivePriceNumber(plan, campaignOpen) <
    effectivePriceNumber(cheapest, campaignOpen)
      ? plan
      : cheapest,
  );
}

/**
 * The upgrade ladder for a surface with NO blocking window to ask the server
 * about — Settings → الاشتراك, where a subscriber may upgrade before hitting a
 * wall. Strictly more expensive than `planId`, cheapest first.
 *
 * Display-only and deliberately price-based: it mirrors the server's downgrade
 * guard (`payment_service.PLAN_RANK`, and `plans.price_sar` in SQL), so it can
 * never offer something checkout would refuse. The blocked-send path does NOT
 * use this — there the ladder arrives on the wire (`upgrade_options`) already
 * filtered by the limit that actually blocked the user, which needs numbers
 * this file does not carry.
 *
 * A plan we cannot price — unknown slug, or a grant like `marketing_lawyer` /
 * `dev` that has no card here — yields an EMPTY list rather than the whole
 * catalog: we cannot prove any of these is an upgrade for them, and
 * `marketing_lawyer` (74 points weekly) would be shown `basic` (50) as a step
 * up. Failing quiet costs an upsell; failing loud sells a downgrade.
 *
 * `campaignOpen` switches the comparison to EFFECTIVE prices so the ladder is
 * ranked the way the cards are priced. It does NOT widen or narrow the ladder:
 * promo pricing preserves the catalog's rank (39.90 < 49.90 < 99.90, exactly as
 * 49.90 < 89.90 < 189.90), so the same plans are offered either way and nothing
 * the server's downgrade guard would refuse can appear. Assert that whenever a
 * promo amount changes — a promo that inverted the order would let this function
 * offer a downgrade.
 */
export function pricingPlansAbove(
  planId: string | null | undefined,
  campaignOpen = false,
): PricingPlan[] {
  const current = planId ? findPricingPlan(planId) : undefined;
  if (!current) return [];
  const floor = effectivePriceNumber(current, campaignOpen);
  return PRICING_PLANS.filter(
    (plan) => effectivePriceNumber(plan, campaignOpen) > floor,
  ).sort(
    (a, b) =>
      effectivePriceNumber(a, campaignOpen) -
      effectivePriceNumber(b, campaignOpen),
  );
}

/**
 * What this plan costs RIGHT NOW, as a number, for ordering only.
 *
 * Reads the hard-coded `promoPrice` rather than the campaign payload: the two
 * ranking helpers above are handed a boolean, not the server's price map, and
 * ranking only needs the relative order. The number a user SEES always comes
 * from `resolvePlanPricing()`, which reads the server's amount and nothing else.
 *
 * A promo that is missing, unparseable, or not actually cheaper falls back to
 * the list price — a "discount" above list must never reorder the ladder.
 */
function effectivePriceNumber(plan: PricingPlan, campaignOpen: boolean): number {
  const list = priceToNumber(plan.price);
  if (!campaignOpen || !plan.promoPrice) return list;
  const promo = priceToNumber(plan.promoPrice);
  return Number.isFinite(promo) && promo > 0 && promo < list ? promo : list;
}

/**
 * «49.90» → 49.9.
 *
 * The catalog strings above are Latin now, but this stays tolerant of the old
 * Arabic-Indic shape (digits AND the ٫ separator): the same helper parses
 * `promoPriceFor()` output, and a stray legacy string must rank correctly
 * rather than collapse to `NaN` and silently reorder the upgrade ladder.
 */
function priceToNumber(price: string): number {
  return Number(toLatinDigits(price).replace("٫", "."));
}

// -----------------------------------------------
// المشتركون الأوائل — the campaign surface
// (.claude/plans/early_adopters.md §6)
// -----------------------------------------------

/**
 * The campaign's name, and the ONLY scarcity signal that may ever appear.
 *
 * ⚠ Do not add «بقي N مقعداً», «100 مقعد», or a closing date next to these. The
 * remaining count is not disclosed anywhere — not on a page, not in the API, not
 * in an error message (plan §1.10). After the campaign closes a visitor simply
 * sees the list price, with no explanation that anything ended.
 */
export const EARLY_ADOPTER_LABEL = "المشتركون الأوائل";
export const SEATS_LIMITED_NOTE = "المقاعد محدودة";

/**
 * The link that must accompany every promotional price we show.
 *
 * The offer's real conditions — who qualifies, the 90 days, the step-up, and
 * that cancelling forfeits it permanently — live on `/promo-terms`, NOT in
 * `/terms`. They were moved out (owner, 2026-08-18) because they bind only the
 * users who take an offer, and folding two long clauses into §5 made the
 * subscription terms unreadable for everyone who never sees a promo.
 *
 * ⚠ That split only holds if the link actually travels with the price. A
 * discounted number shown with no route to its conditions is a worse
 * disclosure than the crowded §5 it replaced. Render `PROMO_TERMS_NOTE` as a
 * link to `LEGAL_ROUTES.promoTerms` on every surface that shows a promo price.
 */
export const PROMO_TERMS_NOTE = "تطبق أحكام العروض الترويجية";

/**
 * The answer every surface starts from and falls back to: campaign closed, list
 * prices. Frozen because it is shared by reference across renders and stores.
 */
export const EARLY_ADOPTER_CAMPAIGN_CLOSED: EarlyAdopterCampaign = Object.freeze({
  open: false,
  promo: {},
});

/**
 * ISR window for the server-rendered price surfaces, in seconds.
 *
 * ⚠ `/pricing` also declares `export const revalidate = 60` as a LITERAL, and
 * must: Next only accepts a statically analysable value there, so an imported
 * constant would be silently useless. Keep the two equal by hand — this one
 * bounds the `fetch` Data Cache entry, that one bounds the rendered page.
 */
export const EARLY_ADOPTER_REVALIDATE_SECONDS = 60;

/** Hard ceiling on the campaign probe. A hung backend must not hang a build. */
const CAMPAIGN_FETCH_TIMEOUT_MS = 4000;

/**
 * Coerce an unknown payload into a campaign answer, or into "closed".
 *
 * Shared by the server fetcher below and the client store so there is exactly
 * one parser: an `open` that is not literally `true` is closed, and a promo
 * entry that is not a finite positive number is dropped rather than rendered.
 * Anything unexpected therefore degrades to list prices instead of putting an
 * unparseable string where a price belongs.
 */
export function normalizeEarlyAdopterCampaign(
  raw: unknown,
): EarlyAdopterCampaign {
  if (!raw || typeof raw !== "object") return EARLY_ADOPTER_CAMPAIGN_CLOSED;
  const body = raw as { open?: unknown; promo?: unknown };
  if (body.open !== true) return EARLY_ADOPTER_CAMPAIGN_CLOSED;

  const promo: Record<string, string> = {};
  if (body.promo && typeof body.promo === "object") {
    for (const [planId, value] of Object.entries(
      body.promo as Record<string, unknown>,
    )) {
      if (typeof value !== "string" && typeof value !== "number") continue;
      const amount = Number(value);
      if (!Number.isFinite(amount) || amount <= 0) continue;
      promo[planId] = String(value);
    }
  }
  return { open: true, promo };
}

/**
 * Read the campaign state SERVER-SIDE for `/pricing` and the landing teaser.
 *
 * ⚠ Server components only. It lives here rather than beside either page so the
 * two surfaces share one definition (and one failure mode), and it resolves the
 * backend origin the way `lib/library/api.ts` does — `INTERNAL_API_URL` first,
 * so a rendered page reaches the backend over Railway's private network instead
 * of round-tripping through the edge. The client path is
 * `paymentsApi.getEarlyAdopter()` via `stores/early-adopter-store.ts`; never
 * call this from a `'use client'` module, where both env vars read `undefined`.
 *
 * `EDGE_SECRET` carries no `NEXT_PUBLIC_` prefix, so Next never inlines its
 * value into the browser bundle — the same guarantee documented at length in
 * `lib/library/api.ts`. Do not add the prefix, and do not log it.
 *
 * FAILS SAFE, ALWAYS: a non-OK status, a timeout, an unreachable backend (which
 * is what `npm run build` sees) or a malformed body all return "closed", i.e.
 * list prices. The opposite failure — advertising 39.90 while checkout charges
 * 49.90 — lands at the moment of payment and is the one this must never make.
 */
export async function fetchEarlyAdopterCampaign(): Promise<EarlyAdopterCampaign> {
  const base =
    process.env.INTERNAL_API_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";
  const edgeSecret = process.env.EDGE_SECRET;

  try {
    const res = await fetch(`${base}/api/v1/payments/early-adopter`, {
      next: { revalidate: EARLY_ADOPTER_REVALIDATE_SECONDS },
      signal: AbortSignal.timeout(CAMPAIGN_FETCH_TIMEOUT_MS),
      ...(edgeSecret ? { headers: { "X-Edge-Secret": edgeSecret } } : {}),
    });
    if (!res.ok) return EARLY_ADOPTER_CAMPAIGN_CLOSED;
    return normalizeEarlyAdopterCampaign(await res.json());
  } catch {
    return EARLY_ADOPTER_CAMPAIGN_CLOSED;
  }
}

/**
 * The promo price to DISPLAY for a plan, or `null` when none applies.
 *
 * Reads the campaign endpoint's amount — the number `effective_plan_price()`
 * will actually charge — and formats it through `formatSar`, so a wire value of
 * "49.9" always renders with the same two decimals as the catalog price and
 * cannot read as a different amount than the one being charged.
 *
 * ⚠ A PLAN MISSING FROM THE PAYLOAD GETS NO DISCOUNT, and deliberately does NOT
 * fall back to the hard-coded `promoPrice`. The two failures are not
 * symmetrical: falling back would advertise a discount the server may have
 * withdrawn for that plan (a mismatch discovered at the moment of payment),
 * while not falling back can only ever charge someone LESS than the card
 * promised. The ranking helpers do trust the hard-coded value, because an
 * ordering that agrees with itself costs nothing if it is wrong; a price does.
 */
export function promoPriceFor(
  plan: PricingPlan,
  campaign: EarlyAdopterCampaign | null | undefined,
): string | null {
  if (!campaign?.open) return null;
  const raw = campaign.promo?.[plan.id];
  if (raw === undefined || raw === null || raw === "") return null;
  const amount = Number(raw);
  if (!Number.isFinite(amount) || amount <= 0) return null;
  return formatSar(amount);
}

/** What one plan card renders once the campaign has been taken into account. */
export interface PlanPricingView {
  /** The headline price — the promo while it applies, else the list price. */
  price: string;
  /** The struck-through original, or `null` when nothing is discounted. */
  listPrice: string | null;
  /** `promoBillingNote` while discounted, else `billingNote`. */
  billingNote: string;
  /** True ⇒ this card may show the «المقاعد محدودة» note. */
  isPromo: boolean;
}

/**
 * One resolution shared by /pricing, the landing teaser and the quota dialog, so
 * the three cannot disagree about what a plan costs or why.
 *
 * A promo that is not strictly BELOW the list price is ignored: striking through
 * 89.90 to advertise 89.90 reads as a rendering bug, and striking it through to
 * advertise more reads as a scam. Either way the list price is the honest
 * answer, so that is what renders.
 */
export function resolvePlanPricing(
  plan: PricingPlan,
  campaign: EarlyAdopterCampaign | null | undefined,
): PlanPricingView {
  const promo = promoPriceFor(plan, campaign);
  const listNumber = priceToNumber(plan.price);
  const promoNumber = promo === null ? NaN : priceToNumber(promo);
  const discounted =
    promo !== null && Number.isFinite(promoNumber) && promoNumber < listNumber;

  if (!discounted || promo === null) {
    return {
      price: plan.price,
      listPrice: null,
      billingNote: plan.billingNote,
      isPromo: false,
    };
  }

  return {
    price: promo,
    listPrice: plan.price,
    billingNote: plan.promoBillingNote ?? plan.billingNote,
    isPromo: true,
  };
}

// -----------------------------------------------
// Phase E — the copy that must appear before purchase
// -----------------------------------------------

// «شامل الضريبة» REMOVED (owner, 2026-08-08): the business holds no VAT
// registration yet, and an unregistered business cannot claim to collect VAT.
// Prices display bare until registration; the server keeps stamping
// `vat_amount_sar` internally (never shown to the user). When a VAT number
// exists, reintroduce the note here AND revisit the receipt email, which
// deliberately carries no tax breakdown (backend/tests/test_receipts.py).

/**
 * Shown ONLY at the refund action (PaymentHistoryDialog — owner decision
 * 2026-08-04): out of context it reads as an anytime-refund promise.
 * «أول 24 ساعة من الاشتراك» anchors the window to the purchase, matching the
 * server's paid_at arithmetic. /terms carries the long-form version.
 */
export const REFUND_POLICY_NOTE =
  "استرداد خلال أول 24 ساعة من الاشتراك · تُخصم رسوم بوابة الدفع + 0.50 ريال";

/**
 * The trust claim, chosen over «لا نحفظ بطاقتك» — see the Phase E table in the
 * plan for what each half of this sentence rests on. It stays true in Wave 2
 * when cards are tokenized at Moyasar, which a no-storage promise would not.
 *
 * ⚠ DO NOT EXTEND THIS with «لا يُحفظ أي شيء عن بطاقتك» or «ريحان لا يحفظ أي
 * بيانات دفع». Both are false: Moyasar retains a payment record as a legal
 * financial record, and `payment_transactions` holds amount, VAT split, status
 * and dates. The mechanics belong in /privacy, where PDPL requires them.
 */
export const PAYMENT_TRUST_NOTE =
  "جميع العمليات المالية تتم عبر مُيسّر؛ بيانات بطاقتك لا تمرّ عبر خوادم ريحان.";

/**
 * The disclosed processing fee, in SAR — DISPLAY ONLY.
 *
 * The server owns the real value (`REFUND_FEE_SAR`) and stamps what it actually
 * charged onto the row. This copy exists solely so the confirmation dialog can
 * show the arithmetic *before* the user commits: someone who expects 49.90 back
 * and receives 47.90 files a complaint; someone who agreed to 47.90 does not.
 */
export const REFUND_FEE_SAR = 3.4; // FALLBACK ONLY (matches the server's REFUND_FEE_FALLBACK_HALALAS) — the real deduction is quoted per payment via refund_quote_fee_sar: provider fee + Moyasar's 1.15 refund fee + a 0.50 margin

/** How long after `paid_at` a self-serve refund stays available. */
export const REFUND_WINDOW_HOURS = 24;

// -----------------------------------------------
// Formatting
// -----------------------------------------------

/**
 * A SAR amount in Latin digits with a `.` separator and exactly two decimals —
 * «49.90». Uses the app-wide `AR_NUM_LOCALE`, so a server-computed charge and a
 * hard-coded card price render identically.
 *
 * Always two decimals, even for a whole number: a prorated upgrade charge of
 * «111.99» next to a credit of «78» looks like a rounding bug, and on a payment
 * screen a rounding bug looks like a wrong charge.
 */
export function formatSar(value: number | string): string {
  // The API ships SAR as 2-dp strings ("89.90"). Coerce BEFORE formatting:
  // String.prototype.toLocaleString ignores locale arguments entirely, so a
  // string slipping through would skip the two-decimal padding below and
  // render "89.9" on a payment screen.
  const n = typeof value === "string" ? Number(value) : value;
  return n.toLocaleString(AR_NUM_LOCALE, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Halalas → the display string. The wire unit is halalas everywhere. */
export function formatHalalas(halalas: number): string {
  return formatSar(halalas / 100);
}

/**
 * Same digits, but without forced decimals — «2», not «2.00».
 *
 * Used ONLY for the processing fee, so that the refund confirmation dialog
 * reads the same fee wording, character-for-character identical to the
 * `REFUND_POLICY_NOTE` the user was shown before they bought. A fee that was
 * disclosed as «2» and confirmed as «2.00» is the same number and a worse
 * disclosure — the reader has to stop and check that it is the same number,
 * which is exactly the moment of doubt this clause exists to prevent.
 *
 * Never use this for an AMOUNT: «49.9» beside «47.90» reads as a rounding bug.
 */
export function formatFeeSar(value: number | string): string {
  const n = typeof value === "string" ? Number(value) : value;
  return n.toLocaleString(AR_NUM_LOCALE, { maximumFractionDigits: 2 });
}

/**
 * Split a price string on the decimal separator so the fractional part can be
 * rendered smaller than the integer part.
 *
 * Purely a layout concern, and a real one: at `text-5xl`, «189.90» is materially
 * wider than «189» and reflows the three-card grid at the md breakpoint. Shared
 * by /pricing and the landing teaser so they can never diverge.
 */
export function splitPrice(price: string): {
  whole: string;
  fraction: string | null;
} {
  const at = price.indexOf(".");
  if (at < 0) return { whole: price, fraction: null };
  return { whole: price.slice(0, at), fraction: price.slice(at) };
}
