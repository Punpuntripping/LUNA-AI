import type { EarlyAdopterCampaign } from "@/types";
import { AR_NUM_LOCALE, toLatinDigits } from "@/lib/format/numerals";

/**
 * Pricing catalog for the public /pricing page. This is marketing copy — the
 * single source of truth for what is *displayed*. The matching enforcement
 * limits live in the `plans` DB table (migrations 076 + 147 + 187 + 188); keep the two in
 * sync by hand when limits change.
 *
 * ⚠ THE DB IS AUTHORITATIVE FOR THE AMOUNT. `plans.price_sar` is what
 * `/payments/checkout` charges; the strings below are display only, and the two
 * drift silently if edited apart. They were repriced together to 49.90 / 89.90 /
 * 189.90 (VAT-inclusive) in the Moyasar Wave 1 commit, and `max` was raised to
 * 289.90 by migration 147 (2026-08-29), `pro` to 94.90 by migration 168
 * (2026-09-29), and `basic` to a flat 29.90 with no promo by migration 174
 * (2026-10-08), then to a flat 39.90 by migration 191 (2026-10-27) — a price
 * migration and this file must always move as one. These are the LIST prices
 * only; no promotional amount is written down in this file any more (see the
 * المشتركون الأوائل block below).
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
 * ── المشتركون الأوائل — price tiers + seat batches ───────────────────────────
 * (.claude/plans/pricing_campaigns_v2.md, superseding early_adopters.md §6)
 *
 * The promotional price AND the number of months it lasts both come from
 * `GET /payments/early-adopter` (`promo` + `months`), never from a constant in
 * this file. Seats are sold in batches that live in a DB table; when one fills
 * the next opens on its own — possibly at a different price or for a different
 * number of months — with no deploy. A hard-coded promo would therefore be
 * wrong the minute a batch rolls over. Absent, unreachable or malformed, every
 * surface falls back to the LIST price. Failing that direction is the only safe
 * one: a card promising 59.90 while checkout charges 94.90 is a mismatch
 * discovered at the moment of payment.
 *
 * ⚠ THE DB REMAINS AUTHORITATIVE FOR THE AMOUNT. `promo_quote()` /
 * `effective_plan_price()` decide what checkout, the renewal job and the
 * upgrade credit charge; this file only displays what the endpoint reports.
 * The public payload carries the GENERAL quote (e.g. Pro at its tier-2 price);
 * the personal profession offer arrives separately on `/payments/intro-offer`
 * and is rendered by `components/promo/`.
 *
 * ⚠ NEVER RENDER A SEAT COUNT, A BATCH, A TIER NUMBER OR A CLOSING DATE. Not
 * here, not on a card, not in an error. The only permitted scarcity signal
 * anywhere is `SEATS_LIMITED_NOTE` («المقاعد محدودة») — an owner decision (plan
 * §1.10), not a stylistic one, and it holds even if the API ever grows a count.
 *
 * ⚠ THE PROMO CHANGES THE PRICE, NOT THE BILLING MODEL. `promoBillingNoteFor()`
 * exists so pro/max can state the step-up honestly («لأول شهرين، ثم يعود إلى
 * السعر المعتاد»), and it still says NOTHING about renewal for the reasons in
 * the block above. `basic` is never in a tier: «ادفع مرة واحدة — بدون تجديد
 * تلقائي» is always true for it, and must never be copied onto pro/max.
 */
export interface PricingPlan {
  /** Matches plans.plan_id in the DB. */
  id: "basic" | "pro" | "max";
  nameAr: string;
  tagline: string;
  /**
   * The LIST price in SAR, Latin digits, with the `.` decimal separator. Also
   * the fallback whenever no promotional price applies. A promo amount is never
   * written here — it comes from the campaign endpoint (`promoPriceFor()`).
   */
  price: string;
  /** Billing cadence label shown next to the price. */
  period: "أسبوعياً" | "شهرياً";
  /**
   * Small muted line under the price. Carries the TERM always, and the renewal
   * position only on `basic` — see the ⚠ block in this file's header for why
   * pro/max are deliberately silent on renewal.
   */
  billingNote: string;
  /**
   * True ⇒ a promotional price on this plan is TIME-BOXED: it lasts the number
   * of months the campaign endpoint states and then steps back up to `price`,
   * and a buyer has to be told that before paying — the same standard `basic`'s
   * «بدون تجديد تلقائي» meets. While the promo is on screen the card's note is
   * built by `promoBillingNoteFor()` from the server's month count.
   *
   * pro/max ONLY. `basic` is never in a price tier, so its permanent note stays
   * exactly as it is even if a promo were ever reported for it.
   */
  promoStepsUp?: boolean;
  features: string[];
  /** The visually emphasised "most popular" card. */
  highlighted?: boolean;
}

export const PRICING_PLANS: PricingPlan[] = [
  {
    id: "basic",
    nameAr: "الأساسية",
    tagline: "للبدء والاستخدام الخفيف",
    // Flat 39.90, no promo (migration 191, 2026-10-27; was 29.90 since 174):
    // nothing is struck through and no «المقاعد محدودة» on this card.
    price: "39.90",
    period: "أسبوعياً",
    billingNote: "ادفع مرة واحدة — بدون تجديد تلقائي",
    features: [
      "75 نقطة استخدام طوال الاشتراك (7 أيام)",
      "15 نقطة لكل جلسة (5 ساعات)",
      "15 صفحة استخراج نص",
      "تقارير الباحث ومسودات الكاتب كاملة مع مشاركتها وتصديرها",
      "الوصول الكامل للمكتبة القانونية",
    ],
  },
  {
    id: "pro",
    nameAr: "الاحترافية",
    tagline: "الأنسب للممارسة اليومية",
    // List price. The promo (tier 2 on the public page) and its months come
    // from `/payments/early-adopter` — never write them here.
    price: "94.90",
    period: "شهرياً",
    billingNote: "فترة الاشتراك 30 يوماً",
    promoStepsUp: true,
    highlighted: true,
    features: [
      "100 نقطة استخدام أسبوعياً",
      "20 نقطة لكل جلسة (5 ساعات)",
      "40 صفحة استخراج نص شهرياً",
      "تقارير الباحث ومسودات الكاتب كاملة مع مشاركتها وتصديرها",
      "الوصول الكامل للمكتبة القانونية",
    ],
  },
  {
    id: "max",
    nameAr: "القصوى",
    tagline: "أقصى سعة للقضايا المكثّفة",
    // List price. Same rule as pro: promo + months come from the endpoint.
    price: "289.90",
    period: "شهرياً",
    billingNote: "فترة الاشتراك 30 يوماً",
    promoStepsUp: true,
    features: [
      "500 نقطة استخدام أسبوعياً",
      "100 نقطة لكل جلسة (5 ساعات)",
      "500 صفحة استخراج نص شهرياً",
      "حتى 5 محادثات متزامنة",
      "تقارير الباحث ومسودات الكاتب كاملة مع مشاركتها وتصديرها",
      "الوصول الكامل للمكتبة القانونية",
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
 * parsed price string rather than a second numeric field, so there is still
 * exactly one place a price is written down.
 *
 * `campaign` makes the comparison the DISPLAYED price — the same
 * `resolvePlanPricing()` answer each card renders, i.e. the server's promo
 * where one applies — so «ابتداءً من …» quotes what the cards beneath it
 * actually show, batch rollovers included. Omitted / closed = list prices — the
 * same fail-safe direction as every other campaign read here.
 */
export function cheapestPricingPlan(
  campaign?: EarlyAdopterCampaign | null,
): PricingPlan {
  return PRICING_PLANS.reduce((cheapest, plan) =>
    displayedPriceNumber(plan, campaign) <
    displayedPriceNumber(cheapest, campaign)
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
 * Ranked on LIST prices, deliberately, and no longer on promo prices. Promo
 * amounts are now batch DATA that change by a DB `UPDATE` with no deploy
 * (pricing_campaigns_v2 §1.4-2), so no frontend review can assert they keep the
 * catalog's order any more — and a promo that inverted it (say a Max batch
 * priced under the Pro list price) would let a price-ranked ladder offer a Max
 * subscriber a "step up" to Pro, which the server's downgrade guard refuses at
 * checkout. The list order is the plan rank itself, so it can never drift.
 *
 * The cards still render in the same order today: the live tiers preserve the
 * catalog's rank — basic 39.90 < pro 59.90 (tier 2) < pro 94.90, and max
 * 149.90 / 189.90 < 289.90, every max price above every pro price. The personal
 * Pro tier-1 price (19.90) sits below basic, but it is never on the public
 * payload and never enters this ranking (`components/promo/` renders it).
 */
export function pricingPlansAbove(
  planId: string | null | undefined,
): PricingPlan[] {
  const current = planId ? findPricingPlan(planId) : undefined;
  if (!current) return [];
  const floor = priceToNumber(current.price);
  return PRICING_PLANS.filter((plan) => priceToNumber(plan.price) > floor).sort(
    (a, b) => priceToNumber(a.price) - priceToNumber(b.price),
  );
}

/**
 * The ladder a DELIBERATE opening of the plan picker offers — no blocking
 * window, so no server ladder (`QuotaUpgradeDialog` without `info`).
 *
 * `pricingPlansAbove`, except for `"free"`: it has no card to rank against (so
 * `pricingPlansAbove` answers `[]`), yet it sits below every paid plan — the
 * server's `PLAN_RANK` holds paid plans only and lets a free user buy any of
 * them. The whole catalog is therefore a real step up, exactly the pre-ladder
 * free paywall. Surfaces: Settings → الاشتراك (a subscriber's own plan) and
 * `WiPreviewWall` (a free user's preview-locked item, wi_preview_lock.md §7).
 */
export function deliberateUpgradePlans(
  planId: string | null | undefined,
): PricingPlan[] {
  return planId === "free" ? PRICING_PLANS : pricingPlansAbove(planId);
}

/**
 * The number a plan's card shows RIGHT NOW — the server's promo where one
 * applies, else the list price — via the same `resolvePlanPricing()` call the
 * card itself makes, so a comparison can never disagree with what is drawn.
 */
function displayedPriceNumber(
  plan: PricingPlan,
  campaign: EarlyAdopterCampaign | null | undefined,
): number {
  return priceToNumber(resolvePlanPricing(plan, campaign).price);
}

/**
 * «49.90» → 49.9.
 *
 * The catalog strings above are Latin now, but this stays tolerant of the old
 * Arabic-Indic shape (digits AND the ٫ separator): the same helper parses
 * `promoPriceFor()` output, and a stray legacy string must compare correctly
 * rather than collapse to `NaN`.
 */
function priceToNumber(price: string): number {
  return Number(toLatinDigits(price).replace("٫", "."));
}

// -----------------------------------------------
// المشتركون الأوائل — the campaign surface
// (.claude/plans/pricing_campaigns_v2.md §4, early_adopters.md §6)
// -----------------------------------------------

/**
 * The campaign's name, and the ONLY scarcity signal that may ever appear.
 *
 * ⚠ Do not add «بقي N مقعداً», «100 مقعد», a batch, a tier number or a closing
 * date next to these. None of them is disclosed anywhere — not on a page, not in
 * the API, not in an error message (plan §1.10). When a batch fills and the next
 * opens, or the campaign closes, a visitor simply sees the new price (or the
 * list price), with no explanation that anything changed.
 */
export const EARLY_ADOPTER_LABEL = "المشتركون الأوائل";
export const SEATS_LIMITED_NOTE = "المقاعد محدودة";

/**
 * The link that must accompany every promotional price we show.
 *
 * The offer's real conditions — who qualifies for which price level, how many
 * months each price lasts, the step-up, and that cancelling loses the seat —
 * live on `/promo-terms`, NOT in `/terms`. They were moved out (owner,
 * 2026-08-18) because they bind only the users who take an offer, and folding
 * long clauses into §5 made the subscription terms unreadable for everyone who
 * never sees a promo.
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
  months: {},
});

/**
 * The batch `months` column is CHECKed 1..12 (migration 192); anything outside
 * that, or not a whole number, is not a month count we can word truthfully.
 */
const PROMO_MONTHS_MIN = 1;
const PROMO_MONTHS_MAX = 12;

/**
 * A wire month count → a whole number in 1..12, or `null`.
 *
 * Shared by the campaign normaliser, the intro-offer parser and /pay's checkout
 * `promo` block, so a malformed count degrades identically everywhere — to a
 * months-free wording, never to «لأول NaN أشهر».
 */
export function parsePromoMonths(value: unknown): number | null {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const n = Number(value);
  return Number.isInteger(n) && n >= PROMO_MONTHS_MIN && n <= PROMO_MONTHS_MAX
    ? n
    : null;
}

/**
 * «لأول شهر» · «لأول شهرين» · «لأول 3 أشهر» … «لأول 10 أشهر» · «لأول 11 شهراً».
 *
 * Arabic number agreement for a count of months, Latin digits (the product's
 * numerals policy): the dual for 2, the plural «أشهر» for 3–10, the accusative
 * singular «شهراً» from 11. The intro-offer copy words a single month as
 * «للشهر الأول» instead — see `components/promo/intro-offer-content.ts`.
 */
export function promoMonthsPhrase(months: number): string {
  if (months === 1) return "لأول شهر";
  if (months === 2) return "لأول شهرين";
  if (months >= 3 && months <= 10) return `لأول ${months} أشهر`;
  return `لأول ${months} شهراً`;
}

/**
 * The card note while a promo price is on screen: the term (the invariant
 * `billingNote` owes the reader), then how long the promo lasts and that the
 * price steps back up — «فترة الاشتراك 30 يوماً · سعر العرض لأول شهرين، ثم يعود
 * إلى السعر المعتاد». Asserts nothing about renewal, and deliberately does NOT
 * repeat the list number: `PlanPrice` already renders it struck through
 * directly above.
 *
 * `months` null (a backend from before batches, which sends no `months`) words
 * the duration without a number rather than guessing one. A plan that is not
 * `promoStepsUp` (basic) keeps its own note unchanged.
 */
export function promoBillingNoteFor(
  plan: PricingPlan,
  months: number | null,
): string {
  if (!plan.promoStepsUp) return plan.billingNote;
  const span = months === null ? "لمدة محددة" : promoMonthsPhrase(months);
  return `${plan.billingNote} · سعر العرض ${span}، ثم يعود إلى السعر المعتاد`;
}

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
 * one parser: an `open` that is not literally `true` is closed, a promo entry
 * that is not a finite positive number is dropped rather than rendered, and a
 * month count outside 1..12 is dropped (the card then words the duration
 * without a number). Anything unexpected therefore degrades to list prices
 * instead of putting an unparseable string where a price belongs.
 *
 * `months` is ADDITIVE (pricing_campaigns_v2 §3): a backend from before batches
 * sends none, and that reads as "duration unknown", never as an error.
 */
export function normalizeEarlyAdopterCampaign(
  raw: unknown,
): EarlyAdopterCampaign {
  if (!raw || typeof raw !== "object") return EARLY_ADOPTER_CAMPAIGN_CLOSED;
  const body = raw as { open?: unknown; promo?: unknown; months?: unknown };
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

  const months: Record<string, number> = {};
  if (body.months && typeof body.months === "object") {
    for (const [planId, value] of Object.entries(
      body.months as Record<string, unknown>,
    )) {
      const n = parsePromoMonths(value);
      if (n !== null) months[planId] = n;
    }
  }
  return { open: true, promo, months };
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
 * list prices. The opposite failure — advertising 59.90 while checkout charges
 * 94.90 — lands at the moment of payment and is the one this must never make.
 *
 * ⚠ BATCH ROLLOVER. A batch filling changes the promo price and/or months with
 * no deploy, so every caller must stay on a short ISR window: the Data Cache
 * entry below lives `EARLY_ADOPTER_REVALIDATE_SECONDS` (60 s), `/pricing`
 * declares `revalidate = 60`, and `/` + `/about_us` inherit 60 s from this fetch
 * (Next takes the lowest `revalidate` of a route's fetches). Never raise it, and
 * never add a `force-cache` / `revalidate: false` caller.
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
 * ⚠ A PLAN MISSING FROM THE PAYLOAD GETS NO DISCOUNT — no strike-through and no
 * «المقاعد محدودة» — and there is no hard-coded promo to fall back to (there
 * used to be; batches made any constant wrong on the next rollover). The two
 * failures are not symmetrical: showing a discount the server has withdrawn is
 * a mismatch discovered at the moment of payment, while showing the list price
 * can only ever charge someone LESS than the card promised.
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

/**
 * How many months the promo price lasts for a plan, per the campaign endpoint,
 * or `null` when no count was reported (an older backend) or no promo applies.
 */
export function promoMonthsFor(
  plan: PricingPlan,
  campaign: EarlyAdopterCampaign | null | undefined,
): number | null {
  if (!campaign?.open) return null;
  return parsePromoMonths(campaign.months?.[plan.id]);
}

/** What one plan card renders once the campaign has been taken into account. */
export interface PlanPricingView {
  /** The headline price — the promo while it applies, else the list price. */
  price: string;
  /** The struck-through original, or `null` when nothing is discounted. */
  listPrice: string | null;
  /** `promoBillingNoteFor()` while discounted, else `billingNote`. */
  billingNote: string;
  /** True ⇒ this card may show the «المقاعد محدودة» note. */
  isPromo: boolean;
  /** Months the promo lasts (server's count); `null` when not discounted or unknown. */
  promoMonths: number | null;
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
      promoMonths: null,
    };
  }

  const promoMonths = promoMonthsFor(plan, campaign);
  return {
    price: promo,
    listPrice: plan.price,
    billingNote: promoBillingNoteFor(plan, promoMonths),
    isPromo: true,
    promoMonths,
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
 * Purely a layout concern, and a real one: at `text-5xl`, «289.90» is materially
 * wider than «289» and reflows the three-card grid at the md breakpoint. Shared
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
