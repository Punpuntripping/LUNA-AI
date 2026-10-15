import assert from "node:assert/strict";
import { test } from "node:test";

import {
  PLACEHOLDER_LIVE_CAP_MS,
  PLACEHOLDER_MAX_AGE_MS,
  isDeadPlaceholder,
  placeholderPollMs,
} from "../placeholder";

/**
 * Time table for the «ريحان يفكّر» placeholder — same thresholds as the web's
 * `use-messages.ts` poll and `MessageList.tsx` dead check.
 *
 * Run:  cd frontend && npx tsx --test lib/contracts/__tests__/*.test.ts
 */

const T0 = Date.parse("2026-10-15T10:00:00Z");
const MIN = 60_000;

function row(ageMs: number, content = "", role: "assistant" | "user" = "assistant") {
  return { role, content, created_at: new Date(T0 - ageMs).toISOString() };
}

test("constants: 7 min recovery window, 30 min live cap", () => {
  assert.equal(PLACEHOLDER_MAX_AGE_MS, 7 * MIN);
  assert.equal(PLACEHOLDER_LIVE_CAP_MS, 30 * MIN);
});

test("placeholderPollMs: not an empty assistant row → false", () => {
  assert.equal(placeholderPollMs(undefined, undefined, T0), false);
  assert.equal(placeholderPollMs(row(0, "", "user"), true, T0), false);
  assert.equal(placeholderPollMs(row(0, "جواب"), true, T0), false);
  assert.equal(placeholderPollMs(row(0, "   \n"), undefined, T0), 4000); // whitespace = empty
});

test("placeholderPollMs: time table", () => {
  // [age, run_active, expected]
  const table: [number, boolean | undefined, number | false][] = [
    [0, undefined, 4000],
    [7 * MIN - 1, undefined, 4000],
    [7 * MIN, undefined, false],
    [0, false, 10000],
    [7 * MIN - 1, false, 10000],
    [7 * MIN, false, false],
    [0, true, 4000],
    [7 * MIN, true, 4000],
    [30 * MIN - 1, true, 4000],
    [30 * MIN, true, false],
    [-5 * MIN, undefined, 4000], // client clock behind the server
  ];
  for (const [age, active, expected] of table) {
    assert.equal(placeholderPollMs(row(age), active, T0), expected, `${age / MIN} min, ${active}`);
  }
});

test("placeholderPollMs: unparsable created_at stops polling (NaN age)", () => {
  assert.equal(
    placeholderPollMs({ role: "assistant", content: "", created_at: "garbage" }, true, T0),
    false,
  );
});

test("isDeadPlaceholder: time table", () => {
  // [isStreaming, runActive, isNewest, age, expected]
  const table: [boolean, boolean | undefined, boolean, number, boolean][] = [
    // Streaming is never dead.
    [true, false, true, 60 * MIN, false],
    // Server says nothing runs → dead now, newest or not.
    [false, false, true, 0, true],
    [false, false, false, 0, true],
    // Server says live → newest never ages out; older rows do.
    [false, true, true, 60 * MIN, false],
    [false, true, false, 7 * MIN - 1, false],
    [false, true, false, 7 * MIN, true],
    // Unknown (older backend) → age decides.
    [false, undefined, true, 7 * MIN - 1, false],
    [false, undefined, true, 7 * MIN, true],
    [false, undefined, false, 7 * MIN, true],
  ];
  for (const [isStreaming, runActive, isNewest, ageMs, expected] of table) {
    assert.equal(
      isDeadPlaceholder({ isStreaming, runActive, isNewest, ageMs }),
      expected,
      JSON.stringify({ isStreaming, runActive, isNewest, ageMs }),
    );
  }
});
