import Link from "next/link";
import { Calculator, ChevronLeft } from "lucide-react";
import type { CalculatorDef } from "@/lib/calculators/registry";

/**
 * One card in the /calculators hub grid: icon + «حاسبة {title}» + description +
 * a «احسبها مجاناً» footer. Server component — links to the calculator page.
 */
export function CalculatorCard({ calc }: { calc: CalculatorDef }) {
  return (
    <Link
      href={`/calculators/${calc.slug}`}
      dir="rtl"
      className="group flex h-full flex-col rounded-xl border border-border bg-card p-5 shadow-xs transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md"
    >
      <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary ring-1 ring-primary/15 transition-transform group-hover:scale-105">
        <Calculator aria-hidden="true" className="h-5 w-5" />
      </span>

      <h2 className="text-base font-bold leading-snug text-foreground group-hover:text-primary">
        حاسبة {calc.title_ar}
      </h2>

      <p className="mt-2 line-clamp-3 flex-1 text-sm leading-relaxed text-text-secondary">
        {calc.description}
      </p>

      <span className="mt-4 inline-flex items-center gap-1 text-xs font-semibold text-primary">
        احسبها مجاناً
        <ChevronLeft
          aria-hidden="true"
          className="h-3.5 w-3.5 shrink-0 transition-transform group-hover:-translate-x-0.5"
        />
      </span>
    </Link>
  );
}
