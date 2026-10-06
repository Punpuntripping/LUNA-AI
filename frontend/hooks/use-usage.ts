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

/**
 * The plan's cap on conversations answering at the same time
 * (parallel_conversations plan). Defaults to 1 when the backend omits the
 * field (older deploy) or sends something that is not a positive integer.
 */
export function maxParallelRuns(report: UsageReport | undefined): number {
  const raw = report?.max_parallel_runs;
  return typeof raw === "number" && Number.isInteger(raw) && raw >= 1 ? raw : 1;
}

/**
 * `max_parallel_runs` for the composer's pre-gate, or `null` while unknown.
 *
 * Only fetched while ``enabled`` (the composer passes "another conversation is
 * running in this tab") so an idle chat never hits /usage. `null` means "do
 * not pre-gate" — the server is authoritative and refuses an over-cap send
 * with `parallel_limit` anyway, so the failure mode of not knowing yet is a
 * clean server refusal, never a composer wrongly locked for a `max` user.
 * Shares the dialog's query key, so an open usage dialog feeds it for free.
 */
export function useMaxParallelRuns(enabled: boolean): number | null {
  const { data } = useQuery<UsageReport>({
    queryKey: usageKeys.current(),
    queryFn: usageApi.get,
    enabled,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
  return data ? maxParallelRuns(data) : null;
}
