"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";

interface Props {
  children: React.ReactNode;
}

// Route prefixes that anonymous visitors may view without a session. These
// pages must render for logged-out users — no /login redirect, no
// `return null`. The public share-by-link surface (/blog/{token}) serves an
// immutable snapshot to prospects without an account; /terms + /privacy are
// the public legal pages reached from the login footer; /pricing is the public
// plans page (a pre-signup decision) — all reachable before signing up.
const PUBLIC_PREFIXES = ["/blog", "/terms", "/privacy", "/pricing"] as const;

function isPublicPath(pathname: string | null): boolean {
  if (!pathname) return false;
  return PUBLIC_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export function AuthGuard({ children }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const { isAuthenticated, isLoading, loadUser, revalidateSession } =
    useAuthStore();

  const isPublic = isPublicPath(pathname);

  useEffect(() => {
    loadUser();
  }, [loadUser]);

  // Revalidate the session whenever the tab becomes visible again. While a
  // tab is backgrounded the proactive-refresh timer is throttled/frozen, so
  // the token can expire silently; refreshing on the visibility change
  // catches a dead session before the user acts (e.g. before sending).
  //
  // We listen ONLY to `visibilitychange` — never the window `focus` event.
  // `focus` also fires every time a native dialog (file picker, print,
  // basic-auth prompt) closes and returns focus to the page. That would
  // force a token refresh mid-interaction which races with Supabase's own
  // single-use-refresh-token rotation and spuriously logs the user out.
  // `visibilitychange` does not fire for those dialogs — the tab stays
  // "visible" the whole time — so it is the safe signal.
  useEffect(() => {
    function handleVisible() {
      if (document.visibilityState === "visible") {
        void revalidateSession();
      }
    }
    document.addEventListener("visibilitychange", handleVisible);
    return () => {
      document.removeEventListener("visibilitychange", handleVisible);
    };
  }, [revalidateSession]);

  useEffect(() => {
    // Public pages never redirect — anon visitors must see /blog/{token}.
    if (isPublic) return;
    if (!isLoading && !isAuthenticated && pathname !== "/login") {
      router.replace("/login");
    }
    if (!isLoading && isAuthenticated && pathname === "/login") {
      router.replace("/chat");
    }
  }, [isLoading, isAuthenticated, pathname, router, isPublic]);

  // Public pages render immediately for everyone (logged-in or anon) without
  // waiting on the session probe or gating on auth state.
  if (isPublic) {
    return <>{children}</>;
  }

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
        <span className="text-sm text-muted-foreground">
          جارٍ تحميل الجلسة...
        </span>
      </div>
    );
  }

  if (!isAuthenticated && pathname !== "/login") {
    return null;
  }

  return <>{children}</>;
}
