import type { AuthTokens } from "@contracts";

import { ApiError } from "@/api/errors";
import { request } from "@/api/transport";
import { jwtExpiry } from "@/lib/jwt";

import { clearTokens, getRefreshToken, setAccessToken, setRefreshToken } from "./token-store";

/** The refresh token is dead (401 AUTH_EXPIRED) or absent. Tokens are already
 *  wiped when this is thrown; the session store routes to login. */
export class SessionExpiredError extends Error {
  constructor() {
    super("session expired");
    this.name = "SessionExpiredError";
  }
}

const REFRESH_AHEAD_MS = 5 * 60 * 1000;

let inflight: Promise<string> | null = null;
let proactiveTimer: ReturnType<typeof setTimeout> | null = null;
let onExpired: (() => void) | null = null;

/** The session store registers here (avoids an import cycle). */
export function setSessionExpiredHandler(handler: () => void): void {
  onExpired = handler;
}

/**
 * Rotate the refresh token. ONE refresh at a time: refresh tokens are
 * single-use, so two parallel calls would make the second one "already used"
 * and could get the whole token family revoked.
 *
 * Order matters: the new refresh token is written to SecureStore BEFORE the
 * new access token is used, so a crash in between never strands the app with
 * a refresh token the server already rotated away.
 *
 * - 401 → SessionExpiredError (tokens wiped, handler notified).
 * - 503 / network → the ApiError is rethrown and the session is KEPT; the
 *   backend separates an outage from an expiry on purpose.
 */
export function refreshAccessToken(): Promise<string> {
  if (inflight) return inflight;
  inflight = (async () => {
    const refreshToken = await getRefreshToken();
    if (!refreshToken) {
      await expireSession();
      throw new SessionExpiredError();
    }
    let tokens: AuthTokens;
    try {
      tokens = await request<AuthTokens>("/auth/refresh", {
        method: "POST",
        body: { refresh_token: refreshToken },
      });
    } catch (err) {
      if (err instanceof ApiError && err.kind === "http" && err.status === 401) {
        await expireSession();
        throw new SessionExpiredError();
      }
      throw err;
    }
    await setRefreshToken(tokens.refresh_token);
    setAccessToken(tokens.access_token);
    scheduleProactiveRefresh(tokens.access_token);
    return tokens.access_token;
  })().finally(() => {
    inflight = null;
  });
  return inflight;
}

/** Wipe tokens and tell the session store to route to login. */
export async function expireSession(): Promise<void> {
  cancelProactiveRefresh();
  await clearTokens();
  onExpired?.();
}

/** True when the access token is missing or expires within 5 minutes. */
export function isAccessTokenStale(token: string | null): boolean {
  if (!token) return true;
  const exp = jwtExpiry(token);
  if (exp === null) return false;
  return exp * 1000 - Date.now() < REFRESH_AHEAD_MS;
}

export function scheduleProactiveRefresh(token: string): void {
  cancelProactiveRefresh();
  const exp = jwtExpiry(token);
  if (exp === null) return;
  const delay = Math.max(exp * 1000 - Date.now() - REFRESH_AHEAD_MS, 5_000);
  proactiveTimer = setTimeout(() => {
    // Failures are fine here: an outage keeps the session, and the next
    // request's 401 path retries.
    refreshAccessToken().catch(() => undefined);
  }, delay);
}

export function cancelProactiveRefresh(): void {
  if (proactiveTimer) clearTimeout(proactiveTimer);
  proactiveTimer = null;
}
