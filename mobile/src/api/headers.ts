import { randomUUID } from "expo-crypto";

import { APP_HEADERS } from "@contracts";

import { APP_BUILD, APP_PLATFORM, APP_UPDATE_ID, APP_VERSION } from "@/lib/app-info";

/** Headers sent on EVERY request. A fresh X-Request-ID per call — the backend
 *  echoes it and logs it as `request_id`, so a failure can be found in Logfire. */
export function appHeaders(): { headers: Record<string, string>; requestId: string } {
  const requestId = randomUUID();
  return {
    requestId,
    headers: {
      [APP_HEADERS.platform]: APP_PLATFORM,
      ...(APP_VERSION ? { [APP_HEADERS.version]: APP_VERSION } : {}),
      [APP_HEADERS.build]: APP_BUILD,
      [APP_HEADERS.updateId]: APP_UPDATE_ID,
      [APP_HEADERS.requestId]: requestId,
    },
  };
}
