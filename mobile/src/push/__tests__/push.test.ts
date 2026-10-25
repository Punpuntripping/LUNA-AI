/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  conversationIdFromPushData,
  createOnceGate,
  foregroundBehavior,
  parseSentPushToken,
  pushPermissionFrom,
  routeForPushData,
  shouldOfferChip,
  shouldRepost,
  type SentPushToken,
} from "../logic";

const ID = "3f2b8c1e-9a4d-4e7b-8c21-5d6f7a8b9c0d";
const OTHER = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d";

describe("payload → route", () => {
  test("a turn-ready payload opens /c/<conversation_id>", () => {
    assert.deepEqual(routeForPushData({ conversation_id: ID }), { pathname: "/c/[id]", params: { id: ID } });
  });

  test("extra keys are ignored, the id is normalised to lower case", () => {
    assert.deepEqual(routeForPushData({ conversation_id: ID.toUpperCase(), scopeKey: "@rayhan-ai/rayhan" }), {
      pathname: "/c/[id]",
      params: { id: ID },
    });
  });

  test("anything that is not a UUID never reaches the router", () => {
    for (const data of [
      null,
      undefined,
      "x",
      42,
      {},
      { conversation_id: "" },
      { conversation_id: 7 },
      { conversation_id: "../settings" },
      { conversation_id: `${ID}/../../settings` },
      { conversationId: ID },
    ]) {
      assert.equal(routeForPushData(data), null, JSON.stringify(data));
      assert.equal(conversationIdFromPushData(data), null);
    }
  });
});

describe("re-post cache", () => {
  const sent: SentPushToken = { token: "ExponentPushToken[abc]", userId: "u1", appVersion: "1.0.0" };

  test("never sent → post", () => {
    assert.equal(shouldRepost(null, sent), true);
  });

  test("same token, account and version → skip", () => {
    assert.equal(shouldRepost(sent, { ...sent }), false);
  });

  test("a rotated token, another account or a new app version → post", () => {
    assert.equal(shouldRepost(sent, { ...sent, token: "ExponentPushToken[xyz]" }), true);
    assert.equal(shouldRepost(sent, { ...sent, userId: "u2" }), true);
    assert.equal(shouldRepost(sent, { ...sent, appVersion: "1.1.0" }), true);
  });

  test("the cached value round-trips; malformed values count as never sent", () => {
    assert.deepEqual(parseSentPushToken(JSON.stringify(sent)), sent);
    assert.deepEqual(parseSentPushToken(JSON.stringify({ token: "t", userId: "u" })), { token: "t", userId: "u", appVersion: "" });
    for (const raw of [null, "", "not json", "null", "[]", '{"token":"t"}', '{"token":"","userId":"u"}', '{"token":1,"userId":"u"}']) {
      assert.equal(parseSentPushToken(raw), null, String(raw));
    }
  });
});

describe("foreground decision", () => {
  test("the chat on screen gets no banner, no list entry, no sound", () => {
    assert.deepEqual(foregroundBehavior(ID, ID), {
      shouldShowBanner: false,
      shouldShowList: false,
      shouldPlaySound: false,
      shouldSetBadge: false,
    });
  });

  test("another chat, or no chat on screen → banner + sound", () => {
    const shown = { shouldShowBanner: true, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: false };
    assert.deepEqual(foregroundBehavior(ID, OTHER), shown);
    assert.deepEqual(foregroundBehavior(ID, null), shown);
  });

  test("a push without a conversation is never silenced", () => {
    assert.equal(foregroundBehavior(null, null).shouldShowBanner, true);
    assert.equal(foregroundBehavior(null, ID).shouldShowBanner, true);
  });
});

describe("permission + chip", () => {
  test("permission mapping", () => {
    assert.equal(pushPermissionFrom({ granted: true, canAskAgain: true }), "granted");
    assert.equal(pushPermissionFrom({ granted: true, canAskAgain: false }), "granted");
    assert.equal(pushPermissionFrom({ granted: false, canAskAgain: true }), "askable");
    assert.equal(pushPermissionFrom({ granted: false, canAskAgain: false }), "blocked");
  });

  test("the chip shows until registered, refused or blocked", () => {
    assert.equal(shouldOfferChip({ permission: "askable", registered: false, refused: false }), true);
    // Granted (Android < 13 default, or turned off in settings) but not registered.
    assert.equal(shouldOfferChip({ permission: "granted", registered: false, refused: false }), true);
    assert.equal(shouldOfferChip({ permission: "granted", registered: true, refused: false }), false);
    assert.equal(shouldOfferChip({ permission: "askable", registered: false, refused: true }), false);
    assert.equal(shouldOfferChip({ permission: "blocked", registered: false, refused: false }), false);
  });
});

describe("once gate", () => {
  test("a response is handled once, even when it arrives twice", () => {
    const first = createOnceGate(2);
    assert.equal(first("n1"), true);
    assert.equal(first("n1"), false);
    assert.equal(first("n2"), true);
    assert.equal(first("n3"), true);
    // Bounded memory: the oldest key is forgotten.
    assert.equal(first("n1"), true);
    assert.equal(first("n3"), false);
  });
});
