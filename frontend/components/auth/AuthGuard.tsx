"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";

interface Props {
  children: React.ReactNode;
}

export function AuthGuard({ children }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const { isAuthenticated, isLoading, loadUser, revalidateSession } =
    useAuthStore();

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
    if (!isLoading && !isAuthenticated && pathname !== "/login") {
      router.replace("/login");
    }
    if (!isLoading && isAuthenticated && pathname === "/login") {
      router.replace("/chat");
    }
  }, [isLoading, isAuthenticated, pathname, router]);

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
