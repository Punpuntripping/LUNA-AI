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
 * «ما الذي يميّزنا عن منافسينا في السوق؟» — corpus breadth across regulators.
 * Four real documents from four sectors, each linking to its page in the public
 * library, then the «التجربة خير برهان» signup close.
 */
export function SectorsSection() {
  return (
    <section className="border-y border-border/40 bg-muted/30">
      <div className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
        <div className="mx-auto max-w-2xl text-center">
          <span className="text-sm font-semibold text-primary">
            {SECTORS_HEADER.eyebrow}
          </span>
          <h2 className="mt-2 text-balance text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            {SECTORS_HEADER.title}
          </h2>
          <p className="mt-3 text-pretty text-base leading-relaxed text-muted-foreground">
            {SECTORS_HEADER.subtitle}
          </p>
        </div>

        <div className="mt-10 grid gap-4 sm:grid-cols-2">
          {SECTOR_EXAMPLES.map((ex) => {
            const Icon = ex.icon;
            return (
              <Link
                key={ex.slug}
                href={`/regulations/${encodeURIComponent(ex.slug)}`}
                className="group flex flex-col rounded-2xl border border-border bg-card p-5 shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md sm:p-6"
              >
                <div className="flex items-center gap-3">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                    <Icon className="h-5 w-5" />
                  </span>
                  <span className="text-sm font-semibold text-primary">
                    {ex.sector}
                  </span>
                </div>
                <h3 className="mt-4 text-lg font-bold leading-snug text-foreground">
                  {ex.title}
                </h3>
                <p className="mt-3 flex items-start gap-2 text-sm leading-relaxed text-muted-foreground">
                  <Search className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>«{ex.q}»</span>
                </p>
                <span className="mt-auto inline-flex items-center gap-1.5 pt-4 text-sm font-medium text-primary">
                  اقرأها في المكتبة
                  <ArrowLeft className="h-4 w-4 transition-transform group-hover:-translate-x-0.5" />
                </span>
              </Link>
            );
          })}
        </div>

        <div className="mx-auto mt-12 max-w-2xl text-center">
          <p className="text-pretty text-lg font-semibold leading-relaxed text-foreground sm:text-xl">
            {SECTORS_CLOSING.lead}
          </p>
          <p className="mt-2 text-base text-muted-foreground">
            {SECTORS_CLOSING.body}
          </p>
          <Link
            href={PRIMARY_CTA_HREF}
            className={cn(
              buttonVariants({ size: "lg" }),
              "mt-7 w-full gap-2 text-base font-semibold shadow-sm transition-shadow hover:shadow-md sm:w-auto",
            )}
          >
            {SECTORS_CLOSING.cta}
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </div>
      </div>
    </section>
  );
}
