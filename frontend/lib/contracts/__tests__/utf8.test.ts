import assert from "node:assert/strict";
import { test } from "node:test";

import { createUtf8Decoder } from "../utf8";

/**
 * `utf8.ts` must match `TextDecoder("utf-8").decode(chunk, { stream: true })`
 * chunk for chunk — valid input AND invalid input (same U+FFFD placement).
 *
 * Run:  cd frontend && npx tsx --test lib/contracts/__tests__/*.test.ts
 */

function nativeChunks(chunks: Uint8Array[]): string[] {
  const d = new TextDecoder("utf-8");
  return [...chunks.map((c) => d.decode(c, { stream: true })), d.decode()];
}

function fallbackChunks(chunks: Uint8Array[]): string[] {
  const d = createUtf8Decoder();
  return [...chunks.map((c) => d.decode(c)), d.end()];
}

// eslint-disable-next-line no-restricted-syntax -- decoder input: agent text may carry Arabic-Indic digits
const TEXT = "ريحان مساعد قانوني — «المادة 77» من نظام العمل 😀 𝔸 é ASCII ١٢٣";

test("Arabic text split at every byte: never a U+FFFD, same as native", () => {
  const bytes = new TextEncoder().encode(TEXT);
  for (let i = 0; i <= bytes.length; i++) {
    const chunks = [bytes.slice(0, i), bytes.slice(i)];
    const fb = fallbackChunks(chunks);
    assert.equal(fb.join(""), TEXT, `split at ${i}`);
    assert.deepEqual(fb, nativeChunks(chunks), `chunk-for-chunk at ${i}`);
  }
});

test("one byte per chunk", () => {
  const bytes = new TextEncoder().encode(TEXT);
  const chunks = [...bytes].map((b) => Uint8Array.of(b));
  assert.equal(fallbackChunks(chunks).join(""), TEXT);
  assert.deepEqual(fallbackChunks(chunks), nativeChunks(chunks));
});

test("invalid sequences: same replacement output as native", () => {
  const cases: number[][] = [
    [0x80],
    [0xff],
    [0xc0, 0x80],
    [0xc2],
    [0xe0, 0x80, 0x80],
    [0xed, 0xa0, 0x80], // surrogate
    [0xf4, 0x90, 0x80, 0x80], // > U+10FFFF
    [0xf0, 0x9f, 0x98], // truncated emoji at end of stream
    [0xd8, 0x41], // broken Arabic lead + ASCII
    [0xe2, 0x82, 0x41, 0xd8, 0xb1],
  ];
  for (const c of cases) {
    const bytes = Uint8Array.from(c);
    for (let i = 0; i <= bytes.length; i++) {
      const chunks = [bytes.slice(0, i), bytes.slice(i)];
      assert.deepEqual(fallbackChunks(chunks), nativeChunks(chunks), `${c} split ${i}`);
    }
  }
});

test("random bytes in random chunks match native (seeded)", () => {
  let seed = 42;
  const r = () => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    return seed / 0x7fffffff;
  };
  const pool = [0x41, 0x80, 0xbf, 0xc2, 0xd8, 0xd9, 0xa7, 0xe0, 0xe2, 0xed, 0xef, 0xf0, 0xf4, 0x9f, 0x90, 0xff];
  for (let n = 0; n < 3000; n++) {
    const len = Math.floor(r() * 12);
    const bytes = Uint8Array.from({ length: len }, () =>
      r() < 0.5 ? pool[Math.floor(r() * pool.length)] : Math.floor(r() * 256),
    );
    const chunks: Uint8Array[] = [];
    let i = 0;
    while (i < bytes.length) {
      const step = 1 + Math.floor(r() * 4);
      chunks.push(bytes.slice(i, i + step));
      i += step;
    }
    assert.deepEqual(fallbackChunks(chunks), nativeChunks(chunks), Array.from(bytes).join(","));
  }
});

test("large input does not overflow the stack", () => {
  const big = "ق".repeat(300_000);
  const d = createUtf8Decoder();
  assert.equal(d.decode(new TextEncoder().encode(big)) + d.end(), big);
});
