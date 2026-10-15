/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { placeholderPollMs } from "@contracts";

import {
  addOptimistic,
  chronological,
  confirmUserMessage,
  ensureAssistantPlaceholder,
  findAdoptable,
  mergeLatestPage,
  writeFinalAssistant,
  type MessagesData,
} from "../message-cache";
import { msg } from "./helpers";

const SENT = Date.parse("2026-10-15T10:00:00Z");
const iso = (ms: number) => new Date(ms).toISOString();

describe("findAdoptable", () => {
  const page = (created: number) => ({
    messages: [
      msg({ message_id: "a1", role: "assistant", content: "" }),
      msg({ message_id: "u1", role: "user", content: "سؤال", created_at: iso(created) }),
      msg({ message_id: "u0", role: "user", content: "قديم" }),
    ],
    has_more: false,
  });

  test("with a baseline, novelty decides — a phone clock 10 min ahead still adopts", () => {
    const m = findAdoptable(page(SENT - 600_000), {
      content: " سؤال ",
      sentAtMs: SENT,
      knownIds: new Set(["u0"]),
      hasBaseline: true,
    });
    assert.equal(m?.user.message_id, "u1");
    assert.equal(m?.assistant?.message_id, "a1");
  });

  test("without a baseline, created_at must be ≥ sentAt − 5 s", () => {
    const q = { content: "سؤال", sentAtMs: SENT, knownIds: new Set<string>(), hasBaseline: false };
    assert.ok(findAdoptable(page(SENT - 4_000), q));
    assert.equal(findAdoptable(page(SENT - 6_000), q), null);
  });

  test("a different text or a known newest user row → null", () => {
    assert.equal(
      findAdoptable(page(SENT), { content: "غيره", sentAtMs: SENT, knownIds: new Set(), hasBaseline: true }),
      null,
    );
    assert.equal(
      findAdoptable(page(SENT), { content: "سؤال", sentAtMs: SENT, knownIds: new Set(["u1"]), hasBaseline: true }),
      null,
    );
  });
});

describe("cache transforms", () => {
  const twoPages = (): MessagesData => ({
    pages: [
      { messages: [msg({ message_id: "m3", role: "user" }), msg({ message_id: "m2", role: "assistant" })], has_more: true },
      { messages: [msg({ message_id: "m1", role: "user" })], has_more: false },
    ],
    pageParams: [undefined, "m2"],
  });

  test("mergeLatestPage keeps cached first-page rows the fetch did not return (no gap)", () => {
    const data = addOptimistic(twoPages(), msg({ message_id: "opt", role: "user", isOptimistic: true }));
    const merged = mergeLatestPage(
      data,
      { messages: [msg({ message_id: "a9", role: "assistant" }), msg({ message_id: "u9", role: "user" }), msg({ message_id: "m3", role: "user" })], has_more: true, run_active: true },
      ["opt"],
    );
    assert.deepEqual(
      chronological(merged).map((m) => m.message_id),
      ["m1", "m2", "m3", "u9", "a9"],
    );
    assert.equal(merged.pages[0].run_active, true);
  });

  test("confirmUserMessage swaps in place, or re-adds a row an empty fetch wiped", () => {
    const real = { id: "u1", content: "س", attachments: [], conversationId: "c1", createdAt: iso(SENT) };
    const swapped = confirmUserMessage(addOptimistic(undefined, msg({ message_id: "opt", role: "user" })), "opt", real);
    assert.deepEqual(swapped.pages[0].messages.map((m) => m.message_id), ["u1"]);
    const readded = confirmUserMessage({ pages: [{ messages: [], has_more: false }], pageParams: [undefined] }, "opt", real);
    assert.deepEqual(readded.pages[0].messages.map((m) => m.message_id), ["u1"]);
  });

  test("writeFinalAssistant never overwrites a row that already has content", () => {
    const data: MessagesData = {
      pages: [{ messages: [msg({ message_id: "a1", role: "assistant", content: "من الخادم" })], has_more: false }],
      pageParams: [undefined],
    };
    const out = writeFinalAssistant(data, {
      id: "a1",
      conversationId: "c1",
      content: "محلي",
      artifactIds: null,
      referencedItemIds: null,
      nextSteps: [],
      templatesUsed: undefined,
      createdAt: iso(SENT),
    });
    assert.equal(out.pages[0].messages[0].content, "من الخادم");
  });

  test("ensureAssistantPlaceholder makes the newest row poll-worthy", () => {
    const out = ensureAssistantPlaceholder(twoPages(), { id: "a5", conversationId: "c1", createdAt: iso(SENT) });
    assert.equal(placeholderPollMs(out.pages[0].messages[0], undefined, SENT + 1_000), 4000);
    // Idempotent.
    assert.equal(ensureAssistantPlaceholder(out, { id: "a5", conversationId: "c1", createdAt: iso(SENT) }), out);
  });
});
