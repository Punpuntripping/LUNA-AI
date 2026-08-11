"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Check, ShieldCheck } from "lucide-react";
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
} from "@/lib/pricing";
import { formatReset } from "@/lib/quota-reset";
import type { SSEQuotaExceeded } from "@/types";

interface QuotaUpgradeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  info: SSEQuotaExceeded;
}

/**
 * The paywall a FREE-plan user meets when a send is refused for quota.
 *
 * Deliberately scoped to the free plan (`QuotaBanner` decides; see
 * `shouldOfferUpgrade` there). A paid subscriber who ran their weekly window
 * down does not need a sales pitch — they need the reset time, which the banner
 * already gives them. Selling to someone who has already paid reads as a
 * shakedown, and this dialog is modal.
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
}: QuotaUpgradeDialogProps) {
  const [now, setNow] = useState<number>(() => Date.now());

  // Keep the countdown honest while the dialog sits open — a user reading three
  // plan cards can easily be here past the minute boundary.
  useEffect(() => {
    if (!open) return;
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, [open]);

  const cheapest = useMemo(() => cheapestPricingPlan(), []);

  // A zero limit is not an exhausted window — it is a feature the free plan
  // never included (OCR is `ocr_pages_monthly = 0` on `free`). Saying «انتهى
  // حدّك» about something the user never had is simply wrong, and there is no
  // reset instant to count down to either.
  const notIncluded = info.limit <= 0;

  const resetText =
    !notIncluded && info.resets_at ? formatReset(info.resets_at, now) : "";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Width only — `DialogContent` already carries
          `max-h-[calc(100dvh-2rem)] overflow-y-auto`, and `dvh` handles the
          mobile URL bar in a way a `90vh` override here would undo. */}
      <DialogContent dir="rtl" lang="ar" className="max-w-4xl">
        <DialogHeader className="text-right sm:text-right">
          <DialogTitle className="text-xl">
            {notIncluded
              ? "هذه الميزة غير متاحة في الباقة المجانية"
              : "انتهى حدّ الاستخدام المجاني"}
          </DialogTitle>
          <DialogDescription className="text-right leading-relaxed">
            {info.message_ar}
            {resetText && ` يُعاد الاحتساب ${resetText}.`}
          </DialogDescription>
        </DialogHeader>

        {/* The offer, in one line, before any card: the cheapest way back to
            work. Quoted from the catalog so it can never contradict the cards
            rendered directly beneath it. */}
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

        {/* Plan cards — same structure as app/pricing/page.tsx, tightened one
            step (p-5, text-base heading) to fit three across inside a modal. */}
        <div className="mt-2 grid gap-4 md:grid-cols-3">
          {PRICING_PLANS.map((plan) => (
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
