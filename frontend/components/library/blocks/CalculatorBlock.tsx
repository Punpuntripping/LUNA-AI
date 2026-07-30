import Link from "next/link";
import { Calculator, ChevronLeft } from "lucide-react";
import { cn } from "@/lib/utils";
import { getCalculator } from "@/lib/calculators/registry";
import { CalculatorForm } from "@/components/calculators/CalculatorForm";
import type { CalculatorBlockProps } from "@/types/library";

/**
 * Inline calculator embed for مادة pages — the bidirectional half of the
 * calculators mesh: a مادة whose number a calculator cites (via
 * `getCalculatorsForArticle`) renders the live calculator right in context.
 *
 * Server component: resolves the registry def for the heading + full-page link
 * and delegates the interactive part to the `CalculatorForm` client leaf (passed
 * only the `slug` string — never the non-serializable `compute`). Renders nothing
 * for an unknown slug.
 */
export function CalculatorBlock({ slug, className }: CalculatorBlockProps) {
  const def = getCalculator(slug);
  if (!def) return null;

  return (
    <section
      dir="rtl"
      className={cn(
        "rounded-2xl border border-border bg-surface-1 p-4 sm:p-5",
        className,
      )}
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-base font-bold text-foreground">
          <Calculator
            aria-hidden="true"
            className="h-5 w-5 shrink-0 text-primary"
          />
          حاسبة {def.title_ar}
        </h2>
        <Link
          href={`/calculators/${def.slug}`}
          className="inline-flex items-center gap-1 text-xs font-medium text-primary underline-offset-2 hover:underline"
        >
          افتح الحاسبة كاملة
          <ChevronLeft aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        </Link>
      </div>

      <p className="mb-4 text-sm leading-relaxed text-text-secondary">
        {def.description}
      </p>

      <CalculatorForm slug={def.slug} compact />

      <p className="mt-4 text-xs text-muted-foreground">
        نتيجة استرشادية — راجع مختصاً.
      </p>
    </section>
  );
}
