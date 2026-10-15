/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { PLACEHOLDER_MAX_AGE_MS } from "@contracts";

import { buildChatItems } from "../chat-items";
import { msg } from "./helpers";

const NOW = Date.parse("2026-10-15T10:00:00Z");
const iso = (ms: number) => new Date(ms).toISOString();
const idle = { phase: null, assistantMessageId: null } as const;

describe("buildChatItems", () => {
  test("settled turn: user + assistant, the newest assistant is latest", () => {
    const items = buildChatItems(
      [msg({ message_id: "u1", role: "user", content: "سؤال" }), msg({ message_id: "a1", role: "assistant", content: "جواب" })],
      idle,
      undefined,
      NOW,
    );
    assert.deepEqual(
      items.map((i) => i.kind),
      ["user", "assistant"],
    );
    assert.equal(items[1].kind === "assistant" && items[1].isLatest, true);
  });

  test("empty placeholder of a live run → thinking", () => {
    const items = buildChatItems(
      [msg({ message_id: "u1", role: "user" }), msg({ message_id: "a1", role: "assistant", content: "", created_at: iso(NOW - 1000) })],
      idle,
      true,
      NOW,
    );
    assert.deepEqual(
      items.map((i) => i.kind),
      ["user", "thinking"],
    );
  });

  test("run_active false → the newest placeholder is failed", () => {
    const items = buildChatItems(
      [msg({ message_id: "u1", role: "user" }), msg({ message_id: "a1", role: "assistant", content: "", created_at: iso(NOW) })],
      idle,
      false,
      NOW,
    );
    assert.equal(items[1].kind, "failed");
  });

  test("an old dead placeholder superseded by a newer turn is dropped", () => {
    const old = iso(NOW - PLACEHOLDER_MAX_AGE_MS - 1000);
    const items = buildChatItems(
      [
        msg({ message_id: "u1", role: "user", created_at: old }),
        msg({ message_id: "a1", role: "assistant", content: "", created_at: old }),
        msg({ message_id: "u2", role: "user" }),
        msg({ message_id: "a2", role: "assistant", content: "جواب" }),
      ],
      idle,
      undefined,
      NOW,
    );
    assert.deepEqual(
      items.map((i) => i.key),
      ["u1", "u2", "a2"],
    );
  });

  test("before message_start: one pending thinking row after the optimistic user row", () => {
    const items = buildChatItems(
      [msg({ message_id: "opt-1", role: "user", isOptimistic: true })],
      { phase: "sending", assistantMessageId: null },
      undefined,
      NOW,
    );
    assert.deepEqual(
      items.map((i) => i.kind),
      ["user", "thinking"],
    );
  });

  test("streaming, assistant not cached yet → a virtual live row", () => {
    const items = buildChatItems(
      [msg({ message_id: "u1", role: "user" })],
      { phase: "streaming", assistantMessageId: "a9" },
      undefined,
      NOW,
    );
    assert.equal(items[1].kind, "live");
    assert.equal(items[1].key, "a9");
  });

  test("streaming into a cached placeholder → that row is live (no duplicate)", () => {
    const items = buildChatItems(
      [msg({ message_id: "u1", role: "user" }), msg({ message_id: "a9", role: "assistant", content: "" })],
      { phase: "streaming", assistantMessageId: "a9" },
      true,
      NOW,
    );
    assert.deepEqual(
      items.map((i) => `${i.kind}:${i.key}`),
      ["user:u1", "live:a9"],
    );
  });

  test("a placeholder already thinking suppresses the pending row", () => {
    const items = buildChatItems(
      [msg({ message_id: "u1", role: "user" }), msg({ message_id: "a1", role: "assistant", content: "", created_at: iso(NOW) })],
      { phase: "recovering", assistantMessageId: null },
      true,
      NOW,
    );
    assert.equal(items.filter((i) => i.kind === "thinking").length, 1);
  });

  test("an assistant row with artifacts but no text is shown, not thinking", () => {
    const items = buildChatItems(
      [msg({ message_id: "a1", role: "assistant", content: "", artifact_ids: ["w1"] })],
      idle,
      false,
      NOW,
    );
    assert.equal(items[0].kind, "assistant");
  });
});
