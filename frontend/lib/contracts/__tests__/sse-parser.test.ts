import assert from "node:assert/strict";
import { test } from "node:test";

import {
  createSSEParser,
  parseSSEData,
  toTypedSSEEvent,
  type SSERawEvent,
} from "../sse-parser";
import { SSE_EVENT_NAMES } from "../sse";
import { createUtf8Decoder } from "../utf8";

/**
 * Run:  cd frontend && npx tsx --test lib/contracts/__tests__/*.test.ts
 */

/** The backend's exact frame format (message_service._sse_event). */
function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function collect(chunks: string[]): SSERawEvent[] {
  const out: SSERawEvent[] = [];
  const p = createSSEParser((e) => out.push(e));
  for (const c of chunks) p.feed(c);
  p.end();
  return out;
}

const STREAM =
  frame("message_start", { user_message_id: "u", assistant_message_id: "a", conversation_id: "c" }) +
  frame("heartbeat", {}) +
  frame("token", { text: "مرحباً، هذا ردّ «ريحان» [1]" }) +
  frame("token", { text: "😀 emoji + سطر\nجديد" }) +
  frame("done", { message_id: "a", usage: { prompt_tokens: 1, completion_tokens: 2 } });

const EXPECTED = collect([STREAM]);

test("a whole stream parses into its frames", () => {
  assert.equal(EXPECTED.length, 5);
  assert.deepEqual(
    EXPECTED.map((e) => e.event),
    ["message_start", "heartbeat", "token", "token", "done"],
  );
  assert.deepEqual(JSON.parse(EXPECTED[2].data), { text: "مرحباً، هذا ردّ «ريحان» [1]" });
  assert.deepEqual(JSON.parse(EXPECTED[3].data), { text: "😀 emoji + سطر\nجديد" });
});

test("frames split at EVERY string position across two chunks parse identically", () => {
  for (let i = 0; i <= STREAM.length; i++) {
    assert.deepEqual(collect([STREAM.slice(0, i), STREAM.slice(i)]), EXPECTED, `split at ${i}`);
  }
});

test("one character per chunk parses identically", () => {
  assert.deepEqual(collect([...STREAM].map((c) => c)), EXPECTED);
});

test("split multi-byte Arabic: every byte split, decoded with the stream decoders", () => {
  const bytes = new TextEncoder().encode(STREAM);
  for (let i = 0; i <= bytes.length; i++) {
    const parts = [bytes.slice(0, i), bytes.slice(i)];

    const native = new TextDecoder("utf-8");
    const nativeText = parts.map((b) => native.decode(b, { stream: true })).concat(native.decode());
    assert.deepEqual(collect(nativeText), EXPECTED, `native, split at byte ${i}`);

    const fallback = createUtf8Decoder();
    const fbText = parts.map((b) => fallback.decode(b)).concat(fallback.end());
    assert.deepEqual(collect(fbText), EXPECTED, `utf8.ts, split at byte ${i}`);
    assert.ok(!fbText.join("").includes("�"), `no U+FFFD at byte ${i}`);
  }
});

test("CRLF and lone CR line endings, including a CR at the end of a chunk", () => {
  const crlf = STREAM.replace(/\n/g, "\r\n");
  // Payload JSON contains an escaped \n (backslash-n), never a raw newline, so
  // rewriting line ends only touches framing.
  assert.deepEqual(collect([crlf]), EXPECTED);
  for (let i = 0; i <= crlf.length; i++) {
    assert.deepEqual(collect([crlf.slice(0, i), crlf.slice(i)]), EXPECTED, `crlf split ${i}`);
  }
  const cr = STREAM.replace(/\n/g, "\r");
  assert.deepEqual(collect([cr]), EXPECTED);
  for (let i = 0; i <= cr.length; i++) {
    assert.deepEqual(collect([cr.slice(0, i), cr.slice(i)]), EXPECTED, `cr split ${i}`);
  }
});

test("comment lines are ignored; a comment-only block dispatches nothing", () => {
  const s = `: keep-alive\n\n:another\n${frame("status", { text: "بحث" })}: trailing\n\n`;
  const out = collect([s]);
  assert.equal(out.length, 1);
  assert.equal(out[0].event, "status");
});

test("multi-line data joins with \\n; one leading space stripped; no-space form", () => {
  const out = collect(["event: x\ndata:  two-spaces\ndata:b\ndata\n\n"]);
  assert.equal(out[0].data, " two-spaces\nb\n");
});

test("frame without event: defaults to 'message'; frame without data dispatches nothing", () => {
  const out = collect(["data: {}\n\nevent: lonely\n\n"]);
  assert.deepEqual(out.map((e) => e.event), ["message"]);
});

test("BOM at stream start is dropped", () => {
  assert.deepEqual(collect(["﻿" + STREAM]), EXPECTED);
});

test("end() dispatches an unterminated final frame; a cut JSON is rejected later", () => {
  const out = collect(['event: token\ndata: {"text":"ab"}']);
  assert.equal(out.length, 1);
  assert.deepEqual(parseSSEData("token", out[0].data), { text: "ab" });

  const cut = collect(['event: token\ndata: {"text":"a']);
  assert.equal(parseSSEData("token", cut[0].data), null);
});

test("heartbeat parses to an empty object", () => {
  const hb = EXPECTED[1];
  assert.deepEqual(parseSSEData("heartbeat", hb.data), {});
  assert.deepEqual(toTypedSSEEvent(hb), { event: "heartbeat", data: {} });
});

test("malformed JSON / non-object payloads → null, never a throw", () => {
  for (const bad of ["", "{", "nope", "[1,2]", "null", "42", '"str"', "{'a':1}", "�"]) {
    assert.doesNotThrow(() => parseSSEData("token", bad));
    assert.equal(parseSSEData("token", bad), null, bad);
    assert.equal(toTypedSSEEvent({ event: "token", data: bad, id: "" }), null);
  }
});

test("unknown event names are dropped by toTypedSSEEvent", () => {
  assert.equal(toTypedSSEEvent({ event: "ask_user", data: "{}", id: "" }), null);
  assert.equal(toTypedSSEEvent({ event: "message", data: "{}", id: "" }), null);
});

test("SSE_EVENT_NAMES: 23 unique names", () => {
  assert.equal(SSE_EVENT_NAMES.length, 23);
  assert.equal(new Set(SSE_EVENT_NAMES).size, 23);
});

test("deep_search_gated is a known event with its payload typed", () => {
  const payload = {
    reason: "free_limit",
    plan_id: "free",
    upgrade_options: ["basic", "pro", "max"],
    resets_at: "2026-11-19T09:00:00+00:00",
    message_ar: "استخدمت البحث المعمّق المتاح في باقتك لهذه الفترة.",
  };
  const out = collect([frame("deep_search_gated", payload)]);
  assert.equal(out.length, 1);
  const typed = toTypedSSEEvent(out[0]);
  assert.ok(typed && typed.event === "deep_search_gated");
  assert.deepEqual(typed.data, payload);
});

test("a long line fed in many small chunks is parsed once, correctly", () => {
  const big = "ب".repeat(200_000);
  const s = frame("token", { text: big });
  const chunks: string[] = [];
  for (let i = 0; i < s.length; i += 7) chunks.push(s.slice(i, i + 7));
  const out = collect(chunks);
  assert.equal(out.length, 1);
  assert.equal((parseSSEData("token", out[0].data) as { text: string }).text.length, 200_000);
});
