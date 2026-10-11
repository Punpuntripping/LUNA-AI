import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { AUDIENCES } from "@/components/audiences/content";
import { WHO_BENEFITS_HEADER } from "./content";

/**
 * «من يستفيد من ريحان؟» — lawyer-first audience block. The lawyer persona is
 * the featured card; the other personas sit beneath it as smaller cards. Each
 * deep-links to its block on /audiences (persona copy is that page's SSoT).
 */
export function WhoBenefitsSection() {
  const [lawyer, ...others] = AUDIENCES;
  const LawyerIcon = lawyer.icon;
  const hrefFor = (id: string) => `${WHO_BENEFITS_HEADER.href}#${id}`;

  return (
    <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
      <div className="mx-auto max-w-2xl text-center">
        <h2 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
          {WHO_BENEFITS_HEADER.title}
        </h2>
        <p className="mt-3 text-pretty text-base leading-relaxed text-muted-foreground">
          {WHO_BENEFITS_HEADER.subtitle}
        </p>
      </div>

      {/* Featured: the lawyer. */}
      <Link
        href={hrefFor(lawyer.id)}
        className="group mt-10 flex flex-col gap-5 rounded-3xl border border-primary/30 bg-primary/[0.05] p-6 shadow-sm transition-shadow hover:shadow-md sm:flex-row sm:items-center sm:p-8"
      >
        <span className="flex h-16 w-16 shrink-0 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
          <LawyerIcon className="h-8 w-8" />
        </span>
        <div className="min-w-0 flex-1">
          <span className="inline-flex rounded-full border border-primary/30 bg-background/60 px-3 py-0.5 text-xs font-semibold text-primary">
            {WHO_BENEFITS_HEADER.lawyerBadge}
          </span>
          <h3 className="mt-2 text-2xl font-bold text-foreground">
            {lawyer.title}
          </h3>
          <p className="mt-2 text-pretty text-base leading-relaxed text-muted-foreground">
            {lawyer.tagline}
          </p>
        </div>
        <ArrowLeft className="hidden h-5 w-5 shrink-0 text-primary transition-transform group-hover:-translate-x-1 sm:block" />
      </Link>

      {/* Secondary personas. */}
      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        {others.map((a) => {
          const Icon = a.icon;
          return (
            <Link
              key={a.id}
              href={hrefFor(a.id)}
              className="group flex flex-col rounded-2xl border border-border bg-card p-5 shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md"
            >
              <div className="flex items-center gap-2.5">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Icon className="h-4 w-4" />
                </span>
                <h3 className="text-base font-bold text-foreground">{a.title}</h3>
              </div>
              <p className="mt-3 line-clamp-3 text-sm leading-relaxed text-muted-foreground">
                {a.tagline}
              </p>
              <span className="mt-auto inline-flex items-center gap-1 pt-3 text-sm font-medium text-primary">
                {WHO_BENEFITS_HEADER.more}
                <ArrowLeft className="h-3.5 w-3.5" />
              </span>
            </Link>
          );
        })}
      </div>

      <div className="mt-8 flex justify-center">
        <Link
          href={WHO_BENEFITS_HEADER.href}
          className={cn(
            buttonVariants({ variant: "outline", size: "lg" }),
            "gap-2 bg-card/60 text-base transition-colors hover:bg-card",
          )}
        >
          {WHO_BENEFITS_HEADER.allCta}
          <ArrowLeft className="h-4 w-4" />
        </Link>
      </div>
    </section>
  );
}
