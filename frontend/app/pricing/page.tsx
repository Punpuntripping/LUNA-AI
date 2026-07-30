import type { Metadata } from "next";
import { Check } from "lucide-react";
import { RiyalSymbol } from "@/components/icons/RiyalSymbol";
import { SitePageShell } from "@/components/site/SitePageShell";
import { PRICING_PLANS } from "@/lib/pricing";

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

        {/* Activation notice — paid plans not live yet; access is via code. */}
        <div className="mx-auto mb-10 max-w-2xl rounded-2xl border border-primary/30 bg-primary/5 p-5 text-center">
          <p className="text-sm font-semibold leading-relaxed text-foreground">
            الاشتراك غير مُفعّل بعد
          </p>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            لم يتم تفعيل الدفع والاشتراك حتى الآن. لاستخدام التطبيق، يُرجى
            التواصل معنا للحصول على رمز تفعيل عبر البريد:{" "}
            <a
              href="mailto:support@rayhanai.com"
              className="font-medium text-primary underline-offset-4 hover:underline"
              dir="ltr"
            >
              support@rayhanai.com
            </a>
          </p>
        </div>

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

              {/* Subscription not live yet — CTA disabled; access via activation code. */}
              <div className="mt-auto pt-7">
                <button
                  type="button"
                  disabled
                  aria-disabled="true"
                  className="w-full cursor-not-allowed rounded-lg border border-border bg-muted px-4 py-2.5 text-sm font-semibold text-muted-foreground opacity-60"
                >
                  غير متاح حالياً
                </button>
              </div>
            </div>
          ))}
        </div>

        <p className="mt-8 text-center text-xs leading-relaxed text-muted-foreground">
          تُستهلك النقاط مع كل بحث أو صياغة بحسب حجمها. جميع الأسعار بالريال
          السعودي.
        </p>
      </main>
    </SitePageShell>
  );
}
