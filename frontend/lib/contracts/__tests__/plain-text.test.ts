import assert from "node:assert/strict";
import { test } from "node:test";

import { stripMarkdownImages } from "../markdown/images";
import { markdownToPlainText } from "../markdown/plain-text";
import { splitStreamingContent } from "../markdown/streaming";

/**
 * The «النسخ لناجز» converter and its helpers, on the cases their doc comments
 * promise (moved verbatim from lib/markdown/*; behaviour must not change).
 *
 * Run:  cd frontend && npx tsx --test lib/contracts/__tests__/*.test.ts
 */

test("headings → own line with blank-line frame; hr/setext removed", () => {
  assert.equal(markdownToPlainText("# عنوان\nنص"), "عنوان\n\nنص");
  assert.equal(markdownToPlainText("## عنوان ##"), "عنوان");
  assert.equal(markdownToPlainText("أ\n\n---\n\nب"), "أ\n\nب");
  assert.equal(markdownToPlainText("عنوان\n===\nنص"), "عنوان\n\nنص");
});

test("every list → N. text; counters restart per block; nesting indents 3 spaces", () => {
  assert.equal(markdownToPlainText("- أ\n- ب\n- ج"), "1. أ\n2. ب\n3. ج");
  assert.equal(markdownToPlainText("1. أ\n2. ب\n\nفقرة\n\n- ج"), "1. أ\n2. ب\n\nفقرة\n\n1. ج");
  assert.equal(markdownToPlainText("- أ\n  - ب\n  - ج\n- د"), "1. أ\n   1. ب\n   2. ج\n2. د");
  assert.equal(markdownToPlainText("- [ ] مهمة\n- [x] تمت"), "1. مهمة\n2. تمت");
});

test("the «المراجع» 1-label lines and [n] markers pass through unchanged", () => {
  const refs = "المراجع\n1-نظام العمل — المادة 77\n2-نظام المرافعات";
  assert.equal(markdownToPlainText(refs), refs);
  assert.equal(markdownToPlainText("نص [3] و [1، 2]."), "نص [3] و [1، 2].");
});

test("inline syntax stripped: emphasis, code, links, html, escapes", () => {
  assert.equal(markdownToPlainText("**غامق** و *مائل* و ~~محذوف~~"), "غامق و مائل و محذوف");
  assert.equal(markdownToPlainText("`code *x*` و [رابط](https://x.com)"), "code *x* و رابط");
  assert.equal(markdownToPlainText("سطر<br>آخر"), "سطر\nآخر");
  assert.equal(markdownToPlainText("نجمة \\*حرفية\\*"), "نجمة *حرفية*");
  assert.equal(markdownToPlainText("snake_case_name"), "snake_case_name");
  assert.equal(markdownToPlainText("a * b * c"), "a * b * c");
});

test("tables → cells joined with « — », separator dropped; stray pipe left alone", () => {
  assert.equal(
    markdownToPlainText("| أ | ب |\n|---|---|\n| 1 | **2** |"),
    "أ — ب\n1 — 2",
  );
  assert.equal(markdownToPlainText("أ | ب"), "أ | ب");
});

test("blockquotes unwrapped; code fences dropped, body verbatim", () => {
  assert.equal(markdownToPlainText("> اقتباس\n>> متداخل"), "اقتباس\nمتداخل");
  assert.equal(markdownToPlainText("```\n# not a heading\n- not a list\n```"), "# not a heading\n- not a list");
});

test("images removed; 3+ newlines collapsed; ends trimmed; Latin counters", () => {
  assert.equal(markdownToPlainText("\n\nأ ![img](https://x/y.png) ب\n\n\n\nج\n\n"), "أ ب\n\nج");
});

test("stripMarkdownImages: no image → byte-for-byte; image-only line dropped; fence kept", () => {
  const plain = "نص\n\n\n\nبلا صور";
  assert.equal(stripMarkdownImages(plain), plain);
  assert.equal(stripMarkdownImages("أ\n\n![c](u)\n\nب"), "أ\n\nب");
  assert.equal(stripMarkdownImages("```\n![c](u)\n```\n![d](v)"), "```\n![c](u)\n```");
  assert.equal(stripMarkdownImages("[رابط](u) ![i](v)"), "[رابط](u)");
});

test("splitStreamingContent: split at last safe boundary, never inside a fence", () => {
  assert.deepEqual(splitStreamingContent("أ\n\nب"), { prefix: "أ\n\n", tail: "ب" });
  assert.deepEqual(splitStreamingContent("```\nx\n\ny"), { prefix: "", tail: "```\nx\n\ny" });
  assert.deepEqual(splitStreamingContent("أ\n\n  مسافة"), { prefix: "", tail: "أ\n\n  مسافة" });
});
