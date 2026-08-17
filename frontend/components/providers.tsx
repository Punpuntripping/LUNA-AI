"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { ThemeProvider } from "next-themes";
import { DirectionProvider } from "@radix-ui/react-direction";
import { AuthGuard } from "@/components/auth/AuthGuard";
import { AuthSync } from "@/components/auth/AuthSync";
import { AnalyticsTracker } from "@/components/analytics/AnalyticsTracker";
import { ApiClientError } from "@/lib/api";
import { ApiEnvBadge } from "@/components/dev/ApiEnvBadge";

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30 * 1000,       // 30 seconds
            gcTime: 5 * 60 * 1000,      // 5 minutes
            retry: (failureCount, error) => {
              if (error instanceof ApiClientError && [401, 403, 404].includes(error.status)) {
                return false;
              }
              return failureCount < 3;
            },
            refetchOnWindowFocus: false,
          },
        },
      })
  );

  return (
    <DirectionProvider dir="rtl">
      <ThemeProvider
        attribute="class"
        defaultTheme="system"
        enableSystem
        themes={["light", "light-conservatory", "dark"]}
      >
        <QueryClientProvider client={queryClient}>
          <AuthSync />
          {/* Product analytics (`.claude/plans/product_analytics.md` §5.2).
              Mounted HERE, once, because Providers wraps the whole app from the
              root layout — public wings, blog, chat and checkout all covered by
              this single mount. It sits OUTSIDE AuthGuard on purpose: a visitor
              being redirected to /login is still a visit worth counting, and it
              must not copy AnonCtaPopup's per-shell mounting or it would miss
              the entire authed app. Renders nothing. */}
          <AnalyticsTracker />
          <AuthGuard>{children}</AuthGuard>
          <ReactQueryDevtools initialIsOpen={false} />
          <ApiEnvBadge />
        </QueryClientProvider>
      </ThemeProvider>
    </DirectionProvider>
  );
}
