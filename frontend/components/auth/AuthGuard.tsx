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
  const { isAuthenticated, isLoading, loadUser } = useAuthStore();

  useEffect(() => {
    loadUser();
  }, [loadUser]);

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
