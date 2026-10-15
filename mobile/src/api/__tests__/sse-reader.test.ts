/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { SSETypedEvent } from "@contracts";

import { scriptedStream, tick } from "../../chat/__tests__/helpers";
import { createChunkDecoder, readSSE } from "../sse-reader";
import { StreamAbortedError } from "../stream-errors";

const enc = new TextEncoder();

async function collect(
  feed: (s: ReturnType<typeof scriptedStream>) => void | Promise<void>,
  opts: { forceFallback?: boolean; stopOn?: string } = {},
) {
  const s = scriptedStream();
  const events: SSETypedEvent[] = [];
  const controller = new AbortController();
  const done = readSSE(
    s.stream,
    (e) => {
      events.push(e);
      return e.event === opts.stopOn;
    },
    {
      signal: controller.signal,
      onIdle: () => controller.abort(),
      idleTimeoutMs: 1_000,
      decoder: createChunkDecoder({ forceFallback: opts.forceFallback }),
    },
  );
  await feed(s);
  return { outcome: await done, events, stream: s };
}

describe("readSSE", () => {
  for (const forceFallback of [false, true]) {
    test(`Arabic split mid code point never becomes U+FFFD (${forceFallback ? "fallback" : "native"} decoder)`, async () => {
      const bytes = enc.encode(`event: token\ndata: {"text":"الشفعة"}\n\n`);
      const { events } = await collect(async (s) => {
        // One byte per chunk: every Arabic letter (2 bytes) is split.
        for (const b of bytes) {
          s.pushBytes(new Uint8Array([b]));
          await tick(1);
        }
        s.close();
      }, { forceFallback });
      assert.deepEqual(events, [{ event: "token", data: { text: "الشفعة" } }]);
    });
  }

  test("CRLF framing, comments, heartbeats, unknown events and bad JSON", async () => {
    const { events, outcome } = await collect((s) => {
      s.push(": ping\r\n\r\n");
      s.push("event: heartbeat\r\ndata: {}\r\n\r\n");
      s.push("event: future_event\r\ndata: {}\r\n\r\n");
      s.push("event: token\r\ndata: {not json\r\n\r\n");
      s.push('event: token\r\ndata: {"text":"أ"}\r');
      s.push('\n\r\nevent: status\ndata: {"text":"ب"}\n\n');
      s.close();
    });
    assert.equal(outcome, "ended");
    assert.deepEqual(
      events.map((e) => e.event),
      ["heartbeat", "token", "status"],
    );
  });

  test("a terminal event stops reading after its chunk and cancels the body", async () => {
    const { events, outcome, stream } = await collect(
      (s) => {
        s.push('event: done\ndata: {"message_id":"a","usage":{"prompt_tokens":0,"completion_tokens":0}}\n\nevent: status\ndata: {"text":"x"}\n\n');
      },
      { stopOn: "done" },
    );
    assert.equal(outcome, "stopped");
    assert.deepEqual(events.map((e) => e.event), ["done", "status"]);
    assert.ok(stream.cancelled);
  });

  test("abort rejects with StreamAbortedError even if the read never settles", async () => {
    const s = scriptedStream();
    const controller = new AbortController();
    const p = readSSE(s.stream, () => false, { signal: controller.signal, onIdle: () => undefined });
    await tick();
    controller.abort();
    await assert.rejects(p, StreamAbortedError);
    assert.ok(s.cancelled);
  });

  test("idle watchdog fires after silence", async () => {
    const s = scriptedStream();
    const controller = new AbortController();
    let idle = 0;
    const p = readSSE(s.stream, () => false, {
      signal: controller.signal,
      idleTimeoutMs: 20,
      onIdle: () => {
        idle += 1;
        controller.abort();
      },
    });
    await assert.rejects(p, StreamAbortedError);
    assert.equal(idle, 1);
  });

  test("a network error surfaces as-is (not as an abort)", async () => {
    const s = scriptedStream();
    const p = readSSE(s.stream, () => false, { signal: new AbortController().signal, onIdle: () => undefined });
    await tick();
    s.fail(new TypeError("Network request failed"));
    await assert.rejects(p, TypeError);
  });
});
