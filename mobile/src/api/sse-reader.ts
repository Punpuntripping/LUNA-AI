import { createSSEParser, createUtf8Decoder, toTypedSSEEvent, type SSETypedEvent } from "@contracts";

import { StreamAbortedError } from "./stream-errors";

// X2 — the decode loop: bytes → UTF-8 text → SSE frames → typed events.
// Pure (no RN imports): `stream.ts` hands it the `expo/fetch` body, the unit
// tests hand it a fake ReadableStream.

/** The backend sends a heartbeat every 15 s once the run started
 *  (message_service.heartbeat_producer) — 45 s of silence = a dead socket. */
export const STREAM_IDLE_TIMEOUT_MS = 45_000;

export interface ChunkDecoder {
  decode(bytes: Uint8Array): string;
  end(): string;
}

let nativeStreaming: boolean | null = null;

/** Probe once: a streaming decoder must HOLD a lone lead byte (0xD8) and
 *  complete it with the next chunk (0xB1 → «ر»), never emit U+FFFD. */
function nativeDecoderStreams(): boolean {
  if (nativeStreaming !== null) return nativeStreaming;
  try {
    if (typeof TextDecoder !== "function") {
      nativeStreaming = false;
    } else {
      const d = new TextDecoder("utf-8");
      const head = d.decode(new Uint8Array([0xd8]), { stream: true });
      const tail = d.decode(new Uint8Array([0xb1]), { stream: true });
      nativeStreaming = head === "" && tail === "ر";
    }
  } catch {
    nativeStreaming = false;
  }
  return nativeStreaming;
}

/** `TextDecoder(…).decode(v, {stream:true})` when the engine supports it
 *  (Expo SDK 57 installs one), else the WHATWG-exact contracts fallback. A
 *  split Arabic code point never becomes «�». */
export function createChunkDecoder(opts: { forceFallback?: boolean } = {}): ChunkDecoder {
  if (!opts.forceFallback && nativeDecoderStreams()) {
    const d = new TextDecoder("utf-8");
    return {
      decode: (bytes) => d.decode(bytes, { stream: true }),
      end: () => d.decode(),
    };
  }
  return createUtf8Decoder();
}

export interface ReadSSEOptions {
  /** Fires → the loop throws StreamAbortedError (even if the native read hangs). */
  signal: AbortSignal;
  /** Called after `idleTimeoutMs` without a single byte. Expected to abort `signal`. */
  onIdle: () => void;
  /** Called on every received chunk (heartbeats included). */
  onBytes?: () => void;
  idleTimeoutMs?: number;
  decoder?: ChunkDecoder;
}

/**
 * - `"ended"`: the server closed the stream.
 * - `"stopped"`: `onEvent` returned true (a terminal event) — the rest of that
 *   chunk is still dispatched, then the reader is cancelled.
 *
 * Throws StreamAbortedError when `signal` fires, or the reader's own error
 * (network drop) otherwise. Unknown event names and unparsable payloads are
 * skipped, never thrown.
 */
export async function readSSE(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: SSETypedEvent) => boolean | void,
  opts: ReadSSEOptions,
): Promise<"ended" | "stopped"> {
  const decoder = opts.decoder ?? createChunkDecoder();
  const idleMs = opts.idleTimeoutMs ?? STREAM_IDLE_TIMEOUT_MS;
  let stop = false;
  const parser = createSSEParser((raw) => {
    const event = toTypedSSEEvent(raw);
    if (event && onEvent(event) === true) stop = true;
  });

  let timer: ReturnType<typeof setTimeout> | null = null;
  const arm = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(opts.onIdle, idleMs);
  };

  let onAbort: (() => void) | null = null;
  const aborted = new Promise<never>((_, reject) => {
    onAbort = () => reject(new StreamAbortedError());
    if (opts.signal.aborted) onAbort();
    else opts.signal.addEventListener("abort", onAbort, { once: true });
  });
  aborted.catch(() => undefined);

  const reader = body.getReader();
  let finished = false;
  try {
    arm();
    for (;;) {
      const { done, value } = await Promise.race([reader.read(), aborted]);
      if (done) break;
      arm();
      opts.onBytes?.();
      if (value && value.length > 0) parser.feed(decoder.decode(value));
      if (stop) return "stopped";
    }
    finished = true;
    parser.feed(decoder.end());
    parser.end();
    return "ended";
  } finally {
    if (timer) clearTimeout(timer);
    if (onAbort) opts.signal.removeEventListener("abort", onAbort);
    if (!finished) reader.cancel().catch(() => undefined);
  }
}
