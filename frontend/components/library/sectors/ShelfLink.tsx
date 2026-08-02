"use client";

import Link from "next/link";
import { LibraryBig } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * `/library` → «مكتبتي». The twin of the «تصفّح المكتبة العامة» link on the
 * shelf, closing the loop between the public corpus and the user's own shelf.
 *
 * WHY THIS IS A CLIENT COMPONENT ON A STATIC PAGE. `/library` is statically
 * prerendered and is the crawl skeleton for the whole sector wing (§12.1/§12.8),
 * so its server render must not read auth — `cookies()`/`headers()` would opt
 * the route out of static generation, which is a far worse regression than the
 * problem this link solves. Auth lives in memory on the client (never
 * localStorage), so the decision can only be made after hydration anyway.
 *
 * ⚠ RENDERS NOTHING FOR ANONYMOUS READERS — and that is the point, in both
 * directions:
 *   · A crawler gets no `/library/mine` link. The shelf is authed and
 *     `force-dynamic`; putting it in the crawl skeleton would offer Googlebot a
 *     URL that can only ever 401. This is the one link on the hub that must NOT
 *     be in the static HTML.
 *   · A signed-out human gets no dead end. «مكتبتي» that bounces to a login
 *     wall is the "trick" feeling the gate copy rules already forbid; the anon
 *     conversion path is the CTA wall, which is designed for it.
 *
 * `isLoading` is checked alongside `isAuthenticated` for the same reason
 * `SiteNav` does it: the store starts unauthenticated, so rendering on
 * `isAuthenticated` alone would flash the link off for a signed-in reader on
 * every cold load. Nothing is reserved during loading — this sits at the end of
 * a flex row, so its late arrival shifts nothing above it.
 */
export function ShelfLink() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isLoading = useAuthStore((s) => s.isLoading);

  if (!isAuthenticated || isLoading) return null;

  return (
    <Link
      href="/library/mine"
      className={cn(buttonVariants({ variant: "outline", size: "sm" }), "gap-1.5")}
    >
      <LibraryBig aria-hidden="true" className="h-4 w-4 shrink-0" />
      مكتبتي
    </Link>
  );
}
