import Link from "next/link";
import { LEGAL_ROUTES } from "@/lib/legal";
import { PROMO_TERMS_NOTE } from "@/lib/pricing";
import { cn } from "@/lib/utils";

interface PromoTermsLinkProps {
  className?: string;
}

/**
 * «تطبق أحكام العروض الترويجية» — the link that travels with every promotional
 * price we display.
 *
 * One component, shared by /pricing, the landing teaser and the quota-upgrade
 * dialog, for the same reason `PlanPrice` is shared: three surfaces showing the
 * same discount must not disagree about where its conditions live.
 *
 * ⚠ WHY THIS EXISTS AT ALL. The offer's conditions — who qualifies, the 90
 * days, the step-up to the list price, and that cancelling forfeits the price
 * permanently — were moved OUT of /terms §5 and onto `/promo-terms` (owner,
 * 2026-08-18), because they bind only the users who take an offer and two long
 * clauses made the subscription terms unreadable for everyone else. That split
 * is only honest while the link actually accompanies the price. A discounted
 * number with no route to its conditions is a WORSE disclosure than the
 * crowded §5 it replaced — so render this wherever a promo price is shown, and
 * never render a promo price without it.
 *
 * Deliberately muted and small: it is a disclosure, not a call to action. It
 * must still be a real link — never plain text, and never a tooltip.
 */
export function PromoTermsLink({ className }: PromoTermsLinkProps) {
  return (
    <Link
      href={LEGAL_ROUTES.promoTerms}
      className={cn(
        "text-xs text-muted-foreground underline underline-offset-4 transition-colors hover:text-foreground",
        className,
      )}
      data-testid="promo-terms-link"
    >
      {PROMO_TERMS_NOTE}
    </Link>
  );
}
