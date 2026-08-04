import Link from "next/link";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { PlanPrice } from "@/components/pricing/PlanPrice";
import { PlanCheckoutCta } from "@/components/pricing/PlanCheckoutCta";
import {
  PAYMENT_TRUST_NOTE,
  PRICING_PLANS,
} from "@/lib/pricing";

/**
 * Pricing teaser on the landing page. Reuses ``PRICING_PLANS`` and the shared
 * ``PlanPrice`` / ``PlanCheckoutCta`` — the same pieces the full /pricing page
 * renders — so the two surfaces cannot drift on a price, a term, or where the
 * CTA leads.
 *
 * The «الدفع غير مُفعّل بعد · الوصول عبر رمز تفعيل» footnote that used to close
 * this section is gone: self-serve checkout went live with the Moyasar Wave 1
 * build, and a landing page that tells a visitor they cannot buy is worse than
 * no footnote at all. Activation codes still work — they are just no longer the
 * only door.
 */
export function PricingSection() {
  return (
    <section id="pricing" className="scroll-mt-20 bg-muted/30 py-16 sm:py-20">
      <div className="mx-auto max-w-5xl px-4">
        {/* Section header */}
        <div className="mx-auto max-w-2xl text-center">
          <span className="text-sm font-semibold text-primary">الباقات والأسعار</span>
          <h2 className="mt-2 text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            أسعار واضحة، تختار ما يناسبك
          </h2>
          <p className="mt-3 text-base leading-relaxed text-muted-foreground">
            تُحتسب النقاط مع كل بحث أو صياغة بحسب حجمه. جميع الأسعار بالريال السعودي.
          </p>
        </div>

        {/* Plan cards */}
        <div className="mt-10 grid gap-6 md:grid-cols-3">
          {PRICING_PLANS.map((plan) => (
            <div
              key={plan.id}
              className={cn(
                "relative flex flex-col rounded-2xl border bg-card p-6 shadow-sm transition-all duration-200 hover:-translate-y-1 hover:shadow-md",
                plan.highlighted
                  ? "border-primary shadow-md ring-1 ring-primary/20"
                  : "border-border",
              )}
            >
              {plan.highlighted && (
                <span className="absolute -top-3 right-6 rounded-full bg-primary px-3 py-1 text-xs font-medium text-primary-foreground">
                  الأكثر شيوعاً
                </span>
              )}

              <h3 className="text-lg font-bold text-foreground">{plan.nameAr}</h3>
              <p className="mt-1 text-sm text-muted-foreground">{plan.tagline}</p>

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

        {/* Refund terms deliberately absent (owner, 2026-08-04) — they appear
            only at the refund action in the receipts dialog. */}
        <p className="mt-8 text-center text-sm leading-relaxed text-muted-foreground">
          <Link
            href="/pricing"
            className="font-medium text-primary underline-offset-4 hover:underline"
          >
            تفاصيل الباقات الكاملة
          </Link>
        </p>
        <p className="mt-2 text-center text-xs leading-relaxed text-muted-foreground">
          {PAYMENT_TRUST_NOTE}
        </p>
      </div>
    </section>
  );
}
