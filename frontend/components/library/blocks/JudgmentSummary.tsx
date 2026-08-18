"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BookOpen, Loader2, Lock, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { loginHref } from "@/lib/safe-next";
import { Button, buttonVariants } from "@/components/ui/button";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import {
  BalanceChip,
  useSharedLibraryReveal,
} from "@/components/library/LibraryReveal";
import type { FullJudgment } from "@/lib/library/full-content";
import { judgmentSummaryCopy, revealCopy } from "@/lib/library/gate-copy";
import {
  trackGateCtaClick,
  useGateImpression,
} from "@/components/analytics/useGateImpression";

/**
 * «ملخص ريحان» on a /judgments/{slug} page — the structured AI summary of the
 * ruling (`cases.summary`: ## الملخص / الوقائع / المطالبات / تسبيب الحكم /
 * منطوق الحكم), which is NOT the ~250-char `short_summary` lead the page already
 * prints for free above the text.
 *
 * TWO components, ONE state. `JudgmentSummaryButton` is the action, and it lives
 * in the المعلومات الأساسية card's footer; `JudgmentSummaryPanel` is the revealed
 * summary and renders directly beneath that card. Both read the SAME
 * `LibraryRevealProvider` the page's `FullContentGate` reads, which is the whole
 * point of the provider: one `/library/full/judgment/{slug}` response carries the
 * summary AND the full ruling, so a single click spends a single unlock and opens
 * both. `judgmentSummaryCopy.hint` says so out loud — a button that also unlocks
 * the whole judgment must not do that silently.
 *
 * Both render `null` outside a provider, so neither can be dropped onto a page
 * that has not wired the shared reveal and quietly do nothing on click.
 */

interface JudgmentSummaryProps {
  /**
   * Does this ruling HAVE a summary (`JudgmentDoc.has_summary`)? False for ~18
   * of 30,531 rulings, and there the button must not render at all — an unlock
   * is never spendable on nothing.
   */
  hasSummary: boolean;
  /**
   * Show the allowance chip beside this button. True exactly when the body gate
   * below will NOT render one — a ruling short enough to ship whole (571 of
   * 30,531 measured on prod) has no gate panel, which would leave this button as
   * a metered click with no meter anywhere on the page (§5.1). On a gated ruling
   * the chip belongs to the body panel and repeating it here is noise.
   */
  showBalance?: boolean;
}

/** The revealed «ملخص ريحان», if the shared reveal has produced one. */
function revealedSummary(full: unknown): string {
  const summary = (full as FullJudgment | null)?.summary_md;
  return typeof summary === "string" ? summary.trim() : "";
}

export function JudgmentSummaryButton({
  hasSummary,
  showBalance = false,
}: JudgmentSummaryProps) {
  const reveal = useSharedLibraryReveal();

  // ── Analytics (product_analytics.md §5.3) ─────────────────────────────────
  // The ANONYMOUS branch only. For a signed-in reader this button is a metered
  // reveal, not a signup pitch, and the refusal branch is an upgrade surface —
  // neither can produce a `register|login` click, so neither belongs in the
  // question-4 denominator. Hooks run before the early return; the ref attaches
  // on the anon branch alone.
  //
  // ⚠ This button sits in the المعلومات الأساسية card while the page's
  // `FullContentGate` panel sits at the bottom of the ruling, so ONE judgment
  // page can legitimately report two impressions of two different `gate_kind`s.
  // That is per-surface conversion, which is what §6.6 groups by — it is not a
  // stacked CTA (the two are viewports apart) and not the suppression case
  // `GateCtaSuppressor` exists for.
  const pathname = usePathname() ?? "";
  const anonCtaRef = useGateImpression("judgment_summary", {
    contentType: "judgment",
  });

  if (!reveal || !hasSummary) return null;

  const {
    isAuthenticated,
    full,
    isRevealing,
    card,
    balance,
    reveal: onReveal,
  } = reveal;

  // Revealed — the panel below the card now carries the summary, so the action
  // has nothing left to offer and steps out of the way (the card's divider is
  // `empty:hidden` for exactly this).
  if (revealedSummary(full)) return null;

  // A refusal REPLACES the action here too, but compactly: the body gate at the
  // bottom of the page owns the full-size refusal card, and two identical cards
  // for one refusal is the stacked-CTA problem in a different costume.
  if (card) {
    return (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <p className="text-xs font-medium leading-relaxed text-text-secondary">
          {card.title}
        </p>
        {card.ctaHref && card.ctaLabel ? (
          <Link
            href={card.ctaHref}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
          >
            {card.ctaLabel}
          </Link>
        ) : (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onReveal}
            disabled={isRevealing}
          >
            {revealCopy.retryCta}
          </Button>
        )}
      </div>
    );
  }

  // Anonymous — straight to /login, the API is never touched (and never charged).
  if (!isAuthenticated) {
    return (
      <div
        ref={anonCtaRef}
        className="flex flex-wrap items-center gap-x-3 gap-y-2"
      >
        <Link
          // «افتح حسابك المجاني لعرض ملخص الحكم» — a signup intent, and the
          // href now carries it. The bare "/login" it replaces opened the form
          // on SIGN IN, which never fires `signup_started`, and dropped the
          // reader on /chat instead of the حكم whose ملخص they came for.
          href={loginHref(pathname, { register: true })}
          onClick={() =>
            trackGateCtaClick("judgment_summary", pathname, "register")
          }
          className={cn(buttonVariants(), "shadow-xs")}
        >
          <Lock aria-hidden="true" className="h-4 w-4 shrink-0" />
          {judgmentSummaryCopy.cta}
        </Link>
        <p className="text-xs leading-relaxed text-muted-foreground">
          {judgmentSummaryCopy.anonHint}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <Button
        type="button"
        onClick={onReveal}
        disabled={isRevealing}
        className="shadow-xs"
        data-testid="judgment-summary-reveal"
      >
        {isRevealing ? (
          <Loader2 aria-hidden="true" className="h-4 w-4 shrink-0 animate-spin" />
        ) : (
          <BookOpen aria-hidden="true" className="h-4 w-4 shrink-0" />
        )}
        {isRevealing ? judgmentSummaryCopy.loadingCta : judgmentSummaryCopy.cta}
      </Button>
      <p className="text-xs leading-relaxed text-muted-foreground">
        {judgmentSummaryCopy.hint}
      </p>
      {showBalance && <BalanceChip balance={balance} />}
    </div>
  );
}

/**
 * The revealed summary itself. Markdown (not `LeadSummary`) because the text is
 * a real `##` + `-` document and the bullet lists have to render as lists — the
 * same reason the شرح panel uses `MarkdownRenderer`. Styled as the AI panel that
 * شرح المادة already established, so «written by ريحان» reads the same in both
 * wings.
 */
export function JudgmentSummaryPanel() {
  const reveal = useSharedLibraryReveal();
  const summary = revealedSummary(reveal?.full ?? null);
  if (!summary) return null;

  return (
    <section
      dir="rtl"
      id="judgment-rayhan-summary"
      className="scroll-mt-24 space-y-3 rounded-2xl border border-primary/30 bg-primary/5 p-5"
    >
      <h2 className="flex items-center gap-2 text-base font-bold text-foreground">
        <Sparkles aria-hidden="true" className="h-5 w-5 shrink-0 text-primary" />
        {judgmentSummaryCopy.panelTitle}
      </h2>
      <div className="text-sm leading-relaxed text-foreground">
        <MarkdownRenderer content={summary} />
      </div>
    </section>
  );
}
