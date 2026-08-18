import type { Metadata } from "next";
import { Check, ShieldCheck, Sparkles } from "lucide-react";
import { SitePageShell } from "@/components/site/SitePageShell";
import { PlanPrice } from "@/components/pricing/PlanPrice";
import { PlanCheckoutCta } from "@/components/pricing/PlanCheckoutCta";
import { PromoTermsLink } from "@/components/pricing/PromoTermsLink";
import {
  EARLY_ADOPTER_LABEL,
  PAYMENT_TRUST_NOTE,
  PRICING_PLANS,
  SEATS_LIMITED_NOTE,
  fetchEarlyAdopterCampaign,
  resolvePlanPricing,
} from "@/lib/pricing";

export const metadata: Metadata = {
  title: "الباقات والأسعار — ريحان",
  description: "باقات اشتراك ريحان: الأساسية والاحترافية والقصوى.",
  alternates: {
    canonical: "/pricing",
  },
};

/**
 * ISR, one minute (.claude/plans/early_adopters.md §6.1).
 *
 * This page was statically rendered — baked once, served forever — which was
 * fine while the prices were constants. With a campaign that opens and closes by
 * a one-row UPDATE, a frozen card showing ٣٩٫٩٠ against a checkout charging
 * ٤٩٫٩٠ is the worst mismatch available: it is discovered at the moment of
 * payment. Sixty seconds bounds that window, and the runbook still purges this
 * path explicitly when the campaign closes (percent-encoded, or the 200 lies).
 *
 * ⚠ Must stay a literal — Next only accepts a statically analysable value here,
 * so `EARLY_ADOPTER_REVALIDATE_SECONDS` cannot be imported into this position.
 * The two are kept equal by hand.
 */
export const revalidate = 60;

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function PricingPage() {
  // Fails safe to "closed" ⇒ list prices, on a timeout, a 404, an unreachable
  // backend (which is what `npm run build` sees) or a malformed body.
  const campaign = await fetchEarlyAdopterCampaign();
  const cards = PRICING_PLANS.map((plan) => ({
    plan,
    pricing: resolvePlanPricing(plan, campaign),
  }));
  // Announce the campaign only when a card is genuinely discounted — an open
  // flag whose payload priced nothing must not put a badge over list prices.
  const campaignVisible = cards.some((card) => card.pricing.isPromo);

  return (
    <SitePageShell>
      <main className="mx-auto max-w-5xl px-4 py-12">
        {/* Page title */}
        <header className="mb-10 flex flex-col items-center gap-4 text-center">
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            الباقات والأسعار
          </h1>
          {/* The ONLY scarcity signal permitted anywhere: no remaining count, no
              seat total, no closing date (plan §1.10). */}
          {campaignVisible && (
            <span
              className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/5 px-4 py-1.5 text-sm font-medium text-primary"
              data-testid="early-adopter-badge"
            >
              <Sparkles className="h-4 w-4 shrink-0" />
              {EARLY_ADOPTER_LABEL} · {SEATS_LIMITED_NOTE}
            </span>
          )}
          {campaignVisible && <PromoTermsLink />}
          <p className="max-w-xl text-sm leading-relaxed text-muted-foreground">
            اختر الباقة الأنسب لك.
          </p>
        </header>

        {/* Plan cards */}
        <div className="grid gap-6 md:grid-cols-3">
          {cards.map(({ plan, pricing }) => (
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
                price={pricing.price}
                listPrice={pricing.listPrice}
                period={plan.period}
                className="mt-5"
              />
              <p className="mt-2 text-xs text-muted-foreground">
                {pricing.billingNote}
              </p>
              {pricing.isPromo && (
                // In the card FLOW, not absolutely positioned: the «الأكثر
                // شيوعاً» ribbon already owns the top-right corner.
                <p className="mt-1.5 text-xs font-medium text-primary">
                  {SEATS_LIMITED_NOTE}
                </p>
              )}

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
