import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { fetchAppConfig } from "@/api/app-config";
import { useUpdateGate } from "@/stores/update-gate";

/**
 * Asks GET /app/config on launch and on every return to the foreground
 * (TanStack focus = AppState active, see lib/query-client.ts) and feeds the
 * update gate. A failed fetch never blocks — the 426 path still catches an
 * unsupported build on its first real request.
 */
export function useAppConfigGate(): void {
  const applyConfig = useUpdateGate((s) => s.applyConfig);
  const { data } = useQuery({
    queryKey: ["app-config"],
    queryFn: fetchAppConfig,
    staleTime: 0,
    refetchOnWindowFocus: "always",
    retry: 1,
  });

  useEffect(() => {
    if (data) applyConfig(data);
  }, [data, applyConfig]);
}
