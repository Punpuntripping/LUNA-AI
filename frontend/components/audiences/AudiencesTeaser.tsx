import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { AUDIENCES_TEASER, TEASER_PERSONAS } from "./content";

/**
 * Compact homepage block that opens the breadth story and routes prospects to
 * the full «ريحان يستهدف مين؟» page. Sits after the About section on `/`.
 */
export function AudiencesTeaser() {
  return (
    <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
      <div className="mx-auto max-w-2xl text-center">
        <span className="text-sm font-semibold text-primary">
          {AUDIENCES_TEASER.eyebrow}
        </span>
        <h2 className="mt-2 text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
          {AUDIENCES_TEASER.title}
        </h2>
        <p className="mt-3 text-base leading-relaxed text-muted-foreground">
          {AUDIENCES_TEASER.subtitle}
        </p>
      </div>

      {/* Persona pills */}
      <div className="mx-auto mt-8 flex max-w-3xl flex-wrap justify-center gap-3">
        {TEASER_PERSONAS.map((p) => {
          const Icon = p.icon;
          return (
            <span
              key={p.label}
              className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-4 py-2 text-sm font-medium text-foreground shadow-sm"
            >
              <Icon className="h-4 w-4 text-primary" />
              {p.label}
            </span>
          );
        })}
      </div>

      <div className="mt-9 flex justify-center">
        <Link
          href={AUDIENCES_TEASER.href}
          className={cn(
            buttonVariants({ variant: "outline", size: "lg" }),
            "gap-2 bg-card/60 text-base backdrop-blur transition-colors hover:bg-card",
          )}
        >
          {AUDIENCES_TEASER.cta}
          <ArrowLeft className="h-4 w-4" />
        </Link>
      </div>
    </section>
  );
}
