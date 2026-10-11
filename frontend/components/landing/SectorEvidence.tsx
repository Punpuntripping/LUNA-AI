import Link from "next/link";
import { ArrowLeft, Search } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  PRIMARY_CTA_HREF,
  SECTORS_CLOSING,
  SECTORS_HEADER,
  SECTOR_EXAMPLES,
} from "./content";

/**
 * Sector evidence for «ما الذي يميّزنا عن منافسينا في السوق؟» — rendered as the
 * `children` of the landing's ComparisonSection (no <section> of its own).
 * Four real documents from four sectors, each linking to its library page, then
 * the «التجربة خير برهان» signup close.
 */
export function SectorEvidence() {
  return (
    <>
      <div className="mt-12 text-center">
        <h3 className="text-xl font-bold text-foreground sm:text-2xl">
          {SECTORS_HEADER.title}
        </h3>
        <p className="mt-1.5 text-sm text-muted-foreground">
          {SECTORS_HEADER.subtitle}
        </p>
      </div>

      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        {SECTOR_EXAMPLES.map((ex) => {
          const Icon = ex.icon;
          return (
            <Link
              key={ex.slug}
              href={`/regulations/${encodeURIComponent(ex.slug)}`}
              className="group flex items-start gap-3 rounded-xl border border-border bg-card p-4 shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md"
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <Icon className="h-4 w-4" />
              </span>
              <div className="min-w-0 flex-1">
                <span className="text-xs font-semibold text-primary">
                  {ex.sector}
                </span>
                <h4 className="mt-0.5 text-sm font-bold leading-snug text-foreground">
                  {ex.title}
                </h4>
                <p className="mt-1.5 flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
                  <Search className="mt-0.5 h-3 w-3 shrink-0" />
                  <span>«{ex.q}»</span>
                </p>
              </div>
              <ArrowLeft className="mt-1 h-4 w-4 shrink-0 text-primary/60 transition-transform group-hover:-translate-x-0.5" />
            </Link>
          );
        })}
      </div>

      <div className="mx-auto mt-10 max-w-2xl text-center">
        <p className="text-pretty text-lg font-semibold leading-relaxed text-foreground">
          {SECTORS_CLOSING.lead}
        </p>
        <p className="mt-1.5 text-base text-muted-foreground">
          {SECTORS_CLOSING.body}
        </p>
        <Link
          href={PRIMARY_CTA_HREF}
          className={cn(
            buttonVariants({ size: "lg" }),
            "mt-6 w-full gap-2 text-base font-semibold shadow-sm transition-shadow hover:shadow-md sm:w-auto",
          )}
        >
          {SECTORS_CLOSING.cta}
          <ArrowLeft className="h-4 w-4" />
        </Link>
      </div>
    </>
  );
}
