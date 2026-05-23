/**
 * Headless wrapper around tus-js-client for direct-to-Supabase Storage
 * resumable uploads. See `.claude/plans/upload_reliability.md` for the
 * full protocol.
 *
 * The browser uploads bytes directly to Supabase Storage's TUS endpoint
 * (returned by the backend `/init` route) — bytes never traverse Railway.
 * Backend only mints metadata + verifies on finalize.
 */
import * as tus from "tus-js-client";

/** Live handle on an in-flight upload. Currently only exposes `abort`. */
export interface UploadHandle {
  abort: () => void;
}

export interface StartTusUploadOptions {
  /** Supabase Storage TUS endpoint, returned by backend `/init`. */
  url: string;
  /** Raw file picked by the user. */
  file: File;
  /** Supabase Storage bucket name (e.g. `documents`). */
  bucket: string;
  /** Final object name inside the bucket (e.g. `cases/{id}/file.pdf`). */
  objectName: string;
  /** File mime — echoed into TUS metadata so Supabase stores it correctly. */
  contentType: string;
  /** Current Supabase access token from `lib/api.ts#getAccessToken()`. */
  accessToken: string;
  /** Fires for every chunk progress event. */
  onProgress: (sent: number, total: number) => void;
  /** Fires once after the final PATCH succeeds. */
  onSuccess: () => void;
  /** Fires on unrecoverable error (after retries exhausted). */
  onError: (err: Error) => void;
}

// 6 MB matches Supabase Storage's recommended TUS chunk size.
const CHUNK_SIZE = 6 * 1024 * 1024;

// Exponential-ish backoff: instant retry, then 1 s, 3 s, 5 s, 10 s.
// 5 attempts is enough to ride out short Railway / Supabase blips
// without making the user wait > ~20 s on a hard outage.
const RETRY_DELAYS = [0, 1000, 3000, 5000, 10000];

/**
 * Kick off a resumable upload to Supabase Storage. Returns a handle the
 * caller can use to cancel the upload (e.g. when the user clicks the X on
 * an AttachmentUploadCard).
 *
 * The `accessToken` is captured at start; if it expires mid-upload the
 * tus retry loop will surface a 401 → caller is responsible for handling
 * that case (currently we let it bubble as a failed upload — the user
 * can retry).
 */
export function startTusUpload(opts: StartTusUploadOptions): UploadHandle {
  const upload = new tus.Upload(opts.file, {
    endpoint: opts.url,
    chunkSize: CHUNK_SIZE,
    retryDelays: RETRY_DELAYS,
    // Keep the fingerprint in localStorage only while the upload is live
    // so we satisfy the "no auth in localStorage" rule. The fingerprint is
    // an opaque URL, not a token.
    removeFingerprintOnSuccess: true,
    metadata: {
      bucketName: opts.bucket,
      objectName: opts.objectName,
      contentType: opts.contentType,
      cacheControl: "3600",
    },
    headers: {
      Authorization: `Bearer ${opts.accessToken}`,
      // `x-upsert: true` lets a retry overwrite a partially-uploaded
      // object instead of 409-ing — required for resume-after-network-blip.
      "x-upsert": "true",
    },
    onError: (err) => {
      opts.onError(err instanceof Error ? err : new Error(String(err)));
    },
    onProgress: (sent, total) => {
      opts.onProgress(sent, total);
    },
    onSuccess: () => {
      opts.onSuccess();
    },
  });

  upload.start();

  return {
    abort: () => {
      // `abort(true)` removes the fingerprint so a re-pick of the same
      // file starts cleanly. Errors here are best-effort — if abort fails
      // the caller has already moved on.
      upload.abort(true).catch(() => undefined);
    },
  };
}
