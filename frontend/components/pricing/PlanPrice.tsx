import { RiyalSymbol } from "@/components/icons/RiyalSymbol";
import { cn } from "@/lib/utils";

interface PlanPriceProps {
  /** Arabic-Indic display price, e.g. «٨٩٫٩٠». */
  price: string;
  /** Cadence label rendered beside the symbol («شهرياً»). */
  period: string;
  className?: string;
}

/**
 * The price block shared by /pricing and the landing teaser — one component so
 * the two surfaces cannot disagree about what a plan costs.
 *
 * The price renders as ONE piece at a uniform size (owner, 2026-08-04): the
 * earlier big-integer/small-fraction split read as «٩٠٫» colliding with the
 * riyal symbol in RTL — ugly and ambiguous on a payment surface. `text-4xl`
 * (not 5xl) keeps «١٨٩٫٩٠» from reflowing the three-card grid at md.
 *
 * No VAT wording here — «شامل الضريبة» was removed 2026-08-08 (no VAT
 * registration yet; see the note in lib/pricing.ts before restoring it).
 */
export function PlanPrice({ price, period, className }: PlanPriceProps) {
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <div className="flex items-end gap-1.5">
        <span className="text-4xl font-bold leading-none tabular-nums text-foreground">
          {price}
        </span>
        <RiyalSymbol className="mb-0.5 h-6 w-auto text-foreground" />
        <span className="mb-0.5 text-sm text-muted-foreground">{period}</span>
      </div>
    </div>
  );
}
