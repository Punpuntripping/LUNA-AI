import { RiyalSymbol } from "@/components/icons/RiyalSymbol";
import { cn } from "@/lib/utils";

interface PlanPriceProps {
  /** Display price in Latin digits, e.g. «89.90». */
  price: string;
  /**
   * The ORIGINAL price, struck through beside `price` — set only while a
   * promotional price is on offer (المشتركون الأوائل). Omit it and this
   * component renders exactly as it did before the campaign existed.
   */
  listPrice?: string | null;
  /** Cadence label rendered beside the symbol («شهرياً»). */
  period: string;
  className?: string;
}

/**
 * The price block shared by /pricing, the landing teaser and the quota upgrade
 * dialog — one component so the surfaces cannot disagree about what a plan
 * costs.
 *
 * The price renders as ONE piece at a uniform size (owner, 2026-08-04): the
 * earlier big-integer/small-fraction split read as «90.» colliding with the
 * riyal symbol in RTL — ugly and ambiguous on a payment surface. `text-4xl`
 * (not 5xl) keeps «289.90» from reflowing the three-card grid at md.
 *
 * ⚠ `listPrice` is a SIBLING element, never a re-split of the number. It sits
 * after the riyal symbol at `text-base`, so the amount the user will actually be
 * charged stays the single large figure and the struck one cannot be mistaken
 * for part of it.
 *
 * No VAT wording here — «شامل الضريبة» was removed 2026-08-08 (no VAT
 * registration yet; see the note in lib/pricing.ts before restoring it).
 */
export function PlanPrice({
  price,
  listPrice,
  period,
  className,
}: PlanPriceProps) {
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <div className="flex flex-wrap items-end gap-x-1.5 gap-y-1">
        <span className="text-4xl font-bold leading-none tabular-nums text-foreground">
          {price}
        </span>
        <RiyalSymbol className="mb-0.5 h-6 w-auto text-foreground" />
        {listPrice && (
          // «بدلاً من» is read aloud but not drawn: a screen reader hearing two
          // bare numbers cannot tell which one is being charged, while on screen
          // the strikethrough already says it in less space.
          <span className="mb-0.5 inline-flex items-center gap-1 text-base text-muted-foreground">
            <span className="sr-only">بدلاً من</span>
            <span className="tabular-nums line-through">{listPrice}</span>
          </span>
        )}
        <span className="mb-0.5 text-sm text-muted-foreground">{period}</span>
      </div>
    </div>
  );
}
