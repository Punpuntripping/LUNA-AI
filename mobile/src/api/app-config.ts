import type { AppConfig } from "@contracts";

import { apiFetch } from "./client";

/** GET /app/config — public, exempt from the 426 gate. */
export function fetchAppConfig(): Promise<AppConfig> {
  return apiFetch<AppConfig>("/app/config", { auth: false, timeoutMs: 8_000 });
}
