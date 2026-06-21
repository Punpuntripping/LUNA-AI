"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { ThemeProvider } from "next-themes";
import { DirectionProvider } from "@radix-ui/react-direction";
import { AuthGuard } from "@/components/auth/AuthGuard";
import { AuthSync } from "@/components/auth/AuthSync";
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
          <AuthGuard>{children}</AuthGuard>
          <ReactQueryDevtools initialIsOpen={false} />
          <ApiEnvBadge />
        </QueryClientProvider>
      </ThemeProvider>
    </DirectionProvider>
  );
}
