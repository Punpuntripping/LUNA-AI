import Link from "next/link";
import { ArrowLeft } from "lucide-react";
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
    <section className="mx-auto max-w-5xl px-4 py-14 sm:py-16">
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
        className="group mt-8 flex flex-col gap-5 rounded-3xl border border-primary/30 bg-primary/[0.05] p-6 shadow-sm transition-shadow hover:shadow-md sm:flex-row sm:items-center sm:p-8"
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

      {/* Secondary personas — compact rows, detail lives on /audiences. */}
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        {others.map((a) => {
          const Icon = a.icon;
          return (
            <Link
              key={a.id}
              href={hrefFor(a.id)}
              className="group flex items-center gap-2.5 rounded-xl border border-border bg-card px-4 py-3.5 shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <Icon className="h-4 w-4" />
              </span>
              <h3 className="flex-1 text-sm font-bold text-foreground">{a.title}</h3>
              <ArrowLeft className="h-4 w-4 shrink-0 text-primary/60 transition-transform group-hover:-translate-x-0.5" />
            </Link>
          );
        })}
      </div>
    </section>
  );
}
