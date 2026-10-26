/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { Token } from "marked";

import { lexMarkdown } from "../adapters/marked/lex";

// «رسائل ريحان» bodies (breaks = true): the owner writes line by line — one
// newline is a visible line break, a blank line a new paragraph. Chat/reader
// (breaks = false) are unchanged.

const LINES = "مثال: الباحث المعمق كان يخلص في 3-5 دقائق\nصار يخلص في **1 ونص** إلى 3 ونص دقيقة";

function inlineTypes(token: Token): string[] {
  return "tokens" in token && token.tokens ? token.tokens.map((t) => t.type) : [];
}

describe("lexMarkdown", () => {
  test("breaks: a single newline inside a paragraph is a line break", () => {
    const [p, ...rest] = lexMarkdown(LINES, true);
    assert.equal(p.type, "paragraph");
    assert.equal(rest.length, 0, "still ONE paragraph");
    assert.deepEqual(inlineTypes(p), ["text", "br", "text", "strong", "text"]);
  });

  test("breaks: a blank line still starts a new paragraph", () => {
    const blocks = lexMarkdown("السطر الأول\n\nفقرة ثانية", true);
    assert.deepEqual(
      blocks.map((b) => b.type),
      ["paragraph", "paragraph"],
    );
  });

  test("breaks: a newline inside a list item breaks too", () => {
    const [list] = lexMarkdown("- بند **أ**\nسطر ثانٍ", true);
    assert.equal(list.type, "list");
    const item = (list as Token & { items: Token[] }).items[0];
    const text = "tokens" in item && item.tokens ? item.tokens[0] : item;
    assert.ok(inlineTypes(text).includes("br"));
  });

  test("default (chat / reader): no br tokens are added", () => {
    const [p] = lexMarkdown(LINES);
    assert.ok(!inlineTypes(p).includes("br"));
  });

  test("empty input → no tokens", () => {
    assert.deepEqual(lexMarkdown("", true), []);
  });
});
