import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEEP_STEPS,
  EXTENDED_STEPS,
  FILE_STEPS,
  NO_MERGE_TEXT_AR,
  activeSearchStep,
  fileStepStatus,
  fillPercent,
  isMerging,
  searchStepStatus,
  type SearchStep,
} from "../progress-stages";
import type { DeepSearchStage } from "../contracts/sse";

const statuses = (
  steps: readonly SearchStep[],
  stage: DeepSearchStage,
  merging = false,
) => steps.map((s) => searchStepStatus(s, steps, stage, merging));

test("deep search: the five steps follow the run, search stays on through evaluation", () => {
  assert.deepEqual(statuses(DEEP_STEPS, "planning"), ["active", "pending", "pending", "pending", "pending"]);
  assert.deepEqual(statuses(DEEP_STEPS, "searching"), ["done", "active", "pending", "pending", "pending"]);
  assert.deepEqual(statuses(DEEP_STEPS, "evaluating"), ["done", "active", "active", "pending", "pending"]);
  assert.deepEqual(statuses(DEEP_STEPS, "aggregating"), ["done", "done", "done", "active", "pending"]);
  assert.deepEqual(statuses(DEEP_STEPS, "writing"), ["done", "done", "done", "done", "active"]);
  assert.deepEqual(statuses(DEEP_STEPS, "done"), ["done", "done", "done", "done", "done"]);
});

test("شامل: reports, then «دمج المصادر», then the summary", () => {
  // planning · searching · evaluating · aggregating · merging · writing
  assert.deepEqual(statuses(EXTENDED_STEPS, "aggregating"), [
    "done", "done", "done", "active", "pending", "pending",
  ]);
  assert.deepEqual(statuses(EXTENDED_STEPS, "aggregating", true), [
    "done", "done", "done", "done", "active", "pending",
  ]);
  assert.deepEqual(statuses(EXTENDED_STEPS, "writing"), [
    "done", "done", "done", "done", "done", "active",
  ]);
  assert.deepEqual(statuses(EXTENDED_STEPS, "done"), Array(6).fill("done"));
});

test("isMerging: every search done while still aggregating — not on a one-report run", () => {
  const all = { total: 3, done: 3 };
  assert.equal(isMerging("aggregating", all, "اكتملت البحوث — دمج النتائج في جواب واحد"), true);
  assert.equal(isMerging("aggregating", { total: 3, done: 2 }, null), false);
  assert.equal(isMerging("evaluating", all, null), false);
  assert.equal(isMerging("writing", all, null), false);
  assert.equal(isMerging("aggregating", null, null), false); // ordinary deep search
  assert.equal(isMerging("aggregating", all, NO_MERGE_TEXT_AR), false);
});

test("activeSearchStep names merging only on the شامل card", () => {
  assert.equal(activeSearchStep(EXTENDED_STEPS, "aggregating", true), "merging");
  assert.equal(activeSearchStep(DEEP_STEPS, "aggregating", true), "aggregating");
  assert.equal(activeSearchStep(DEEP_STEPS, "evaluating", false), "evaluating");
});

test("files card: earlier steps done, skipped steps fill when a later one starts", () => {
  assert.deepEqual(
    FILE_STEPS.map((s) => fileStepStatus(s, "extracting")),
    ["active", "pending", "pending", "pending", "pending"],
  );
  // No OCR, no summary wait: the run starts at the segmenter.
  assert.deepEqual(
    FILE_STEPS.map((s) => fileStepStatus(s, "arranging")),
    ["done", "done", "active", "pending", "pending"],
  );
  assert.deepEqual(
    FILE_STEPS.map((s) => fileStepStatus(s, "threads")),
    ["done", "done", "done", "done", "active"],
  );
});

test("fillPercent: empty / creeping below full / full", () => {
  assert.equal(fillPercent("pending", 10_000, 20_000), 0);
  assert.equal(fillPercent("done", 0, 20_000), 100);
  const early = fillPercent("active", 5_000, 20_000);
  const late = fillPercent("active", 600_000, 20_000);
  assert.ok(early > 0 && early < late);
  assert.ok(late <= 94, "an active stage never claims it is finished");
});
