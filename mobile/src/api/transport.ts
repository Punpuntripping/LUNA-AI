import { fetch as expoFetch } from "expo/fetch";

import { API_PREFIX, API_URL } from "@/config/env";
import { useUpdateGate } from "@/stores/update-gate";

import { appHeaders } from "./headers";
import { ApiError, errorFromBody, MSG_EDGE, MSG_NETWORK } from "./errors";

// Low-level request: headers, timeout, error parsing. Knows nothing about
// sessions — `client.ts` adds the bearer token and the 401 → refresh → retry.

export const DEFAULT_TIMEOUT_MS = 15_000;

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  /** JSON-serialised as the request body. */
  body?: unknown;
  /** Bearer token to send (omit for public / pre-login routes). */
  token?: string | null;
  timeoutMs?: number;
  signal?: AbortSignal;
}

function looksLikeHtml(contentType: string | null): boolean {
  return !!contentType && contentType.toLowerCase().includes("text/html");
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { headers: base, requestId } = appHeaders();
  const headers: Record<string, string> = { ...base, Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const onOuterAbort = () => controller.abort();
  opts.signal?.addEventListener("abort", onOuterAbort);

  let res: Response;
  try {
    res = await fetch(`${API_URL}${API_PREFIX}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: controller.signal,
    });
  } catch {
    // Offline, DNS, TLS, timeout or caller abort — all the same to the user.
    throw new ApiError({ kind: "network", status: 0, message: MSG_NETWORK, requestId });
  } finally {
    clearTimeout(timer);
    opts.signal?.removeEventListener("abort", onOuterAbort);
  }

  await throwIfFailed(res, requestId);

  if (res.status === 204) return undefined as T;
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError({ kind: "edge", status: res.status, message: MSG_EDGE, requestId });
  }
}

/** The parts of a fetch Response the error check reads (RN fetch and
 *  `expo/fetch` both satisfy it). */
interface ResponseLike {
  ok: boolean;
  status: number;
  headers: { get: (name: string) => string | null };
  json: () => Promise<unknown>;
}

async function throwIfFailed(res: ResponseLike, requestId: string): Promise<void> {
  const contentType = res.headers.get("content-type");
  // A Cloudflare challenge/block is HTML (or flagged by cf-mitigated), never
  // our JSON. A native app cannot solve it — say so in Arabic.
  if (res.headers.get("cf-mitigated") || looksLikeHtml(contentType)) {
    throw new ApiError({ kind: "edge", status: res.status, message: MSG_EDGE, requestId });
  }
  if (res.ok) return;
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  const err = errorFromBody(res.status, body, requestId);
  if (res.status === 426) useUpdateGate.getState().markUpdateRequired();
  throw err;
}

/** A binary success body (e.g. `POST /export`). Same headers, timeout and
 *  Arabic error mapping as `request`; the body is read through `expo/fetch`
 *  because React Native's own fetch cannot hand back an ArrayBuffer. */
export async function requestBytes(path: string, opts: RequestOptions = {}): Promise<Uint8Array> {
  const { headers: base, requestId } = appHeaders();
  const headers: Record<string, string> = { ...base, Accept: "*/*" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  try {
    let res: Awaited<ReturnType<typeof expoFetch>>;
    try {
      res = await expoFetch(`${API_URL}${API_PREFIX}${path}`, {
        method: opts.method ?? "GET",
        headers,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        signal: controller.signal,
      });
    } catch {
      throw new ApiError({ kind: "network", status: 0, message: MSG_NETWORK, requestId });
    }
    await throwIfFailed(res, requestId);
    try {
      return new Uint8Array(await res.arrayBuffer());
    } catch {
      throw new ApiError({ kind: "network", status: 0, message: MSG_NETWORK, requestId });
    }
  } finally {
    clearTimeout(timer);
  }
}
