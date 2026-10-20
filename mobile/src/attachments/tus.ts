// F3 — TUS 1.0.0 upload to Supabase Storage (the `upload_url` the backend's
// `/attachments/init` returns). Own small loop instead of tus-js-client (F0
// spike): tus-js-client reads RN files through `fetch(uri).blob()` + Blob
// slicing, which is fragile on Hermes; here the bytes come from expo-file-system
// byte ranges and go out through `expo/fetch` (Uint8Array bodies), both injected
// so this file stays pure and unit-tested.
//
// Same wire as the web's lib/upload-client.ts (tus-js-client options):
//   POST   endpoint   Upload-Length + Upload-Metadata(bucketName, objectName,
//                     contentType, cacheControl) → 201 + Location
//   PATCH  Location   Upload-Offset + application/offset+octet-stream, 6 MB
//                     chunks (Supabase requires exactly 6 MB except the last)
//   HEAD   Location   → Upload-Offset (resync after a failure / background)
// Every request: Tus-Resumable 1.0.0, `Authorization: Bearer <access token>`,
// `x-upsert: true` (a retry overwrites a partial object instead of 409).
//
// Failures: retry with the web's delays [0, 1, 3, 5, 10 s], resyncing the
// offset with HEAD first. While the app is not active (iOS suspends the
// socket), the loop parks until it is active again and then resumes from the
// server's offset — that wait never uses up a retry. A 401/403 refreshes the
// access token once per failure streak.

export const TUS_CHUNK_BYTES = 6 * 1024 * 1024;
export const TUS_RETRY_DELAYS_MS: readonly number[] = [0, 1_000, 3_000, 5_000, 10_000];

export interface TusResponse {
  status: number;
  header(name: string): string | null;
}

export interface TusRequest {
  method: "POST" | "PATCH" | "HEAD";
  url: string;
  headers: Record<string, string>;
  body?: Uint8Array;
  signal: AbortSignal;
}

export interface TusDeps {
  send(req: TusRequest): Promise<TusResponse>;
  /** Bytes [offset, offset + length) of the local file (shorter at the end). */
  readChunk(offset: number, length: number): Promise<Uint8Array>;
  getToken(): string | null;
  /** A fresh access token (single-flight refresh); throws if the session is gone. */
  refreshToken(): Promise<string>;
  sleep(ms: number, signal: AbortSignal): Promise<void>;
  isActive(): boolean;
  /** Resolves once the app is in the foreground again. */
  waitUntilActive(signal: AbortSignal): Promise<void>;
}

export interface TusUploadParams {
  endpoint: string;
  size: number;
  metadata: Record<string, string>;
  signal: AbortSignal;
  onProgress?: (sent: number, total: number) => void;
  chunkBytes?: number;
  retryDelaysMs?: readonly number[];
}

export class TusAbortedError extends Error {
  constructor() {
    super("aborted");
    this.name = "TusAbortedError";
  }
}

/** Never shown to the user (raw status text) — the caller maps it to Arabic. */
export class TusError extends Error {
  readonly status: number;
  readonly fatal: boolean;
  constructor(message: string, status: number, fatal: boolean) {
    super(message);
    this.name = "TusError";
    this.status = status;
    this.fatal = fatal;
  }
}

// ---- base64 of UTF-8 (Upload-Metadata values) ----

const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

export function base64Utf8(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let out = "";
  for (let i = 0; i < bytes.length; i += 3) {
    const a = bytes[i];
    const b = i + 1 < bytes.length ? bytes[i + 1] : 0;
    const c = i + 2 < bytes.length ? bytes[i + 2] : 0;
    const triple = (a << 16) | (b << 8) | c;
    out += B64[(triple >> 18) & 63] + B64[(triple >> 12) & 63];
    out += i + 1 < bytes.length ? B64[(triple >> 6) & 63] : "=";
    out += i + 2 < bytes.length ? B64[triple & 63] : "=";
  }
  return out;
}

export function encodeMetadata(metadata: Record<string, string>): string {
  return Object.entries(metadata)
    .map(([k, v]) => `${k} ${base64Utf8(v)}`)
    .join(",");
}

/** `Location` may be absolute or relative to the endpoint. */
export function resolveLocation(endpoint: string, location: string): string {
  if (/^https?:\/\//i.test(location)) return location;
  const origin = /^(https?:\/\/[^/]+)/i.exec(endpoint)?.[1] ?? "";
  if (location.startsWith("/")) return `${origin}${location}`;
  return `${endpoint.replace(/\/+$/, "")}/${location}`;
}

function parseOffset(value: string | null): number | null {
  if (value === null || !/^\d+$/.test(value.trim())) return null;
  return Number(value.trim());
}

/** Statuses worth retrying (tus-js-client: anything but 4xx, plus 409/423). */
function retryable(status: number): boolean {
  return status === 0 || status >= 500 || status === 409 || status === 423 || status === 429;
}

const isAuthStatus = (status: number) => status === 401 || status === 403;

export interface TusUploadResult {
  /** The upload's URL (Location) — kept for a later resume. */
  uploadUrl: string;
}

/**
 * Uploads `size` bytes. Resolves once the server holds all of them; rejects
 * with TusAbortedError (signal) or TusError / the transport's error.
 */
export async function tusUpload(deps: TusDeps, params: TusUploadParams): Promise<TusUploadResult> {
  const { endpoint, size, signal } = params;
  const chunkBytes = params.chunkBytes ?? TUS_CHUNK_BYTES;
  const delays = params.retryDelaysMs ?? TUS_RETRY_DELAYS_MS;
  let token = deps.getToken();
  if (!token) throw new TusError("no session", 401, true);

  const base = (): Record<string, string> => ({
    "Tus-Resumable": "1.0.0",
    Authorization: `Bearer ${token}`,
    "x-upsert": "true",
  });
  const checkAbort = () => {
    if (signal.aborted) throw new TusAbortedError();
  };

  let attempt = 0;
  let refreshedThisStreak = false;
  /** One failed request: decide wait / refresh / give up. */
  const recover = async (err: unknown): Promise<void> => {
    checkAbort();
    if (err instanceof TusError && err.fatal) throw err;
    if (err instanceof TusError && isAuthStatus(err.status)) {
      if (refreshedThisStreak) throw new TusError("auth", err.status, true);
      refreshedThisStreak = true;
      token = await deps.refreshToken();
      return;
    }
    // Backgrounded: park, then resume without spending a retry.
    if (!deps.isActive()) {
      await deps.waitUntilActive(signal);
      checkAbort();
      return;
    }
    if (attempt >= delays.length) throw err instanceof Error ? err : new TusError("failed", 0, true);
    await deps.sleep(delays[attempt], signal);
    attempt++;
    checkAbort();
  };
  const succeeded = () => {
    attempt = 0;
    refreshedThisStreak = false;
  };
  const send = async (req: Omit<TusRequest, "signal">): Promise<TusResponse> => {
    checkAbort();
    try {
      return await deps.send({ ...req, signal });
    } catch (err) {
      checkAbort();
      throw err instanceof TusError ? err : new TusError("network", 0, false);
    }
  };
  const failure = (status: number, what: string) =>
    new TusError(`${what} ${status}`, status, !(retryable(status) || isAuthStatus(status)));

  // ---- 1. create ----
  let uploadUrl: string | null = null;
  while (uploadUrl === null) {
    try {
      const res = await send({
        method: "POST",
        url: endpoint,
        headers: {
          ...base(),
          "Upload-Length": String(size),
          "Upload-Metadata": encodeMetadata(params.metadata),
        },
      });
      const location = res.header("Location");
      if (res.status < 200 || res.status >= 300) throw failure(res.status, "create");
      if (!location) throw new TusError("no location", res.status, true);
      uploadUrl = resolveLocation(endpoint, location);
      succeeded();
    } catch (err) {
      await recover(err);
    }
  }

  // ---- 2. PATCH chunks (HEAD to resync after any failure) ----
  let offset = 0;
  let needsSync = false;
  params.onProgress?.(0, size);
  while (offset < size || needsSync) {
    try {
      if (needsSync) {
        const res = await send({ method: "HEAD", url: uploadUrl, headers: base() });
        if (res.status < 200 || res.status >= 300) throw failure(res.status, "head");
        const serverOffset = parseOffset(res.header("Upload-Offset"));
        if (serverOffset === null) throw new TusError("no offset", res.status, false);
        offset = serverOffset;
        needsSync = false;
        succeeded();
        params.onProgress?.(offset, size);
        if (offset >= size) break;
      }
      let chunk: Uint8Array;
      try {
        chunk = await deps.readChunk(offset, Math.min(chunkBytes, size - offset));
      } catch {
        throw new TusError("read", 0, true); // the local file is gone — no retry helps
      }
      checkAbort();
      if (chunk.length === 0) throw new TusError("short read", 0, true);
      const res = await send({
        method: "PATCH",
        url: uploadUrl,
        headers: {
          ...base(),
          "Upload-Offset": String(offset),
          "Content-Type": "application/offset+octet-stream",
        },
        body: chunk,
      });
      if (res.status < 200 || res.status >= 300) throw failure(res.status, "patch");
      offset = parseOffset(res.header("Upload-Offset")) ?? offset + chunk.length;
      succeeded();
      params.onProgress?.(Math.min(offset, size), size);
    } catch (err) {
      await recover(err);
      needsSync = true;
    }
  }
  return { uploadUrl };
}
