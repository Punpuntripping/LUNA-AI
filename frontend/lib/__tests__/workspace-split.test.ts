import assert from "node:assert/strict";
import { test } from "node:test";

import { CHAT_SHARE_FOR_OUTPUTS, opensWithNarrowChat } from "../workspace-split";

test("legal analysis, «بحث شامل» and writer output open with the chat at 25%", () => {
  assert.equal(CHAT_SHARE_FOR_OUTPUTS, 25);
  assert.equal(opensWithNarrowChat("agent_search", "legal_synthesis"), true);
  assert.equal(opensWithNarrowChat("agent_search", "extended_synthesis"), true);
  assert.equal(opensWithNarrowChat("agent_writing", undefined), true);
  assert.equal(opensWithNarrowChat("agent_writing", "memo"), true);
});

test("everything else keeps the user's split", () => {
  assert.equal(opensWithNarrowChat("agent_search", undefined), false);
  assert.equal(opensWithNarrowChat("note", null), false);
  assert.equal(opensWithNarrowChat("attachment", null), false);
  assert.equal(opensWithNarrowChat("pinned_texts", "pinned_texts"), false);
  assert.equal(opensWithNarrowChat(undefined, undefined), false);
});
