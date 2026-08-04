import type { Metadata } from "next";
import { Check, ShieldCheck } from "lucide-react";
import { SitePageShell } from "@/components/site/SitePageShell";
import { PlanPrice } from "@/components/pricing/PlanPrice";
import { PlanCheckoutCta } from "@/components/pricing/PlanCheckoutCta";
import {
  PAYMENT_TRUST_NOTE,
  PRICING_PLANS,
} from "@/lib/pricing";

export const metadata: Metadata = {
  title: "الباقات والأسعار — ريحان",
  description: "باقات اشتراك ريحان: الأساسية والاحترافية والقصوى.",
  alternates: {
    canonical: "/pricing",
  },
};

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function PricingPage() {
  return (
    <SitePageShell>
      <main className="mx-auto max-w-5xl px-4 py-12">
        {/* Page title */}
        <header className="mb-10 flex flex-col items-center gap-4 text-center">
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            الباقات والأسعار
          </h1>
          <p className="max-w-xl text-sm leading-relaxed text-muted-foreground">
            اختر الباقة الأنسب لك.
          </p>
        </header>

        {/* Plan cards */}
        <div className="grid gap-6 md:grid-cols-3">
          {PRICING_PLANS.map((plan) => (
            <div
              key={plan.id}
              className={`relative flex flex-col rounded-2xl border bg-card p-6 ${
                plan.highlighted
                  ? "border-primary shadow-lg"
                  : "border-border"
              }`}
            >
              {plan.highlighted && (
                <span className="absolute -top-3 right-6 rounded-full bg-primary px-3 py-1 text-xs font-medium text-primary-foreground">
                  الأكثر شيوعاً
                </span>
              )}

              <h2 className="text-lg font-bold text-foreground">
                {plan.nameAr}
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {plan.tagline}
              </p>

              <PlanPrice
                price={plan.price}
                period={plan.period}
                className="mt-5"
              />
              <p className="mt-2 text-xs text-muted-foreground">
                {plan.billingNote}
              </p>

              <ul className="mt-6 flex flex-col gap-3">
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

              <div className="mt-auto pt-7">
                <PlanCheckoutCta
                  planId={plan.id}
                  highlighted={plan.highlighted}
                />
              </div>
            </div>
          ))}
        </div>

        {/* Refund terms deliberately absent here (owner, 2026-08-04) — they
            surface only at the refund action itself, in the receipts dialog,
            where they can't be misread as an anytime-refund promise. */}
        <div className="mx-auto mt-8 flex max-w-2xl flex-col items-center gap-2 text-center">
          <p className="flex items-start gap-2 text-xs leading-relaxed text-muted-foreground">
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
            <span>{PAYMENT_TRUST_NOTE}</span>
          </p>
        </div>

        <p className="mt-6 text-center text-xs leading-relaxed text-muted-foreground">
          تُستهلك النقاط مع كل بحث أو صياغة بحسب حجمها. جميع الأسعار بالريال
          السعودي.
        </p>
      </main>
    </SitePageShell>
  );
}
