import { RiyalSymbol } from "@/components/icons/RiyalSymbol";
import { splitPrice } from "@/lib/pricing";
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
 * The fractional part renders at `text-2xl` against the integer's `text-5xl`.
 * That is not decoration: «١٨٩٫٩٠» at a uniform `text-5xl` is wide enough to
 * reflow the three-card grid at the md breakpoint, which is what made the
 * repricing from «١٨٩» a layout change and not just a copy change.
 *
 * NO tax language here (decision 2026-08-04): the business holds no VAT
 * registration, so «شامل الضريبة» must not appear anywhere — the price is
 * simply the price. Receipts are plain «إيصال دفع» for the same reason.
 */
export function PlanPrice({ price, period, className }: PlanPriceProps) {
  const { whole, fraction } = splitPrice(price);

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <div className="flex items-end gap-1.5">
        <span className="flex items-baseline font-bold leading-none tabular-nums text-foreground">
          <span className="text-5xl leading-none">{whole}</span>
          {fraction && (
            <span className="text-2xl leading-none">{fraction}</span>
          )}
        </span>
        <RiyalSymbol className="mb-1 h-7 w-auto text-foreground" />
        <span className="mb-1 text-sm text-muted-foreground">{period}</span>
      </div>
    </div>
  );
}
