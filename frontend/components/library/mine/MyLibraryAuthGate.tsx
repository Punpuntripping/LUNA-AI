"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";
import { MY_LIBRARY_COPY } from "@/components/library/mine/copy";

/**
 * Auth gate for `/library/mine`.
 *
 * ⚠ Why this exists instead of relying on the global `AuthGuard`: that guard
 * treats `/library` as a PUBLIC prefix (`AuthGuard.PUBLIC_PREFIXES`) and
 * matches by `startsWith`, so `/library/mine` inherits the public treatment and
 * would render for anonymous visitors — who would then watch every shelf
 * request 401. The prefix list is shared surface owned elsewhere, so the gate
 * is applied locally here instead, mirroring AuthGuard's own behaviour: the
 * root guard has already fired `loadUser()`, so this only reads the resolved
 * session and redirects.
 */
export function MyLibraryAuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isLoading = useAuthStore((s) => s.isLoading);

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.replace("/login");
    }
  }, [isLoading, isAuthenticated, router]);

  if (isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
        <span className="text-sm text-muted-foreground">
          {MY_LIBRARY_COPY.sessionLoading}
        </span>
      </div>
    );
  }

  if (!isAuthenticated) return null;

  return <>{children}</>;
}
