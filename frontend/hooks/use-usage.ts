import { useQuery } from "@tanstack/react-query";
import { usageApi } from "@/lib/api";
import type { UsageReport } from "@/types";

export const usageKeys = {
  all: ["usage"] as const,
  current: () => [...usageKeys.all, "current"] as const,
};

/**
 * Current usage snapshot for the Settings → حدود الاستخدام dialog. Only
 * fetched when ``enabled`` is true (dialog is open) so we don't churn the
 * endpoint on every page mount. Stale time is short — the bars should
 * reflect the latest spend the moment the user opens the dialog.
 */
export function useUsageLimits(enabled: boolean) {
  return useQuery<UsageReport>({
    queryKey: usageKeys.current(),
    queryFn: usageApi.get,
    enabled,
    staleTime: 10_000,
    // While the dialog is open, refetch every 5 min so the bars stay live
    // for users who keep it as a monitor. `enabled` gates the polling, so
    // closing the dialog stops the interval — zero background traffic.
    refetchInterval: enabled ? 300_000 : false,
    refetchOnWindowFocus: false,
  });
}
