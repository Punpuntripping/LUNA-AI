import Link from "next/link";
import { Check } from "lucide-react";
import { RiyalSymbol } from "@/components/icons/RiyalSymbol";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { PRICING_PLANS } from "@/lib/pricing";
import { PRIMARY_CTA_HREF, SUPPORT_EMAIL } from "./content";

/**
 * Pricing teaser on the landing page. Reuses ``PRICING_PLANS`` — the same source
 * of truth the full /pricing page renders — so the two never drift. Payment
 * isn't wired yet (access is activation-code based), so the cards lead to
 * signup and the footnote points at support + the full pricing page.
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

              <div className="mt-5 flex items-end gap-1.5">
                <span className="text-5xl font-bold leading-none tabular-nums text-foreground">
                  {plan.price}
                </span>
                <RiyalSymbol className="mb-1 h-7 w-auto text-foreground" />
                <span className="mb-1 text-sm text-muted-foreground">
                  {plan.period}
                </span>
              </div>
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
                <Link
                  href={PRIMARY_CTA_HREF}
                  className={cn(
                    buttonVariants({
                      variant: plan.highlighted ? "default" : "outline",
                    }),
                    "w-full font-semibold",
                  )}
                >
                  ابدأ الآن
                </Link>
              </div>
            </div>
          ))}
        </div>

        {/* Activation-code notice + full-pricing link */}
        <p className="mt-8 text-center text-sm leading-relaxed text-muted-foreground">
          الدفع والاشتراك غير مُفعّل بعد؛ الوصول حالياً عبر رمز تفعيل —{" "}
          <a
            href={`mailto:${SUPPORT_EMAIL}`}
            dir="ltr"
            className="font-medium text-primary underline-offset-4 hover:underline"
          >
            {SUPPORT_EMAIL}
          </a>
          {" · "}
          <Link
            href="/pricing"
            className="font-medium text-primary underline-offset-4 hover:underline"
          >
            تفاصيل الباقات الكاملة
          </Link>
        </p>
      </div>
    </section>
  );
}
