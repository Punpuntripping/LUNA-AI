import { Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PlanPrice } from "@/components/pricing/PlanPrice";
import { PromoTermsLink } from "@/components/pricing/PromoTermsLink";
import type { PricingPlan } from "@/lib/pricing";
import { FREE_TRIAL_COPY } from "@/components/promo/free-trial-content";

interface FreeTrialCardProps {
  /** The catalog card the week is presented as: name, tagline, list price. */
  plan: PricingPlan;
  pending: boolean;
  onClaim: () => void;
}

/**
 * The free week, as a plan card in `QuotaUpgradeDialog`: the list price struck
 * through beside 0. The button grants the week directly, with no checkout, so
 * this is not a `PlanCheckoutCta`.
 */
export function FreeTrialCard({ plan, pending, onClaim }: FreeTrialCardProps) {
  return (
    <div
      className="relative flex flex-col rounded-2xl border border-primary bg-card p-5 shadow-lg"
      data-testid="free-trial-card"
    >
      <span className="absolute -top-3 right-5 rounded-full bg-primary px-3 py-1 text-xs font-medium text-primary-foreground">
        {FREE_TRIAL_COPY.badge}
      </span>

      <h3 className="text-base font-bold text-foreground">{plan.nameAr}</h3>
      <p className="mt-1 text-xs text-muted-foreground">{plan.tagline}</p>

      <PlanPrice
        price={FREE_TRIAL_COPY.price}
        listPrice={plan.price}
        period={FREE_TRIAL_COPY.period}
        className="mt-4"
      />
      <p className="mt-2 text-xs text-muted-foreground">{FREE_TRIAL_COPY.note}</p>
      <PromoTermsLink className="mt-1.5 text-xs" />

      <ul className="mt-5 flex flex-col gap-2.5">
        {FREE_TRIAL_COPY.features.map((feature) => (
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
        <Button
          className="w-full"
          disabled={pending}
          onClick={onClaim}
          data-testid="free-trial-claim"
        >
          {pending ? FREE_TRIAL_COPY.pending : FREE_TRIAL_COPY.cta}
        </Button>
      </div>
    </div>
  );
}
