import type { ApiError } from "./errors";

// Pure (no RN imports) so the send state machine can be unit-tested in Node.

/**
 * The message POST was refused BEFORE the server wrote anything: a 4xx from
 * the route's dependencies (auth, validation, ownership, rate limit, 426), a
 * Cloudflare block, or a dead session found while refreshing. The send failed
 * for certain — there is nothing to recover or adopt.
 *
 * Anything else thrown by `openMessageStream` (network error, 5xx) means the
 * request MAY have reached the backend, and the caller must not re-POST.
 */
export class StreamNotSentError extends Error {
  readonly error: ApiError;

  constructor(error: ApiError) {
    super(error.message);
    this.name = "StreamNotSentError";
    this.error = error;
  }
}

/** The read loop stopped because the caller's AbortSignal fired. The caller
 *  knows why (Stop, idle watchdog, resume, superseded) — this only says "it was us". */
export class StreamAbortedError extends Error {
  constructor() {
    super("stream aborted");
    this.name = "StreamAbortedError";
  }
}
