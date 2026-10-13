import { expireSession, refreshAccessToken, SessionExpiredError } from "@/auth/refresh";
import { getAccessToken } from "@/auth/token-store";

import { ApiError, fallbackForStatus } from "./errors";
import { request, type RequestOptions } from "./transport";

export interface ApiFetchOptions extends Omit<RequestOptions, "token"> {
  /** Send the bearer token (default true). Pre-login routes pass false. */
  auth?: boolean;
}

/**
 * `apiFetch<T>(path)` — the app's single entry to the backend (mirrors the
 * web's `apiFetch`, frontend/lib/api.ts). Authenticated calls get the
 * in-memory access token; a 401 triggers ONE refresh and ONE retry. If the
 * refresh says the session is dead, the session store has already been told.
 */
export async function apiFetch<T>(path: string, opts: ApiFetchOptions = {}): Promise<T> {
  const { auth = true, ...rest } = opts;
  if (!auth) return request<T>(path, rest);

  const token = getAccessToken();
  try {
    return await request<T>(path, { ...rest, token });
  } catch (err) {
    if (!(err instanceof ApiError) || err.kind !== "http" || err.status !== 401 || !token) throw err;
  }

  let fresh: string;
  try {
    fresh = await refreshAccessToken();
  } catch (err) {
    if (err instanceof SessionExpiredError) {
      throw new ApiError({ kind: "http", status: 401, code: "AUTH_EXPIRED", message: fallbackForStatus(401) });
    }
    throw err;
  }
  try {
    return await request<T>(path, { ...rest, token: fresh });
  } catch (err) {
    // A fresh token still refused: the account is gone/blocked — log out.
    if (err instanceof ApiError && err.kind === "http" && err.status === 401) await expireSession();
    throw err;
  }
}
