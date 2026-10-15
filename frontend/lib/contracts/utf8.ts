// Incremental UTF-8 → string decoder — the FALLBACK for an engine whose
// `TextDecoder` lacks `decode(bytes, { stream: true })`. Prefer the native
// decoder when it streams; use this only when it does not.
//
// A network chunk can end in the middle of a multi-byte code point (every
// Arabic letter is 2 bytes). Decoding chunks independently turns each such
// split into `�` «�» in the middle of the answer; this decoder holds the
// incomplete tail until the next chunk completes it.
//
// Implements the WHATWG Encoding "UTF-8 decoder" (replacement mode), so its
// output — including where `�` goes for invalid input — matches
// `new TextDecoder("utf-8").decode(chunk, { stream: true })` chunk for chunk.

export interface Utf8StreamDecoder {
  /** Decode the next chunk; an incomplete trailing sequence is held back. */
  decode(bytes: Uint8Array): string;
  /** End of stream: a held incomplete sequence becomes one `�`. */
  end(): string;
}

const REPLACEMENT = 0xfffd;

export function createUtf8Decoder(): Utf8StreamDecoder {
  let codePoint = 0;
  let bytesNeeded = 0;
  let bytesSeen = 0;
  let lower = 0x80;
  let upper = 0xbf;

  const reset = () => {
    codePoint = 0;
    bytesNeeded = 0;
    bytesSeen = 0;
    lower = 0x80;
    upper = 0xbf;
  };

  return {
    decode(bytes: Uint8Array): string {
      const units: number[] = [];
      const push = (cp: number) => {
        if (cp > 0xffff) {
          const v = cp - 0x10000;
          units.push(0xd800 + (v >> 10), 0xdc00 + (v & 0x3ff));
        } else {
          units.push(cp);
        }
      };

      for (let i = 0; i < bytes.length; i++) {
        const byte = bytes[i];
        if (bytesNeeded === 0) {
          if (byte <= 0x7f) {
            push(byte);
          } else if (byte >= 0xc2 && byte <= 0xdf) {
            bytesNeeded = 1;
            codePoint = byte & 0x1f;
          } else if (byte >= 0xe0 && byte <= 0xef) {
            if (byte === 0xe0) lower = 0xa0;
            if (byte === 0xed) upper = 0x9f;
            bytesNeeded = 2;
            codePoint = byte & 0x0f;
          } else if (byte >= 0xf0 && byte <= 0xf4) {
            if (byte === 0xf0) lower = 0x90;
            if (byte === 0xf4) upper = 0x8f;
            bytesNeeded = 3;
            codePoint = byte & 0x07;
          } else {
            push(REPLACEMENT);
          }
          continue;
        }
        if (byte < lower || byte > upper) {
          // Broken sequence: emit one replacement, then re-read this byte as
          // the start of something new ("prepend to stream" in the spec).
          reset();
          push(REPLACEMENT);
          i--;
          continue;
        }
        lower = 0x80;
        upper = 0xbf;
        codePoint = (codePoint << 6) | (byte & 0x3f);
        bytesSeen++;
        if (bytesSeen !== bytesNeeded) continue;
        const cp = codePoint;
        reset();
        push(cp);
      }

      // Chunked fromCharCode: a spread of a huge array would overflow the stack.
      let out = "";
      for (let k = 0; k < units.length; k += 8192) {
        out += String.fromCharCode.apply(null, units.slice(k, k + 8192));
      }
      return out;
    },

    end(): string {
      if (bytesNeeded === 0) return "";
      reset();
      return String.fromCharCode(REPLACEMENT);
    },
  };
}
