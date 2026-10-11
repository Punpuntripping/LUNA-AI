import { Quote } from "lucide-react";
import { TESTIMONIALS, TESTIMONIALS_HEADER } from "./content";

/** «تجارب المستخدمين» — real lawyer reviews, verbatim (spelling-only edits). */
export function TestimonialsSection() {
  return (
    <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
      <div className="mx-auto max-w-2xl text-center">
        <h2 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
          {TESTIMONIALS_HEADER.title}
        </h2>
        <p className="mt-3 text-base leading-relaxed text-muted-foreground">
          {TESTIMONIALS_HEADER.subtitle}
        </p>
      </div>

      <div className="mt-10 grid gap-5 md:grid-cols-2">
        {TESTIMONIALS.map((t) => (
          <figure
            key={t.name}
            className="flex flex-col rounded-2xl border border-border bg-card p-6 shadow-sm sm:p-7"
          >
            <Quote className="h-7 w-7 text-primary/40" aria-hidden="true" />
            <blockquote className="mt-4 flex-1 text-pretty text-base leading-loose text-foreground">
              «{t.quote}»
            </blockquote>
            <figcaption className="mt-6 border-t border-border pt-4 text-sm font-semibold text-primary">
              {t.name}
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}
