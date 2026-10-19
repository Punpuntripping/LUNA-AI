import assert from "node:assert/strict";
import { test } from "node:test";

import { CITATION_RE, linkifyCitations, splitCitations, toAsciiDigits } from "../citations";

/**
 * Run:  cd frontend && npx tsx --test lib/contracts/__tests__/*.test.ts
 */

/* eslint-disable no-restricted-syntax -- Arabic-Indic digits are the INPUT under test */

test("toAsciiDigits: Arabic-Indic + Persian → ASCII, rest untouched", () => {
  assert.equal(toAsciiDigits("١٢٣ ۴۵۶ 789 مادة"), "123 456 789 مادة");
});

test("splitCitations mirrors the remark plugin", () => {
  assert.deepEqual(splitCitations("نص [4] ثم [4،17] و[١١]."), [
    { type: "text", value: "نص " },
    { type: "cite", n: 4 },
    { type: "text", value: " ثم " },
    { type: "cite", n: 4 },
    { type: "cite", n: 17 },
    { type: "text", value: " و" },
    { type: "cite", n: 11 },
    { type: "text", value: "." },
  ]);
  assert.deepEqual(splitCitations("[4, 17 , 23]"), [
    { type: "cite", n: 4 },
    { type: "cite", n: 17 },
    { type: "cite", n: 23 },
  ]);
  // Non-citations stay text; [0] vanishes (web behaviour).
  assert.deepEqual(splitCitations("[abc] [4abc] [4 x[0]y"), [
    { type: "text", value: "[abc] [4abc] [4 x" },
    { type: "text", value: "y" },
  ]);
  assert.deepEqual(splitCitations(""), []);
});

test("CITATION_RE is not consumed by the helpers (lastIndex untouched)", () => {
  splitCitations("[1] [2]");
  linkifyCitations("[1] [2]");
  assert.equal(CITATION_RE.lastIndex, 0);
});

test("linkifyCitations: single, ASCII list, Arabic comma «،», Arabic-Indic digits", () => {
  assert.equal(linkifyCitations("انظر [4]."), "انظر [[4]](cite:4).");
  assert.equal(linkifyCitations("[4,17]"), "[[4]](cite:4)[[17]](cite:17)");
  assert.equal(linkifyCitations("[4، 17]"), "[[4]](cite:4)[[17]](cite:17)");
  assert.equal(linkifyCitations("[٤،١٧]"), "[[4]](cite:4)[[17]](cite:17)");
  assert.equal(linkifyCitations("[۱۲]"), "[[12]](cite:12)");
});

test("linkifyCitations: fenced and inline code untouched", () => {
  const md = [
    "قبل [1]",
    "```",
    "code [2]",
    "```",
    "~~~js",
    "x [3]",
    "~~~",
    "inline `a [4] b` and [5]",
    "``double `[6]` ticks`` [7]",
  ].join("\n");
  assert.equal(
    linkifyCitations(md),
    [
      "قبل [[1]](cite:1)",
      "```",
      "code [2]",
      "```",
      "~~~js",
      "x [3]",
      "~~~",
      "inline `a [4] b` and [[5]](cite:5)",
      "``double `[6]` ticks`` [[7]](cite:7)",
    ].join("\n"),
  );
});

test("linkifyCitations: inside a table cell, row stays a row", () => {
  const md = "| المادة | المصدر |\n|---|---|\n| 77 | نظام العمل [3،4] |";
  const out = linkifyCitations(md);
  assert.equal(out, "| المادة | المصدر |\n|---|---|\n| 77 | نظام العمل [[3]](cite:3)[[4]](cite:4) |");
  assert.equal(out.split("\n")[2].split("|").length, md.split("\n")[2].split("|").length);
});

test("linkifyCitations: existing markdown syntax and idempotency", () => {
  assert.equal(linkifyCitations("[4](https://x.com)"), "[4](https://x.com)");
  assert.equal(linkifyCitations("![4](img.png)"), "![4](img.png)");
  assert.equal(linkifyCitations("[4][ref]"), "[4][ref]");
  assert.equal(linkifyCitations("[4]: https://x.com"), "[4]: https://x.com");
  assert.equal(linkifyCitations("\\[4]"), "\\[4]");
  const once = linkifyCitations("أ [1،2] ب [3]");
  assert.equal(linkifyCitations(once), once);
});

test("linkifyCitations: adjacent markers and a marker before a mid-line colon (QA F1)", () => {
  assert.equal(linkifyCitations("كتابي [1][6]:"), "كتابي [[1]](cite:1)[[6]](cite:6):");
  assert.equal(linkifyCitations("شهرين [1][5]."), "شهرين [[1]](cite:1)[[5]](cite:5).");
  assert.equal(linkifyCitations("كالآتي [1]:"), "كالآتي [[1]](cite:1):");
  assert.equal(linkifyCitations("- البند [2]: نص"), "- البند [[2]](cite:2): نص");
  assert.equal(linkifyCitations("[4،5][6]"), "[[4]](cite:4)[[5]](cite:5)[[6]](cite:6)");
  const once = linkifyCitations("أ [1][2]: ب");
  assert.equal(linkifyCitations(once), once);
});

test("linkifyCitations: no '[' → same string back; [0] removed like the web", () => {
  const s = "لا توجد إشارات هنا";
  assert.equal(linkifyCitations(s), s);
  assert.equal(linkifyCitations("x [0] y"), "x  y");
});
