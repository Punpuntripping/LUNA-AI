import Link from "next/link";
import { ArrowLeft, ChevronDown } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { HERO, HERO_TRUST, PRIMARY_CTA_HREF } from "./content";

/**
 * Above-the-fold hero. Leads with Rayhan's core differentiator — a complete,
 * source-cited legal report — rather than a generic "AI for lawyers" line.
 */
export function LandingHero() {
  return (
    <section className="relative overflow-hidden border-b border-border/40 bg-gradient-to-b from-primary/[0.05] via-background to-background">
      {/* Soft brand glow + faint dotted texture behind the headline. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 -top-32 mx-auto h-80 max-w-4xl rounded-full bg-primary/10 blur-3xl"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-[0.4] [background-image:radial-gradient(theme(colors.border)_1px,transparent_1px)] [background-size:22px_22px] [mask-image:radial-gradient(ellipse_60%_50%_at_50%_0%,#000_55%,transparent_100%)]"
      />

      <div className="relative mx-auto max-w-3xl px-4 pb-14 pt-16 text-center sm:pt-24">
        <span className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/5 px-3.5 py-1 text-xs font-semibold text-primary shadow-sm">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
          {HERO.badge}
        </span>

        <h1 className="mt-6 text-balance text-4xl font-bold leading-[1.25] tracking-tight text-foreground sm:text-[3.25rem] sm:leading-[1.2]">
          {HERO.titleLead}{" "}
          <span className="text-primary">{HERO.titleEmphasis}</span>
        </h1>

        <p className="mx-auto mt-5 max-w-2xl text-pretty text-base leading-relaxed text-muted-foreground sm:text-lg">
          {HERO.subtitle}
        </p>

        <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link
            href={PRIMARY_CTA_HREF}
            className={cn(
              buttonVariants({ size: "lg" }),
              "w-full gap-2 text-base font-semibold shadow-sm transition-shadow hover:shadow-md sm:w-auto",
            )}
          >
            {HERO.primaryCta}
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <a
            href="#showcase"
            className={cn(
              buttonVariants({ variant: "outline", size: "lg" }),
              "w-full gap-2 bg-card/60 text-base backdrop-blur transition-colors hover:bg-card sm:w-auto",
            )}
          >
            {HERO.secondaryCta}
            <ChevronDown className="h-4 w-4" />
          </a>
        </div>

        {/* Data-moat trust strip — corpus scale visible above the fold. */}
        <dl className="mx-auto mt-12 flex max-w-xl flex-wrap items-center justify-center gap-x-8 gap-y-4 border-t border-border/60 pt-7">
          {HERO_TRUST.map((s) => (
            <div key={s.label} className="flex flex-col items-center">
              <dt className="text-2xl font-bold tabular-nums text-foreground sm:text-3xl">
                {s.value}
              </dt>
              <dd className="mt-0.5 text-xs text-muted-foreground sm:text-sm">
                {s.label}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
