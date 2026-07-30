"use client";

import { createContext, useContext, type ReactNode } from "react";
import Link from "next/link";
import { Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import type { GateBannerProps } from "@/types/library";

// Cycled bar widths so the decorative skeleton reads like real ragged text.
const BAR_WIDTHS = ["100%", "92%", "84%", "96%", "78%", "88%", "90%"] as const;

/**
 * True inside a `FullContentGate` (access tiers Phase B). See `GateCtaSuppressor`.
 */
const GateCtaSuppressionContext = createContext(false);

/**
 * Marks a subtree as "a FullContentGate owns the conversion CTA here", so every
 * GateBanner inside it degrades to decorative bars.
 *
 * Why a context and not a prop: the gated body is SERVER-rendered and handed to
 * the gate as opaque `children`, so the gate cannot reach into it. A client
 * Provider wrapping server children still reaches the client leaves nested in
 * them, and — unlike a wrapper `<div>` — renders no DOM, so nothing shifts and
 * the pages' `space-y-*` rhythm is untouched.
 *
 * The rule it enforces (§5.1): a gated document shows exactly ONE action, and it
 * is tier-correct — «سجّل مجاناً لعرض النص كاملاً» for an anonymous reader,
 * «اعرض النص كاملاً» + the balance chip for a signed-in one. Before this, an
 * anonymous reader saw the banner card AND the gate's own CTA stacked, and a
 * signed-in reader was told to «سجّل مجاناً» for an account they already have.
 */
export function GateCtaSuppressor({ children }: { children: ReactNode }) {
  return (
    <GateCtaSuppressionContext.Provider value={true}>
      {children}
    </GateCtaSuppressionContext.Provider>
  );
}

/**
 * The signup gate that renders right after a server-truncated ArticleBody, or
 * standalone for a document's hidden-section count.
 *
 * The hidden text is NEVER in the DOM — these bars are PURELY DECORATIVE divs
 * (not CSS-blurred real content). A soft gradient fade lifts the conversion
 * card off the ragged skeleton, with a lock badge + primary CTA.
 *
 * `barsOnly` renders JUST the faded skeleton bars, with NO CTA card — so a
 * page with several truncated sections can show the bars everywhere while a
 * single document-level GateBanner owns the one conversion card (no stacked,
 * back-to-back CTA cards).
 *
 * ACCESS TIERS (Phase B): inside a `FullContentGate` the CTA card is suppressed
 * (see `GateCtaSuppressor`) and only the bars render — the gate owns the single,
 * tier-correct action below them. Outside one, the banner keeps its original
 * standalone signup card. Every real GateBanner on the site sits inside a
 * FullContentGate, so a gated document has exactly ONE conversion surface.
 *
 * Suppression is a render-time context read, not an auth read, so SSR and the
 * client agree and nothing shifts on hydration. Googlebot gets the same markup
 * as a signed-out human — no cloaking.
 */
export function GateBanner({
  hiddenPlaceholderLines,
  ctaHref,
  ctaLabel = "سجّل مجاناً لعرض المحتوى كاملاً",
  barsOnly,
  className,
}: GateBannerProps) {
  const ctaSuppressed = useContext(GateCtaSuppressionContext);
  const lines = Math.min(Math.max(4, hiddenPlaceholderLines), 7);

  // Decorative skeleton bars — aria-hidden, no textual content. Shared by both
  // the full card and the bars-only variant.
  const bars = (
    <div
      aria-hidden="true"
      className="space-y-3 [mask-image:linear-gradient(to_bottom,black,55%,transparent)]"
    >
      {Array.from({ length: lines }).map((_, index) => (
        <div
          key={index}
          className="h-3.5 rounded-full bg-gradient-to-l from-surface-3 via-surface-2 to-surface-3"
          style={{ width: BAR_WIDTHS[index % BAR_WIDTHS.length] }}
        />
      ))}
    </div>
  );

  if (barsOnly || ctaSuppressed) {
    return (
      <div dir="rtl" className={cn("relative mt-3 select-none", className)}>
        {bars}
      </div>
    );
  }

  return (
    <div dir="rtl" className={cn("relative mt-3 select-none", className)}>
      {bars}

      {/* CTA card overlaid on the fading bars — standalone usage only. */}
      <div
        data-gate-cta="anon"
        className="absolute inset-0 flex items-end justify-center pb-1"
      >
        <div className="w-full max-w-md rounded-2xl border border-primary/20 bg-gradient-to-b from-card to-surface-2 p-6 text-center shadow-md">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary ring-1 ring-primary/15">
            <Lock aria-hidden="true" className="h-5 w-5" />
          </div>
          <p className="text-sm font-bold leading-snug text-foreground">
            {ctaLabel}
          </p>
          <p className="mx-auto mt-1.5 max-w-xs text-xs leading-relaxed text-muted-foreground">
            أنشئ حسابك المجاني لقراءة النص كاملاً مع الشرح والمراجع.
          </p>
          <Link
            href={ctaHref}
            className={cn(
              buttonVariants({ size: "default" }),
              "mt-4 w-full shadow-sm sm:w-auto sm:px-8",
            )}
          >
            سجّل مجاناً
          </Link>
        </div>
      </div>
    </div>
  );
}
