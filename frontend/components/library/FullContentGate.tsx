"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { BookOpen, Loader2, Lock, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button, buttonVariants } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth-store";
import { ArticleBody } from "@/components/library/blocks/ArticleBody";
import { GateCtaSuppressor } from "@/components/library/blocks/GateBanner";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import {
  fetchFullContent,
  fetchLibraryBalance,
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
  balanceCopy,
  refusalCardCopy,
  revealCopy,
  rateLimitedCopy,
  sourceUnavailableCopy,
  staleSessionCopy,
  transportErrorCopy,
  type RefusalCardCopy,
} from "@/lib/library/gate-copy";

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
  children,
}: FullContentGateProps) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [full, setFull] = useState<FullContentPayload | null>(null);
  const [isRevealing, setIsRevealing] = useState(false);
  const [card, setCard] = useState<RefusalCardCopy | null>(null);
  const [balance, setBalance] = useState<LibraryBalance | null>(null);

  // A client-side navigation between two مواد reuses this component instance —
  // drop any revealed content so item B never renders under item A's key.
  useEffect(() => {
    setFull(null);
    setCard(null);
    setIsRevealing(false);
  }, [contentType, fullKey]);

  // Passive balance — «no prompt, but never a silent meter» (§5.1). Reading the
  // allowance costs nothing and charges nothing, so this one IS a mount effect.
  useEffect(() => {
    if (!isAuthenticated || !gated) {
      setBalance(null);
      return;
    }
    let active = true;
    void (async () => {
      const next = await fetchLibraryBalance();
      if (active) setBalance(next);
    })();
    return () => {
      active = false;
    };
  }, [isAuthenticated, gated]);

  const reveal = useCallback(async () => {
    setIsRevealing(true);
    setCard(null);

    const result = await fetchFullContent<FullContentPayload>(
      contentType,
      fullKey,
    );

    if (result.ok) {
      setFull(result.data);
      // An unlock may have just been spent — resync the chip so the next
      // document in this visit shows a truthful balance.
      void fetchLibraryBalance().then(setBalance);
    } else if (result.kind === "refusal") {
      setCard(
        refusalCardCopy({
          reason: result.refusal.reason,
          resetsAt: result.refusal.resets_at,
          storedCount: result.refusal.stored_count,
        }),
      );
    } else if (result.error === "unauthorized" || result.error === "no_token") {
      setCard(staleSessionCopy);
    } else if (result.error === "rate_limited") {
      // NOT a refusal and NOT a network fault. `/library/full` shares one 20/min
      // budget with the reference-source endpoint (D13.2), so this is reachable
      // in normal use — and its copy says explicitly that nothing was charged.
      setCard(rateLimitedCopy);
    } else if (result.error === "not_found") {
      // Unknown slug, unpublished form, or a corpus row that vanished. Retrying
      // cannot help, so it must not render as a retryable transport error.
      setCard(sourceUnavailableCopy);
    } else {
      setCard(transportErrorCopy);
    }

    setIsRevealing(false);
  }, [contentType, fullKey]);

  const revealed = full ? renderFull(kind, full) : null;
  if (revealed) {
    // «المصادر الرسمية» is part of what the unlock buys (user decision
    // 2026-07-28, reversing §1.2's "always shown"). The gated ANON payload sends
    // an empty list, so the page-level <OfficialSources> renders nothing — this
    // is the only place the block reaches a reader, and it must therefore sit
    // INSIDE the revealed branch rather than beside it.
    const sources = (full as { official_sources?: FullOfficialSource[] })
      .official_sources;
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
          isAuthenticated={isAuthenticated}
          isRevealing={isRevealing}
          balance={balance}
          card={card}
          hiddenSections={hiddenSections}
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
  isAuthenticated: boolean;
  isRevealing: boolean;
  balance: LibraryBalance | null;
  card: RefusalCardCopy | null;
  hiddenSections?: number;
  onReveal: () => void;
}

function RevealPanel({
  isAuthenticated,
  isRevealing,
  balance,
  card,
  hiddenSections,
  onReveal,
}: RevealPanelProps) {
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
        className="mt-4 flex flex-col items-center gap-2 rounded-2xl border border-primary/20 bg-gradient-to-b from-primary/5 to-card p-5 text-center shadow-xs"
      >
        <Link
          href="/login"
          className={cn(buttonVariants({ size: "lg" }), "shadow-sm")}
        >
          <Lock aria-hidden="true" className="h-4 w-4 shrink-0" />
          {revealCopy.anonCta}
        </Link>
        <p className="text-xs leading-relaxed text-muted-foreground">
          {revealCopy.anonHint(hiddenSections)}
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
        {isRevealing ? revealCopy.loadingCta : revealCopy.authedCta}
      </Button>

      <BalanceChip balance={balance} />

      <p className="text-xs leading-relaxed text-muted-foreground">
        {revealCopy.authedHint}
      </p>
    </section>
  );
}

/**
 * The passive meter beside the reveal action. Silent when the allowance could
 * not be read (anonymous, locked account, or a failed usage call) — a wrong
 * number beside a spend button is worse than no number.
 */
function BalanceChip({ balance }: { balance: LibraryBalance | null }) {
  if (!balance) return null;

  const label =
    balance.limit === null
      ? balanceCopy.unlimited
      : balance.remaining !== null && balance.remaining > 0
        ? balanceCopy.remaining(balance.remaining, balance.limit)
        : balanceCopy.exhausted;

  const renews =
    balance.limit === null ? "" : balanceCopy.renewsOn(balance.resets_at);

  return (
    <p className="flex flex-wrap items-center justify-center gap-x-2 gap-y-0.5 text-xs text-text-secondary">
      <span className="rounded-full bg-pill px-2.5 py-0.5 font-medium tabular-nums text-pill-fg">
        {label}
      </span>
      {renews && <span className="text-muted-foreground">{renews}</span>}
    </p>
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
