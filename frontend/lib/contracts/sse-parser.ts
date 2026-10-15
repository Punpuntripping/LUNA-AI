// Incremental `text/event-stream` parser for the message stream
// (`POST /conversations/{id}/messages`). Transport-agnostic: feed it decoded
// text chunks in arrival order — it never sees bytes, so UTF-8 decoding (with
// `stream: true`, or `utf8.ts`) happens before it.
//
// The backend writes every frame as `event: X\ndata: <one-line JSON>\n\n`
// (`message_service._sse_event`, `ensure_ascii=False`). The parser follows the
// WHATWG event-stream rules anyway, so a proxy that rewrites line endings or
// splits a frame across TCP packets changes nothing:
//   - lines end in `\n`, `\r\n` or a lone `\r` (a `\r` at the very end of a
//     chunk is held back until the next chunk says whether `\n` follows);
//   - a partial line is kept across chunks;
//   - `:`-prefixed lines are comments and are ignored;
//   - multiple `data:` lines join with `\n`; one leading space after the colon
//     is stripped;
//   - a blank line dispatches; a frame with no `data:` line dispatches nothing.

import type { SSEEventMap, SSEEventName } from "./sse";
import { isSSEEventName } from "./sse";

/** One dispatched frame, untyped. `event` defaults to `"message"` (spec). */
export interface SSERawEvent {
  event: string;
  data: string;
  /** Last `id:` seen so far on this stream ("" when none). */
  id: string;
}

export interface SSEParser {
  /** Feed the next decoded text chunk. Dispatches every frame it completes. */
  feed(chunk: string): void;
  /**
   * End of stream. Flushes a held-back `\r` and an unterminated last line, then
   * dispatches a final frame that was not followed by a blank line (the backend
   * always terminates frames; this is leniency for a cut connection — a JSON
   * payload cut mid-way is still rejected by `parseSSEData`).
   */
  end(): void;
}

export function createSSEParser(onEvent: (event: SSERawEvent) => void): SSEParser {
  let buffer = "";
  let eventName = "";
  let dataLines: string[] = [];
  let hasData = false;
  let lastId = "";
  let first = true;

  const dispatch = () => {
    if (hasData) {
      onEvent({ event: eventName || "message", data: dataLines.join("\n"), id: lastId });
    }
    eventName = "";
    dataLines = [];
    hasData = false;
  };

  const processLine = (line: string) => {
    if (line === "") {
      dispatch();
      return;
    }
    if (line.charCodeAt(0) === 0x3a /* ':' */) return; // comment
    const colon = line.indexOf(":");
    let field: string;
    let value: string;
    if (colon === -1) {
      field = line;
      value = "";
    } else {
      field = line.slice(0, colon);
      value = line.slice(colon + 1);
      if (value.charCodeAt(0) === 0x20 /* ' ' */) value = value.slice(1);
    }
    switch (field) {
      case "event":
        eventName = value;
        break;
      case "data":
        dataLines.push(value);
        hasData = true;
        break;
      case "id":
        if (!value.includes("\u0000")) lastId = value;
        break;
      default:
        // `retry` and unknown fields: nothing to do for a one-shot POST stream.
        break;
    }
  };

  // Index in `buffer` already scanned without finding a line end — so a long
  // line arriving in many small chunks is scanned once, not once per chunk.
  let scanned = 0;

  const drain = (final: boolean) => {
    let start = 0;
    const n = buffer.length;
    let i = scanned;
    for (; i < n; i++) {
      const c = buffer.charCodeAt(i);
      if (c === 0x0a /* \n */) {
        processLine(buffer.slice(start, i));
        start = i + 1;
      } else if (c === 0x0d /* \r */) {
        if (i + 1 < n) {
          processLine(buffer.slice(start, i));
          if (buffer.charCodeAt(i + 1) === 0x0a) i++;
          start = i + 1;
        } else if (final) {
          processLine(buffer.slice(start, i));
          start = i + 1;
        } else {
          // `\r` is the last char: wait for the next chunk (it may be `\r\n`).
          break;
        }
      }
    }
    buffer = buffer.slice(start);
    // Resume at the held `\r` (if we stopped on one) or at the end.
    scanned = i - start;
  };

  return {
    feed(chunk: string) {
      if (!chunk) return;
      if (first) {
        first = false;
        if (chunk.charCodeAt(0) === 0xfeff) chunk = chunk.slice(1); // BOM
      }
      buffer += chunk;
      drain(false);
    },
    end() {
      drain(true);
      if (buffer.length > 0) {
        processLine(buffer);
        buffer = "";
      }
      scanned = 0;
      dispatch();
    },
  };
}

/**
 * Parse a frame's `data` as the payload of `name`. NEVER throws: invalid JSON,
 * or anything that is not a JSON object, yields `null`. The shape itself is
 * trusted (same as the web) — callers read optional fields defensively.
 */
export function parseSSEData<K extends SSEEventName>(
  name: K,
  json: string,
): SSEEventMap[K] | null {
  void name;
  let value: unknown;
  try {
    value = JSON.parse(json);
  } catch {
    return null;
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  return value as SSEEventMap[K];
}

/** A frame narrowed to a known event name with its typed payload. */
export type SSETypedEvent = {
  [K in SSEEventName]: { event: K; data: SSEEventMap[K] };
}[SSEEventName];

/**
 * Raw frame → typed event, or `null` for an unknown event name or an
 * unparsable payload (both are ignored by the client, never thrown).
 */
export function toTypedSSEEvent(raw: SSERawEvent): SSETypedEvent | null {
  if (!isSSEEventName(raw.event)) return null;
  const data = parseSSEData(raw.event, raw.data);
  if (data === null) return null;
  return { event: raw.event, data } as SSETypedEvent;
}
