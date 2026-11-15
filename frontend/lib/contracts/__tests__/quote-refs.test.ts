import assert from "node:assert/strict";
import { test } from "node:test";

import { MAX_QUOTE_REFS, parseQuoteRefs, withQuoteRefs } from "../quote-refs";

const REF = { n: 1, text: "فعلى الهيئة أن تطلب عزله", source: "نظام الهيئة — المادة 24" };

test("a valid ref survives; a missing note reads as null", () => {
  assert.deepEqual(parseQuoteRefs([REF]), [{ ...REF, note: null }]);
});

test("absent or malformed metadata yields no refs", () => {
  assert.deepEqual(parseQuoteRefs(undefined), []);
  assert.deepEqual(parseQuoteRefs("x"), []);
  assert.deepEqual(
    parseQuoteRefs([null, 3, [], { n: 1 }, { n: 0, text: "x" }, { n: 1.5, text: "x" }, { n: "1", text: "x" }]),
    [],
  );
});

test("a repeated number keeps its first quote, and refs come back in order", () => {
  const out = parseQuoteRefs([{ ...REF, n: 2, text: "ثانٍ" }, REF, { ...REF, text: "مكرر" }]);
  assert.deepEqual(out.map((r) => [r.n, r.text]), [[1, REF.text], [2, "ثانٍ"]]);
});

test("the list is capped", () => {
  const many = Array.from({ length: MAX_QUOTE_REFS + 3 }, (_, i) => ({ n: i + 1, text: `نص ${i}` }));
  assert.equal(parseQuoteRefs(many).length, MAX_QUOTE_REFS);
});

test("copied text carries the quotes behind its numbers", () => {
  const refs = parseQuoteRefs([REF, { n: 2, text: "نص بلا مصدر" }]);
  assert.equal(
    withQuoteRefs("للهيئة أن تطلب العزل. [1] [2]\n", refs),
    "للهيئة أن تطلب العزل. [1] [2]\n\n[1] «فعلى الهيئة أن تطلب عزله»\n— نظام الهيئة — المادة 24\n\n[2] «نص بلا مصدر»",
  );
  assert.equal(withQuoteRefs("نص", []), "نص");
});
