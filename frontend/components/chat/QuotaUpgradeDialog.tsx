"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Check, ShieldCheck, Sparkles, Zap } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PlanPrice } from "@/components/pricing/PlanPrice";
import { PlanCheckoutCta } from "@/components/pricing/PlanCheckoutCta";
import { PromoTermsLink } from "@/components/pricing/PromoTermsLink";
import { RiyalSymbol } from "@/components/icons/RiyalSymbol";
import {
  EARLY_ADOPTER_LABEL,
  PAYMENT_TRUST_NOTE,
  PRICING_PLANS,
  SEATS_LIMITED_NOTE,
  cheapestPricingPlan,
  findPricingPlan,
  pricingPlansAbove,
  resolvePlanPricing,
} from "@/lib/pricing";
import type { PricingPlan } from "@/lib/pricing";
import { formatReset } from "@/lib/quota-reset";
import { track } from "@/lib/analytics/client";
import { useEarlyAdopterStore } from "@/stores/early-adopter-store";
import { useIntroOfferStore } from "@/stores/intro-offer-store";
import { IntroOfferSummary } from "@/components/promo/IntroOfferSummary";
import {
  INTRO_OFFER_COPY,
  INTRO_OFFER_PLAN_ID,
} from "@/components/promo/intro-offer-content";
import type { SSEDeepSearchGated, SSEQuotaExceeded } from "@/types";

/**
 * The deep-search plan gate's variant (migration 197) — the ONE place its web
 * copy lives; the body line is the server's `message_ar`.
 * ⚠ PROPOSED copy, not owner-approved yet. Latin digits only.
 */
const DEEP_SEARCH_GATE_COPY = {
  title: {
    free_limit: "استخدمت البحث المعمّق المتاح في باقتك",
    low_points: "رصيدك لا يكفي لبحث معمّق جديد",
  },
  /** Before «{resetText} — أو رقِّ باقتك الآن.» on the count refusal (the points
   *  refusal keeps the dialog's «تعود نقاطك»). */
  countBack: "يتجدّد بحثك المعمّق",
} as const;

interface QuotaUpgradeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * The block that opened this dialog, when there was one. The ladder, the
   * countdown and the headline are all read from it.
   *
   * ABSENT = opened deliberately from Settings, where no window is blown. That
   * surface passes `currentPlanId` instead — deliberately NOT a synthetic
   * `SSEQuotaExceeded` with invented `used`/`limit`/`resets_at`, which would
   * render as fact on screen.
   */
  info?: SSEQuotaExceeded;
  /**
   * The deep-search plan gate's refusal, when THAT opened the dialog (never
   * together with `info`). A real server block like `info` — ladder, countdown
   * and plan come from it — just not a spent window, so it is its own prop
   * rather than a synthetic `SSEQuotaExceeded`.
   */
  deepSearchGate?: SSEDeepSearchGated;
  /**
   * The user's plan, for the block-less surfaces. Only read when neither
   * `info` nor `deepSearchGate` is set; the ladder is then derived from the
   * catalog by price.
   */
  currentPlanId?: string | null;
}

/**
 * The plans that would unblock the user, shown either at a quota block or on
 * demand from Settings.
 *
 * ⚠ It renders ONLY the plans that are a real step up — never the current plan
 * and never a cheaper one. Usage is a rolling sum over `llm_calls`, not a
 * balance: re-buying the same plan leaves the same spend sitting in the same
 * window against the same cap, so only a higher limit clears a block. The
 * ladder therefore arrives ready-made — from the server on the block path
 * (`upgrade_options`, filtered by the window that actually blocked) and from
 * `pricingPlansAbove()` on the Settings path (price only, no limits needed).
 *
 * The plan cards MIRROR /pricing by construction: same `PRICING_PLANS` array,
 * same `PlanPrice`, same `PlanCheckoutCta` (so the signed-in → `/pay/{id}` vs
 * signed-out → `/login?next=…` split is inherited, not re-derived). Nothing
 * about a plan is written down here — a repricing touches `lib/pricing.ts` and
 * both surfaces move together. The «ابتداءً من» figure comes from
 * `cheapestPricingPlan()` for the same reason.
 *
 * المشتركون الأوائل rides on that property: every price on this surface goes
 * through `resolvePlanPricing()`, the same call /pricing makes, so the promo
 * price, the struck-through original and the «المقاعد محدودة» note appear here
 * exactly when they appear there. The card markup is deliberately NOT forked to
 * accommodate it.
 */
export function QuotaUpgradeDialog({
  open,
  onOpenChange,
  info,
  deepSearchGate: gate,
  currentPlanId,
}: QuotaUpgradeDialogProps) {
  const [now, setNow] = useState<number>(() => Date.now());

  // Keep the countdown honest while the dialog sits open — a user reading three
  // plan cards can easily be here past the minute boundary. The `setNow` on
  // entry matters just as much: the banner can sit dismissed-but-mounted for a
  // long while before «عرض الباقات» is clicked, and the modal must not open
  // showing the same `resets_at` differently from the banner behind it.
  useEffect(() => {
    if (!open) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, [open]);

  // المشتركون الأوائل — one probe per session, shared with إعدادات الحساب. The
  // store defaults to "closed", so the cards render list prices until an answer
  // actually arrives and never the other way round.
  const campaign = useEarlyAdopterStore((s) => s.campaign);
  const ensureCampaignLoaded = useEarlyAdopterStore((s) => s.ensureLoaded);
  useEffect(() => {
    if (open) ensureCampaignLoaded();
  }, [open, ensureCampaignLoaded]);

  // The profession offer — Pro tier 1 (pricing_campaigns_v2; was
  // profession_intro_offer §4.4). Re-asked on every open: eligibility is
  // per-user and changes the moment they buy. Fail-closed like the campaign —
  // until the server says eligible, the Pro card is unchanged.
  const introOffer = useIntroOfferStore((s) => s.offer);
  useEffect(() => {
    if (open) void useIntroOfferStore.getState().refresh();
  }, [open]);

  // A server block opened this dialog: a spent window, or the deep-search gate.
  const blocked = Boolean(info || gate);
  const ladder = info?.upgrade_options ?? gate?.upgrade_options;

  const plans = useMemo<PricingPlan[]>(() => {
    // No block → Settings. Derived by LIST price (the plan rank), since there
    // is no blocking window to ask the server about. Promo prices are batch
    // data that can change with no deploy, so they never rank the ladder — see
    // `pricingPlansAbove`.
    if (!blocked) return pricingPlansAbove(currentPlanId);
    // Deploy skew: a backend from before the ladder shipped sends no field at
    // all. Fall back to the whole catalog — i.e. exactly the pre-ladder free
    // paywall — rather than to an empty dialog. An EMPTY array is different:
    // the server did answer, and the answer was "nothing would help".
    if (!ladder) return PRICING_PLANS;
    const resolved: PricingPlan[] = [];
    for (const id of ladder) {
      // Only plans we hold display copy for. A slug with no card here (a grant
      // like `dev`) is not something we can sell in a dialog.
      const plan = findPricingPlan(id);
      if (plan) resolved.push(plan);
    }
    return resolved;
  }, [blocked, ladder, currentPlanId]);

  // A zero limit is not an exhausted window — it is a feature the free plan
  // never included (OCR is `ocr_pages_monthly = 0` on `free`). Saying «انتهى
  // حدّك» about something the user never had is simply wrong, and there is no
  // reset instant to count down to either.
  const notIncluded = info ? info.limit <= 0 : false;

  const resetsAt = info && !notIncluded ? info.resets_at : gate?.resets_at;
  const resetText = resetsAt ? formatReset(resetsAt, now) : "";

  // EFFECTIVE plan on the block path (an expired `pro` reports `"free"`).
  const planId = info ? info.plan_id : gate ? gate.plan_id : currentPlanId;
  const onPaidPlan = Boolean(planId) && planId !== "free";
  const planNameAr = planId ? findPricingPlan(planId)?.nameAr : undefined;

  const title = gate
    ? DEEP_SEARCH_GATE_COPY.title[gate.reason]
    : notIncluded
      ? "هذه الميزة غير متاحة في الباقة المجانية"
      : !info
        ? "ترقية الباقة"
        : onPaidPlan
          ? planNameAr
            ? `انتهت نقاط باقتك — ${planNameAr}`
            : "انتهت نقاط باقتك"
          : "انتهى حدّ الاستخدام المجاني";

  // The one true thing worth saying out loud, and the reason this dialog beats
  // waiting: an upgrade takes effect at once.
  //
  // ⚠ Branched, because the first half is only true on a PAID → higher-paid
  // move. `stamp_usage_reset` (plan §A3) no-ops unless BOTH plans carry a
  // `price_sar`, so a free user's consumed points are never zeroed — they
  // simply stop binding, because the plan they just bought measures a
  // different, far larger window. Both branches promise «فوراً»; only the paid
  // one promises a reset. DEPENDS ON migration 131 — do not ship this copy
  // ahead of it.
  const unblockLine = notIncluded
    ? "الاشتراك يفعّل هذه الميزة فوراً."
    : onPaidPlan
      ? "الترقية تصفّر استهلاكك الحالي وتعيدك للعمل فوراً."
      : "الاشتراك يرفع حدودك ويعيدك للعمل فوراً.";

  // The intro offer replaces the Pro card's price block — only when the server
  // says eligible AND the ladder actually holds Pro. Ranking and the card list
  // are untouched: the offer price never enters `PRICING_PLANS`.
  const offerOnCard =
    introOffer !== null && plans.some((plan) => plan.id === INTRO_OFFER_PLAN_ID);

  // One `intro_offer_shown` per opening of the BLOCK path (the quota wall); the
  // Settings path shows the same card but is not the wall being measured.
  const offerTrackedRef = useRef(false);
  useEffect(() => {
    if (!open || !offerOnCard || !blocked) {
      if (!open) offerTrackedRef.current = false;
      return;
    }
    if (offerTrackedRef.current || !introOffer) return;
    offerTrackedRef.current = true;
    track("intro_offer_shown", {
      trigger: "quota",
      group: introOffer.profession_group,
    });
  }, [open, offerOnCard, blocked, introOffer]);

  // The «ابتداءً من …» summary earns its place only when the full ladder is on
  // offer; one or two cards are read directly, and quoting the catalog's
  // cheapest plan next to a card set that excludes it would be a lie. Hidden
  // under the intro offer too, which would otherwise sit below a «ابتداءً من»
  // figure higher than the price on its own card.
  const showFromPrice = plans.length === PRICING_PLANS.length && !offerOnCard;
  // Cheapest by the DISPLAYED price — the server's promo where one applies,
  // whatever batch is open right now — and quoted at the same price its own
  // card shows: the list figure would undersell the offer by naming a number
  // nothing on screen charges.
  const cheapest = cheapestPricingPlan(campaign);
  const cheapestPricing = resolvePlanPricing(cheapest, campaign);

  // Cards priced against the campaign — resolved once, so the badge and the
  // prices can never disagree about whether an offer is running.
  const cards = plans.map((plan) => ({
    plan,
    pricing: resolvePlanPricing(plan, campaign),
  }));
  const campaignVisible = cards.some((card) => card.pricing.isPromo);

  // One card in a 4xl modal under a 3-column grid looks broken. Both the shell
  // and the grid follow the count.
  const widthClass =
    plans.length >= 3
      ? "max-w-4xl"
      : plans.length === 2
        ? "max-w-2xl"
        : "max-w-md";
  const gridClass =
    plans.length >= 3
      ? "md:grid-cols-3"
      : plans.length === 2
        ? "md:grid-cols-2"
        : "md:grid-cols-1";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Width only — `DialogContent` already carries
          `max-h-[calc(100dvh-2rem)] overflow-y-auto`, and `dvh` handles the
          mobile URL bar in a way a `90vh` override here would undo. */}
      <DialogContent
        dir="rtl"
        lang="ar"
        presentation="mobileSheet"
        className={widthClass}
      >
        <DialogHeader className="text-right sm:text-right">
          <DialogTitle className="text-xl">{title}</DialogTitle>
          <DialogDescription className="text-right leading-relaxed">
            {info
              ? info.message_ar
              : gate
                ? gate.message_ar
                : "اختر باقة أعلى للحصول على نقاط استخدام أكثر."}
          </DialogDescription>
        </DialogHeader>

        {/* Both ways out, waiting first: the user is not cornered, and saying
            so is what makes the upgrade line credible. */}
        {resetText && (
          <p className="text-sm leading-relaxed text-foreground">
            {gate?.reason === "free_limit"
              ? DEEP_SEARCH_GATE_COPY.countBack
              : "تعود نقاطك"}{" "}
            {resetText} — أو رقِّ باقتك الآن.
          </p>
        )}

        <p className="flex items-start gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2.5 text-sm font-medium leading-relaxed text-foreground">
          <Zap className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <span>{unblockLine}</span>
        </p>

        {/* المشتركون الأوائل — shown only when a card is actually discounted by
            the server's promo (`resolvePlanPricing` reads `/payments/
            early-adopter`). «المقاعد محدودة» is the ONLY scarcity signal
            permitted — never a remaining count, a batch, a tier number or a
            closing date (pricing_campaigns_v2 §1.4-10). */}
        {campaignVisible && (
          <p
            className="flex items-center gap-2 text-sm font-medium text-primary"
            data-testid="early-adopter-badge"
          >
            <Sparkles className="h-4 w-4 shrink-0" />
            <span>
              {EARLY_ADOPTER_LABEL} · {SEATS_LIMITED_NOTE}
            </span>
            <PromoTermsLink className="font-normal" />
          </p>
        )}

        {/* The offer, in one line, before any card: the cheapest way back to
            work. Quoted from the catalog so it can never contradict the cards
            rendered directly beneath it. */}
        {showFromPrice && (
          <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm leading-relaxed text-foreground">
            <span>يمكنك متابعة العمل بالاشتراك ابتداءً من</span>
            <span className="inline-flex items-center gap-1 font-bold tabular-nums">
              {cheapestPricing.price}
              <RiyalSymbol className="h-4 w-auto" />
            </span>
            <span className="text-muted-foreground">
              (باقة {cheapest.nameAr} — {cheapest.period})
            </span>
          </p>
        )}

        {/* Plan cards — same structure as app/pricing/page.tsx, tightened one
            step (p-5, text-base heading) to fit three across inside a modal. */}
        <div className={`mt-2 grid gap-4 ${gridClass}`}>
          {cards.map(({ plan, pricing }) => {
            const offer =
              offerOnCard && plan.id === INTRO_OFFER_PLAN_ID ? introOffer : null;
            return (
              <div
                key={plan.id}
                className={`relative flex flex-col rounded-2xl border bg-card p-5 ${
                  plan.highlighted || offer
                    ? "border-primary shadow-lg"
                    : "border-border"
                }`}
                data-testid={offer ? "intro-offer-card" : undefined}
              >
                {(offer || plan.highlighted) && (
                  <span className="absolute -top-3 right-5 rounded-full bg-primary px-3 py-1 text-xs font-medium text-primary-foreground">
                    {offer
                      ? INTRO_OFFER_COPY.headline(offer.profession_group)
                      : "الأكثر شيوعاً"}
                  </span>
                )}

                <h3 className="text-base font-bold text-foreground">
                  {plan.nameAr}
                </h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  {plan.tagline}
                </p>

                {offer ? (
                  // The offer's own block carries the schedule (price and months
                  // from the server), the non-refundable first payment, the
                  // cancel line, «المقاعد محدودة» and the promo-terms link.
                  <IntroOfferSummary offer={offer} className="mt-4" />
                ) : (
                  <>
                    <PlanPrice
                      price={pricing.price}
                      listPrice={pricing.listPrice}
                      period={plan.period}
                      className="mt-4"
                    />
                    <p className="mt-2 text-xs text-muted-foreground">
                      {pricing.billingNote}
                    </p>
                    {pricing.isPromo && (
                      <p className="mt-1.5 text-xs font-medium text-primary">
                        {SEATS_LIMITED_NOTE}
                      </p>
                    )}
                  </>
                )}

                <ul className="mt-5 flex flex-col gap-2.5">
                  {plan.features.map((feature) => (
                    <li
                      key={feature}
                      className="flex items-start gap-2 text-sm text-foreground"
                    >
                      <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                      <span>{feature}</span>
                    </li>
                  ))}
                </ul>

                <div className="mt-auto pt-6">
                  <PlanCheckoutCta
                    planId={plan.id}
                    highlighted={plan.highlighted || offer !== null}
                    label={onPaidPlan ? "ترقية الآن" : "اشترك الآن"}
                    onClick={
                      offer
                        ? () =>
                            track("intro_offer_cta", {
                              trigger: "quota",
                              group: offer.profession_group,
                            })
                        : undefined
                    }
                  />
                </div>
              </div>
            );
          })}
        </div>

        <div className="mt-2 flex flex-col items-center gap-3 text-center">
          <p className="flex items-start gap-2 text-xs leading-relaxed text-muted-foreground">
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
            <span>{PAYMENT_TRUST_NOTE}</span>
          </p>
          {/* An exit that isn't "buy" and isn't "dismiss" — /pricing carries the
              full terms this modal deliberately keeps short. */}
          <Link
            href="/pricing"
            className="text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
          >
            عرض جميع تفاصيل الباقات
          </Link>
        </div>
      </DialogContent>
    </Dialog>
  );
}
