"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Check, ShieldCheck, Zap } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PlanPrice } from "@/components/pricing/PlanPrice";
import { PlanCheckoutCta } from "@/components/pricing/PlanCheckoutCta";
import { RiyalSymbol } from "@/components/icons/RiyalSymbol";
import {
  PAYMENT_TRUST_NOTE,
  PRICING_PLANS,
  cheapestPricingPlan,
  findPricingPlan,
  pricingPlansAbove,
} from "@/lib/pricing";
import type { PricingPlan } from "@/lib/pricing";
import { formatReset } from "@/lib/quota-reset";
import type { SSEQuotaExceeded } from "@/types";

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
   * The user's plan, for the block-less surfaces. Only read when `info` is
   * absent; the ladder is then derived from the catalog by price.
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
 */
export function QuotaUpgradeDialog({
  open,
  onOpenChange,
  info,
  currentPlanId,
}: QuotaUpgradeDialogProps) {
  const [now, setNow] = useState<number>(() => Date.now());

  // Keep the countdown honest while the dialog sits open — a user reading three
  // plan cards can easily be here past the minute boundary.
  useEffect(() => {
    if (!open) return;
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, [open]);

  const ladder = info?.upgrade_options;

  const plans = useMemo<PricingPlan[]>(() => {
    // No block → Settings. Derived by price, since there is no blocking window
    // to ask the server about.
    if (!info) return pricingPlansAbove(currentPlanId);
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
  }, [info, ladder, currentPlanId]);

  // A zero limit is not an exhausted window — it is a feature the free plan
  // never included (OCR is `ocr_pages_monthly = 0` on `free`). Saying «انتهى
  // حدّك» about something the user never had is simply wrong, and there is no
  // reset instant to count down to either.
  const notIncluded = info ? info.limit <= 0 : false;

  const resetText =
    info && !notIncluded && info.resets_at
      ? formatReset(info.resets_at, now)
      : "";

  // EFFECTIVE plan on the block path (an expired `pro` reports `"free"`).
  const planId = info ? info.plan_id : currentPlanId;
  const onPaidPlan = Boolean(planId) && planId !== "free";
  const planNameAr = planId ? findPricingPlan(planId)?.nameAr : undefined;

  const title = notIncluded
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

  // The «ابتداءً من …» summary earns its place only when the full ladder is on
  // offer; one or two cards are read directly, and quoting the catalog's
  // cheapest plan next to a card set that excludes it would be a lie.
  const showFromPrice = plans.length === PRICING_PLANS.length;
  const cheapest = cheapestPricingPlan();

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
      <DialogContent dir="rtl" lang="ar" className={widthClass}>
        <DialogHeader className="text-right sm:text-right">
          <DialogTitle className="text-xl">{title}</DialogTitle>
          <DialogDescription className="text-right leading-relaxed">
            {info
              ? info.message_ar
              : "اختر باقة أعلى للحصول على نقاط استخدام أكثر."}
          </DialogDescription>
        </DialogHeader>

        {/* Both ways out, waiting first: the user is not cornered, and saying
            so is what makes the upgrade line credible. */}
        {resetText && (
          <p className="text-sm leading-relaxed text-foreground">
            تعود نقاطك {resetText} — أو رقِّ باقتك الآن.
          </p>
        )}

        <p className="flex items-start gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2.5 text-sm font-medium leading-relaxed text-foreground">
          <Zap className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <span>{unblockLine}</span>
        </p>

        {/* The offer, in one line, before any card: the cheapest way back to
            work. Quoted from the catalog so it can never contradict the cards
            rendered directly beneath it. */}
        {showFromPrice && (
          <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm leading-relaxed text-foreground">
            <span>يمكنك متابعة العمل بالاشتراك ابتداءً من</span>
            <span className="inline-flex items-center gap-1 font-bold tabular-nums">
              {cheapest.price}
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
          {plans.map((plan) => (
            <div
              key={plan.id}
              className={`relative flex flex-col rounded-2xl border bg-card p-5 ${
                plan.highlighted ? "border-primary shadow-lg" : "border-border"
              }`}
            >
              {plan.highlighted && (
                <span className="absolute -top-3 right-5 rounded-full bg-primary px-3 py-1 text-xs font-medium text-primary-foreground">
                  الأكثر شيوعاً
                </span>
              )}

              <h3 className="text-base font-bold text-foreground">
                {plan.nameAr}
              </h3>
              <p className="mt-1 text-xs text-muted-foreground">
                {plan.tagline}
              </p>

              <PlanPrice
                price={plan.price}
                period={plan.period}
                className="mt-4"
              />
              <p className="mt-2 text-xs text-muted-foreground">
                {plan.billingNote}
              </p>

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
                  highlighted={plan.highlighted}
                  label={onPaidPlan ? "ترقية الآن" : "اشترك الآن"}
                />
              </div>
            </div>
          ))}
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
