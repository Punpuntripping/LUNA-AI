/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { applyProgress, formatSearches, mergeSearches } from "../deep-search-progress";

// «بحث شامل» — `data.searches` on agent_progress (web chat-store
// `mergeSearches` + DeepSearchProgress `formatSearchesAr`).

describe("mergeSearches", () => {
  test("a missing or malformed block keeps the previous value", () => {
    const prev = { total: 4, done: 1 };
    assert.equal(mergeSearches(prev, undefined), prev);
    assert.equal(mergeSearches(prev, null), prev);
    assert.equal(mergeSearches(prev, { total: 0, done: 0 }), prev);
    assert.equal(mergeSearches(prev, { total: "4", done: 1 }), prev);
    assert.equal(mergeSearches(prev, { total: Number.NaN, done: 1 }), prev);
    assert.equal(mergeSearches(null, { done: 1 }), null);
  });

  test("done only goes up and never passes total", () => {
    assert.deepEqual(mergeSearches(null, { total: 4, done: 0 }), { total: 4, done: 0 });
    assert.deepEqual(mergeSearches({ total: 4, done: 2 }, { total: 4, done: 1 }), { total: 4, done: 2 });
    assert.deepEqual(mergeSearches({ total: 4, done: 2 }, { total: 4, done: 9 }), { total: 4, done: 4 });
    assert.deepEqual(mergeSearches(null, { total: 3.4, done: -2 }), { total: 3, done: 0 });
  });
});

describe("applyProgress — searches", () => {
  test("an ordinary deep search has none; an event without the block keeps the last one", () => {
    const first = applyProgress(null, { stage: "planning", text: "" }, 0);
    assert.equal(first.searches, null);
    const withSearches = applyProgress(first, { stage: "searching", data: { searches: { total: 3, done: 1 } } }, 1);
    assert.deepEqual(withSearches.searches, { total: 3, done: 1 });
    const later = applyProgress(withSearches, { stage: "searching", data: { sources: 12 } }, 2);
    assert.deepEqual(later.searches, { total: 3, done: 1 });
    assert.equal(later.sources, 12);
  });
});

describe("formatSearches", () => {
  test("Arabic agreement like the web, Latin digits", () => {
    assert.equal(formatSearches({ total: 2, done: 0 }), "بحثان متوازيان · اكتمل 0");
    assert.equal(formatSearches({ total: 4, done: 1 }), "4 بحوث متوازية · اكتمل 1");
    assert.equal(formatSearches({ total: 10, done: 10 }), "10 بحوث متوازية · اكتمل 10");
    assert.equal(formatSearches({ total: 11, done: 3 }), "11 بحثًا متوازيًا · اكتمل 3");
    assert.doesNotMatch(formatSearches({ total: 12, done: 7 }), /[٠-٩۰-۹]/);
  });
});
