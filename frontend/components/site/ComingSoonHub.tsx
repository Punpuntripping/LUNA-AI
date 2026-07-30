import Link from "next/link";
import { Clock } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ComingSoonHubProps {
  title: string;
  description: string;
  /** Optional escape hatch to a live surface so the placeholder isn't a dead end. */
  cta?: { href: string; label: string };
}

/**
 * Minimal placeholder body for a nav hub whose real content hasn't shipped yet
 * (المكتبة القانونية, اكتشف). The route + header/footer chrome exist so the
 * global nav links resolve today; the page stays deliberately empty — a «قريباً»
 * state — until its phase fills it. Pages using this set `robots: noindex` so
 * Google isn't handed a thin page.
 */
export function ComingSoonHub({ title, description, cta }: ComingSoonHubProps) {
  return (
    <main className="mx-auto flex min-h-[60vh] max-w-2xl flex-col items-center justify-center px-4 py-20 text-center">
      <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-3 py-1 text-xs font-medium text-muted-foreground">
        <Clock className="h-3.5 w-3.5" />
        قريباً
      </span>
      <h1 className="mt-5 text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
        {title}
      </h1>
      <p className="mt-4 text-base leading-relaxed text-muted-foreground">
        {description}
      </p>
      {cta && (
        <Link
          href={cta.href}
          className={cn(
            buttonVariants({ variant: "outline" }),
            "mt-8 text-sm font-semibold",
          )}
        >
          {cta.label}
        </Link>
      )}
    </main>
  );
}
