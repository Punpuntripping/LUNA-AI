/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { plainSnippet } from "../snippet";

describe("plainSnippet", () => {
  test("headings lose their markers and fold onto one line", () => {
    assert.equal(
      plainSnippet("# انتهاء عقد العمل\n\n## أولاً: متى ينتهي عقد العمل؟\n..."),
      "انتهاء عقد العمل أولاً: متى ينتهي عقد العمل؟ ...",
    );
  });

  test("emphasis, links, list markers, images and table pipes", () => {
    assert.equal(plainSnippet("**مهم** و[النظام](https://x.sa)"), "مهم والنظام");
    assert.equal(plainSnippet("- أول\n- ثانٍ"), "1. أول 2. ثانٍ");
    assert.equal(plainSnippet("![صورة](https://x.sa/a.png)\nنص"), "نص");
    assert.equal(plainSnippet("| أ | ب |\n|---|---|\n| 1 | 2 |"), "أ — ب 1 — 2");
  });

  test("citation markers survive; empty input is empty", () => {
    assert.equal(plainSnippet("المادة 77 [3]"), "المادة 77 [3]");
    assert.equal(plainSnippet(null), "");
    assert.equal(plainSnippet("   "), "");
  });
});
