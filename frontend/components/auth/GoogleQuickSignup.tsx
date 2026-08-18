"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { supabase } from "@/lib/supabase";
import { LEGAL_ROUTES } from "@/lib/legal";
import { DEFAULT_NEXT, safeNext } from "@/lib/safe-next";
import { googleGateCopy } from "@/lib/library/gate-copy";
import { trackGateCtaClick } from "@/components/analytics/useGateImpression";
import type { GateKind } from "@/lib/analytics/events";

// Google "G" mark — multicolor official logo. Owned here (not LoginForm) so
// every conversion surface renders the same mark.
export function GoogleIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.76h3.56c2.08-1.92 3.28-4.74 3.28-8.09Z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.56-2.76c-.98.66-2.24 1.06-3.72 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23Z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84Z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1A11 11 0 0 0 2.18 7.06l3.66 2.84C6.71 7.3 9.14 5.38 12 5.38Z"
      />
    </svg>
  );
}

interface GoogleQuickSignupProps {
  /** Which conversion surface owns this button — the analytics dimension. */
  gateKind: GateKind;
  /**
   * The page to return to after OAuth (usually the caller's `usePathname()`).
   * Validated through `safeNext` before it ships; a value that could not
   * survive validation on the way back is left off entirely, exactly like
   * `loginHref` does — the visitor then lands on the default post-auth page.
   */
  returnTo: string;
  /**
   * Element id (no `#`) appended to `returnTo` as a fragment, so the return
   * lands scrolled at the surface the visitor left from — the gate panel
   * passes its own id and the reader comes back to the reveal button, not the
   * top of a document that runs several viewports. `safeNext` preserves
   * fragments end to end (client, GoTrue's redirect_to, /auth/callback).
   */
  returnFragment?: string;
  className?: string;
}

/**
 * One-tap Google signup for anonymous conversion surfaces (gates, CTAs).
 *
 * Same OAuth call as LoginForm's `handleGoogleSignIn`, without the detour
 * through /login: the visitor goes straight from the document they are reading
 * to Google and back to that same document via `/auth/callback?next=…`. First
 * Google sign-in auto-creates the account, which is why the terms/privacy
 * fine-print is part of this component rather than optional around it.
 *
 * On success the browser navigates away, so the pending state only ever
 * resolves on failure (provider misconfigured, popup blocked at the OAuth
 * layer) — mirror of LoginForm's handling.
 */
export function GoogleQuickSignup({
  gateKind,
  returnTo,
  returnFragment,
  className,
}: GoogleQuickSignupProps) {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClick = async () => {
    if (isLoading) return;
    setError(null);
    // Tracked before navigation, never awaited — same rule as every gate CTA.
    trackGateCtaClick(gateKind, returnTo, "google");
    setIsLoading(true);

    const next = safeNext(returnTo);
    // The fragment only makes sense on a real return target — never on the
    // DEFAULT_NEXT fallback, where the surface it points at does not exist.
    const target =
      next !== DEFAULT_NEXT && returnFragment
        ? `${next}#${returnFragment}`
        : next;
    const { error: oauthError } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        // `next` rides through Google and back into /auth/callback, which
        // re-validates it before redirecting (§7.2 path B).
        redirectTo: `${window.location.origin}/auth/callback${
          target !== DEFAULT_NEXT ? `?next=${encodeURIComponent(target)}` : ""
        }`,
      },
    });

    if (oauthError) {
      setError(googleGateCopy.error);
      setIsLoading(false);
    }
  };

  return (
    <div className={cn("flex w-full flex-col items-center gap-2", className)}>
      <Button
        type="button"
        size="lg"
        onClick={handleClick}
        disabled={isLoading}
        className="shadow-sm"
        data-testid="google-quick-signup"
      >
        {isLoading ? (
          <Loader2 aria-hidden="true" className="h-4 w-4 shrink-0 animate-spin" />
        ) : (
          <GoogleIcon />
        )}
        {isLoading ? googleGateCopy.loading : googleGateCopy.cta}
      </Button>

      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}

      <p className="text-[11px] leading-relaxed text-muted-foreground">
        {googleGateCopy.consentPrefix}
        <Link
          href={LEGAL_ROUTES.terms}
          target="_blank"
          rel="noopener noreferrer"
          className="underline underline-offset-2 hover:text-foreground transition-colors"
        >
          {googleGateCopy.consentTerms}
        </Link>
        {googleGateCopy.consentAnd}
        <Link
          href={LEGAL_ROUTES.privacy}
          target="_blank"
          rel="noopener noreferrer"
          className="underline underline-offset-2 hover:text-foreground transition-colors"
        >
          {googleGateCopy.consentPrivacy}
        </Link>
      </p>
    </div>
  );
}
