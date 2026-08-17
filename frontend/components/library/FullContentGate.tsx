"use client";

import { type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BookOpen, Loader2, Lock, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button, buttonVariants } from "@/components/ui/button";
import { ArticleBody } from "@/components/library/blocks/ArticleBody";
import { GateCtaSuppressor } from "@/components/library/blocks/GateBanner";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import {
  BalanceChip,
  useLibraryReveal,
  useSharedLibraryReveal,
} from "@/components/library/LibraryReveal";
import {
  type FullContentType,
  type FullContentPayload,
  type FullRegulation,
  type FullArticle,
  type FullCircular,
  type FullForm,
  type FullOfficialSource,
  type LibraryBalance,
} from "@/lib/library/full-content";
import { OfficialSources } from "@/components/library/blocks/OfficialSources";
import {
  revealCopy,
  revealCopyFor,
  type RefusalCardCopy,
  type RevealTarget,
} from "@/lib/library/gate-copy";
import {
  trackGateCtaClick,
  useGateImpression,
} from "@/components/analytics/useGateImpression";

/**
 * The shape of the full payload for a given `kind`:
 *   sections → regulation ({ sections })   text → circular ({ text })
 *   article  → article ({ text, sharh_md }) body_md → form ({ body_md })
 */
type GateKind = "sections" | "text" | "body_md" | "article";

interface FullContentGateProps {
  contentType: FullContentType;
  /** The item key the authed endpoint resolves (the page's own slug). */
  fullKey: string;
  kind: GateKind;
  /**
   * Is anything actually behind the gate on THIS page? Pages pass their own gate
   * flags (`gate === 'gated'`, `is_truncated`, `hidden_section_count > 0`, a شرح
   * teaser …). When false the document already renders in full, so no reveal
   * action is offered and no unlock can be spent on nothing. Defaults to true
   * (fail towards offering the action, never towards a silent charge).
   */
  gated?: boolean;
  /**
   * How many sections sit entirely behind the gate, when the page knows. Used
   * only to make the anonymous CTA concrete («٥ أقسام إضافية بانتظارك»); the
   * string itself lives in `gate-copy.ts` like every other gate string.
   */
  hiddenSections?: number;
  /**
   * What the reveal actually buys, which is what its CTA must say. `"content"`
   * (default) → «اعرض النص كاملاً». `"sharh"` → «اعرض الشرح كاملاً», for a مادة
   * whose نص already renders in full and whose only gated region is the AI شرح.
   */
  revealTarget?: RevealTarget;
  /** The ANON, gate-truncated render — shown on server + for signed-out readers. */
  children: ReactNode;
}

/**
 * REVEAL-triggered authed reveal for a gated library region (plan §5.1).
 *
 * The server always ships `children` (the anon, gate-truncated body — SEO-correct,
 * no cloaking). Below it this component renders ONE explicit action, and only a
 * click on that action spends an unlock and swaps in the full document.
 *
 * ⚠ This used to auto-fetch the moment `isAuthenticated` flipped true. That is
 * the bug §5.1 forbids: with the charge on the page view, a signed-in user
 * skimming ten judgment summaries would burn ten unlocks without deliberately
 * reading a single full document — destroying the free summary layer that does
 * the SEO and engagement work. The click IS the consent: no confirmation dialog
 * and no stored decision (the ledger row is the record).
 *
 * Re-visits are free by construction (`ON CONFLICT DO NOTHING` server-side), so
 * revealing a page the reader already unlocked simply works — entitlement is
 * NEVER cached client-side, and nothing here tries to predict the answer.
 *
 * "ONE action" is per REVEAL, not per component. A page whose unlock buys more
 * than one region — the judgment page, where the same response carries «ملخص
 * ريحان» and the full ruling — wraps both in a `LibraryRevealProvider`; this
 * component then shares that state instead of opening a second, separately
 * charged one. See `components/library/LibraryReveal.tsx`.
 *
 * Failure is layered, and the layers are distinguishable — that is the other half
 * of the PART 5 bug, where the old code returned `null` on every non-OK response
 * and an exhausted quota looked exactly like being logged out:
 *   402         → the refusal card, driven by `reason`
 *   401/403     → «انتهت جلستك» (never a redirect — this is a public page)
 *   network/5xx → a retryable «تعذّر فتح هذا المصدر»
 * In every one of those states `children` stays exactly as rendered, and so does
 * everything in the never-gated class around it — `OfficialSources` above all
 * (§1.2: the official source URL is always shown, gated or not).
 */
export function FullContentGate({
  contentType,
  fullKey,
  kind,
  gated = true,
  hiddenSections,
  revealTarget = "content",
  children,
}: FullContentGateProps) {
  // A page with a SECOND trigger for the same unlock (the judgment page's «ملخص
  // ريحان» button) hoists this state into a `LibraryRevealProvider`; both
  // surfaces then read and write one reveal. Everywhere else there is no
  // provider and this component owns its state exactly as it always did — the
  // fallback instance skips its balance read when a shared one is in charge, so
  // `/usage` is never fetched twice.
  const shared = useSharedLibraryReveal();
  const own = useLibraryReveal({
    contentType,
    fullKey,
    gated,
    enabled: !shared,
  });
  const { isAuthenticated, full, isRevealing, card, balance, reveal } =
    shared ?? own;

  const revealed = full ? renderFull(kind, full) : null;
  if (revealed) {
    // «المصادر الرسمية» is part of what the unlock buys (user decision
    // 2026-07-28, reversing §1.2's "always shown"). The gated ANON payload sends
    // an empty list, so the page-level <OfficialSources> renders nothing — this
    // is the only place the block reaches a reader, and it must therefore sit
    // INSIDE the revealed branch rather than beside it.
    //
    // ⚠ `gated &&` is what keeps that "only place" true. An OPEN item's anon
    // payload already PUBLISHES its sources, so the page renders the block
    // itself; the reveal ships them regardless, and printing them again here
    // would duplicate it. Unreachable while a reveal could only be triggered by
    // this component's own gated panel — reachable now that a page can reveal
    // from elsewhere (the judgment «ملخص ريحان» button on a ruling short enough
    // to ship whole).
    const sources = gated
      ? (full as { official_sources?: FullOfficialSource[] }).official_sources
      : undefined;
    return (
      <>
        {revealed}
        {sources && sources.length > 0 && (
          <OfficialSources
            sources={sources.map((s) => ({ label: s.title, href: s.href }))}
          />
        )}
      </>
    );
  }

  return (
    <>
      {/* Renders no DOM — it only tells the GateBanners inside the server-rendered
          body that THIS component owns the conversion CTA, so the reader never
          sees two stacked calls to action. */}
      <GateCtaSuppressor>{children}</GateCtaSuppressor>
      {gated && (
        <RevealPanel
          contentType={contentType}
          isAuthenticated={isAuthenticated}
          isRevealing={isRevealing}
          balance={balance}
          card={card}
          hiddenSections={hiddenSections}
          revealTarget={revealTarget}
          onReveal={reveal}
        />
      )}
    </>
  );
}

// ------------------------------------------------------------------
// The reveal panel — the single conversion surface on a gated document
// ------------------------------------------------------------------

interface RevealPanelProps {
  /** Analytics only — the `content_type` dimension on the gate events. */
  contentType: FullContentType;
  isAuthenticated: boolean;
  isRevealing: boolean;
  balance: LibraryBalance | null;
  card: RefusalCardCopy | null;
  hiddenSections?: number;
  revealTarget: RevealTarget;
  onReveal: () => void;
}

function RevealPanel({
  contentType,
  isAuthenticated,
  isRevealing,
  balance,
  card,
  hiddenSections,
  revealTarget,
  onReveal,
}: RevealPanelProps) {
  // ── Analytics (product_analytics.md §5.3) ─────────────────────────────────
  // Question 4 counts the ANONYMOUS branch only. The authed branch below is a
  // metered reveal and the refusal card above it is an UPGRADE surface pointing
  // at /pricing — neither is a "decided not to sign in" moment, and folding them
  // into the same denominator would make the signup funnel unreadable.
  //
  // The ref is attached to the anon panel alone, so nothing is reported on any
  // other branch. Hooks stay unconditional (they run before every early return).
  const pathname = usePathname() ?? "";
  const anonPanelRef = useGateImpression("full_content", { contentType });

  // «النص» vs «الشرح» — the CTA has to name what is actually behind the gate.
  const copy = revealCopyFor(revealTarget);

  // A refusal REPLACES the action: re-offering a button that cannot succeed is
  // exactly the "trick" feeling §5.1 forbids. A retryable transport error is the
  // one exception — it carries its own retry affordance instead of a link CTA.
  if (card) {
    return (
      <RefusalCard copy={card} onRetry={card.ctaHref ? undefined : onReveal} />
    );
  }

  if (!isAuthenticated) {
    return (
      <section
        dir="rtl"
        // Tagged for the anon conversion POPUP's gate 5 (T6): while this panel
        // is on screen the popup drops its fire, so a reader looking at «سجّل
        // مجاناً لعرض النص كاملاً» never gets a modal saying the same thing.
        data-anon-cta
        // `gate_view` fires when this panel is genuinely on screen, not when it
        // renders — it sits at the END of a document that can run several
        // viewports (T6).
        ref={anonPanelRef}
        className="mt-4 flex flex-col items-center gap-2 rounded-2xl border border-primary/20 bg-gradient-to-b from-primary/5 to-card p-5 text-center shadow-xs"
      >
        <Link
          href="/login"
          // The CTA reads «سجّل مجاناً…», so the intent is register even though
          // the href is the bare /login (the form's own toggle takes it from
          // there). Tracked before navigation, never awaited.
          onClick={() => trackGateCtaClick("full_content", pathname, "register")}
          className={cn(buttonVariants({ size: "lg" }), "shadow-sm")}
        >
          <Lock aria-hidden="true" className="h-4 w-4 shrink-0" />
          {copy.anonCta}
        </Link>
        <p className="text-xs leading-relaxed text-muted-foreground">
          {copy.anonHint(hiddenSections)}
        </p>
      </section>
    );
  }

  return (
    <section
      dir="rtl"
      className="mt-4 flex flex-col items-center gap-2 rounded-2xl border border-primary/20 bg-gradient-to-b from-primary/5 to-card p-5 text-center shadow-xs"
    >
      <Button
        type="button"
        size="lg"
        onClick={onReveal}
        disabled={isRevealing}
        className="shadow-sm"
        data-testid="library-reveal"
      >
        {isRevealing ? (
          <Loader2
            aria-hidden="true"
            className="h-4 w-4 shrink-0 animate-spin"
          />
        ) : (
          <BookOpen aria-hidden="true" className="h-4 w-4 shrink-0" />
        )}
        {isRevealing ? copy.loadingCta : copy.authedCta}
      </Button>

      <BalanceChip balance={balance} />

      <p className="text-xs leading-relaxed text-muted-foreground">
        {copy.authedHint}
      </p>
    </section>
  );
}

/**
 * The refusal card (D14). Framed as a plan feature, never a paywall slap (§1.2):
 * neutral primary tone — no destructive colour, no scolding — and always a way
 * forward. The document's never-gated layer (summary, metadata, TOC, citation
 * mesh and `OfficialSources`) is untouched behind it.
 */
function RefusalCard({
  copy,
  onRetry,
}: {
  copy: RefusalCardCopy;
  onRetry?: () => void;
}) {
  return (
    <section
      dir="rtl"
      role="status"
      data-testid="library-refusal"
      className="mt-4 rounded-2xl border border-primary/20 bg-gradient-to-b from-primary/5 to-card p-6 text-center shadow-xs"
    >
      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary ring-1 ring-primary/15">
        <Sparkles aria-hidden="true" className="h-5 w-5" />
      </div>
      <h2 className="text-sm font-bold leading-snug text-foreground">
        {copy.title}
      </h2>
      <p className="mx-auto mt-1.5 max-w-sm text-xs leading-relaxed text-muted-foreground">
        {copy.body}
      </p>
      {copy.ctaHref && copy.ctaLabel ? (
        <Link
          href={copy.ctaHref}
          className={cn(
            buttonVariants({ size: "default" }),
            "mt-4 w-full shadow-sm sm:w-auto sm:px-8",
          )}
        >
          {copy.ctaLabel}
        </Link>
      ) : onRetry ? (
        <Button
          type="button"
          variant="outline"
          onClick={onRetry}
          className="mt-4 w-full sm:w-auto sm:px-8"
        >
          {revealCopy.retryCta}
        </Button>
      ) : null}
    </section>
  );
}

// ------------------------------------------------------------------
// Full-content renderers (payload shapes: lib/library/full-content.ts)
// ------------------------------------------------------------------

/**
 * Returns null when the payload carries nothing to show — the caller then keeps
 * the anon render rather than blanking the page.
 */
function renderFull(kind: GateKind, full: FullContentPayload): ReactNode {
  if (kind === "sections") {
    const { sections } = full as FullRegulation;
    if (!sections?.length) return null;
    return (
      <div className="space-y-8">
        {sections.map((section) => (
          <section
            key={section.id}
            id={`sec-${section.id}`}
            className="scroll-mt-24 space-y-3"
          >
            {section.title && (
              <h2 className="text-lg font-bold text-foreground">
                {section.title}
              </h2>
            )}
            <ArticleBody
              visibleText={section.text}
              plain
              dedupeHeading={section.title ?? undefined}
            />
          </section>
        ))}
      </div>
    );
  }

  if (kind === "text") {
    const { text } = full as FullCircular;
    if (!text) return null;
    return <ArticleBody visibleText={text} plain />;
  }

  if (kind === "article") {
    const { text, sharh_md } = full as FullArticle;
    if (!text && !sharh_md) return null;
    return (
      <div className="space-y-6">
        <section id="article-body" className="scroll-mt-24">
          <ArticleBody visibleText={text} plain />
        </section>
        {sharh_md && (
          <section
            dir="rtl"
            className="space-y-3 rounded-2xl border border-primary/30 bg-primary/5 p-5"
          >
            <h2 className="flex items-center gap-2 text-base font-bold text-foreground">
              <Sparkles
                aria-hidden="true"
                className="h-5 w-5 shrink-0 text-primary"
              />
              شرح المادة
            </h2>
            <div className="text-sm leading-relaxed text-foreground">
              <MarkdownRenderer content={sharh_md} />
            </div>
          </section>
        )}
      </div>
    );
  }

  // kind === "body_md" — form template body (markdown).
  const { body_md } = full as FullForm;
  if (!body_md) return null;
  return <ArticleBody visibleText={body_md} />;
}
