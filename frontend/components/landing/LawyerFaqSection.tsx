import Link from "next/link";
import { ArrowLeft, ChevronDown } from "lucide-react";
import { LAWYER_FAQ } from "./content";

/**
 * The lawyer's objections, answered short. Native <details> keeps it a server
 * component (no client JS) and fully indexable.
 */
export function LawyerFaqSection() {
  return (
    <section className="mx-auto max-w-3xl px-4 py-16 sm:py-20">
      <div className="text-center">
        <span className="text-sm font-semibold text-primary">أسئلة المحامين</span>
        <h2 className="mt-2 text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
          قبل أن تبدأ
        </h2>
      </div>

      <div className="mt-8 divide-y divide-border rounded-2xl border border-border bg-card shadow-sm">
        {LAWYER_FAQ.map((item) => (
          <details key={item.q} className="group px-5 py-4 [&_summary::-webkit-details-marker]:hidden">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-base font-semibold text-foreground">
              {item.q}
              <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
            </summary>
            <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
              {item.a}
            </p>
            {item.link && (
              <Link
                href={item.link.href}
                className="mt-2 inline-flex items-center gap-1 text-sm font-medium text-primary underline-offset-4 hover:underline"
              >
                {item.link.label}
                <ArrowLeft className="h-3.5 w-3.5" />
              </Link>
            )}
          </details>
        ))}
      </div>
    </section>
  );
}
