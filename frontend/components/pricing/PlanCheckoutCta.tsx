"use client";

import Link from "next/link";
import { useAuthStore } from "@/stores/auth-store";
import { buttonVariants } from "@/components/ui/button";
import { loginHref } from "@/lib/safe-next";
import { cn } from "@/lib/utils";

interface PlanCheckoutCtaProps {
  /** `plans.plan_id` — the /pay route segment. */
  planId: string;
  highlighted?: boolean;
  label?: string;
}

/**
 * The «اشترك الآن» CTA on a plan card — a client island inside an otherwise
 * static page, exactly like `HeaderAuthActions`.
 *
 * Two destinations, one button:
 *   - signed in  → `/pay/{plan}` (AuthGuard lets them straight through)
 *   - signed out → `/login?next=%2Fpay%2F{plan}&mode=register`, so signing up
 *     RESUMES the purchase instead of dumping them on /chat. `loginHref` builds
 *     and validates that URL; `/pay` had to be added to the `safeNext`
 *     allowlist for the `next` value to survive.
 *
 * While the session probe is in flight we render the anonymous href rather than
 * a placeholder: /pricing is a public, indexed page whose CTA is the whole
 * point, so it must be a real, crawlable link in the initial HTML. A signed-in
 * user who somehow clicks during the probe lands on /login and is bounced
 * onward by `next` — a slower path, never a wrong one.
 */
export function PlanCheckoutCta({
  planId,
  highlighted,
  label = "اشترك الآن",
}: PlanCheckoutCtaProps) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isLoading = useAuthStore((s) => s.isLoading);

  const target = `/pay/${planId}`;
  const href =
    !isLoading && isAuthenticated
      ? target
      : loginHref(target, { register: true });

  return (
    <Link
      href={href}
      className={cn(
        buttonVariants({ variant: highlighted ? "default" : "outline" }),
        "w-full font-semibold",
      )}
      data-testid={`pricing-cta-${planId}`}
    >
      {label}
    </Link>
  );
}
