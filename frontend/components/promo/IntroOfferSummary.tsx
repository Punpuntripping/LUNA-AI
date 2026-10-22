import { PlanPrice } from "@/components/pricing/PlanPrice";
import { PromoTermsLink } from "@/components/pricing/PromoTermsLink";
import { SEATS_LIMITED_NOTE, formatSar } from "@/lib/pricing";
import { cn } from "@/lib/utils";
import type { IntroOffer } from "@/stores/intro-offer-store";
import {
  INTRO_OFFER_COPY,
  introOfferSchedule,
} from "@/components/promo/intro-offer-content";

interface IntroOfferSummaryProps {
  offer: IntroOffer;
  className?: string;
}

/**
 * The offer's price block — shared by `IntroOfferDialog` and the Pro card in
 * `QuotaUpgradeDialog`, so the two surfaces cannot quote it differently.
 *
 * Carries every disclosure the offer owes at the point it is shown: the
 * first-month price against the list price, the step-up schedule, the
 * non-refundable first month, how to cancel, «المقاعد محدودة», and the link to
 * the offer's conditions (a promo price is never shown without it).
 */
export function IntroOfferSummary({ offer, className }: IntroOfferSummaryProps) {
  return (
    <div className={cn("space-y-2", className)}>
      <PlanPrice
        price={formatSar(offer.first_month_sar)}
        listPrice={formatSar(offer.list_sar)}
        period={INTRO_OFFER_COPY.period}
      />
      <p className="text-sm leading-relaxed text-foreground">
        {introOfferSchedule(offer.then_sar, offer.then_months, offer.list_sar)}
      </p>
      <ul className="space-y-1 text-xs leading-relaxed text-muted-foreground">
        <li>{INTRO_OFFER_COPY.nonRefundable}</li>
        <li>{INTRO_OFFER_COPY.cancelAnytime}</li>
      </ul>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span className="font-medium text-primary">{SEATS_LIMITED_NOTE}</span>
        <span aria-hidden>·</span>
        <PromoTermsLink />
      </div>
    </div>
  );
}
