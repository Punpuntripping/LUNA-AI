import type { AppPushTokenDeleteRequest, AppPushTokenRequest } from "@contracts";

import { apiFetch } from "./client";
import { request } from "./transport";

// /api/v1/push/app-token — the device's Expo push token (plan 06 §3 B4).
// Bodies carry the token; it is never put in a URL, a log or an error.

export function registerAppPushToken(body: AppPushTokenRequest): Promise<void> {
  return apiFetch<void>("/push/app-token", { method: "POST", body });
}

export function deleteAppPushToken(token: string): Promise<void> {
  const body: AppPushTokenDeleteRequest = { token };
  return apiFetch<void>("/push/app-token", { method: "DELETE", body });
}

/** Sign-out variant: the CURRENT access token, no refresh (signing out must not
 *  rotate a session it is about to discard — same rule as `auth.logout`). */
export function deleteAppPushTokenWith(accessToken: string, token: string, timeoutMs: number): Promise<void> {
  const body: AppPushTokenDeleteRequest = { token };
  return request<void>("/push/app-token", { method: "DELETE", body, token: accessToken, timeoutMs });
}
