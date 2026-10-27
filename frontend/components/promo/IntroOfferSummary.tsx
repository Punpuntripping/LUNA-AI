import { PlanPrice } from "@/components/pricing/PlanPrice";
import { PromoTermsLink } from "@/components/pricing/PromoTermsLink";
import { SEATS_LIMITED_NOTE, formatSar } from "@/lib/pricing";
import { cn } from "@/lib/utils";
import {
  INTRO_OFFER_COPY,
  introOfferSchedule,
  type OfferTerms,
} from "@/components/promo/intro-offer-content";

interface IntroOfferSummaryProps {
  offer: OfferTerms;
  className?: string;
}

/**
 * The offer's price block — shared by `IntroOfferDialog` and the Pro card in
 * `QuotaUpgradeDialog`, so the two surfaces cannot quote it differently.
 *
 * Carries every disclosure the offer owes at the point it is shown: the offer
 * price against the list price for the server's number of months, the whole
 * schedule in one sentence («19.90 ريال لأول شهرين، ثم 94.90 ريال شهرياً»), the
 * non-refundable first payment, how to cancel, «المقاعد محدودة», and the link to
 * the offer's conditions (a promo price is never shown without it).
 */
export function IntroOfferSummary({ offer, className }: IntroOfferSummaryProps) {
  return (
    <div className={cn("space-y-2", className)}>
      <PlanPrice
        price={formatSar(offer.offer_sar)}
        listPrice={formatSar(offer.list_sar)}
        period={INTRO_OFFER_COPY.period(offer)}
      />
      <p className="text-sm leading-relaxed text-foreground">
        {introOfferSchedule(offer)}
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
