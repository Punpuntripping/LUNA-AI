/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { formatCount, formatDateTime, formatElapsed, formatRelativeTime } from "../format";

const NOW = Date.parse("2026-10-15T10:00:00Z");
const ago = (ms: number) => new Date(NOW - ms).toISOString();
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

describe("formatRelativeTime", () => {
  test("Arabic words, Latin digits, dual forms", () => {
    assert.equal(formatRelativeTime(ago(10_000), NOW), "الآن");
    assert.equal(formatRelativeTime(ago(MIN), NOW), "منذ دقيقة");
    assert.equal(formatRelativeTime(ago(2 * MIN), NOW), "منذ دقيقتين");
    assert.equal(formatRelativeTime(ago(5 * MIN), NOW), "منذ 5 دقائق");
    assert.equal(formatRelativeTime(ago(25 * MIN), NOW), "منذ 25 دقيقة");
    assert.equal(formatRelativeTime(ago(3 * HOUR), NOW), "منذ 3 ساعات");
    assert.equal(formatRelativeTime(ago(30 * HOUR), NOW), "أمس");
    assert.equal(formatRelativeTime(ago(4 * DAY), NOW), "منذ 4 أيام");
    assert.equal(formatRelativeTime(ago(15 * DAY), NOW), "منذ أسبوعين");
  });

  test("older than 30 days → a date with Latin digits only", () => {
    const out = formatRelativeTime(ago(60 * DAY), NOW);
    assert.ok(out.length > 0);
    assert.doesNotMatch(out, /[٠-٩۰-۹]/);
  });

  test("invalid input → empty", () => {
    assert.equal(formatRelativeTime("not a date", NOW), "");
  });
});

describe("formatCount / formatElapsed", () => {
  test("plural forms", () => {
    const forms = { one: "مصدر واحد", two: "مصدران", few: "مصادر", many: "مصدرًا" };
    assert.equal(formatCount(1, forms), "مصدر واحد");
    assert.equal(formatCount(2, forms), "مصدران");
    assert.equal(formatCount(7, forms), "7 مصادر");
    assert.equal(formatCount(12, forms), "12 مصدرًا");
  });

  test("m:ss", () => {
    assert.equal(formatElapsed(0), "0:00");
    assert.equal(formatElapsed(65_400), "1:05");
  });
});

describe("formatDateTime", () => {
  test("Arabic words, Latin digits, invalid → empty", () => {
    const out = formatDateTime("2026-10-19T12:05:00Z");
    assert.match(out, /2026/);
    assert.match(out, /05/);
    assert.doesNotMatch(out, /[٠-٩]/);
    assert.equal(formatDateTime("nope"), "");
  });
});
